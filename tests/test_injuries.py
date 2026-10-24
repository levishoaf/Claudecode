import unittest

from nfl_edge.board import short_injury


class ShortInjuryTest(unittest.TestCase):
    def test_summaries(self):
        cases = {"Knee": "Knee", "Left Knee": "Knee", "Hamstring, Ankle": "Hamstring",
                 "Not injury related - resting player": "Rest",
                 "Not injury related - personal matter": "Personal",
                 "Not injury related": "Non-injury", "": "Undisclosed",
                 "right shoulder sprain": "Shoulder Sprain"}
        for text, want in cases.items():
            self.assertEqual(short_injury(text), want, text)
            self.assertLessEqual(len(short_injury(text).split()), 2)


if __name__ == "__main__":
    unittest.main()


class EspnOfficialTest(unittest.TestCase):
    # Shape of https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries
    PAYLOAD = {"injuries": [
        {"displayName": "Philadelphia Eagles", "injuries": [
            {"status": "Questionable", "athlete": {"displayName": "Saquon Barkley",
                                                   "position": {"abbreviation": "RB"}},
             "details": {"type": "Hamstring"}},
            {"status": "Out", "athlete": {"displayName": "DeVonta Smith",
                                          "position": {"abbreviation": "WR"}},
             "details": {"type": "Left Hamstring"}},
            {"status": "Injured Reserve", "athlete": {"displayName": "Long Term Guy"},
             "details": {"type": "Knee"}},
        ]},
        {"displayName": "Not A Team", "injuries": [{"status": "Out", "athlete": {}}]},
    ]}

    def test_parses_game_statuses_only(self):
        from nfl_edge.board import espn_official

        got = espn_official(self.PAYLOAD)
        self.assertEqual(got[("PHI", "saquon barkley")]["status"], "Questionable")
        self.assertEqual(got[("PHI", "devonta smith")]["injury"], "Hamstring")
        self.assertEqual(len(got), 2)  # IR and unknown teams skipped

    def test_unexpected_shape_is_ignored(self):
        from nfl_edge.board import espn_official

        self.assertEqual(espn_official({"injuries": "nope"}), {})
        self.assertEqual(espn_official([]), {})
