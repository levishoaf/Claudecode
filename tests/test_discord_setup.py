import tempfile
import unittest
from pathlib import Path
from unittest import mock

import discord_bot as bot
from nfl_edge import updater


class TokenTest(unittest.TestCase):
    def test_pasted_tokens_are_cleaned(self):
        self.assertEqual(bot.clean_token('  "Bot abc.def.ghi" \n'), "abc.def.ghi")
        self.assertEqual(bot.clean_token("abc.def"), "abc.def")

    def test_invite_link_asks_for_commands_and_messages(self):
        link = bot.invite_link(42)
        self.assertIn("client_id=42", link)
        self.assertIn("scope=bot%20applications.commands", link)
        self.assertIn(f"permissions={bot.PERMISSIONS}", link)
        self.assertEqual(bot.PERMISSIONS, 1024 + 2048 + 16384 + 65536)


class StartupTest(unittest.TestCase):
    def test_startup_file_starts_the_exe_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(bot.sys, "platform", "win32"), \
                mock.patch.dict(bot.os.environ, {"APPDATA": d}), \
                mock.patch.object(bot.sys, "frozen", True, create=True), \
                mock.patch.object(bot.sys, "executable", str(Path(d) / "Bot.exe")):
            print_ = bot.set_startup(True)
            path = bot.startup_file()
            self.assertTrue(path.exists(), print_)
            text = path.read_text()
            self.assertIn("Bot.exe", text)
            self.assertIn("/min", text)
            bot.set_startup(False)
            self.assertFalse(path.exists())

    def test_asked_only_once(self):
        with mock.patch.object(bot, "startup_file", return_value=Path("x")), \
                mock.patch.object(bot, "interactive", return_value=True), \
                mock.patch.object(bot.sys, "frozen", True, create=True), \
                mock.patch("builtins.input", return_value="n") as ask, \
                mock.patch.object(bot, "set_startup", return_value="ok"), \
                mock.patch.object(bot, "save_settings"):
            settings = {}
            bot.offer_startup(settings)
            bot.offer_startup(settings)
        self.assertEqual(ask.call_count, 1)


class UpdateTest(unittest.TestCase):
    def test_bot_updates_from_its_own_release_file(self):
        found = updater.Update(99, "u", 1)
        with mock.patch.object(updater, "available", return_value=found) as avail, \
                mock.patch.object(updater, "install") as install:
            self.assertTrue(bot.update_now())
        self.assertEqual(avail.call_args.kwargs["assets"], updater.BOT_ASSETS)
        self.assertTrue(install.call_args.kwargs["console"])

    def test_no_update_or_offline_keeps_running(self):
        with mock.patch.object(updater, "available", return_value=None):
            self.assertFalse(bot.update_now())
        with mock.patch.object(updater, "available", side_effect=updater.UpdateError("x")):
            self.assertFalse(bot.update_now())

    def test_bot_release_file_is_published(self):
        workflow = (Path(bot.__file__).parent / ".github" / "workflows" /
                    "build-executables.yml").read_text()
        self.assertIn(updater.BOT_ASSETS["win32"], workflow)


class LoginTest(unittest.TestCase):
    def test_wrong_token_is_forgotten(self):
        class LoginFailure(Exception):
            pass
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / "discord token.txt"
            token.write_text("bad\n")
            with mock.patch.object(bot, "TOKEN_FILE", token), \
                    mock.patch.object(bot, "update_now", return_value=False), \
                    mock.patch.object(bot, "interactive", return_value=False), \
                    mock.patch.object(bot, "run_bot", side_effect=LoginFailure("no")), \
                    mock.patch.object(bot.sys, "argv", ["discord_bot.py"]):
                self.assertEqual(bot.main(), 1)
            self.assertFalse(token.exists())


if __name__ == "__main__":
    unittest.main()
