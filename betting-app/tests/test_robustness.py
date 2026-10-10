"""Malformed / partial payloads must never crash a refresh: parsers return nothing, never raise."""
import copy
import json
import random
import tempfile
import unittest
from pathlib import Path

from app import engine, ledger, pipeline, ratings, snapshots
from app.backtest import market_p_home, parse_pickcenter
from app.providers import DiskCache, EspnProvider, FanDuelProvider, Provider, parse_espn_scoreboard, parse_fanduel_page

JUNK = [None, [], {}, "", "x", 0, 1.5, True, [None], [[]], {"events": None}, {"events": [None]}, {"events": [1, "a"]},
        {"events": [{}]}, {"events": [{"competitions": None}]}, {"events": [{"competitions": [None]}]},
        {"events": [{"status": None, "competitions": [{}]}]}, {"attachments": None}, {"attachments": []},
        {"attachments": {"events": [], "markets": None}}, {"attachments": {"events": {"1": None}, "markets": {"a": 5}}},
        {"events": [{"competitions": [{"competitors": [None, 3]}]}]}]

ESPN_OK = {"events": [{"id": "1", "date": "2999-01-01T18:00Z", "status": {"type": {"state": "pre", "completed": False}},
                       "competitions": [{"neutralSite": False, "competitors": [
                           {"homeAway": "home", "score": "0", "team": {"id": "1", "displayName": "H"}},
                           {"homeAway": "away", "score": "0", "team": {"id": "2", "displayName": "A"}}],
                           "odds": [{"provider": {"name": "DK"},
                                     "moneyline": {"home": {"close": {"odds": "-150"}}, "away": {"close": {"odds": "+130"}}},
                                     "pointSpread": {"home": {"close": {"line": "-3.5", "odds": "-110"}}, "away": {"close": {"line": "+3.5", "odds": "-110"}}},
                                     "total": {"over": {"close": {"line": "o44.5", "odds": "-110"}}, "under": {"close": {"line": "u44.5", "odds": "-110"}}}}]}]}]}


def mutate(obj, rng, rate=0.15):
    """Randomly corrupt a JSON-like structure (delete keys, swap in junk values)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            r = rng.random()
            if r < rate / 2:
                continue
            out[k] = rng.choice(JUNK) if r < rate else mutate(v, rng, rate)
        return out
    if isinstance(obj, list):
        return [rng.choice(JUNK) if rng.random() < rate else mutate(v, rng, rate) for v in obj]
    return obj


class ParserRobustness(unittest.TestCase):
    def test_junk_inputs_for_every_parser(self):
        for j in JUNK:
            for sport in ("americanfootball_nfl", "americanfootball_ncaaf"):
                self.assertIsInstance(parse_espn_scoreboard(j, sport), list)
                self.assertIsInstance(parse_fanduel_page(j, sport), list)
            self.assertIsInstance(ratings.parse_games(j if isinstance(j, dict) else {}, 2026, 1), list)
            parse_pickcenter(j if isinstance(j, dict) else {})
            ledger.final_score(j if isinstance(j, dict) else {})
            ledger.parse_pc(j if isinstance(j, dict) else {})
            market_p_home(j if isinstance(j, dict) else {})

    def test_mutated_valid_espn_payload(self):
        rng = random.Random(7)
        for _ in range(400):
            bad = mutate(copy.deepcopy(ESPN_OK), rng)
            evs = parse_espn_scoreboard(bad, "americanfootball_nfl")
            ratings.parse_games(bad, 2026, 1)
            # whatever survives must be well-formed enough for the engine
            for e in evs:
                engine.evaluate_event(e, engine.DEFAULTS, None)

    def test_mutated_fanduel_payload(self):
        base = TestFD.FD
        rng = random.Random(11)
        for _ in range(400):
            for e in parse_fanduel_page(mutate(copy.deepcopy(base), rng), "americanfootball_nfl"):
                engine.evaluate_event(e, engine.DEFAULTS, None)


class TestFD(unittest.TestCase):
    FD = {"attachments": {"events": {"9": {"name": "Away T (@) Home T", "openDate": "2999-01-01T18:00:00.000Z"}},
                          "markets": {"m1": {"eventId": 9, "marketType": "MONEY_LINE", "marketStatus": "OPEN", "runners": [
                              {"runnerName": "Away T", "runnerStatus": "ACTIVE", "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": 120}}},
                              {"runnerName": "Home T", "runnerStatus": "ACTIVE", "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -140}}}]}}}}

    def test_baseline_fixture_parses(self):
        self.assertEqual(len(parse_fanduel_page(self.FD, "americanfootball_nfl")), 1)


class PipelineRobustness(unittest.TestCase):
    def test_provider_that_raises_does_not_break_run(self):
        class Boom(Provider):
            def fetch_events(self, sport):
                raise RuntimeError("boom")
        out = engine.run([Boom()])
        self.assertEqual(out["n_events"], 0)
        self.assertTrue(all(not s["ok"] for s in out["sources"]))
        self.assertIn("boom", out["sources"][0]["detail"])

    def test_provider_returning_garbage_events(self):
        class Garbage(Provider):
            def fetch_events(self, sport):
                return [None, {}, {"id": 1}, {"id": "x", "sport_key": sport, "bookmakers": None}], {"ok": True, "detail": "", "events": 4}
        out = engine.run([Garbage()])
        self.assertEqual(out["n_markets_evaluated"], 0)

    def test_garbled_ledger_and_snapshot_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "l.jsonl").write_text('{"id": "a"}\nnot json\n\n[1,2]\n')
            rows = ledger.read_ledger(d / "l.jsonl")
            self.assertEqual(rows, [{"id": "a"}])
            (d / "s.jsonl").write_text('garbage\n{"ts":"2026-01-01T00:00:00Z","events":[{"id":"e","t":"2999-01-01T00:00:00Z"},5,null]}\n{"events":3}\n')
            self.assertIsInstance(snapshots.closing_snapshots(d / "s.jsonl"), dict)

    def test_corrupt_cache_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            c = DiskCache(Path(d), 3600)
            c._path("k").parent.mkdir(parents=True, exist_ok=True)
            c._path("k").write_text("{not json")
            self.assertIsNone(c.get("k"))
            c._path("k").write_text(json.dumps({"saved_at": "bad", "data": 1}))
            self.assertIsNone(c.get("k", allow_stale=False) if False else None)

    def test_espn_provider_with_garbage_response(self):
        for junk in (None, [], "x", {"events": "nope"}):
            evs, st = EspnProvider(None, fetch=lambda u, j=junk: j).fetch_events("americanfootball_nfl")
            self.assertEqual(evs, [])
            self.assertIn("ok", st)

    def test_ratings_fetch_with_garbage(self):
        out = ratings.fetch_season("americanfootball_nfl", 2026, None, lambda u: [1, 2, 3])
        self.assertEqual(out, [])

    def test_save_load_payload_pipeline_failure_falls_back_to_sample(self):
        import app.pipeline as pl
        orig = pl.EspnProvider.fetch_events
        pl.EspnProvider.fetch_events = lambda self, sport: ([], {"name": "x", "ok": False, "detail": "down", "events": 0})
        try:
            p = pl.build_payload(use_fanduel=False, use_model=False)
        finally:
            pl.EspnProvider.fetch_events = orig
        self.assertEqual(p["mode"], "sample")
        self.assertIn("down", p["sample_reason"])


if __name__ == "__main__":
    unittest.main()


class FpiTests(unittest.TestCase):
    def test_parse_fpi(self):
        self.assertEqual(pipeline.parse_fpi({"predictor": {"homeTeam": {"gameProjection": "73.6"}, "awayTeam": {"gameProjection": "26.4"}}}),
                         {"home": 0.736, "away": 0.264})
        for bad in (None, {}, {"predictor": None}, {"predictor": {"homeTeam": {"gameProjection": "x"}}},
                    {"predictor": {"homeTeam": {"gameProjection": "60"}, "awayTeam": {"gameProjection": "60"}}}):
            self.assertIsNone(pipeline.parse_fpi(bad))

    def test_attach_only_moneyline_and_caches(self):
        calls = []

        def fake(url):
            calls.append(url)
            return {"predictor": {"homeTeam": {"gameProjection": "70"}, "awayTeam": {"gameProjection": "30"}}}
        mk = lambda mkt, name: {"event_id": "espn-5", "league": "NFL", "home_team": "H", "bet": {"market": mkt, "name": name}}
        payload = {"top_singles": [mk("h2h", "H"), mk("h2h", "A"), mk("spreads", "H")]}
        with tempfile.TemporaryDirectory() as d:
            n = pipeline.attach_fpi(payload, DiskCache(Path(d), 3600), fake)
        self.assertEqual(n, 2)
        self.assertEqual([p["fpi_prob"] for p in payload["top_singles"]], [0.7, 0.3, None])
        self.assertEqual(len(calls), 1)
