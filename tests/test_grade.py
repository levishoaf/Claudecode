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


if __name__ == "__main__":
    unittest.main()
