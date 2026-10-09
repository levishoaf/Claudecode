import unittest

from nfl_edge.grade import grade_leg

GAMES = {
    "g1": {"game_id": "g1", "away_team": "HOU", "home_team": "TEN", "away_score": "24", "home_score": "17"},
    "g2": {"game_id": "g2", "away_team": "CIN", "home_team": "MIA", "away_score": "", "home_score": ""},
}
STATS = [{"game_id": "g1", "player_display_name": "C.J. Stroud", "passing_tds": "2"}]


class GradeTest(unittest.TestCase):
    def test_moneyline(self):
        self.assertEqual(grade_leg({"type": "moneyline", "game_id": "g1", "team": "HOU"}, GAMES, [])[0], "won")
        self.assertEqual(grade_leg({"type": "moneyline", "game_id": "g1", "team": "TEN"}, GAMES, [])[0], "lost")
        self.assertEqual(grade_leg({"type": "moneyline", "game_id": "g2", "team": "CIN"}, GAMES, [])[0], "pending")

    def test_passing_tds(self):
        leg = {"type": "passing_tds", "game_id": "g1", "player": "C.J. Stroud", "min": 1}
        self.assertEqual(grade_leg(leg, GAMES, STATS), ("won", "2 passing TDs"))
        self.assertEqual(grade_leg(dict(leg, min=3), GAMES, STATS)[0], "lost")
        self.assertEqual(grade_leg(dict(leg, player="Someone Else"), GAMES, STATS)[0], "pending")

    def test_spread_and_total(self):
        spread = {"type": "spread", "game_id": "g1", "team": "TEN", "point": 7.5}
        self.assertEqual(grade_leg(spread, GAMES, [])[0], "won")  # lost by 7
        self.assertEqual(grade_leg(dict(spread, point=7), GAMES, [])[0], "push")
        self.assertEqual(grade_leg(dict(spread, team="HOU", point=-7.5), GAMES, [])[0], "lost")
        total = {"type": "total", "game_id": "g1", "side": "Over", "line": 40.5}
        self.assertEqual(grade_leg(total, GAMES, [])[0], "won")  # 41 points
        self.assertEqual(grade_leg(dict(total, side="Under"), GAMES, [])[0], "lost")
        self.assertEqual(grade_leg(dict(total, line=41), GAMES, [])[0], "push")

    def test_profit(self):
        from nfl_edge.grade import profit_of
        leg = {"odds": -115, "stake": 100}
        self.assertAlmostEqual(profit_of(leg, "won"), 86.96, places=2)
        self.assertEqual(profit_of(leg, "lost"), -100)
        self.assertEqual(profit_of(leg, "push"), 0)


if __name__ == "__main__":
    unittest.main()
