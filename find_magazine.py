#!/usr/bin/env python3
"""
Find Magazine by Keyword Feature
Searches Telegram channels for English magazines and uses an OpenAI-compatible
API to evaluate relevance.
Outputs results to outputs/find_magazine_<timestamp>.json and .md.

Requires OPENAI_MODEL; optional OPENAI_API_KEY and OPENAI_BASE_URL
(see openai_compat.py).
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langdetect import DetectorFactory, detect
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename, Message

from openai_compat import OpenAICompatConfigError, load_openai_compat
from telegram_links import message_deep_link


async def retry_with_backoff(func, *args, max_retries=5, initial_delay=1, **kwargs):
    """Retry an async operation with exponential backoff for database lock errors."""
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e):
                raise
            if attempt == max_retries - 1:
                logger.error(f"Database lock persisted after {max_retries} retries.")
                raise
            logger.warning(
                f"Database locked (attempt {attempt + 1}/{max_retries}), retrying in {delay}s..."
            )
            await asyncio.sleep(delay)
            delay *= 2


# For reproducible language detection
DetectorFactory.seed = 0

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("find_magazine.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Constants
SESSION_NAME = "toi_session"
OUTPUT_DIR = Path("outputs")
CACHE_DIR = Path(".cache/magazine_search")
MAX_LLM_RETRIES = 5
DEFAULT_BATCH_DELAY = 40
# Local models typically have 4k–16k context; reserved max_tokens counts against it.
DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_AGE_DAYS = 90  # ~3 months; skip older channel posts
TOKENS_PER_DECISION = 48
MAX_COMPLETION_TOKENS_CAP = 4096
CAPTION_PROMPT_CHARS = 80


def message_cutoff(max_age_days: int, *, now: Optional[datetime] = None) -> datetime:
    """UTC timestamp before which channel messages are ignored."""
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref - timedelta(days=max(0, int(max_age_days)))


def resolve_max_age_days(cli_value: Optional[int] = None) -> int:
    """Resolve max age from CLI override, else MAGAZINE_MAX_AGE_DAYS, else default."""
    if cli_value is not None:
        return max(0, int(cli_value))
    raw = (os.getenv("MAGAZINE_MAX_AGE_DAYS") or "").strip()
    if raw.isdigit():
        return max(0, int(raw))
    return DEFAULT_MAX_AGE_DAYS


def is_message_recent(msg_date: Optional[datetime], cutoff: datetime) -> bool:
    """True when msg_date is on/after cutoff (missing dates are treated as recent)."""
    if msg_date is None:
        return True
    if msg_date.tzinfo is None:
        msg_date = msg_date.replace(tzinfo=timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return msg_date >= cutoff


def completion_token_budget(item_count: int) -> int:
    """Scale max_tokens with batch size without blowing typical local contexts."""
    cap = MAX_COMPLETION_TOKENS_CAP
    env_cap = (os.getenv("OPENAI_MAX_TOKENS") or "").strip()
    if env_cap.isdigit():
        cap = max(256, int(env_cap))
    return min(cap, max(512, int(item_count) * TOKENS_PER_DECISION + 256))


def _is_context_overflow_error(err: BaseException) -> bool:
    err_str = str(err).lower()
    needles = (
        "context size",
        "context length",
        "context_length",
        "maximum context",
        "too many tokens",
        "prompt is too long",
        "exceeds the model",
    )
    return any(n in err_str for n in needles)


class MagazineSearcher:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        batch_delay: float = DEFAULT_BATCH_DELAY,
        keyword_only: bool = False,
        cache_enabled: bool = True,
        openai_client=None,
        openai_model: Optional[str] = None,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.batch_size = batch_size
        self.batch_delay = batch_delay
        self.keyword_only = keyword_only
        self.cache_enabled = cache_enabled
        self.client = TelegramClient(SESSION_NAME, api_id, api_hash)

        self.openai_client = openai_client
        self.openai_model = openai_model

        if not keyword_only and (self.openai_client is None or not self.openai_model):
            compat = load_openai_compat()
            self.openai_client = compat.client
            self.openai_model = compat.model

        OUTPUT_DIR.mkdir(exist_ok=True)
        if cache_enabled:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def start(self):
        await retry_with_backoff(self.client.start)
        logger.info("Telegram client started.")

    async def stop(self):
        await self.client.disconnect()
        logger.info("Telegram client disconnected.")

    async def scan_channels(
        self, limit: int = 500, max_age_days: int = DEFAULT_MAX_AGE_DAYS
    ) -> List[Dict[str, Any]]:
        candidates = []
        cutoff = message_cutoff(max_age_days)
        logger.info(
            "Enumerating channels and scanning for candidate magazines "
            "(messages since %s, last %s days)...",
            cutoff.date().isoformat(),
            max_age_days,
        )

        async for dialog in self.client.iter_dialogs():
            if not getattr(dialog.entity, "broadcast", False):
                continue

            title = dialog.name or "Unknown Channel"

            logger.info(f"Scanning channel: {title}")

            is_junk = False
            channel_candidates = []
            msg_count = 0

            async for msg in self.client.iter_messages(dialog.id, limit=limit):
                msg_count += 1

                # Newest-first: stop once we leave the recency window.
                if not is_message_recent(getattr(msg, "date", None), cutoff):
                    break

                # Check for "junk" channels (APK/Software) early (first 20 messages)
                if msg_count <= 20 and msg.media and hasattr(msg.media, "document"):
                    for attr in msg.media.document.attributes:
                        if isinstance(attr, DocumentAttributeFilename):
                            if attr.file_name.lower().endswith(
                                (".apk", ".exe", ".dmg", ".ipa")
                            ):
                                is_junk = True
                                break

                if is_junk:
                    logger.info(f"Skipping junk/APK channel: {title}")
                    channel_candidates = []  # Discard any gathered candidates
                    break

                candidate = self._extract_candidate(
                    msg,
                    title,
                    dialog.id,
                    getattr(dialog.entity, "username", None),
                )
                if candidate:
                    channel_candidates.append(candidate)

            if not is_junk:
                candidates.extend(channel_candidates)

        # Deduplicate by filename + size
        deduped = {}
        for c in candidates:
            key = (c["filename"], c["size"])
            if key not in deduped or c["date"] > deduped[key]["date"]:
                deduped[key] = c

        logger.info(f"Found {len(deduped)} unique candidates.")
        return list(deduped.values())

    def _extract_candidate(
        self,
        msg: Message,
        channel_name: str,
        channel_id: int,
        channel_username: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not msg.media or not hasattr(msg.media, "document"):
            return None

        filename = None
        for attr in msg.media.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break

        if not filename:
            return None

        ext = Path(filename).suffix.lower()
        valid_exts = {".pdf", ".epub", ".mobi", ".zip", ".rar"}

        # Heuristic: filename or caption suggests magazine
        caption = msg.message or ""

        # Relaxed logic: If it's a PDF/EPUB, it's likely a candidate even without explicit "magazine" keywords
        # The AI will filter out irrelevant stuff later.
        is_potential_magazine = ext in valid_exts

        if is_potential_magazine:
            # Language check (fast)
            if not self._is_likely_english(filename, caption):
                return None

            return {
                "msg_id": msg.id,
                "channel_id": channel_id,
                "channel_username": channel_username,
                "channel_name": channel_name,
                "filename": filename,
                "size": msg.media.document.size,
                "date": msg.date.isoformat(),
                "caption": caption,
                "message": msg,
                "link": message_deep_link(
                    channel_id=channel_id,
                    msg_id=msg.id,
                    username=channel_username,
                ),
            }
        return None

    def _is_likely_english(self, filename: str, caption: str) -> bool:
        text = f"{filename} {caption}".strip()
        if not text:
            return False

        # For short text, langdetect is unreliable. Let it pass to AI.
        if len(text) < 50:
            return True

        try:
            # Simple check: if mostly non-ascii, maybe not English?
            # But langdetect is better.
            lang = detect(text)
            return lang == "en"
        except Exception:
            return True  # Fallback to true if detection fails

    def _get_deep_link(
        self,
        channel_id: int,
        msg_id: int,
        channel_username: Optional[str] = None,
    ) -> str:
        return message_deep_link(
            channel_id=channel_id,
            msg_id=msg_id,
            username=channel_username,
        )

    def _keyword_only_filter(
        self, candidates: List[Dict[str, Any]], user_keywords: str
    ) -> List[Dict[str, Any]]:
        """Filter candidates by keyword in filename or caption (case-insensitive)."""
        keywords_lower = user_keywords.lower().strip()
        if not keywords_lower:
            return []
        parts = [p.strip() for p in keywords_lower.split() if p]
        results = []
        for c in candidates:
            text = f"{c.get('filename', '')} {c.get('caption', '')}".lower()
            if any(p in text for p in parts):
                c["ai_decision"] = {
                    "decision": "RELEVANT",
                    "confidence": 0.8,
                    "reasons": ["Keyword match in filename/caption"],
                }
                results.append(c)
                self._log_match(c)
        return results

    async def evaluate_candidates(
        self, candidates: List[Dict[str, Any]], user_keywords: str
    ) -> List[Dict[str, Any]]:
        if self.keyword_only:
            return self._keyword_only_filter(candidates, user_keywords)

        results = []
        to_evaluate = []
        for c in candidates:
            cache_key = hashlib.sha256(
                f"{user_keywords}:{c['filename']}:{c['size']}".encode()
            ).hexdigest()
            cache_path = CACHE_DIR / f"{cache_key}.json"
            if self.cache_enabled and cache_path.exists():
                with open(cache_path, "r") as f:
                    c["ai_decision"] = json.load(f)
                    if c["ai_decision"].get("decision") == "RELEVANT":
                        results.append(c)
                        self._log_match(c)
                continue
            to_evaluate.append(c)

        for i in range(0, len(to_evaluate), self.batch_size):
            batch = to_evaluate[i : i + self.batch_size]
            logger.info(f"Evaluating batch of {len(batch)} magazines...")
            metadata_list = [
                {
                    "id": idx,
                    "filename": c["filename"],
                    "caption": (c.get("caption") or "")[:CAPTION_PROMPT_CHARS],
                }
                for idx, c in enumerate(batch)
            ]
            decisions = await self._call_llm_batch(metadata_list, user_keywords)
            for idx, c in enumerate(batch):
                decision = decisions.get(str(idx), {"decision": "NOT_RELEVANT"})
                c["ai_decision"] = decision
                if self.cache_enabled:
                    cache_key = hashlib.sha256(
                        f"{user_keywords}:{c['filename']}:{c['size']}".encode()
                    ).hexdigest()
                    cache_path = CACHE_DIR / f"{cache_key}.json"
                    with open(cache_path, "w") as f:
                        json.dump(decision, f)
                if decision.get("decision") == "RELEVANT":
                    results.append(c)
                    self._log_match(c)
            if self.batch_delay > 0 and i + self.batch_size < len(to_evaluate):
                await asyncio.sleep(self.batch_delay)
        return results

    def _log_match(self, c: Dict[str, Any]):
        size_mb = c["size"] / (1024 * 1024)
        logger.info(
            f"[MATCH] {c['filename']} | Channel: {c['channel_name']} | Size: {size_mb:.2f} MB | msg_id: {c['msg_id']} | Link: {c['link']}"
        )

    def _build_eval_prompt(self, items: List[Dict[str, Any]], keywords: str) -> str:
        # Compact payload + no free-text "reasons" — local models often emit
        # unescaped quotes there ("Expecting ',' delimiter").
        return f"""Evaluate magazine relevance for keywords: {json.dumps(keywords)}.

Items: {json.dumps(items, ensure_ascii=False, separators=(",", ":"))}

Return ONLY a JSON object. Keys are item id strings. Each value is:
{{"decision":"RELEVANT"|"NOT_RELEVANT"|"UNCERTAIN","confidence":0.0}}
No markdown. No extra keys. No trailing commas."""

    async def _call_llm_batch(
        self, items: List[Dict[str, Any]], keywords: str
    ) -> Dict[str, Any]:
        """Call OpenAI-compatible chat Completions for a batch."""
        if not self.openai_client or not self.openai_model:
            return {}
        if not items:
            return {}

        prompt = self._build_eval_prompt(items, keywords)
        messages: List[Dict[str, str]] = [{"role": "user", "content": prompt}]
        use_json_object = True
        last_text = ""

        for attempt in range(MAX_LLM_RETRIES):
            try:
                kwargs: Dict[str, Any] = {
                    "model": self.openai_model,
                    "messages": messages,
                    "max_tokens": completion_token_budget(len(items)),
                }
                if use_json_object:
                    kwargs["response_format"] = {"type": "json_object"}
                response = await self.openai_client.chat.completions.create(**kwargs)
                choice = response.choices[0]
                last_text = (choice.message.content or "").strip()
                finish = getattr(choice, "finish_reason", None)
                if finish == "length":
                    raise json.JSONDecodeError(
                        "response truncated (finish_reason=length)",
                        last_text or "",
                        0,
                    )
                if not last_text:
                    raise ValueError("empty model response")
                parsed = self._parse_llm_json(last_text)
                return self._normalize_decisions(parsed)
            except Exception as e:
                err_str = str(e).lower()
                is_429 = "429" in err_str or "rate" in err_str
                is_context = _is_context_overflow_error(e)
                is_json_mode = (
                    use_json_object
                    and (
                        "response_format" in err_str
                        or "json_object" in err_str
                    )
                )
                is_parse = isinstance(e, (json.JSONDecodeError, ValueError))

                # Oversized prompt/output: split immediately — retries won't help.
                if is_context and len(items) > 1:
                    logger.warning(
                        "Context exceeded for batch of %s; splitting.",
                        len(items),
                    )
                    break

                if is_json_mode:
                    logger.warning(
                        "JSON response_format unsupported; retrying without it."
                    )
                    use_json_object = False
                    continue

                if is_429 and attempt < MAX_LLM_RETRIES - 1:
                    delay = 30.0 * (2**attempt)
                    logger.warning(
                        "AI rate limit (429). Retrying after %.0fs.", delay
                    )
                    await asyncio.sleep(min(delay, 120.0))
                    continue

                if is_parse and attempt < MAX_LLM_RETRIES - 1:
                    logger.warning(
                        "AI JSON parse failed (attempt %s/%s): %s",
                        attempt + 1,
                        MAX_LLM_RETRIES,
                        e,
                    )
                    messages = [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": last_text[:4000]},
                        {
                            "role": "user",
                            "content": (
                                "Your previous reply was invalid JSON "
                                f"({e}). Reply again with ONLY valid JSON matching "
                                "the required schema. No markdown."
                            ),
                        },
                    ]
                    continue

                logger.error("AI batch error: %s", e)
                break

        # Last resort: split so one bad object cannot wipe the whole batch.
        if len(items) > 1:
            mid = len(items) // 2
            logger.warning(
                "Splitting failed AI batch of %s into %s + %s",
                len(items),
                mid,
                len(items) - mid,
            )
            left = await self._call_llm_batch(items[:mid], keywords)
            right = await self._call_llm_batch(items[mid:], keywords)
            merged = dict(left)
            merged.update(right)
            return merged
        return {}

    @staticmethod
    def _clean_json_text(text: str) -> str:
        """Strip markdown fences and isolate the outermost JSON object."""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
        text = re.sub(r",\s*}", "}", text)
        text = re.sub(r",\s*]", "]", text)
        return text.strip()

    @staticmethod
    def _parse_llm_json(text: str) -> Dict[str, Any]:
        cleaned = MagazineSearcher._clean_json_text(text)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            repaired = (
                cleaned.replace("\u201c", '"')
                .replace("\u201d", '"')
                .replace("\u2018", "'")
                .replace("\u2019", "'")
            )
            data = json.loads(repaired)
        if not isinstance(data, dict):
            raise ValueError(f"expected JSON object, got {type(data).__name__}")
        return data

    @staticmethod
    def _normalize_decisions(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Coerce model output into {id: {decision, confidence, reasons}}."""
        out: Dict[str, Any] = {}
        for key, value in raw.items():
            sid = str(key)
            if isinstance(value, str):
                decision = value.strip().upper().replace(" ", "_")
                out[sid] = {
                    "decision": decision
                    if decision in {"RELEVANT", "NOT_RELEVANT", "UNCERTAIN"}
                    else "NOT_RELEVANT",
                    "confidence": 0.5,
                    "reasons": [],
                }
                continue
            if not isinstance(value, dict):
                continue
            decision = str(value.get("decision", "NOT_RELEVANT")).strip().upper()
            decision = decision.replace(" ", "_")
            if decision in {"R", "YES", "TRUE"}:
                decision = "RELEVANT"
            elif decision in {"N", "NO", "FALSE"}:
                decision = "NOT_RELEVANT"
            elif decision in {"U", "UNKNOWN", "MAYBE"}:
                decision = "UNCERTAIN"
            if decision not in {"RELEVANT", "NOT_RELEVANT", "UNCERTAIN"}:
                decision = "NOT_RELEVANT"
            try:
                confidence = float(value.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            reasons = value.get("reasons", [])
            if isinstance(reasons, str):
                reasons = [reasons]
            elif not isinstance(reasons, list):
                reasons = []
            out[sid] = {
                "decision": decision,
                "confidence": confidence,
                "reasons": reasons,
            }
        return out


def save_outputs(results: List[Dict[str, Any]], keywords: str):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUT_DIR / f"find_magazine_{timestamp}.json"
    md_path = OUTPUT_DIR / f"find_magazine_{timestamp}.md"

    # Save JSON
    with open(json_path, "w") as f:
        json.dump(
            {
                "keywords": keywords,
                "timestamp": timestamp,
                "total_matches": len(results),
                "results": [
                    {k: v for k, v in r.items() if k != "message"} for r in results
                ],
            },
            f,
            indent=2,
        )

    # Save Markdown
    with open(md_path, "w") as f:
        f.write(f"# Magazine Search Results: {keywords}\n")
        f.write(f"Generated at: {timestamp}\n\n")
        f.write(f"Total Matches: {len(results)}\n\n")
        for i, r in enumerate(results, 1):
            f.write(f"## {i}. {r['filename']}\n")
            f.write(f"- **Channel**: {r['channel_name']}\n")
            f.write(f"- **Size**: {r['size'] / (1024*1024):.2f} MB\n")
            f.write(f"- **Link**: [Open in Telegram]({r['link']})\n")
            f.write(
                f"- **Decision**: {r['ai_decision'].get('decision')} (Conf: {r['ai_decision'].get('confidence')})\n"
            )
            f.write(
                f"- **Reasons**: {', '.join(r['ai_decision'].get('reasons', []))}\n"
            )
            f.write("\n---\n")


async def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "Find Magazines by Keyword using an OpenAI-compatible API "
            "(OPENAI_MODEL required; OPENAI_API_KEY / OPENAI_BASE_URL optional)."
        ),
    )
    parser.add_argument(
        "--keywords", required=True, help="Keywords to search for (e.g. computers)"
    )
    parser.add_argument(
        "--limit", type=int, default=500, help="Messages to scan per channel"
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help=(
            "Only consider messages from the last N days "
            f"(default from MAGAZINE_MAX_AGE_DAYS or {DEFAULT_MAX_AGE_DAYS})"
        ),
    )
    parser.add_argument(
        "--keyword-only",
        action="store_true",
        help="Filter by keyword in filename/caption only (no API calls)",
    )
    parser.add_argument(
        "--batch-delay",
        type=float,
        default=DEFAULT_BATCH_DELAY,
        help=f"Seconds to wait between AI batches (default {DEFAULT_BATCH_DELAY})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            f"Candidates per AI batch (default {DEFAULT_BATCH_SIZE}; "
            "oversized batches auto-split on context errors)"
        ),
    )
    args = parser.parse_args()

    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

    if not api_id or not api_hash:
        logger.error("Missing TG_API_ID or TG_API_HASH in .env")
        return

    openai_client = None
    openai_model = None
    if not args.keyword_only:
        try:
            compat = load_openai_compat()
            openai_client = compat.client
            openai_model = compat.model
        except OpenAICompatConfigError as e:
            logger.error("%s", e)
            return

    max_age_days = resolve_max_age_days(args.max_age_days)
    logger.info("Using magazine max age of %s days.", max_age_days)

    searcher = MagazineSearcher(
        int(api_id),
        api_hash,
        batch_size=args.batch_size,
        batch_delay=args.batch_delay,
        keyword_only=args.keyword_only,
        openai_client=openai_client,
        openai_model=openai_model,
    )
    try:
        await searcher.start()
        candidates = await searcher.scan_channels(
            limit=args.limit, max_age_days=max_age_days
        )
        results = await searcher.evaluate_candidates(candidates, args.keywords)
        save_outputs(results, args.keywords)
        logger.info("Done! Results saved to %s", OUTPUT_DIR)
    finally:
        await searcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
