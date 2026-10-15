import unittest

from nfl_edge import cfb

COLS = ["game_id", "season", "week", "season_type", "start_date", "completed", "neutral_site",
        "home_team", "home_division", "home_points", "home_pregame_elo",
        "away_team", "away_division", "away_points", "away_pregame_elo"]


def row(week, away, home, away_pts=None, home_pts=None, away_elo="1500", home_elo="1500",
        away_div="fbs", home_div="fbs", neutral="FALSE"):
    done = home_pts is not None
    return dict(zip(COLS, [f"{week}{away}{home}", "2026", str(week), "regular",
                           f"2026-09-{week + 5:02d}T19:00:00.000Z", "TRUE" if done else "FALSE",
                           neutral, home, home_div, "NA" if not done else str(home_pts), home_elo,
                           away, away_div, "NA" if not done else str(away_pts), away_elo]))


def season():
    return [
        row(1, "Weak U", "Power State", 10, 45, away_elo="1300", home_elo="1900"),
        row(1, "Some FCS", "Middle Tech", 7, 38, away_elo="NA", away_div="fcs"),
        row(2, "Middle Tech", "Power State", 17, 35, home_elo="1910"),
        row(2, "Weak U", "Middle Tech", 14, 24, away_elo="1290"),
        row(3, "Weak U", "Power State", home_elo="1920", away_elo="1280"),
    ]


class MatchTeamTest(unittest.TestCase):
    schools = ["Iowa", "Iowa State", "Miami", "Miami (OH)", "Mississippi State", "Ole Miss",
               "North Carolina", "NC State", "San José State", "Hawai'i", "Texas A&M", "App State"]

    def test_longest_prefix_and_aliases(self):
        cases = {
            "Iowa Hawkeyes": "Iowa", "Iowa State Cyclones": "Iowa State",
            "Miami Hurricanes": "Miami", "Miami (OH) RedHawks": "Miami (OH)",
            "Mississippi State Bulldogs": "Mississippi State", "Mississippi Rebels": "Ole Miss",
            "North Carolina State Wolfpack": "NC State", "North Carolina Tar Heels": "North Carolina",
            "San Jose State Spartans": "San José State", "Hawaii Rainbow Warriors": "Hawai'i",
            "Texas A&M Aggies": "Texas A&M", "Appalachian State Mountaineers": "App State",
        }
        for odds_name, school in cases.items():
            self.assertEqual(cfb.match_team(odds_name, self.schools), school, odds_name)
        self.assertIsNone(cfb.match_team("Nowhere Tech Owls", self.schools))


class CfbModelTest(unittest.TestCase):
    def test_prior_and_fcs_pooling(self):
        prior = cfb.preseason_prior(season())
        self.assertGreater(prior["Power State"], prior["Middle Tech"])
        self.assertGreater(prior["Middle Tech"], prior["Weak U"])
        self.assertEqual(prior[cfb.FCS], cfb.FCS_PRIOR)
        games = cfb.to_games(season())
        self.assertEqual(len(games), 4)
        self.assertIn(cfb.FCS, {g.away for g in games})

    def test_predicts_upcoming_game(self):
        model = cfb.CfbModel(season())
        event = {"id": "e", "home_team": "Power State Titans", "away_team": "Weak U Owls",
                 "commence_time": "2026-09-08T19:00:00Z"}
        p = model.predict(event)
        self.assertGreater(p.home_pts - p.away_pts, 10)
        probs = model.outcome_probabilities(
            event, "h2h", [{"name": "Power State Titans"}, {"name": "Weak U Owls"}])
        self.assertGreater(probs["Power State Titans"], 0.75)
        self.assertAlmostEqual(sum(probs.values()), 1)
        self.assertGreater(model.sample_weight_for(event), 0)

    def test_unknown_game(self):
        model = cfb.CfbModel(season())
        event = {"home_team": "Power State Titans", "away_team": "Weak U Owls",
                 "commence_time": "2026-12-25T19:00:00Z"}
        self.assertIsNone(model.predict(event))
        self.assertEqual(model.sample_weight_for(event), 0.0)


if __name__ == "__main__":
    unittest.main()
