import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nfl_edge.finder import find_bets
from nfl_edge.stats import TEAM_ABBR, Game, SeasonModel, normal_cdf

SAMPLE = Path(__file__).parent.parent / "nfl_edge" / "sample_odds.json"
BEFORE_KICKOFF = datetime(2026, 10, 10, tzinfo=timezone.utc)


def season(weeks: int) -> list[Game]:
    """KC beats everyone by 14; everyone else splits evenly."""
    games = []
    others = ["BUF", "PHI", "DAL", "DET", "GB"]
    for w in range(weeks):
        games.append(Game("KC", others[w % 5], 31, 17, False))
        a, b = others[(w + 1) % 5], others[(w + 2) % 5]
        games.append(Game(a, b, 20, 20, False))
    return games


class SeasonModelTest(unittest.TestCase):
    def test_strong_team_rated_highest(self):
        model = SeasonModel(season(8))
        best = max(model.ratings.values(), key=lambda r: r.net)
        self.assertEqual(best.team, "KC")
        home, away = model.predict("KC", "BUF")
        self.assertGreater(home - away, 5)

    def test_ridge_shrinks_small_samples(self):
        few = SeasonModel(season(2)).ratings["KC"].net
        many = SeasonModel(season(10)).ratings["KC"].net
        self.assertLess(few, many)

    def test_sample_weight(self):
        model = SeasonModel(season(4))
        self.assertEqual(model.games_played["KC"], 4)
        self.assertEqual(model.games_played["BUF"], 2)
        self.assertAlmostEqual(model.sample_weight("KC", "BUF"), 2 / 8)
        self.assertEqual(model.sample_weight("KC", "XXX"), 0.0)

    def test_outcome_probabilities_sum_to_one(self):
        model = SeasonModel(season(8))
        kc, buf = "Kansas City Chiefs", "Buffalo Bills"
        cases = [
            ("h2h", [{"name": kc, "price": -200}, {"name": buf, "price": 170}]),
            ("spreads", [{"name": kc, "price": -110, "point": 3.5},
                         {"name": buf, "price": -110, "point": -3.5}]),
            ("totals", [{"name": "Over", "price": -110, "point": 44.5},
                        {"name": "Under", "price": -110, "point": 44.5}]),
        ]
        for market, outcomes in cases:
            probs = model.outcome_probabilities(buf, kc, market, outcomes)
            self.assertAlmostEqual(sum(probs.values()), 1)
            if market != "totals":
                self.assertGreater(probs[kc], 0.5)

    def test_normal_cdf(self):
        self.assertAlmostEqual(normal_cdf(0), 0.5)
        self.assertAlmostEqual(normal_cdf(1.96), 0.975, places=3)

    def test_all_32_teams_mapped(self):
        self.assertEqual(len(set(TEAM_ABBR.values())), 32)


class BlendTest(unittest.TestCase):
    def setUp(self):
        self.events = json.loads(SAMPLE.read_text())
        self.model = SeasonModel(season(8))

    def test_zero_weight_matches_market_only(self):
        plain = find_bets(self.events, now=BEFORE_KICKOFF)
        blended = find_bets(self.events, model=self.model, model_weight=0,
                            now=BEFORE_KICKOFF)
        self.assertEqual([(b.pick, round(b.ev, 9)) for b in plain],
                         [(b.pick, round(b.ev, 9)) for b in blended])

    def test_blend_moves_toward_model(self):
        bets = find_bets(self.events, model=self.model, model_weight=1, min_ev=-1,
                         now=BEFORE_KICKOFF)
        for b in bets:
            lo, hi = sorted((b.market_prob, b.model_prob))
            self.assertTrue(lo - 1e-12 <= b.fair_prob <= hi + 1e-12)

    def test_require_agreement(self):
        bets = find_bets(self.events, model=self.model, model_weight=0.3, min_ev=-1,
                         require_agreement=True, now=BEFORE_KICKOFF)
        self.assertTrue(bets)
        self.assertTrue(all(b.model_prob > b.market_prob for b in bets))


if __name__ == "__main__":
    unittest.main()
