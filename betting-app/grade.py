#!/usr/bin/env python3
"""Settle past ledger picks from ESPN final scores and write data/track.json (paper trading only)."""
import json

from app import ledger, pipeline
from app.providers import DiskCache

cache = DiskCache(pipeline.DATA / "cache", 6 * 3600)
t = ledger.grade(pipeline.DATA / "ledger.jsonl", pipeline.DATA / "grades.json", cache)
(pipeline.DATA / "track.json").write_text(json.dumps(t, indent=1))
g = t["groups"]["all"]
print(f"settled={t['settled']} pending={t['pending']} record={g['wins']}-{g['losses']}-{g['pushes']} "
      f"flat_units={g['flat_units_won']} flat_roi={g['flat_roi']} kelly_roi={g['kelly_roi']} avg_clv={g['avg_clv']}")
print("wrote", pipeline.DATA / "track.json")
