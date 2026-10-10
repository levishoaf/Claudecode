"""Build the picks payload: live keyless sources first, labeled SAMPLE fallback if none work."""
from __future__ import annotations

import json
from pathlib import Path

from datetime import datetime, timezone

from . import engine, ratings
from .providers import DiskCache, EspnProvider, FanDuelProvider
from .sample_data import SampleProvider

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def build_payload(sample: bool = False, ttl: int = 6 * 3600, fanduel_state: str = "nj", use_fanduel: bool = True,
                  use_model: bool = True, debug_dump: str | None = None) -> dict:
    if sample:
        return engine.run([SampleProvider()], force_sample_reason="Sample mode requested (--sample).")
    cache = DiskCache(DATA / "cache", ttl)
    provs = ([FanDuelProvider(cache, state=fanduel_state, debug_dir=debug_dump)] if use_fanduel else []) + [EspnProvider(cache)]
    models, notes = {}, []
    if use_model:
        try:
            models, notes = ratings.build_models(cache=cache)
        except Exception as exc:  # the model is optional; never let it break the weekly run
            notes = [f"ratings model disabled: {type(exc).__name__}: {exc}"]
    payload = engine.run(provs, models=models, model_notes=notes)
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
