#!/usr/bin/env python3
"""Windowed bet builder in a modern sportsbook style: the same picks as
bets.py, on a charcoal football field. No API key needed.

    python3 bets_gui.py
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import queue
import tempfile
import sys
import threading
import traceback
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
NUMBER_ROWS = (163, 752)  # yard numbers sit in the gaps between panels

# Field (charcoal)
TURF = ("#2b2b2e", "#252528")
ENDZONE = "#1c1c1f"
CHALK, CHALK_SOFT = "#8e8e94", "#5c5c62"

# Sportsbook-style UI
NAVY, NAVY_2 = "#0c1b33", "#13284a"
BLUE, BLUE_DARK, BLUE_SOFT = "#1677ff", "#0f5fd6", "#e8f1ff"
BG, CARD, BORDER = "#eef1f5", "#ffffff", "#d9e0e8"
TEXT, MUTED, WHITE = "#0d1b2a", "#5b6b7f", "#ffffff"
GREEN, AMBER, RED, TRACK = "#22a352", "#e0a100", "#d64545", "#e3e8ef"
NEW_BADGE, MOVED_BADGE = "#1677ff", "#e07b00"
# Bets you placed and picks you didn't, graded after the games.
LEDGER = Path.home() / "Bet Builder" / "my bets.json"
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


def draw_field(c: tk.Canvas, w: float = W, h: float = H) -> None:
    """An original football field: end zones, 5-yard stripes, yard lines,
    hash marks and yard numbers, drawn to fill the window (redrawn when it's resized)."""
    W, H = w, h  # noqa: N806  (the drawing below is in window units)
    zone = W * 0.08
    yard = (W - 2 * zone) / 100
    c.create_rectangle(0, 0, W, H, fill=TURF[0], outline="", tags="field")
    for i in range(20):
        x0 = zone + i * 5 * yard
        c.create_rectangle(x0, 0, x0 + 5 * yard, H, fill=TURF[i % 2], outline="", tags="field")
    for x0 in (0, W - zone):
        c.create_rectangle(x0, 0, x0 + zone, H, fill=ENDZONE, outline="", tags="field")
    for side, x in ((90, zone / 2), (-90, W - zone / 2)):
        c.create_text(x, H / 2, text="BET  BUILDER", angle=side, fill="#6e6e74",
                      font=("Helvetica", 30, "bold"), tags="field")
    for i in range(0, 101, 5):
        x = zone + i * yard
        c.create_line(x, 0, x, H, fill=CHALK if i % 10 == 0 else CHALK_SOFT,
                      width=3 if i % 10 == 0 else 1, tags="field")
    for i in range(1, 100):
        x = zone + i * yard
        for y in (H * 0.36, H * 0.64):
            c.create_line(x, y - 5, x, y + 5, fill=CHALK_SOFT, tags="field")
    for i in range(10, 100, 10):
        x = zone + i * yard
        n = str(i if i <= 50 else 100 - i)
        for y, ang in zip((NUMBER_ROWS[0], H - (870 - NUMBER_ROWS[1])), (180, 0)):
            c.create_text(x, y, text=n, fill=CHALK, angle=ang, font=("Helvetica", 24, "bold"), tags="field")


class FlatButton(tk.Label):
    """A flat, modern button with a hover color."""

    def __init__(self, parent, text, command, kind="primary", font=None, **kw):
        colors = {"primary": (BLUE, WHITE, BLUE_DARK), "secondary": (WHITE, BLUE, BLUE_SOFT),
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

        c = tk.Canvas(root, width=W, height=H, highlightthickness=0)
        c.pack(fill="both", expand=True)
        draw_field(c)
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
            s.configure(name, fieldbackground=WHITE, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=WHITE, darkcolor=WHITE, insertcolor=TEXT, padding=5,
                        arrowcolor=BLUE, background=WHITE)
        # Keep a chosen week readable while the box has focus.
        s.map("TCombobox", fieldbackground=[("readonly", WHITE)],
              selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", TEXT)],
              foreground=[("readonly", TEXT)])
        s.configure("Vertical.TScrollbar", troughcolor=BG, background="#c3ccd8",
                    bordercolor=BG, arrowcolor=MUTED, lightcolor="#c3ccd8", darkcolor="#c3ccd8")

    def header(self, c: tk.Canvas) -> None:
        top = tk.Frame(c, bg=NAVY)
        row1 = tk.Frame(top, bg=NAVY)
        row1.pack(fill="x", padx=18, pady=(12, 4))
        tk.Label(row1, text="Bet Builder", bg=NAVY, fg=WHITE, font=self.f["title"]).pack(side="left")
        self.countdown = tk.StringVar(value="")
        tk.Label(row1, text="   Free data  ·  break-even odds", bg=NAVY, fg="#9fb0c8",
                 font=self.f["small"]).pack(side="left")

        self.sport = tk.StringVar(value="nfl")
        seg = tk.Frame(row1, bg=NAVY_2)
        seg.pack(side="right")
        self.seg_btns = {}
        for key, text in (("nfl", "NFL"), ("ncaaf", "College")):
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
        c.delete("field")
        draw_field(c, w, h)
        c.tag_lower("field")
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
            self.load_weeks()
            self.build()

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
        tk.Label(chk, text="Check FanDuel's price", bg=NAVY, fg=WHITE,
                 font=self.f["bold"]).pack(side="left", padx=(0, 8))
        self.check_entry = ttk.Entry(chk, width=9, font=self.f["body"])
        self.check_entry.pack(side="left")
        self.check_entry.bind("<Return>", lambda e: self.check())
        FlatButton(chk, "Check", self.check, "primary", self.f["bold"]).pack(side="left", padx=8)
        FlatButton(chk, "Grade saved bets", self.grade, "secondary",
                   self.f["bold"]).pack(side="right")
        FlatButton(chk, "Check for updates", lambda: self.updates and self.updates.check(manual=True),
                   "secondary", self.f["bold"]).pack(side="right", padx=(0, 8))
        self.verdict = tk.Label(chk, text="e.g. 3 -150 or P1 +240", bg=NAVY, fg="#9fb0c8",
                                anchor="w", justify="left", font=self.f["small"],
                                wraplength=int(PANEL_W) - 580)
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
        sport = "NFL" if args.sport == "nfl" else "College Football"
        tk.Label(head, text=f"{sport}  ·  {bets.week_label(singles, parlays)}", bg=BG, fg=TEXT,
                 font=self.f["h2"]).pack(side="left")
        checked = getattr(args, "fanduel_key", None)
        order = "best value first" if checked else "likeliest first"
        kind = getattr(args, "bet_type", "All bets")
        tk.Label(head, text=f"   {'' if kind == 'All bets' else kind + ' only, '}{order}"
                            "",
                 bg=BG, fg=MUTED, font=self.f["small"]).pack(side="left", pady=(3, 0))
        self.fd_mode = bool(getattr(args, "fanduel_key", None))
        if self.fd_mode and board.NOTES.get("fanduel"):
            tk.Label(self.body, text="✓ " + board.NOTES["fanduel"] + "  ·  ranked by expected value",
                     bg=BG, fg=BLUE,
                     font=self.f["small"]).pack(anchor="w", padx=16)
        if board.NOTES.get("confirm_line"):
            tk.Label(self.body, text="⚠ " + board.NOTES["confirm_line"], bg=BG, fg=MOVED_BADGE,
                     font=self.f["small"], wraplength=820, justify="left").pack(anchor="w", padx=16)
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
                          ("injuries", f"Injuries ({len(self.injuries[1])})"),
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
        if self.tab == "singles":
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
        tk.Label(self.body, text=("Every bet here was found on FanDuel; odds are FanDuel's from "
                                  "the last check. EV is the expected profit per $1." if self.fd_mode else
                                  "Odds shown are break-even: bet only if FanDuel pays that or "
                                  "better. Check any price at the bottom."),
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
        head = tk.Frame(table, bg="#f6f8fb")
        head.pack(fill="x")
        self.grid_cols(head)
        fd = getattr(self, "fd_mode", False)
        last_text = "PAYS" if last == "pays" else (last or "")
        for col, text, anchor in ((0, first, "w"), (1, "BET", "w"), (2, "CHANCE", "e"),
                                  (3, "ODDS" if fd else "WORST ODDS", "e"), (4, last_text, "e")):
            tk.Label(head, text=text, bg="#f6f8fb", fg=MUTED, font=self.f["label"]).grid(
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

    def history_tab(self, parent) -> None:
        items = tracker.history(self.ledger)

        def tally(group) -> str:
            n = {s: sum(e["status"] == s for _, e in group) for s in RESULT_STYLE}
            return (f"{n['won']} won  ·  {n['lost']} lost"
                    + (f"  ·  {n['push']} pushed" if n["push"] else "") + f"  ·  {n['pending']} pending")

        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x", padx=6, pady=(6, 0))
        tk.Label(top, text=tally(items), bg=BG, fg=TEXT, font=self.f["bold"]).pack(side="left")
        FlatButton(top, "Check results now", self.build, "secondary",
                   self.f["tinyb"]).pack(side="right")
        # Win rate of settled bets, a bar per league, under the overall record.
        for sport, name in (("nfl", "NFL"), ("ncaaf", "College")):
            group = [e for _, e in items if e.get("sport", "nfl") == sport]
            self.win_bar(parent, name, sum(e["status"] == "won" for e in group),
                         sum(e["status"] == "lost" for e in group))
        self.report_card(parent, [e for _, e in items])
        for sport, name in (("nfl", "NFL"), ("ncaaf", "COLLEGE FOOTBALL")):
            group = [x for x in items if x[1].get("sport", "nfl") == sport]
            head = tk.Frame(parent, bg=BG)
            head.pack(fill="x", padx=6, pady=(10, 0))
            tk.Label(head, text=name, bg=BG, fg=NAVY, font=self.f["h2"]).pack(side="left")
            tk.Label(head, text="   " + tally(group), bg=BG, fg=MUTED,
                     font=self.f["small"]).pack(side="left", pady=(3, 0))
            tk.Frame(parent, bg=BLUE, height=2).pack(fill="x", padx=6, pady=(0, 4))
            if not group:
                tk.Label(parent, text="No bets yet.", bg=BG, fg=MUTED,
                         font=self.f["small"]).pack(anchor="w", padx=6)
                continue
            # Single bets by team (the team bet on, or the player's team), then game
            # totals; parlays cover several teams, so they get their own section.
            by_team: dict[str, list] = {}
            for x in group:
                if x[1]["kind"] == "single":
                    team = tracker.leg_team(x[1]["legs"][0], sport, self.player_team) or "Other"
                else:
                    team = "Parlays"
                by_team.setdefault(team, []).append(x)
            order = sorted(t for t in by_team if t not in (tracker.GAME_TOTALS, "Other", "Parlays"))
            order += [t for t in (tracker.GAME_TOTALS, "Other", "Parlays") if t in by_team]
            for team in order:
                th = tk.Frame(parent, bg=BG)
                th.pack(fill="x", padx=6, pady=(10, 0))
                tk.Label(th, text=team, bg=BG, fg=TEXT, font=self.f["bold"]).pack(side="left")
                tk.Label(th, text="   " + tally(by_team[team]), bg=BG, fg=MUTED,
                         font=self.f["tiny"]).pack(side="left", pady=(2, 0))
                tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=6)
                self.history_weeks(parent, by_team[team], tally)

    def report_card(self, parent, entries) -> None:
        """The model's report card: how often each kind of bet actually won vs how often
        the model said it would, by bet type, by confidence tier and for parlays."""
        from nfl_edge.insights import report_card

        card = report_card(entries)
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x", padx=6, pady=(10, 0))
        tk.Label(head, text="Model report card", bg=BG, fg=NAVY,
                 font=self.f["bold"]).pack(side="left")
        tk.Label(head, text="   settled bets: won vs the model's expected win rate",
                 bg=BG, fg=MUTED, font=self.f["tiny"]).pack(side="left", pady=(2, 0))
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=6)
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

    def win_bar(self, parent, name: str, won: int, lost: int) -> None:
        """A league's green/red bar of settled bets won vs lost (out of 100%), filling in
        when shown."""
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="x", padx=6, pady=(4, 2))
        tk.Label(box, text=name, bg=BG, fg=TEXT, font=self.f["bold"], width=8,
                 anchor="w").pack(side="left")
        total = won + lost
        if not total:
            tk.Label(box, text="No settled bets yet: the win rate shows once games finish.",
                     bg=BG, fg=MUTED, font=self.f["small"]).pack(side="left")
            return
        share = won / total
        width, height = int(PANEL_W) - 420, 18
        bar = tk.Canvas(box, width=width, height=height, bg=RED, highlightthickness=0)
        bar.pack(side="left")
        green = bar.create_rectangle(0, 0, 0, height, fill=GREEN, outline="")
        words = bar.create_text(8, height / 2, anchor="w", fill=WHITE, font=self.f["tinyb"],
                                text="")
        tk.Label(box, text=f"  {share:.0%} won ({won})  ·  {1 - share:.0%} lost ({lost})",
                 bg=BG, fg=TEXT, font=self.f["small"]).pack(side="left")

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

    def history_weeks(self, parent, items, tally) -> None:
        """One team's (or the parlays') bets by week; in each week wins, then losses,
        then pushes and pending bets."""
        weeks = sorted({e.get("week") for _, e in items}, key=lambda w: -(w or -1))
        for week in weeks:
            in_week = [x for x in items if x[1].get("week") == week]
            wk = tk.Frame(parent, bg=BG)
            wk.pack(fill="x", padx=12, pady=(4, 0))
            tk.Label(wk, text=f"Week {week}" if week else "Other", bg=BG, fg=TEXT,
                     font=self.f["small"]).pack(side="left")
            tk.Label(wk, text="   " + tally(in_week), bg=BG, fg=MUTED,
                     font=self.f["tiny"]).pack(side="left", pady=(1, 0))
            for status, label in (("won", "WON"), ("lost", "LOST"), ("push", "PUSHED"),
                                  ("pending", "PENDING")):
                these = sorted((e for _, e in in_week if e["status"] == status),
                               key=tracker.first_kickoff, reverse=status != "pending")
                if not these:
                    continue
                tk.Label(parent, text=f"{label}  ({len(these)})", bg=BG,
                         fg=RESULT_STYLE[status][1], font=self.f["tinyb"]).pack(
                    anchor="w", padx=18, pady=(3, 0))
                holder = tk.Frame(parent, bg=BG)
                holder.pack(fill="x")
                cols = self.columns(holder, 3, CARD_W)
                heights = [0, 0, 0]
                for e in these:  # fill the shortest column, so there are no gaps
                    col = heights.index(min(heights))
                    heights[col] += 1 + (len(e["legs"]) if e["kind"] == "parlay" else 0) // 2
                    self.history_row(cols[col], e)

    def history_row(self, parent, e: dict) -> None:
        """One compact line: the bet, its chance, and WON/LOST once it's settled."""
        row = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        row.pack(fill="x", pady=2)
        if e["status"] in ("won", "lost", "push"):
            word = {"won": "WON", "lost": "LOST", "push": "PUSH"}[e["status"]]
            tk.Label(row, text=f" {word} ", bg=RESULT_STYLE[e["status"]][1], fg=WHITE,
                     font=self.f["tinyb"]).pack(side="right", padx=(0, 6), pady=4)
        tk.Label(row, text=f"{e['win_prob']:.0%}", bg=CARD, fg=MUTED,
                 font=self.f["bold"]).pack(side="right", padx=6)
        text = tk.Frame(row, bg=CARD)
        text.pack(side="left", fill="x", padx=(8, 0), pady=3)
        if e["kind"] == "single":
            tk.Label(text, text=e["legs"][0].get("pick") or e["legs"][0]["label"], bg=CARD,
                     fg=TEXT, font=self.f["tinyb"], wraplength=170, justify="left").pack(anchor="w")
        else:
            tk.Label(text, text=f"{len(e['legs'])}-leg parlay", bg=CARD, fg=TEXT,
                     font=self.f["tinyb"]).pack(anchor="w")
            tk.Label(text, text="  ·  ".join(leg.get("pick") or leg["label"] for leg in e["legs"]),
                     bg=CARD, fg=MUTED, font=self.f["tiny"], wraplength=170,
                     justify="left").pack(anchor="w")

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
            self.render(args, self.singles, self.parlays, self.previous)
            self.previous = {bets.label(b): b.fair_prob for b in self.singles}
        self.schedule()
        if self.selftest:
            self.show_tab("money")
            print(f"{len(self.singles)} singles, {len(self.parlays)} parlays, "
                  f"{len(self.money)} money makers rendered")
            self.root.after(500, self.root.destroy)
            return
        self.root.after(150, self.poll)

    def check(self) -> None:
        entry = self.check_entry.get().strip()
        if not self.singles and not self.parlays:
            self.verdict.configure(text="Build bets first.")
            return
        self.verdict.configure(text=bets.check_price(self.singles, self.parlays, entry), fg=WHITE)

    def grade(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bets.grade_saved("all")
        self.say(buf.getvalue().strip() or "No saved bets yet.")


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
