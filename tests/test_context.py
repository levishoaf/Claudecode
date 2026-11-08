import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nfl_edge.context import Features, SeasonData, name_key
from nfl_edge.factors import Coefficients, ols
from nfl_edge.finder import find_bets
from nfl_edge.model import GameModel
from tests import fixtures as fx

SAMPLE = Path(__file__).parent.parent / "nfl_edge" / "sample_odds.json"


def kc_buf_event():
    return {"id": "e1", "commence_time": "2026-09-14T17:00:00Z",
            "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills"}


class ContextTest(unittest.TestCase):
    def test_name_key(self):
        self.assertEqual(name_key("Odell Beckham Jr."), "odell beckham")
        self.assertEqual(name_key("Amon-Ra St. Brown"), "amonra st brown")

    def test_injuries_weighted_by_snap_share(self):
        snaps = fx.snaps("KC", "Star Receiver", "WR", 0.9, 0) + fx.snaps("KC", "Backup", "WR", 0.1, 0)
        injuries = [fx.injury("KC", 5, "Star Receiver", "WR", "Out"),
                    fx.injury("KC", 5, "Backup", "WR", "Questionable")]
        sd = SeasonData(fx.season(), injuries, snaps)
        off, dfn, qb_out, notable = sd.injury_report("KC", 5)
        self.assertAlmostEqual(off, 0.9 + 0.2 * 0.1)
        self.assertEqual(dfn, 0)
        self.assertEqual([i.name for i in notable], ["Star Receiver"])

    def test_primary_qb_out_from_injury_report(self):
        injuries = [fx.injury("KC", 5, "KC Starter", "QB", "Out", gsis_id="kc1")]
        sd = SeasonData(fx.season(), injuries, [])
        self.assertEqual(sd.primary_qb("KC", 5), ("kc1", "KC Starter"))
        f = sd.features(fx.season()[-1], actual_qb=False)
        self.assertEqual(f.qb_out_home, 1.0)
        self.assertEqual(f.qb_out_away, 0.0)

    def test_qb_downgrade_only_for_less_experienced_starter(self):
        rows = fx.season()
        backup = fx.game(5, "BUF", "KC", 10, 20)
        backup.update(home_qb_id="kc2", home_qb_name="KC Backup",
                      away_qb_id="buf1", away_qb_name="BUF Starter")
        sd = SeasonData(rows[:-1] + [backup], [], [])
        f = sd.features(backup, actual_qb=True)
        self.assertEqual(f.qb_out_home, 1.0)
        self.assertEqual(f.qb_out_away, 0.0)

    def test_schedule_features(self):
        row = fx.game(5, "MIA", "BUF", div_game=1, wind=22, temp=30)
        sd = SeasonData(fx.season() + [row], [], [])
        f = sd.features(row, actual_qb=False)
        self.assertEqual(f.div_game, 1)
        self.assertGreater(f.travel_diff, 1)  # Miami flies ~1,200 miles to Buffalo
        self.assertEqual(f.wind_excess, 12)
        self.assertEqual(f.cold, 15)

    def test_weather_ignored_indoors(self):
        f = Features(indoor=1.0, wind=30, temp=10)
        self.assertEqual((f.wind_excess, f.cold), (0, 0))


class FactorsTest(unittest.TestCase):
    def test_ols_recovers_coefficients(self):
        rows = [[1.0, x, (x * 7) % 5] for x in range(50)]
        y = [2 + 0.5 * r[1] - 3 * r[2] for r in rows]
        beta, se = ols(rows, y)
        for got, want in zip(beta, [2, 0.5, -3]):
            self.assertAlmostEqual(got, want, places=4)

    def test_qb_out_moves_margin_and_total(self):
        c = Coefficients.load()
        adj = dict((label, (m, t)) for label, m, t in c.adjustments(Features(qb_out_home=1.0), 0.0))
        m, t = adj["starting QB out"]
        self.assertLess(m, -1)  # home team worse
        self.assertLess(t, -1)  # fewer points

    def test_wind_lowers_total(self):
        c = Coefficients.load()
        adj = dict((label, (m, t)) for label, m, t in c.adjustments(Features(wind=25, temp=60), 0.0))
        self.assertLess(adj["wind"][1], 0)


class GameModelTest(unittest.TestCase):
    def setUp(self):
        injuries = [fx.injury("KC", 5, "KC Starter", "QB", "Out", gsis_id="kc1")]
        self.weather_calls = []

        def weather(stadium, kickoff):
            self.weather_calls.append(stadium)
            return {"temp": 20.0, "wind": 25.0, "precip": 0.1}

        rows = fx.season()
        self.model = GameModel(rows, SeasonData(rows, injuries, []), Coefficients.load(), weather)
        self.plain = GameModel(rows, SeasonData(rows, [], []), Coefficients.zero(), None)

    def test_context_adjusts_projection(self):
        with_ctx = self.model.predict(kc_buf_event())
        base = self.plain.predict(kc_buf_event())
        self.assertEqual(self.weather_calls, ["KAN00"])
        self.assertLess(with_ctx.home_pts - with_ctx.away_pts, base.home_pts - base.away_pts)
        self.assertLess(with_ctx.home_pts + with_ctx.away_pts, base.home_pts + base.away_pts)
        self.assertTrue(any("wind 25" in n for n in with_ctx.notes))

    def test_unscheduled_game_uses_ratings_only(self):
        event = dict(kc_buf_event(), commence_time="2026-12-25T17:00:00Z")
        p = self.model.predict(event)
        self.assertFalse(p.matched)
        self.assertEqual(p.adjustments, [])

    def test_find_bets_with_model(self):
        events = json.loads(SAMPLE.read_text())
        bets = find_bets(events, model=self.plain, model_weight=1, min_ev=-1,
                         now=datetime(2026, 10, 10, tzinfo=timezone.utc))
        for b in bets:
            lo, hi = sorted((b.market_prob, b.model_prob))
            self.assertTrue(lo - 1e-12 <= b.fair_prob <= hi + 1e-12)
        agree = find_bets(events, model=self.plain, model_weight=0.3, min_ev=-1,
                          require_agreement=True, now=datetime(2026, 10, 10, tzinfo=timezone.utc))
        self.assertTrue(all(b.model_prob > b.market_prob for b in agree))


if __name__ == "__main__":
    unittest.main()
