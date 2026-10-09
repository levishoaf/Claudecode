import unittest

from nfl_edge import extras
from nfl_edge.cli import _describe

EVENT = {"id": "e1", "home_team": "Jacksonville Jaguars", "away_team": "Philadelphia Eagles",
         "commence_time": "2026-10-11T13:30:00Z",
         "bookmakers": [{"key": "pinnacle", "markets": [
             {"key": "spreads", "outcomes": [
                 {"name": "Jacksonville Jaguars", "price": -110, "point": -7.5},
                 {"name": "Philadelphia Eagles", "price": -110, "point": 7.5}]},
             {"key": "totals", "outcomes": [
                 {"name": "Over", "price": -110, "point": 41.5},
                 {"name": "Under", "price": -110, "point": 41.5}]}]}]}


def stat_rows(name, team, pos, col, values, season=2026):
    return [{"season_type": "REG", "player_display_name": name, "team": team, "position": pos,
             "week": str(w + 1), "targets": "8", "attempts": "30", "carries": "10", col: str(v)}
            for w, v in enumerate(values)]


def games(n_weeks=6):
    return [{"game_type": "REG", "season": "2026", "home_team": "JAX", "away_team": "TEN",
             "home_score": "20", "week": str(w + 1)} for w in range(n_weeks)]


class DistributionTest(unittest.TestCase):
    def test_normal_fallback_is_symmetric(self):
        d = extras.LineDistribution("ncaaf")
        over, push = d.outcome("margin", 7.0, 7.0)
        self.assertAlmostEqual(over, 0.5)
        self.assertEqual(push, 0.0)

    def test_p_over_and_anchor(self):
        self.assertGreater(extras.p_over("normal", 80, 25, 60), extras.p_over("normal", 80, 25, 90))
        mean = extras.anchor_mean("count", 2.0, 6.5, 0.55)
        self.assertAlmostEqual(extras.p_over("count", mean, 2.0, 6.5), 0.55, places=3)
        lam = extras.anchor_mean("poisson", 0, 0.5, 0.40)
        self.assertAlmostEqual(extras.p_over("poisson", lam, 0, 0.5), 0.40, places=3)


class PriceEventTest(unittest.TestCase):
    def setUp(self):
        stats = {2026: stat_rows("Brian Thomas Jr.", "JAX", "WR", "receptions", [3, 5, 7, 4, 6, 5])}
        self.players = extras.PlayerModel(2026, games(), stats)
        self.lines = extras.LineDistribution("ncaaf")  # normal curve keeps the test small

    def test_prices_alt_lines_team_totals_and_anchored_props(self):
        extra = {"bookmakers": [{"key": "fanduel", "markets": [
            {"key": "alternate_spreads", "outcomes": [
                {"name": "Philadelphia Eagles", "price": -400, "point": 17.5}]},
            {"key": "team_totals", "outcomes": [
                {"name": "Over", "price": -110, "point": 24.5, "description": "Jacksonville Jaguars"}]},
            {"key": "player_receptions", "outcomes": [
                {"name": "Over", "price": -110, "point": 4.5, "description": "Brian Thomas Jr."},
                {"name": "Under", "price": -110, "point": 4.5, "description": "Brian Thomas Jr."}]},
            {"key": "player_receptions_alternate", "outcomes": [
                {"name": "Over", "price": -300, "point": 2.5, "description": "Brian Thomas Jr."},
                {"name": "Over", "price": 250, "point": 6.5, "description": "Brian Thomas Jr."},
                {"name": "Over", "price": -110, "point": 4.5, "description": "Somebody Else"}]},
        ]}]}
        bets = {_describe(b): b for b in extras.price_event(EVENT, extra, "ncaaf", self.lines, self.players)}
        self.assertGreater(bets["Philadelphia Eagles +17.5"].fair_prob, 0.6)
        self.assertIn("Jacksonville Jaguars Team Total Over 24.5", bets)
        # Anchored to the -110/-110 main line, the 4.5 Over is a coin flip.
        self.assertAlmostEqual(bets["Brian Thomas Jr. 5+ Receptions"].fair_prob, 0.5, places=2)
        self.assertGreater(bets["Brian Thomas Jr. 3+ Receptions"].fair_prob,
                           bets["Brian Thomas Jr. 7+ Receptions"].fair_prob)
        self.assertEqual(bets["Brian Thomas Jr. 3+ Receptions"].books, ["market line"])
        self.assertFalse(any("Somebody Else" in k for k in bets))  # unknown player skipped

    def test_missed_most_of_a_season_is_skipped(self):
        stats = {2026: stat_rows("Hurt Guy", "JAX", "WR", "receptions", [5, 5, 5, 5])}
        model = extras.PlayerModel(2026, games(10), stats)
        self.assertIsNone(model.profile("Hurt Guy", "receptions"))  # 4 of 10 games


if __name__ == "__main__":
    unittest.main()
