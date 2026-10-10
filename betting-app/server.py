#!/usr/bin/env python3
"""Tiny stdlib web server. GET / (UI), GET /picks.json (cached; refreshed when older than the TTL)."""
import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import pipeline

ap = argparse.ArgumentParser()
ap.add_argument("--sample", action="store_true")
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--no-model", action="store_true")
ap.add_argument("--ttl", type=int, default=6 * 3600)
args = ap.parse_args()
INDEX_PATH = pipeline.ROOT / "web" / "index.html"
PICKS = pipeline.DATA / "picks_sample.json" if args.sample else pipeline.DATA / "picks.json"


def current() -> bytes:
    if not PICKS.exists() or time.time() - PICKS.stat().st_mtime > args.ttl:
        pipeline.save(pipeline.build_payload(args.sample, args.ttl, use_model=not args.no_model), PICKS)
    return PICKS.read_bytes()


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.path = self.path.split("?", 1)[0] or "/"
        if self.path in ("/", "/index.html"):
            body, ctype = INDEX_PATH.read_bytes(), "text/html; charset=utf-8"
        elif self.path.startswith("/picks.json"):
            body, ctype = current(), "application/json"
        elif self.path in ("/track.json", "/backtest.json"):
            f = pipeline.DATA / self.path.lstrip("/")
            body, ctype = (f.read_bytes() if f.exists() else b"{}"), "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"http://{args.host}:{args.port}  ({'SAMPLE' if args.sample else 'live keyless + sample fallback'})")
    ThreadingHTTPServer((args.host, args.port), H).serve_forever()
