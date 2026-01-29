#!/usr/bin/env python3
"""
Find papers/magazines from Telegram channels whose filenames contain specified keywords and a date in DD-MM-YYYY.

Usage:
    python find_toi.py [--date DD-MM-YYYY] [--keywords KEY1,KEY2] [--ai-query QUERY]

Requires:
- TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables
- A valid Telethon session file named `toi_session.session` in the project root
- GOOGLE_API_KEY for Gemini AI semantic search
"""
import os
import re
import sys
import argparse
import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()
try:
    from telethon import TelegramClient, errors
    from telethon.tl.types import Message
    from telethon.tl.types import DocumentAttributeFilename
    from google import genai
except Exception:
    TelegramClient = None  # type: ignore
    genai = None  # type: ignore

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('find_toi.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Fix console encoding for Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass  # If reconfigure fails, continue anyway

DEFAULT_KEYWORDS = ["TOI", "TOIH"]  # Only TOI/TOIH are required in matching
SESSION_NAME = "toi_session"


def get_env_api_credentials():
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    return api_id, api_hash


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
                logger.error(f"Database lock persisted after {max_retries} retries. "
                           f"Possible causes: another instance running, file lock, or corrupted session. "
                           f"Try: 1) Close other instances, 2) Delete {SESSION_NAME}.session-shm and -wal files, "
                           f"3) Restart your system.")
                raise
            logger.warning(f"Database locked (attempt {attempt + 1}/{max_retries}), retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay *= 2  # Exponential backoff


def compile_matchers(keywords: List[str], date_str: str):
    """Build a regex that matches filenames containing:
    - MUST have: TOI or TOIH (required)
    - MUST have: Hyderabad or Hyd (required)
    - MUST have: Date in various formats (DD-MM-YYYY, DD-MM, DD.MM.YYYY)
    All case-insensitive
    """
    # Parse the input date (DD-MM-YYYY)
    try:
        dt = datetime.strptime(date_str, "%d-%m-%Y")
        day = dt.day
        month = dt.month
        year = dt.year

        # Build multiple date pattern variations
        date_patterns = [
            rf"{day:02d}[-./\s]{month:02d}[-./\s]{year}",  # 29-11-2025, 29.11.2025, 29/11/2025
            rf"{day:02d}[-./\s]{month:02d}",  # 29-11, 29.11
            rf"{day}[-./\s]{month:02d}[-./\s]{year}",  # 29-11-2025 (day without leading zero)
            rf"{day}[-./\s]{month:02d}",  # 29-11 (day without leading zero)
        ]
        date_pattern = "|".join(date_patterns)
    except ValueError:
        # Fallback to exact match if date parsing fails
        date_pattern = re.escape(date_str)

    # MUST contain TOI or TOIH (not just any keyword)
    toi_pattern = r"TOI[H]?"  # Matches TOI or TOIH

    # Match files that contain (TOI/TOIH AND Hyderabad/Hyd AND date) in any order
    # Use positive lookaheads to ensure all three conditions are met
    regex = re.compile(
        rf"(?=.*{toi_pattern})(?=.*(?:hyd|hyderabad))(?=.*{date_pattern})",
        re.IGNORECASE
    )
    return regex


def extract_filename_from_message(msg: Message) -> Optional[str]:
    """Extract filename from a Telethon message with media attachment."""
    # Check for document attribute
    if hasattr(msg, "document") and msg.document:
        attrs = getattr(msg.document, "attributes", []) or []
        for attr in attrs:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name
    return None


def get_file_size(msg: Message) -> int:
    """Get file size in bytes from message."""
    if hasattr(msg, "document") and msg.document:
        return getattr(msg.document, "size", 0)
    return 0


def get_deep_link(dialog, msg) -> str:
    """Generate a Telegram deep link for a message."""
    try:
        channel_username = getattr(dialog.entity, 'username', None)
        if channel_username:
            return f"https://t.me/{channel_username}/{msg.id}"
        else:
            # For private channels/groups use tg://openmessage
            return f"tg://openmessage?chat_id={dialog.id}&message_id={msg.id}"
    except Exception:
        return "N/A"


async def ai_filter_matches(filenames: List[str], query: str) -> List[str]:
    """Use Gemini AI to filter filenames based on a semantic query."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key or genai is None:
        logger.warning("GOOGLE_API_KEY not set or google-genai not installed. Skipping AI filter.")
        return filenames

    try:
        client = genai.Client(api_key=api_key)
        
        prompt = (
            f"Given the following list of filenames from Telegram messages, "
            f"identify which ones best match the search query: '{query}'.\n"
            f"Return ONLY the matching filenames, one per line, no other text or explanation.\n\n"
            f"Filenames:\n" + "\n".join(filenames)
        )
        
        response = await client.aio.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        matches = [line.strip() for line in response.text.strip().split('\n') if line.strip()]
        # Filter to make sure it only returns names that were in the original list
        return [f for f in matches if f in filenames]
    except Exception as e:
        logger.error(f"Error during AI filtering: {e}")
        return filenames


async def find_matching_files(
    keywords: List[str],
    date_str: str,
    verbose: bool,
    max_retries: int = 5,
    ai_query: Optional[str] = None,
):
    api_id, api_hash = get_env_api_credentials()
    logger.info(f"Starting TOI PDF lookup for date: {date_str}")
    logger.info(f"Keywords: {keywords}, Must contain: Hyderabad or Hyd")

    if TelegramClient is None:
        logger.error("Error: telethon is not installed. Please install it.")
        return 1
    if not api_id or not api_hash:
        logger.error("Error: TG_API_ID and TG_API_HASH must be set in environment.")
        return 1

    session_file = Path(SESSION_NAME + ".session")
    if not session_file.exists():
        logger.error(f"Session file not found: {session_file}. Make sure you have a valid Telethon session.")
        return 1

    logger.info(f"Using session file: {session_file}")
    client = TelegramClient(SESSION_NAME, int(api_id), api_hash)

    regex = compile_matchers(keywords, date_str)

    # Parse target date for message filtering
    try:
        target_date = datetime.strptime(date_str, "%d-%m-%Y").date()
        logger.info(f"Filtering messages from: {target_date}")
    except ValueError:
        logger.warning(f"Could not parse date {date_str}, will scan all messages")
        target_date = None

    logger.info("Connecting to Telegram...")
    await retry_with_backoff(client.start, max_retries=max_retries)
    logger.info("Successfully connected to Telegram")

    matches = []  # list of tuples: (dialog, msg, filename, channel_title, file_size)

    try:
        logger.info("Starting channel scan (filtering for newspaper/epaper channels only)...")
        scanned = 0
        async for dialog in client.iter_dialogs():
            # only channels (broadcast)
            if not getattr(dialog.entity, "broadcast", False):
                continue

            title = getattr(dialog.entity, "title", None) or ""
            title_l = title.lower()

            # Channel name filter
            _channel_filters = ("newspapers", "newspaper", "epaper", "paper", "epapers")
            if not any(k in title_l for k in _channel_filters):
                logger.debug(f"Skipping channel (no newspaper keywords in name): {title}")
                continue

            scanned += 1
            logger.info(f"[{scanned}] Scanning channel: {title}")

            async for msg in client.iter_messages(dialog.id, limit=None):
                if not getattr(msg, "media", None):
                    continue

                if target_date and getattr(msg, 'date', None):
                    try:
                        msg_date = msg.date.date()
                    except Exception:
                        msg_date = None
                    if msg_date and msg_date != target_date:
                        continue

                fname = extract_filename_from_message(msg)
                if not fname:
                    continue

                is_match = False
                if ai_query:
                    is_match = True 
                else:
                    is_match = bool(regex.search(fname))

                if is_match:
                    size = get_file_size(msg)
                    matches.append((dialog, msg, fname, title, size))
                    size_mb = size / (1024 * 1024) if size else 0.0
                    if not ai_query:
                        deep_link = get_deep_link(dialog, msg)
                        logger.info(f"[MATCH] {fname} | Channel: {title} | Size: {size_mb:.2f} MB | msg_id: {msg.id} | Link: {deep_link}")

        if not matches:
            logger.warning(f"No matching files found for {date_str}")
            return 2

        if ai_query:
            logger.info(f"Performing AI semantic search for: '{ai_query}'...")
            filenames = [m[2] for m in matches]
            ai_matches_filenames = await ai_filter_matches(filenames, ai_query)
            
            filtered_matches = [m for m in matches if m[2] in ai_matches_filenames]
            if not filtered_matches:
                logger.warning(f"AI search found no matches for: '{ai_query}'")
                return 2
            matches = filtered_matches
            
            for dialog, msg, fname, title, size in matches:
                size_mb = size / (1024 * 1024) if size else 0.0
                deep_link = get_deep_link(dialog, msg)
                logger.info(f"[MATCH] {fname} | Channel: {title} | Size: {size_mb:.2f} MB | msg_id: {msg.id} | Link: {deep_link}")

        matches.sort(key=lambda x: x[4] or 0, reverse=True)

        logger.info(f"\nSummary: Found {len(matches)} matching file(s). Listing all with deep links:")
        for idx, (dialog, msg, fname, channel_title, size) in enumerate(matches, 1):
            size_mb = (size / (1024 * 1024)) if size else 0.0
            deep_link = get_deep_link(dialog, msg)

            logger.info(f"[{idx}] {fname} | Channel: {channel_title} | Size: {size_mb:.2f} MB")
            logger.info(f"     Link: {deep_link}")

        return 0

    except errors.RPCError as e:
        logger.error(f"Telegram RPC error: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error while scanning: {e}", exc_info=True)
    finally:
        logger.info("Disconnecting from Telegram...")
        await client.disconnect()

    return 2


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Find papers/magazines deep links from Telegram channels")
    parser.add_argument("--date", help="Override date in DD-MM-YYYY format")
    parser.add_argument("--keywords", help="Comma-separated keywords to match in filename (default TOI,TOIH)")
    parser.add_argument("--ai-query", help="Use Gemini AI to semantically search for files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--retry", type=int, default=5, help="Number of retries for database lock errors (default 5)")
    return parser.parse_args(argv)


def validate_date(date_str: str) -> Optional[str]:
    try:
        datetime.strptime(date_str, "%d-%m-%Y")
        return date_str
    except Exception:
        return None


def main(argv=None):
    args = parse_args(argv)

    if args.date:
        date_str = validate_date(args.date)
        if not date_str:
            logger.error("Date must be in DD-MM-YYYY format")
            return 1
    else:
        date_str = datetime.now().strftime("%d-%m-%Y")

    keywords = DEFAULT_KEYWORDS
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    try:
        return_code = asyncio.run(
            find_matching_files(
                keywords=keywords,
                date_str=date_str,
                verbose=args.verbose,
                max_retries=args.retry,
                ai_query=args.ai_query,
            )
        )
        return return_code or 0
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
