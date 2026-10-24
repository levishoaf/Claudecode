import unittest
from datetime import datetime, timezone

from nfl_edge.board import _pick_line, make_bet
from nfl_edge.cli import format_slip
from nfl_edge.parlays import Parlay

KICK = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)


class BoardTest(unittest.TestCase):
    def test_pick_line_takes_highest_chance_within_range(self):
        prob = {0.5: 0.55, 3.5: 0.66, 7.5: 0.79, 10.5: 0.85}
        self.assertEqual(_pick_line(prob.get, prob, 0.6, 0.8), (7.5, 0.79))
        self.assertIsNone(_pick_line(prob.get, prob, 0.9, 0.95))

    def test_unpriced_bet_uses_break_even_odds(self):
        b = make_bet("A @ B", KICK, "team_totals", "B Over", 20.5, 0.8, "g1", "market line")
        self.assertFalse(b.priced)
        self.assertEqual(b.fd_price, -400)
        self.assertAlmostEqual(b.ev, 0.0, places=6)
        priced = make_bet("A @ B", KICK, "h2h", "B", None, 0.8, "g1", "consensus", price=-300)
        self.assertTrue(priced.priced)
        self.assertAlmostEqual(priced.ev, 0.8 * (1 + 100 / 300) - 1)

    def test_break_even_slip(self):
        legs = (make_bet("A @ B", KICK, "h2h", "B", None, 0.8, "g1", "m"),
                make_bet("C @ D", KICK, "player_receptions_alternate", "Joe Smith Over", 4.5,
                         0.75, "g2", "player logs"))
        slip = "\n".join(format_slip(Parlay(legs), 10, break_even=True))
        self.assertIn("break-even -150", slip)  # 0.8 * 0.75 = 60% -> -150
        self.assertIn("Joe Smith 5+ Receptions", slip)
        self.assertIn("$16.67*", slip)
        self.assertNotIn("EV", slip)


if __name__ == "__main__":
    unittest.main()
