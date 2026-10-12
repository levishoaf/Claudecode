#!/usr/bin/env python3
"""Windowed bet builder in a modern sportsbook style: the same picks as
bets.py, on a charcoal football field. No API key needed.

    python3 bets_gui.py
"""

from __future__ import annotations

import argparse
import os
import queue
import tempfile
import sys
import threading
import traceback
from types import SimpleNamespace
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import date, datetime
from pathlib import Path
from tkinter import ttk

import bets  # sets up paths for the standalone build
from nfl_edge import board, data, hitrate, tracker, updater
from nfl_edge.cli import _fmt_american, _slip_market, _slip_selection, _slip_time
from nfl_edge.cli import clock as _clock
from nfl_edge.context import name_key
from nfl_edge.odds import american_to_decimal, decimal_to_american
from nfl_edge.parlays import Parlay
from nfl_edge.picks import BET_TYPES, empty_type_note, make_or_break, money_maker_parlays

W, H = 1180, 870
PANEL_W = W - 2 * W * 0.08 - 90  # leaves turf visible on both sides
CARD_W = 272  # bet cards, three across

# Field (charcoal)
# Sportsbook-style UI, in dark mode
NAVY, NAVY_2 = "#111a2b", "#1b2740"
BLUE, BLUE_DARK, BLUE_SOFT = "#3d8bff", "#2a6fdb", "#22314d"
BG, CARD, BORDER = "#151a23", "#1d2430", "#2c3545"
SUBTLE, INPUT = "#19202b", "#0f141c"  # nested rows; text boxes
TEXT, MUTED, WHITE = "#e6ebf2", "#94a0b2", "#ffffff"
GREEN, AMBER, RED, TRACK = "#2fb864", "#e8ad1c", "#ef5b5b", "#2a3343"
NEW_BADGE, MOVED_BADGE = "#3d8bff", "#f08a24"
# Bets you placed and picks you didn't, graded after the games.
LEDGER = Path.home() / "Bet Builder" / "my bets.json"
SPORTS = (("nfl", "NFL"), ("ncaaf", "College"), ("nba", "NBA"))
LEAGUE_NAMES = {"nfl": "NFL", "ncaaf": "College football", "nba": "NBA"}
RESULT_STYLE = {"won": ("HIT", GREEN), "lost": ("MISS", RED), "push": ("PUSH", "#8a96a8"),
                "pending": ("PENDING", NEW_BADGE)}
STATUS_COLORS = {"Out": "#d64545", "Doubtful": "#e8590c", "Questionable": "#e0a100",
                 "Did not practice": "#6b7280", "Limited": "#94a3b8"}
STATUS_SHORT = {"Did not practice": "No practice"}  # fits the badge


def pick_font() -> str:
    """A modern system font that exists on this computer."""
    available = set(tkfont.families())
    for name in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "Roboto",
                 "Noto Sans", "DejaVu Sans", "Helvetica"):
        if name in available:
            return name
    return "TkDefaultFont"


def chance_color(p: float) -> str:
    return GREEN if p >= 0.7 else AMBER if p >= 0.5 else RED


def draw_backdrop(c: tk.Canvas, w: float = W, h: float = H) -> None:
    """The same dark backdrop for every league: a soft top-to-bottom gradient, faint
    diagonal pinstripes and the name down both sides (redrawn when resized)."""
    top, bottom = (11, 14, 19), (20, 25, 35)
    steps = 48
    for i in range(steps):
        t = i / (steps - 1)
        rgb = "#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        c.create_rectangle(0, h * i / steps, w, h * (i + 1) / steps + 1, fill=rgb, outline="",
                           tags="field")
    for x in range(-int(h), int(w), 34):
        c.create_line(x, h, x + h, 0, fill="#161c27", tags="field")
    for side, x in ((90, (w - PANEL_W) / 4), (-90, w - (w - PANEL_W) / 4)):
        c.create_text(x, h / 2, text="BET  BUILDER", angle=side, fill="#263042",
                      font=("Helvetica", 30, "bold"), tags="field")


class FlatButton(tk.Label):
    """A flat, modern button with a hover color."""

    def __init__(self, parent, text, command, kind="primary", font=None, **kw):
        colors = {"primary": (BLUE, WHITE, BLUE_DARK), "secondary": (CARD, BLUE, BLUE_SOFT),
                  "ghost": (NAVY_2, WHITE, "#1d3a66")}[kind]
        self.bg, self.fg, self.hover = colors
        super().__init__(parent, text=text, bg=self.bg, fg=self.fg, cursor="hand2",
                         font=font, padx=13, pady=7, **kw)
        self.command = command
        self.bind("<Enter>", lambda e: self.configure(bg=self.hover))
        self.bind("<Leave>", lambda e: self.configure(bg=self.bg))
        self.bind("<Button-1>", lambda e: self.command())

    def set_kind(self, kind: str) -> None:
        self.bg, self.fg, self.hover = {"primary": (BLUE, WHITE, BLUE_DARK),
                                        "ghost": (NAVY_2, "#b8c4d6", "#1d3a66")}[kind]
        self.configure(bg=self.bg, fg=self.fg)


class App:
    def __init__(self, root: tk.Tk, selftest: bool = False):
        self.root, self.selftest = root, selftest
        self.singles, self.parlays, self.previous = [], [], {}
        self.money: list = []  # Money Maker parlays
        self.last_args = None
        self.tab = "singles"
        self.injuries: tuple = (None, [], "")
        self.week_values: dict[str, int | None] = {}
        self.results: queue.Queue = queue.Queue()
        self.next_job = None  # pending automatic update
        self.next_at: float | None = None  # when it runs (time.monotonic)
        # The self-test must not touch your real bet history.
        self.ledger_path = (Path(tempfile.mkdtemp()) / "my bets.json") if selftest else LEDGER
        self.ledger_lock = threading.Lock()
        self.ledger = tracker.load(self.ledger_path)
        root.title("Bet Builder by Levi Shoaf"
                   + (f"  ·  build {updater.BUILD}" if updater.BUILD else ""))
        self.updating: str | None = None  # status text while an update installs
        self.offered = 0  # newest build already offered this session
        self.updates = None  # set by Updates
        root.geometry(f"{W}x{H}")
        root.minsize(W, H)  # resizable and maximizable; the layout follows the window

        fam = pick_font()
        self.f = {k: (fam, size, *style) for k, (size, *style) in {
            "title": (19, "bold"), "h2": (13, "bold"), "body": (11,), "bold": (11, "bold"),
            "pick": (12, "bold"), "small": (9,), "tiny": (8,), "tinyb": (8, "bold"),
            "odds": (15, "bold"), "label": (9, "bold")}.items()}
        self.style()

        c = tk.Canvas(root, width=W, height=H, highlightthickness=0, bg=BG)
        c.pack(fill="both", expand=True)
        draw_backdrop(c)
        self.mark = c.create_text(W - 12, H - 8, anchor="se", text="Levi Shoaf",
                                  fill="#8a8f99", font=self.f["tinyb"])  # owner watermark
        self.canvas = c
        self.header(c)
        self.output(c)
        self.checker(c)
        c.bind("<Configure>", self.relayout)

        self.say("Loading this week's picks...")
        root.after(100, self.poll)
        root.after(150, lambda: (self.load_weeks(), self.build()))  # always start with the latest data
        root.after(1000, self.tick)

    # -------------------------------------------------------------- layout
    def style(self) -> None:
        s = ttk.Style(self.root)
        s.theme_use("clam")
        for name in ("TEntry", "TCombobox"):
            s.configure(name, fieldbackground=INPUT, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=INPUT, darkcolor=INPUT, insertcolor=TEXT, padding=5,
                        arrowcolor=BLUE, background=CARD)
        # Keep a chosen week readable while the box has focus.
        s.map("TCombobox", fieldbackground=[("readonly", INPUT)],
              selectbackground=[("readonly", INPUT)], selectforeground=[("readonly", TEXT)],
              foreground=[("readonly", TEXT)], background=[("active", BORDER)])
        for opt, value in (("background", INPUT), ("foreground", TEXT),
                           ("selectBackground", BLUE), ("selectForeground", WHITE)):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", value)  # the open list
        s.configure("Vertical.TScrollbar", troughcolor=BG, background="#3a4558",
                    bordercolor=BG, arrowcolor=MUTED, lightcolor="#3a4558", darkcolor="#3a4558")

    def header(self, c: tk.Canvas) -> None:
        top = tk.Frame(c, bg=NAVY)
        row1 = tk.Frame(top, bg=NAVY)
        row1.pack(fill="x", padx=18, pady=(12, 4))
        tk.Label(row1, text="Bet Builder", bg=NAVY, fg=WHITE, font=self.f["title"]).pack(side="left")
        self.countdown = tk.StringVar(value="")
        tk.Label(row1, text="   Multi-sport  ·  free data  ·  break-even odds", bg=NAVY, fg="#9fb0c8",
                 font=self.f["small"]).pack(side="left")

        self.sport = tk.StringVar(value="nfl")
        seg = tk.Frame(row1, bg=NAVY_2)
        seg.pack(side="right")
        self.seg_btns = {}
        for key, text in SPORTS:
            b = FlatButton(seg, text, lambda k=key: self.pick_sport(k), "ghost", self.f["bold"])
            b.pack(side="left")
            self.seg_btns[key] = b
        tk.Label(row1, textvariable=self.countdown, bg=NAVY, fg=WHITE,
                 font=self.f["bold"]).pack(side="right", padx=(0, 14))
        self.pick_sport("nfl")

        row2 = tk.Frame(top, bg=NAVY)
        row2.pack(fill="x", padx=18, pady=(6, 14))
        self.when = tk.StringVar(value="Loading weeks...")
        self.n_singles, self.n_parlays = tk.StringVar(value="30"), tk.StringVar(value="10")
        self.min_legs, self.max_legs = tk.StringVar(value="3"), tk.StringVar(value="5")
        self.stake, self.refresh = tk.StringVar(value="10"), tk.StringVar(value="5")

        def field(label, var, width, values=None):
            box = tk.Frame(row2, bg=NAVY)
            box.pack(side="left", padx=(0, 5))
            tk.Label(box, text=label.upper(), bg=NAVY, fg="#9fb0c8",
                     font=self.f["label"]).pack(anchor="w")
            if values:
                w = ttk.Combobox(box, textvariable=var, values=values, width=width,
                                 font=self.f["body"], state="readonly")
            else:
                w = ttk.Entry(box, textvariable=var, width=width, font=self.f["body"])
            w.pack(anchor="w")
            return w

        def pair(label, a, b):
            box = tk.Frame(row2, bg=NAVY)
            box.pack(side="left", padx=(0, 5))
            tk.Label(box, text=label.upper(), bg=NAVY, fg="#9fb0c8",
                     font=self.f["label"]).pack(anchor="w")
            inner = tk.Frame(box, bg=NAVY)
            inner.pack(anchor="w")
            ttk.Entry(inner, textvariable=a, width=3, font=self.f["body"]).pack(side="left")
            tk.Label(inner, text="–", bg=NAVY, fg=WHITE).pack(side="left", padx=2)
            ttk.Entry(inner, textvariable=b, width=3, font=self.f["body"]).pack(side="left")

        self.games_box = field("Games", self.when, 16, ["Loading weeks..."])
        def chose_week(_event):
            self.games_box.selection_clear()
            self.root.focus_set()
            self.build()

        self.games_box.bind("<<ComboboxSelected>>", chose_week)
        self.bet_type = tk.StringVar(value="All bets")
        self.type_box = field("Bet type", self.bet_type, 12, list(BET_TYPES))

        def chose_type(_event):
            self.type_box.selection_clear()
            self.root.focus_set()
            self.build()

        self.type_box.bind("<<ComboboxSelected>>", chose_type)
        field("Singles", self.n_singles, 3)
        field("Parlays", self.n_parlays, 3)
        pair("Legs", self.min_legs, self.max_legs)
        field("Wager $", self.stake, 3)
        self.pays = tk.StringVar(value="40")
        field("Pays $", self.pays, 3)
        field("Refresh", self.refresh, 3)
        self.refresh.trace_add("write", lambda *_: self.schedule())

        btns = tk.Frame(row2, bg=NAVY)
        btns.pack(side="right", anchor="s")
        self.build_btn = FlatButton(btns, "Build bets", self.build, "primary", self.f["bold"])
        self.build_btn.pack(side="left")
        self.win_top = c.create_window(W / 2, 72, window=top, width=PANEL_W)

    def relayout(self, event) -> None:
        """Fit the window's new size: redraw the field, center the panels and give the
        bets list the extra height (so the window can be maximized or fullscreen)."""
        w, h = max(event.width, W), max(event.height, H)
        if getattr(self, "_size", None) == (w, h):
            return
        self._size = (w, h)
        c = self.canvas
        self.redraw_background()
        c.coords(self.win_top, w / 2, 72)
        out_h = h - 266  # between the header and the bottom bar
        c.coords(self.win_out, w / 2, 192 + out_h / 2)
        c.itemconfigure(self.win_out, height=out_h)
        c.coords(self.win_bottom, w / 2, h - 32)
        c.coords(self.mark, w - 12, h - 8)

    def pick_sport(self, key: str) -> None:
        changed = self.sport.get() != key
        self.sport.set(key)
        for k, b in self.seg_btns.items():
            b.set_kind("primary" if k == key else "ghost")
        if changed:
            if key == "nba":  # the NBA is a preview: no bets are built for it yet
                self.cancel_update()
                self.games_box.configure(values=["NBA  ·  coming soon"])
                self.when.set("NBA  ·  coming soon")
                self.render_nba()
                return
            self.load_weeks()
            self.build()

    def redraw_background(self) -> None:
        """The dark backdrop, fitted to the window."""
        c = getattr(self, "canvas", None)
        if c is None:
            return
        w, h = getattr(self, "_size", None) or (W, H)
        c.delete("field")
        draw_backdrop(c, w, h)
        c.tag_lower("field")

    def render_nba(self) -> None:
        self.render(SimpleNamespace(sport="nba", money_makers=[], bet_type="All bets"),
                    [], [], {})

    def nba_placeholder(self, parent) -> None:
        box = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="x", padx=6, pady=10)
        tk.Label(box, text="NBA bets are coming", bg=CARD, fg=TEXT,
                 font=self.f["h2"]).pack(anchor="w", padx=16, pady=(14, 4))
        tk.Label(box, text=("This is a preview of the NBA layout. NBA moneylines, spreads, "
                            "totals and player props will show here, with the same chances, "
                            "track records and Bet History as football, once NBA data is "
                            "added. Football picks are on the NFL and College tabs."),
                 bg=CARD, fg=MUTED, font=self.f["small"], wraplength=760,
                 justify="left").pack(anchor="w", padx=16, pady=(0, 14))

    def load_weeks(self) -> None:
        """Fill the Games list with this week and every later week known right now."""
        values: dict[str, int | None] = {}
        try:
            if self.sport.get() == "ncaaf":
                for i, w in enumerate(board.cfb_weeks()):
                    values[f"Week {w}" + ("  ·  this week" if i == 0 else "")] = w
            else:
                for i, (w, lines) in enumerate(board.nfl_weeks()):
                    tag = "this week" if i == 0 else ("lines posted" if lines else "no lines yet")
                    values[f"Week {w}  ·  {tag}"] = w
        except data.DataError:
            pass
        values["Today's games"] = None
        self.week_values = values
        self.games_box.configure(values=list(values))
        self.when.set(next(iter(values)))

    def output(self, c: tk.Canvas) -> None:
        out = tk.Frame(c, bg=BG)
        self.view = tk.Canvas(out, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(out, command=self.view.yview)
        self.view.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.view.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.view, bg=BG)
        self.view.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>",
                       lambda e: self.view.configure(scrollregion=self.view.bbox("all")))
        for seq, step in (("<MouseWheel>", None), ("<Button-4>", -1), ("<Button-5>", 1)):
            self.root.bind_all(seq, lambda e, s=step: self.view.yview_scroll(
                s if s else (-1 if e.delta > 0 else 1), "units"))
        self.win_out = c.create_window(W / 2, 192 + (H - 266) / 2, window=out, width=PANEL_W,
                                       height=H - 266)

    def checker(self, c: tk.Canvas) -> None:
        bottom = tk.Frame(c, bg=NAVY)
        chk = tk.Frame(bottom, bg=NAVY)
        chk.pack(fill="x", padx=14, pady=10)
        FlatButton(chk, "Check for updates", lambda: self.updates and self.updates.check(manual=True),
                   "secondary", self.f["bold"]).pack(side="right")
        # Update messages show here.
        self.verdict = tk.Label(chk, text="", bg=NAVY, fg="#9fb0c8",
                                anchor="w", justify="left", font=self.f["small"],
                                wraplength=int(PANEL_W) - 260)
        self.verdict.pack(side="left", fill="x", expand=True, padx=6)
        self.win_bottom = c.create_window(W / 2, H - 32, window=bottom, width=PANEL_W, height=56)

    # -------------------------------------------------------------- output
    def clear(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        self.view.yview_moveto(0)

    def say(self, text: str, clear: bool = True) -> None:
        if clear:
            self.clear()
        mono = ("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10)
        tk.Label(self.body, text=text, bg=BG, fg=TEXT, font=mono, justify="left",
                 anchor="nw").pack(anchor="nw", padx=16, pady=14)

    def render(self, args, singles, parlays, previous) -> None:
        """Header, tab bar and the current tab's cards."""
        self.render_args = (args, singles, parlays, previous)
        self.rates = getattr(args, "hit_rates", None) or {}
        self.clear()
        self.row_widgets = {}
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x", padx=16, pady=(12, 2))
        sport = {"nfl": "NFL", "ncaaf": "College Football", "nba": "NBA"}[args.sport]
        when = ("2026-27 season" if args.sport == "nba"
                else bets.week_label(singles, parlays))
        tk.Label(head, text=f"{sport}  ·  {when}", bg=BG, fg=TEXT,
                 font=self.f["h2"]).pack(side="left")
        checked = getattr(args, "fanduel_key", None)
        order = "best value first" if checked else "likeliest first"
        kind = getattr(args, "bet_type", "All bets")
        tk.Label(head, text="" if args.sport == "nba" else
                 f"   {'' if kind == 'All bets' else kind + ' only, '}{order}"
                            "",
                 bg=BG, fg=MUTED, font=self.f["small"]).pack(side="left", pady=(3, 0))
        self.fd_mode = bool(getattr(args, "fanduel_key", None))
        if self.fd_mode and board.NOTES.get("fanduel"):
            tk.Label(self.body, text="✓ " + board.NOTES["fanduel"] + "  ·  ranked by expected value",
                     bg=BG, fg=BLUE,
                     font=self.f["small"]).pack(anchor="w", padx=16)
        if board.NOTES.get("confirm_line") and args.sport != "nba":
            tk.Label(self.body, text="⚠ " + board.NOTES["confirm_line"], bg=BG, fg=MOVED_BADGE,
                     font=self.f["small"], wraplength=820, justify="left").pack(anchor="w", padx=16)
        if args.sport == "nfl" and board.NOTES.get("late"):
            tk.Label(self.body, text="⏱ " + board.NOTES["late"], bg=BG, fg=BLUE,
                     font=self.f["small"]).pack(anchor="w", padx=16)
        if args.sport == "nfl" and board.NOTES.get("injuries"):
            ready = "included for all" in board.NOTES["injuries"]
            tk.Label(self.body, text=("✓ " if ready else "⚠ ") + board.NOTES["injuries"],
                     bg=BG, fg=GREEN if ready else MOVED_BADGE,
                     font=self.f["small"]).pack(anchor="w", padx=16)

        money = getattr(args, "money_makers", [])
        tabs = tk.Frame(self.body, bg=BG)
        tabs.pack(fill="x", padx=16, pady=(10, 0))
        self.tab_labels = {}
        for key, text in (("singles", f"Single bets ({len(singles)})"),
                          ("parlays", f"Parlays ({len(parlays)})"),
                          ("money", f"Money Maker ({len(money)})"),
                          ("injuries", "Injuries" if args.sport == "nba"
                           else f"Injuries ({len(self.injuries[1])})"),
                          ("history", f"Bet History ({len(tracker.history(self.ledger))})")):
            on = key == self.tab
            tab = tk.Frame(tabs, bg=BG)
            tab.pack(side="left", padx=(0, 18))
            lbl = tk.Label(tab, text=text, bg=BG, fg=BLUE if on else MUTED, cursor="hand2",
                           font=self.f["bold"])
            lbl.pack()
            self.tab_labels[key] = lbl
            tk.Frame(tab, bg=BLUE if on else BG, height=3).pack(fill="x", pady=(4, 0))
            for w in (tab, lbl):
                w.bind("<Button-1>", lambda e, k=key: self.show_tab(k))
        tk.Frame(self.body, bg=BORDER, height=1).pack(fill="x", padx=16)

        grid = tk.Frame(self.body, bg=BG)
        grid.pack(fill="both", padx=10, pady=(6, 0))
        if args.sport == "nba" and self.tab != "history":
            self.nba_placeholder(grid)
        elif self.tab == "singles":
            self.singles_tab(grid, singles, previous)
        elif self.tab == "parlays":
            if board.NOTES.get("parlay_pool"):
                tk.Label(grid, text="● " + board.NOTES["parlay_pool"], bg=BG,
                         fg=GREEN if "only Strong" in board.NOTES["parlay_pool"] else AMBER,
                         font=self.f["small"], wraplength=820,
                         justify="left").pack(anchor="w", padx=6, pady=(6, 0))
            self.parlays_tab(grid, parlays, args.stake)
        elif self.tab == "money":
            tk.Label(grid, text=("Riskier parlays that hang on one leg: two or three strong legs "
                                 "plus one coin-flip leg (⚡ MAKE OR BREAK) that decides it. "
                                 "About 1 in 5 win, paying about 5x your wager. The make-or-break "
                                 "leg is never in the regular parlays."),
                     bg=BG, fg=MUTED, font=self.f["small"], wraplength=820,
                     justify="left").pack(anchor="w", padx=6, pady=(6, 0))
            self.parlays_tab(grid, money, args.stake,
                             swing=True)
        elif self.tab == "injuries":
            self.injuries_tab(grid)
        else:
            self.history_tab(grid)
        if args.sport != "nba":
            tk.Label(self.body, text=(
                "Every bet here was found on FanDuel; odds are FanDuel's from the last check. "
                "EV is the expected profit per $1." if self.fd_mode else
                "Odds shown are break-even: bet only if FanDuel pays that or better."),
                bg=BG, fg=MUTED, font=self.f["tiny"]).pack(anchor="w", padx=16, pady=10)

    def show_tab(self, key: str) -> None:
        self.tab = key
        if getattr(self, "render_args", None):
            self.render(*self.render_args)

    def columns(self, parent, n: int = 2, width: int = 418) -> list[tk.Frame]:
        cols = []
        for _ in range(n):
            col = tk.Frame(parent, bg=BG)
            col.pack(side="left", anchor="n", padx=4)
            tk.Frame(col, bg=BG, width=width, height=1).pack()
            cols.append(col)
        return cols

    # -------------------------------------------------------------- list view
    def list_table(self, parent, first: str, last: str | None = "pays") -> tk.Frame:
        """A white list with a header row: #, Bet, Chance, Worst odds, Pays."""
        table = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightcolor=BORDER,
                         highlightthickness=1)
        table.pack(fill="x", padx=6, pady=(6, 0))
        tk.Frame(table, bg=CARD, width=int(PANEL_W) - 70, height=0).pack()  # full panel width
        head = tk.Frame(table, bg=SUBTLE)
        head.pack(fill="x")
        self.grid_cols(head)
        fd = getattr(self, "fd_mode", False)
        last_text = "PAYS" if last == "pays" else (last or "")
        for col, text, anchor in ((0, first, "w"), (1, "BET", "w"), (2, "CHANCE", "e"),
                                  (3, "ODDS" if fd else "WORST ODDS", "e"), (4, last_text, "e")):
            tk.Label(head, text=text, bg=SUBTLE, fg=MUTED, font=self.f["label"]).grid(
                row=0, column=col, sticky=anchor, padx=6, pady=5)
        return table

    @staticmethod
    def grid_cols(frame) -> None:
        for col, size in ((0, 40), (2, 70), (3, 90), (4, 90)):
            frame.grid_columnconfigure(col, minsize=size)
        frame.grid_columnconfigure(1, weight=1)

    def wager(self) -> float:
        try:
            return float(self.stake.get() or 10)
        except ValueError:
            return 10.0

    def list_row(self, table, num: str, title: str, sub: str, prob: float, key=None,
                 last: str | None = None, price: int | None = None,
                 ev: float | None = None, extra=None) -> tk.Frame:
        """One bet: number, name with game below (and an optional colored line, e.g. its
        track record), chance, worst odds, payout."""
        tk.Frame(table, bg=BORDER, height=1).pack(fill="x")
        row = tk.Frame(table, bg=CARD)
        row.pack(fill="x")
        self.grid_cols(row)
        num_lbl = tk.Label(row, text=num, bg=CARD, fg=MUTED, font=self.f["bold"])
        num_lbl.grid(row=0, column=0, rowspan=3, sticky="w", padx=8)
        tk.Label(row, text=title, bg=CARD, fg=TEXT, font=self.f["bold"], anchor="w",
                 justify="left", wraplength=470).grid(row=0, column=1, sticky="w", padx=6,
                                                       pady=(6, 0))
        tk.Label(row, text=sub, bg=CARD, fg=MUTED, font=self.f["tiny"], anchor="w",
                 justify="left", wraplength=470).grid(row=1, column=1, sticky="w", padx=6,
                                                       pady=(0, 0 if extra else 6))
        if extra:
            lines = [extra] if isinstance(extra, tuple) else extra  # (text, color) pairs
            box = tk.Frame(row, bg=CARD)
            box.grid(row=2, column=1, sticky="w", padx=6, pady=(1, 6))
            for text, color in lines:
                tk.Label(box, text=text, bg=CARD, fg=color, font=self.f["tinyb"], anchor="w",
                         justify="left", wraplength=470).pack(anchor="w")
        tk.Label(row, text=f"{prob:.0%}", bg=CARD, fg=chance_color(prob),
                 font=self.f["pick"]).grid(row=0, column=2, rowspan=3, sticky="e", padx=6)
        fd = getattr(self, "fd_mode", False) and price is not None
        odds_text = _fmt_american(price) if fd else _fmt_american(decimal_to_american(1 / prob))
        tk.Label(row, text=odds_text, bg=CARD, fg=BLUE,
                 font=self.f["bold"]).grid(row=0, column=3, rowspan=3, sticky="e", padx=6)
        if last is not None:
            end = last
        else:  # total payout: at FanDuel's price when known, else at the break-even odds
            end = f"${self.wager() * (american_to_decimal(price) if fd else 1 / prob):,.2f}"
        end_lbl = tk.Label(row, text=end, bg=CARD, fg=TEXT, font=self.f["bold"])
        if last is None and fd and ev is not None:
            end_lbl.grid(row=0, column=4, sticky="se", padx=8, pady=(6, 0))
            tk.Label(row, text=f"EV {ev:+.1%}", bg=CARD, fg=GREEN if ev > 0 else RED,
                     font=self.f["tiny"]).grid(row=1, column=4, sticky="ne", padx=8, pady=(0, 6))
        else:
            end_lbl.grid(row=0, column=4, rowspan=3, sticky="e", padx=8)
        if key is not None:
            self.row_widgets[key] = (row, num_lbl, num)
        return row

    def empty_text(self, default: str) -> str:
        """What to say when nothing is found: why, if a bet type was chosen."""
        kind = self.bet_type.get()
        if kind == "All bets":
            return default
        return empty_type_note(kind, self.sport.get(), False)

    def singles_tab(self, parent, singles, previous) -> None:
        if not singles:
            tk.Label(parent, text=self.empty_text("None found. Try another week."), bg=BG,
                     fg=TEXT, font=self.f["body"]).pack(anchor="w", padx=6, pady=8)
            return
        table = self.list_table(parent, "#")
        for i, b in enumerate(singles, 1):
            title = _slip_selection(b) + {"Questionable": "  (Q)",
                                          "Did not practice": "  (DNP)"}.get(b.note, "")
            old = previous.get(bets.label(b)) if previous else None
            if previous and old is None:
                title += "  · NEW"
            elif old is not None and abs(old - b.fair_prob) >= 0.02:
                title += f"  · was {old:.0%}"
            on_fd = getattr(b, "on_fanduel", False)
            tag = "CONFIRM LINE  ·  " if b.confirm_line else ""
            self.list_row(table, str(i), title,
                          f"{tag}{_slip_market(b)}  ·  {b.game}  ·  {_slip_time(b)}",
                          b.fair_prob, bets.label(b),
                          price=b.fd_price if on_fd else None, ev=b.ev if on_fd else None,
                          extra=self.record_line(b))

    def parlays_tab(self, parent, parlays, stake, swing: bool = False) -> None:
        if not parlays:
            empty = ("No money makers here: this pick needs coin-flip legs (35-55% to win), "
                     "and there aren't enough this week. Try All bets." if swing else
                     self.empty_text("Not enough games for these parlays this week."))
            tk.Label(parent, text=empty,
                     bg=BG,
                     fg=TEXT, font=self.f["body"]).pack(anchor="w", padx=6, pady=8)
            return
        table = self.list_table(parent, "")
        for i, p in enumerate(parlays, 1):
            key = make_or_break(p) if swing else None
            legs = "  ·  ".join(self.leg_text(b) for b in p.legs if b is not key)
            if key is not None:
                legs += f"  ·  ⚡ MAKE OR BREAK: {self.leg_text(key)}, {key.fair_prob:.0%} to hit"
            if any(b.confirm_line for b in p.legs):
                legs = "CONFIRM LINES  ·  " + legs
            on_fd = all(getattr(b, "on_fanduel", False) for b in p.legs)
            self.list_row(table, f"M{i}" if swing else f"P{i}", f"{len(p.legs)}-leg parlay",
                          legs, p.win_prob,
                          tuple(bets.label(b) for b in p.legs),
                          price=p.american if on_fd else None, ev=p.ev if on_fd else None,
                          extra=[x for x in (self.legs_tier_line(p.legs),
                                             self.legs_record_line(p.legs)) if x])

    def hit_rates(self, args, singles) -> dict | None:
        """{bet label: {season: HitRate}}: how often each single bet would have won in
        games already played (worker thread). NFL only; None if it can't be worked out."""
        if args.sport != "nfl":
            return None
        try:
            from nfl_edge import insights

            season = bets.season_for(date.today())
            ctx = insights.context(season)  # shared with the tiers, reloaded every 30 minutes
            self._players = (season, time.monotonic(), ctx.players)
            return {bets.label(b): hitrate.rates(b, ctx.players, ctx.games, season)
                    for b in singles}
        except Exception:  # stats are extra: never block the bets
            traceback.print_exc()
            return None

    def _record(self, b):
        """(this season, last season) hit rates for a bet, or None."""
        season = bets.season_for(date.today())
        r = (getattr(self, "rates", None) or {}).get(bets.label(b)) or {}
        if not r:
            return None
        return r.get(season), r.get(season - 1)

    @staticmethod
    def _rate_color(*records) -> str:
        """Color by this season's rate once there are a few games, else last season's."""
        for hr in records:
            if hr and hr.games >= 3:
                return chance_color(hr.rate)
        return MUTED

    def record_line(self, b) -> tuple[str, str] | None:
        """How often this exact bet would have won in games already played."""
        rec = self._record(b)
        if not rec:
            return None
        now, last = rec
        parts = [f"{hr.hits} of {hr.games} {when} ({hr.rate:.0%})"
                 for hr, when in ((now, "this season"), (last, "last season")) if hr and hr.games]
        lines = self.tier_lines(b)
        if parts:
            lines.append(("Hit " + "  ·  ".join(parts), self._rate_color(now, last)))
        matchup = self.matchup_line(b)
        if matchup:
            lines.append(matchup)
        raw = getattr(b, "raw_prob", None)
        if raw is not None and abs(raw - b.fair_prob) >= 0.02:
            lines.append((f"Learned: the model said {raw:.0%}; bets like this have really won "
                          f"about {b.fair_prob:.0%}, so that's the chance shown", MUTED))
        ins = getattr(b, "insight", None)
        if ins:
            lines += [(n, TEXT) for n in ins.notes]
            lines += [("⚠ " + f, AMBER) for f in ins.flags]
        return lines or None

    TIER_COLORS = {"green": GREEN, "yellow": AMBER, "red": RED}

    def tier_lines(self, b) -> list:
        """The bet's confidence tier and why."""
        ins = getattr(b, "insight", None)
        if not ins:
            return []
        from nfl_edge.insights import TIER_WORDS

        why = ", ".join(ins.reasons) if ins.reasons else "no strong signals either way"
        if abs(getattr(ins, "shift", 0.0)) >= 0.005:
            why += f"  ({ins.shift * 100:+.0f} pts from tested signals)"
        return [(f"● {TIER_WORDS[ins.tier]}: {why}", self.TIER_COLORS[ins.tier])]

    def legs_tier_line(self, legs) -> tuple[str, str] | None:
        """How many of a parlay's legs are Strong, Mixed and Weak."""
        from nfl_edge.insights import TIER_WORDS

        tiers = [getattr(b, "tier", None) for b in legs]
        if not any(tiers):
            return None
        counts = [(t, tiers.count(t)) for t in ("green", "yellow", "red") if tiers.count(t)]
        worst = "red" if "red" in tiers else "yellow" if "yellow" in tiers else "green"
        return ("● Legs: " + ", ".join(f"{n} {TIER_WORDS[t]}" for t, n in counts),
                self.TIER_COLORS[worst])

    def matchup_line(self, b) -> tuple[str, str] | None:
        """A moneyline pick's record against the team it plays this week, game by game."""
        hr = self._h2h(b)
        if hr is None:
            return None
        opp = self._opponent(b)
        since = bets.season_for(date.today()) - hitrate.H2H_SEASONS + 1
        if not hr.games:
            return f"Vs this week's opponent, the {opp}: no games since {since}", MUTED
        return (f"Vs this week's opponent, the {opp}: {hr.hits}-{hr.games - hr.hits} since "
                f"{since}  ({', '.join(hr.results)})", chance_color(hr.rate))

    def _h2h(self, b):
        if b.market != "h2h":
            return None
        return ((getattr(self, "rates", None) or {}).get(bets.label(b)) or {}).get("h2h")

    @staticmethod
    def _opponent(b) -> str:
        return next((t for t in b.game.split(" @ ") if t != b.pick), "opponent")

    def h2h_text(self, b) -> str | None:
        """Short form for a parlay leg: "3-1 vs Titans" (this week's opponent)."""
        hr = self._h2h(b)
        if hr is None or not hr.games:
            return None
        return f"{hr.hits}-{hr.games - hr.hits} vs {self._opponent(b).split()[-1]}"

    def _leg_record(self, b):
        """This season's record for a parlay leg (last season's if none yet), or None."""
        rec = self._record(b)
        if not rec:
            return None
        return rec[0] if rec[0] and rec[0].games else (rec[1] if rec[1] and rec[1].games else None)

    def leg_text(self, b) -> str:
        """A parlay leg with its record, e.g. "Jahmyr Gibbs Anytime TD (4/4)"."""
        hr = self._leg_record(b)
        bits = [f"{hr.hits}/{hr.games}"] if hr else []
        h2h = self.h2h_text(b)
        if h2h:
            bits.append(h2h)
        return _slip_selection(b) + (f" ({', '.join(bits)})" if bits else "")

    def legs_record_line(self, legs) -> tuple[str, str] | None:
        """The parlay's weakest leg by its record; colored by it."""
        weakest = min(((self._leg_record(b), b) for b in legs if self._leg_record(b)),
                      key=lambda x: x[0].rate, default=None)
        if weakest is None:
            return None
        hr, b = weakest
        return (f"Weakest leg: {_slip_selection(b)} hit {hr.hits} of {hr.games} "
                f"({hr.rate:.0%})", self._rate_color(hr))

    def injuries_tab(self, parent) -> None:
        week, players, note = self.injuries
        tk.Label(parent, text=note, bg=BG, fg=MUTED, font=self.f["small"], wraplength=820,
                 justify="left").pack(anchor="w", padx=6, pady=(6, 2))
        if not players:
            return
        legend = tk.Frame(parent, bg=BG)
        legend.pack(anchor="w", padx=6, pady=(0, 4))
        for status in board.SEVERITY:
            tk.Label(legend, text=f" {STATUS_SHORT.get(status, status)} ", bg=STATUS_COLORS[status], fg=WHITE,
                     font=self.f["tinyb"]).pack(side="left", padx=(0, 6))
        holder = tk.Frame(parent, bg=BG)
        holder.pack(fill="both")
        cols = self.columns(holder)
        teams: dict[str, list] = {}
        for p in players:
            teams.setdefault(p["team"], []).append(p)
        heights = [0, 0]
        for team, group in teams.items():
            col = heights.index(min(heights))  # balance the two columns
            heights[col] += len(group) + 2
            card = self.card(cols[col])
            head = tk.Frame(card, bg=NAVY)
            head.pack(fill="x")
            first = group[0]
            tk.Label(head, text=first["team_name"], bg=NAVY, fg=WHITE,
                     font=self.f["bold"]).pack(side="left", padx=(12, 6), pady=6)
            tk.Label(head, text=f"{first['opponent']}  ·  {_clock(first['kickoff'])}", bg=NAVY,
                     fg="#9fb0c8", font=self.f["tiny"]).pack(side="left")
            for n, p in enumerate(group):
                if n:
                    tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=12)
                row = tk.Frame(card, bg=CARD)
                row.pack(fill="x", padx=12, pady=4)
                tk.Label(row, text=STATUS_SHORT.get(p["status"], p["status"]),
                         bg=STATUS_COLORS[p["status"]], fg=WHITE, font=self.f["tinyb"],
                         width=11).pack(side="right")
                tk.Label(row, text=p["injury"], bg=CARD, fg=MUTED, font=self.f["tiny"],
                         width=11, anchor="e").pack(side="right", padx=6)
                tk.Label(row, text=f"{p['player']}", bg=CARD, fg=TEXT, font=self.f["bold"],
                         wraplength=190, justify="left").pack(side="left")
                tk.Label(row, text=p["position"], bg=CARD, fg=BLUE,
                         font=self.f["tinyb"]).pack(side="left", padx=6)

    # -------------------------------------------------------------- tracking
    def track(self, args, singles, parlays) -> dict:
        """Log the shown picks for the coming week and grade finished games (worker thread)."""
        season = bets.season_for(date.today())
        # Only this week's picks (or today's games) count as picks the builder made.
        this_week = args.week is None or args.week == next(iter(self.week_values.values()), None)
        with self.ledger_lock:
            ledger = tracker.load(self.ledger_path)
            if this_week:
                tracker.record_generated(ledger, bets.tracker_entries(
                    singles, parlays, args.sport, season))
            if not self.selftest:  # bets saved with Save / Save slip join the history
                try:
                    tracker.import_saved(ledger, sorted((bets.ROOT / "bets").glob("*.json")))
                except Exception:
                    pass
            try:
                tracker.regrade(ledger)
                tracker.annotate_weeks(ledger)
            except Exception:  # never let grading break the board
                pass
            tracker.save(ledger, self.ledger_path)
        return ledger

    # -------------------------------------------------------------- Bet History
    HIST_RESULTS = {"Any result": None, "Won": "won", "Lost": "lost", "Pushed": "push",
                    "Pending": "pending"}
    HIST_KINDS = {"Singles + parlays": None, "Singles": "single", "Parlays": "parlay"}

    def history_tab(self, parent) -> None:
        """The record at a glance, a search box and filters, then everything in
        dropdowns: the report card, what the model learned, and each league by team."""
        items = tracker.history(self.ledger)
        entries = [e for _, e in items]
        if not hasattr(self, "hist_open"):  # remembered while the app is open
            self.hist_open: set[str] = set()
            self.hist_query = tk.StringVar(value="")
            self.hist_league = tk.StringVar(value="All leagues")
            self.hist_result = tk.StringVar(value="Any result")
            self.hist_kind = tk.StringVar(value="Singles + parlays")
            self.hist_week = tk.StringVar(value="Any week")

        top = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        top.pack(fill="x", padx=6, pady=(8, 0))
        line = tk.Frame(top, bg=CARD)
        line.pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(line, text=self.tally(entries), bg=CARD, fg=TEXT,
                 font=self.f["bold"]).pack(side="left")
        FlatButton(line, "Check results now", self.build, "secondary",
                   self.f["tinyb"]).pack(side="right")
        for sport, name in (("nfl", "NFL"), ("ncaaf", "College")):
            group = [e for e in entries if e.get("sport", "nfl") == sport]
            self.win_bar(top, name, sum(e["status"] == "won" for e in group),
                         sum(e["status"] == "lost" for e in group), bg=CARD)
        tk.Frame(top, bg=CARD, height=8).pack()

        # Search and filters: type anything (a team, a player, "week 5", "parlay").
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=6, pady=(10, 4))
        box = tk.Frame(bar, bg=INPUT, highlightbackground=BORDER, highlightthickness=1)
        box.pack(side="left")
        tk.Label(box, text="🔍", bg=INPUT, fg=MUTED, font=self.f["small"]).pack(side="left",
                                                                             padx=(6, 0))
        search = tk.Entry(box, textvariable=self.hist_query, width=25, relief="flat",
                          bg=INPUT, fg=TEXT, font=self.f["body"], insertbackground=TEXT)
        search.pack(side="left", ipady=4, padx=(2, 6))
        hint = tk.Label(box, text="Search a team, player or week", bg=INPUT, fg=MUTED,
                        font=self.f["small"])

        def show_hint(*_):
            if self.hist_query.get() or self.root.focus_get() is search:
                hint.place_forget()
            else:
                hint.place(x=26, rely=0.5, anchor="w")
        search.bind("<FocusIn>", show_hint)
        search.bind("<FocusOut>", show_hint)
        weeks = sorted({e.get("week") for e in entries if e.get("week")}, reverse=True)
        for var, values, width in (
                (self.hist_league, ["All leagues"] + [LEAGUE_NAMES[k] for k, _ in SPORTS], 12),
                (self.hist_result, list(self.HIST_RESULTS), 9),
                (self.hist_kind, list(self.HIST_KINDS), 13),
                (self.hist_week, ["Any week"] + [f"Week {w}" for w in weeks], 8)):
            cb = ttk.Combobox(bar, textvariable=var, values=values, width=width,
                              state="readonly", font=self.f["small"])
            cb.pack(side="left", padx=(6, 0))
        clear = tk.Label(bar, text="Clear", bg=BG, fg=BLUE, cursor="hand2",
                         font=self.f["small"])
        clear.pack(side="left", padx=8)

        results = tk.Frame(parent, bg=BG)
        results.pack(fill="x")
        job = [None]

        def refresh():
            job[0] = None
            if not results.winfo_exists():
                return
            for w in results.winfo_children():
                w.destroy()
            if self.hist_filtered():
                self.history_matches(results, entries)
            else:
                self.history_tree(results, entries)
            show_hint()

        def later(*_):  # wait for a pause in typing
            if job[0] is not None:
                self.root.after_cancel(job[0])
            job[0] = self.root.after(250, refresh)

        def reset(_=None):
            self.hist_query.set("")
            for var, value in ((self.hist_league, "All leagues"), (self.hist_result, "Any result"),
                               (self.hist_kind, "Singles + parlays"),
                               (self.hist_week, "Any week")):
                var.set(value)
        clear.bind("<Button-1>", reset)
        for var in (self.hist_query, self.hist_league, self.hist_result, self.hist_kind,
                    self.hist_week):
            for trace in var.trace_info():  # only this view listens
                var.trace_remove(*trace)
            var.trace_add("write", later)
        refresh()

    def tally(self, entries) -> str:
        n = {s: sum(e["status"] == s for e in entries) for s in RESULT_STYLE}
        return (f"{n['won']} won  ·  {n['lost']} lost"
                + (f"  ·  {n['push']} pushed" if n["push"] else "") + f"  ·  {n['pending']} pending")

    def hist_filtered(self) -> bool:
        return bool(self.hist_query.get().strip()) or (
            self.hist_league.get(), self.hist_result.get(), self.hist_kind.get(),
            self.hist_week.get()) != ("All leagues", "Any result", "Singles + parlays",
                                      "Any week")

    def entry_team(self, e: dict) -> str:
        if e["kind"] != "single":
            return "Parlays"
        return tracker.leg_team(e["legs"][0], e.get("sport", "nfl"), self.player_team) or "Other"

    def history_matches(self, parent, entries) -> None:
        """Every bet matching the search and filters, newest first, in one list."""
        league = {v: k for k, v in LEAGUE_NAMES.items()}.get(self.hist_league.get())
        result = self.HIST_RESULTS[self.hist_result.get()]
        kind = self.HIST_KINDS[self.hist_kind.get()]
        week = self.hist_week.get()
        words = self.hist_query.get().lower().split()
        found = []
        for e in entries:
            sport = e.get("sport", "nfl")
            if (league and sport != league or result and e["status"] != result
                    or kind and e["kind"] != kind
                    or week != "Any week" and f"Week {e.get('week')}" != week):
                continue
            text = " ".join([LEAGUE_NAMES.get(sport, sport), self.entry_team(e), e["kind"],
                             e["status"], f"week {e.get('week')}"]
                            + [leg.get("pick") or leg["label"] for leg in e["legs"]]
                            + [leg.get("game", "") for leg in e["legs"]]).lower()
            if all(w in text for w in words):
                found.append(e)
        found.sort(key=tracker.first_kickoff, reverse=True)
        limit = 150
        tk.Label(parent, text=(f"{len(found)} bet{'s' * (len(found) != 1)} found  ·  "
                               + self.tally(found)) if found else
                 "No bets match. Try fewer words, or Clear the filters.",
                 bg=BG, fg=MUTED, font=self.f["small"]).pack(anchor="w", padx=8, pady=(4, 4))
        listing = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        if found:
            listing.pack(fill="x", padx=6)
        for e in found[:limit]:
            self.history_row(listing, e, meta=True)
        if len(found) > limit:
            tk.Label(parent, text=f"Showing the newest {limit}. Narrow the search to see the rest.",
                     bg=BG, fg=MUTED, font=self.f["tiny"]).pack(anchor="w", padx=8, pady=4)

    def history_tree(self, parent, entries) -> None:
        """Collapsed dropdowns: report card, what the model learned, then each league
        by team, each team by week."""
        self.dropdown(parent, "card", "Model report card",
                      "won vs the model's expected win rate",
                      lambda body: self.report_card(body, entries, titled=False))
        self.dropdown(parent, "learned", "What the model has learned",
                      "chances corrected by how bets like them really did",
                      lambda body: self.learned(body, titled=False))
        for sport, _ in SPORTS:
            group = [e for e in entries if e.get("sport", "nfl") == sport]
            name = LEAGUE_NAMES[sport]

            def league(body, sport=sport, group=group):
                if not group:
                    tk.Label(body, text=("No NBA bets yet: they'll show here once NBA bets "
                                         "are built." if sport == "nba" else "No bets yet."),
                             bg=BG, fg=MUTED, font=self.f["small"]).pack(anchor="w", padx=24,
                                                                       pady=4)
                    return
                by_team: dict[str, list] = {}
                for e in group:
                    by_team.setdefault(self.entry_team(e), []).append(e)
                special = (tracker.GAME_TOTALS, "Other", "Parlays")
                order = sorted(t for t in by_team if t not in special)
                order += [t for t in special if t in by_team]
                for team in order:
                    self.dropdown(body, f"{sport}/{team}", team, self.tally(by_team[team]),
                                  lambda b, these=by_team[team]: self.history_weeks(b, these),
                                  level=1)
            self.dropdown(parent, sport, name,
                          self.tally(group) if group else "no bets yet", league)

    def dropdown(self, parent, key: str, title: str, sub: str, fill, level: int = 0) -> None:
        """A header that opens and closes the section under it (built the first time
        it's opened, and remembered open)."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=(6 + 18 * level, 6), pady=(6 if level == 0 else 2, 0))
        bg = CARD if level == 0 else SUBTLE
        head = tk.Frame(wrap, bg=bg, highlightbackground=BORDER, highlightthickness=1,
                        cursor="hand2")
        head.pack(fill="x")
        arrow = tk.Label(head, text="▸", bg=bg, fg=BLUE, font=self.f["bold"], width=2)
        arrow.pack(side="left", padx=(6, 0), pady=6 if level == 0 else 3)
        tk.Label(head, text=title, bg=bg, fg=TEXT,
                 font=self.f["bold"] if level == 0 else self.f["small"] + ("bold",)
                 ).pack(side="left")
        tk.Label(head, text="   " + sub, bg=bg, fg=MUTED,
                 font=self.f["small"] if level == 0 else self.f["tiny"]).pack(side="left")
        body = tk.Frame(wrap, bg=BG)
        built = []

        def show(on: bool) -> None:
            arrow.configure(text="▾" if on else "▸")
            if on:
                if not built:
                    fill(body)
                    built.append(True)
                body.pack(fill="x", pady=(2, 4))
            else:
                body.pack_forget()

        def toggle(_=None) -> None:
            on = key not in self.hist_open
            (self.hist_open.add if on else self.hist_open.discard)(key)
            show(on)
        for w in (head, *head.winfo_children()):
            w.bind("<Button-1>", toggle)
        if key in self.hist_open:
            show(True)

    def report_card(self, parent, entries, titled: bool = True) -> None:
        """The model's report card: how often each kind of bet actually won vs how often
        the model said it would, by bet type, by confidence tier and for parlays."""
        from nfl_edge.insights import report_card

        card = report_card(entries)
        if titled:  # a dropdown names it already
            self.section_title(parent, "Model report card",
                               "settled bets: won vs the model's expected win rate")
        self.closing_line(parent, entries)
        if not any(card.values()):
            tk.Label(parent, text="No settled bets yet.", bg=BG, fg=MUTED,
                     font=self.f["small"]).pack(anchor="w", padx=6)
            return
        for key, title in (("tier", "By confidence"), ("type", "By bet type"),
                           ("parlays", "Parlays")):
            if not card[key]:
                continue
            tk.Label(parent, text=title, bg=BG, fg=TEXT, font=self.f["small"],
                     anchor="w").pack(fill="x", padx=6, pady=(4, 0))
            for name, won, n, expected, actual in card[key]:
                # Within 5 points of the model counts as on track; a handful of bets
                # can swing either way, so say how many there are.
                color = GREEN if actual >= expected - 0.05 else RED
                row = tk.Frame(parent, bg=BG)
                row.pack(fill="x", padx=18)
                tk.Label(row, text=name, bg=BG, fg=TEXT, font=self.f["small"], width=22,
                         anchor="w").pack(side="left")
                tk.Label(row, text=f"won {won} of {n} ({actual:.0%})", bg=BG, fg=color,
                         font=self.f["small"], width=20, anchor="w").pack(side="left")
                tk.Label(row, text=f"expected {expected:.0%}"
                         + ("  ·  too few to judge" if n < 10 else ""),
                         bg=BG, fg=MUTED, font=self.f["small"]).pack(side="left")

    def closing_line(self, parent, entries) -> None:
        """How often the NFL picks beat the closing line: the market moving toward them
        before kickoff is the earliest sign of a real edge."""
        try:
            from nfl_edge import clv

            rows = clv.report(entries, data.games(None))
        except Exception:
            return
        if not rows:
            return
        tk.Label(parent, text="Beat the closing line (NFL)", bg=BG, fg=TEXT,
                 font=self.f["small"], anchor="w").pack(fill="x", padx=6, pady=(4, 0))
        for kind, (n, beat, avg) in rows.items():
            gained = f"{avg * 100:+.1f} chance points" if kind == "moneyline" else f"{avg:+.1f} points"
            row = tk.Frame(parent, bg=BG)
            row.pack(fill="x", padx=18)
            tk.Label(row, text="Moneylines" if kind == "moneyline" else "Spreads", bg=BG,
                     fg=TEXT, font=self.f["small"], width=22, anchor="w").pack(side="left")
            tk.Label(row, text=f"beat it {beat} of {n} ({beat / n:.0%})", bg=BG,
                     fg=GREEN if beat / n >= 0.5 else RED, font=self.f["small"], width=20,
                     anchor="w").pack(side="left")
            tk.Label(row, text=f"average {gained}", bg=BG, fg=MUTED,
                     font=self.f["small"]).pack(side="left")

    def section_title(self, parent, title: str, sub: str) -> None:
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x", padx=6, pady=(10, 0))
        tk.Label(head, text=title, bg=BG, fg=TEXT, font=self.f["bold"]).pack(side="left")
        tk.Label(head, text="   " + sub, bg=BG, fg=MUTED,
                 font=self.f["tiny"]).pack(side="left", pady=(2, 0))
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=6)

    def learned(self, parent, titled: bool = True) -> None:
        """What the model has learned from past seasons and this season's graded picks."""
        from nfl_edge import learn

        rows = [r for r in learn.summary() if abs(r[4] - r[3]) >= 0.01 or r[2]]
        if not rows:
            tk.Label(parent, text="Nothing learned yet.", bg=BG, fg=MUTED,
                     font=self.f["small"]).pack(anchor="w", padx=18)
            return
        if titled:  # a dropdown names it already
            self.section_title(parent, "What the model has learned",
                               "chances are corrected by how bets like them really did")
        for name, past, live, said, real in rows:
            row = tk.Frame(parent, bg=BG)
            row.pack(fill="x", padx=18)
            tk.Label(row, text=name, bg=BG, fg=TEXT, font=self.f["small"], width=22,
                     anchor="w").pack(side="left")
            tk.Label(row, text=f"a {said:.0%} bet really wins about {real:.0%}", bg=BG,
                     fg=RED if real < said - 0.01 else GREEN if real > said + 0.01 else TEXT,
                     font=self.f["small"], width=30, anchor="w").pack(side="left")
            seen = [f"{past:,} past bets" if past else "", f"{live} graded this season" if live else ""]
            tk.Label(row, text=" · ".join(s for s in seen if s), bg=BG, fg=MUTED,
                     font=self.f["small"]).pack(side="left")

    def win_bar(self, parent, name: str, won: int, lost: int, bg: str = BG) -> None:
        """A league's green/red bar of settled bets won vs lost (out of 100%), filling in
        when shown."""
        box = tk.Frame(parent, bg=bg)
        box.pack(fill="x", padx=12, pady=(4, 2))
        tk.Label(box, text=name, bg=bg, fg=TEXT, font=self.f["bold"], width=8,
                 anchor="w").pack(side="left")
        total = won + lost
        if not total:
            tk.Label(box, text="No settled bets yet: the win rate shows once games finish.",
                     bg=bg, fg=MUTED, font=self.f["small"]).pack(side="left")
            return
        share = won / total
        width, height = int(PANEL_W) - 470, 18
        bar = tk.Canvas(box, width=width, height=height, bg=RED, highlightthickness=0)
        bar.pack(side="left")
        green = bar.create_rectangle(0, 0, 0, height, fill=GREEN, outline="")
        words = bar.create_text(8, height / 2, anchor="w", fill=WHITE, font=self.f["tinyb"],
                                text="")
        tk.Label(box, text=f"  {share:.0%} won ({won})  ·  {1 - share:.0%} lost ({lost})",
                 bg=bg, fg=TEXT, font=self.f["small"]).pack(side="left")

        def grow(step=0, steps=24):  # fills in over about half a second
            frac = share * min(step / steps, 1) ** 0.6
            bar.coords(green, 0, 0, width * frac, height)
            bar.itemconfigure(words, text=f"{frac:.0%} won")
            if step < steps and bar.winfo_exists():
                bar.after(20, grow, step + 1, steps)
        grow()

    def player_team(self, name: str) -> str | None:
        """A player's current NFL team code, from the stats loaded for the track records."""
        cached = getattr(self, "_players", None)
        return cached[2].current_team.get(name_key(name)) if cached else None

    def history_weeks(self, parent, entries) -> None:
        """One team's (or the parlays') bets by week, newest week first; in each week
        wins, then losses, then pushes and pending bets."""
        order = {"won": 0, "lost": 1, "push": 2, "pending": 3}
        for week in sorted({e.get("week") for e in entries}, key=lambda w: -(w or -1)):
            these = sorted((e for e in entries if e.get("week") == week),
                           key=lambda e: (order[e["status"]], tracker.first_kickoff(e)))
            head = tk.Frame(parent, bg=BG)
            head.pack(fill="x", padx=(44, 6), pady=(4, 1))
            tk.Label(head, text=f"Week {week}" if week else "Other", bg=BG, fg=TEXT,
                     font=self.f["tinyb"]).pack(side="left")
            tk.Label(head, text="   " + self.tally(these), bg=BG, fg=MUTED,
                     font=self.f["tiny"]).pack(side="left")
            listing = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            listing.pack(fill="x", padx=(44, 6))
            for e in these:
                self.history_row(listing, e)

    def history_row(self, parent, e: dict, meta: bool = False) -> None:
        """One line: the result, the bet, its chance (and with `meta` its league, team
        and week, for search results)."""
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x")
        tk.Frame(row, bg=BORDER, height=1).pack(fill="x", side="bottom")
        word, color = {"won": ("WON", GREEN), "lost": ("LOST", RED), "push": ("PUSH", "#8a96a8"),
                       "pending": ("PENDING", BLUE)}[e["status"]]
        tk.Label(row, text=word, bg=color if e["status"] != "pending" else CARD,
                 fg=WHITE if e["status"] != "pending" else BLUE, font=self.f["tinyb"],
                 width=8).pack(side="left", padx=(8, 8), pady=5)
        tk.Label(row, text=f"{e['win_prob']:.0%}", bg=CARD, fg=MUTED, font=self.f["bold"],
                 width=5, anchor="e").pack(side="right", padx=(4, 10))
        text = tk.Frame(row, bg=CARD)
        text.pack(side="left", fill="x", expand=True, pady=3)
        legs = [leg.get("pick") or leg["label"] for leg in e["legs"]]
        title = legs[0] if e["kind"] == "single" else f"{len(legs)}-leg parlay"
        tk.Label(text, text=title, bg=CARD, fg=TEXT, font=self.f["tinyb"],
                 anchor="w").pack(fill="x")
        details = [] if e["kind"] == "single" else ["  ·  ".join(legs)]
        if meta:
            details.append("  ·  ".join(x for x in (
                LEAGUE_NAMES.get(e.get("sport", "nfl"), ""),
                f"Week {e['week']}" if e.get("week") else "",
                self.entry_team(e) if e["kind"] == "single" else "") if x))
        for d in details:
            tk.Label(text, text=d, bg=CARD, fg=MUTED, font=self.f["tiny"], anchor="w",
                     justify="left", wraplength=640).pack(fill="x")

    def card(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="x", pady=3)
        return frame

    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            sport=self.sport.get(),
            date="today" if self.when.get() == "Today's games" else "week",
            week=self.week_values.get(self.when.get()),
            singles=int(self.n_singles.get() or 30),
            legs=int(self.min_legs.get() or 3), min_legs=int(self.min_legs.get() or 3),
            max_legs=int(self.max_legs.get() or 5), parlays=int(self.n_parlays.get() or 0),
            stake=float(self.stake.get() or 10), min_prob=bets.MIN_PROB,
            max_prob=bets.MAX_PROB, per_game=3, allow_overlap=False, games_file=None,
            bet_type=self.bet_type.get(),
            pays=float(self.pays.get()) if self.pays.get().strip() else None)

    def build(self) -> None:
        if self.sport.get() == "nba":  # a preview for now: nothing to build
            self.render_nba()
            return
        try:
            args = self.args()
        except ValueError:
            self.say("Please use numbers for singles, legs, parlays, wager and payout.")
            return
        self.build_btn.configure(text="Loading...")
        self.cancel_update()
        self.countdown.set("Updating...")

        def work():
            try:
                from nfl_edge import learn

                learn.remember(tracker.all_entries(self.ledger))  # learn from graded picks
                pool, singles, parlays = bets.build(args)
                # Money makers never lean on a Weak (red) leg.
                args.money_makers = money_maker_parlays(
                    [b for b in pool if getattr(b, "tier", "yellow") != "red"], 10, parlays)
                args.hit_rates = self.hit_rates(
                    args, singles + [b for p in parlays + args.money_makers for b in p.legs])
                injuries = (None, [], "College injury reports aren't in the free data.")
                if args.sport == "nfl" and args.date != "today":
                    injuries = board.injury_report(args.week)
                self.results.put(("ok", args, singles, parlays, injuries,
                                  self.track(args, singles, parlays + args.money_makers)))
            except (data.DataError, ValueError) as e:
                self.results.put(("error", str(e)))
            except Exception as e:  # never leave the window stuck on "Loading..."
                traceback.print_exc()
                self.results.put(("error", f"Something went wrong building bets ({e}). "
                                           "Try Build bets again."))

        threading.Thread(target=work, daemon=True).start()

    def minutes(self) -> float:
        try:
            return max(float(self.refresh.get() or 0), 0)
        except ValueError:
            return 0

    def cancel_update(self) -> None:
        if self.next_job is not None:
            self.root.after_cancel(self.next_job)
        self.next_job = self.next_at = None

    def schedule(self) -> None:
        """Queue the next automatic update, Refresh minutes from now (0 = off)."""
        if self.build_btn.cget("text") == "Loading...":
            return  # an update is running; it schedules the next one when done
        self.cancel_update()
        minutes = self.minutes()
        if minutes > 0:
            # Downloads must be at least this fresh, injury reports included.
            data.FRESH_SECONDS = min(data.FRESH_SECONDS, minutes * 60)
            board.INJURY_MAX_AGE = min(board.INJURY_MAX_AGE, minutes * 60)
            self.next_at = time.monotonic() + minutes * 60
            self.next_job = self.root.after(int(minutes * 60_000), self.build)
        self.tick(repeat=False)

    def tick(self, repeat: bool = True) -> None:
        """Show the countdown to the next automatic update."""
        if self.updating:
            self.countdown.set(self.updating)
        elif self.sport.get() == "nba":
            self.countdown.set("NBA preview")
        elif self.next_at is not None:
            left = max(int(self.next_at - time.monotonic() + 0.999), 0)
            self.countdown.set(f"⟳  Next update in {left // 60}:{left % 60:02d}")
        elif self.build_btn.cget("text") != "Loading...":
            self.countdown.set("Auto-update off")
        if repeat:
            self.root.after(1000, self.tick)

    def poll(self) -> None:
        try:
            item = self.results.get_nowait()
        except queue.Empty:
            self.root.after(150, self.poll)
            return
        self.build_btn.configure(text="Build bets")
        if item[0] == "error":
            self.say(f"Couldn't load data: {item[1]}")
        else:
            _, args, self.singles, self.parlays, self.injuries, self.ledger = item
            self.money = getattr(args, "money_makers", [])
            if self.last_args and (self.last_args.sport, self.last_args.week) != (args.sport, args.week):
                self.previous = {}  # a different week or sport: nothing is "new" or "moved"
            self.last_args = args
            if self.sport.get() != "nba":  # switched to the NBA preview meanwhile
                self.render(args, self.singles, self.parlays, self.previous)
            self.previous = {bets.label(b): b.fair_prob for b in self.singles}
        self.schedule()
        if self.selftest:
            self.show_tab("money")
            print(f"{len(self.singles)} singles, {len(self.parlays)} parlays, "
                  f"{len(self.money)} money makers rendered")
            from nfl_edge import learn

            from nfl_edge import signals

            if not learn.saved().get("groups") or not signals.saved():  # must ship in the build
                print("selftest FAILED: no learned calibration or signal weights")
                os._exit(1)
            self.root.after(500, self.root.destroy)
            return
        self.root.after(150, self.poll)


class Updates:
    """Checks GitHub for a newer build of the app and installs it (see nfl_edge/updater.py)."""

    def __init__(self, app: "App"):
        self.app = app
        app.updates = self
        if not app.selftest:
            threading.Thread(target=updater.cleanup, daemon=True).start()  # last update's leftovers
        if updater.can_update() and not app.selftest:
            app.root.after(5000, self.check)

    def _background(self, work, done) -> None:
        """Run work() off the window's thread; call done(kind, value) back on it."""
        q: queue.Queue = queue.Queue()

        def run():
            try:
                q.put(("ok", work(q)))
            except updater.UpdateError as e:
                q.put(("error", str(e)))

        def wait():
            try:
                while True:
                    kind, value = q.get_nowait()
                    if kind != "progress":
                        done(kind, value)
                        return
                    self.app.updating = f"Downloading update…  {value:.0%}"
                    self.app.countdown.set(self.app.updating)
            except queue.Empty:
                self.app.root.after(300, wait)

        threading.Thread(target=run, daemon=True).start()
        wait()

    def check(self, manual: bool = False) -> None:
        """Look for a newer build: on a timer (quiet unless there is one), or from the
        Check for updates button (always says what it found)."""
        from tkinter import messagebox

        if manual and self.app.updating:
            return  # an update is already installing
        if not manual:
            def done(kind, update):
                if kind == "ok" and update and update.build > self.app.offered:
                    self.offer(update)
                self.app.root.after(updater.CHECK_HOURS * 3_600_000, self.check)
            self._background(lambda q: updater.available(), done)
            return

        self.app.verdict.configure(text="Checking for updates…", fg=WHITE)
        platform = sys.platform if sys.platform in updater.ASSETS else "win32"

        def done(kind, found):
            self.app.verdict.configure(text="")
            if kind == "error":
                messagebox.showerror("Check for updates", f"Couldn't check for updates: {found}."
                                     "\n\nCheck your internet connection and try again.",
                                     parent=self.app.root)
            elif not updater.can_update():
                newest = f"The newest build is {found.build}." if found else ""
                messagebox.showinfo("Check for updates",
                                    "This copy is running from its source code, so it doesn't "
                                    f"update itself. {newest}\n\nDownload the app from "
                                    "github.com/levishoaf/Claudecode/releases/latest.",
                                    parent=self.app.root)
            elif found and found.build > updater.BUILD:
                self.offer(found)
            else:
                messagebox.showinfo("Check for updates", "You're on the latest version "
                                    f"(build {updater.BUILD}).", parent=self.app.root)
        self._background(lambda q: updater.latest(platform), done)

    def offer(self, update) -> None:
        from tkinter import messagebox

        self.app.offered = update.build
        if not messagebox.askyesno(
                "Update available",
                f"A new version of Bet Builder is ready (build {update.build}; you have "
                f"build {updater.BUILD}).\n\nInstall it now? The app closes and reopens by "
                "itself in a few seconds. Your bets and settings are kept.",
                parent=self.app.root):
            return
        self.app.cancel_update()
        self.app.next_at = None
        self.app.updating = "Downloading update…"
        self.app.countdown.set(self.app.updating)

        def work(q):
            updater.install(update, progress=lambda d, t: q.put(("progress", d / t if t else 0)))

        def done(kind, value):
            if kind == "ok":
                self.app.updating = "Restarting with the new version…"
                self.app.countdown.set(self.app.updating)
                self.app.root.after(800, self.app.root.destroy)
                return
            self.app.updating = None
            messagebox.showerror("Update failed", f"The update didn't install: {value}.\n\n"
                                 "You can keep using this version; it will try again later.",
                                 parent=self.app.root)
            self.app.schedule()
        self._background(work, done)


def check_update_cli() -> int:
    """--check-update: print this build and the newest one on GitHub."""
    print(f"This build: {updater.BUILD or 'running from source'}")
    try:
        found = updater.latest(sys.platform if sys.platform in updater.ASSETS else "win32")
    except updater.UpdateError as e:
        print(f"Couldn't check: {e}")
        return 0
    print(f"Newest build: {found.build} ({found.url})" if found else "No builds published yet.")
    return 0


def main() -> int:
    # A windowed build has no console; give stray prints somewhere to go.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    if "--check-update" in sys.argv:
        return check_update_cli()
    selftest = "--selftest" in sys.argv
    root = tk.Tk()
    Updates(App(root, selftest=selftest))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
