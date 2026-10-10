"""Walk-forward backtest of the ratings model, plus a comparison with the market's closing line
where ESPN still exposes it (recent completed games only, via the public summary 'pickcenter')."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import odds_math as om
from .providers import DiskCache, polite_get_json
from .ratings import LEAGUES, SUMMARY, Rater, blend, brier, current_season, fetch_season, log_loss, phi, to_day


def walk_forward(sport: str, by_season: dict[int, list[dict]], eval_season: int, min_week: int, **overrides):
    """Predict each game of eval_season (week >= min_week) using only earlier games."""
    r = Rater(sport, **overrides)
    rows = []
    for i, season in enumerate(sorted(by_season)):
        if season > eval_season:
            break
        if i:
            r.new_season()
        for g in sorted((g for g in by_season[season] if g["completed"]), key=lambda g: g["date"]):
            if season == eval_season and g["week"] >= min_week:
                d = r.diff(g["home_id"], g["away_id"], g["neutral"], to_day(g["date"]))
                known = min(r.n_total.get(g["home_id"], 0), r.n_total.get(g["away_id"], 0)) >= 2
                if known:
                    margin = g["home_score"] - g["away_score"]
                    rows.append({"id": g["id"], "diff": d, "p": r.win_prob(d), "margin": margin,
                                 "y": 1.0 if margin > 0 else 0.0 if margin < 0 else 0.5, "week": g["week"]})
            r.update(g)
    return rows, r


def metrics(rows, key="p"):
    ps, ys = [x[key] for x in rows], [x["y"] for x in rows]
    return {"n": len(rows), "brier": round(brier(ps, ys), 4), "log_loss": round(log_loss(ps, ys), 4)}


def tune(sport: str, by_season: dict, eval_season: int, min_week: int):
    """Tiny grid on the PRIOR season only (so the current season stays out-of-sample)."""
    best = None
    for k in (0.04, 0.08, 0.12, 0.16, 0.20, 0.25):
        for sigma in (9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5):
            rows, _ = walk_forward(sport, by_season, eval_season, min_week, k=k, sigma=sigma)
            ll = metrics(rows)["log_loss"]
            if best is None or ll < best[0]:
                best = (ll, k, sigma)
    return {"k": best[1], "sigma": best[2], "log_loss": best[0]}


def closing_line(sport: str, event_id: str, cache: DiskCache, fetch=polite_get_json):
    """Closing moneyline/spread/total from ESPN summary pickcenter, or None if not exposed."""
    key = f"summary_{sport}_{event_id}"
    hit = cache.get(key, allow_stale=True) if cache else None
    if hit:
        data = hit[0]
    else:
        try:
            data = fetch(SUMMARY.format(path=LEAGUES[sport]["path"], eid=event_id))
        except Exception:
            return None
        if cache:
            cache.put(key, data)
    pc = data.get("pickcenter") if isinstance(data, dict) else None
    if not isinstance(pc, list) or not pc or not isinstance(pc[0], dict):
        return None
    return parse_pickcenter(pc[0])


def parse_pickcenter(p: dict):
    if not isinstance(p, dict):
        return None
    try:
        home, away = p["homeTeamOdds"], p["awayTeamOdds"]
        out = {"book": (p.get("provider") or {}).get("name"), "spread_home": p.get("spread"), "total": p.get("overUnder"),
               "ml_home": home.get("moneyLine"), "ml_away": away.get("moneyLine"),
               "spread_odds_home": home.get("spreadOdds"), "spread_odds_away": away.get("spreadOdds"),
               "over_odds": p.get("overOdds"), "under_odds": p.get("underOdds")}
    except (KeyError, AttributeError, TypeError):
        return None
    return out


def market_p_home(cl: dict, method="power"):
    if not isinstance(cl, dict) or not cl.get("ml_home") or not cl.get("ml_away"):
        return None
    try:
        imp = [om.implied_prob_from_american(cl["ml_home"]), om.implied_prob_from_american(cl["ml_away"])]
    except ValueError:
        return None
    return om.devig(imp, method)[0]


def run_backtest(sport: str, cache: DiskCache, now: datetime | None = None, fetch=polite_get_json,
                 max_closing: int = 120, w_market: float = 0.8, min_week_prior: int = 5, min_week_cur: int = 3) -> dict:
    now = now or datetime.now(timezone.utc)
    cur = current_season(now)
    by_season = {s: fetch_season(sport, s, cache, fetch, now) for s in (cur - 1, cur)}
    out = {"league": LEAGUES[sport]["label"], "seasons": [cur - 1, cur]}
    tuned = tune(sport, by_season, cur - 1, min_week_prior)
    out["tuned_on_prior_season"] = tuned
    p_rows, _ = walk_forward(sport, by_season, cur - 1, min_week_prior, k=tuned["k"], sigma=tuned["sigma"])
    out["prior_season_walk_forward"] = {**metrics(p_rows), "baseline_coin_flip_brier": 0.25,
                                       "baseline_coin_flip_log_loss": 0.6931,
                                       "home_win_rate": round(sum(x["y"] for x in p_rows) / max(1, len(p_rows)), 3),
                                       "margin_mae": round(sum(abs(x["margin"] - x["diff"]) for x in p_rows) / max(1, len(p_rows)), 2)}
    c_rows, _ = walk_forward(sport, by_season, cur, min_week_cur, k=tuned["k"], sigma=tuned["sigma"])
    out["current_season_walk_forward"] = {**metrics(c_rows)} if c_rows else {"n": 0}
    # market comparison on the most recent completed games that still expose closing lines
    sigma = tuned["sigma"]
    rows, tried = [], 0
    for x in sorted(c_rows, key=lambda r: -r["week"]):
        if tried >= max_closing:
            break
        tried += 1
        cl = closing_line(sport, x["id"], cache, fetch)
        pm = market_p_home(cl)
        if pm is None:
            continue
        x = dict(x, pm=pm, pb=blend(pm, x["p"], w_market))
        if cl.get("spread_home") is not None:
            x["mkt_margin"] = -cl["spread_home"]  # home spread -3 => market expects home by 3
        rows.append(x)
    cmp = {"n": len(rows), "closing_lines_tried": tried, "blend_market_weight": w_market}
    if rows:
        cmp["model"] = metrics(rows, "p")
        cmp["market_no_vig_moneyline"] = metrics(rows, "pm")
        cmp["blend"] = metrics(rows, "pb")
        ms = [x for x in rows if "mkt_margin" in x]
        if ms:
            cmp["margin_mae_model"] = round(sum(abs(x["margin"] - x["diff"]) for x in ms) / len(ms), 2)
            cmp["margin_mae_market_spread"] = round(sum(abs(x["margin"] - x["mkt_margin"]) for x in ms) / len(ms), 2)
        # does the model's DISAGREEMENT carry information? (mean outcome minus market prob when model is higher / lower)
        hi = [x for x in rows if x["p"] - x["pm"] > 0.05]
        lo = [x for x in rows if x["p"] - x["pm"] < -0.05]
        cmp["disagreement"] = {
            "model_higher_n": len(hi), "model_higher_actual_minus_market": round(sum(x["y"] - x["pm"] for x in hi) / len(hi), 3) if hi else None,
            "model_lower_n": len(lo), "model_lower_actual_minus_market": round(sum(x["y"] - x["pm"] for x in lo) / len(lo), 3) if lo else None}
        m, mk, b = cmp["model"]["brier"], cmp["market_no_vig_moneyline"]["brier"], cmp["blend"]["brier"]
        cmp["verdict"] = ("Model alone is worse than the market closing line" if m > mk else "Model alone matched/beat the market (small sample)") + \
                         ("; blend slightly better than market alone" if b < mk else "; blend does NOT improve on the market alone")
    out["vs_market_closing"] = cmp
    return out


def save(results: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "leagues": results}, indent=1))


# ---------------------------------------------------------------- model-change experiments
def evaluate_variant(sport, by_season, cache, cur, overrides, base_params, max_closing=120):
    """Score one parameter set: log loss on last season, on this season, and Brier vs the market closing line."""
    prm = {**base_params, **overrides}
    p_rows, _ = walk_forward(sport, by_season, cur - 1, 5, **prm)
    c_rows, _ = walk_forward(sport, by_season, cur, 3, **prm)
    mk = []
    for x in sorted(c_rows, key=lambda r: -r["week"])[:max_closing]:
        pm = market_p_home(closing_line(sport, x["id"], cache))  # served from the cache populated by run_backtest
        if pm is not None:
            mk.append(dict(x, pm=pm, pb=blend(pm, x["p"], 0.8)))
    out = {"overrides": overrides, "prior": metrics(p_rows), "current": metrics(c_rows) if c_rows else None}
    if mk:
        out["market_subset"] = {"n": len(mk), "model_brier": metrics(mk, "p")["brier"], "market_brier": metrics(mk, "pm")["brier"],
                                "blend_brier": metrics(mk, "pb")["brier"], "model_ll": metrics(mk, "p")["log_loss"],
                                "blend_ll": metrics(mk, "pb")["log_loss"], "market_ll": metrics(mk, "pm")["log_loss"]}
    return out


def accept(base: dict, cand: dict, min_gain: float = 0.002) -> tuple[bool, str]:
    """Keep a change only if log loss improves on BOTH seasons and the blend-vs-market Brier does not get worse."""
    g1 = base["prior"]["log_loss"] - cand["prior"]["log_loss"]
    g2 = base["current"]["log_loss"] - cand["current"]["log_loss"]
    ms_b, ms_c = base.get("market_subset"), cand.get("market_subset")
    ms_ok = (not ms_b or not ms_c) or ms_c["blend_brier"] <= ms_b["blend_brier"] + 1e-9
    ok = g1 >= min_gain and g2 >= min_gain and ms_ok
    return ok, f"prior ll {-g1:+.4f}, current ll {-g2:+.4f}, blend-vs-market brier {'ok' if ms_ok else 'worse'}"


GRIDS = {
    "americanfootball_nfl": [("rest_pts", [0.15, 0.3, 0.5]), ("hfa", [1.0, 1.5, 2.5, 3.0]), ("reg", [0.2, 0.4]), ("cap", [17.0, 35.0])],
    "americanfootball_ncaaf": [("init_unseen", [-3.0, -6.0, -10.0]), ("rest_pts", [0.15, 0.3]), ("hfa", [2.0, 3.0, 3.5]), ("reg", [0.25, 0.45]), ("cap", [21.0, 35.0, 45.0])],
}


def run_experiments(sport: str, cache: DiskCache, now: datetime | None = None, fetch=polite_get_json) -> dict:
    now = now or datetime.now(timezone.utc)
    cur = current_season(now)
    by_season = {s: fetch_season(sport, s, cache, fetch, now) for s in (cur - 1, cur)}
    base_params = {k: LEAGUES[sport][k] for k in ("k", "sigma", "hfa", "reg", "cap", "rest_pts", "init_unseen")}
    best = evaluate_variant(sport, by_season, cache, cur, {}, {})
    log = [{"change": "baseline", "result": best, "kept": True}]
    kept = {}
    for name, values in GRIDS[sport]:
        for v in values:
            cand = evaluate_variant(sport, by_season, cache, cur, {**kept, name: v}, {})
            ok, why = accept(best, cand)
            log.append({"change": f"{name}={v}", "kept": ok, "why": why, "result": cand})
            if ok:
                best, kept = cand, {**kept, name: v}
    return {"league": LEAGUES[sport]["label"], "baseline_params": base_params, "kept_changes": kept, "log": log}
