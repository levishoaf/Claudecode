#!/usr/bin/env python3
"""Capture a fresh (uncached) odds snapshot only: no ranking, no ledger. Run it near kickoff
(for example hourly on game days) so grade.py can compute closing-line value from our own lines.
One request per league per run."""
from datetime import datetime, timezone

from app import pipeline, snapshots
from app.providers import DiskCache, EspnProvider, merge_events

cache = DiskCache(pipeline.DATA / "cache", 0)  # ttl 0 = always fetch fresh
lists = []
for sport in ("americanfootball_nfl", "americanfootball_ncaaf"):
    ev, st = EspnProvider(cache).fetch_events(sport)
    print(f"{sport}: {st['detail']}")
    if ev:
        lists.append(ev)
n = snapshots.append(pipeline.DATA / "snapshots.jsonl", merge_events(lists), datetime.now(timezone.utc))
print(f"snapshot: {n} events appended to data/snapshots.jsonl")
