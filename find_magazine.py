#!/usr/bin/env python3
"""
Find Magazine by Keyword Feature
Searches Telegram channels for English magazines and uses AI (Gemini and/or OpenAI) to evaluate relevance.
Outputs results to outputs/find_magazine_<timestamp>.json and .md.

Provider: gemini (GOOGLE_API_KEY), openai (OPENAI_API_KEY), or auto (Gemini first, fallback to OpenAI on 429).
Quota: https://ai.google.dev/gemini-api/docs/rate-limits and https://ai.dev/rate-limit
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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from google import genai
from langdetect import DetectorFactory, detect
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename, Message

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None


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
GEMINI_QUOTA_LINKS = (
    "https://ai.google.dev/gemini-api/docs/rate-limits and https://ai.dev/rate-limit"
)
MAX_LLM_RETRIES = 5
DEFAULT_BATCH_DELAY = 40


def _parse_retry_delay_seconds(err: Exception) -> float:
    """Parse retry delay from Gemini 429 error details or message. Returns delay in seconds."""
    msg = str(err)
    # e.g. "Please retry in 36.956829754s."
    m = re.search(r"retry in ([\d.]+)s", msg, re.IGNORECASE)
    if m:
        return max(1.0, min(120.0, float(m.group(1))))
    if hasattr(err, "details") and err.details:
        for d in getattr(err.details, "__iter__", lambda: [])() if err.details else []:
            if getattr(d, "retry_delay", None):
                secs = getattr(d.retry_delay, "seconds", None) or 0
                return max(1.0, min(120.0, float(secs)))
    return 40.0


class MagazineSearcher:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        *,
        gemini_key: Optional[str] = None,
        openai_key: Optional[str] = None,
        provider: Literal["gemini", "openai", "auto"] = "auto",
        batch_size: int = 10,
        batch_delay: float = DEFAULT_BATCH_DELAY,
        keyword_only: bool = False,
        cache_enabled: bool = True,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.provider = provider
        self.batch_size = batch_size
        self.batch_delay = batch_delay
        self.keyword_only = keyword_only
        self.cache_enabled = cache_enabled
        self.client = TelegramClient(SESSION_NAME, api_id, api_hash)

        self.gemini_key = (gemini_key or "").strip() or None
        self.openai_key = (openai_key or "").strip() or None
        self.ai_client = None
        self.openai_client = None
        self.model_id = "gemini-2.0-flash"

        if not keyword_only:
            if self.gemini_key and provider in ("gemini", "auto"):
                self.ai_client = genai.Client(
                    api_key=self.gemini_key, http_options={"api_version": "v1"}
                )
            if self.openai_key and provider in ("openai", "auto") and AsyncOpenAI:
                self.openai_client = AsyncOpenAI(api_key=self.openai_key)

        OUTPUT_DIR.mkdir(exist_ok=True)
        if cache_enabled:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def start(self):
        await retry_with_backoff(self.client.start)
        logger.info("Telegram client started.")

    async def stop(self):
        await self.client.disconnect()
        logger.info("Telegram client disconnected.")

    async def scan_channels(self, limit: int = 500) -> List[Dict[str, Any]]:
        candidates = []
        logger.info("Enumerating channels and scanning for candidate magazines...")

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

                candidate = self._extract_candidate(msg, title, dialog.id)
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
        self, msg: Message, channel_name: str, channel_id: int
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
                "channel_name": channel_name,
                "filename": filename,
                "size": msg.media.document.size,
                "date": msg.date.isoformat(),
                "caption": caption,
                "message": msg,
                "link": self._get_deep_link(channel_name, channel_id, msg.id),
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

    def _get_deep_link(self, channel_name: str, channel_id: int, msg_id: int) -> str:
        # Simplified link generation
        return f"tg://openmessage?chat_id={channel_id}&message_id={msg_id}"

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
                {"id": idx, "filename": c["filename"], "caption": c["caption"]}
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
        return f"""Evaluate if the following magazine entries are relevant to the keywords: "{keywords}".

Magazines to evaluate:
{json.dumps(items, indent=2)}

Instructions:
1. For each item, identify if the publication typically covers "{keywords}".
2. Use your internal knowledge of the magazine name.
3. Return a JSON object where keys are the 'id' (as a string) and values are:
   {{ "decision": "RELEVANT" | "NOT_RELEVANT" | "UNCERTAIN", "confidence": 0.0 to 1.0, "reasons": ["Short reason"] }}
RETURN ONLY RAW JSON. NO MARKDOWN."""

    async def _call_llm_batch(
        self, items: List[Dict[str, Any]], keywords: str
    ) -> Dict[str, Any]:
        """Dispatch to Gemini or OpenAI; in auto mode try Gemini then OpenAI on failure."""
        if self.provider == "openai" and self.openai_client:
            return await self._call_openai_batch(items, keywords)
        if self.provider == "auto" and not self.ai_client and self.openai_client:
            return await self._call_openai_batch(items, keywords)
        if self.ai_client:
            out = await self._call_gemini_batch(items, keywords)
            if out is not None:
                return out
            if self.provider == "auto" and self.openai_client:
                logger.info(
                    "Gemini quota/error; falling back to OpenAI for this batch."
                )
                return await self._call_openai_batch(items, keywords)
        return {}

    async def _call_gemini_batch(
        self, items: List[Dict[str, Any]], keywords: str
    ) -> Optional[Dict[str, Any]]:
        prompt = self._build_eval_prompt(items, keywords)
        for attempt in range(MAX_LLM_RETRIES):
            try:
                response = await self.ai_client.aio.models.generate_content(
                    model=self.model_id,
                    contents=prompt,
                )
                text = self._clean_json_text(response.text)
                return json.loads(text)
            except Exception as e:
                err_str = str(e).lower()
                is_429 = (
                    "429" in err_str
                    or "resource_exhausted" in err_str
                    or "quota" in err_str
                )
                if is_429 and attempt < MAX_LLM_RETRIES - 1:
                    delay = _parse_retry_delay_seconds(e)
                    logger.warning(
                        "Gemini rate limit (429). Retrying after %.0fs. See %s",
                        delay,
                        GEMINI_QUOTA_LINKS,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("Gemini AI batch error: %s", e)
                return None
        return None

    async def _call_openai_batch(
        self, items: List[Dict[str, Any]], keywords: str
    ) -> Dict[str, Any]:
        if not self.openai_client:
            return {}
        prompt = self._build_eval_prompt(items, keywords)
        for attempt in range(MAX_LLM_RETRIES):
            try:
                response = await self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                )
                text = (response.choices[0].message.content or "").strip()
                if not text:
                    return {}
                text = self._clean_json_text(text)
                return json.loads(text)
            except Exception as e:
                err_str = str(e).lower()
                is_429 = "429" in err_str or "rate" in err_str
                if is_429 and attempt < MAX_LLM_RETRIES - 1:
                    delay = 30.0 * (2**attempt)
                    logger.warning(
                        "OpenAI rate limit (429). Retrying after %.0fs.", delay
                    )
                    await asyncio.sleep(min(delay, 120.0))
                    continue
                logger.error("OpenAI batch error: %s", e)
                return {}
        return {}

    async def _call_gemini(self, metadata: str, keywords: str) -> Dict[str, Any]:
        # Keep this for fallback or single item if needed, but updated for 404 fix
        prompt = f"""
        Evaluate if the following magazine entry is relevant to the keywords: "{keywords}".
        {metadata}
        Return JSON list of properties: decision, confidence, reasons.
        RETURN ONLY RAW JSON. NO MARKDOWN.
        """
        try:
            response = await self.ai_client.aio.models.generate_content(
                model=self.model_id, contents=prompt
            )
            text = self._clean_json_text(response.text)
            return json.loads(text)
        except Exception as e:
            logger.error(f"Gemini AI error: {e}")
            return {"decision": "UNCERTAIN", "reasons": [str(e)]}

    def _clean_json_text(self, text: str) -> str:
        """Strips markdown code blocks from JSON response."""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()


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
    parser = argparse.ArgumentParser(
        description="Find Magazines by Keyword. AI quotas: " + GEMINI_QUOTA_LINKS,
    )
    parser.add_argument(
        "--keywords", required=True, help="Keywords to search for (e.g. computers)"
    )
    parser.add_argument(
        "--limit", type=int, default=500, help="Messages to scan per channel"
    )
    parser.add_argument(
        "--provider",
        choices=("gemini", "openai", "auto"),
        default="auto",
        help="AI provider: gemini, openai, or auto (Gemini first, fallback to OpenAI on 429)",
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
        default=10,
        help="Candidates per AI batch (default 10)",
    )
    args = parser.parse_args()

    load_dotenv()
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    gemini_key = os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if not api_id or not api_hash:
        logger.error("Missing TG_API_ID or TG_API_HASH in .env")
        return
    if not args.keyword_only:
        if args.provider == "gemini" and not gemini_key:
            logger.error("GOOGLE_API_KEY required for --provider gemini")
            return
        if args.provider == "openai":
            if not openai_key:
                logger.error("OPENAI_API_KEY required for --provider openai")
                return
            if not AsyncOpenAI:
                logger.error(
                    "openai package not installed. Add openai to pyproject.toml and run uv sync"
                )
                return
        if args.provider == "auto" and not gemini_key and not openai_key:
            logger.error(
                "For --provider auto set at least one of GOOGLE_API_KEY or OPENAI_API_KEY in .env"
            )
            return

    searcher = MagazineSearcher(
        int(api_id),
        api_hash,
        gemini_key=gemini_key,
        openai_key=openai_key,
        provider=args.provider,
        batch_size=args.batch_size,
        batch_delay=args.batch_delay,
        keyword_only=args.keyword_only,
    )
    try:
        await searcher.start()
        candidates = await searcher.scan_channels(limit=args.limit)
        results = await searcher.evaluate_candidates(candidates, args.keywords)
        save_outputs(results, args.keywords)
        logger.info("Done! Results saved to %s", OUTPUT_DIR)
    finally:
        await searcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
