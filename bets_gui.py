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
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import date
from tkinter import ttk

import bets  # sets up paths for the standalone build
from nfl_edge import board, data
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


def bar(parent, p: float, bg: str, width: int, font) -> tk.Frame:
    """A rounded chance bar with its percentage."""
    row = tk.Frame(parent, bg=bg)
    c = tk.Canvas(row, width=width, height=10, bg=bg, highlightthickness=0)
    c.create_line(5, 5, width - 5, 5, fill=TRACK, width=8, capstyle="round")
    c.create_line(5, 5, 5 + (width - 10) * p, 5, fill=chance_color(p), width=8, capstyle="round")
    c.pack(side="left")
    tk.Label(row, text=f"{p:.0%} to win", bg=bg, fg=TEXT, font=font).pack(side="left", padx=8)
    return row


def odds_button(parent, text: str, caption: str, f_big, f_small, bg=BLUE_SOFT) -> tk.Frame:
    """A sportsbook-style odds box: big price over a small caption."""
    box = tk.Frame(parent, bg=bg, padx=6, pady=3, highlightbackground="#c7dbff",
                   highlightthickness=1)
    tk.Label(box, text=text, bg=bg, fg=BLUE, font=f_big).pack()
    tk.Label(box, text=caption, bg=bg, fg=MUTED, font=f_small).pack()
    return box


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
        root.title("Bet Builder")
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
        tk.Label(row1, text="   Free data  ·  odds shown are break-even", bg=NAVY, fg="#9fb0c8", font=self.f["small"]).pack(side="left")

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
        for key, (frame, badge, oval) in self.card_widgets.items():
            on = key in self.picked or key in self.picked_parlays
            frame.configure(highlightbackground=BLUE if on else BORDER,
                            highlightthickness=2 if on else 1)
            if badge is not None:
                badge.itemconfigure(oval, fill=BLUE if on else NAVY)
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
        self.card_widgets = {}
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x", padx=16, pady=(12, 2))
        sport = "NFL" if args.sport == "nfl" else "College Football"
        tk.Label(head, text=f"{sport}  ·  {bets.week_label(singles, parlays)}", bg=BG, fg=TEXT,
                 font=self.f["h2"]).pack(side="left")
        tk.Label(head, text=f"   {args.min_prob:.0f}–{args.max_prob:.0f}% chance, likeliest first"
                            "  ·  click a card to add it to your slip",
                 bg=BG, fg=MUTED, font=self.f["small"]).pack(side="left", pady=(3, 0))
        if args.sport == "nfl" and board.NOTES.get("injuries"):
            ready = "included for all" in board.NOTES["injuries"]
            tk.Label(self.body, text=("✓ " if ready else "⚠ ") + board.NOTES["injuries"],
                     bg=BG, fg=GREEN if ready else MOVED_BADGE,
                     font=self.f["small"]).pack(anchor="w", padx=16)

        tabs = tk.Frame(self.body, bg=BG)
        tabs.pack(fill="x", padx=16, pady=(10, 0))
        n_slip = len(self.picked) + len(self.picked_parlays)
        self.tab_labels = {}
        for key, text in (("singles", f"Single bets ({len(singles)})"),
                          ("parlays", f"Parlays ({len(parlays)})"),
                          ("injuries", f"Injuries ({len(self.injuries[1])})"),
                          ("slip", f"Your slip ({n_slip})")):
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
            self.parlays_tab(grid, parlays, args.stake)
        elif self.tab == "injuries":
            self.injuries_tab(grid)
        else:
            self.slip_tab(grid)
        self.refresh_cards()
        tk.Label(self.body, text="Odds shown are break-even: bet only if FanDuel pays that or "
                                 "better. Check any price at the bottom.",
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

    def singles_tab(self, parent, singles, previous) -> None:
        if not singles:
            tk.Label(parent, text="None found. Try a wider chance range or another week.", bg=BG,
                     fg=TEXT, font=self.f["body"]).pack(anchor="w", padx=6, pady=8)
            return
        cols = self.columns(parent, 3, CARD_W)
        for i, b in enumerate(singles, 1):
            self.single_card(cols[(i - 1) % 3], i, b, previous)

    def parlays_tab(self, parent, parlays, stake) -> None:
        if not parlays:
            tk.Label(parent, text="Not enough games for these parlays this week.", bg=BG,
                     fg=TEXT, font=self.f["body"]).pack(anchor="w", padx=6, pady=8)
            return
        cols = self.columns(parent, 3, CARD_W)
        for i, p in enumerate(parlays, 1):
            self.parlay_card(cols[(i - 1) % 3], i, p, stake)

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
            tk.Label(parent, text="Your slip is empty. Click cards on the Single bets or Parlays "
                                  "tabs to add them.", bg=BG, fg=TEXT,
                     font=self.f["body"]).pack(anchor="w", padx=6, pady=8)
            return
        left, right = self.columns(parent)
        stake = float(self.stake.get() or 10)
        if singles:
            tk.Label(left, text=f"SINGLE BETS  ·  ${stake:,.0f} EACH", bg=BG, fg=MUTED,
                     font=self.f["label"]).pack(anchor="w", pady=(6, 4))
            total = 0.0
            for b in singles:
                self.single_card(left, self.singles.index(b) + 1, b, {}, stake=stake)
                total += stake / b.fair_prob
            tk.Label(left, text=f"If every single wins: ${total:,.2f} back on "
                                f"${stake * len(singles):,.2f}*",
                     bg=BG, fg=TEXT, font=self.f["bold"]).pack(anchor="w", pady=6)
        combo = self.combined(singles)
        if combo:
            tk.Label(right, text="YOUR SINGLES AS ONE PARLAY", bg=BG, fg=MUTED,
                     font=self.f["label"]).pack(anchor="w", pady=(6, 4))
            self.parlay_card(right, "Mine", combo, stake)
        if parlays:
            tk.Label(right, text="PARLAYS YOU PICKED", bg=BG, fg=MUTED,
                     font=self.f["label"]).pack(anchor="w", pady=(6, 4))
            for p in parlays:
                self.parlay_card(right, self.parlays.index(p) + 1, p, stake)

    def view_slip(self) -> None:
        self.show_tab("slip")

    def card(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="x", pady=3)
        return frame

    def single_card(self, parent, i: int, b, previous, stake: float | None = None) -> None:
        card = self.card(parent)
        badge = tk.Canvas(card, width=22, height=22, bg=CARD, highlightthickness=0)
        oval = badge.create_oval(2, 2, 20, 20, fill=NAVY, outline="")
        badge.create_text(11, 11, text=str(i), fill=WHITE, font=self.f["tinyb"])
        badge.pack(side="left", anchor="n", padx=(8, 2), pady=8)

        odds_button(card, _fmt_american(decimal_to_american(1 / b.fair_prob)), "break-even",
                    self.f["pick"], self.f["tiny"]).pack(side="right", padx=8, pady=8)

        mid = tk.Frame(card, bg=CARD)
        mid.pack(side="left", fill="both", expand=True, padx=4, pady=6)
        top = tk.Frame(mid, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text=_slip_selection(b), bg=CARD, fg=TEXT, font=self.f["bold"],
                 wraplength=122, justify="left").pack(side="left")
        old = previous.get(bets.label(b)) if previous else None
        if previous and old is None:
            tk.Label(top, text=" NEW ", bg=NEW_BADGE, fg=WHITE, font=self.f["tinyb"]).pack(
                side="left", padx=6)
        elif old is not None and abs(old - b.fair_prob) >= 0.02:
            tk.Label(top, text=f" was {old:.0%} ", bg=MOVED_BADGE, fg=WHITE,
                     font=self.f["tinyb"]).pack(side="left", padx=6)
        if b.note:
            badge_text = {"Questionable": " Q ", "Did not practice": " DNP "}.get(b.note, b.note)
            tk.Label(top, text=badge_text, bg=STATUS_COLORS.get(b.note, MOVED_BADGE), fg=WHITE,
                     font=self.f["tinyb"]).pack(side="left", padx=4)
        tk.Label(mid, text=_slip_market(b), bg=CARD, fg=BLUE, font=self.f["tinyb"]).pack(anchor="w")
        meta = f"{b.game}  ·  {_slip_time(b)}"
        if b.priced:
            meta += f"  ·  consensus {_fmt_american(b.fd_price)}"
        tk.Label(mid, text=meta, bg=CARD, fg=MUTED, font=self.f["tiny"], wraplength=124,
                 justify="left").pack(anchor="w")
        bar(mid, b.fair_prob, CARD, 50, self.f["tinyb"]).pack(anchor="w", pady=(4, 0))
        if stake:
            tk.Label(mid, text=f"${stake:,.2f} wins ${stake / b.fair_prob - stake:,.2f}*",
                     bg=CARD, fg=TEXT, font=self.f["tinyb"]).pack(anchor="w", pady=(4, 0))
        key = bets.label(b)
        self.card_widgets[key] = (card, badge, oval)
        self.clickable(card, lambda b=b: self.toggle_single(b))

    def parlay_card(self, parent, i, p, wager: float) -> None:
        card = self.card(parent)
        head = tk.Frame(card, bg=NAVY)
        head.pack(fill="x")
        title = "Your parlay" if i == "Mine" else f"Parlay P{i}"
        tk.Label(head, text=title, bg=NAVY, fg=WHITE, font=self.f["bold"]).pack(
            side="left", padx=(10, 4), pady=4)
        tk.Label(head, text=f"{len(p.legs)} legs", bg=NAVY, fg="#9fb0c8",
                 font=self.f["small"]).pack(side="left")
        tk.Label(head, text=f" {_fmt_american(decimal_to_american(1 / p.win_prob))} ", bg=BLUE,
                 fg=WHITE, font=self.f["bold"]).pack(side="right", padx=8)
        for n, b in enumerate(p.legs):
            if n:
                tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=10)
            leg = tk.Frame(card, bg=CARD)
            leg.pack(fill="x", padx=10, pady=3)
            tk.Label(leg, text=_fmt_american(decimal_to_american(1 / b.fair_prob)), bg=CARD,
                     fg=BLUE, font=self.f["bold"]).pack(side="right", anchor="n")
            dot = tk.Canvas(leg, width=12, height=12, bg=CARD, highlightthickness=0)
            dot.create_oval(1, 1, 11, 11, outline=BLUE, width=2)
            dot.pack(side="left", anchor="n", pady=3)
            txt = tk.Frame(leg, bg=CARD)
            txt.pack(side="left", padx=8)
            name = _slip_selection(b) + {"Questionable": "  (Q)",
                                         "Did not practice": "  (DNP)"}.get(b.note, "")
            tk.Label(txt, text=name, bg=CARD, fg=TEXT, font=self.f["tinyb"],
                     wraplength=170, justify="left").pack(anchor="w")
            tk.Label(txt, text=_slip_market(b), bg=CARD, fg=BLUE, font=self.f["tinyb"]).pack(anchor="w")
            tk.Label(txt, text=f"{b.game}  ·  {_slip_time(b)}", bg=CARD, fg=MUTED,
                     font=self.f["tiny"], wraplength=170, justify="left").pack(anchor="w")
        foot = tk.Frame(card, bg="#f6f8fb")
        foot.pack(fill="x")
        payout = wager / p.win_prob
        for title, value in (("Wager", f"${wager:,.2f}"), ("To win*", f"${payout - wager:,.2f}"),
                             ("Payout*", f"${payout:,.2f}")):
            box = tk.Frame(foot, bg="#f6f8fb")
            box.pack(side="left", expand=True, pady=4)
            tk.Label(box, text=title.upper(), bg="#f6f8fb", fg=MUTED, font=self.f["tinyb"]).pack()
            tk.Label(box, text=value, bg="#f6f8fb", fg=TEXT, font=self.f["bold"]).pack()
        bar(card, p.win_prob, CARD, 120, self.f["tinyb"]).pack(anchor="w", padx=10, pady=5)
        if i != "Mine":
            key = tuple(bets.label(b) for b in p.legs)
            self.card_widgets[key] = (card, None, None)
            self.clickable(card, lambda p=p: self.toggle_parlay(p))

    # -------------------------------------------------------------- actions
    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            sport=self.sport.get(),
            date="today" if self.when.get() == "Today's games" else "week",
            week=self.week_values.get(self.when.get()),
            singles=int(self.n_singles.get() or 30),
            legs=int(self.min_legs.get() or 3), min_legs=int(self.min_legs.get() or 3),
            max_legs=int(self.max_legs.get() or 5), parlays=int(self.n_parlays.get() or 0),
            stake=float(self.stake.get() or 10), min_prob=float(self.lo.get() or 60),
            max_prob=float(self.hi.get() or 80), per_game=3, allow_overlap=False, games_file=None)

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
                _, singles, parlays = bets.build(args)
                injuries = (None, [], "College injury reports aren't in the free data.")
                if args.sport == "nfl" and args.date != "today":
                    injuries = board.injury_report(args.week)
                self.results.put(("ok", args, singles, parlays, injuries))
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
            _, args, self.singles, self.parlays, self.injuries = item
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
