#!/usr/bin/env python3
"""
Find Magazine by Keyword Feature
Searches Telegram channels for English magazines and uses Gemini AI to evaluate relevance.
Outputs results to outputs/find_magazine_<timestamp>.json and .md.
"""
import os
import sys
import json
import sqlite3
import asyncio
import logging
import argparse
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

from dotenv import load_dotenv
from telethon import TelegramClient, errors
from telethon.tl.types import Message, DocumentAttributeFilename
from google import genai
from langdetect import detect, DetectorFactory

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
            logger.warning(f"Database locked (attempt {attempt + 1}/{max_retries}), retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay *= 2

# For reproducible language detection
DetectorFactory.seed = 0

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('find_magazine.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Constants
SESSION_NAME = "toi_session"
OUTPUT_DIR = Path("outputs")
CACHE_DIR = Path(".cache/magazine_search")

class MagazineSearcher:
    def __init__(self, api_id: int, api_hash: str, gemini_key: str, cache_enabled: bool = True):
        self.api_id = api_id
        self.api_hash = api_hash
        self.gemini_key = gemini_key
        self.client = TelegramClient(SESSION_NAME, api_id, api_hash)
        # Use gemini-1.5-flash-latest and specify the version explicitly if needed, 
        # but the SDK should handle it. The 404 might be from an old model name.
        self.ai_client = genai.Client(api_key=gemini_key)
        self.model_id = 'gemini-1.5-flash' 
        self.cache_enabled = cache_enabled
        
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
            
            # Check for "junk" channels (APK/Software) early
            is_junk = False
            async for msg in self.client.iter_messages(dialog.id, limit=20):
                if msg.media and hasattr(msg.media, 'document'):
                    for attr in msg.media.document.attributes:
                        if isinstance(attr, DocumentAttributeFilename):
                            if attr.file_name.lower().endswith(('.apk', '.exe', '.dmg', '.ipa')):
                                is_junk = True
                                break
                if is_junk: break
            
            if is_junk:
                logger.info(f"Skipping junk/APK channel: {title}")
                continue

            logger.info(f"Scanning channel: {title}")
            
            async for msg in self.client.iter_messages(dialog.id, limit=limit):
                candidate = self._extract_candidate(msg, title, dialog.id)
                if candidate:
                    candidates.append(candidate)
                    
        # Deduplicate by filename + size
        deduped = {}
        for c in candidates:
            key = (c['filename'], c['size'])
            if key not in deduped or c['date'] > deduped[key]['date']:
                deduped[key] = c
                
        logger.info(f"Found {len(deduped)} unique candidates.")
        return list(deduped.values())

    def _extract_candidate(self, msg: Message, channel_name: str, channel_id: int) -> Optional[Dict[str, Any]]:
        if not msg.media or not hasattr(msg.media, 'document'):
            return None
            
        filename = None
        for attr in msg.media.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break
        
        if not filename:
            return None
            
        ext = Path(filename).suffix.lower()
        valid_exts = {'.pdf', '.epub', '.mobi', '.zip', '.rar'}
        
        # Heuristic: filename or caption suggests magazine
        caption = msg.message or ""
        keywords = ['magazine', 'issue', 'vol', 'edition', '2024', '2025', 'weekly', 'monthly']
        is_magazine_hint = any(k in filename.lower() or k in caption.lower() for k in keywords)
        
        if ext in valid_exts or is_magazine_hint:
            # Language check (fast)
            if not self._is_likely_english(filename, caption):
                return None
                
            return {
                'msg_id': msg.id,
                'channel_id': channel_id,
                'channel_name': channel_name,
                'filename': filename,
                'size': msg.media.document.size,
                'date': msg.date.isoformat(),
                'caption': caption,
                'message': msg,
                'link': self._get_deep_link(channel_name, channel_id, msg.id)
            }
        return None

    def _is_likely_english(self, filename: str, caption: str) -> bool:
        text = f"{filename} {caption}".strip()
        if not text:
            return False
        try:
            # Simple check: if mostly non-ascii, maybe not English? 
            # But langdetect is better.
            lang = detect(text)
            return lang == 'en'
        except:
            return True # Fallback to true if detection fails

    def _get_deep_link(self, channel_name: str, channel_id: int, msg_id: int) -> str:
        # Simplified link generation
        return f"tg://openmessage?chat_id={channel_id}&message_id={msg_id}"

    async def evaluate_candidates(self, candidates: List[Dict[str, Any]], user_keywords: str) -> List[Dict[str, Any]]:
        results = []
        to_evaluate = []
        
        for c in candidates:
            # Caching check
            cache_key = hashlib.sha256(f"{user_keywords}:{c['filename']}:{c['size']}".encode()).hexdigest()
            cache_path = CACHE_DIR / f"{cache_key}.json"
            
            if self.cache_enabled and cache_path.exists():
                with open(cache_path, 'r') as f:
                    c['ai_decision'] = json.load(f)
                    if c['ai_decision'].get('decision') == 'RELEVANT':
                        results.append(c)
                        self._log_match(c)
                continue
            
            to_evaluate.append(c)

        # Batch evaluate remaining candidates
        batch_size = 10
        for i in range(0, len(to_evaluate), batch_size):
            batch = to_evaluate[i:i + batch_size]
            logger.info(f"Evaluating batch of {len(batch)} magazines...")
            
            metadata_list = [
                {"id": idx, "filename": c['filename'], "caption": c['caption']} 
                for idx, c in enumerate(batch)
            ]
            
            decisions = await self._call_gemini_batch(metadata_list, user_keywords)
            
            for idx, c in enumerate(batch):
                decision = decisions.get(str(idx), {"decision": "NOT_RELEVANT"})
                c['ai_decision'] = decision
                
                if self.cache_enabled:
                    cache_key = hashlib.sha256(f"{user_keywords}:{c['filename']}:{c['size']}".encode()).hexdigest()
                    cache_path = CACHE_DIR / f"{cache_key}.json"
                    with open(cache_path, 'w') as f:
                        json.dump(decision, f)
                
                if decision.get('decision') == 'RELEVANT':
                    results.append(c)
                    self._log_match(c)
                
        return results

    def _log_match(self, c: Dict[str, Any]):
        size_mb = c['size'] / (1024 * 1024)
        logger.info(f"[MATCH] {c['filename']} | Channel: {c['channel_name']} | Size: {size_mb:.2f} MB | msg_id: {c['msg_id']} | Link: {c['link']}")

    async def _call_gemini_batch(self, items: List[Dict[str, Any]], keywords: str) -> Dict[str, Any]:
        prompt = f"""
        Evaluate if the following magazine entries are relevant to the keywords: "{keywords}".
        
        Magazines to evaluate:
        {json.dumps(items, indent=2)}
        
        Instructions:
        1. For each item, identify if the publication typically covers "{keywords}".
        2. Use your internal knowledge of the magazine name.
        3. Return a JSON object where keys are the 'id' (as a string) and values are:
           {{
             "decision": "RELEVANT" | "NOT_RELEVANT" | "UNCERTAIN",
             "confidence": 0.0 to 1.0,
             "reasons": ["Short reason"]
           }}
        """
        try:
            response = await self.ai_client.aio.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Gemini AI batch error: {e}")
            return {}

    async def _call_gemini(self, metadata: str, keywords: str) -> Dict[str, Any]:
        # Keep this for fallback or single item if needed, but updated for 404 fix
        prompt = f"""
        Evaluate if the following magazine entry is relevant to the keywords: "{keywords}".
        {metadata}
        Return JSON list of properties: decision, confidence, reasons.
        """
        try:
            response = await self.ai_client.aio.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Gemini AI error: {e}")
            return {"decision": "UNCERTAIN", "reasons": [str(e)]}

def save_outputs(results: List[Dict[str, Any]], keywords: str):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUT_DIR / f"find_magazine_{timestamp}.json"
    md_path = OUTPUT_DIR / f"find_magazine_{timestamp}.md"
    
    # Save JSON
    with open(json_path, 'w') as f:
        json.dump({
            "keywords": keywords,
            "timestamp": timestamp,
            "total_matches": len(results),
            "results": [{k: v for k, v in r.items() if k != 'message'} for r in results]
        }, f, indent=2)
        
    # Save Markdown
    with open(md_path, 'w') as f:
        f.write(f"# Magazine Search Results: {keywords}\n")
        f.write(f"Generated at: {timestamp}\n\n")
        f.write(f"Total Matches: {len(results)}\n\n")
        for i, r in enumerate(results, 1):
            f.write(f"## {i}. {r['filename']}\n")
            f.write(f"- **Channel**: {r['channel_name']}\n")
            f.write(f"- **Size**: {r['size'] / (1024*1024):.2f} MB\n")
            f.write(f"- **Link**: [Open in Telegram]({r['link']})\n")
            f.write(f"- **Decision**: {r['ai_decision'].get('decision')} (Conf: {r['ai_decision'].get('confidence')})\n")
            f.write(f"- **Reasons**: {', '.join(r['ai_decision'].get('reasons', []))}\n")
            f.write("\n---\n")

async def main():
    parser = argparse.ArgumentParser(description="Find Magazines by Keyword")
    parser.add_argument("--keywords", required=True, help="Keywords to search for")
    parser.add_argument("--limit", type=int, default=500, help="Messages to scan per channel")
    args = parser.parse_args()

    load_dotenv()
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    gemini_key = os.getenv("GOOGLE_API_KEY")

    if not all([api_id, api_hash, gemini_key]):
        logger.error("Missing credentials in .env")
        return

    searcher = MagazineSearcher(int(api_id), api_hash, gemini_key)
    try:
        await searcher.start()
        candidates = await searcher.scan_channels(limit=args.limit)
        results = await searcher.evaluate_candidates(candidates, args.keywords)
        save_outputs(results, args.keywords)
        logger.info(f"Done! Results saved to {OUTPUT_DIR}")
    finally:
        await searcher.stop()

if __name__ == "__main__":
    asyncio.run(main())
