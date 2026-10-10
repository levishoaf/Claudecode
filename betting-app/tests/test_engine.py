import unittest
from datetime import datetime, timezone

from app import engine
from app.providers import merge_events, parse_espn_scoreboard, parse_fanduel_page, EspnProvider, FanDuelProvider
from app.sample_data import build_events

FUTURE = "2999-01-01T00:00:00Z"


def ev(books, eid="g1"):
    return {"id": eid, "sport_key": "americanfootball_nfl", "league": "NFL", "home_team": "H", "away_team": "A",
            "commence_time": FUTURE, "bookmakers": books}


def book(key, hp, ap, hpt=None):
    o = [{"name": "H", "price": hp}, {"name": "A", "price": ap}]
    return {"key": key, "title": key, "source": "test", "markets": [{"key": "h2h", "outcomes": o}]}


P = {**engine.DEFAULTS}


class Engine(unittest.TestCase):
    def test_single_source_has_negative_ev_and_flag(self):
        picks = engine.evaluate_event(ev([book("dk", -110, -110)]), P)
        self.assertTrue(all(p["ev_per_dollar"] < 0 for p in picks))
        self.assertTrue(all(p["confidence"] == "none" for p in picks))

    def test_best_price_beats_consensus(self):
        books = [book("pinnacle", -105, -105), book("a", -115, -115), book("b", -115, -115), book("c", +105, -130)]
        picks = engine.evaluate_event(ev(books), P)
        h = next(p for p in picks if p["selection"] == "H ML")
        self.assertEqual(h["best_book"], "c")
        self.assertGreater(h["ev_per_dollar"], 0)
        self.assertEqual(h["confidence"], "high")

    def test_lines_only_compared_when_identical(self):
        def sp(key, pt):
            return {"key": key, "title": key, "source": "t", "markets": [{"key": "spreads", "outcomes": [
                {"name": "H", "price": -110, "point": pt}, {"name": "A", "price": -110, "point": -pt}]}]}
        picks = engine.evaluate_event(ev([sp("a", -3), sp("b", -3.5)]), P)
        self.assertTrue(all(p["n_books"] == 1 for p in picks))

    def test_absurd_edge_dropped(self):
        books = [book("a", -110, -110), book("b", -110, -110), book("c", +900, -110)]
        picks = engine.evaluate_event(ev(books), P)
        self.assertFalse([p for p in picks if p["best_book"] == "c" and p["selection"] == "H ML"])

    def test_parlays_one_leg_per_game_no_dupes(self):
        events = build_events("americanfootball_nfl", datetime(2026, 10, 10, tzinfo=timezone.utc))
        events += build_events("americanfootball_ncaaf", datetime(2026, 10, 10, tzinfo=timezone.utc))
        picks = [p for e in events for p in engine.evaluate_event(e, P)]
        picks.sort(key=lambda x: -x["ev_per_dollar"])
        parlays = engine.build_parlays(picks[:200], P)
        self.assertGreaterEqual(len(parlays), 1)
        for pl in parlays:
            self.assertTrue(3 <= pl["n_legs"] <= 5)
            games = [l["game"] for l in pl["legs"]]
            self.assertEqual(len(games), len(set(games)))
            self.assertAlmostEqual(pl["ev_per_dollar"], pl["win_prob"] * pl["combined_decimal"] - 1, delta=0.01)

    def test_run_sample_shape(self):
        from app.sample_data import SampleProvider
        out = engine.run([SampleProvider()])
        self.assertEqual(out["mode"], "sample")
        self.assertLessEqual(len(out["top_singles"]), 30)
        self.assertLessEqual(len(out["parlays"]), 5)
        for s in out["top_singles"]:
            self.assertLessEqual(s["stake_pct"], 0.02)
            self.assertGreaterEqual(s["model_prob"], 0.25)


class Providers(unittest.TestCase):
    def test_espn_parse(self):
        data = {"events": [{"id": "1", "date": "2999-01-01T18:00Z", "status": {"type": {"state": "pre"}},
                            "competitions": [{"competitors": [
                                {"homeAway": "home", "team": {"displayName": "Home T"}}, {"homeAway": "away", "team": {"displayName": "Away T"}}],
                                "odds": [{"provider": {"name": "DraftKings"},
                                          "moneyline": {"home": {"close": {"odds": "-150"}}, "away": {"close": {"odds": "EVEN"}}},
                                          "pointSpread": {"home": {"close": {"line": "-3.5", "odds": "-110"}}, "away": {"close": {"line": "+3.5", "odds": "-110"}}},
                                          "total": {"over": {"close": {"line": "o44.5", "odds": "-110"}}, "under": {"close": {"line": "u44.5", "odds": "-110"}}}}]}]},
                           {"id": "2", "status": {"type": {"state": "post"}}, "competitions": [{}]}]}
        evs = parse_espn_scoreboard(data, "americanfootball_nfl")
        self.assertEqual(len(evs), 1)
        keys = [m["key"] for m in evs[0]["bookmakers"][0]["markets"]]
        self.assertEqual(keys, ["h2h", "spreads", "totals"])
        self.assertEqual(evs[0]["bookmakers"][0]["markets"][0]["outcomes"][1]["price"], 100)

    def test_fanduel_parse_synthetic_fixture(self):
        # SYNTHETIC fixture shaped like FanDuel's public layout; not captured from the live site.
        data = {"attachments": {"events": {"9": {"name": "Away T (@) Home T", "openDate": "2999-01-01T18:00:00.000Z"}},
                                "markets": {"m1": {"eventId": 9, "marketType": "MONEY_LINE", "marketStatus": "OPEN", "runners": [
                                    {"runnerName": "Away T", "runnerStatus": "ACTIVE", "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": 120}}},
                                    {"runnerName": "Home T", "runnerStatus": "ACTIVE", "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -140}}}]}}}}
        evs = parse_fanduel_page(data, "americanfootball_nfl")
        self.assertEqual(len(evs), 1)
        self.assertEqual(parse_fanduel_page({"weird": 1}, "americanfootball_nfl"), [])

    def test_blocked_source_recorded_not_bypassed(self):
        import urllib.error
        calls = []
        def blocked(url):
            calls.append(url)
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        evs, st = FanDuelProvider(None, fetch=blocked).fetch_events("americanfootball_nfl")
        self.assertEqual(evs, [])
        self.assertFalse(st["ok"])
        self.assertIn("403", st["detail"])
        self.assertEqual(len(calls), 1)  # no retries / workarounds

    def test_merge_adds_books(self):
        a = [ev([book("dk", -110, -110)])]
        b = [ev([book("fanduel", -105, -115)], eid="x")]
        m = merge_events([a, b])
        self.assertEqual(len(m), 1)
        self.assertEqual({x["key"] for x in m[0]["bookmakers"]}, {"dk", "fanduel"})


if __name__ == "__main__":
    unittest.main()
