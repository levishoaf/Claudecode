import random
import unittest
from datetime import datetime, timedelta

from nfl_edge import board, cfb, clv, learn, signals


class CollegeMarketTest(unittest.TestCase):
    def test_moneylines_first_then_spread(self):
        self.assertAlmostEqual(cfb.market_prob(-200, 170, None), 0.64, delta=0.02)
        self.assertAlmostEqual(cfb.market_prob(None, None, -7.0), 0.69, delta=0.02)
        self.assertLess(cfb.market_prob(None, None, 7.0), 0.5)  # home is the underdog
        self.assertIsNone(cfb.market_prob(None, None, None))


class ClosingLineTest(unittest.TestCase):
    GAME = {"home_team": "ATL", "away_team": "BAL", "home_score": "20",
            "home_moneyline": "150", "away_moneyline": "-170", "spread_line": "-3.5"}

    def test_moneyline_moved_toward_the_pick(self):
        leg = {"type": "moneyline", "team": "BAL", "market_prob": 0.55}
        kind, gained = clv.leg_clv(leg, self.GAME)
        self.assertEqual(kind, "moneyline")
        self.assertGreater(gained, 0.04)  # closed about 61%

    def test_spread_uses_the_main_line_when_picked(self):
        leg = {"type": "spread", "team": "BAL", "point": 11.5, "main_line": -2.5}
        self.assertEqual(clv.leg_clv(leg, self.GAME), ("spread", 1.0))  # closed -3.5
        self.assertIsNone(clv.leg_clv({"type": "spread", "team": "BAL", "point": 11.5},
                                      self.GAME))  # older picks: not measurable

    def test_unplayed_games_skip(self):
        self.assertIsNone(clv.leg_clv({"type": "moneyline", "team": "BAL"},
                                      dict(self.GAME, home_score="")))


class SignalWeightsTest(unittest.TestCase):
    def test_fit_finds_a_real_effect_and_ignores_noise(self):
        rnd = random.Random(3)
        rows = []
        for _ in range(4000):
            f = {"real": rnd.choice((-1.0, 1.0)), "noise": rnd.choice((-1.0, 1.0))}
            p = 0.6
            true = signals.sigmoid(signals.logit(p) + 0.5 * f["real"])
            rows.append((p, f, rnd.random() < true))
        w = signals.fit_weights(rows, ["real", "noise"], ridge=5)
        self.assertAlmostEqual(w["real"], 0.5, delta=0.12)
        self.assertLess(abs(w["noise"]), 0.1)
        self.assertLess(signals.loss(rows, w), signals.loss(rows))

    def test_reasons_name_the_biggest_moves(self):
        out = signals.reasons({"record": 0.5, "trend": 0.3, "wind": -0.4},
                              {"record": 0.4, "trend": -0.5, "wind": 1.0})
        self.assertEqual(out, ["wind", "strong record", "usage falling"])


class LateCheckTest(unittest.TestCase):
    def test_only_games_within_two_hours(self):
        now = datetime(2026, 10, 12, 18, 0, tzinfo=board.EASTERN)
        games = [{"gameday": "2026-10-12", "gametime": "19:30"},
                 {"gameday": "2026-10-12", "gametime": "22:15"},
                 {"gameday": "2026-10-12", "gametime": "17:00"}]
        self.assertEqual(board.starting_soon(games, now), games[:1])


class ParlayCorrectionTest(unittest.TestCase):
    def tearDown(self):
        learn.remember([])

    def test_graded_parlays_move_parlay_chances(self):
        def parlay(i, status):
            # (legs left ungraded so only the parlay-level correction is learned)
            legs = [{"id": f"{i}-{j}", "type": "moneyline", "win_prob": 0.7,
                     "status": "pending"} for j in range(3)]
            return {"id": f"p{i}", "kind": "parlay", "sport": "nfl", "status": status,
                    "legs": legs}
        before = learn.parlay_prob(0.34)
        learn.remember([parlay(i, "lost") for i in range(300)])
        self.assertLess(learn.parlay_prob(0.34), before - 0.05)


if __name__ == "__main__":
    unittest.main()
