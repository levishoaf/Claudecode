"""Put real sportsbook odds (nfl_edge/lines.py) on the boards the app, web page and bot show.

Each bet that matches a line FanDuel posts gets FanDuel's real price, the true chance
from every other sportsbook (its cut taken out), its value at FanDuel's price and the
best price anywhere. For the NFL, FanDuel's own player-prop lines replace the
model-made prop lines of the same games, so props are at the exact number FanDuel
offers. Bets with no FanDuel line keep the model's chance and break-even odds.

The market's chance replaces the model's: tested league by league, the betting market
was as good or better (MLB and college lines; UFC most of all). Only "rough" chances
(anytime TDs posted "Yes" only, with an assumed cut) are averaged with the model's.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timedelta

from . import lines
from .finder import Bet
from .odds import expected_value, kelly_fraction

# Our market -> the feed's market.
MARKETS = {"h2h": "h2h", "spreads": "spreads", "alternate_spreads": "spreads",
           "totals": "totals", "alternate_totals": "totals", "run_line": "spreads",
           "totals_runs": "totals"}
WINDOW = timedelta(hours=18)  # a game's start may differ a little between sources


def norm(name: str) -> str:
    """'Marvin Mims Jr.' -> 'marvinmims', 'São Paulo' -> 'saopaulo'."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", text)
    return re.sub(r"[^a-z]", "", text)


def _same(ours: str, theirs: str) -> bool:
    """Our team or fighter name against the feed's: equal, or a prefix of it ('Iowa' and
    'Iowa Hawkeyes'), or the same last name for fighters."""
    a, b = norm(ours), norm(theirs)
    if not a or not b:
        return False
    if a == b or b.startswith(a) or a.startswith(b):
        return True
    return norm(ours.split()[-1]) == norm(theirs.split()[-1]) and len(ours.split()) > 1


def _teams(game: str) -> list[str]:
    game = game.split(", ")[0]  # MLB games carry their date
    return [t.strip() for t in re.split(r" @ | vs ", game)]


def _event_for(bet: Bet, events: dict[tuple, list]) -> tuple | None:
    """The feed's game this bet is on: both teams (or fighters) match, at about the time."""
    ours = _teams(bet.game)
    for (game, start), offers in events.items():
        theirs = _teams(game)
        if abs(start - bet.commence_time) > WINDOW or len(theirs) != len(ours):
            continue
        if all(any(_same(o, t) for t in theirs) for o in ours):
            return game, start
    return None


def _offer_for(bet: Bet, offers: list) -> object | None:
    """The FanDuel line priced in the feed that is exactly this bet."""
    market = MARKETS.get(bet.market)
    if market:
        pick = bet.pick
        for o in offers:
            if o.market != market:
                continue
            if market == "totals":
                if o.side == pick and o.point == bet.point:
                    return o
            elif _same(pick, o.side) and (market == "h2h" or o.point == bet.point):
                return o
        return None
    if bet.market.startswith("player_"):
        base = bet.market.removesuffix(lines_alt())
        player, side = bet.pick.rsplit(" ", 1)
        for o in offers:
            if o.market == base and norm(o.subject) == norm(player) and o.side == side and \
                    (base == "player_anytime_td" or o.point == bet.point):
                return o
    return None


def lines_alt() -> str:
    from .extras import ALT

    return ALT


def _reprice(bet: Bet, offer) -> None:
    model = bet.fair_prob
    chance = (offer.chance + model) / 2 if offer.rough else offer.chance
    bet.fd_price = offer.fanduel
    bet.priced = True
    bet.market_prob = offer.chance
    bet.model_prob = model
    bet.fair_prob = chance
    bet.ev = expected_value(chance, offer.fanduel)
    bet.kelly = kelly_fraction(chance, offer.fanduel)
    bet.best_price, bet.best_book = offer.best, offer.best_book
    bet.books_count, bet.sharp = offer.books, offer.sharp
    bet.check, bet.rough = offer.check, offer.rough
    bet.confirm_line = False  # it's FanDuel's own line
    bet.on_fanduel = True


def feed() -> lines.Feed | None:
    """The odds feed, if a key is set (ODDS_API_KEY, or the app's saved key file)."""
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        try:
            import bets as launcher

            key = launcher.odds_api_key() or ""
        except Exception:
            key = ""
    return lines.Feed(key=key) if key else None


def apply(bets: list[Bet], sport: str, offers: list, props: bool = True) -> list[Bet]:
    """The board with real odds: matched bets repriced; for the NFL, FanDuel's prop lines
    in place of the model's (same games and stats). Returns the new list."""
    if not offers:
        return bets
    events: dict[tuple, list] = {}
    for o in offers:
        events.setdefault((o.game, o.start), []).append(o)
    found: dict[tuple, tuple] = {}  # our game -> the feed's
    used = set()
    for b in bets:
        key = (b.game, b.commence_time)
        if key not in found:
            found[key] = _event_for(b, events)
        event = found[key]
        if event is None:
            continue
        offer = _offer_for(b, events[event])
        if offer is not None:
            _reprice(b, offer)
            used.add(id(offer))
    if sport != "nfl" or not props:
        return bets
    # FanDuel's own prop lines for each matched game replace the model's lines there.
    from .extras import PLAYER_MARKETS

    out = []
    replaced = set()  # (our game, market) pairs FanDuel has props for
    added = []
    for (game, start), event in found.items():
        if event is None:
            continue
        sample = next(b for b in bets if (b.game, b.commence_time) == (game, start))
        for o in events[event]:
            if o.market not in PLAYER_MARKETS or id(o) in used or o.side == "No":
                continue
            pick = f"{o.subject} {o.side}"
            b = Bet(game, start, o.market, pick, o.point if o.market != "player_anytime_td"
                    else 0.5, o.fanduel, o.chance, o.chance, None, 0.0, 0.0, list(sample.books),
                    sample.game_id, True, sample.week)
            _reprice(b, o)
            b.raw_prob = None
            added.append(b)
            replaced.add((game, o.market))
    for b in bets:
        base = b.market.removesuffix(lines_alt())
        if b.market.startswith("player_") and (b.game, base) in replaced and not b.priced:
            continue  # a model line where FanDuel posts its own
        out.append(b)
    return out + added


def value_list(offers: list) -> list[dict]:
    """Every priced FanDuel line, firm value first (then rough and to-check ones)."""
    ranked = sorted(offers, key=lambda o: (o.ev > 0 and not o.rough and not o.check, o.ev),
                    reverse=True)
    return [o.to_json() for o in ranked]
