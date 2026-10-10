#!/usr/bin/env python3
"""Weekly refresh: fetch (cached, keyless), rank, write data/picks.json (+ optional static site).

  python refresh.py                 # live keyless sources, SAMPLE fallback if none reachable
  python refresh.py --sample        # force labeled sample data
  python refresh.py --static site/  # also write a self-contained static site
  python refresh.py --no-fanduel    # ESPN only
"""
import argparse
import shutil
from pathlib import Path

from app import pipeline

ap = argparse.ArgumentParser()
ap.add_argument("--sample", action="store_true")
ap.add_argument("--no-fanduel", action="store_true")
ap.add_argument("--state", default="nj", help="FanDuel state subdomain (default nj)")
ap.add_argument("--ttl", type=int, default=6 * 3600, help="cache TTL seconds")
ap.add_argument("--static", metavar="DIR")
a = ap.parse_args()

payload = pipeline.build_payload(a.sample, a.ttl, a.state, not a.no_fanduel)
out = pipeline.save(payload)
print(f"mode={payload['mode']} events={payload['n_events']} markets={payload['n_markets_evaluated']} "
      f"+EV={payload['positive_ev_count']} max_books/market={payload['max_books_per_market']}")
for s in payload["sources"]:
    print(f"  source: {s['name']} [{s.get('league')}] ok={s['ok']} - {s['detail']}")
if payload.get("sample_reason") and payload["mode"] == "sample":
    print("  SAMPLE reason:", payload["sample_reason"])
print("wrote", out)
if a.static:
    d = Path(a.static)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(pipeline.ROOT / "web" / "index.html", d / "index.html")
    shutil.copy(out, d / "picks.json")
    print("static site in", d)
