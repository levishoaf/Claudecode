import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from nfl_edge import edges, tracker
from nfl_edge.board import make_bet

KICK = datetime.now(timezone.utc) + timedelta(days=1)


def priced(i, p, price, market="h2h", pick=None, game=None, **kw):
    """A bet repriced at FanDuel: market chance p at FanDuel's price."""
    b = make_bet(game or f"Away{i} @ Home{i}", KICK, market, pick or f"Home{i}", None, p,
                 f"g{i}", "x")
    b.fd_price, b.on_fanduel, b.books_count, b.market_prob = price, True, 8, p
    b.ev = p * (1 + (price / 100 if price > 0 else 100 / -price)) - 1
    for k, v in kw.items():
        setattr(b, k, v)
    return b


class EdgeRuleTest(unittest.TestCase):
    def test_only_clear_edges_that_win_more_often_than_not(self):
        self.assertTrue(edges.is_edge(priced(1, 0.55, -110)))   # +5.0%
        self.assertFalse(edges.is_edge(priced(2, 0.535, -110)))  # +2.1%: thin
        self.assertFalse(edges.is_edge(priced(3, 0.45, +140)))  # +8% but loses more often
        self.assertFalse(edges.is_edge(priced(4, 0.55, -110, check=True)))
        no_price = make_bet("A @ B", KICK, "h2h", "B", None, 0.8, "g", "x")
        self.assertFalse(edges.is_edge(no_price))

    def test_ranked_by_value_then_chance(self):
        a, b, c = priced(1, 0.55, -110), priced(2, 0.60, -110), priced(3, 0.58, -105)
        self.assertEqual([x.pick for x in edges.rank([a, b, c])], ["Home2", "Home3", "Home1"])

    def test_worst_odds_keep_the_edge(self):
        self.assertEqual(edges.worst_odds(0.55), -114)

    def test_market_chance_is_never_moved(self):
        b = priced(1, 0.55, -110)
        b.fair_prob, b.ev = 0.62, 0.18  # as if a model nudged it
        offers = [object()]
        with mock.patch.object(edges, "full_board", return_value=[b]), \
                mock.patch("nfl_edge.realodds.apply", side_effect=lambda bets, s, o: bets):
            out = edges.league_edges("nba", offers=offers)
        self.assertEqual(out[0].fair_prob, 0.55)
        self.assertAlmostEqual(out[0].ev, 0.05, places=3)

    def test_parlays_only_from_edges_in_different_games(self):
        legs = [priced(1, 0.56, -110), priced(2, 0.57, -110), priced(3, 0.55, -105),
                priced(4, 0.6, -110, game="Away1 @ Home1", pick="Away1")]
        found = edges.edge_parlays(legs, 10)
        self.assertTrue(found)
        for p in found:
            self.assertEqual(len({b.game for b in p.legs}), len(p.legs))
            self.assertGreaterEqual(p.ev, edges.EDGE_MIN_EV)
            self.assertLessEqual(len(p.legs), 3)
        probs = [p.win_prob for p in found]
        self.assertEqual(probs, sorted(probs, reverse=True))


class EdgeRecordTest(unittest.TestCase):
    def test_record_counts_edges_at_fanduel_odds(self):
        legs = [{"game_id": "g", "odds": 120, "kickoff": KICK.isoformat(), "type": "moneyline",
                 "team": "X", "label": "X ML", "win_prob": 0.55}]
        ledger = {"generated": []}
        e = tracker.make_entry("single", legs, 0.55, "nba", 2026)
        tracker.record_generated(ledger, [e], edge=True)
        got = ledger["generated"][0]
        self.assertTrue(got["edge"])
        self.assertEqual(got["odds"], 120)
        got["status"] = "won"
        r = tracker.edge_record(ledger["generated"])
        self.assertEqual((r["won"], r["lost"], r["profit"]), (1, 0, 12.0))
        self.assertFalse(tracker.model_line(dict(got, legs=[dict(legs[0], fd_line=True,
                                                                 type="spread")])))



class BudgetTest(unittest.TestCase):
    def test_a_day_spends_only_its_share(self):
        import tempfile

        from nfl_edge import lines

        with tempfile.TemporaryDirectory() as d:
            feed = lines.Feed(key="k", folder=d, get=lambda path, params: ([], None))
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            feed.credits = 150
            feed.budget(now)
            first = feed.allowance
            feed.credits, feed.spent = 130, {today: 20}  # spent some today
            feed.budget(now)
            self.assertAlmostEqual(feed.allowance, first)  # the day's share stays put
            self.assertFalse(feed.can_spend(first))


if __name__ == "__main__":
    unittest.main()
