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
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langdetect import DetectorFactory, detect
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename, Message

from openai_compat import OpenAICompatConfigError, load_openai_compat


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


class MagazineSearcher:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        *,
        batch_size: int = 10,
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
        """Call OpenAI-compatible chat Completions for a batch."""
        if not self.openai_client or not self.openai_model:
            return {}
        prompt = self._build_eval_prompt(items, keywords)
        for attempt in range(MAX_LLM_RETRIES):
            try:
                response = await self.openai_client.chat.completions.create(
                    model=self.openai_model,
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
                        "AI rate limit (429). Retrying after %.0fs.", delay
                    )
                    await asyncio.sleep(min(delay, 120.0))
                    continue
                logger.error("AI batch error: %s", e)
                return {}
        return {}

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
        candidates = await searcher.scan_channels(limit=args.limit)
        results = await searcher.evaluate_candidates(candidates, args.keywords)
        save_outputs(results, args.keywords)
        logger.info("Done! Results saved to %s", OUTPUT_DIR)
    finally:
        await searcher.stop()


if __name__ == "__main__":
    asyncio.run(main())
