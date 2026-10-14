"""UFC: fight history and fighter bios from ESPN, and a fight model learned from them.

The model rates every fighter from his UFC results (an Elo rating that carries across
weight classes) and turns the gap, together with age, reach and experience, into a win
chance with weights fit on past fights (`python -m nfl_edge.ufc --write` refits
ufc_weights.json and prints how it did on the newest fights it never saw)."""

from __future__ import annotations

import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import data

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard?dates={}"
ATHLETE = "https://site.web.api.espn.com/apis/common/v3/sports/mma/athletes/{}"
FIRST_SEASON = 2014
CURRENT_MAX_AGE = 3 * 3600
TRYOUTS = "Dana White's Contender Series"  # not offered: tryouts, fighters new to the UFC  # this year's cards: results and line-ups change
WEIGHTS_FILE = Path(__file__).with_name("ufc_weights.json")


def _get(url: str, tries: int = 3) -> dict:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * 2 ** i)  # ESPN turns away bursts; wait and retry


def _event_on(event_id: str, start: datetime) -> dict | None:
    """ESPN files a card under its US date, which can differ from the UTC one."""
    for shift in (-5, 0, -24):
        day = (start + timedelta(hours=shift)).strftime("%Y%m%d")
        try:
            events = _get(SCOREBOARD.format(day)).get("events", [])
        except Exception:
            continue
        for e in events:
            if e.get("id") == event_id:
                return e
    return None


def _bout(event: dict, comp: dict) -> dict | None:
    sides = comp.get("competitors") or []
    if len(sides) != 2:
        return None
    a, b = sorted(sides, key=lambda s: s.get("order", 0))
    status = (comp.get("status") or {}).get("type") or {}
    winner = "a" if a.get("winner") else "b" if b.get("winner") else None
    return {
        "id": comp.get("id"),
        "event_id": event.get("id"),
        "event": event.get("name", ""),
        "date": comp.get("date") or event.get("date"),
        "weight": (comp.get("type") or {}).get("abbreviation", ""),
        "rounds": ((comp.get("format") or {}).get("regulation") or {}).get("periods", 3),
        "a": a.get("id"), "a_name": (a.get("athlete") or {}).get("displayName", ""),
        "b": b.get("id"), "b_name": (b.get("athlete") or {}).get("displayName", ""),
        "done": bool(status.get("completed")),
        "state": status.get("state", ""),
        "round": (comp.get("status") or {}).get("period"),
        "clock": (comp.get("status") or {}).get("clock"),
        "winner": winner,
    }


def season(year: int, max_age: float | None = None) -> list[dict]:
    """Every bout on the year's UFC cards, finished or not, cached (past years for good)."""
    path = data.cache_dir() / f"ufc_fights_{year}.json"
    if max_age is None:
        max_age = float("inf") if year < date.today().year else CURRENT_MAX_AGE
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        return json.loads(path.read_text())
    try:
        calendar = _get(SCOREBOARD.format(year))["leagues"][0]["calendar"]
    except Exception:
        if path.exists():
            return json.loads(path.read_text())  # stale beats nothing
        raise data.DataError("Could not reach ESPN for the UFC schedule")
    entries = []
    for c in calendar:
        try:
            eid = c["event"]["$ref"].split("/events/")[1].split("?")[0]
            entries.append((eid, datetime.fromisoformat(c["startDate"].replace("Z", "+00:00"))))
        except (KeyError, IndexError, ValueError):
            continue
    with ThreadPoolExecutor(8) as pool:
        events = list(pool.map(lambda e: _event_on(*e), entries))
    bouts = []
    for e in events:
        for comp in (e or {}).get("competitions", []):
            bout = _bout(e, comp)
            if bout:
                bouts.append(bout)
    bouts.sort(key=lambda b: (b["date"] or "", b["id"] or ""))
    path.write_text(json.dumps(bouts))
    return bouts


def history(first: int = FIRST_SEASON, last: int | None = None) -> list[dict]:
    last = last or date.today().year
    out = []
    for year in range(first, last + 1):
        out.extend(season(year))
    seen, unique = set(), []
    for b in out:  # a card on New Year's can show up in both years
        if b["id"] not in seen:
            seen.add(b["id"])
            unique.append(b)
    return sorted(unique, key=lambda b: (b["date"] or "", b["id"] or ""))


# ---------------------------------------------------------------- fighter bios

def _inches(text: str | None) -> float | None:
    if not text:
        return None
    text = text.replace('"', "").strip()
    try:
        if "'" in text:
            feet, inches = text.split("'")
            return int(feet) * 12 + float(inches or 0)
        return float(text)
    except ValueError:
        return None


def _born(text: str | None) -> date | None:
    try:
        return datetime.strptime(text, "%d/%m/%Y").date() if text else None
    except ValueError:
        return None


def _bio(athlete_id: str) -> dict:
    try:
        a = _get(ATHLETE.format(athlete_id)).get("athlete", {})
    except Exception:
        return {}
    return {
        "born": a.get("displayDOB"),
        "reach": _inches(a.get("displayReach")),
        "height": _inches(a.get("displayHeight")),
        "stance": (a.get("stance") or {}).get("text"),
    }


def bios(ids: set[str]) -> dict[str, dict]:
    """{fighter id: born, reach, height, stance}, cached; only new fighters are fetched."""
    path = data.cache_dir() / "ufc_athletes.json"
    known = json.loads(path.read_text()) if path.exists() else {}
    missing = sorted(i for i in ids if i and i not in known)
    if missing:
        with ThreadPoolExecutor(8) as pool:
            for i, bio in zip(missing, pool.map(_bio, missing)):
                if bio:
                    known[i] = bio
        path.write_text(json.dumps(known))
    return known


# ---------------------------------------------------------------- the model

START_ELO = 1500.0
K = 32.0  # rating points a fight can move
NEW_K = 2.0  # how much faster a newcomer's rating moves (fades over his first fights)
FINISH_K = 1.25  # a knockout or submission says more than a decision
FIRST_TRAIN = "2020"  # older fights only warm up the ratings (newer ones predicted better)
TRAIN_UNTIL = "2024"  # weights are fit on fights before this; newer ones test them
WIN_FEATURES = ["elo", "age", "reach", "experience", "layoff", "form"]
DISTANCE_FEATURES = ["bias", "five", "women", "heavy", "light", "decisions", "mismatch"]
DISTANCE_PRIOR = 6.0  # fights of the league rate mixed into each fighter's own


def went_distance(b: dict) -> bool | None:
    """True when the fight lasted every scheduled round (a decision)."""
    if not b.get("done") or b.get("round") is None:
        return None
    return b["round"] >= b["rounds"] and (b.get("clock") or 0) >= 299


@dataclass
class Record:
    elo: float = START_ELO
    fights: int = 0
    decisions: int = 0
    last: date | None = None
    results: list = field(default_factory=list)  # 1 win, 0 loss, newest last


def _day(b: dict) -> date:
    return datetime.fromisoformat(b["date"].replace("Z", "+00:00")).date()


def _age(bio: dict | None, day: date) -> float | None:
    born = _born((bio or {}).get("born"))
    return (day - born).days / 365.25 if born else None


class Ratings:
    """Every fighter's record going into each fight, built in date order."""

    def __init__(self, bios: dict[str, dict] | None = None):
        self.fighters: dict[str, Record] = {}
        self.bios = bios or {}
        self.decision_rate = 0.5

    def rec(self, fid: str) -> Record:
        return self.fighters.setdefault(fid, Record())

    def features(self, b: dict) -> tuple[dict, dict]:
        """(win features for fighter a over b, fight-length features)."""
        day = _day(b)
        a, c = self.rec(b["a"]), self.rec(b["b"])
        ba, bc = self.bios.get(b["a"]), self.bios.get(b["b"])

        def diff(x, y, scale):
            return (x - y) / scale if x is not None and y is not None else 0.0

        def layoff(r):
            return min((day - r.last).days / 365.25, 3) if r.last else 1.0

        def form(r):
            last = r.results[-3:]
            return (sum(last) - len(last) / 2) / 3 if last else 0.0

        win = {
            "elo": (a.elo - c.elo) / 100,
            "age": diff(_age(ba, day), _age(bc, day), 5),
            "reach": diff((ba or {}).get("reach"), (bc or {}).get("reach"), 5),
            "experience": math.log1p(a.fights) - math.log1p(c.fights),
            "layoff": layoff(a) - layoff(c),
            "form": form(a) - form(c),
        }
        league = self.decision_rate

        def rate(r):
            return (r.decisions + DISTANCE_PRIOR * league) / (r.fights + DISTANCE_PRIOR)

        weight = b.get("weight", "")
        length = {
            "bias": 1.0,
            "five": 1.0 if b.get("rounds") == 5 else 0.0,
            "women": 1.0 if weight.startswith("W ") else 0.0,
            "heavy": 1.0 if weight in ("Heavyweight", "Light Heavyweight") else 0.0,
            "light": 1.0 if weight in ("Flyweight", "Bantamweight", "W Strawweight",
                                       "W Flyweight") else 0.0,
            "decisions": ((rate(a) + rate(c)) / 2 - league) * 4,
            "mismatch": abs(a.elo - c.elo) / 100,
        }
        return win, length

    def update(self, b: dict) -> None:
        if not b.get("done") or b.get("winner") is None:
            return
        a, c = self.rec(b["a"]), self.rec(b["b"])
        distance = went_distance(b)
        expect = 1 / (1 + 10 ** ((c.elo - a.elo) / 400))
        score = 1.0 if b["winner"] == "a" else 0.0
        k = K * (FINISH_K if distance is False else 1.0)
        a.elo += k * (1 + NEW_K / (a.fights + 1)) * (score - expect)
        c.elo -= k * (1 + NEW_K / (c.fights + 1)) * (score - expect)
        day = _day(b)
        for r, won in ((a, score), (c, 1 - score)):
            r.fights += 1
            r.decisions += 1 if distance else 0
            r.last = day
            r.results.append(won)
        n = sum(r.fights for r in self.fighters.values()) / 2
        self.decision_rate += ((1.0 if distance else 0.0) - self.decision_rate) / max(n, 50)


def replay(bouts: list[dict], bios: dict[str, dict]):
    """(Ratings after every finished fight, [(bout, win features, length features)])
    with each fight's features taken before it happened."""
    ratings, rows = Ratings(bios), []
    for b in bouts:
        if not b.get("done"):
            continue
        win, length = ratings.features(b)
        rows.append((b, win, length))
        ratings.update(b)
    return ratings, rows


def _p(weights: dict, feats: dict) -> float:
    from .signals import sigmoid

    return sigmoid(sum(weights.get(k, 0.0) * v for k, v in feats.items()))


def win_prob(weights: dict, feats: dict) -> float:
    return _p(weights["win"], feats)


def distance_prob(weights: dict, feats: dict) -> float:
    return _p(weights["distance"], feats)


# ---------------------------------------------------------------- fitting and testing

def _fit(rows, names, mirror: bool):
    from .signals import fit_weights

    data_rows = []
    for feats, y in rows:
        data_rows.append((0.5, feats, y))
        if mirror:  # the same fight seen from the other corner
            data_rows.append((0.5, {k: -v for k, v in feats.items()}, not y))
    return fit_weights(data_rows, names, ridge=1.0)


def _score(rows, weights) -> dict:
    """Log loss, how often the likelier side won, and a calibration table."""
    if not rows:
        return {}
    loss, right, buckets = 0.0, 0, {}
    for feats, y in rows:
        p = min(max(_p(weights, feats), 1e-6), 1 - 1e-6)
        loss -= math.log(p if y else 1 - p)
        fav = max(p, 1 - p)
        right += (p >= 0.5) == bool(y)
        band = min(int(fav * 10) / 10, 0.9)
        n, w, tot = buckets.get(band, (0, 0, 0.0))
        buckets[band] = (n + 1, w + ((p >= 0.5) == bool(y)), tot + fav)
    return {"n": len(rows), "log_loss": round(loss / len(rows), 4),
            "likelier_won": round(right / len(rows), 4),
            "bands": {f"{int(k * 100)}-{int(k * 100) + 10}%":
                      [n, round(w / n, 3), round(t / n, 3)] for k, (n, w, t) in sorted(buckets.items())}}


def _stretch(rows, weights) -> float:
    """How much to sharpen (>1) or soften (<1) the chances to fit `rows` best."""
    def loss(scale):
        tot = 0.0
        for feats, y in rows:
            p = _p({k: v * scale for k, v in weights.items()}, feats)
            p = min(max(p, 1e-6), 1 - 1e-6)
            tot -= math.log(p if y else 1 - p)
        return tot
    return min((x / 20 for x in range(14, 41)), key=loss)


def build(first_train: str = FIRST_TRAIN) -> dict:
    """Fit the win and fight-length weights on fights from `first_train` up to
    TRAIN_UNTIL, test them on the newer ones, then refit on everything for use. The win
    chances are sharpened by a stretch learned on the two years before the test."""
    global K, NEW_K
    bouts = history()
    ids = {b["a"] for b in bouts} | {b["b"] for b in bouts}
    known = bios(ids)
    best = None
    for k in (48.0, 64.0, 96.0, 128.0, 192.0):
        for new_k in (0.0, 1.0, 2.0):
            K, NEW_K = k, new_k
            _, rows = replay(bouts, known)
            elo = [({"elo": w["elo"]}, b["winner"] == "a") for b, w, _ in rows
                   if b["winner"] and first_train <= b["date"] < TRAIN_UNTIL]
            weights = _fit(elo, ["elo"], mirror=True)
            ll = _score(elo, weights)["log_loss"]
            if best is None or ll < best[0]:
                best = (ll, k, new_k)
    K, NEW_K = best[1], best[2]
    final, rows = replay(bouts, known)
    win = [(w, b["winner"] == "a", b["date"]) for b, w, _ in rows if b["winner"]]
    length = [(ln, went_distance(b), b["date"]) for b, _, ln in rows
              if went_distance(b) is not None]

    def part(items, lo, hi=None):
        return [(f, y) for f, y, d in items if lo <= d and (hi is None or d < hi)]

    def scaled(weights, s):
        return {k: v * s for k, v in weights.items()}

    check = str(int(TRAIN_UNTIL) - 2)  # the stretch is learned on these two years
    early = _fit(part(win, first_train, check), WIN_FEATURES, mirror=True)
    stretch = _stretch(part(win, check, TRAIN_UNTIL), early)
    train_w, test_w = part(win, first_train, TRAIN_UNTIL), part(win, TRAIN_UNTIL)
    train_d, test_d = part(length, first_train, TRAIN_UNTIL), part(length, TRAIN_UNTIL)
    win_weights = scaled(_fit(train_w, WIN_FEATURES, mirror=True), stretch)
    elo_only = _fit([({"elo": f["elo"]}, y) for f, y in train_w], ["elo"], mirror=True)
    dist_weights = _fit(train_d, DISTANCE_FEATURES, mirror=False)
    base = sum(y for _, y in train_d) / len(train_d)
    from .signals import logit

    test = {
        "win": _score(test_w, win_weights),
        "win_elo_only": _score([({"elo": f["elo"]}, y) for f, y in test_w], elo_only),
        "distance": _score(test_d, dist_weights),
        "distance_base_rate": _score(test_d, {"bias": logit(base)}),
        "trained": f"{first_train} to {int(TRAIN_UNTIL) - 1}",
        "tested": f"{TRAIN_UNTIL} on",
    }
    return {
        "elo": {"k": K, "new_k": NEW_K},
        "stretch": stretch,
        "win": scaled(_fit(train_w + test_w, WIN_FEATURES, mirror=True), stretch),
        "distance": _fit(train_d + test_d, DISTANCE_FEATURES, mirror=False),
        "test": test,
        "ratings": snapshot(final, bouts),
    }


_weights: dict | None = None


def weights() -> dict:
    global _weights, K, NEW_K
    if _weights is None:
        _weights = json.loads(WEIGHTS_FILE.read_text()) if WEIGHTS_FILE.exists() else {}
        if _weights.get("elo"):
            K, NEW_K = _weights["elo"]["k"], _weights["elo"]["new_k"]
    return _weights


def main(argv=None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Fit and test the UFC fight model.")
    p.add_argument("--write", action="store_true", help=f"save to {WEIGHTS_FILE.name}")
    args = p.parse_args(argv)
    result = build()
    print(json.dumps(result, indent=1))
    if args.write:
        WEIGHTS_FILE.write_text(json.dumps(result, indent=1) + "\n")
        print(f"wrote {WEIGHTS_FILE}")



# ---------------------------------------------------------------- cards, bets, grading

def last_name(name: str) -> str:
    return name.split()[-1] if name else name


def fights_by_id(year: int) -> dict[str, dict]:
    return {b["id"]: b for b in season(year)}


def card_numbers(year: int) -> dict[str, int]:
    """{fight id: which card of the year it's on (1 = the first)} for grouping history."""
    order = {}
    for b in season(year):
        order.setdefault(b["event_id"], len(order) + 1)
    return {b["id"]: order[b["event_id"]] for b in season(year)}


def grade_leg(leg: dict, b: dict) -> tuple[str, str]:
    if not b.get("done"):
        return "pending", "not final yet"
    winner = b["a_name"] if b["winner"] == "a" else b["b_name"] if b["winner"] == "b" else None
    distance = went_distance(b)
    how = ("went the distance" if distance else
           f"finished in round {b.get('round')}" if distance is False else "")
    detail = f"{winner or 'no winner (draw or no contest)'} · {how}".strip(" ·")
    if leg["type"] == "fight":
        if winner is None:
            return "push", detail  # a draw or no contest is refunded
        return ("won" if winner == leg["fighter"] else "lost"), detail
    if leg["type"] == "distance":
        if distance is None:
            return "pending", "result not posted yet"
        return ("won" if distance == leg["yes"] else "lost"), detail
    return "pending", f"unknown leg type {leg['type']}"


def upcoming_cards(now: datetime | None = None) -> list[tuple[str, str, list[dict]]]:
    """[(event id, event name, its fights not yet started)] soonest first."""
    now = now or datetime.now(timezone.utc)
    years = {now.year} | ({now.year + 1} if now.month == 12 else set())
    cards: dict[str, tuple[str, list[dict]]] = {}
    for year in sorted(years):
        try:
            bouts = season(year)
        except data.DataError:
            continue
        for b in bouts:
            when = datetime.fromisoformat(b["date"].replace("Z", "+00:00"))
            if b["event"].startswith(TRYOUTS):  # tryout fighters: no UFC record to go on
                continue
            if not b["done"] and b.get("state", "pre") == "pre" and when > now - timedelta(hours=8):
                cards.setdefault(b["event_id"], (b["event"], []))[1].append(b)
    out = [(eid, name, sorted(bs, key=lambda b: b["date"])) for eid, (name, bs) in cards.items()]
    return sorted(out, key=lambda c: c[2][0]["date"])


def snapshot(ratings: Ratings, bouts: list[dict]) -> dict:
    """Every fighter's record as of the newest finished fight, shipped in the builds so a
    fresh install doesn't download every card since FIRST_SEASON (see current_ratings)."""
    done = [b["date"] for b in bouts if b.get("done")]
    return {"asof": max(done) if done else "", "decision_rate": round(ratings.decision_rate, 4),
            "fighters": {i: [round(r.elo, 2), r.fights, r.decisions,
                             r.last.isoformat() if r.last else None, r.results[-3:]]
                         for i, r in ratings.fighters.items() if r.fights}}


def current_ratings() -> Ratings:
    """Every fighter's record after the latest finished fight: the shipped snapshot plus
    the fights since (only this year's cards are downloaded), or, without a snapshot,
    every card since FIRST_SEASON. Bios are fetched as fights are priced (chances)."""
    snap = weights().get("ratings")  # also loads the fitted Elo settings
    if not snap:
        bouts = history()
        known = bios({b["a"] for b in bouts} | {b["b"] for b in bouts})
        ratings, _ = replay(bouts, known)
        return ratings
    ratings = Ratings({})
    ratings.decision_rate = snap["decision_rate"]
    for i, (elo, fights, decisions, last, results) in snap["fighters"].items():
        ratings.fighters[i] = Record(elo, fights, decisions,
                                     date.fromisoformat(last) if last else None, list(results))
    newer = history(int(snap["asof"][:4]))
    seen = {b["id"] for b in newer if b["date"] <= snap["asof"]}
    for b in newer:
        if b["date"] > snap["asof"] and b["id"] not in seen:
            ratings.update(b)
    return ratings


def chances(b: dict, ratings: Ratings) -> tuple[float, float]:
    """(chance fighter a wins, chance the fight goes the distance)."""
    w = weights()
    if not w:
        raise data.DataError("The UFC model isn't fitted yet (python -m nfl_edge.ufc --write)")
    ratings.bios.update(bios({b["a"], b["b"]}))
    win, length = ratings.features(b)
    return win_prob(w, win), distance_prob(w, length)


if __name__ == "__main__":
    main()
