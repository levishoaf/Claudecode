import unittest

from nfl_edge.stats import TEAM_ABBR, Game, SeasonModel, normal_cdf, probabilities_from_points


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

    def test_normal_cdf(self):
        self.assertAlmostEqual(normal_cdf(0), 0.5)
        self.assertAlmostEqual(normal_cdf(1.96), 0.975, places=3)

    def test_all_32_teams_mapped(self):
        self.assertEqual(len(set(TEAM_ABBR.values())), 32)


class ProbabilitiesTest(unittest.TestCase):
    kc, buf = "Kansas City Chiefs", "Buffalo Bills"

    def test_sum_to_one_and_favor_projected_winner(self):
        cases = [
            ("h2h", [{"name": self.kc}, {"name": self.buf}]),
            ("spreads", [{"name": self.kc, "point": -3.5}, {"name": self.buf, "point": 3.5}]),
        ]
        for market, outcomes in cases:
            probs = probabilities_from_points(self.kc, 28, 20, market, outcomes)
            self.assertAlmostEqual(sum(probs.values()), 1)
            self.assertGreater(probs[self.kc], 0.5)

    def test_totals(self):
        outcomes = [{"name": "Over", "point": 44.5}, {"name": "Under", "point": 44.5}]
        probs = probabilities_from_points(self.kc, 28, 24, "totals", outcomes)
        self.assertGreater(probs["Over"], 0.6)
        self.assertAlmostEqual(sum(probs.values()), 1)

    def test_pickem_spread_is_coin_flip(self):
        outcomes = [{"name": self.kc, "point": 0}, {"name": self.buf, "point": 0}]
        probs = probabilities_from_points(self.kc, 24, 24, "spreads", outcomes)
        self.assertAlmostEqual(probs[self.kc], 0.5)


if __name__ == "__main__":
    unittest.main()
