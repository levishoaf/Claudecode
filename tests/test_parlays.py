import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nfl_edge.cli import SLIP_WIDTH, format_slip
from nfl_edge.finder import Bet, find_bets
from nfl_edge.parlays import Parlay, build_parlays, most_likely_parlay

SAMPLE = Path(__file__).parent.parent / "nfl_edge" / "sample_odds.json"
KICKOFF = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)


def bet(game, price, prob, pick="X"):
    from nfl_edge.odds import expected_value, kelly_fraction
    return Bet(game=game, commence_time=KICKOFF, market="h2h", pick=pick, point=None,
               fd_price=price, fair_prob=prob, market_prob=prob, model_prob=None,
               ev=expected_value(prob, price), kelly=kelly_fraction(prob, price), books=[])


class ParlayMathTest(unittest.TestCase):
    def test_two_leg_pricing(self):
        p = Parlay((bet("A", 100, 0.55), bet("B", 100, 0.55)))
        self.assertAlmostEqual(p.decimal, 4.0)
        self.assertEqual(p.american, 300)
        self.assertAlmostEqual(p.win_prob, 0.3025)
        self.assertAlmostEqual(p.ev, 0.21)
        self.assertGreater(p.kelly, 0)

    def test_vig_compounds(self):
        # Fair coin flips at -110: -4.5% as a single, about -8.9% as a parlay.
        single = bet("A", -110, 0.5)
        p = Parlay((single, bet("B", -110, 0.5)))
        self.assertAlmostEqual(single.ev, -0.04545, places=4)
        self.assertAlmostEqual(p.ev, -0.08884, places=4)


class BuildParlaysTest(unittest.TestCase):
    def test_ranked_by_win_probability_and_positive_ev(self):
        legs = [bet("A", -300, 0.77), bet("B", -110, 0.54), bet("C", 150, 0.42),
                bet("D", -400, 0.81)]
        parlays = build_parlays(legs, count=10)
        self.assertTrue(parlays)
        probs = [p.win_prob for p in parlays]
        self.assertEqual(probs, sorted(probs, reverse=True))
        self.assertTrue(all(p.ev >= 0 for p in parlays))
        # The two big favorites make the most likely parlay.
        self.assertEqual({b.game for b in parlays[0].legs}, {"A", "D"})

    def test_never_combines_legs_from_same_game(self):
        legs = [bet("A", 100, 0.55, "ML"), bet("A", -110, 0.55, "Over"), bet("B", 100, 0.55)]
        for p in build_parlays(legs, count=10):
            self.assertEqual(len({b.game for b in p.legs}), len(p.legs))

    def test_skips_negative_ev_legs_and_respects_limits(self):
        legs = [bet("A", 100, 0.55), bet("B", 100, 0.55), bet("C", -200, 0.60)]  # C is -EV
        parlays = build_parlays(legs, max_legs=3, count=10)
        self.assertTrue(all("C" not in {b.game for b in p.legs} for p in parlays))
        self.assertEqual(build_parlays(legs, max_legs=3, min_ev=5, count=10), [])
        many = [bet(g, 100, 0.55) for g in "ABCDE"]
        self.assertTrue(all(len(p.legs) <= 2 for p in build_parlays(many, max_legs=2, count=50)))
        self.assertEqual(len(build_parlays(many, count=3)), 3)

    def test_sample_data(self):
        events = json.loads(SAMPLE.read_text())
        legs = find_bets(events, min_ev=0, now=datetime(2026, 10, 10, tzinfo=timezone.utc))
        parlays = build_parlays(legs, count=3)
        self.assertEqual(len(parlays), 3)
        self.assertIn("Houston Texans", {b.pick for b in parlays[0].legs})


class MostLikelyParlayTest(unittest.TestCase):
    def test_picks_most_likely_leg_per_game_then_best_games(self):
        legs = [bet("A", -400, 0.78, "A fav"), bet("A", 300, 0.22, "A dog"),
                bet("B", -110, 0.50, "B side"), bet("C", -250, 0.70, "C fav"),
                bet("D", -150, 0.58, "D fav")]
        p = most_likely_parlay(legs, 3)
        self.assertEqual({b.pick for b in p.legs}, {"A fav", "C fav", "D fav"})
        self.assertAlmostEqual(p.win_prob, 0.78 * 0.70 * 0.58)

    def test_needs_enough_games(self):
        self.assertIsNone(most_likely_parlay([bet("A", 100, 0.5), bet("B", 100, 0.5)], 3))

    def test_allows_negative_ev_legs(self):
        legs = [bet(g, -110, 0.5) for g in "ABCD"]
        p = most_likely_parlay(legs, 4)
        self.assertEqual(len(p.legs), 4)
        self.assertLess(p.ev, 0)


class SlipTest(unittest.TestCase):
    def test_slip_layout_and_payout(self):
        events = json.loads(SAMPLE.read_text())
        legs = find_bets(events, min_ev=0, now=datetime(2026, 10, 10, tzinfo=timezone.utc))
        p = build_parlays(legs, max_legs=3, count=1)[0]
        slip = format_slip(p, 10)
        self.assertTrue(all(len(line) == SLIP_WIDTH for line in slip))
        text = "\n".join(slip)
        self.assertIn(f"{len(p.legs)} Leg Parlay", text)
        self.assertIn(f"${10 * p.decimal:,.2f}", text)
        self.assertIn("MONEYLINE", text)
        self.assertIn("Houston Texans @ Tennessee Titans", text)
        self.assertIn("1:00 PM ET", text)


if __name__ == "__main__":
    unittest.main()
