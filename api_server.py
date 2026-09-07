#!/usr/bin/env python3
"""
Lightweight local HTTP wrapper around find_toi.py's Telegram scan, so an
external scheduler (e.g. an n8n workflow) can poll for new TOI/DC editions
without embedding Telethon (MTProto) itself.

This process must stay running on a machine that already has a valid
`toi_session.session` file (created once via find_toi.py / toi_gui.py login).
It reuses that same session, so do NOT run this at the same time as another
script that logs in with the same session file (Telethon sessions are not
safe for concurrent use).

Usage:
    python api_server.py [--host 127.0.0.1] [--port 8787]

Endpoint:
    GET /search?date=DD-MM-YYYY&newspaper=toi&keywords=TOI,TOIH&ai_query=...

    All query params are optional:
      - date: defaults to today (server local date)
      - newspaper: "toi" (default) or "dc"
      - keywords: comma-separated override of the default keyword list
      - ai_query: optional semantic query (requires OPENAI_MODEL configured)

    Response: 200 JSON
      {"ok": true, "count": N, "results": [ {filename, channel, size_mb,
       msg_id, date, deep_link, newspaper}, ... ]}

    On scan errors: {"ok": false, "error": "..."} with an appropriate status.
"""

import argparse
import asyncio
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from find_toi import (
    DEFAULT_NEWSPAPER,
    NEWSPAPER_PROFILES,
    find_matching_files,
)

logger = logging.getLogger("api_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Telethon session access is not concurrency-safe; serialize scans.
SCAN_LOCK = threading.Lock()


def run_scan(date_str: str, newspaper: str, keywords, ai_query):
    profile = NEWSPAPER_PROFILES.get(newspaper.lower())
    if not profile:
        raise ValueError(f"Unknown newspaper '{newspaper}'. Choices: {sorted(NEWSPAPER_PROFILES)}")
    resolved_keywords = keywords or profile["keywords"]

    results: list = []
    code = asyncio.run(
        find_matching_files(
            keywords=resolved_keywords,
            date_str=date_str,
            verbose=False,
            ai_query=ai_query,
            newspaper=newspaper,
            out_results=results,
        )
    )
    # code 0 = matches found, 2 = no matches found, 1 = config/error.
    if code == 1:
        raise RuntimeError("Scan failed; check find_toi.log for details.")
    return results


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter default logging
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/search":
            self._send_json(404, {"ok": False, "error": "Not found. Use GET /search"})
            return

        qs = parse_qs(parsed.query)
        date_str = qs.get("date", [datetime.now().strftime("%d-%m-%Y")])[0]
        newspaper = qs.get("newspaper", [DEFAULT_NEWSPAPER])[0]
        keywords_raw = qs.get("keywords", [None])[0]
        keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()] if keywords_raw else None
        ai_query = qs.get("ai_query", [None])[0]

        if not SCAN_LOCK.acquire(blocking=False):
            self._send_json(429, {"ok": False, "error": "A scan is already in progress. Try again shortly."})
            return
        try:
            results = run_scan(date_str, newspaper, keywords, ai_query)
            self._send_json(200, {"ok": True, "count": len(results), "results": results})
        except Exception as e:
            logger.exception("Scan failed")
            self._send_json(500, {"ok": False, "error": str(e)})
        finally:
            SCAN_LOCK.release()


def main():
    parser = argparse.ArgumentParser(description="Local HTTP wrapper for TOI Telegram search")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info(f"Listening on http://{args.host}:{args.port} (GET /search)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
