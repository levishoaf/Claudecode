"""Compare FanDuel prices against fair odds from reference books."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .odds import (
    DEVIG_METHODS,
    decimal_to_american,
    expected_value,
    implied_probability,
    kelly_fraction,
)

if TYPE_CHECKING:
    from .model import GameModel

TARGET_BOOK = "fanduel"

# Books whose lines are known to be efficient. Pinnacle (EU region) is the
# industry benchmark; the rest are low-margin books that move quickly.
SHARP_BOOKS = {"pinnacle": 3.0, "circasports": 2.0, "betonlineag": 1.5, "lowvig": 1.5}
DEFAULT_WEIGHT = 1.0


@dataclass
class Bet:
    game: str
    commence_time: datetime
    market: str
    pick: str
    point: float | None
    fd_price: int
    fair_prob: float  # blended probability used for EV and sizing
    market_prob: float  # no-vig sharp-book probability
    model_prob: float | None  # ratings + context model probability
    ev: float
    kelly: float
    books: list[str]
    game_id: str | None = None  # schedule id, for saving and grading
    priced: bool = True  # False when fd_price is only the break-even price
    week: int | None = None  # schedule week, for display
    note: str = ""  # e.g. "Questionable" from the injury report

    @property
    def fair_american(self) -> int:
        return decimal_to_american(1 / self.fair_prob)


def _line_key(outcomes: list[dict]) -> frozenset:
    """Two books offer the same line only if every (name, point) matches."""
    return frozenset((o["name"], o.get("point")) for o in outcomes)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fair_probabilities(
    reference: list[tuple[str, list[dict]]],
    devig: str,
    sharp_only: bool,
) -> tuple[dict[str, float], list[str]] | None:
    """Weighted average of each reference book's no-vig probabilities."""
    if sharp_only:
        sharp = [r for r in reference if r[0] in SHARP_BOOKS]
        if sharp:
            reference = sharp
    if not reference:
        return None

    devig_fn = DEVIG_METHODS[devig]
    totals: dict[str, float] = {}
    weight_sum = 0.0
    for book, outcomes in reference:
        weight = SHARP_BOOKS.get(book, DEFAULT_WEIGHT)
        probs = devig_fn([implied_probability(o["price"]) for o in outcomes])
        for o, p in zip(outcomes, probs):
            totals[o["name"]] = totals.get(o["name"], 0.0) + weight * p
        weight_sum += weight
    return {name: t / weight_sum for name, t in totals.items()}, [b for b, _ in reference]


def find_bets(
    events: list[dict],
    *,
    min_ev: float = 0.01,
    min_books: int = 2,
    devig: str = "power",
    sharp_only: bool = True,
    include_started: bool = False,
    model: GameModel | None = None,
    model_weight: float = 0.0,
    require_agreement: bool = False,
    now: datetime | None = None,
    target_book: str = TARGET_BOOK,
) -> list[Bet]:
    """Find FanDuel bets whose price beats the estimated true probability.

    The estimate starts from no-vig sharp-book odds. With a season model it
    becomes (1 - w) * market + w * model, where w is `model_weight` scaled
    down by how few games the teams have played. With `require_agreement`,
    a bet is kept only if the model alone also rates it above the market.
    """
    now = now or datetime.now(timezone.utc)
    bets: list[Bet] = []

    for event in events:
        start = _parse_time(event["commence_time"])
        if start <= now and not include_started:
            continue
        game = f"{event['away_team']} @ {event['home_team']}"

        # market key -> line key -> [(book, outcomes)]
        lines: dict[str, dict[frozenset, list[tuple[str, list[dict]]]]] = {}
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                outcomes = market["outcomes"]
                if len(outcomes) != 2:  # skip 3-way markets
                    continue
                lines.setdefault(market["key"], {}).setdefault(
                    _line_key(outcomes), []
                ).append((bookmaker["key"], outcomes))

        for market_key, by_line in lines.items():
            for offers in by_line.values():
                fd = next((o for b, o in offers if b == target_book), None)
                if fd is None:
                    continue
                reference = [(b, o) for b, o in offers if b != target_book]
                if len(reference) < min_books:
                    continue
                result = fair_probabilities(reference, devig, sharp_only)
                if result is None:
                    continue
                fair, used = result
                model_probs = None
                weight = 0.0
                if model is not None:
                    model_probs = model.outcome_probabilities(event, market_key, fd)
                    weight = model_weight * model.sample_weight_for(event)
                for outcome in fd:
                    market_p = fair[outcome["name"]]
                    model_p = model_probs[outcome["name"]] if model_probs else None
                    if require_agreement and (model_p is None or model_p <= market_p):
                        continue
                    p = market_p if model_p is None else (1 - weight) * market_p + weight * model_p
                    ev = expected_value(p, outcome["price"])
                    if ev < min_ev:
                        continue
                    bets.append(
                        Bet(
                            game=game,
                            commence_time=start,
                            market=market_key,
                            pick=outcome["name"],
                            point=outcome.get("point"),
                            fd_price=outcome["price"],
                            fair_prob=p,
                            market_prob=market_p,
                            model_prob=model_p,
                            ev=ev,
                            kelly=kelly_fraction(p, outcome["price"]),
                            books=used,
                        )
                    )

    bets.sort(key=lambda b: b.ev, reverse=True)
    return bets
