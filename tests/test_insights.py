import unittest
from types import SimpleNamespace

from nfl_edge import insights
from nfl_edge.hitrate import HitRate

GAME = {"home_team": "ATL", "away_team": "BAL"}


class FakeContext:
    """A Context whose signals are set by the test instead of loaded."""
    season = 2026
    abbr_name = {}

    def __init__(self, matchup=None, usage=None, floor=None, mates=(), qb=None, rest=(),
                 weather=None, game=GAME):
        self.m, self.u, self.f, self.mates = matchup, usage, floor, list(mates)
        self.q, self.rt, self.w, self.g = qb, list(rest), weather, game

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
    return SimpleNamespace(market=market, pick="Drake London Over", fair_prob=prob)


def rates(hits, games):
    return {2026: HitRate(hits, games, [])}


class TierTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
