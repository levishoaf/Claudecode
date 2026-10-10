#!/usr/bin/env python3
"""Backtest the ratings model (walk-forward) and compare to market closing lines. Writes data/backtest.json."""
import argparse

from app import backtest, pipeline
from app.providers import DiskCache
from app.ratings import LEAGUES

ap = argparse.ArgumentParser()
ap.add_argument("--max-closing", type=int, default=120, help="max ESPN summary requests per league (cached afterwards)")
a = ap.parse_args()
cache = DiskCache(pipeline.DATA / "cache", 6 * 3600)
res = [backtest.run_backtest(s, cache, max_closing=a.max_closing) for s in LEAGUES]
backtest.save(res, pipeline.DATA / "backtest.json")
import json
print(json.dumps(res, indent=1))
