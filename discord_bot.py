#!/usr/bin/env python3
"""Bet Builder by Levi Shoaf, as a Discord bot. Runs on your computer.

    python3 discord_bot.py              # asks for the bot token the first time
    python3 discord_bot.py --selftest   # checks everything except the Discord connection

Slash commands: /bets, /parlays, /chance, /place, /remove, /history,
/injuries and /results-here. The whole server shares one bet history,
saved in "discord bets.json" next to this program. Placed bets and the
picks the bot showed are graded after the games, and results are posted in
the channel chosen with /results-here.

The token is read from the DISCORD_BOT_TOKEN environment variable or from
"discord token.txt" next to this program. Keep it secret.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import bets as launcher  # sets up paths and certificates, like the app
from nfl_edge import botcore, data, fdfeed

ROOT = launcher.ROOT
LEDGER = ROOT / "discord bets.json"
SETTINGS = ROOT / "discord bot settings.json"
TOKEN_FILE = ROOT / "discord token.txt"
CHECK_MINUTES = 10  # how often finished games are graded

SPORTS = {"nfl": "NFL", "ncaaf": "College football"}


def read_token() -> str | None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token and TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
    if not token and sys.stdin and sys.stdin.isatty():
        print("Paste your Discord bot token (from discord.com/developers → your app → Bot):")
        token = input("> ").strip()
        if token:
            TOKEN_FILE.write_text(token + "\n")
            print(f"Saved to {TOKEN_FILE}. Keep that file private.")
    return token or None


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    SETTINGS.write_text(json.dumps(settings, indent=1) + "\n")


class Core:
    """Everything the commands do, without Discord (so --selftest can run it)."""

    def __init__(self, ledger_path: Path, state=lambda: None):
        self.state = state  # the FanDuel state to check bets against, or None
        self.boards = botcore.Boards()
        self.ledger = botcore.ServerLedger(ledger_path)
        self.last_parlays: dict[int, tuple[str, list]] = {}  # channel -> (sport, parlays)

    def _record(self, sport, week, singles, parlays) -> None:
        if week is None:  # only this week's picks count as picks the builder made
            self.ledger.record_shown(launcher.tracker_entries(singles, parlays, sport,
                                                              botcore.season_now()))

    def _checked(self, sport, bets):
        """(bets, note): with a FanDuel state set, only the bets found on FanDuel."""
        state = self.state()
        if not state:
            return list(bets), None
        return fdfeed.verify(list(bets), state, sport)

    def bets(self, sport, count, lo, hi, week, wager) -> str:
        try:
            pool, note = self._checked(sport, self.boards.get(sport, week, lo / 100, hi / 100,
                                                              sure_only=not self.state()))
        except fdfeed.FeedError as e:
            return f"Couldn't check against FanDuel ({e}), so no bets are shown. Try again soon."
        singles = botcore.pick_singles(pool, count, rank_by="ev" if note else "prob")
        self._record(sport, week, singles, [])
        return botcore.singles_text(singles, sport, wager) + (f"\n_{note}_" if note else "")

    def parlays(self, channel, sport, count, min_legs, max_legs, lo, hi, week, wager) -> list[str]:
        try:
            pool, note = self._checked(sport, self.boards.get(sport, week, lo / 100, hi / 100,
                                                              sure_only=not self.state()))
        except fdfeed.FeedError as e:
            return [f"Couldn't check against FanDuel ({e}), so no parlays are shown."]
        parlays = botcore.pick_parlays(pool, count, min_legs, max_legs)
        self.last_parlays[channel] = (sport, parlays)
        self._record(sport, week, [], parlays)
        return botcore.parlays_text(parlays, sport, wager)

    def find(self, sport, query):
        return botcore.search(self.boards.get(sport, None, *botcore.SEARCH_RANGE, every_line=True),
                              query)

    def chance(self, sport, query, odds_text, wager) -> str:
        try:
            odds = botcore.parse_odds(odds_text)
        except ValueError:
            return "Odds look like -150 or +240."
        matches = self.find(sport, query)
        if self.state() and matches:
            try:
                found, _ = self._checked(sport, matches)
            except fdfeed.FeedError as e:
                return f"Couldn't check against FanDuel ({e})."
            if not found:
                return (f"**{botcore._describe(matches[0])}** isn't on FanDuel at that line right "
                        "now, so it can't be placed there.")
            matches = found
            odds = odds if odds is not None else matches[0].fd_price
        return botcore.chance_text(matches, odds, wager)

    def place(self, channel, sport, query, odds_text, stake, user_id, user_name) -> str:
        try:
            odds = botcore.parse_odds(odds_text)
        except ValueError:
            return "Odds look like -150 or +240."
        if stake <= 0:
            return "The stake has to be more than $0."
        season = botcore.season_now()
        m = re.fullmatch(r"\s*[pP](\d+)\s*", query)
        if m:
            shown_sport, parlays = self.last_parlays.get(channel, (sport, []))
            n = int(m.group(1))
            if not 1 <= n <= len(parlays):
                return "Run `/parlays` in this channel first, then place one with `bet:P1`."
            entries = launcher.tracker_entries([], [parlays[n - 1]], shown_sport, season)
            what = f"Parlay P{n}"
        else:
            matches = self.find(sport, query)
            if not matches:
                return "Couldn't find that bet. Try the team or player name and the line."
            if self.state():
                try:
                    matches, _ = self._checked(sport, matches[:1])
                except fdfeed.FeedError as e:
                    return f"Couldn't check against FanDuel ({e}), so the bet wasn't logged."
                if not matches:
                    return "That bet isn't on FanDuel at that line, so it wasn't logged."
                odds = odds if odds is not None else matches[0].fd_price
            entries = launcher.tracker_entries([matches[0]], [], sport, season)
            what = botcore._describe(matches[0])
        if not entries:
            return "That bet can't be tracked (no game found for it)."
        e = self.ledger.place(entries[0], odds, stake, user_id, user_name)
        win = e["stake"] * (botcore.american_to_decimal(e["odds"]) - 1)
        return (f"Logged for **{user_name}**: {what}, ${e['stake']:g} at "
                f"{botcore._fmt_american(e['odds'])} to win ${win:,.2f} "
                f"({e['win_prob']:.0%} to win). It's graded after the game.")

    def remove(self, user_id, query) -> str:
        e = self.ledger.remove(user_id, query)
        return (f"Removed your bet: {botcore.entry_title(e)}." if e
                else "None of your placed bets match that.")

    def history(self, sport, week, show_picks) -> str:
        return botcore.history_text(self.ledger.load(), sport, week, show_picks)

    def check_results(self) -> str | None:
        return botcore.settled_text(self.ledger.regrade())


def run_bot(token: str) -> None:
    import discord
    from discord import app_commands
    from discord.ext import tasks

    settings = load_settings()
    core = Core(LEDGER, state=lambda: settings.get("fanduel_state"))
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    sport_choices = [app_commands.Choice(name=v, value=k) for k, v in SPORTS.items()]

    async def send(interaction, text):
        parts = text if isinstance(text, list) else [text]
        parts = [c for p in parts for c in botcore.chunks(p)]
        await interaction.followup.send(parts[0])
        for part in parts[1:]:
            await interaction.followup.send(part)

    async def work(interaction, fn, *args):
        await interaction.response.defer(thinking=True)
        try:
            text = await asyncio.to_thread(fn, *args)
        except data.DataError as e:
            text = f"Couldn't load the data right now ({e}). Try again in a few minutes."
        except Exception as e:  # keep the bot answering
            text = f"Something went wrong: {e}"
        await send(interaction, text)

    @tree.command(name="bets", description="Top single bets, likeliest first")
    @app_commands.choices(sport=sport_choices)
    @app_commands.describe(count="How many bets (1-30)", min_chance="Lowest win % (default 60)",
                           max_chance="Highest win % (default 80)",
                           week="NFL or college week (default: this week)", wager="Wager in $")
    async def bets_cmd(interaction: discord.Interaction, sport: str = "nfl",
                       count: app_commands.Range[int, 1, 30] = 10,
                       min_chance: app_commands.Range[int, 1, 99] = 60,
                       max_chance: app_commands.Range[int, 1, 99] = 80,
                       week: int | None = None, wager: float = 10.0):
        await work(interaction, core.bets, sport, count, min_chance, max_chance, week, wager)

    @tree.command(name="parlays", description="Top parlays as bet slips")
    @app_commands.choices(sport=sport_choices)
    @app_commands.describe(count="How many parlays (1-10)", min_legs="Fewest legs",
                           max_legs="Most legs", week="Week (default: this week)",
                           wager="Wager in $")
    async def parlays_cmd(interaction: discord.Interaction, sport: str = "nfl",
                          count: app_commands.Range[int, 1, 10] = 3,
                          min_legs: app_commands.Range[int, 2, 8] = 3,
                          max_legs: app_commands.Range[int, 2, 8] = 5,
                          week: int | None = None, wager: float = 10.0):
        await work(interaction, core.parlays, interaction.channel_id, sport, count,
                   min_legs, max(min_legs, max_legs), 60, 80, week, wager)

    @tree.command(name="chance", description="Win % for a bet, and whether FanDuel's price is worth it")
    @app_commands.choices(sport=sport_choices)
    @app_commands.describe(bet="Team or player and the line, e.g. Ravens +11.5",
                           odds="FanDuel's price, e.g. -350 (optional)", wager="Wager in $")
    async def chance_cmd(interaction: discord.Interaction, bet: str, odds: str | None = None,
                         sport: str = "nfl", wager: float = 10.0):
        await work(interaction, core.chance, sport, bet, odds, wager)

    @tree.command(name="place", description="Log a bet you placed in the server's history")
    @app_commands.choices(sport=sport_choices)
    @app_commands.describe(bet="The bet, e.g. Ravens +11.5, or P1 for a parlay from /parlays",
                           odds="The odds you got, e.g. -350 (blank = break-even)",
                           stake="Dollars you bet")
    async def place_cmd(interaction: discord.Interaction, bet: str, stake: float = 10.0,
                        odds: str | None = None, sport: str = "nfl"):
        await work(interaction, core.place, interaction.channel_id, sport, bet, odds, stake,
                   interaction.user.id, interaction.user.display_name)

    @tree.command(name="remove", description="Remove one of your logged bets")
    @app_commands.describe(bet="Words from the bet, e.g. Ravens")
    async def remove_cmd(interaction: discord.Interaction, bet: str):
        await work(interaction, core.remove, interaction.user.id, bet)

    @tree.command(name="history", description="The server's bet history: won, lost and pending")
    @app_commands.choices(sport=sport_choices)
    @app_commands.describe(week="Just one week", show_picks="Also list every pick the bot showed")
    async def history_cmd(interaction: discord.Interaction, sport: str | None = None,
                          week: int | None = None, show_picks: bool = False):
        await work(interaction, core.history, sport, week, show_picks)

    @tree.command(name="injuries", description="This week's NFL injury report")
    @app_commands.describe(team="Team name or code, e.g. Bills or BUF (optional)")
    async def injuries_cmd(interaction: discord.Interaction, team: str | None = None):
        await work(interaction, botcore.injuries_text, team)

    @tree.command(name="fanduel-check",
                  description="Only show bets found on FanDuel's site (your state), or 'off'")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(state="Two-letter state you bet in, e.g. NJ, or off")
    async def fanduel_check(interaction: discord.Interaction, state: str):
        s = state.strip().lower()
        if s == "off":
            settings.pop("fanduel_state", None)
            msg = ("FanDuel check is off. Bets come from markets FanDuel posts for every game, "
                   "but confirm each one on FanDuel before you bet.")
        elif s in fdfeed.STATES:
            settings["fanduel_state"] = s
            msg = (f"FanDuel check is on ({s.upper()}): /bets, /parlays, /chance and /place "
                   "only use bets found on FanDuel's site, at FanDuel's odds. This reads "
                   "FanDuel's website data unofficially; it can stop working if FanDuel "
                   "changes or blocks it.")
        else:
            msg = f"FanDuel isn't available in {state.strip().upper()}. Use a state like NJ, or off."
        save_settings(settings)
        await interaction.response.send_message(msg)

    @tree.command(name="results-here", description="Post bet results in this channel")
    @app_commands.default_permissions(manage_guild=True)
    async def results_here(interaction: discord.Interaction):
        settings.setdefault("results_channels", {})[str(interaction.guild_id)] = interaction.channel_id
        save_settings(settings)
        await interaction.response.send_message("Results will be posted in this channel after games finish.")

    @tasks.loop(minutes=CHECK_MINUTES)
    async def grade_loop():
        try:
            text = await asyncio.to_thread(core.check_results)
        except Exception as e:
            print(f"Grading failed: {e}")
            return
        if not text:
            return
        for channel_id in settings.get("results_channels", {}).values():
            channel = client.get_channel(channel_id)
            if channel is not None:
                for part in botcore.chunks(text):
                    await channel.send(part)

    @client.event
    async def on_ready():
        for guild in client.guilds:  # commands show up right away in each server
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        if not grade_loop.is_running():
            grade_loop.start()
        print(f"Bet Builder bot is online as {client.user} in {len(client.guilds)} server(s). "
              "Press Ctrl+C to stop.")

    @client.event
    async def on_guild_join(guild):
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)

    client.run(token)


def selftest() -> int:
    """Run every command's logic against live data, without connecting to Discord."""
    with tempfile.TemporaryDirectory() as d:
        core = Core(Path(d) / "ledger.json")
        out = {"bets": core.bets("nfl", 10, 60, 80, None, 10.0),
               "parlays": core.parlays(1, "nfl", 3, 3, 5, 60, 80, None, 10.0),
               "chance": core.chance("nfl", "ravens +11.5", "-350", 10.0)}
        top = core.boards.get("nfl", None, 0.6, 0.8)
        query = botcore._describe(botcore.pick_singles(top, 1)[0])
        out["place"] = core.place(1, "nfl", query, "-300", 20, 1, "Tester")
        out["place parlay"] = core.place(1, "nfl", "P1", None, 10, 2, "Tester 2")
        out["history"] = core.history(None, None, True)
        out["remove"] = core.remove(1, query.split()[0])
        out["results"] = core.check_results() or "(nothing settled)"
        out["injuries"] = botcore.injuries_text("BUF")
        out["college"] = core.bets("ncaaf", 5, 60, 80, None, 10.0)
    ok = True
    for name, text in out.items():
        parts = [c for p in (text if isinstance(text, list) else [text]) for c in botcore.chunks(p)]
        too_long = [p for p in parts if len(p) > 2000]
        ok &= not too_long
        print(f"--- {name}: {len(parts)} message(s){' TOO LONG' if too_long else ''}")
        print(parts[0][:600])
    import discord  # noqa: F401  (the library is bundled and imports)
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a codepage without → or ·
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    token = read_token()
    if not token:
        print("No bot token. Put it in DISCORD_BOT_TOKEN or in", TOKEN_FILE)
        return 1
    try:
        run_bot(token)
    except KeyboardInterrupt:
        pass
    except Exception as e:  # e.g. a wrong token
        print(f"The bot stopped: {e}")
        if sys.stdin and sys.stdin.isatty():
            input("Press Enter to close.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
