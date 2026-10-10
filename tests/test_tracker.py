import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from nfl_edge import tracker

KICK = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()


def leg(label, game="g1", **kw):
    return dict({"game_id": game, "label": label, "type": "moneyline", "team": "HOU",
                 "odds": -150, "stake": 100, "win_prob": 0.6, "kickoff": KICK}, **kw)


class TrackerTest(unittest.TestCase):
    def test_generated_first_seen_wins_and_only_soon_games(self):
        ledger = {"placed": [], "generated": []}
        first = tracker.make_entry("single", [leg("HOU ML")], 0.6, "nfl", 2026)
        again = tracker.make_entry("single", [leg("HOU ML")], 0.7, "nfl", 2026)
        far = tracker.make_entry("single", [leg("X", kickoff=(datetime.now(timezone.utc)
                                                              + timedelta(days=30)).isoformat())],
                                 0.6, "nfl", 2026)
        self.assertEqual(tracker.record_generated(ledger, [first, again, far]), 1)
        self.assertEqual(ledger["generated"][0]["win_prob"], 0.6)

    def test_place_and_save_round_trip(self):
        ledger = {"placed": [], "generated": [], "saved": []}
        e = tracker.make_entry("parlay", [leg("A"), leg("B", game="g2")], 0.36, "nfl", 2026)
        tracker.place(ledger, e, 180, 25)
        tracker.place(ledger, e, 175, 20)  # placing again replaces it
        self.assertEqual(len(ledger["placed"]), 1)
        self.assertEqual(ledger["placed"][0]["odds"], 175)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "ledger.json"
            tracker.save(ledger, path)
            self.assertEqual(tracker.load(path), ledger)
            self.assertEqual(tracker.load(Path(d) / "missing.json"), {"placed": [], "generated": [], "saved": []})

    def test_combine_and_profit(self):
        self.assertEqual(tracker.combine(["won", "lost", "pending"]), "lost")
        self.assertEqual(tracker.combine(["won", "pending"]), "pending")
        self.assertEqual(tracker.combine(["won", "push"]), "won")
        self.assertEqual(tracker.combine(["push"]), "push")
        e = tracker.make_entry("single", [leg("A")], 0.6, "nfl", 2026, odds=150, stake=10)
        e["status"] = "won"
        self.assertAlmostEqual(tracker.profit(e), 15.0)
        e["status"] = "lost"
        self.assertEqual(tracker.profit(e), -10)

    def test_regrade(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        ledger = {"placed": [tracker.make_entry("single", [leg("HOU ML", kickoff=past)], 0.6,
                                                "nfl", 2026)],
                  "generated": [tracker.make_entry("parlay", [leg("HOU ML", kickoff=past),
                                                              leg("TEN ML", game="g2", team="TEN",
                                                                  kickoff=past)], 0.3, "nfl", 2026)]}
        games = [{"game_id": "g1", "away_team": "HOU", "home_team": "TEN",
                  "away_score": "24", "home_score": "17"},
                 {"game_id": "g2", "away_team": "TEN", "home_team": "IND",
                  "away_score": "", "home_score": ""}]
        with mock.patch("nfl_edge.data.games", return_value=games):
            self.assertEqual(tracker.regrade(ledger), 2)
        self.assertEqual(ledger["placed"][0]["status"], "won")
        self.assertEqual(ledger["generated"][0]["status"], "pending")
        self.assertTrue(tracker.summary(ledger["placed"], money=True).startswith("Record 1-0"))

    def test_history_and_saved_files(self):
        placed = tracker.make_entry("single", [leg("A")], 0.6, "nfl", 2026)
        other = tracker.make_entry("single", [leg("B")], 0.7, "nfl", 2026)
        ledger = {"placed": [placed], "generated": [placed, other], "saved": []}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "paper.json"
            path.write_text('{"name": "Paper", "kind": "singles", "season": 2026, "legs": ['
                            '{"game_id": "g9", "label": "C", "type": "moneyline", "team": "HOU",'
                            ' "odds": -150, "stake": 100, "win_prob": 0.6}]}')
            with mock.patch("nfl_edge.tracker.kickoffs", return_value={"g9": (KICK, "X @ Y")}):
                self.assertEqual(tracker.import_saved(ledger, [path]), 1)
                self.assertEqual(tracker.import_saved(ledger, [path]), 0)  # no duplicates
        self.assertEqual([(src, e["legs"][0]["label"]) for src, e in tracker.history(ledger)],
                         [("placed", "A"), ("not placed", "B"), ("saved", "C")])
        self.assertEqual(ledger["saved"][0]["legs"][0]["game"], "X @ Y")

    def test_annotate_weeks(self):
        a = tracker.make_entry("single", [leg("A")], 0.6, "nfl", 2026)
        b = tracker.make_entry("single", [leg("B", game="gx")], 0.6, "nfl", 2026)
        c = dict(tracker.make_entry("single", [leg("C")], 0.6, "nfl", 2026), week=3)
        ledger = {"placed": [a], "generated": [b, c], "saved": []}
        with mock.patch("nfl_edge.tracker.game_weeks", return_value={"g1": 5}):
            tracker.annotate_weeks(ledger)
        self.assertEqual(a["week"], 5)
        self.assertNotIn("week", b)  # unknown game: left alone
        self.assertEqual(c["week"], 3)  # already known: kept


if __name__ == "__main__":
    unittest.main()
