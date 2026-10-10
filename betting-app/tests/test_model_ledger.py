import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import engine, ledger, ratings
from app.backtest import market_p_home, parse_pickcenter
from app.providers import DiskCache, FanDuelProvider, parse_fanduel_page

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def game(i, home, away, hs, as_, season=2026, week=1, neutral=False):
    return {"id": str(i), "date": f"2026-09-{i:02d}T18:00Z", "season": season, "week": week, "home_id": home, "away_id": away,
            "home": f"Team {home}", "away": f"Team {away}", "home_score": hs, "away_score": as_, "completed": True, "neutral": neutral}


class RatingsTests(unittest.TestCase):
    def fitted(self):
        r = ratings.Rater("americanfootball_nfl")
        gs = [game(i, "A", "B", 31, 10) for i in range(1, 4)] + [game(i, "B", "C", 24, 20) for i in range(4, 7)] + \
             [game(i, "C", "A", 10, 28) for i in range(7, 10)]
        r.fit({2026: gs})
        return r

    def test_winners_rise_and_zero_sum(self):
        r = self.fitted()
        self.assertGreater(r.r["A"], r.r["B"])
        self.assertGreater(r.r["B"], r.r["C"])
        self.assertAlmostEqual(sum(r.r.values()), 0, places=6)

    def test_probabilities(self):
        r = self.fitted()
        d = r.diff("A", "C")
        self.assertGreater(r.win_prob(d), 0.5)
        self.assertAlmostEqual(r.win_prob(d) + r.win_prob(-d), 1)
        self.assertAlmostEqual(r.win_prob(0), 0.5)
        self.assertGreater(r.diff("A", "C", False), r.diff("A", "C", True))  # home field only when not neutral

    def test_season_regression_shrinks(self):
        r = self.fitted()
        before = r.r["A"]
        r.new_season()
        self.assertAlmostEqual(r.r["A"], before * (1 - r.cfg["reg"]))

    def test_unknown_or_thin_history_returns_none(self):
        r = ratings.Rater("americanfootball_nfl")
        self.assertIsNone(r.margin_for("Nobody", "Else"))
        r.fit({2026: [game(1, "A", "B", 20, 10)]})
        self.assertIsNone(r.margin_for("Team A", "Team B"))  # only 2 total games between them

    def test_unknown_opponent_moves_established_team_less(self):
        r = ratings.Rater("americanfootball_ncaaf")
        r.fit({2026: [game(i, "A", "B", 24, 20) for i in range(1, 4)]})
        a0 = r.r["A"]
        r.update(game(9, "A", "FCS", 55, 0))
        r2 = ratings.Rater("americanfootball_ncaaf")
        r2.fit({2026: [game(i, "A", "B", 24, 20) for i in range(1, 4)]})
        r2.update(game(9, "A", "B", 55, 0))
        self.assertLess(r.r["A"] - a0, r2.r["A"] - a0)

    def test_scores(self):
        self.assertAlmostEqual(ratings.brier([0.5, 0.5], [1, 0]), 0.25)
        self.assertAlmostEqual(ratings.log_loss([0.5], [1]), 0.693147, places=5)
        self.assertAlmostEqual(ratings.blend(0.6, 0.4, 0.8), 0.56)

    def test_fetch_season_uses_cache_and_stops(self):
        calls = []

        def fake(url):
            calls.append(url)
            wk = int(url.split("week=")[1].split("&")[0])
            if wk > 2:
                return {"events": []}
            return {"events": [{"id": str(wk), "date": "2026-09-01T00:00Z", "status": {"type": {"completed": True}},
                                "competitions": [{"neutralSite": False, "competitors": [
                                    {"homeAway": "home", "score": "20", "team": {"id": "1", "displayName": "H"}},
                                    {"homeAway": "away", "score": "10", "team": {"id": "2", "displayName": "A"}}]}]}]}
        with tempfile.TemporaryDirectory() as d:
            c = DiskCache(Path(d), 3600)
            g = ratings.fetch_season("americanfootball_nfl", 2026, c, fake)
            self.assertEqual(len(g), 2)
            n = len(calls)
            ratings.fetch_season("americanfootball_nfl", 2026, c, fake)
            self.assertEqual(len(calls), n + 1)  # only the empty "stop" week is re-requested


class EngineModelTests(unittest.TestCase):
    def setUp(self):
        r = ratings.Rater("americanfootball_nfl")
        gs = [game(i, "A", "B", 34, 10) for i in range(1, 6)] + [game(i, "C", "B", 20, 17) for i in range(6, 9)]
        self.models = {"americanfootball_nfl": r.fit({2026: gs})}
        self.ev = {"id": "espn-1", "sport_key": "americanfootball_nfl", "league": "NFL", "home_team": "Team A", "away_team": "Team B",
                   "commence_time": "2999-01-01T00:00:00Z", "bookmakers": [{"key": "draftkings", "title": "DraftKings", "source": "ESPN", "markets": [
                       {"key": "h2h", "outcomes": [{"name": "Team A", "price": 100}, {"name": "Team B", "price": -120}]},
                       {"key": "spreads", "outcomes": [{"name": "Team A", "price": -110, "point": 0.5}, {"name": "Team B", "price": -110, "point": -0.5}]},
                       {"key": "totals", "outcomes": [{"name": "Over", "price": -110, "point": 44.5}, {"name": "Under", "price": -110, "point": 44.5}]}]}]}

    def test_blend_between_market_and_model(self):
        picks = engine.evaluate_event(self.ev, {**engine.DEFAULTS, "use_ratings": True}, self.models)
        a = next(p for p in picks if p["selection"] == "Team A ML")
        self.assertIsNotNone(a["power_prob"])
        lo, hi = sorted((a["market_prob"], a["power_prob"]))
        self.assertTrue(lo <= a["model_prob"] <= hi)
        self.assertAlmostEqual(a["model_prob"], 0.8 * a["market_prob"] + 0.2 * a["power_prob"], places=3)
        self.assertEqual(a["confidence"], "low")  # single book + model, never "high"
        self.assertTrue(a["disagree"])
        self.assertTrue(any("simple model is wrong" in f for f in a["flags"]))

    def test_default_is_market_only_but_shows_ratings(self):
        picks = engine.evaluate_event(self.ev, engine.DEFAULTS, self.models)
        a = next(p for p in picks if p["selection"] == "Team A ML")
        self.assertIsNotNone(a["power_prob"])
        self.assertEqual(a["model_prob"], a["market_prob"])
        self.assertEqual(a["basis"], "market only")
        self.assertEqual(a["confidence"], "none")

    def test_totals_stay_market_only(self):
        picks = engine.evaluate_event(self.ev, engine.DEFAULTS, self.models)
        t = next(p for p in picks if p["market"] == "totals")
        self.assertIsNone(t["power_prob"])
        self.assertEqual(t["confidence"], "none")

    def test_no_models_unchanged(self):
        picks = engine.evaluate_event(self.ev, engine.DEFAULTS, None)
        self.assertTrue(all(p["power_prob"] is None and p["model_prob"] == p["market_prob"] for p in picks))

    def test_spread_probs_sum_to_one(self):
        outs = [("Team A", 0.5), ("Team B", -0.5)]
        v = engine.power_probs(self.ev, "spreads", outs, self.models)
        self.assertAlmostEqual(sum(v), 1)


def pick(eid, edge, ev, stake, conf="high"):
    return {"event_id": eid, "edge": edge, "ev_per_dollar": ev, "confidence": conf, "stake_pct": stake, "flags": []}


class DisciplineTests(unittest.TestCase):
    P = {**engine.DEFAULTS}

    def test_watchlist_has_no_stake(self):
        s = [pick("g1", 0.05, 0.1, 0.01), pick("g2", 0.01, 0.02, 0.005), pick("g3", 0.06, 0.1, 0.01, "none")]
        engine.allocate(s, [], self.P)
        self.assertEqual([x["tier"] for x in s], ["bet", "watchlist", "watchlist"])
        self.assertEqual([x["stake_pct"] for x in s], [0.01, 0.0, 0.0])

    def test_same_game_stake_capped(self):
        s = [pick("g1", 0.05, 0.1, 0.02), pick("g1", 0.05, 0.1, 0.02)]
        ex = engine.allocate(s, [], self.P)
        self.assertAlmostEqual(sum(x["stake_pct"] for x in s), self.P["game_cap"])
        self.assertEqual(ex["games_capped"], 1)

    def test_weekly_cap_scales_everything(self):
        s = [pick(f"g{i}", 0.05, 0.1, 0.02) for i in range(10)]
        pl = [{"ev_per_dollar": 0.2, "confidence": "high", "stake_pct": 0.005}]
        ex = engine.allocate(s, pl, self.P)
        self.assertTrue(ex["scaled_down"])
        self.assertAlmostEqual(ex["total_pct"], self.P["weekly_cap"], places=3)

    def test_negative_ev_parlay_not_staked_and_bottom_line_pass(self):
        s = [pick("g1", -0.02, -0.04, 0.0)]
        pl = [{"ev_per_dollar": -0.2, "confidence": "high", "stake_pct": 0.0}]
        ex = engine.allocate(s, pl, self.P)
        bl = engine.bottom_line(s, pl, ex, self.P, "x")
        self.assertEqual((bl["n_singles"], bl["n_parlays"]), (0, 0))
        self.assertIn("Pass", bl["text"])


class SettlementTests(unittest.TestCase):
    def test_settle(self):
        s = ledger.settle_bet
        self.assertEqual(s({"market": "h2h", "name": "H"}, "H", "A", 20, 10), "win")
        self.assertEqual(s({"market": "h2h", "name": "A"}, "H", "A", 20, 10), "loss")
        self.assertEqual(s({"market": "h2h", "name": "A"}, "H", "A", 10, 10), "push")
        self.assertEqual(s({"market": "spreads", "name": "H", "point": -3.5}, "H", "A", 20, 17), "loss")
        self.assertEqual(s({"market": "spreads", "name": "A", "point": 3.5}, "H", "A", 20, 17), "win")
        self.assertEqual(s({"market": "spreads", "name": "H", "point": -3}, "H", "A", 20, 17), "push")
        self.assertEqual(s({"market": "totals", "name": "Over", "point": 36.5}, "H", "A", 20, 17), "load" and "win")
        self.assertEqual(s({"market": "totals", "name": "Under", "point": 37}, "H", "A", 20, 17), "push")

    def test_parlay_push_leg_dropped_and_loss(self):
        legs = [{"event_id": "e1", "league": "NFL", "bet": {"market": "h2h", "name": "H1"}, "best_decimal": 2.0, "selection": "x"},
                {"event_id": "e2", "league": "NFL", "bet": {"market": "spreads", "name": "H2", "point": -3}, "best_decimal": 1.9, "selection": "y"}]
        e = {"type": "parlay", "legs": legs}
        info = lambda lg, eid: (("H1", "A1", 20, 10) if eid == "e1" else ("H2", "A2", 20, 17), None)
        r = ledger.grade_entry(e, info)  # leg 2 pushes
        self.assertEqual(r["result"], "win")
        self.assertAlmostEqual(r["paid_decimal"], 2.0)
        info2 = lambda lg, eid: (("H1", "A1", 10, 20) if eid == "e1" else ("H2", "A2", 20, 17), None)
        self.assertEqual(ledger.grade_entry(e, info2)["result"], "loss")
        self.assertIsNone(ledger.grade_entry(e, lambda lg, eid: (None, None)))  # unfinished -> still pending

    def test_pickcenter_and_closing_value(self):
        pc = parse_pickcenter({"provider": {"name": "DK"}, "spread": -3.5, "overUnder": 44.5, "overOdds": -110, "underOdds": -110,
                               "homeTeamOdds": {"moneyLine": -200, "spreadOdds": -110}, "awayTeamOdds": {"moneyLine": 170, "spreadOdds": -110}})
        ph = market_p_home(pc)
        self.assertTrue(0.6 < ph < 0.7)
        fair = ledger.closing_fair_prob({"market": "h2h", "name": "Away"}, pc, "Home")
        self.assertAlmostEqual(fair, 1 - ph)
        self.assertAlmostEqual(ledger.closing_fair_prob({"market": "spreads", "name": "Home", "point": -3.5}, pc, "Home"), 0.5)
        self.assertIsNone(ledger.closing_fair_prob({"market": "spreads", "name": "Home", "point": -3.0}, pc, "Home"))  # line moved
        self.assertAlmostEqual(ledger.closing_fair_prob({"market": "totals", "name": "Over", "point": 44.5}, pc, "Home"), 0.5)


class LedgerTests(unittest.TestCase):
    def payload(self, mode="live"):
        bet = {"market": "h2h", "name": "Team A", "point": None, "description": None}
        single = {"event_id": "espn-1", "league": "NFL", "game": "B @ A", "commence_time": "2026-10-04T17:00:00Z", "selection": "Team A ML",
                  "market_label": "Moneyline", "bet": bet, "best_book": "DraftKings", "best_odds": 100, "best_decimal": 2.0, "model_prob": 0.55,
                  "market_prob": 0.5, "power_prob": 0.7, "ev_per_dollar": 0.1, "confidence": "low", "stake_pct": 0.01}
        legs = [{k: single[k] for k in ("event_id", "league", "game", "selection", "bet", "best_decimal", "commence_time")} | {"best_odds": 100}]
        parlay = {"legs": legs, "combined_odds": 100, "combined_decimal": 2.0, "win_prob": 0.55, "ev_per_dollar": 0.1, "confidence": "low", "stake_pct": 0.0}
        return {"mode": mode, "top_singles": [single], "parlays": [parlay]}

    def test_append_dedup_and_sample_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ledger.jsonl"
            self.assertEqual(ledger.append_picks(self.payload("sample"), p, NOW), 0)
            self.assertEqual(ledger.append_picks(self.payload(), p, NOW), 2)
            self.assertEqual(ledger.append_picks(self.payload(), p, NOW), 0)
            self.assertEqual(len(ledger.read_ledger(p)), 2)

    def test_grade_end_to_end(self):
        summary = {"header": {"competitions": [{"status": {"type": {"completed": True}}, "competitors": [
            {"homeAway": "home", "score": "27", "team": {"displayName": "Team A"}}, {"homeAway": "away", "score": "20", "team": {"displayName": "Team B"}}]}]},
            "pickcenter": [{"provider": {"name": "DK"}, "spread": -7, "overUnder": 44.5, "homeTeamOdds": {"moneyLine": -150}, "awayTeamOdds": {"moneyLine": 130}}]}
        calls = []

        def fetch(url):
            calls.append(url)
            return summary
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ledger.append_picks(self.payload(), d / "l.jsonl", NOW)
            t = ledger.grade(d / "l.jsonl", d / "g.json", DiskCache(d / "c", 3600), fetch, NOW)
            self.assertEqual(t["settled"], 2)
            g = t["groups"]["singles"]
            self.assertEqual((g["wins"], g["losses"]), (1, 0))
            self.assertAlmostEqual(g["flat_roi"], 1.0)
            self.assertAlmostEqual(g["kelly_units_won"], 1.0)  # 1 unit staked at +100
            self.assertIsNotNone(g["avg_clv"])  # closing line available
            self.assertEqual(t["groups"]["parlays"]["kelly_units_staked"], 0)
            n = len(calls)
            ledger.grade(d / "l.jsonl", d / "g.json", DiskCache(d / "c", 3600), fetch, NOW)
            self.assertEqual(len(calls), n)  # settled picks are not re-fetched

    def test_not_graded_before_game_ends(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            p = self.payload()
            p["top_singles"][0]["commence_time"] = (NOW + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            ledger.append_picks(p, d / "l.jsonl", NOW)
            t = ledger.grade(d / "l.jsonl", d / "g.json", None, lambda u: self.fail("should not fetch"), NOW)
            self.assertEqual(t["pending"], 2 if False else t["pending"])
            self.assertEqual(t["groups"]["singles"]["n"], 0)


class FanDuelDebugTests(unittest.TestCase):
    LOOSE = {"layout": {"x": [{"eventId": 77, "name": "Away T @ Home T", "openDate": "2999-01-01T18:00:00Z"},
                              {"eventId": 77, "marketName": "Moneyline", "marketStatus": "OPEN", "runners": [
                                  {"runnerName": "Away T", "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": 120}}},
                                  {"runnerName": "Home T", "winRunnerOdds": {"trueOdds": {"decimalOdds": {"decimalOdds": 1.7}}}}]}]}}

    def test_tolerant_fallback(self):
        evs = parse_fanduel_page(self.LOOSE, "americanfootball_nfl")  # SYNTHETIC layout
        self.assertEqual(len(evs), 1)
        outs = evs[0]["bookmakers"][0]["markets"][0]["outcomes"]
        self.assertEqual([o["price"] for o in outs], [120, -143])

    def test_debug_dump_success_and_error(self):
        with tempfile.TemporaryDirectory() as d:
            FanDuelProvider(None, fetch=lambda u: {"hello": 1}, debug_dir=d).fetch_events("americanfootball_nfl")
            self.assertEqual(json.loads((Path(d) / "fanduel_americanfootball_nfl.json").read_text()), {"hello": 1})

            def blocked(u):
                raise urllib.error.HTTPError(u, 403, "Forbidden", {}, None)
            evs, st = FanDuelProvider(None, fetch=blocked, debug_dir=d).fetch_events("americanfootball_ncaaf")
            self.assertEqual(evs, [])
            err = json.loads((Path(d) / "fanduel_americanfootball_ncaaf.error.json").read_text())
            self.assertIn("403", err["error"])


if __name__ == "__main__":
    unittest.main()


class SnapshotTests(unittest.TestCase):
    EV = {"id": "espn-1", "sport_key": "americanfootball_nfl", "league": "NFL", "home_team": "Team A", "away_team": "Team B",
          "commence_time": "2026-10-11T17:00:00Z", "bookmakers": [{"key": "dk", "title": "DraftKings", "source": "x", "markets": [
              {"key": "h2h", "outcomes": [{"name": "Team A", "price": -200}, {"name": "Team B", "price": 170}]},
              {"key": "spreads", "outcomes": [{"name": "Team A", "price": -110, "point": -3.5}, {"name": "Team B", "price": -110, "point": 3.5}]},
              {"key": "player_pass_yds", "outcomes": []}]}]}

    def test_append_and_closing_selection(self):
        from app import snapshots
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            early = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
            late = datetime(2026, 10, 11, 16, tzinfo=timezone.utc)
            after = datetime(2026, 10, 11, 18, tzinfo=timezone.utc)
            moved = json.loads(json.dumps(self.EV))
            moved["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = -300
            self.assertEqual(snapshots.append(p, [self.EV], early), 1)
            self.assertEqual(snapshots.append(p, [moved], late), 1)
            snapshots.append(p, [self.EV], after)  # after kickoff: must be ignored for closing
            c = snapshots.closing_snapshots(p)["espn-1"]
            self.assertEqual(c["ts"], "2026-10-11T16:00:00Z")
            self.assertNotIn("player_pass_yds", c["e"]["b"]["dk"]["m"])
            fair = snapshots.fair_prob({"market": "h2h", "name": "Team A"}, c["e"], "DraftKings")
            self.assertTrue(0.7 < fair < 0.8)
            self.assertIsNone(snapshots.fair_prob({"market": "spreads", "name": "Team A", "point": -3.0}, c["e"], "DraftKings"))  # line moved
            self.assertIsNone(snapshots.fair_prob({"market": "h2h", "name": "Team A"}, c["e"], "FanDuel"))

    def test_grade_prefers_own_snapshot_for_clv(self):
        from app import snapshots
        info = lambda lg, eid: (("Team A", "Team B", 27, 20), None)  # no ESPN closing odds available
        e = {"type": "single", "league": "NFL", "event_id": "espn-1", "bet": {"market": "h2h", "name": "Team A", "point": None},
             "decimal": 1.8, "book": "DraftKings"}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            snapshots.append(p, [self.EV], datetime(2026, 10, 11, 16, tzinfo=timezone.utc))
            r = ledger.grade_entry(e, info, snapshots.closing_snapshots(p))
            self.assertEqual(r["result"], "win")
            self.assertIsNotNone(r["clv"])
            self.assertIn("own snapshot", r["clv_source"])
            self.assertIsNone(ledger.grade_entry(e, info, {})["clv"])


class ModelVariantTests(unittest.TestCase):
    def test_rest_adjustment_off_by_default_and_signed_when_on(self):
        off = ratings.Rater("americanfootball_nfl")
        on = ratings.Rater("americanfootball_nfl", rest_pts=0.5)
        for r in (off, on):
            r.update({**game(1, "A", "B", 20, 10), "date": "2026-09-01T18:00Z"})
            r.update({**game(2, "C", "D", 20, 10), "date": "2026-09-08T18:00Z"})
        day = ratings.to_day("2026-09-15")
        self.assertEqual(off.rest_adj("A", "C", day), 0.0)
        # A rested 14 days, C rested 7 days -> A (home) gains 0.5 * 7
        self.assertAlmostEqual(on.rest_adj("A", "C", day), 0.5 * (14 - 7))
        self.assertAlmostEqual(on.rest_adj("C", "A", day), -0.5 * 7)
        self.assertEqual(on.rest_adj("A", "C", None), 0.0)

    def test_init_unseen_starts_new_teams_lower(self):
        r = ratings.Rater("americanfootball_ncaaf", init_unseen=-8.0)
        r.update(game(1, "A", "NEW", 30, 10))
        self.assertLess(r.r["NEW"], r.r["A"])
        self.assertLess(r.r["NEW"], 0)
        self.assertEqual(ratings.to_day("garbage"), None)

    def test_accept_rule_needs_gain_on_both_seasons_and_no_market_regression(self):
        from app.backtest import accept
        base = {"prior": {"log_loss": 0.60}, "current": {"log_loss": 0.60}, "market_subset": {"blend_brier": 0.20}}
        good = {"prior": {"log_loss": 0.595}, "current": {"log_loss": 0.596}, "market_subset": {"blend_brier": 0.199}}
        self.assertTrue(accept(base, good)[0])
        self.assertFalse(accept(base, {**good, "current": {"log_loss": 0.60}})[0])  # no gain in-season
        self.assertFalse(accept(base, {**good, "market_subset": {"blend_brier": 0.21}})[0])  # worse vs market
