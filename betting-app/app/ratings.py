"""Independent, deliberately simple team-strength model (margin-based Elo in points).

Built from public ESPN results only. It is a *complement* to the market, not a replacement:
the betting market is usually sharper than any model this simple.

rating = expected points better than an average team. Predicted home margin
  d = r_home - r_away + HFA (0 at neutral sites). Win prob = Phi(d / sigma).
Early-season shrinkage: ratings carry into a new season regressed toward 0 (REG), and the
update step is larger when teams have few games (so new info is learned fast but old info
is not trusted fully).
"""
from __future__ import annotations

import re
import time
import urllib.error
from datetime import datetime, timezone
from math import erf, log, sqrt

from .providers import DiskCache, polite_get_json

LEAGUES = {
    "americanfootball_nfl": dict(label="NFL", path="nfl", extra="", max_week=18,
                                 hfa=2.0, k=0.12, sigma=15.5, reg=0.30, cap=24.0),
    "americanfootball_ncaaf": dict(label="NCAAF", path="college-football", extra="&groups=80&limit=200", max_week=15,
                                   hfa=2.5, k=0.12, sigma=10.5, reg=0.35, cap=28.0),
}
SB = "https://site.api.espn.com/apis/site/v2/sports/football/{path}/scoreboard?dates={season}&seasontype=2&week={week}{extra}"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/{path}/summary?event={eid}"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def phi(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))


def current_season(now: datetime) -> int:
    return now.year if now.month >= 7 else now.year - 1


def parse_games(data: dict, season: int, week: int) -> list[dict]:
    games = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        cs = {c.get("homeAway"): c for c in comp.get("competitors", []) if c.get("team")}
        if "home" not in cs or "away" not in cs:
            continue
        done = bool((ev.get("status") or {}).get("type", {}).get("completed"))
        try:
            hs, as_ = float(cs["home"].get("score")), float(cs["away"].get("score"))
        except (TypeError, ValueError):
            hs = as_ = None
        games.append({"id": str(ev.get("id")), "date": ev.get("date", ""), "season": season, "week": week,
                      "home_id": cs["home"]["team"]["id"], "away_id": cs["away"]["team"]["id"],
                      "home": cs["home"]["team"]["displayName"], "away": cs["away"]["team"]["displayName"],
                      "home_score": hs, "away_score": as_, "completed": done and hs is not None,
                      "neutral": bool(comp.get("neutralSite"))})
    return games


def fetch_season(sport_key: str, season: int, cache: DiskCache | None, fetch=polite_get_json,
                 now: datetime | None = None, live_ttl: int = 6 * 3600, status: list | None = None) -> list[dict]:
    """All regular-season games of a season, week by week (one request per uncached week).
    Fully completed weeks are immutable, so they are cached indefinitely."""
    cfg = LEAGUES[sport_key]
    out: list[dict] = []
    for week in range(1, cfg["max_week"] + 1):
        key = f"results_{sport_key}_{season}_{week}"
        data = None
        hit = cache.get(key, allow_stale=True) if cache else None
        if hit:
            games = parse_games(hit[0], season, week)
            if games and (all(g["completed"] for g in games) or hit[1] < live_ttl):
                data = hit[0]
        if data is None:
            try:
                url = SB.format(path=cfg["path"], season=season, week=week, extra=cfg["extra"])
                try:
                    data = fetch(url)
                except OSError as first:  # one polite retry for transient network errors (not for HTTP blocks)
                    if isinstance(first, urllib.error.HTTPError):
                        raise
                    time.sleep(4)
                    data = fetch(url)
            except Exception as exc:
                if status is not None:
                    status.append(f"{cfg['label']} {season} wk{week}: {type(exc).__name__}: {exc}")
                break
            if cache:
                cache.put(key, data)
        games = parse_games(data, season, week)
        out += games
        if not any(g["completed"] for g in games):
            break  # reached the first week with no finished games
    return out


class Rater:
    """Margin-based Elo for one league."""

    def __init__(self, sport_key: str, **overrides):
        self.cfg = {**LEAGUES[sport_key], **overrides}
        self.sport_key = sport_key
        self.r: dict[str, float] = {}
        self.n_total: dict[str, int] = {}
        self.n_season: dict[str, int] = {}
        self.names: dict[str, str] = {}  # normalised displayName -> team id

    # ----- prediction
    def diff(self, home_id: str, away_id: str, neutral: bool = False) -> float:
        return self.r.get(home_id, 0.0) - self.r.get(away_id, 0.0) + (0.0 if neutral else self.cfg["hfa"])

    def win_prob(self, d: float) -> float:
        return phi(d / self.cfg["sigma"])

    def margin_for(self, home: str, away: str, neutral: bool = False):
        h, a = self.names.get(norm(home)), self.names.get(norm(away))
        if h is None or a is None:
            return None
        if self.n_total.get(h, 0) + self.n_total.get(a, 0) < 4:
            return None  # too little history to say anything
        return self.diff(h, a, neutral)

    # ----- learning
    def new_season(self):
        for t in self.r:
            self.r[t] *= (1 - self.cfg["reg"])
        self.n_season = {}

    def update(self, g: dict):
        h, a = g["home_id"], g["away_id"]
        self.names[norm(g["home"])] = h
        self.names[norm(g["away"])] = a
        margin = g["home_score"] - g["away_score"]
        cap = self.cfg["cap"]
        margin = max(-cap, min(cap, margin))
        err = margin - self.diff(h, a, g["neutral"])
        nmin = min(self.n_season.get(h, 0), self.n_season.get(a, 0))
        k = self.cfg["k"] * (1 + 1.5 / (1 + nmin))  # faster learning early in a season
        # a team we know nothing about (e.g. an FCS opponent) should not drag an established team around
        w = 1.0 if min(self.n_total.get(h, 0), self.n_total.get(a, 0)) >= 2 else 0.3
        self.r[h] = self.r.get(h, 0.0) + w * k * err
        self.r[a] = self.r.get(a, 0.0) - w * k * err
        for t in (h, a):
            self.n_total[t] = self.n_total.get(t, 0) + 1
            self.n_season[t] = self.n_season.get(t, 0) + 1

    def fit(self, games_by_season: dict[int, list[dict]]):
        for i, season in enumerate(sorted(games_by_season)):
            if i:
                self.new_season()
            for g in sorted((g for g in games_by_season[season] if g["completed"]), key=lambda g: g["date"]):
                self.update(g)
        return self


def build_models(now: datetime | None = None, cache: DiskCache | None = None, fetch=polite_get_json,
                 seasons_back: int = 1) -> tuple[dict, list[str]]:
    """Fit one Rater per league from the previous and current season. Returns (models, problems)."""
    now = now or datetime.now(timezone.utc)
    cur = current_season(now)
    models, problems = {}, []
    for sport in LEAGUES:
        by_season = {}
        for season in range(cur - seasons_back, cur + 1):
            by_season[season] = fetch_season(sport, season, cache, fetch, now, status=problems)
        if not any(by_season.values()):
            problems.append(f"{LEAGUES[sport]['label']}: no results available; ratings model disabled")
            continue
        models[sport] = Rater(sport).fit(by_season)
    return models, problems


# ----- scoring helpers (used by the engine and the backtest)
def brier(ps, ys) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def log_loss(ps, ys, eps=1e-6) -> float:
    return -sum(y * log(max(eps, min(1 - eps, p))) + (1 - y) * log(max(eps, min(1 - eps, 1 - p))) for p, y in zip(ps, ys)) / len(ys)


def blend(market: float, model: float, w_market: float) -> float:
    return w_market * market + (1 - w_market) * model
