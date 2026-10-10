#!/usr/bin/env python3
"""Walk-forward experiments on ratings-model changes (rest, home field, regression, caps, FCS start).
A change is kept only if log loss improves out of sample on BOTH seasons and the blend-vs-market Brier does not worsen.
Writes data/experiments.json. Uses cached ESPN data (run backtest.py first so closing lines are cached)."""
import json

from app import backtest, pipeline
from app.providers import DiskCache
from app.ratings import LEAGUES

cache = DiskCache(pipeline.DATA / "cache", 6 * 3600)
res = [backtest.run_experiments(s, cache) for s in LEAGUES]
(pipeline.DATA / "experiments.json").write_text(json.dumps(res, indent=1))
for r in res:
    print(r["league"], "kept:", r["kept_changes"] or "none")
    for e in r["log"][1:]:
        print("  ", "KEEP" if e["kept"] else "drop", e["change"], "|", e["why"])
