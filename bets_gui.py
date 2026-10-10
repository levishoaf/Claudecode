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
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import date, datetime
from pathlib import Path
from tkinter import ttk

import bets  # sets up paths for the standalone build
from nfl_edge import board, data, fanduel, fdfeed, tracker
from nfl_edge.cli import _fmt_american, _slip_market, _slip_selection, _slip_time
from nfl_edge.cli import clock as _clock
from nfl_edge.odds import decimal_to_american
from nfl_edge.parlays import Parlay

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


def draw_field(c: tk.Canvas) -> None:
    """An original football field: end zones, 5-yard stripes, yard lines,
    hash marks and yard numbers, drawn to fill the window."""
    zone = W * 0.08
    yard = (W - 2 * zone) / 100
    c.create_rectangle(0, 0, W, H, fill=TURF[0], outline="")
    for i in range(20):
        x0 = zone + i * 5 * yard
        c.create_rectangle(x0, 0, x0 + 5 * yard, H, fill=TURF[i % 2], outline="")
    for x0 in (0, W - zone):
        c.create_rectangle(x0, 0, x0 + zone, H, fill=ENDZONE, outline="")
    for side, x in ((90, zone / 2), (-90, W - zone / 2)):
        c.create_text(x, H / 2, text="BET  BUILDER", angle=side, fill="#6e6e74",
                      font=("Helvetica", 30, "bold"))
    for i in range(0, 101, 5):
        x = zone + i * yard
        c.create_line(x, 0, x, H, fill=CHALK if i % 10 == 0 else CHALK_SOFT,
                      width=3 if i % 10 == 0 else 1)
    for i in range(1, 100):
        x = zone + i * yard
        for y in (H * 0.36, H * 0.64):
            c.create_line(x, y - 5, x, y + 5, fill=CHALK_SOFT)
    for i in range(10, 100, 10):
        x = zone + i * yard
        n = str(i if i <= 50 else 100 - i)
        for y, ang in zip(NUMBER_ROWS, (180, 0)):
            c.create_text(x, y, text=n, fill=CHALK, angle=ang, font=("Helvetica", 24, "bold"))


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
        self.picked: set[str] = set()  # labels of singles on your slip
        self.picked_parlays: set[tuple] = set()  # parlays on your slip
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
        root.title("Bet Builder by Levi Shoaf")
        root.geometry(f"{W}x{H}")
        root.resizable(False, False)

        fam = pick_font()
        self.f = {k: (fam, size, *style) for k, (size, *style) in {
            "title": (19, "bold"), "h2": (13, "bold"), "body": (11,), "bold": (11, "bold"),
            "pick": (12, "bold"), "small": (9,), "tiny": (8,), "tinyb": (8, "bold"),
            "odds": (15, "bold"), "label": (9, "bold")}.items()}
        self.style()

        c = tk.Canvas(root, width=W, height=H, highlightthickness=0)
        c.pack(fill="both", expand=True)
        draw_field(c)
        c.create_text(W - 12, H - 8, anchor="se", text="Levi Shoaf", fill="#8a8f99",
                      font=self.f["tinyb"])  # owner watermark
        self.header(c)
        self.output(c)
        self.checker(c)

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
        self.fanduel_on = bets.fanduel_state() is not None  # on once a state is chosen
        self.mode_lbl = tk.Label(row1, text="", bg=NAVY, fg="#9fb0c8", font=self.f["small"])
        self.mode_lbl.pack(side="left")

        self.sport = tk.StringVar(value="nfl")
        seg = tk.Frame(row1, bg=NAVY_2)
        seg.pack(side="right")
        self.seg_btns = {}
        for key, text in (("nfl", "NFL"), ("ncaaf", "College")):
            b = FlatButton(seg, text, lambda k=key: self.pick_sport(k), "ghost", self.f["bold"])
            b.pack(side="left")
            self.seg_btns[key] = b
        self.fd_btn = FlatButton(row1, "", self.toggle_fanduel, "secondary", self.f["tinyb"])
        self.fd_btn.pack(side="right", padx=(0, 10))
        self.show_mode()
        tk.Label(row1, textvariable=self.countdown, bg=NAVY, fg=WHITE,
                 font=self.f["bold"]).pack(side="right", padx=(0, 14))
        self.pick_sport("nfl")

        row2 = tk.Frame(top, bg=NAVY)
        row2.pack(fill="x", padx=18, pady=(6, 14))
        self.when = tk.StringVar(value="Loading weeks...")
        self.n_singles, self.n_parlays = tk.StringVar(value="30"), tk.StringVar(value="10")
        self.min_legs, self.max_legs = tk.StringVar(value="3"), tk.StringVar(value="5")
        self.stake, self.lo, self.hi, self.refresh = (tk.StringVar(value=v)
                                                       for v in ("10", "60", "80", "5"))

        def field(label, var, width, values=None):
            box = tk.Frame(row2, bg=NAVY)
            box.pack(side="left", padx=(0, 8))
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
            box.pack(side="left", padx=(0, 8))
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
        field("Singles", self.n_singles, 3)
        field("Parlays", self.n_parlays, 3)
        pair("Legs", self.min_legs, self.max_legs)
        field("Wager $", self.stake, 4)
        pair("Chance %", self.lo, self.hi)
        field("Refresh", self.refresh, 3)
        self.refresh.trace_add("write", lambda *_: self.schedule())

        btns = tk.Frame(row2, bg=NAVY)
        btns.pack(side="right", anchor="s")
        self.build_btn = FlatButton(btns, "Build bets", self.build, "primary", self.f["bold"])
        self.build_btn.pack(side="left", padx=(0, 6))
        FlatButton(btns, "Save", self.save, "secondary", self.f["bold"]).pack(side="left")
        c.create_window(W / 2, 72, window=top, width=PANEL_W)

    def show_mode(self) -> None:
        on = self.fanduel_on
        state = (bets.fanduel_state() or "").upper()
        self.fd_btn.configure(text=f" FanDuel check: On ({state}) " if on else " FanDuel check: Off ")
        self.fd_btn.set_kind("primary" if on else "ghost")
        self.mode_lbl.configure(text="" if on else "   Free data  ·  break-even odds")

    def toggle_fanduel(self) -> None:
        if not self.fanduel_on and bets.fanduel_state() is None:
            from tkinter import simpledialog

            state = simpledialog.askstring(
                "FanDuel check",
                "Check every bet against FanDuel's own site and show only the ones it lists,\n"
                "at FanDuel's odds. Which state do you bet in? (two letters, e.g. NJ)\n\n"
                "This reads the odds FanDuel's website loads. It's unofficial: FanDuel may\n"
                "change or block it, and automated reading may go against its terms.",
                parent=self.root)
            if not state or state.strip().lower() not in fdfeed.STATES:
                if state:
                    self.verdict.configure(text=f"FanDuel isn't available in {state.strip().upper()}.",
                                           fg=WHITE)
                return
            bets.save_fanduel_state(state)
        self.fanduel_on = not self.fanduel_on
        self.show_mode()
        self.build()

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
        c.create_window(W / 2, 458, window=out, width=PANEL_W, height=532)

    def checker(self, c: tk.Canvas) -> None:
        bottom = tk.Frame(c, bg=NAVY)
        slip = tk.Frame(bottom, bg=NAVY)
        slip.pack(fill="x", padx=14, pady=(8, 2))
        tk.Label(slip, text="Your slip", bg=NAVY, fg=WHITE, font=self.f["bold"]).pack(side="left")
        self.slip_summary = tk.Label(slip, text="Click any bet card to add it.", bg=NAVY,
                                     fg="#9fb0c8", font=self.f["small"])
        self.slip_summary.pack(side="left", padx=10)
        for text, cmd, kind in (("Clear", self.clear_slip, "secondary"),
                                ("Save slip", self.save, "secondary"),
                                ("View slip", self.view_slip, "primary")):
            FlatButton(slip, text, cmd, kind, self.f["bold"]).pack(side="right", padx=(6, 0))
        self.view_btn_row = slip

        chk = tk.Frame(bottom, bg=NAVY)
        chk.pack(fill="x", padx=14, pady=(4, 8))
        tk.Label(chk, text="Check FanDuel's price", bg=NAVY, fg=WHITE,
                 font=self.f["bold"]).pack(side="left", padx=(0, 8))
        self.check_entry = ttk.Entry(chk, width=12, font=self.f["body"])
        self.check_entry.pack(side="left")
        self.check_entry.bind("<Return>", lambda e: self.check())
        FlatButton(chk, "Check", self.check, "primary", self.f["bold"]).pack(side="left", padx=8)
        FlatButton(chk, "Grade saved bets", self.grade, "secondary",
                   self.f["bold"]).pack(side="right")
        self.verdict = tk.Label(chk, text="e.g.  3 -150   or   P1 +240", bg=NAVY, fg="#9fb0c8",
                                anchor="w", justify="left", font=self.f["small"],
                                wraplength=int(PANEL_W) - 580)
        self.verdict.pack(side="left", fill="x", expand=True, padx=6)
        c.create_window(W / 2, 818, window=bottom, width=PANEL_W, height=96)

    # -------------------------------------------------------------- slip
    def clickable(self, widget, command) -> None:
        widget.bind("<Button-1>", lambda e: command())
        widget.configure(cursor="hand2")
        for child in widget.winfo_children():
            self.clickable(child, command)

    def toggle_single(self, b) -> None:
        key = bets.label(b)
        self.picked.symmetric_difference_update({key})
        self.after_toggle()

    def toggle_parlay(self, p) -> None:
        key = tuple(bets.label(b) for b in p.legs)
        self.picked_parlays.symmetric_difference_update({key})
        self.after_toggle()

    def after_toggle(self) -> None:
        if self.tab == "slip":  # removing from the slip view redraws it
            self.show_tab("slip")
        else:
            self.refresh_cards()
            self.update_tab_count()

    def update_tab_count(self) -> None:
        label = getattr(self, "tab_labels", {}).get("slip")
        if label is not None and label.winfo_exists():
            label.configure(text=f"Your slip ({len(self.picked) + len(self.picked_parlays)})")

    def slip_bets(self) -> tuple[list, list]:
        singles = [b for b in self.singles if bets.label(b) in self.picked]
        parlays = [p for p in self.parlays
                   if tuple(bets.label(b) for b in p.legs) in self.picked_parlays]
        return singles, parlays

    def combined(self, singles) -> Parlay | None:
        """Your picked singles as one parlay, if they're all from different games."""
        if len(singles) >= 2 and len({b.game for b in singles}) == len(singles):
            return Parlay(tuple(singles))
        return None

    def refresh_cards(self) -> None:
        for key, (row, num_lbl, num) in self.row_widgets.items():
            on = key in self.picked or key in self.picked_parlays
            bg = BLUE_SOFT if on else CARD
            for w in [row, *row.winfo_children()]:
                w.configure(bg=bg)
            num_lbl.configure(text="✓" if on else num, fg=BLUE if on else MUTED)
        singles, parlays = self.slip_bets()
        if not singles and not parlays:
            self.slip_summary.configure(text="Click any bet card to add it.")
            return
        text = f"{len(singles)} single{'s' * (len(singles) != 1)}, {len(parlays)} parlay" \
               f"{'s' * (len(parlays) != 1)} picked"
        combo = self.combined(singles)
        if combo:
            text += (f"  ·  your singles as one parlay: {combo.win_prob:.0%} to win, break-even "
                     f"{_fmt_american(decimal_to_american(1 / combo.win_prob))}")
        elif len(singles) >= 2:
            text += "  ·  two picks share a game, so they can't be one standard parlay"
        self.slip_summary.configure(text=text)

    def clear_slip(self) -> None:
        self.picked.clear()
        self.picked_parlays.clear()
        self.show_tab(self.tab)

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
        self.clear()
        self.row_widgets = {}
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x", padx=16, pady=(12, 2))
        sport = "NFL" if args.sport == "nfl" else "College Football"
        tk.Label(head, text=f"{sport}  ·  {bets.week_label(singles, parlays)}", bg=BG, fg=TEXT,
                 font=self.f["h2"]).pack(side="left")
        checked = getattr(args, "fanduel_state", None) or getattr(args, "fanduel_key", None)
        order = "best value first" if checked else "likeliest first"
        tk.Label(head, text=f"   {args.min_prob:.0f}–{args.max_prob:.0f}% chance, {order}"
                            "  ·  click a bet to add it to your slip",
                 bg=BG, fg=MUTED, font=self.f["small"]).pack(side="left", pady=(3, 0))
        self.fd_mode = bool(getattr(args, "fanduel_key", None) or getattr(args, "fanduel_state", None))
        if self.fd_mode and board.NOTES.get("fanduel") and not board.NOTES.get("fanduel_error"):
            tk.Label(self.body, text="✓ " + board.NOTES["fanduel"] + "  ·  ranked by expected value",
                     bg=BG, fg=BLUE,
                     font=self.f["small"]).pack(anchor="w", padx=16)
        elif board.NOTES.get("fanduel_error") and self.fd_mode:
            tk.Label(self.body, text="⚠ " + board.NOTES["fanduel_error"], bg=BG, fg=MOVED_BADGE,
                     font=self.f["small"], wraplength=820, justify="left").pack(anchor="w", padx=16)
        if args.sport == "nfl" and board.NOTES.get("injuries"):
            ready = "included for all" in board.NOTES["injuries"]
            tk.Label(self.body, text=("✓ " if ready else "⚠ ") + board.NOTES["injuries"],
                     bg=BG, fg=GREEN if ready else MOVED_BADGE,
                     font=self.f["small"]).pack(anchor="w", padx=16)

        tabs = tk.Frame(self.body, bg=BG)
        tabs.pack(fill="x", padx=16, pady=(10, 0))
        self.tab_labels = {}
        for key, text in (("singles", f"Single bets ({len(singles)})"),
                          ("parlays", f"Parlays ({len(parlays)})"),
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
            self.hidden_note(grid, singles, parlays)
            self.singles_tab(grid, [b for b in singles if self.shown(b)], previous)
        elif self.tab == "parlays":
            self.hidden_note(grid, singles, parlays)
            self.parlays_tab(grid, [p for p in parlays if self.shown_parlay(p)], args.stake)
        elif self.tab == "injuries":
            self.injuries_tab(grid)
        elif self.tab == "history":
            self.history_tab(grid)
        else:
            self.slip_tab(grid)
        self.refresh_cards()
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
        last_text = (("EV" if fd else f"PAYS ${self.wager():g}") if last == "pays" else (last or ""))
        for col, text, anchor in ((0, first, "w"), (1, "BET", "w"), (2, "CHANCE", "e"),
                                  (3, "FANDUEL" if fd else "WORST ODDS", "e"), (4, last_text, "e")):
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
                 on_click=None, last: str | None = None, price: int | None = None,
                 ev: float | None = None) -> tk.Frame:
        """One bet: number, name with game below, chance, worst odds, payout."""
        tk.Frame(table, bg=BORDER, height=1).pack(fill="x")
        row = tk.Frame(table, bg=CARD)
        row.pack(fill="x")
        self.grid_cols(row)
        num_lbl = tk.Label(row, text=num, bg=CARD, fg=MUTED, font=self.f["bold"])
        num_lbl.grid(row=0, column=0, rowspan=2, sticky="w", padx=8)
        tk.Label(row, text=title, bg=CARD, fg=TEXT, font=self.f["bold"], anchor="w",
                 justify="left", wraplength=470).grid(row=0, column=1, sticky="w", padx=6,
                                                       pady=(6, 0))
        tk.Label(row, text=sub, bg=CARD, fg=MUTED, font=self.f["tiny"], anchor="w",
                 justify="left", wraplength=470).grid(row=1, column=1, sticky="w", padx=6,
                                                       pady=(0, 6))
        tk.Label(row, text=f"{prob:.0%}", bg=CARD, fg=chance_color(prob),
                 font=self.f["pick"]).grid(row=0, column=2, rowspan=2, sticky="e", padx=6)
        fd = getattr(self, "fd_mode", False) and price is not None
        odds_text = _fmt_american(price) if fd else _fmt_american(decimal_to_american(1 / prob))
        tk.Label(row, text=odds_text, bg=CARD, fg=BLUE,
                 font=self.f["bold"]).grid(row=0, column=3, rowspan=2, sticky="e", padx=6)
        if last is not None:
            end, end_fg = last, TEXT
        elif fd and ev is not None:
            end, end_fg = f"{ev:+.1%}", GREEN if ev > 0 else RED
        else:
            end, end_fg = f"${self.wager() / prob:,.2f}", TEXT
        end_lbl = tk.Label(row, text=end, bg=CARD, fg=end_fg, font=self.f["small"])
        end_lbl.grid(row=0, column=4, rowspan=2, sticky="e", padx=8)
        if on_click is not None:
            self.clickable(row, on_click)
        if key is not None:
            self.row_widgets[key] = (row, num_lbl, num)
        return row

    def singles_tab(self, parent, singles, previous) -> None:
        if not singles:
            tk.Label(parent, text="None found. Try a wider chance range or another week.", bg=BG,
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
            self.list_row(table, str(i), title, f"{_slip_market(b)}  ·  {b.game}  ·  {_slip_time(b)}",
                          b.fair_prob, bets.label(b), lambda b=b: self.toggle_single(b),
                          price=b.fd_price, ev=b.ev)

    def parlays_tab(self, parent, parlays, stake) -> None:
        if not parlays:
            tk.Label(parent, text="Not enough games for these parlays this week.", bg=BG,
                     fg=TEXT, font=self.f["body"]).pack(anchor="w", padx=6, pady=8)
            return
        table = self.list_table(parent, "")
        for i, p in enumerate(parlays, 1):
            legs = "  ·  ".join(_slip_selection(b) + {"Questionable": " (Q)",
                                                      "Did not practice": " (DNP)"}.get(b.note, "")
                                for b in p.legs)
            self.list_row(table, f"P{i}", f"{len(p.legs)}-leg parlay", legs, p.win_prob,
                          tuple(bets.label(b) for b in p.legs), lambda p=p: self.toggle_parlay(p),
                          price=p.american, ev=p.ev)

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

    def slip_tab(self, parent) -> None:
        singles, parlays = self.slip_bets()
        if not singles and not parlays:
            tk.Label(parent, text="Your slip is empty. Click bets on the Single bets or Parlays "
                                  "tabs to add them.", bg=BG, fg=TEXT,
                     font=self.f["body"]).pack(anchor="w", padx=6, pady=8)
            return
        self.place_inputs = []  # (entry, odds box, stake box) for Placed all
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x", padx=6, pady=(6, 0))
        FlatButton(top, "Placed all", self.mark_all_placed, "primary",
                   self.f["tinyb"]).pack(side="left")
        tk.Label(top, text="  Marks every confirmed bet below as placed, at the odds and stake "
                           "next to it.",
                 bg=BG, fg=MUTED, font=self.f["small"]).pack(side="left")
        stake = self.wager()
        table = self.list_table(parent, "", last="")
        for b in singles:
            self.list_row(table, "", _slip_selection(b), f"{b.game}  ·  {_slip_time(b)}",
                          b.fair_prob, last="✕", price=b.fd_price)
            self.remove_link(table, lambda b=b: self.toggle_single(b))
            if self.confirmed([b]):
                self.place_row(table, self.entry_for([b], []), stake, prob=b.fair_prob,
                               prefill=b.fd_price if self.fd_mode else None)
            else:
                self.confirm_row(table, [b], lambda b=b: self.toggle_single(b))
        for p in parlays:
            self.list_row(table, "", f"{len(p.legs)}-leg parlay",
                          "  ·  ".join(_slip_selection(b) for b in p.legs), p.win_prob, last="✕",
                          price=p.american)
            self.remove_link(table, lambda p=p: self.toggle_parlay(p))
            if self.confirmed(list(p.legs)):
                self.place_row(table, self.entry_for([], [p]), stake, prob=p.win_prob,
                               prefill=p.american if self.fd_mode else None)
            else:
                self.confirm_row(table, list(p.legs), lambda p=p: self.toggle_parlay(p))
        combo = self.combined(singles) if self.confirmed(singles) else None
        if combo:
            tk.Label(parent, text=f"Or bet your {len(singles)} singles as one parlay:", bg=BG,
                     fg=MUTED, font=self.f["small"]).pack(anchor="w", padx=6, pady=(10, 0))
            table = self.list_table(parent, "", last="")
            self.list_row(table, "", f"{len(singles)}-leg parlay (your singles)",
                          "  ·  ".join(_slip_selection(b) for b in singles), combo.win_prob, last="",
                          price=combo.american)
            self.place_row(table, self.entry_for([], [combo]), stake, bulk=False,
                           prob=combo.win_prob, prefill=combo.american if self.fd_mode else None)

    # -------------------------------------------------------------- FanDuel checks
    def checks(self) -> dict:
        return self.ledger.setdefault("checks", {})

    def confirmed(self, legs) -> bool:
        """Every leg confirmed on FanDuel (by you, or by the FanDuel feed)."""
        return bool(legs) and all(self.checks().get(bets.label(b)) is True
                                  or getattr(b, "on_fanduel", False) for b in legs)

    def shown(self, b) -> bool:
        return self.checks().get(bets.label(b)) is not False

    def shown_parlay(self, p) -> bool:
        return all(self.shown(b) for b in p.legs)

    def set_checks(self, keys: list[str], on: bool | None) -> None:
        def change(ledger):
            for k in keys:
                if on is None:
                    ledger["checks"].pop(k, None)
                else:
                    ledger["checks"][k] = on
        with self.ledger_lock:
            ledger = tracker.load(self.ledger_path)
            change(ledger)
            tracker.save(ledger, self.ledger_path)
        self.ledger = ledger

    def confirm_row(self, parent, legs, remove) -> None:
        """'Is this on FanDuel?  [✓ On FanDuel]  [✗ Not on FanDuel]' under a slip row."""
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=(48, 8), pady=(0, 7))
        todo = [b for b in legs if self.checks().get(bets.label(b)) is not True]
        what = ("Find this bet on FanDuel. Is it there?" if len(legs) == 1 else
                f"Find {'these legs' if len(todo) > 1 else 'this leg'} on FanDuel: "
                + "; ".join(_slip_selection(b) for b in todo) + ". All there?")
        tk.Label(row, text=what, bg=CARD, fg=TEXT, font=self.f["tiny"], wraplength=330,
                 justify="left").pack(side="left")

        def yes():
            self.set_checks([bets.label(b) for b in todo], True)
            self.show_tab("slip")

        def no():
            # With one leg left to check, that's the missing one; otherwise hide them all.
            self.set_checks([bets.label(b) for b in todo], False)
            remove()
            # Parlays that need a missing leg can't be placed either.
            self.picked_parlays = {k for k in self.picked_parlays
                                   if all(self.checks().get(leg) is not False for leg in k)}
            self.after_toggle()
        FlatButton(row, "✗ Not on FanDuel", no, "secondary", self.f["tinyb"]).pack(side="right")
        FlatButton(row, "✓ On FanDuel", yes, "primary", self.f["tinyb"]).pack(side="right", padx=6)

    def hidden_note(self, parent, singles, parlays) -> None:
        hidden = [b for b in singles if not self.shown(b)]
        hidden_p = [p for p in parlays if not self.shown_parlay(p)]
        if not hidden and not hidden_p:
            return
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=6, pady=(6, 0))
        parts = [f"{len(hidden)} bet{'s' * (len(hidden) != 1)}" if hidden else "",
                 f"{len(hidden_p)} parla{'ys' if len(hidden_p) != 1 else 'y'} with "
                 f"{'them' if len(hidden) != 1 else 'it'}" if hidden_p else ""]
        tk.Label(row, text=" and ".join(x for x in parts if x).capitalize()
                 + " hidden: marked not on FanDuel.", bg=BG, fg=MUTED,
                 font=self.f["small"]).pack(side="left")
        link = tk.Label(row, text="Show again", bg=BG, fg=BLUE, cursor="hand2",
                        font=self.f["small"])
        link.pack(side="left", padx=6)
        keys = [k for k, v in self.checks().items() if v is False]
        link.bind("<Button-1>", lambda e: (self.set_checks(keys, None), self.show_tab(self.tab)))

    def remove_link(self, table, command) -> None:
        """Make the ✕ at the end of the row just added remove it from the slip."""
        row = table.winfo_children()[-1]
        x = row.grid_slaves(row=0, column=4)[0]
        x.configure(fg=MUTED, cursor="hand2", font=self.f["bold"])
        x.bind("<Button-1>", lambda e: command())

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

    def entry_for(self, singles, parlays) -> dict | None:
        args = self.last_args or self.args()
        entries = bets.tracker_entries(singles, parlays, args.sport, bets.season_for(date.today()))
        return entries[0] if entries else None

    def place_row(self, parent, entry: dict | None, stake: float, bulk: bool = True,
                  prob: float | None = None, prefill: int | None = None) -> None:
        """'Odds you got [ ]  Stake [ ]  [I placed this]  Worth it' under a slip row."""
        if entry is None:
            return
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=(48, 8), pady=(0, 7))
        if any(e["id"] == entry["id"] for e in self.ledger["placed"]):
            tk.Label(row, text="✓ Placed · tracked in Bet History", bg=CARD, fg=GREEN,
                     font=self.f["tinyb"]).pack(side="left")
            return
        tk.Label(row, text="Odds you got", bg=CARD, fg=MUTED, font=self.f["tiny"]).pack(side="left")
        odds = ttk.Entry(row, width=6, font=self.f["small"])
        odds.pack(side="left", padx=(4, 8))
        verdict = tk.Label(row, text="", bg=CARD, font=self.f["tinyb"])
        if prob:
            def judge(_e=None):  # is the price you got worth it?
                try:
                    price = int(odds.get().replace("+", ""))
                    ev = prob * bets.american_to_decimal(price) - 1 if abs(price) >= 100 else None
                except ValueError:
                    ev = None
                verdict.configure(text="" if ev is None else
                                  f"{'Worth it' if ev > 0 else 'Not worth it'} · EV {ev:+.1%}",
                                  fg=GREEN if ev and ev > 0 else RED)
            odds.bind("<KeyRelease>", judge)
            if prefill is not None:  # FanDuel's own price, ready to confirm
                odds.insert(0, _fmt_american(prefill))
                self.root.after_idle(judge)
        tk.Label(row, text="Stake $", bg=CARD, fg=MUTED, font=self.f["tiny"]).pack(side="left")
        amount = ttk.Entry(row, width=5, font=self.f["small"])
        amount.insert(0, f"{stake:g}")
        amount.pack(side="left", padx=(4, 8))
        if bulk:  # your singles as one parlay is an alternative, not part of Placed all
            self.place_inputs.append((entry, odds, amount))
        FlatButton(row, "I placed this", lambda: self.mark_placed(entry, odds.get(), amount.get()),
                   "secondary", self.f["tinyb"]).pack(side="left")
        verdict.pack(side="left", padx=8)

    @staticmethod
    def parse_place(odds_text: str, stake_text: str) -> tuple[int | None, float]:
        """('-150' or blank, '10') -> (-150 or None, 10.0); ValueError if malformed."""
        odds = int(odds_text.replace("+", "")) if odds_text.strip() else None
        stake = float(stake_text)
        if (odds is not None and abs(odds) < 100) or stake <= 0:
            raise ValueError
        return odds, stake

    def mark_all_placed(self) -> None:
        todo = []
        for entry, odds_box, stake_box in getattr(self, "place_inputs", []):
            try:
                todo.append((entry, *self.parse_place(odds_box.get(), stake_box.get())))
            except ValueError:
                self.verdict.configure(text="Fix the odds or stake on one of the bets: odds "
                                            "look like -150 or +240.", fg=WHITE)
                return
        if not todo:
            self.verdict.configure(text="Everything on your slip is already placed.", fg=WHITE)
            return
        with self.ledger_lock:
            ledger = tracker.load(self.ledger_path)
            for entry, odds, stake in todo:
                tracker.place(ledger, entry, odds, stake)
            tracker.save(ledger, self.ledger_path)
        self.ledger = ledger
        total = sum(stake for _, _, stake in todo)
        self.verdict.configure(text=f"Placed {len(todo)} bet{'s' * (len(todo) != 1)}, "
                                    f"${total:,.2f} total.", fg=WHITE)
        self.show_tab("slip")

    def mark_placed(self, entry: dict, odds_text: str, stake_text: str) -> None:
        try:
            odds, stake = self.parse_place(odds_text, stake_text)
        except ValueError:
            self.verdict.configure(text="Odds look like -150 or +240; stake is a dollar amount.",
                                   fg=WHITE)
            return
        with self.ledger_lock:
            ledger = tracker.load(self.ledger_path)
            tracker.place(ledger, entry, odds, stake)
            tracker.save(ledger, self.ledger_path)
        self.ledger = ledger
        price = _fmt_american(odds) if odds is not None else "break-even odds"
        self.verdict.configure(text=f"Placed: ${stake:,.2f} at {price}. Graded after the game.",
                               fg=WHITE)
        self.show_tab("slip")

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
            weeks = sorted({e.get("week") for _, e in group}, key=lambda w: -(w or -1))
            for week in weeks:
                in_week = [x for x in group if x[1].get("week") == week]
                wk = tk.Frame(parent, bg=BG)
                wk.pack(fill="x", padx=6, pady=(6, 2))
                tk.Label(wk, text=f"Week {week}" if week else "Other", bg=BG, fg=TEXT,
                         font=self.f["bold"]).pack(side="left")
                tk.Label(wk, text="   " + tally(in_week), bg=BG, fg=MUTED,
                         font=self.f["tiny"]).pack(side="left", pady=(2, 0))
                done = sorted((e for _, e in in_week if e["status"] != "pending"),
                              key=tracker.first_kickoff, reverse=True)
                pending = sorted((e for _, e in in_week if e["status"] == "pending"),
                                 key=tracker.first_kickoff)
                holder = tk.Frame(parent, bg=BG)
                holder.pack(fill="x")
                cols = self.columns(holder, 3, CARD_W)
                heights = [0, 0, 0]
                for e in done + pending:  # fill the shortest column, so there are no gaps
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

    def view_slip(self) -> None:
        self.show_tab("slip")

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
            stake=float(self.stake.get() or 10), min_prob=float(self.lo.get() or 60),
            max_prob=float(self.hi.get() or 80), per_game=3, allow_overlap=False, games_file=None,
            fanduel_state=bets.fanduel_state() if self.fanduel_on else None)

    def build(self) -> None:
        try:
            args = self.args()
        except ValueError:
            self.say("Please use numbers for singles, legs, parlays, wager and chance.")
            return
        self.build_btn.configure(text="Loading...")
        self.cancel_update()
        self.countdown.set("Updating...")

        def work():
            try:
                try:
                    _, singles, parlays = bets.build(args)
                except (fanduel.OddsAPIError, fdfeed.FeedError) as e:
                    # Never show bets FanDuel hasn't confirmed while the FanDuel check is on.
                    singles, parlays = [], []
                    board.NOTES["fanduel_error"] = (
                        f"Couldn't check against FanDuel ({e}), so no bets are shown. "
                        "Try again in a minute, or turn the FanDuel check off and confirm "
                        "bets yourself in the slip.")
                else:
                    board.NOTES.pop("fanduel_error", None)
                injuries = (None, [], "College injury reports aren't in the free data.")
                if args.sport == "nfl" and args.date != "today":
                    injuries = board.injury_report(args.week)
                self.results.put(("ok", args, singles, parlays, injuries,
                                  self.track(args, singles, parlays)))
            except (data.DataError, ValueError) as e:
                self.results.put(("error", str(e)))

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
        if self.next_at is not None:
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
            if self.last_args and (self.last_args.sport, self.last_args.week) != (args.sport, args.week):
                self.previous = {}  # a different week or sport: nothing is "new" or "moved"
            self.last_args = args
            labels = {bets.label(b) for b in self.singles}
            self.picked &= labels
            self.picked_parlays = {k for k in self.picked_parlays
                                   if k in {tuple(bets.label(b) for b in p.legs) for p in self.parlays}}
            self.render(args, self.singles, self.parlays, self.previous)
            self.previous = {bets.label(b): b.fair_prob for b in self.singles}
        self.schedule()
        if self.selftest:
            print(f"{len(self.singles)} singles, {len(self.parlays)} parlays rendered")
            self.root.after(500, self.root.destroy)
            return
        self.root.after(150, self.poll)

    def check(self) -> None:
        entry = self.check_entry.get().strip()
        if not self.singles and not self.parlays:
            self.verdict.configure(text="Build bets first.")
            return
        self.verdict.configure(text=bets.check_price(self.singles, self.parlays, entry), fg=WHITE)

    def save(self) -> None:
        if not self.singles and not self.parlays:
            self.verdict.configure(text="Build bets first.")
            return
        args = self.last_args or self.args()
        singles, parlays = self.slip_bets()
        what = "your slip"
        if not singles and not parlays:
            singles, parlays, what = self.singles, self.parlays, "all bets"
        combo = self.combined(singles)
        paths = bets.save(singles, parlays + ([combo] if combo else []), args.sport,
                          bets.season_for(date.today()))
        self.verdict.configure(text=f"Saved {what} ({len(paths)} file(s)) to {bets.ROOT / 'bets'}",
                               fg=WHITE)

    def grade(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bets.grade_saved("all")
        self.say(buf.getvalue().strip() or "No saved bets yet.")


def main() -> int:
    # A windowed build has no console; give stray prints somewhere to go.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    selftest = "--selftest" in sys.argv
    root = tk.Tk()
    App(root, selftest=selftest)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
