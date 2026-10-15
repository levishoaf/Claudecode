#!/usr/bin/env python3
"""Bet Builder by Levi Shoaf, as a Discord bot. Runs on your computer.

    python3 discord_bot.py              # asks for the bot token the first time
    python3 discord_bot.py --selftest   # checks everything except the Discord connection

Slash commands: /bets, /parlays, /multilays, /moneymaker, /chance, /place,
/remove, /history, /injuries and /results-here. The whole server shares one bet history,
saved in "discord bets.json" next to this program. Placed bets and the
picks the bot showed are graded after the games, and results are posted in
the channel chosen with /results-here.

The token is read from the DISCORD_BOT_TOKEN environment variable or from
"discord token.txt" next to this program. Keep it secret.

On Windows the downloaded bot also: offers to start whenever you sign in
(a small file in your Startup folder; `--startup on` / `--startup off`), updates
itself from the latest release (at start and every few hours), and opens its own
invite link while it isn't in any server yet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import webbrowser
from pathlib import Path

import bets as launcher  # sets up paths and certificates, like the app
from nfl_edge import botcore, data, updater

try:  # discord.py reads the slash commands' type hints (strings here) from this module
    import discord
    from discord import app_commands
except ImportError:  # --startup and the command logic work without the library
    discord = app_commands = None
from nfl_edge.picks import (BET_TYPES, CONFIRM_NOTE, SURE_TYPES, empty_type_note,
                            every_line_of, fanduel_estimate,
                            money_maker_parlays, of_type,
                            type_max_prob)

ROOT = launcher.ROOT
LEDGER = ROOT / "discord bets.json"
SETTINGS = ROOT / "discord bot settings.json"
TOKEN_FILE = ROOT / "discord token.txt"
CHECK_MINUTES = 10  # how often finished games are graded
UPDATE_HOURS = 6  # how often the downloaded bot looks for a newer release
# What the bot may do in a server: view channels, send messages, embed links and read
# message history (Discord permission bits 1024 + 2048 + 16384 + 65536).
PERMISSIONS = 84992
STARTUP_NAME = "Bet Builder Discord Bot.cmd"

SPORTS = {"nfl": "NFL", "ncaaf": "College football", "ufc": "UFC", "mlb": "MLB"}


def clean_token(text: str) -> str:
    """A pasted token without quotes, spaces or a "Bot " in front."""
    text = text.strip().strip('"\'').strip()
    return text[4:].strip() if text.lower().startswith("bot ") else text


def interactive() -> bool:
    return bool(sys.stdin and sys.stdin.isatty())


def read_token() -> str | None:
    token = clean_token(os.environ.get("DISCORD_BOT_TOKEN", ""))
    if not token and TOKEN_FILE.exists():
        token = clean_token(TOKEN_FILE.read_text())
    if not token and interactive():
        print("Paste your Discord bot token (discord.com/developers → your app → Bot → "
              "Reset Token → Copy), then press Enter.\n"
              "In this window, right-click pastes. The token won't be shown again; it's "
              "saved next to this program.")
        token = clean_token(input("> "))
        if token:
            TOKEN_FILE.write_text(token + "\n")
            print(f"Saved to {TOKEN_FILE}. Keep that file private: anyone with it controls "
                  "your bot.")
    return token or None


def invite_link(app_id: int) -> str:
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}"
            f"&permissions={PERMISSIONS}&scope=bot%20applications.commands")


# ---------------------------------------------------------------- Windows niceties

def startup_file() -> Path | None:
    """The file in the Windows Startup folder that starts the bot at sign-in."""
    folder = os.environ.get("APPDATA")
    if sys.platform != "win32" or not folder:
        return None
    return Path(folder) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / \
        STARTUP_NAME


def set_startup(on: bool) -> str:
    """Start the bot whenever you sign in to Windows (on), or stop doing that (off)."""
    path = startup_file()
    if path is None:
        return "Starting with the computer is only set up for the Windows bot."
    if not on:
        path.unlink(missing_ok=True)
        return "The bot won't start by itself any more."
    if not getattr(sys, "frozen", False):
        return "Starting with Windows works for the downloaded bot (the .exe), not from source."
    exe = Path(sys.executable).resolve()  # updates keep the same name, so this stays right
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'@echo off\r\ncd /d "{exe.parent}"\r\n'
                    f'start "Bet Builder Discord Bot" /min "{exe}"\r\n')
    return ("The bot will start (minimized) whenever you sign in to Windows. "
            "To stop that, run it with --startup off.")


def offer_startup(settings: dict) -> None:
    """Ask once, the first time the downloaded Windows bot runs."""
    if settings.get("startup_asked") or startup_file() is None or not interactive() \
            or not getattr(sys, "frozen", False):
        return
    answer = input("Start the bot automatically whenever you sign in to Windows? [Y/n] ")
    print(set_startup(not answer.strip().lower().startswith("n")))
    settings["startup_asked"] = True
    save_settings(settings)


def update_now() -> bool:
    """Install a newer build of the bot if there is one. True when the new version was
    started, so this one must quit."""
    try:
        found = updater.available(assets=updater.BOT_ASSETS)
        if not found:
            return False
        print(f"Updating the bot to build {found.build}; it reopens in a new window...")
        updater.install(found, console=True, assets=updater.BOT_ASSETS)
        return True
    except updater.UpdateError as e:
        print(f"Couldn't update right now ({e}); trying again later.")
        return False


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    SETTINGS.write_text(json.dumps(settings, indent=1) + "\n")


WAGER = 10.0  # every payout is shown for a $10 wager

class Core:
    """Everything the commands do, without Discord (so --selftest can run it)."""

    def __init__(self, ledger_path: Path):
        self.boards = botcore.Boards()
        self.ledger = botcore.ServerLedger(ledger_path)
        self.last_parlays: dict[int, tuple[str, list]] = {}  # channel -> (sport, parlays)
        self.last_money: dict[int, tuple[str, list]] = {}  # channel -> (sport, money makers)
        self.last_multi: dict[int, tuple[str, list]] = {}  # channel -> ("multi", Multi-Lays)

    def _record(self, sport, week, singles, parlays) -> None:
        if week is None:  # only this week's picks count as picks the builder made
            self.ledger.record_shown(launcher.tracker_entries(singles, parlays, sport,
                                                              botcore.season_of(sport)))

    def _pool(self, sport, week, lo, hi):
        """(bets, note, checked): the bets sure to be on FanDuel (moneylines, anytime TDs)."""
        return self.boards.get(sport, week, lo / 100, hi / 100), None, False

    def _typed(self, sport, week, lo, hi, kind):
        """(bets of the type, note, checked). Types that aren't sure to be on FanDuel
        get every model line, marked to confirm on FanDuel."""
        pool, note, checked = self._pool(sport, week, lo, hi)
        if sport == "mlb" and kind == "Total runs":  # DraftKings' total: confirm on FanDuel
            out = of_type(self.boards.get(sport, week, lo / 100, hi / 100, sure_only=False), kind)
            return out, (CONFIRM_NOTE.format(kind=kind) if any(b.confirm_line for b in out)
                         else note), checked
        if kind in SURE_TYPES or sport != "nfl":
            return of_type(pool, kind), note, checked
        cap = type_max_prob(kind)  # likeliest lines within the odds limit
        top = cap if cap is not None and cap < hi / 100 else hi / 100
        out = every_line_of(self.boards.get(sport, week, lo / 100, top, sure_only=False), kind)
        if any(b.confirm_line for b in out):
            note = CONFIRM_NOTE.format(kind=kind)
        return out, note, checked

    def bets(self, sport, count, lo, hi, week, wager, kind="All bets") -> str:
        pool, note, checked = self._typed(sport, week, lo, hi, kind)
        if not pool and kind != "All bets":
            return empty_type_note(kind, sport, checked)
        singles = botcore.pick_singles(pool, count, rank_by="ev" if checked else "prob",
                                       mix=kind == "All bets")
        self._record(sport, week, singles, [])
        return botcore.singles_text(singles, sport, wager) + (f"\n_{note}_" if note else "")

    def parlays(self, channel, sport, count, min_legs, max_legs, lo, hi, week, wager,
                pays=None, kind="All bets") -> list[str]:
        pool, note, checked = self._typed(sport, week, lo, hi, kind)
        if not pool and kind != "All bets":
            return [empty_type_note(kind, sport, checked)]
        if any(getattr(b, "tier", None) for b in pool):  # Strong legs first, never Weak
            from nfl_edge import insights

            pool, tier_note = insights.parlay_pool(pool)
            note = f"{note} {tier_note}" if note else tier_note
        parlays = botcore.pick_parlays(pool, count, min_legs, max_legs,
                                       pays=pays / wager if pays else None)
        self.last_parlays[channel] = (sport, parlays)
        self._record(sport, week, [], parlays)
        out = botcore.parlays_text(parlays, sport, wager)
        if note:
            out[0] += f"\n_{note}_"
        return out

    def money(self, channel, sport, week, wager, kind="All bets") -> list[str]:
        """Money makers: the likeliest parlays that pay back at least double the wager."""
        pool, note, checked = self._typed(sport, week, 1, 99, kind)
        pool = [b for b in pool if getattr(b, "tier", "yellow") != "red"]  # never a Weak leg
        from nfl_edge import hitrate

        money = money_maker_parlays(pool, 5, strong=hitrate.strong_leg_check(
            botcore.season_now()) if sport == "nfl" else None)
        self.last_money[channel] = (sport, money)
        self._record(sport, week, [], money)
        if not money:
            return ["No money makers: not enough games for that pick this week. "
                    "Try bet_type All bets."]
        out = botcore.parlays_text(money, sport, wager)
        out[0] = (out[0].replace("parlays**", "money makers**").replace("bet:P1", "bet:M1")
                  + "\nThe likeliest parlays that pay back at least double your wager if they "
                  "win, even at FanDuel's prices. "
                  "Parlays whose every leg has won at least 75% of the time come first.")
        for i, p in enumerate(money, 1):
            out[i] = (out[i].replace(f"**P{i}**", f"**M{i}**", 1)
                      + f"\nAbout ${wager * fanduel_estimate(p):,.2f} back on "
                      f"${wager:g} at FanDuel's prices")
        if note and (not checked or kind not in SURE_TYPES):
            out[0] += f"\n_{note}_"
        return out

    def multi(self, channel, wager, pays=40.0) -> list[str]:
        """Multi-Lays: parlays mixing leagues, each paying at least `pays` on $10."""
        from nfl_edge.picks import multi_lays

        found = multi_lays(launcher.league_pools(), pays / 10, 5)
        self.last_multi[channel] = ("multi", found)
        self.ledger.record_shown(launcher.tracker_entries([], found, "multi",
                                                          botcore.season_of("multi")))
        if not found:
            return ["No Multi-Lays right now: fewer than two leagues have games."]
        out = botcore.parlays_text(found, "multi", wager)
        out[0] = (out[0].replace("bet:P1", "bet:X1")
                  + f"\nParlays with legs from at least two leagues (NFL, College Football, "
                  f"UFC, MLB), each paying at least ${pays:g} back on $10, likeliest first.")
        for i, p in enumerate(found, 1):
            leagues = " + ".join(sorted({botcore.SPORT_NAME[b.league] for b in p.legs}))
            out[i] = out[i].replace(f"**P{i}**", f"**X{i}** ({leagues})", 1)
        return out

    def find(self, sport, query):
        return botcore.search(self.boards.get(sport, None, *botcore.SEARCH_RANGE, every_line=True),
                              query)

    def chance(self, sport, query, odds_text, wager) -> str:
        try:
            odds = botcore.parse_odds(odds_text)
        except ValueError:
            return "Odds look like -150 or +240."
        matches = self.find(sport, query)
        return botcore.chance_text(matches, odds, wager)

    def place(self, channel, sport, query, odds_text, stake, user_id, user_name) -> str:
        try:
            odds = botcore.parse_odds(odds_text)
        except ValueError:
            return "Odds look like -150 or +240."
        if stake <= 0:
            return "The stake has to be more than $0."
        m = re.fullmatch(r"\s*([pPmMxX])(\d+)\s*", query)
        if m:
            kind = m.group(1).upper()
            shown_sport, parlays = {"P": self.last_parlays, "M": self.last_money,
                                    "X": self.last_multi}[kind].get(channel, (sport, []))
            n = int(m.group(2))
            if not 1 <= n <= len(parlays):
                cmd = {"P": "/parlays", "M": "/moneymaker", "X": "/multilays"}[kind]
                return f"Run `{cmd}` in this channel first, then place one with `bet:{kind}1`."
            entries = launcher.tracker_entries([], [parlays[n - 1]], shown_sport,
                                               botcore.season_of(shown_sport))
            what = f"{dict(P='Parlay', M='Money maker', X='Multi-Lay')[kind]} {kind}{n}"
        else:
            matches = self.find(sport, query)
            if not matches:
                return "Couldn't find that bet. Try the team or player name and the line."
            entries = launcher.tracker_entries([matches[0]], [], sport, botcore.season_of(sport))
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


def run_bot(token: str | None, connect: bool = True):
    """Set up the slash commands and run the bot. With connect=False (the self-test),
    stop before connecting and return the command tree."""
    from discord.ext import tasks

    settings = load_settings()
    core = Core(LEDGER)
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    sport_choices = [app_commands.Choice(name=v, value=k) for k, v in SPORTS.items()]
    type_choices = [app_commands.Choice(name=k, value=k) for k in BET_TYPES]

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
    @app_commands.choices(sport=sport_choices, bet_type=type_choices)
    @app_commands.describe(count="How many bets (1-30)",
                           week="NFL or college week (default: this week)",
                           bet_type="Only one type of bet, e.g. Anytime TD (default: all)")
    async def bets_cmd(interaction: discord.Interaction, sport: str = "nfl",
                       count: app_commands.Range[int, 1, 30] = 10,
                       week: int | None = None, bet_type: str = "All bets"):
        await work(interaction, core.bets, sport, count, 1, 99, week, WAGER, bet_type)

    @tree.command(name="parlays", description="Top parlays as bet slips")
    @app_commands.choices(sport=sport_choices, bet_type=type_choices)
    @app_commands.describe(count="How many parlays (1-10)", min_legs="Fewest legs",
                           max_legs="Most legs", week="Week (default: this week)",
                           pays="Build parlays that pay at least this much back (default $40; 0 = any)",
                           bet_type="Only one type of bet, e.g. Anytime TD (default: all)")
    async def parlays_cmd(interaction: discord.Interaction, sport: str = "nfl",
                          count: app_commands.Range[int, 1, 10] = 3,
                          min_legs: app_commands.Range[int, 2, 8] = 2,
                          max_legs: app_commands.Range[int, 2, 8] = 6,
                          week: int | None = None, pays: float = 40.0,
                          bet_type: str = "All bets"):
        await work(interaction, core.parlays, interaction.channel_id, sport, count,
                   min_legs, max(min_legs, max_legs), 1, 99, week, WAGER, pays or None, bet_type)

    @tree.command(name="moneymaker",
                  description="The likeliest parlays that pay back at least double the wager")
    @app_commands.choices(sport=sport_choices, bet_type=type_choices)
    @app_commands.describe(week="Week (default: this week)",
                           bet_type="Only one type of bet (default: all)")
    async def moneymaker_cmd(interaction: discord.Interaction, sport: str = "nfl",
                             week: int | None = None, bet_type: str = "All bets"):
        await work(interaction, core.money, interaction.channel_id, sport, week, WAGER, bet_type)

    @tree.command(name="multilays",
                  description="Parlays mixing leagues: NFL, college football, UFC and MLB legs")
    async def multilays_cmd(interaction: discord.Interaction):
        await work(interaction, core.multi, interaction.channel_id, WAGER)

    @tree.command(name="chance", description="Win % for a bet, and whether FanDuel's price is worth it")
    @app_commands.choices(sport=sport_choices)
    @app_commands.describe(bet="Team or player and the line, e.g. Ravens +11.5",
                           odds="FanDuel's price, e.g. -350 (optional)")
    async def chance_cmd(interaction: discord.Interaction, bet: str, odds: str | None = None,
                         sport: str = "nfl"):
        await work(interaction, core.chance, sport, bet, odds, WAGER)

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

    @tasks.loop(hours=UPDATE_HOURS)
    async def update_loop():
        if update_loop.current_loop == 0:  # just checked at start
            return
        if await asyncio.to_thread(update_now):
            await client.close()  # the new version is starting in its own window

    @client.event
    async def on_ready():
        for guild in client.guilds:  # commands show up right away in each server
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        if not grade_loop.is_running():
            grade_loop.start()
        if updater.can_update(assets=updater.BOT_ASSETS) and not update_loop.is_running():
            update_loop.start()
        link = invite_link(client.application_id or client.user.id)
        names = ", ".join(g.name for g in client.guilds)
        print(f"Bet Builder bot is online as {client.user}"
              + (f" in: {names}." if names else "."))
        print(f"Add it to a server with this link: {link}")
        if not client.guilds:  # not in a server yet: open the invite page
            print("It isn't in any server yet, so the invite page is opening in your browser.")
            try:
                webbrowser.open(link)
            except Exception:
                pass
        print("Leave this window open (minimizing is fine) for the bot to answer. "
              "Close it or press Ctrl+C to stop.")

    @client.event
    async def on_guild_join(guild):
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        print(f"Joined {guild.name}: type / in a channel there to see the commands.")

    if not connect:
        return tree
    client.run(token)


def selftest() -> int:
    """Run every command's logic against live data, without connecting to Discord."""
    with tempfile.TemporaryDirectory() as d:
        core = Core(Path(d) / "ledger.json")
        out = {"bets": core.bets("nfl", 10, 1, 99, None, 10.0),
               "td_bets": core.bets("nfl", 5, 1, 99, None, 10.0, "Anytime TD"),
               "rushing": core.bets("nfl", 5, 1, 99, None, 10.0, "Rushing yards"),
               "receiving": core.bets("nfl", 5, 1, 99, None, 10.0, "Receiving yards"),
               "spread parlays": core.parlays(1, "nfl", 2, 2, 6, 1, 99, None, 10.0, 40.0,
                                              "Spread"),
               "parlays": core.parlays(1, "nfl", 3, 2, 6, 1, 99, None, 10.0, pays=40.0),
               "chance": core.chance("nfl", "ravens +11.5", "-350", 10.0),
               "moneymaker": core.money(1, "nfl", None, 10.0)}
        top = core.boards.get("nfl", None, 0.6, 0.8)
        query = botcore._describe(botcore.pick_singles(top, 1)[0])
        out["place"] = core.place(1, "nfl", query, "-300", 20, 1, "Tester")
        out["place parlay"] = core.place(1, "nfl", "P1", None, 10, 2, "Tester 2")
        out["place money maker"] = core.place(1, "nfl", "M1", None, 10, 3, "Tester 3")
        out["history"] = core.history(None, None, True)
        out["remove"] = core.remove(1, query.split()[0])
        out["results"] = core.check_results() or "(nothing settled)"
        out["injuries"] = botcore.injuries_text("BUF")
        out["college"] = core.bets("ncaaf", 5, 1, 99, None, 10.0)
        out["ufc"] = core.bets("ufc", 10, 1, 99, None, 10.0)
        out["ufc distance"] = core.bets("ufc", 5, 1, 99, None, 10.0, "Fight distance")
        out["ufc moneymaker"] = core.money(4, "ufc", None, 10.0)
        out["mlb"] = core.bets("mlb", 10, 1, 99, None, 10.0)
        out["mlb totals"] = core.bets("mlb", 5, 1, 99, None, 10.0, "Total runs")
        out["mlb parlays"] = core.parlays(5, "mlb", 2, 2, 4, 1, 99, None, 10.0, 40.0)
        out["multilays"] = core.multi(6, 10.0)
        out["place multi-lay"] = core.place(6, "nfl", "X1", None, 10, 4, "Tester 4")
    ok = True
    for name, text in out.items():
        parts = [c for p in (text if isinstance(text, list) else [text]) for c in botcore.chunks(p)]
        too_long = [p for p in parts if len(p) > 2000]
        ok &= not too_long
        print(f"--- {name}: {len(parts)} message(s){' TOO LONG' if too_long else ''}")
        print(parts[0][:600])
    from nfl_edge import learn

    from nfl_edge import signals

    if not learn.saved().get("groups") or not signals.saved():  # must ship in the build
        print("no learned calibration or signal weights (calibration.json, signal_weights.json)")
        ok = False
    from nfl_edge import mlb, ufc

    if not ufc.weights().get("win"):
        print("no UFC model weights (ufc_weights.json)")
        ok = False
    if not mlb.weights().get("weights"):
        print("no MLB model weights (mlb_weights.json)")
        ok = False
    # Build every slash command as Discord would see them (without connecting): a
    # command Discord can't read would stop the bot as it starts.
    try:
        names = sorted(c.name for c in run_bot(None, connect=False).get_commands())
        print("--- slash commands:", ", ".join(names))
        ok &= len(names) >= 10
    except Exception as e:
        print(f"slash commands FAILED to build: {e!r}")
        ok = False
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
    ap.add_argument("--startup", choices=["on", "off"],
                    help="start the bot whenever you sign in to Windows, or stop doing that")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.startup:
        print(set_startup(args.startup == "on"))
        return 0
    if sys.platform == "win32":
        os.system("title Bet Builder Discord Bot")
    updater.cleanup(assets=updater.BOT_ASSETS)
    if update_now():  # a newer build is opening in its own window
        return 0
    for attempt in range(3):
        token = read_token()
        if not token:
            print("No bot token. Put it in DISCORD_BOT_TOKEN or in", TOKEN_FILE)
            return 1
        offer_startup(load_settings())
        try:
            run_bot(token)
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            if type(e).__name__ == "LoginFailure":  # a wrong or reset token
                print("Discord didn't accept that token. On discord.com/developers open your "
                      "app → Bot → Reset Token → Copy, and paste the new one.")
                TOKEN_FILE.unlink(missing_ok=True)
                if interactive() and attempt < 2:
                    continue
            else:
                print(f"The bot stopped: {e}")
            if interactive():
                input("Press Enter to close.")
            return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
