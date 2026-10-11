import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nfl_edge import botcore, tracker
from nfl_edge.board import make_bet

KICK = datetime.now(timezone.utc) + timedelta(days=2)
GAME = "Baltimore Ravens @ Atlanta Falcons"
BETS = [make_bet(GAME, KICK, "alternate_spreads", "Baltimore Ravens", 11.5, 0.80, "g1", "x", week=5),
        make_bet(GAME, KICK, "alternate_spreads", "Baltimore Ravens", 3.5, 0.62, "g1", "x", week=5),
        make_bet(GAME, KICK, "team_totals", "Baltimore Ravens Over", 11.5, 0.82, "g1", "x", week=5),
        make_bet(GAME, KICK, "h2h", "Atlanta Falcons", None, 0.45, "g1", "x", week=5),
        make_bet(GAME, KICK, "player_reception_yds_alternate", "Drake London Over", 39.5, 0.79,
                 "g1", "x", week=5)]


def entry(label, kind="single", prob=0.8):
    leg = {"game_id": "g1", "label": label, "pick": label, "type": "moneyline", "team": "ATL",
           "odds": -400, "stake": 100, "win_prob": prob, "kickoff": KICK.isoformat()}
    e = tracker.make_entry(kind, [leg], prob, "nfl", 2026)
    e["week"] = 5
    return e


class SearchTest(unittest.TestCase):
    def names(self, query):
        return [botcore._describe(b) for b in botcore.search(BETS, query)]

    def test_line_must_match(self):
        self.assertEqual(self.names("ravens +11.5"), ["Baltimore Ravens +11.5"])
        self.assertEqual(self.names("ravens +3.5"), ["Baltimore Ravens +3.5"])
        self.assertEqual(self.names("ravens +21.5"), [])  # no such line

    def test_words_and_synonyms(self):
        self.assertEqual(self.names("falcons moneyline")[0], "Atlanta Falcons ML")
        self.assertEqual(self.names("london 40 yards")[0], "Drake London 40+ Receiving Yds")
        self.assertEqual(self.names("ravens team total over 11.5")[0],
                         "Baltimore Ravens Team Total Over 11.5")
        self.assertEqual(self.names(""), [])

    def test_parse_odds(self):
        self.assertEqual(botcore.parse_odds("-150"), -150)
        self.assertEqual(botcore.parse_odds("+240"), 240)
        self.assertIsNone(botcore.parse_odds(" "))
        with self.assertRaises(ValueError):
            botcore.parse_odds("50")

    def test_chance_text(self):
        text = botcore.chance_text(botcore.search(BETS, "ravens +11.5"), -300, 10)
        self.assertIn("80%", text)
        self.assertIn("worth it", text)  # 0.8 * 1.333 = 1.067 -> +6.7%
        self.assertIn("+6.7%", text)
        self.assertIn("Couldn't find", botcore.chance_text([], None, 10))


class LedgerTest(unittest.TestCase):
    def test_place_two_members_history_and_remove(self):
        with tempfile.TemporaryDirectory() as d:
            led = botcore.ServerLedger(Path(d) / "l.json")
            led.record_shown([entry("Falcons ML"), entry("Ravens +11.5")])
            e = entry("Ravens +11.5")
            led.place(e, -350, 20, 1, "Levi")
            led.place(e, None, 10, 2, "Sam")  # same bet, another member: both kept
            ledger = led.load()
            self.assertEqual(len(ledger["placed"]), 2)
            text = botcore.history_text(ledger)
            self.assertIn("**Levi**", text)
            self.assertIn("**Sam**", text)
            self.assertIn("Week 5", text)
            # the placed pick no longer counts among picks you didn't place
            self.assertIn("Builder's picks you didn't place: No results yet · 1 pending", text)
            self.assertIsNone(led.remove(3, "Ravens"))  # someone with no bets
            self.assertEqual(botcore.entry_title(led.remove(1, "ravens")), "Ravens +11.5")
            self.assertEqual([p["by"] for p in led.load()["placed"]], ["Sam"])

    def test_settled_text(self):
        won = dict(entry("Ravens +11.5"), status="won", by="Levi", stake=10, odds=-400)
        pick = dict(entry("Falcons ML"), status="lost")
        text = botcore.settled_text([won, pick])
        self.assertIn("✅ WON", text)
        self.assertIn("+$2.50", text)
        self.assertIn("0 won, 1 lost", text)
        self.assertIsNone(botcore.settled_text([]))


class ChunksTest(unittest.TestCase):
    def test_chunks_fit_discord(self):
        text = "\n".join(f"line {i} " + "x" * 50 for i in range(200)) + "\n" + "y" * 5000
        parts = botcore.chunks(text)
        self.assertTrue(all(len(p) <= botcore.MAX_MESSAGE for p in parts))
        self.assertEqual("".join(p.replace("\n", "") for p in parts),
                         text.replace("\n", ""))


if __name__ == "__main__":
    unittest.main()
