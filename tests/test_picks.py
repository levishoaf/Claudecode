import unittest
from datetime import datetime, timezone

from nfl_edge.finder import Bet
from nfl_edge.odds import expected_value, kelly_fraction
from nfl_edge.picks import best_parlays, rank_singles

KICK = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)


def bet(game, price, prob, market="h2h", pick=None):
    return Bet(game=game, commence_time=KICK, market=market, pick=pick or f"{game} pick",
               point=None, fd_price=price, fair_prob=prob, market_prob=prob, model_prob=None,
               ev=expected_value(prob, price), kelly=kelly_fraction(prob, price), books=[])


class RankSinglesTest(unittest.TestCase):
    def test_ranks_by_ev_then_win_prob(self):
        legs = [bet("A", -110, 0.50), bet("B", 100, 0.52), bet("C", -400, 0.80001),
                bet("D", 300, 0.25)]
        ranked = rank_singles(legs, 4)
        self.assertEqual(ranked[0].game, "B")  # +4% EV
        # C (-0.0%) and D (0.0%) tie on rounded EV; the likelier bet wins the tie.
        self.assertEqual([b.game for b in ranked[1:3]], ["C", "D"])

    def test_one_side_per_market_and_min_prob(self):
        legs = [bet("A", 100, 0.55, pick="x"), bet("A", 100, 0.50, pick="y"), bet("B", 300, 0.3)]
        ranked = rank_singles(legs, 5, min_prob=0.5)
        self.assertEqual([b.pick for b in ranked], ["x"])


class BestParlaysTest(unittest.TestCase):
    def test_no_shared_games_by_default(self):
        legs = [bet(g, -110, 0.55) for g in "ABCDEFG"]
        parlays = best_parlays(legs, 3, 5)
        self.assertEqual(len(parlays), 2)  # 7 games fit two 3-leg parlays
        games = [b.game for p in parlays for b in p.legs]
        self.assertEqual(len(games), len(set(games)))

    def test_allow_overlap_gives_distinct_parlays(self):
        legs = [bet(g, -110, 0.55) for g in "ABCD"]
        parlays = best_parlays(legs, 3, 4, allow_overlap=True)
        self.assertEqual(len(parlays), 4)
        self.assertEqual(len({frozenset(id(b) for b in p.legs) for p in parlays}), 4)


if __name__ == "__main__":
    unittest.main()
