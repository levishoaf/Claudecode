import random
import unittest
from datetime import datetime, timezone

from nfl_edge import learn
from nfl_edge.board import make_bet


def results(model_p, real_p, n, seed=1):
    rnd = random.Random(seed)
    return [(model_p, rnd.random() < real_p, 1.0) for _ in range(n)]


class CurveTest(unittest.TestCase):
    def test_no_evidence_no_change(self):
        curve = learn.fit([])
        for p in (0.1, 0.55, 0.8):
            self.assertAlmostEqual(learn.apply(curve, p), p)

    def test_overrated_bets_come_down(self):
        curve = learn.fit(results(0.65, 0.52, 3000))
        self.assertAlmostEqual(learn.apply(curve, 0.65), 0.52, delta=0.03)

    def test_a_few_results_barely_move_it(self):
        curve = learn.fit([(0.7, False, 1.0)] * 5)
        self.assertGreater(learn.apply(curve, 0.7), 0.6)

    def test_never_falls_as_chance_rises(self):
        pts = results(0.6, 0.3, 500) + results(0.5, 0.6, 500, seed=2)
        curve = learn.fit(pts)
        ps = [i / 100 for i in range(1, 100)]
        got = [learn.apply(curve, p) for p in ps]
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(got, got[1:])))


class LiveTest(unittest.TestCase):
    def tearDown(self):
        learn.remember([])

    def entry(self, status, prob, leg_id, stat="receiving_yards", raw=None):
        leg = {"id": leg_id, "type": "player_stat", "stat": stat, "status": status,
               "win_prob": prob}
        if raw is not None:
            leg["raw_prob"] = raw
        return {"sport": "nfl", "legs": [leg]}

    def test_groups_dedupes_and_uses_raw(self):
        entries = [self.entry("won", 0.7, "a", raw=0.8), self.entry("won", 0.7, "a"),
                   self.entry("lost", 0.6, "b", stat="receptions"),
                   self.entry("pending", 0.6, "c")]
        legs = learn.graded_legs(entries)
        self.assertEqual(legs["nfl:prop:receiving_yards"], [(0.8, True, 1.0)])
        self.assertEqual(len(legs["nfl:prop:receptions"]), 1)

    def test_graded_picks_shift_the_chances(self):
        group = "nfl:prop:rushing_tds+receiving_tds"
        before = learn.calibrated(0.6, group)
        learn.remember([self.entry("lost", 0.6, str(i), stat="rushing_tds+receiving_tds")
                        for i in range(200)])
        self.assertLess(learn.calibrated(0.6, group), before - 0.1)

    def test_adjust_reprices_and_keeps_the_original(self):
        kick = datetime.now(timezone.utc)
        b = make_bet("A @ B", kick, "player_reception_yds_alternate", "X Over", 49.5,
                     0.65, "g", "player logs")
        learn.adjust([b], "nfl")
        self.assertEqual(b.raw_prob, 0.65)
        self.assertLess(b.fair_prob, 0.6)  # receiving yards ran hot in past seasons
        self.assertAlmostEqual(b.ev, 0.0, delta=0.02)  # still priced at break-even
        once = b.fair_prob
        learn.adjust([b], "nfl")  # adjusting twice changes nothing
        self.assertEqual(b.fair_prob, once)

    def test_market_lines_untouched(self):
        self.assertAlmostEqual(learn.calibrated(0.7, "nfl:moneyline"), 0.7)


if __name__ == "__main__":
    unittest.main()
