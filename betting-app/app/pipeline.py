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


def decide_use_ratings(path: Path | None = None) -> tuple[bool, str]:
    """Blend the ratings model into probabilities ONLY if the backtest shows the blend beating the market
    (lower Brier AND log loss) in every league with a usable sample. Otherwise market-only."""
    path = path or DATA / "backtest.json"
    try:
        leagues = json.loads(path.read_text())["leagues"]
    except (OSError, ValueError, KeyError):
        return False, "no backtest.json yet (run backtest.py); defaulting to market-only"
    ok = []
    for lg in leagues:
        c = lg.get("vs_market_closing") or {}
        if c.get("n", 0) < 40 or "blend" not in c:
            return False, f"{lg.get('league')}: too few closing lines to judge; market-only"
        ok.append(c["blend"]["brier"] < c["market_no_vig_moneyline"]["brier"] and c["blend"]["log_loss"] < c["market_no_vig_moneyline"]["log_loss"])
    return (all(ok) and bool(ok)), ("blend beat the market in the backtest" if all(ok) and ok else "blend did not beat the market in the backtest; market-only")


def build_payload(sample: bool = False, ttl: int = 6 * 3600, fanduel_state: str = "nj", use_fanduel: bool = True,
                  use_model: bool = True, debug_dump: str | None = None, use_fpi: bool = True) -> dict:
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
    use, why = decide_use_ratings()
    notes = notes + [f"Ratings blend {'ON' if use else 'OFF'}: {why}"]
    payload = engine.run(provs, params={"use_ratings": use}, models=models, model_notes=notes)
    if use_fpi:
        try:
            payload["fpi_attached"] = attach_fpi(payload, cache)
        except Exception:
            payload["fpi_attached"] = 0
    if payload["n_events"] == 0:
        reasons = "; ".join(f"{s['name']} [{s['league']}]: {s['detail']}" for s in payload["sources"])
        fallback = engine.run([SampleProvider()], force_sample_reason=f"No live source returned events. {reasons}")
        fallback["sources"] = payload["sources"] + fallback["sources"]
        return fallback
    return payload


def parse_fpi(summary) -> dict | None:
    """ESPN 'Matchup Predictor' (FPI-style) win probabilities from a summary payload; None if absent/malformed."""
    try:
        pr = summary["predictor"]
        h, a = float(pr["homeTeam"]["gameProjection"]) / 100, float(pr["awayTeam"]["gameProjection"]) / 100
        return {"home": h, "away": a} if 0 < h < 1 and 0 < a < 1 and abs(h + a - 1) < 0.02 else None
    except (KeyError, TypeError, ValueError):
        return None


def attach_fpi(payload: dict, cache, fetch=None, max_events: int = 40) -> int:
    """Reference only: add ESPN's independent matchup-predictor probability (`fpi_prob`) to moneyline picks.
    One cached summary request per event (6h cache). Never used in EV; cannot be backtested (ESPN drops it after the game)."""
    from .providers import polite_get_json
    from .ratings import LEAGUES, SUMMARY
    fetch = fetch or polite_get_json
    done: dict = {}
    n = 0
    for pk in payload.get("top_singles", []):
        pk["fpi_prob"] = None
        if pk["bet"]["market"] != "h2h" or not str(pk["event_id"]).startswith("espn-"):
            continue
        eid = pk["event_id"][5:]
        if eid not in done:
            if len(done) >= max_events:
                continue
            sport = "americanfootball_nfl" if pk["league"] == "NFL" else "americanfootball_ncaaf"
            key = f"fpi_{sport}_{eid}"
            hit = cache.get(key) if cache else None
            if hit:
                data = hit[0]
            else:
                try:
                    data = fetch(SUMMARY.format(path=LEAGUES[sport]["path"], eid=eid))
                except Exception:
                    data = None
                if cache and data is not None:
                    cache.put(key, {"predictor": data.get("predictor")} if isinstance(data, dict) else {})
            done[eid] = parse_fpi(data if isinstance(data, dict) else {})
        f = done[eid]
        if f:
            pk["fpi_prob"] = round(f["home"] if pk["bet"]["name"] == pk["home_team"] else f["away"], 4)
            n += 1
    return n


def save(payload: dict, path: Path | None = None) -> Path:
    path = path or DATA / "picks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in payload.items() if not k.startswith("_")}, indent=1))
    return path
