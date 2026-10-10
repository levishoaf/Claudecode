"""Build the picks payload: live keyless sources first, labeled SAMPLE fallback if none work."""
from __future__ import annotations

import json
from pathlib import Path

from . import engine
from .providers import DiskCache, EspnProvider, FanDuelProvider
from .sample_data import SampleProvider

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def build_payload(sample: bool = False, ttl: int = 6 * 3600, fanduel_state: str = "nj", use_fanduel: bool = True) -> dict:
    if sample:
        return engine.run([SampleProvider()], force_sample_reason="Sample mode requested (--sample).")
    cache = DiskCache(DATA / "cache", ttl)
    provs = ([FanDuelProvider(cache, state=fanduel_state)] if use_fanduel else []) + [EspnProvider(cache)]
    payload = engine.run(provs)
    if payload["n_events"] == 0:
        reasons = "; ".join(f"{s['name']} [{s['league']}]: {s['detail']}" for s in payload["sources"])
        fallback = engine.run([SampleProvider()], force_sample_reason=f"No live source returned events. {reasons}")
        fallback["sources"] = payload["sources"] + fallback["sources"]
        return fallback
    return payload


def save(payload: dict, path: Path | None = None) -> Path:
    path = path or DATA / "picks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1))
    return path
