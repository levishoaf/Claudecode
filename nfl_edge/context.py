"""Game context: everything besides team strength that moves an NFL result.

All features for a game in week W use only information available before
kickoff: snap shares and starting QBs from weeks < W, that week's injury
report, the schedule (venue, divisional) and the weather. Rest days are
intentionally not used.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .venues import miles_between

INDOOR_ROOFS = {"dome", "closed", ""}  # nflverse leaves retractable roofs blank until game day
STATUS_WEIGHT = {"Out": 1.0, "Doubtful": 0.9, "Questionable": 0.2}
SKIP_POSITIONS = {"QB", "K", "P", "LS"}
OFFENSE = {"RB", "FB", "WR", "TE", "T", "G", "C", "OL", "OT", "OG"}
WIND_THRESHOLD = 10  # mph before wind starts to matter
COLD_THRESHOLD = 45  # degrees F


def name_key(name: str) -> str:
    name = re.sub(r"[^a-z ]", "", name.lower())
    return " ".join(w for w in name.split() if w not in {"jr", "sr", "ii", "iii", "iv", "v"})


@dataclass
class Injury:
    name: str
    position: str
    status: str
    snap_share: float


@dataclass
class Features:
    home_field: float = 1.0
    travel_diff: float = 0.0  # away travel minus home travel, thousands of miles
    div_game: float = 0.0
    off_inj_home: float = 0.0  # starter-equivalents missing
    off_inj_away: float = 0.0
    def_inj_home: float = 0.0
    def_inj_away: float = 0.0
    qb_out_home: float = 0.0  # 1 = primary QB not starting
    qb_out_away: float = 0.0
    indoor: float = 0.0
    wind: float | None = None  # mph, outdoor games
    temp: float | None = None  # F, outdoor games
    notes: list[str] = field(default_factory=list)

    @property
    def wind_excess(self) -> float:
        return max(0.0, self.wind - WIND_THRESHOLD) if self.wind is not None and not self.indoor else 0.0

    @property
    def cold(self) -> float:
        return max(0.0, COLD_THRESHOLD - self.temp) if self.temp is not None and not self.indoor else 0.0


def _num(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SeasonData:
    """One season's schedule, injury reports and snap counts."""

    def __init__(self, games: list[dict], injuries: list[dict], snaps: list[dict],
                 history: list[dict] | None = None):
        """`games` is one season's schedule; `history` is every game on record,
        used to count each QB's career starts."""
        self.games = [g for g in games if g["game_type"] == "REG"]
        self._starts: dict[str, list[str]] = defaultdict(list)  # qb id -> game dates
        for g in history or games:
            if g["home_score"] == "":
                continue
            for side in ("home", "away"):
                if g[f"{side}_qb_id"]:
                    self._starts[g[f"{side}_qb_id"]].append(g["gameday"])
        self.home_venue: dict[str, str] = {}
        venues: dict[str, Counter] = defaultdict(Counter)
        for g in self.games:
            if g["location"] != "Neutral":
                venues[g["home_team"]][g["stadium_id"]] += 1
        self.home_venue = {t: c.most_common(1)[0][0] for t, c in venues.items()}

        # (team, week) -> {player key: (offense pct, defense pct)}
        self.snaps: dict[tuple[str, int], dict[str, tuple[float, float]]] = defaultdict(dict)
        for s in snaps:
            if s["game_type"] != "REG":
                continue
            self.snaps[(s["team"], int(s["week"]))][name_key(s["player"])] = (
                _num(s["offense_pct"]) or 0.0, _num(s["defense_pct"]) or 0.0
            )

        self.injuries: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for i in injuries:
            if i["game_type"] == "REG" and i["report_status"] in STATUS_WEIGHT:
                self.injuries[(i["team"], int(i["week"]))].append(i)

        self._share_cache: dict[tuple[str, int], dict[str, tuple[float, float]]] = {}
        self._qb_cache: dict[tuple[str, int], tuple[str, str] | None] = {}

    def snap_shares(self, team: str, week: int) -> dict[str, tuple[float, float]]:
        """Average snap share per player over the team's games before `week`
        (0 for games a player missed)."""
        key = (team, week)
        if key not in self._share_cache:
            weeks = [w for (t, w) in self.snaps if t == team and w < week]
            totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
            for w in weeks:
                for player, (o, d) in self.snaps[(team, w)].items():
                    totals[player][0] += o
                    totals[player][1] += d
            n = len(weeks) or 1
            self._share_cache[key] = {p: (o / n, d / n) for p, (o, d) in totals.items()}
        return self._share_cache[key]

    def primary_qb(self, team: str, week: int) -> tuple[str, str] | None:
        """(gsis id, name) of the QB with the most starts before `week`;
        ties go to the most recent starter."""
        if (team, week) in self._qb_cache:
            return self._qb_cache[(team, week)]
        starts: dict[tuple[str, str], list[int]] = {}
        for g in self.games:
            w = int(g["week"])
            if w >= week or g["home_score"] == "":
                continue
            for side in ("home", "away"):
                if g[f"{side}_team"] == team and g[f"{side}_qb_id"]:
                    entry = starts.setdefault((g[f"{side}_qb_id"], g[f"{side}_qb_name"]), [0, 0])
                    entry[0] += 1
                    entry[1] = max(entry[1], w)
        qb = max(starts, key=lambda k: tuple(starts[k])) if starts else None
        self._qb_cache[(team, week)] = qb
        return qb

    def career_starts(self, qb_id: str, before: str) -> int:
        return sum(1 for d in self._starts.get(qb_id, ()) if d < before)

    def injury_report(self, team: str, week: int) -> tuple[float, float, float, list[Injury]]:
        """(offense missing, defense missing, primary QB out probability, notable)."""
        shares = self.snap_shares(team, week)
        qb = self.primary_qb(team, week)
        off = dfn = qb_out = 0.0
        notable = []
        for i in self.injuries.get((team, week), []):
            weight = STATUS_WEIGHT[i["report_status"]]
            if i["position"] == "QB":
                if qb and i["gsis_id"] == qb[0]:
                    qb_out = weight
                    notable.append(Injury(i["full_name"], "QB", i["report_status"], 1.0))
                continue
            if i["position"] in SKIP_POSITIONS:
                continue
            o, d = shares.get(name_key(i["full_name"]), (0.0, 0.0))
            share = o if i["position"] in OFFENSE else d
            if i["position"] in OFFENSE:
                off += weight * o
            else:
                dfn += weight * d
            if share >= 0.5:
                notable.append(Injury(i["full_name"], i["position"], i["report_status"], share))
        return off, dfn, qb_out, notable

    def features(self, game: dict, *, actual_qb: bool) -> Features:
        """Context for a schedule row. With `actual_qb`, QB change comes from who
        actually started (for completed games); otherwise from the injury report."""
        week = int(game["week"])
        home, away = game["home_team"], game["away_team"]
        f = Features()
        f.home_field = 0.0 if game["location"] == "Neutral" else 1.0
        venue = game["stadium_id"]
        h_miles = miles_between(self.home_venue.get(home, ""), venue)
        a_miles = miles_between(self.home_venue.get(away, ""), venue)
        if h_miles is not None and a_miles is not None:
            f.travel_diff = (a_miles - h_miles) / 1000
        f.div_game = 1.0 if game["div_game"] == "1" else 0.0
        f.indoor = 1.0 if game["roof"] in INDOOR_ROOFS else 0.0
        if not f.indoor:
            f.wind, f.temp = _num(game["wind"]), _num(game["temp"])

        for side, team in (("home", home), ("away", away)):
            off, dfn, qb_out, notable = self.injury_report(team, week)
            setattr(f, f"off_inj_{side}", off)
            setattr(f, f"def_inj_{side}", dfn)
            if actual_qb:
                # A downgrade: the usual starter sits for a less experienced QB.
                qb = self.primary_qb(team, week)
                starter = game[f"{side}_qb_id"]
                qb_out = 0.0
                if qb and starter and starter != qb[0]:
                    day = game["gameday"]
                    if self.career_starts(starter, day) < self.career_starts(qb[0], day):
                        qb_out = 1.0
            setattr(f, f"qb_out_{side}", qb_out)
            for inj in notable:
                f.notes.append(f"{team} {inj.position} {inj.name} {inj.status.lower()}")
            if not actual_qb and (team, week) not in self.injuries:
                f.notes.append(f"{team}: no game-status designations yet (usually posted "
                               "Friday); rerun closer to kickoff")
        return f
