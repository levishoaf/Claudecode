import math
import unittest

from app import odds_math as om


class OddsMath(unittest.TestCase):
    def test_conversions(self):
        self.assertAlmostEqual(om.american_to_decimal(+150), 2.5)
        self.assertAlmostEqual(om.american_to_decimal(-110), 1 + 100 / 110)
        self.assertEqual(om.decimal_to_american(2.5), 150)
        self.assertEqual(om.decimal_to_american(1 + 100 / 110), -110)
        self.assertAlmostEqual(om.implied_prob_from_american(-200), 2 / 3)
        for bad in (0, 50, -99):
            with self.assertRaises(ValueError):
                om.american_to_decimal(bad)

    def test_devig_proportional(self):
        p = om.devig_proportional([om.implied_prob_from_american(-110)] * 2)
        self.assertEqual([round(x, 6) for x in p], [0.5, 0.5])
        p = om.devig_proportional([0.6, 0.5])
        self.assertAlmostEqual(sum(p), 1)
        self.assertAlmostEqual(p[0], 0.6 / 1.1)

    def test_devig_power(self):
        imp = [0.7, 0.35]  # overround 1.05
        p = om.devig_power(imp)
        self.assertAlmostEqual(sum(p), 1, places=9)
        self.assertLess(p[1] / 0.35, p[0] / 0.7)  # longshot shrinks proportionally more
        self.assertEqual([round(x, 6) for x in om.devig_power([0.5, 0.5])], [0.5, 0.5])
        # under-round input is scaled up to sum 1
        self.assertAlmostEqual(sum(om.devig_power([0.4, 0.5])), 1)

    def test_consensus_weights(self):
        c = om.weighted_consensus([[0.6, 0.4], [0.5, 0.5]], [3, 1])
        self.assertAlmostEqual(c[0], (0.6 * 3 + 0.5) / 4)
        self.assertAlmostEqual(sum(c), 1)
        with self.assertRaises(ValueError):
            om.weighted_consensus([[0.5, 0.5]], [1, 2])

    def test_ev_edge(self):
        self.assertAlmostEqual(om.expected_value(0.5, 2.0), 0.0)
        self.assertAlmostEqual(om.expected_value(0.55, 2.0), 0.10)
        self.assertAlmostEqual(om.expected_value(0.5, 1.9), -0.05)
        self.assertAlmostEqual(om.edge(0.55, 2.0), 0.05)

    def test_kelly(self):
        # p=.55 at even money: f* = .10
        self.assertAlmostEqual(om.kelly_full(0.55, 2.0), 0.10)
        self.assertAlmostEqual(om.kelly_stake(0.55, 2.0, 0.25, 0.02), 0.02 if 0.025 > 0.02 else 0.025)
        self.assertAlmostEqual(om.kelly_stake(0.51, 2.0, 0.25, 0.02), 0.005)
        self.assertEqual(om.kelly_full(0.4, 2.0), 0.0)  # negative edge -> no bet
        self.assertEqual(om.kelly_stake(0.4, 2.0), 0.0)
        self.assertLessEqual(om.kelly_stake(0.9, 3.0, 1.0, 0.02), 0.02)

    def test_parlay(self):
        legs = [(0.55, 2.0), (0.60, 1.8), (0.5, 2.1)]
        s = om.parlay_summary(legs)
        self.assertAlmostEqual(s["decimal"], 2.0 * 1.8 * 2.1)
        self.assertAlmostEqual(s["prob"], 0.55 * 0.6 * 0.5)
        self.assertAlmostEqual(s["ev"], s["prob"] * s["decimal"] - 1)
        self.assertAlmostEqual(s["implied_prob"], 1 / s["decimal"])
        self.assertEqual(s["american"], om.decimal_to_american(s["decimal"]))

    def test_parlay_vig_compounds(self):
        # fair 50/50 legs priced at -110 (4.5% margin each): 3-leg parlay EV is worse than single EV
        d = om.american_to_decimal(-110)
        single = om.expected_value(0.5, d)
        par = om.parlay_summary([(0.5, d)] * 3)["ev"]
        self.assertLess(par, single)
        self.assertTrue(math.isclose(par, 0.125 * d ** 3 - 1))


if __name__ == "__main__":
    unittest.main()
