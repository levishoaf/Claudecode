import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import discord_bot
from nfl_edge import fdfeed
from nfl_edge.board import make_bet

KICK = datetime.now(timezone.utc) + timedelta(days=2)
GAME = "Baltimore Ravens @ Atlanta Falcons"


def bets():
    return [make_bet(GAME, KICK, "h2h", "Atlanta Falcons", None, 0.62, "2026_05_BAL_ATL", "x", week=5),
            make_bet(GAME, KICK, "h2h", "Baltimore Ravens", None, 0.38, "2026_05_BAL_ATL", "x", week=5)]


def only_falcons(found, state, sport="nfl", opener=None):
    kept = [b for b in found if b.pick == "Atlanta Falcons"]
    for b in kept:
        b.fd_price, b.on_fanduel, b.priced, b.ev = -150, True, True, 0.62 * (1 + 100 / 150) - 1
    return kept, f"Checked against FanDuel ({state.upper()}): {len(kept)} bets confirmed"


class BotFanDuelCheckTest(unittest.TestCase):
    def core(self, state):
        self.dir = tempfile.TemporaryDirectory()
        core = discord_bot.Core(Path(self.dir.name) / "l.json", state=lambda: state)
        core.boards.get = lambda *a, **k: bets()
        return core

    def tearDown(self):
        self.dir.cleanup()

    def test_bets_only_confirmed_with_fanduel_odds(self):
        with mock.patch("nfl_edge.fdfeed.verify", side_effect=only_falcons):
            text = self.core("nj").bets("nfl", 10, 1, 99, None, 10.0)
        self.assertIn("Atlanta Falcons", text)
        self.assertNotIn("Baltimore Ravens ML", text)
        self.assertIn("FanDuel -150", text)
        self.assertIn("found on FanDuel", text)

    def test_failed_check_falls_back_to_sure_bets(self):
        core = self.core("in")
        asked = []
        core.boards.get = lambda *a, **k: asked.append(k.get("sure_only")) or bets()
        with mock.patch("nfl_edge.fdfeed.verify", side_effect=fdfeed.FeedError("blocked")):
            text = core.bets("nfl", 10, 1, 99, None, 10.0)
        self.assertEqual(asked, [False, True])  # every market first, then only sure bets
        self.assertIn("Couldn't check against FanDuel (blocked)", text)
        self.assertIn("only bets sure to be on FanDuel", text)
        self.assertIn("Falcons", text)
        self.assertNotIn("FanDuel -150", text)  # break-even odds, not FanDuel's

    def test_place_refuses_bets_not_on_fanduel(self):
        core = self.core("nj")
        with mock.patch("nfl_edge.fdfeed.verify", side_effect=only_falcons):
            self.assertIn("isn't on FanDuel", core.place(1, "nfl", "ravens ml", None, 10, 1, "Levi"))
            self.assertIn("Logged for **Levi**", core.place(1, "nfl", "falcons ml", None, 10, 1, "Levi"))
            self.assertIn("isn't on FanDuel", core.chance("nfl", "ravens ml", None, 10))
        placed = core.ledger.load()["placed"]
        self.assertEqual([(p["legs"][0]["pick"], p["odds"]) for p in placed],
                         [("Atlanta Falcons ML", -150)])  # FanDuel's price when none typed

    def test_without_a_state_nothing_is_checked(self):
        with mock.patch("nfl_edge.fdfeed.verify") as verify:
            text = self.core(None).bets("nfl", 10, 1, 99, None, 10.0)
        verify.assert_not_called()
        self.assertIn("Confirm each bet on FanDuel", text)


if __name__ == "__main__":
    unittest.main()


class DefaultStateTest(unittest.TestCase):
    def test_indiana_unless_changed_or_off(self):
        import bets as launcher

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(launcher, "STATE_FILE", Path(d) / "fanduel state.txt"):
            self.assertEqual(launcher.fanduel_state(), "in")
            launcher.save_fanduel_state("NJ")
            self.assertEqual(launcher.fanduel_state(), "nj")
            launcher.save_fanduel_state("off")
            self.assertIsNone(launcher.fanduel_state())
