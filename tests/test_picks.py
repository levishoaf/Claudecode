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
        from nfl_edge.picks import BET_TYPES, empty_type_note, every_line_of, of_type

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
        self.assertIn("No spread bets", empty_type_note("Spread", "nfl", checked=False))
        lines = every_line_of(bets, "Rushing yards")
        self.assertTrue(lines[0].confirm_line)  # the model's line: confirm it on FanDuel
        bets[3].on_fanduel = True
        self.assertFalse(every_line_of(bets, "Receiving yards")[0].confirm_line)
        self.assertIn("moneylines only", empty_type_note("Anytime TD", "ncaaf", checked=False))
        self.assertEqual(set(BET_TYPES) >= {"Moneyline", "Anytime TD", "Receptions"}, True)
        with self.assertRaises(ValueError):
            of_type(bets, "Corners")


class OddsLimitTest(unittest.TestCase):
    def test_receiving_yards_only_at_minus_300_or_better(self):
        from datetime import datetime, timedelta, timezone

        from nfl_edge.board import make_bet
        from nfl_edge.picks import every_line_of, type_max_prob

        kick = datetime.now(timezone.utc) + timedelta(days=2)
        m = "player_reception_yds_alternate"
        bets = [make_bet("A @ B", kick, m, "Al Over", 24.5, 0.90, "g1", "x"),   # -900
                make_bet("A @ B", kick, m, "Bo Over", 49.5, 0.75, "g1", "x"),   # -300
                make_bet("C @ D", kick, m, "Cy Over", 49.5, 0.60, "g2", "x"),   # -150
                make_bet("C @ D", kick, m, "Di Over", 24.5, 0.92, "g2", "x")]
        bets[3].on_fanduel, bets[3].fd_price = True, -250  # FanDuel's odds count
        self.assertAlmostEqual(type_max_prob("Receiving yards"), 0.75, places=3)
        self.assertIsNone(type_max_prob("Rushing yards"))
        self.assertEqual([b.pick for b in every_line_of(bets, "Receiving yards")],
                         ["Bo Over", "Cy Over", "Di Over"])


class MoneyMakerTest(unittest.TestCase):
    def test_hangs_on_one_new_leg(self):
        from datetime import datetime, timedelta, timezone

        from nfl_edge.board import make_bet
        from nfl_edge.picks import make_or_break, money_maker_parlays, payout_parlays

        kick = datetime.now(timezone.utc) + timedelta(days=2)
        probs = [0.80, 0.78, 0.76, 0.72, 0.70, 0.66, 0.62, 0.55, 0.50, 0.46, 0.42, 0.38]
        bets = [make_bet(f"T{i} @ H{i}", kick, "h2h", f"T{i}", None, p, f"g{i}", "x")
                for i, p in enumerate(probs)]
        regular = payout_parlays(bets, 4.0, 5)
        money = money_maker_parlays(bets, 5, regular)
        self.assertTrue(money)
        used = {b.pick for p in regular for b in p.legs}
        swings = [make_or_break(p) for p in money]
        for p, s in zip(money, swings):
            self.assertTrue(0.14 <= p.win_prob <= 0.20)  # riskier than the $40 parlays
            self.assertTrue(0.35 <= s.fair_prob <= 0.55)  # the coin-flip leg
            self.assertTrue(all(b.fair_prob >= 0.60 for b in p.legs if b is not s))
            self.assertNotIn(s.pick, used)  # never a leg of the regular parlays
            self.assertEqual(len({b.game for b in p.legs}), len(p.legs))
            for r in regular:
                self.assertLessEqual(len({b.pick for b in p.legs} & {b.pick for b in r.legs}),
                                     len(p.legs) // 2)
        self.assertEqual(len({s.pick for s in swings}), len(swings))  # each swing once
