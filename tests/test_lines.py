import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nfl_edge import lines

FIX = Path(__file__).parent / "fixtures" / "odds"
MAIN = json.loads((FIX / "main.json").read_text())  # Seahawks @ Broncos, Elon @ Stanford
PROPS = json.loads((FIX / "props-nfl.json").read_text())  # Seahawks @ Broncos props
BEFORE = datetime(2026, 10, 15, tzinfo=timezone.utc)


class PricingTest(unittest.TestCase):
    def test_game_lines_against_every_book(self):
        offers = lines.price(MAIN[0], "nfl")
        self.assertEqual({o.market for o in offers}, {"h2h", "spreads", "totals"})
        for o in offers:
            self.assertGreater(o.books, 10)  # dozens of books price an NFL game
            self.assertTrue(o.sharp)  # Pinnacle among them
            self.assertGreaterEqual(o.best, o.fanduel)
        ml = [o for o in offers if o.market == "h2h"]
        self.assertAlmostEqual(sum(o.chance for o in ml), 1.0, places=6)
        spreads = [o for o in offers if o.market == "spreads"]
        self.assertTrue(all(abs(o.point) == abs(spreads[0].point) for o in spreads[:2]))

    def test_junk_prices_are_ignored(self):
        # Elon @ Stanford: FanDuel +3000/-10000; an exchange posted -10000 on both sides.
        self.assertEqual([o for o in lines.price(MAIN[1], "ncaaf") if o.market == "h2h"], [])

    def test_props_pair_over_and_under_per_player(self):
        offers = lines.price(PROPS, "nfl")
        nix = [o for o in offers if o.subject == "Bo Nix" and o.market == "player_pass_yds"]
        self.assertEqual({o.side for o in nix}, {"Over", "Under"})
        self.assertAlmostEqual(sum(o.chance for o in nix), 1.0, places=6)
        self.assertFalse(any(o.rough for o in nix))
        td = [o for o in offers if o.market == "player_anytime_td"]
        self.assertTrue(td)
        self.assertTrue(all(o.side == "Yes" and o.rough for o in td))  # "Yes"-only market
        self.assertIn("Anytime TD", td[0].label)
        self.assertIn("Passing Yds", nix[0].label)

    def test_far_from_market_is_flagged(self):
        event = {"id": "x", "commence_time": "2026-10-20T00:00:00Z", "home_team": "H",
                 "away_team": "A", "bookmakers": [
                     {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [
                         {"name": "H", "price": 150}, {"name": "A", "price": -180}]}]},
                     {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                         {"name": "H", "price": -200}, {"name": "A", "price": 180}]}]}]}
        h = next(o for o in lines.price(event, "nfl") if o.side == "H")
        self.assertTrue(h.check)  # FanDuel +150 vs the sharp book's -200
        self.assertGreater(h.ev, 0.3)


class FeedTest(unittest.TestCase):
    def feed(self, folder, credits=400):
        calls = []

        def get(path, params):
            calls.append(path)
            body = PROPS if "/events/" in path else MAIN
            return body, credits - len(calls)
        return lines.Feed(key="k", folder=folder, get=get), calls

    def test_pulls_are_cached_until_due(self):
        with tempfile.TemporaryDirectory() as d:
            feed, calls = self.feed(d)
            first = lines.board("nfl", feed, now=BEFORE)
            n = len(calls)
            second = lines.board("nfl", feed, now=BEFORE)
            self.assertEqual(len(calls), n)  # nothing due: no credits spent
            self.assertEqual(len(first), len(second))
            self.assertTrue(any(o.market.startswith("player_") for o in first))
            again, calls2 = self.feed(d)  # a restart reads the saved pulls and state
            lines.board("nfl", again, now=BEFORE)
            self.assertEqual(calls2, [])

    def test_stops_before_credits_run_out(self):
        with tempfile.TemporaryDirectory() as d:
            feed, calls = self.feed(d, credits=lines.MIN_CREDITS + 1)
            feed.main("nfl")  # leaves MIN_CREDITS
            feed.pulled.clear()
            feed.main("ncaaf")
            self.assertEqual(len(calls), 1)

    def test_no_key_means_no_pulls(self):
        with tempfile.TemporaryDirectory() as d:
            feed = lines.Feed(key=None, folder=d)
            feed.key = None
            self.assertFalse(feed.enabled)
            self.assertEqual(feed.main("nfl"), [])

    def test_failed_pull_keeps_the_last_good_one(self):
        with tempfile.TemporaryDirectory() as d:
            feed, _ = self.feed(d)
            feed.main("nfl")
            feed.pulled.clear()

            def broken(path, params):
                raise lines.OddsError("down")
            feed.get = broken
            self.assertEqual(len(feed.main("nfl")), 2)

    def test_props_only_for_soon_games_on_fanduel(self):
        with tempfile.TemporaryDirectory() as d:
            feed, calls = self.feed(d)
            events = feed.main("nfl")
            later = BEFORE + timedelta(days=30)
            self.assertEqual(feed.props("nfl", events, now=later), [])
            self.assertEqual(feed.props("ufc", events, now=BEFORE), [])  # no UFC props


if __name__ == "__main__":
    unittest.main()
