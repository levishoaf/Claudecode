import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nfl_edge.finder import find_bets
from nfl_edge.odds import (
    american_to_decimal,
    decimal_to_american,
    devig_multiplicative,
    devig_power,
    expected_value,
    implied_probability,
    kelly_fraction,
)

SAMPLE = Path(__file__).parent.parent / "nfl_edge" / "sample_odds.json"
BEFORE_KICKOFF = datetime(2026, 10, 10, tzinfo=timezone.utc)


class OddsMathTest(unittest.TestCase):
    def test_conversions(self):
        self.assertAlmostEqual(american_to_decimal(150), 2.5)
        self.assertAlmostEqual(american_to_decimal(-200), 1.5)
        self.assertEqual(decimal_to_american(2.5), 150)
        self.assertEqual(decimal_to_american(1.5), -200)
        self.assertAlmostEqual(implied_probability(-110), 110 / 210)

    def test_devig_sums_to_one(self):
        implied = [implied_probability(-110), implied_probability(-110)]
        for fn in (devig_multiplicative, devig_power):
            probs = fn(implied)
            self.assertAlmostEqual(sum(probs), 1, places=8)
            self.assertAlmostEqual(probs[0], 0.5, places=8)

    def test_power_devig_shades_longshot(self):
        implied = [implied_probability(-300), implied_probability(240)]
        mult = devig_multiplicative(implied)
        power = devig_power(implied)
        self.assertLess(power[1], mult[1])

    def test_ev_and_kelly(self):
        self.assertAlmostEqual(expected_value(0.5, 100), 0.0)
        self.assertAlmostEqual(expected_value(0.55, 100), 0.10)
        self.assertAlmostEqual(kelly_fraction(0.55, 100), 0.10)
        self.assertEqual(kelly_fraction(0.40, 100), 0.0)


class FinderTest(unittest.TestCase):
    def setUp(self):
        self.events = json.loads(SAMPLE.read_text())

    def test_finds_mispriced_fanduel_lines(self):
        bets = find_bets(self.events, now=BEFORE_KICKOFF)
        picks = {(b.market, b.pick) for b in bets}
        self.assertEqual(picks, {("h2h", "Green Bay Packers"), ("totals", "Over"),
                                 ("h2h", "Houston Texans"), ("spreads", "Cincinnati Bengals")})
        self.assertEqual(bets, sorted(bets, key=lambda b: b.ev, reverse=True))
        self.assertTrue(all(b.ev >= 0.01 for b in bets))

    def test_only_compares_identical_lines(self):
        # DraftKings' CHI/GB total is 46.0 vs FanDuel's 45.5, so it must not be used.
        bets = find_bets(self.events, min_ev=-1, now=BEFORE_KICKOFF)
        gb_total = [b for b in bets if b.market == "totals" and "Packers" in b.game]
        self.assertTrue(gb_total)
        for b in gb_total:
            self.assertNotIn("draftkings", b.books)

    def test_prefers_sharp_books(self):
        bets = find_bets(self.events, now=BEFORE_KICKOFF)
        for b in bets:
            self.assertNotIn("draftkings", b.books)
        all_books = find_bets(self.events, sharp_only=False, min_ev=-1, now=BEFORE_KICKOFF)
        self.assertTrue(any("draftkings" in b.books for b in all_books))

    def test_skips_started_games(self):
        after = datetime(2026, 12, 1, tzinfo=timezone.utc)
        self.assertEqual(find_bets(self.events, now=after), [])

    def test_requires_min_books(self):
        self.assertEqual(find_bets(self.events, min_books=5, now=BEFORE_KICKOFF), [])


if __name__ == "__main__":
    unittest.main()
