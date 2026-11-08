import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

import bets as launcher
from nfl_edge import board, grade, nba
from nfl_edge.parlays import league_of
from nfl_edge.picks import NBA_TYPES, league_types, of_type

ODDS = {  # an ESPN scoreboard odds entry, trimmed
    "overUnder": 231.5, "spread": -4.5,
    "moneyline": {"home": {"close": {"odds": "-180"}}, "away": {"close": {"odds": "+150"}}},
    "pointSpread": {"home": {"close": {"line": "-4.5", "odds": "-110"}},
                    "away": {"close": {"line": "+4.5", "odds": "-110"}}},
    "total": {"over": {"close": {"line": "o231.5", "odds": "-112"}},
              "under": {"close": {"line": "u231.5", "odds": "-108"}}},
}
WEIGHTS = {"weights": {"elo": 0.55, "home": 0.25, "rest": 0.15},
           "margin": {"slope": 8.0, "sd": 12.5}, "market_share": 1.0,
           "test": {"season": "2025-26", "games": 500, "model": {"likelier_won": 0.66},
                    "market": {"likelier_won": 0.68}}}


def game(i, home="NY", away="PHI", hs=None, as_=None, done=False, when="2026-10-21T23:30Z",
         lines=None, state="pre"):
    return {"id": str(i), "date": when, "season_type": 2, "home": home, "away": away,
            "home_name": f"{home} Team", "away_name": f"{away} Team", "home_score": hs,
            "away_score": as_, "state": state, "done": done, "status": "", "lines": lines}


class ModelTest(unittest.TestCase):
    def test_winner_gains_and_back_to_backs_count(self):
        r = nba.Ratings()
        r.update(game(1, hs=120, as_=100, done=True, when="2026-10-21T23:30Z"))
        self.assertGreater(r.elo["NY"], nba.START_ELO)
        self.assertLess(r.elo["PHI"], nba.START_ELO)
        # PHI plays again the next (US) day: tired, NY rested two days
        f = r.features(game(2, home="BOS", away="PHI", when="2026-10-22T23:30Z"))
        self.assertEqual(f["rest"], 1.0)
        f = r.features(game(3, home="NY", away="BOS", when="2026-10-23T23:30Z"))
        self.assertEqual(f["rest"], 0.0)

    def test_seasons_are_named_by_their_first_year(self):
        self.assertEqual(nba.season_of(date(2026, 10, 21)), 2026)
        self.assertEqual(nba.season_of(date(2027, 4, 10)), 2026)
        self.assertEqual(launcher.season_of("nba", date(2027, 1, 5)), 2026)

    def test_posted_line_is_used_and_model_fills_in(self):
        with mock.patch.object(nba, "weights", return_value=WEIGHTS):
            c = nba.chances(game(1, lines=nba._lines(ODDS)), nba.Ratings())
            self.assertAlmostEqual(c["home"], nba.no_vig(-180, 150))
            self.assertEqual(c["source"], "NBA DraftKings line")
            self.assertEqual(c["spread"], -4.5)
            self.assertAlmostEqual(c["home_cover"], 0.5)
            self.assertAlmostEqual(c["over"], nba.no_vig(-112, -108))
            c = nba.chances(game(2), nba.Ratings())  # no line yet
            self.assertEqual(c["source"], "NBA model")
            self.assertIsNone(c["over"])
            self.assertNotEqual(c["spread"], int(c["spread"]))  # a half point: no pushes
            self.assertTrue(0.3 < c["home_cover"] < 0.7)
            self.assertFalse(c["spread_posted"])


class BoardTest(unittest.TestCase):
    def board(self, every_line):
        games = [game(1, lines=nba._lines(ODDS)),
                 game(2, home="BOS", away="MIA", when="2026-10-22T00:00Z"),
                 game(3, home="DET", away="BOS", state="in")]  # already started: left out
        with mock.patch.object(nba, "upcoming_days", return_value=[date(2026, 10, 21)]), \
                mock.patch.object(nba, "day", return_value=games), \
                mock.patch.object(nba, "current_ratings", return_value=nba.Ratings()), \
                mock.patch.object(nba, "weights", return_value=WEIGHTS):
            return board.nba_board(0.01, 0.99, every_line=every_line)

    def test_moneylines_by_default(self):
        bets = self.board(False)
        self.assertEqual({b.market for b in bets}, {"h2h"})
        self.assertEqual(len(bets), 4)
        self.assertTrue(all(league_of(b) == "nba" for b in bets))
        no_line = [b for b in bets if "BOS" in b.game]
        self.assertTrue(all(b.note == board.NO_LINE for b in no_line))
        self.assertFalse(any(b.confirm_line for b in bets))

    def test_spreads_and_totals_when_picked_are_confirm_line(self):
        bets = self.board(True)
        spreads, totals = of_type(bets, "Spread"), of_type(bets, "Total points")
        self.assertEqual(len(spreads), 2)  # only the game with a posted spread
        self.assertEqual(len(totals), 2)  # only the game with a posted total
        self.assertTrue(all(b.confirm_line for b in spreads + totals))
        self.assertEqual(sorted(b.point for b in spreads if "NY" in b.game), [-4.5, 4.5])
        self.assertEqual(league_types("nba"), NBA_TYPES)


class GradeTest(unittest.TestCase):
    def test_legs_grade_on_espn_scores(self):
        b = SimpleNamespace(game_id="1", fd_price=-110, fair_prob=0.5, market="spreads",
                            pick="NY Team", point=-4.5, market_prob=None, game="PHI @ NY",
                            commence_time=None, books=["NBA DraftKings line"])
        with mock.patch.object(launcher, "label", return_value="NY -4.5"):
            leg = launcher.leg_record(b, "nba")
        self.assertEqual((leg["type"], leg["team"], leg["point"]), ("spread", "NY Team", -4.5))
        final = {"1": {"game_id": "1", "home_team": "NY Team", "away_team": "PHI Team",
                       "home_score": 112, "away_score": 105}}
        with mock.patch.object(nba, "games_by_id", return_value=final):
            (_, status, _), = grade.grade({"sport": "nba", "season": 2026, "legs": [leg]})
        self.assertEqual(status, "won")

    def test_shipped_weights_were_tested(self):
        w = nba.weights()
        self.assertTrue(w.get("weights"), "nba_weights.json missing")
        self.assertIn("season_start", w)
        self.assertGreater(w["test"]["all_games"]["games"], 1000)


class MinimumTest(unittest.TestCase):
    def test_nba_singles_start_at_60_percent(self):
        from nfl_edge.picks import SINGLE_MIN_PROB, single_min

        self.assertEqual(single_min("nba"), 0.60)
        self.assertEqual(single_min("nfl"), SINGLE_MIN_PROB)


if __name__ == "__main__":
    unittest.main()
