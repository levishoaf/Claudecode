"""What the Discord bot says, kept apart from Discord itself so it can be tested.

Every function here takes plain values and returns plain text (Discord
markdown). discord_bot.py wires them to slash commands.

The server shares one bet history: a tracker ledger (nfl_edge/tracker.py)
whose placed bets also record who placed them ("by", "by_id").
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime

from . import board, learn, tracker
from .lines import book_name
from .board import EASTERN, cfb_board, nfl_board, season_for
from .cli import _describe, _fmt_american, clock, format_slip
from .odds import american_to_decimal, decimal_to_american
from .parlays import Parlay
from .picks import (SINGLE_MIN_PROB, single_min, mixed_parlays, payout_parlays, rank_singles, worth_it,
                    worth_parlay)

MAX_MESSAGE = 1900  # Discord allows 2,000 characters per message
CACHE_SECONDS = 300
SEARCH_RANGE = (0.03, 0.97)  # /chance looks through bets of nearly any chance
SPORT_NAME = {"nfl": "NFL", "ncaaf": "College football", "ufc": "UFC", "mlb": "MLB",
              "nba": "NBA", "multi": "Multi-Lays"}
RESULT_ICON = {"won": "✅ WON", "lost": "❌ LOST", "push": "➖ PUSH", "pending": "⏳"}
NOTE_TAG = {"Questionable": " `Q`", "Did not practice": " `DNP`", "No line yet": " *no line yet*"}


# ------------------------------------------------------------------ boards

class Boards:
    """Candidate bets per (sport, week, chance range), rebuilt every few minutes."""

    def __init__(self, ttl: float = CACHE_SECONDS):
        self.ttl = ttl
        self._cache: dict[tuple, tuple[float, list]] = {}
        self._lock = threading.Lock()

    def get(self, sport: str, week: int | None, lo: float, hi: float,
            every_line: bool = False, sure_only: bool = True) -> list:
        key = (sport, week, round(lo, 3), round(hi, 3), every_line, sure_only)
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < self.ttl:
                return hit[1]
        if sport == "ufc":  # the next card's fights
            bets = board.ufc_board(lo, hi)
        elif sport == "mlb":  # the next 7 days (a day or two of playoff games is too few)
            bets = board.mlb_board(lo, hi, all_days=True, every_line=True)
            if sure_only and not every_line:  # totals only when picked: lines differ
                bets = [b for b in bets if b.market != "totals_runs"]
        elif sport == "nba":  # the next 7 days; spreads and totals only when picked
            bets = board.nba_board(lo, hi, all_days=True, every_line=True)
            if sure_only and not every_line:
                bets = [b for b in bets if b.market == "h2h"]
        else:
            bets = (cfb_board(None, lo, hi, week=week) if sport == "ncaaf"
                    else nfl_board(None, lo, hi, week=week, every_line=every_line,
                                   always_offered=sure_only and not every_line))
        try:  # real sportsbook odds, with an Odds API key: FanDuel's price and the market's chance
            import bets as launcher
            from . import realodds

            offers = launcher.real_offers(sport, week if sport == "nfl" else None)
            if offers:
                bets = [b for b in realodds.apply(bets, sport, offers)
                        if lo <= b.fair_prob <= hi]
        except Exception:  # never block the bets
            pass
        if sport == "nfl":
            try:  # confidence tiers: matchup, usage, floor, injuries, rest, weather
                from . import insights

                insights.annotate(bets, "nfl", season_now())
            except Exception:  # extra information: never block the bets
                pass
        with self._lock:
            self._cache[key] = (time.monotonic(), bets)
        return bets


def label(b) -> str:
    return f"{_describe(b)} ({b.game})"


def when(b) -> str:
    return clock(b.commence_time.astimezone(EASTERN)) + " ET"


def break_even(p: float) -> str:
    return _fmt_american(decimal_to_american(1 / p))


def week_of(bets, sport: str = "nfl") -> str:
    if sport == "ufc":  # a UFC card, not a week
        return board.NOTES.get("ufc_card", "next card")
    if sport == "mlb":
        return board.NOTES.get("mlb_day", "next 7 days")
    if sport == "nba":
        return board.NOTES.get("nba_day", "next 7 days")
    if sport == "multi":
        return "every league"
    weeks = sorted({b.week for b in bets if b.week})
    return f"Week {weeks[0]}" if len(weeks) == 1 else (
        f"Weeks {weeks[0]}–{weeks[-1]}" if weeks else "this week")


def singles_text(singles, sport: str, wager: float) -> str:
    """One bold line per bet (tier dot, bet, chance, odds, payout), then its game, time and
    anything to watch."""
    if not singles:
        return (f"No bets at {single_min(sport):.0%} or better here. Pick a `bet_type` to see "
                "every bet of that type, or try a wider chance range.")
    on_fd = any(getattr(b, "on_fanduel", False) for b in singles)
    lines = [f"**{SPORT_NAME[sport]} · {week_of(singles, sport)} · {len(singles)} likeliest "
             "singles**"]
    for i, b in enumerate(singles, 1):
        if getattr(b, "on_fanduel", False):
            odds, pays = _fmt_american(b.fd_price), wager * american_to_decimal(b.fd_price)
        else:
            odds, pays = break_even(b.fair_prob), wager / b.fair_prob
        ins = getattr(b, "insight", None)
        dot = TIER_ICON[ins.tier] + " " if ins is not None else ""
        tag = NOTE_TAG.get(b.note, "") + (" · *confirm line*" if b.confirm_line else "")
        more = [b.game, when(b)]
        best = getattr(b, "best_price", None)
        if getattr(b, "on_fanduel", False) and best is not None and best > b.fd_price:
            more.append(f"better {_fmt_american(best)} at {book_name(b.best_book)}")
        if getattr(b, "check", False):
            more.append("⚠ far from other books, check the news")
        if ins is not None:
            more += [f"⚠ {f}" for f in ins.flags[:2]]
        ok, why = worth_it(b)
        lines.append(f"**{i}.** {'👍' if ok else '👎'} {dot}**{_describe(b)}**{tag} — "
                     f"**{b.fair_prob:.0%}** · {odds} · ${wager:g} pays ${pays:,.2f}\n   "
                     + " · ".join(more))
    ups = sum(worth_it(b)[0] for b in singles)
    lines.append(f"👍 {ups} of {len(singles)} worth betting at FanDuel's price; 👎 means not worth "
                 "it, or not sure (no FanDuel price to check, a doubtful price, an uncertain "
                 "player or Weak signals).")
    lines.append("Odds marked with a FanDuel price are FanDuel's; the rest are break-even: bet "
                 "only if FanDuel pays that or better." if on_fd else
                 "Odds are break-even: bet only if FanDuel pays that or better. Use `/chance` "
                 "with FanDuel's price to see the real value.")
    return "\n".join(lines)


def edges_text(found, sport: str, wager: float) -> str:
    """The league's edges, best first: one bold line each (bet, how often it wins, FanDuel's
    odds, the edge and its average profit, the payout), then its game, time and the worst
    odds that keep the edge."""
    from . import edges

    rule = (f"An edge wins {edges.EDGE_MIN_PROB:.0%}+ of the time and FanDuel pays "
            f"{edges.EDGE_MIN_EV:+.0%}+ more than that chance is worth (every other book's "
            "price, cut taken out). Thin edges are left out.")
    if not found:
        return f"**{SPORT_NAME[sport]} · edges**\n{edges.none_note()}"
    lines = [f"**{SPORT_NAME[sport]} · {len(found)} edge{'s' * (len(found) != 1)}, best first**",
             f"_{rule}_"]
    for i, b in enumerate(found, 1):
        pays = wager * american_to_decimal(b.fd_price)
        more = [b.game.split(", ")[0], when(b),
                f"still an edge down to {_fmt_american(edges.worst_odds(b.fair_prob))}"]
        best = getattr(b, "best_price", None)
        if best is not None and best > b.fd_price:
            more.append(f"better {_fmt_american(best)} at {book_name(b.best_book)}")
        lines.append(f"**{i}. {_describe(b)}** — wins **{b.fair_prob:.0%}** · FanDuel "
                     f"{_fmt_american(b.fd_price)} · edge **{b.ev:+.1%}** (about "
                     f"+${edges.profit_per(b, wager):,.2f} per ${wager:g}) · ${wager:g} pays "
                     f"${pays:,.2f}\n   " + " · ".join(more))
    return "\n".join(lines)


def leans_text(found, sport: str, wager: float) -> str:
    """FanDuel lines worth a look: not edges, with what each misses of the edge rule."""
    from . import edges

    if not found:
        return (f"**{SPORT_NAME[sport]} · worth a look**\nNothing right now: no FanDuel line "
                f"pays even {edges.LEAN_MIN_EV:+.0%} over what it's worth.")
    lines = [f"**{SPORT_NAME[sport]} · worth a look ({len(found)}) — not edges**",
             f"_FanDuel pays {edges.LEAN_MIN_EV:+.0%}+ over what these are worth, but each "
             f"misses the edge rule (a {edges.EDGE_MIN_EV:+.0%} edge and a "
             f"{edges.EDGE_MIN_PROB:.0%}+ win chance). Thinner or riskier: bet with care._"]
    for i, b in enumerate(found, 1):
        pays = wager * american_to_decimal(b.fd_price)
        lines.append(f"**{i}. {_describe(b)}** — wins **{b.fair_prob:.0%}** · FanDuel "
                     f"{_fmt_american(b.fd_price)} · value {b.ev:+.1%} · ${wager:g} pays "
                     f"${pays:,.2f}\n   {b.game.split(', ')[0]} · {when(b)} · "
                     f"{edges.why_not_edge(b)}")
    return "\n".join(lines)


def edge_parlays_text(parlays, wager: float) -> list[str]:
    """Edge parlays as numbered slips (P1, P2...): every leg an edge, any league."""
    from . import edges

    if not parlays:
        return ["No edge parlays right now: they need edges in at least two different games."]
    out = [f"**{len(parlays)} edge parlay{'s' * (len(parlays) != 1)}** (place one with "
           "`/place bet:P1`)\n_Built only from edges, 2-3 legs from different games, "
           "likeliest first. A parlay wins less often than any of its legs._"]
    for i, p in enumerate(parlays, 1):
        legs = "\n".join(f"   • {_describe(b)} ({SPORT_NAME.get(getattr(b, 'league', ''), '')}, "
                         f"{b.game.split(', ')[0]}) {_fmt_american(b.fd_price)} · wins "
                         f"{b.fair_prob:.0%} · edge {b.ev:+.1%}" for b in p.legs)
        out.append(f"**P{i}** — wins **{p.win_prob:.0%}** · FanDuel about "
                   f"{_fmt_american(p.american)} · edge **{p.ev:+.1%}** (about "
                   f"+${edges.profit_per(p, wager):,.2f} per ${wager:g}) · ${wager:g} pays "
                   f"${wager * p.decimal:,.2f}\n{legs}")
    return out


SINGLES_NOTE = [None]  # "Singles have won 62% of the time and parlays 17%..." (set by the bot)

TIER_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def tier_text(b) -> str:
    """A bet's confidence tier and why, plus its warnings, as extra lines."""
    ins = getattr(b, "insight", None)
    if ins is None:
        return ""
    from .insights import TIER_WORDS

    why = ", ".join(ins.reasons) or "no strong signals either way"
    out = f"\n   {TIER_ICON[ins.tier]} {TIER_WORDS[ins.tier]}: {why}"
    raw = getattr(b, "raw_prob", None)
    if raw is not None and abs(raw - b.fair_prob) >= 0.02:
        out += f" · model said {raw:.0%}, learned {b.fair_prob:.0%}"
    return out + "".join(f"\n   ⚠ {f}" for f in ins.flags[:2])


def legs_tier_text(legs) -> str:
    from .insights import TIER_WORDS

    tiers = [getattr(b, "tier", None) for b in legs]
    if not any(tiers):
        return ""
    return "\nLegs: " + ", ".join(f"{TIER_ICON[t]} {tiers.count(t)} {TIER_WORDS[t]}"
                                  for t in ("green", "yellow", "red") if tiers.count(t))


def parlays_text(parlays, sport: str, wager: float) -> list[str]:
    """One message per parlay, each a FanDuel-style slip."""
    if not parlays:
        return ["Not enough games for those parlays."]
    out = [f"**{SPORT_NAME[sport]} · {week_of([b for p in parlays for b in p.legs], sport)} · "
           f"top {len(parlays)} parlays** (place one with `/place bet:P1`)"
           + (f"\n{SINGLES_NOTE[0]}" if SINGLES_NOTE[0] else "")]
    for i, p in enumerate(parlays, 1):
        on_fd = all(getattr(b, "on_fanduel", False) for b in p.legs)
        slip = "\n".join(format_slip(p, wager, break_even=not on_fd))
        odds = (f"FanDuel {_fmt_american(p.american)} · EV {p.ev:+.1%}" if on_fd
                else f"worst odds {break_even(p.win_prob)}")
        ok, why = worth_parlay(p)
        out.append(f"**P{i}** {'👍' if ok else '👎'} · {len(p.legs)} legs · "
                   f"**{p.win_prob:.0%}** to win · {odds}\n{why}"
                   f"{legs_tier_text(p.legs)}\n```\n{slip}\n```")
    return out


# ------------------------------------------------------------------ search

SYNONYMS = {"moneyline": "ml", "money": "ml", "line": "", "to": "", "win": "",
            "yards": "yds", "yard": "yds", "rec": "receptions", "td": "td", "tds": "tds",
            "o": "over", "u": "under", "tt": "total"}


def _words(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9.+\-']+", text.lower())
    return [w2 for w in words if (w2 := SYNONYMS.get(w, w))]


NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?\+?$")


def _score(want: list[str], pick: list[str], game: list[str]) -> float:
    """How well query words match a bet: its own name counts most, then the game.
    A number in the query (a line like +11.5) must match the bet's line exactly."""
    score = 0.0
    for w in want:
        if NUMBER.match(w):
            bare = w.lstrip("+").rstrip("+")
            nums = {x.lstrip("+").rstrip("+") for x in pick if NUMBER.match(x)}
            signed = {x for x in pick if NUMBER.match(x)}
            if w in signed or (w[0] not in "+-" and bare in nums):
                score += 3
            else:
                return float("-inf")  # a different line is a different bet
        elif w in pick:
            score += 3
        elif len(w) >= 3 and any(x.startswith(w) for x in pick):
            score += 2
        elif w in game or (len(w) >= 3 and any(x.startswith(w) for x in game)):
            score += 1
    return score


def search(bets, query: str, limit: int = 3) -> list:
    """Bets that best match the query ('ravens +11.5', 'gesicki 2+ rec')."""
    want = _words(query)
    if not want:
        return []
    scored = []
    for b in bets:
        s = _score(want, _words(_describe(b)), _words(b.game))
        if s > 0:
            scored.append((s, b.fair_prob, b))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    if not scored:
        return []
    best = scored[0][0]
    return [b for s, _, b in scored if s >= best - 1][:limit]


def parse_odds(text: str | None) -> int | None:
    """'-150' / '+240' / '240' -> int; None if blank. ValueError if not odds."""
    if text is None or not str(text).strip():
        return None
    odds = int(str(text).strip().replace("+", ""))
    if abs(odds) < 100:
        raise ValueError("odds look like -150 or +240")
    return odds


def chance_text(matches, odds: int | None, wager: float) -> str:
    if not matches:
        return "Couldn't find that bet. Try the team or player name and the line, like `Ravens +11.5`."
    b, others = matches[0], matches[1:]
    lines = [f"**{_describe(b)}**{NOTE_TAG.get(b.note, '')} — {b.game}, {when(b)}",
             f"Chance to win: **{b.fair_prob:.0%}** · worst odds worth taking: "
             f"**{break_even(b.fair_prob)}** · ${wager:g} pays ${wager / b.fair_prob:,.2f} "
             "at those odds"]
    if odds is not None:
        ev = b.fair_prob * american_to_decimal(odds) - 1
        lines.append(f"At {_fmt_american(odds)}: expected value **{ev:+.1%}** "
                     f"({'+' if ev >= 0 else '-'}${abs(ev * wager):,.2f} per ${wager:g}) → "
                     f"**{'worth it' if ev > 0 else 'not worth it'}**; "
                     f"${wager:g} pays ${wager * american_to_decimal(odds):,.2f}")
    if others:
        lines.append("Also matched: " + "; ".join(f"{_describe(o)} ({o.fair_prob:.0%})"
                                                  for o in others))
    return "\n".join(lines)


# ------------------------------------------------------------------ ledger

class ServerLedger:
    """The server's shared bet history, saved to one JSON file."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        entries = tracker.all_entries(tracker.load(path))
        learn.remember(entries)  # learn from past results
        SINGLES_NOTE[0] = tracker.singles_vs_parlays(entries)

    def load(self) -> dict:
        return tracker.load(self.path)

    def update(self, change) -> dict:
        """Load, apply change(ledger), save; returns the ledger."""
        with self.lock:
            ledger = tracker.load(self.path)
            change(ledger)
            tracker.save(ledger, self.path)
        learn.remember(tracker.all_entries(ledger))  # the next boards learn from the results
        SINGLES_NOTE[0] = tracker.singles_vs_parlays(tracker.all_entries(ledger))
        return ledger

    def record_shown(self, entries: list[dict], edge: bool = False, lean: bool = False) -> None:
        self.update(lambda ledger: tracker.record_generated(ledger, entries, edge=edge,
                                                            lean=lean))

    def place(self, entry: dict, odds: int | None, stake: float, user_id: int,
              user_name: str) -> dict:
        """Log a member's bet. Two members can place the same bet; each is kept."""
        mine = dict(entry, id=f"{entry['id']}#{user_id}", bet_id=entry["id"])
        placed: dict = {}

        def change(ledger):
            placed.update(tracker.place(ledger, mine, odds, stake))
            ledger["placed"][-1].update(by=user_name, by_id=user_id)
        self.update(change)
        return dict(placed, by=user_name, by_id=user_id)

    def remove(self, user_id: int, query: str) -> dict | None:
        """Remove the caller's placed bet that best matches the query."""
        removed: dict = {}

        def change(ledger):
            mine = [e for e in ledger["placed"] if e.get("by_id") == user_id]
            want = _words(query)
            best = max(mine, key=lambda e: sum(w in " ".join(_words(entry_title(e)))
                                               for w in want), default=None)
            if best and any(w in " ".join(_words(entry_title(best))) for w in want):
                ledger["placed"] = [e for e in ledger["placed"] if e is not best]
                removed.update(best)
        self.update(change)
        return removed or None

    def regrade(self) -> list[dict]:
        """Grade finished games. Returns the bets that settled just now."""
        before: dict[str, str] = {}
        settled: list[dict] = []

        def change(ledger):
            for e in tracker.all_entries(ledger):
                before[e["id"]] = e["status"]
            tracker.withdraw_injured(ledger)  # since listed Out/Doubtful/Questionable
            tracker.regrade(ledger)
            tracker.annotate_weeks(ledger)
            settled.extend(e for e in tracker.all_entries(ledger)
                           if before.get(e["id"]) == "pending" and e["status"] != "pending")
        self.update(change)
        return settled


def entry_title(e: dict) -> str:
    if e["kind"] == "single":
        return e["legs"][0].get("pick") or e["legs"][0]["label"]
    return f"{len(e['legs'])}-leg parlay: " + " · ".join(leg.get("pick") or leg["label"]
                                                       for leg in e["legs"])


def money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def placed_line(e: dict) -> str:
    who = e.get("by", "someone")
    head = f"{RESULT_ICON[e['status']]} **{entry_title(e)}** · {e['win_prob']:.0%}"
    if e["status"] == "pending":
        win = e["stake"] * (american_to_decimal(e["odds"]) - 1)
        return f"{head} · {who}: ${e['stake']:g} at {_fmt_american(e['odds'])} to win ${win:,.2f}"
    return (f"{head} · {who}: ${e['stake']:g} at {_fmt_american(e['odds'])}, "
            f"{money(tracker.profit(e))}")


def record_by_member(placed: list[dict]) -> list[str]:
    people: dict[str, list] = {}
    for e in placed:
        people.setdefault(e.get("by", "someone"), []).append(e)
    rows = []
    for name, bets in sorted(people.items(), key=lambda kv: -sum(tracker.profit(e) for e in kv[1])):
        rows.append(f"• **{name}**: {tracker.summary(bets, money=True)}")
    return rows


def history_text(ledger: dict, sport: str | None = None, week: int | None = None,
                 show_picks: bool = False) -> str:
    """The server's bet history: placed bets by sport and week, and how the builder's
    picks did. With show_picks, every pick is listed too."""
    placed = ledger["placed"]
    taken = {e.get("bet_id", e["id"]) for e in placed}
    # The builder's picks on lines FanDuel may not have offered don't count.
    picks = [e for e in ledger["generated"]
             if e["id"] not in taken and not tracker.model_line(e)]
    lines = ["**Server bet history**"]
    if placed:
        lines.append("Placed bets: " + tracker.summary(placed, money=True))
        lines += record_by_member(placed)
    else:
        lines.append("No placed bets yet. Log one with `/place`.")
    for key in (("nfl", "ncaaf", "ufc", "mlb", "nba") if sport is None else (sport,)):
        mine = [e for e in placed if e["sport"] == key]
        theirs = [e for e in picks if e["sport"] == key]
        weeks = sorted({e.get("week") for e in mine + theirs if week is None or e.get("week") == week},
                       key=lambda w: -(w or -1))
        if not weeks:
            continue
        lines.append(f"\n__**{SPORT_NAME[key]}**__")
        for w in weeks:
            wk_placed = [e for e in mine if e.get("week") == w]
            wk_picks = [e for e in theirs if e.get("week") == w]
            unit = "Card" if key == "ufc" else "Week"  # UFC: the year's card number
            lines.append(f"**{unit + ' ' + str(w) if w else 'Other'}**")
            for e in sorted(wk_placed, key=lambda e: (e["status"] == "pending",
                                                      tracker.first_kickoff(e))):
                lines.append(placed_line(e))
            if wk_picks:
                lines.append(f"Builder's picks you didn't place: "
                             f"{tracker.summary(wk_picks, money=False)}")
                if show_picks:
                    for e in sorted(wk_picks, key=lambda e: (e["status"] == "pending",
                                                             tracker.first_kickoff(e))):
                        lines.append(f"{RESULT_ICON[e['status']]} {entry_title(e)} · "
                                     f"{e['win_prob']:.0%}")
    return "\n".join(lines)


def settled_text(entries: list[dict]) -> str | None:
    """The results post after games finish: each placed bet, then the picks tally."""
    placed = [e for e in entries if e.get("by")]
    picks = [e for e in entries if not e.get("by") and not tracker.model_line(e)]
    if not placed and not picks:
        return None
    lines = ["**Results are in**"]
    lines += [placed_line(e) for e in placed]
    if picks:
        won = sum(e["status"] == "won" for e in picks)
        lost = sum(e["status"] == "lost" for e in picks)
        lines.append(f"Builder's picks just settled: {won} won, {lost} lost.")
    return "\n".join(lines)


# ------------------------------------------------------------------ injuries

def injuries_text(team: str | None = None) -> str:
    week, players, note = board.injury_report()
    if team:
        t = team.lower()
        players = [p for p in players if t in p["team"].lower() or t in p["team_name"].lower()]
    keep = [p for p in players if p["status"] in ("Out", "Doubtful", "Questionable")] or players
    if not keep:
        return f"No injuries listed{' for ' + team if team else ''}. {note}"
    lines = [f"**Week {week} injury report**{' · ' + team if team else ''}", note]
    current = None
    for p in keep:
        if p["team_name"] != current:
            current = p["team_name"]
            lines.append(f"__{current}__ ({p['opponent']})")
        injury = (p["injury"] or "").rstrip(" -")
        lines.append(f"• **{p['status']}** {p['player']} ({p['position']})"
                     + (f" — {injury}" if injury else ""))
    return "\n".join(lines)


# ------------------------------------------------------------------ helpers

def chunks(text: str, size: int = MAX_MESSAGE) -> list[str]:
    """Split long text on line breaks into Discord-sized messages."""
    out, cur = [], ""
    for line in text.split("\n"):
        if len(line) > size:  # a single very long line: finish this message, then split it
            if cur:
                out.append(cur)
                cur = ""
            while len(line) > size:
                out.append(line[:size])
                line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def pick_singles(bets, count: int, rank_by: str = "prob", mix: bool = True,
                 sport: str | None = None):
    """The top singles; with `mix`, only those at the league's minimum chance or better."""
    return rank_singles(bets, count, single_min(sport) if mix else 0.0, rank_by=rank_by,
                        per_game=3, every_line=not mix)


def pick_parlays(bets, count: int, min_legs: int, max_legs: int,
                 pays: float | None = None) -> list[Parlay]:
    """Parlays of min-max legs, or with `pays` (times the wager) ones that pay about that."""
    if pays:
        return payout_parlays(bets, pays, count, min_legs=min_legs, max_legs=max_legs)
    return mixed_parlays(bets, min_legs, max_legs, count)


def season_of(sport: str) -> int:
    """The season a bet is filed under: the calendar year for UFC, MLB and Multi-Lays,
    the year the season started for the NBA."""
    if sport == "nba":
        from .nba import season_of as nba_season

        return nba_season(datetime.now(EASTERN).date())
    return datetime.now().year if sport in ("ufc", "mlb", "multi") else season_now()


def season_now() -> int:
    return season_for(datetime.now(EASTERN).date())
