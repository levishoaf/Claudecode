import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from nfl_edge import board, grade, mlb
from nfl_edge.picks import of_type

ODDS = {  # an ESPN scoreboard odds entry, trimmed
    "overUnder": 7.5, "spread": -1.5,
    "moneyline": {"home": {"close": {"odds": "-182"}}, "away": {"close": {"odds": "+149"}}},
    "pointSpread": {"home": {"close": {"line": "-1.5", "odds": "+128"}},
                    "away": {"close": {"line": "+1.5", "odds": "-155"}}},
    "total": {"over": {"close": {"line": "o7.5", "odds": "-106"}},
              "under": {"close": {"line": "u7.5", "odds": "-114"}}},
}
WEIGHTS = {"weights": {"elo": 0.44, "home": 0.12, "sp_now": 0.08},
           "cover": {"home": {"bias": -0.72, "p": 1.0}, "away": {"bias": -0.48, "p": 0.96}},
           "market_share": 1.0,
           "test": {"games": 100, "model": {"likelier_won": 0.54}, "market": {"likelier_won": 0.56}}}


def game(i, home="LAD", away="MIL", hs=None, as_=None, done=False, when="2026-10-15T00:00Z",
         lines=None, state="pre"):
    return {"id": str(i), "date": when, "season_type": 3, "home": home, "away": away,
            "home_name": f"{home} Team", "away_name": f"{away} Team", "home_score": hs,
            "away_score": as_, "state": state, "done": done, "status": "",
            "home_sp": {"id": "p1", "name": "Home Ace", "era": 2.5},
            "away_sp": {"id": "p2", "name": "Away Arm", "era": 4.5}, "lines": lines}


class LinesTest(unittest.TestCase):
    def test_prices_parse_and_cut_comes_out(self):
        lines = mlb._lines(ODDS)
        self.assertEqual((lines["home_ml"], lines["away_ml"]), (-182, 149))
        self.assertEqual((lines["home_rl_line"], lines["home_rl"], lines["away_rl"]), (-1.5, 128, -155))
        self.assertEqual((lines["total"], lines["over"], lines["under"]), (7.5, -106, -114))
        p = mlb.no_vig(-182, 149)
        self.assertAlmostEqual(p + mlb.no_vig(149, -182), 1.0)
        self.assertTrue(0.6 < p < 0.65)
        self.assertEqual(mlb.american("EVEN"), 100)
        self.assertIsNone(mlb.american(None))


class RatingsTest(unittest.TestCase):
    def test_winner_gains_and_starters_count(self):
        r = mlb.Ratings()
        r.update(game(1, hs=8, as_=1, done=True, when="2026-05-01T23:00Z"))
        self.assertGreater(r.elo["LAD"], mlb.START_ELO)
        f = r.features(game(2, when="2026-05-02T23:00Z"))
        self.assertGreater(f["elo"], 0)
        # The away starter gave up 8 in his start, the home one 1: good for the home side.
        self.assertGreater(f["sp_now"], 0)


class ChancesTest(unittest.TestCase):
    def test_posted_line_is_used_and_model_fills_in(self):
        with mock.patch.object(mlb, "weights", return_value=WEIGHTS):
            lined = mlb.chances(game(1, lines=mlb._lines(ODDS)), mlb.Ratings())
            bare = mlb.chances(game(2), mlb.Ratings())
        self.assertAlmostEqual(lined["home"], mlb.no_vig(-182, 149))
        self.assertEqual(lined["source"], "DraftKings line")
        self.assertAlmostEqual(lined["home_rl"], mlb.no_vig(128, -155))
        self.assertAlmostEqual(lined["over"], mlb.no_vig(-106, -114))
        self.assertEqual(bare["source"], "MLB model")
        self.assertLess(bare["home_rl"], bare["home"])  # winning by 2+ is harder than winning
        self.assertIsNone(bare["over"])


class BoardTest(unittest.TestCase):
    def test_moneylines_run_lines_and_totals_when_asked(self):
        soon = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ")
        games = [game(1, lines=mlb._lines(ODDS), when=soon), game(2, home="CLE", away="TB", when=soon)]
        with mock.patch.object(mlb, "upcoming_days", return_value=[date.today()]), \
                mock.patch.object(mlb, "day", return_value=games), \
                mock.patch.object(mlb, "current_ratings", return_value=mlb.Ratings()), \
                mock.patch.object(mlb, "weights", return_value=WEIGHTS):
            plain = board.mlb_board(0.0, 1.0)
            full = board.mlb_board(0.0, 1.0, every_line=True)
        self.assertEqual(len(of_type(plain, "Moneyline")), 4)
        self.assertEqual(len(of_type(plain, "Run line")), 4)
        self.assertEqual(of_type(plain, "Total runs"), [])
        totals = of_type(full, "Total runs")
        self.assertEqual(len(totals), 2)  # only the game with a posted total
        self.assertTrue(all(b.confirm_line for b in totals))
        bare = [b for b in plain if b.game_id == "2"]
        self.assertTrue(all(b.note == board.NO_LINE for b in bare))
        self.assertTrue(all(", " in b.game for b in plain))  # the date tells series games apart

    def test_run_line_legs_grade_like_spreads(self):
        import bets as launcher

        b = board.make_bet("MIL Team @ LAD Team, Oct 15", datetime.now(timezone.utc), "run_line",
                           "MIL Team", 1.5, 0.6, "9", "DraftKings line")
        leg = launcher.leg_record(b, "mlb")
        self.assertEqual((leg["type"], leg["team"], leg["point"]), ("run_line", "MIL Team", 1.5))
        games = {"9": {"home_team": "LAD Team", "away_team": "MIL Team", "home_score": 4,
                       "away_score": 3}}
        self.assertEqual(grade.grade_leg(leg, games, [])[0], "won")  # lost by 1: +1.5 covers
        games["9"]["home_score"] = 5
        self.assertEqual(grade.grade_leg(leg, games, [])[0], "lost")


class WeightsTest(unittest.TestCase):
    def test_shipped_weights_were_tested(self):
        w = mlb.weights()
        self.assertTrue(w.get("weights") and w.get("cover"))
        self.assertLessEqual(w["test"]["market"]["log_loss"], w["test"]["model"]["log_loss"])


if __name__ == "__main__":
    unittest.main()
