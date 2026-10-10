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


class MixedParlaysTest(unittest.TestCase):
    def test_sizes_quota_and_reuse_limit(self):
        from nfl_edge.picks import mixed_parlays

        legs = [bet(g, -300, 0.75 - i * 0.005) for i, g in enumerate("ABCDEFGHIJKLMN")]
        parlays = mixed_parlays(legs, 3, 5, 10, max_uses=3)
        self.assertEqual(len(parlays), 10)
        self.assertEqual(sorted({len(p.legs) for p in parlays}), [3, 4, 5])
        self.assertEqual([len(p.legs) for p in parlays].count(3), 4)  # 10 = 4 + 3 + 3
        for p in parlays:
            self.assertEqual(len({b.game for b in p.legs}), len(p.legs))
        uses = {}
        for p in parlays:
            for b in p.legs:
                uses[id(b)] = uses.get(id(b), 0) + 1
        self.assertLessEqual(max(uses.values()), 3)
        self.assertEqual(len({frozenset(id(b) for b in p.legs) for p in parlays}), 10)


class PayoutParlaysTest(unittest.TestCase):
    def test_pays_at_least_the_target_likeliest_first(self):
        from datetime import datetime, timedelta, timezone

        from nfl_edge.board import make_bet
        from nfl_edge.picks import payout_parlays

        kick = datetime.now(timezone.utc) + timedelta(days=2)
        bets = [make_bet(f"Team{i} @ Home{i}", kick, "h2h", f"Team{i}", None, p, f"g{i}", "x")
                for i, p in enumerate([0.78, 0.75, 0.72, 0.7, 0.66, 0.63, 0.6, 0.55])]
        parlays = payout_parlays(bets, 4.0, 5)
        self.assertEqual(len(parlays), 5)
        for p in parlays:
            self.assertGreaterEqual(1 / p.win_prob, 4.0)  # at least $40 back on $10
            self.assertLessEqual(1 / p.win_prob, 4.0 * 1.05)  # and not much more
            self.assertEqual(len({b.game for b in p.legs}), len(p.legs))
        probs = [p.win_prob for p in parlays]
        self.assertEqual(probs, sorted(probs, reverse=True))


class BetTypeTest(unittest.TestCase):
    def test_of_type_keeps_only_that_type(self):
        from datetime import datetime, timedelta, timezone

        from nfl_edge.board import make_bet
        from nfl_edge.picks import BET_TYPES, empty_type_note, of_type

        kick = datetime.now(timezone.utc) + timedelta(days=2)
        bets = [make_bet("A @ B", kick, "h2h", "A", None, 0.7, "g1", "x"),
                make_bet("A @ B", kick, "player_anytime_td", "Joe Yes", None, 0.5, "g1", "x"),
                make_bet("C @ D", kick, "player_rush_yds_alternate", "Sam Over", 49.5, 0.6, "g2", "x"),
                make_bet("C @ D", kick, "player_reception_yds_alternate", "Al Over", 24.5, 0.6, "g2", "x")]
        self.assertEqual(len(of_type(bets, "All bets")), 4)
        self.assertEqual([b.pick for b in of_type(bets, "Moneyline")], ["A"])
        self.assertEqual([b.pick for b in of_type(bets, "Anytime TD")], ["Joe Yes"])
        self.assertEqual([b.pick for b in of_type(bets, "Rushing yards")], ["Sam Over"])
        self.assertEqual([b.pick for b in of_type(bets, "Receiving yards")], ["Al Over"])
        self.assertEqual(of_type(bets, "Spread"), [])
        self.assertIn("FanDuel check", empty_type_note("Spread", "nfl", checked=False))
        self.assertIn("moneylines only", empty_type_note("Anytime TD", "ncaaf", checked=False))
        self.assertEqual(set(BET_TYPES) >= {"Moneyline", "Anytime TD", "Receptions"}, True)
        with self.assertRaises(ValueError):
            of_type(bets, "Corners")
