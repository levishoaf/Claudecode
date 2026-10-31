import unittest
from types import SimpleNamespace
from unittest import mock

from nfl_edge import insights, signals
from nfl_edge.hitrate import HitRate

GAME = {"home_team": "ATL", "away_team": "BAL"}


class FakeContext:
    """A Context whose signals are set by the test instead of loaded."""
    season = 2026
    abbr_name = {}

    def __init__(self, matchup=None, usage=None, floor=None, mates=(), qb=None, rest=(),
                 weather=None, game=GAME, feats=None):
        self.m, self.u, self.f, self.mates = matchup, usage, floor, list(mates)
        self.q, self.rt, self.w, self.g = qb, list(rest), weather, game
        self.feats = feats or {}

    def signal_features(self, *a): return self.feats

    def game_row(self, b): return self.g
    def team_of(self, b): return "ATL"
    def matchup(self, *a): return self.m
    def usage(self, *a): return self.u
    def floor(self, *a): return self.f
    def teammates(self, *a): return self.mates
    def splits(self, *a): return None
    def rest_travel(self, *a): return self.rt
    def qb(self, *a): return self.q
    def weather_flag(self, *a): return self.w


def prop(prob=0.7, market="player_reception_yds"):
    return SimpleNamespace(market=market, pick="Drake London Over", fair_prob=prob,
                           priced=False, fd_price=-233, ev=0.0, kelly=0.0, point=49.5)


def rates(hits, games):
    return {2026: HitRate(hits, games, [])}


class TierTest(unittest.TestCase):
    """The hand-scored tiers, still used for kinds of bet the signals weren't tested on."""

    def setUp(self):
        patch = mock.patch.object(signals, "tested", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)

    def test_green_needs_two_good_signals_and_enough_games(self):
        ctx = FakeContext(matchup=("Allows the 3rd most", 1), floor=("Cleared 8 of 8", 1))
        ins = insights.analyze(prop(), ctx, rates(6, 7))
        self.assertEqual(ins.tier, insights.GREEN)
        self.assertIn("strong record", ins.reasons)
        self.assertIn("Allows the 3rd most", ins.notes)

    def test_small_sample_is_never_green(self):
        ctx = FakeContext(matchup=("m", 1), floor=("f", 1))
        ins = insights.analyze(prop(), ctx, rates(3, 3))
        self.assertTrue(ins.small_sample)
        self.assertEqual(ins.tier, insights.YELLOW)
        self.assertTrue(any("Small sample" in f for f in ins.flags))

    def test_weak_record_and_tough_matchup_is_red(self):
        ctx = FakeContext(matchup=("Allows the fewest", -1))
        ins = insights.analyze(prop(0.75), ctx, rates(2, 7))
        self.assertEqual(ins.tier, insights.RED)

    def test_qb_change_blocks_green(self):
        ctx = FakeContext(matchup=("m", 1), floor=("f", 1), usage=("u", 1),
                          qb="Kirk Cousins is Questionable")
        ins = insights.analyze(prop(), ctx, rates(6, 7))
        self.assertNotEqual(ins.tier, insights.GREEN)
        self.assertIn("Kirk Cousins is Questionable", ins.flags)

    def test_bad_weather_hurts_passing_props(self):
        ctx = FakeContext(weather="Wind 22 mph")
        ins = insights.analyze(prop(), ctx, rates(4, 7))
        self.assertIn("bad weather", ins.reasons)
        self.assertEqual(ins.tier, insights.RED)

    def test_touchdowns_flagged_streaky(self):
        ins = insights.analyze(prop(0.3, "player_anytime_td"), FakeContext(game=None),
                               rates(2, 7))
        self.assertTrue(any("streaky" in f for f in ins.flags))


class LearnedTierTest(unittest.TestCase):
    """Tiers from the weights learned on past seasons (signal_weights.json)."""

    def test_good_signals_raise_the_chance_and_make_it_strong(self):
        b = prop(0.6, "player_rush_yds_alternate")  # raising held up for rushing yards
        ctx = FakeContext(feats={"record": 0.3, "trend": 0.4, "matchup": 0.2, "home": 1.0})
        ins = insights.analyze(b, ctx, rates(6, 7))
        self.assertGreater(b.fair_prob, 0.62)
        self.assertEqual(ins.tier, insights.GREEN)
        self.assertIn("usage rising", ins.reasons)

    def test_only_directions_that_held_up(self):
        b = prop(0.6)  # receiving yards: lowering held up, raising didn't
        ins = insights.analyze(b, FakeContext(feats={"record": 0.3, "trend": 0.4}),
                               rates(6, 7))
        self.assertEqual((b.fair_prob, ins.tier), (0.6, insights.YELLOW))

    def test_bad_signals_lower_it_and_make_it_weak(self):
        b = prop(0.6)
        ctx = FakeContext(feats={"record": -0.3, "trend": -0.4, "matchup": -0.2})
        ins = insights.analyze(b, ctx, rates(2, 7))
        self.assertLess(b.fair_prob, 0.58)
        self.assertEqual(ins.tier, insights.RED)

    def test_applied_once(self):
        b = prop(0.6)
        ctx = FakeContext(feats={"record": -0.3, "trend": -0.4})
        insights.analyze(b, ctx, rates(6, 7))
        once = b.fair_prob
        insights.analyze(b, ctx, rates(6, 7))
        self.assertAlmostEqual(b.fair_prob, once)

    def test_tested_without_signals_is_mixed(self):
        b = prop(0.6, "player_pass_yds_alternate")  # its signals didn't predict 2025
        ins = insights.analyze(b, FakeContext(matchup=("m", 1), floor=("f", 1)), rates(6, 7))
        self.assertEqual((ins.tier, b.fair_prob), (insights.YELLOW, 0.6))


class ParlayPoolTest(unittest.TestCase):
    def legs(self, tier, games, each=1):
        return [SimpleNamespace(tier=tier, game=f"G{g}") for g in range(games)
                for _ in range(each)]

    def test_green_only_when_enough(self):
        bets = self.legs("green", 4, 3) + self.legs("yellow", 4) + self.legs("red", 4)
        pool, note = insights.parlay_pool(bets)
        self.assertEqual({b.tier for b in pool}, {"green"})
        self.assertIn("only Strong", note)

    def test_falls_back_to_yellow_never_red(self):
        bets = self.legs("green", 2) + self.legs("yellow", 4) + self.legs("red", 4)
        pool, note = insights.parlay_pool(bets)
        self.assertEqual({b.tier for b in pool}, {"green", "yellow"})
        self.assertIn("Weak (red) legs are left out", note)


class ReportCardTest(unittest.TestCase):
    def entry(self, status, prob, kind="single", tier="green", type_="player_stat"):
        leg = {"type": type_, "stat": "receiving_yards", "tier": tier}
        return {"status": status, "win_prob": prob, "kind": kind,
                "legs": [leg] if kind == "single" else [leg, dict(leg)]}

    def test_predicted_vs_actual(self):
        card = insights.report_card([
            self.entry("won", 0.8), self.entry("lost", 0.6),
            self.entry("won", 0.7, tier="red", type_="moneyline"),
            self.entry("pending", 0.9), self.entry("push", 0.5),
            self.entry("lost", 0.3, kind="parlay")])
        self.assertEqual(card["type"][0], ("Receiving yards", 1, 2, 0.7, 0.5))
        self.assertEqual([r[0] for r in card["tier"]], ["Strong", "Weak"])
        self.assertEqual(card["tier"][1][1:3], (1, 1))
        self.assertEqual(card["parlays"], [("All Strong legs", 0, 1, 0.3, 0.0)])

    def test_old_bets_without_tiers(self):
        e = self.entry("won", 0.6, tier=None)
        card = insights.report_card([e])
        self.assertEqual(card["tier"], [])
        self.assertEqual(len(card["type"]), 1)



class HeadToHeadWarningTest(unittest.TestCase):
    def ml(self, pick="Detroit Lions"):
        return SimpleNamespace(market="h2h", pick=pick, game="Detroit Lions @ Green Bay Packers")

    def test_losing_record_warns(self):
        h2h = SimpleNamespace(hits=1, games=3, results=("2026 L 10-24", "2025 W 30-20",
                                                         "2025 L 17-27"))
        w = insights.h2h_warning(self.ml(), h2h, 2026)
        self.assertIn("lost 2 of 3 meetings with the Green Bay Packers since 2024", w)

    def test_no_warning_otherwise(self):
        even = SimpleNamespace(hits=1, games=2, results=("2026 W 24-10", "2025 L 17-27"))
        self.assertIsNone(insights.h2h_warning(self.ml(), even, 2026))
        one = SimpleNamespace(hits=0, games=1, results=("2026 L 10-24",))
        self.assertIsNone(insights.h2h_warning(self.ml(), one, 2026))  # one game isn't a record
        spread = SimpleNamespace(market="spreads", pick="Detroit Lions", game="x")
        self.assertIsNone(insights.h2h_warning(spread, even, 2026))


if __name__ == "__main__":
    unittest.main()
