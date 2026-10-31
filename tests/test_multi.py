import unittest
from datetime import datetime, timedelta, timezone

from nfl_edge import board, tracker
from nfl_edge.parlays import Parlay, league_of
from nfl_edge.picks import multi_lays

SOON = datetime.now(timezone.utc) + timedelta(days=1)


def bet(game, pick, p, gid, source, market="h2h"):
    return board.make_bet(game, SOON, market, pick, None, p, gid, source)


class MultiLaysTest(unittest.TestCase):
    def pools(self):
        return {
            "nfl": [bet("A @ B", "B", 0.8, "n1", "nfl model"), bet("C @ D", "D", 0.75, "n2", "nfl model")],
            "ufc": [bet("E vs F", "E", 0.7, "u1", "UFC model")],
            "mlb": [bet("G @ H, Oct 16", "H", 0.65, "m1", "DraftKings line")],
        }

    def test_every_parlay_mixes_leagues_and_pays_the_target(self):
        found = multi_lays(self.pools(), 2.0, 10)
        self.assertTrue(found)
        for p in found:
            self.assertGreaterEqual(len({b.league for b in p.legs}), 2)
            self.assertGreaterEqual(1 / p.win_prob, 2.0)
            self.assertEqual(len({b.game for b in p.legs}), len(p.legs))
        probs = [p.win_prob for p in found]
        self.assertEqual(probs, sorted(probs, reverse=True))  # likeliest first

    def test_one_league_makes_none(self):
        self.assertEqual(multi_lays({"nfl": self.pools()["nfl"]}, 1.5, 10), [])

    def test_mixed_parlays_skip_a_leagues_correction(self):
        legs = [b for bets in self.pools().values() for b in bets][:3]
        self.assertEqual({league_of(b) for b in legs}, {"nfl", "ufc"})
        found = multi_lays(self.pools(), 1.0, 10)
        mixed = next(p for p in found if len({b.league for b in p.legs}) > 1)
        expected = 1.0
        for b in mixed.legs:
            expected *= b.fair_prob
        self.assertAlmostEqual(mixed.win_prob, expected)
        self.assertEqual(league_of(bet("x", "y", 0.5, "z", "DraftKings line")), "mlb")


class MultiGradingTest(unittest.TestCase):
    def test_each_leg_is_graded_in_its_own_league(self):
        import bets as launcher
        from unittest import mock

        found = multi_lays(MultiLaysTest().pools(), 1.0, 10)
        p = next(p for p in found if {b.league for b in p.legs} >= {"nfl", "ufc"})
        entry = launcher.tracker_entries([], [p], "multi", 2026)[0]
        self.assertEqual(entry["sport"], "multi")
        self.assertNotIn("week", entry)
        for leg in entry["legs"]:
            leg["kickoff"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        asked = []

        def fake_grade(leg, games, stats):
            asked.append(leg["sport"])
            return "won", ""
        with mock.patch.object(tracker, "grade_leg", side_effect=fake_grade), \
                mock.patch.object(tracker, "college_games", return_value={}), \
                mock.patch.object(tracker.data, "games", return_value=[]), \
                mock.patch("nfl_edge.ufc.fights_by_id", return_value={}), \
                mock.patch("nfl_edge.mlb.games_by_id", return_value={}):
            tracker.regrade({"placed": [], "generated": [entry], "saved": []})
        self.assertEqual(sorted(asked), sorted(b.league for b in p.legs))
        self.assertEqual(entry["status"], "won")


if __name__ == "__main__":
    unittest.main()
