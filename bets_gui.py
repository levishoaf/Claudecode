#!/usr/bin/env python3
"""Windowed bet builder: the same picks as bets.py, on a football-field
background. No API key needed.

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
import tkinter as tk
from datetime import date
from tkinter import ttk

import bets  # sets up paths for the standalone build
from nfl_edge import data
from nfl_edge.cli import _fmt_american, _slip_market, _slip_selection, _slip_time
from nfl_edge.odds import decimal_to_american

W, H = 1180, 870
PANEL_W = W - 2 * W * 0.08 - 90  # leaves turf visible on both sides
NUMBER_ROWS = (163, 792)  # yard numbers sit in the gaps between panels
TURF = ("#2f7d32", "#2a7130")  # alternating 5-yard stripes
ENDZONE = "#1d4d20"
PANEL, PANEL_EDGE, INK, GOLD = "#0f1a10", "#e8e2c8", "#f4f1e4", "#f2c14e"
MONO = ("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10)
CARD, CARD_EDGE, MUTED, SLIP_HEAD = "#16241a", "#35503a", "#a9b8a4", "#1f3a8a"
NEW_BADGE, MOVED_BADGE = "#2e7dd1", "#b8741a"


def chance_color(p: float) -> str:
    """Green for likely, amber for middling, red for unlikely."""
    if p >= 0.7:
        return "#43a047"
    if p >= 0.5:
        return "#c0a030"
    return "#c0503a"


def chance_bar(parent: tk.Widget, p: float, bg: str, width: int = 150) -> tk.Frame:
    row = tk.Frame(parent, bg=bg)
    bar = tk.Canvas(row, width=width, height=10, bg="#0b130c", highlightthickness=0)
    bar.create_rectangle(0, 0, width * p, 10, fill=chance_color(p), outline="")
    bar.pack(side="left")
    tk.Label(row, text=f"{p:.0%} to win", bg=bg, fg=INK,
             font=("Helvetica", 10, "bold")).pack(side="left", padx=6)
    return row


def pill(parent: tk.Widget, text: str, bg: str, fg: str = "white") -> tk.Label:
    return tk.Label(parent, text=f" {text} ", bg=bg, fg=fg, font=("Helvetica", 9, "bold"))


def draw_field(c: tk.Canvas) -> None:
    """An original football field: end zones, 5-yard stripes, yard lines,
    hash marks and yard numbers, drawn to fill the window."""
    zone = W * 0.08
    play = W - 2 * zone
    yard = play / 100
    c.create_rectangle(0, 0, W, H, fill=TURF[0], outline="")
    for i in range(20):  # 5-yard stripes
        x0 = zone + i * 5 * yard
        c.create_rectangle(x0, 0, x0 + 5 * yard, H, fill=TURF[i % 2], outline="")
    for x0 in (0, W - zone):  # end zones
        c.create_rectangle(x0, 0, x0 + zone, H, fill=ENDZONE, outline="")
    for side, x in ((90, zone / 2), (-90, W - zone / 2)):
        c.create_text(x, H / 2, text="BET  BUILDER", angle=side, fill="#d9d4b8",
                      font=("Helvetica", 30, "bold"))
    for i in range(0, 101, 5):  # yard lines
        x = zone + i * yard
        c.create_line(x, 0, x, H, fill="white", width=3 if i % 10 == 0 else 1)
    for i in range(1, 100):  # hash marks
        x = zone + i * yard
        for y in (H * 0.36, H * 0.64):
            c.create_line(x, y - 5, x, y + 5, fill="white")
    for i in range(10, 100, 10):  # yard numbers
        x = zone + i * yard
        n = str(i if i <= 50 else 100 - i)
        for y, ang in zip(NUMBER_ROWS, (180, 0)):
            c.create_text(x, y, text=n, fill="white", angle=ang, font=("Helvetica", 24, "bold"))


class App:
    def __init__(self, root: tk.Tk, selftest: bool = False):
        self.root, self.selftest = root, selftest
        self.singles, self.parlays, self.previous = [], [], {}
        self.results: queue.Queue = queue.Queue()
        root.title("FanDuel Bet Builder")
        root.geometry(f"{W}x{H}")
        root.resizable(False, False)

        c = tk.Canvas(root, width=W, height=H, highlightthickness=0)
        c.pack(fill="both", expand=True)
        draw_field(c)

        # Controls panel
        top = tk.Frame(c, bg=PANEL, highlightbackground=PANEL_EDGE, highlightthickness=2)
        tk.Label(top, text="FanDuel Bet Builder", bg=PANEL, fg=GOLD,
                 font=("Helvetica", 18, "bold")).grid(row=0, column=0, columnspan=4, sticky="w",
                                                      padx=10, pady=(8, 2))
        tk.Label(top, text="Free data. Odds are break-even: bet only if FanDuel pays that or better.",
                 bg=PANEL, fg=INK).grid(row=0, column=4, columnspan=8, sticky="w")

        self.sport = tk.StringVar(value="nfl")
        self.when = tk.StringVar(value="week")
        self.n_singles = tk.StringVar(value="10")
        self.legs = tk.StringVar(value="3")
        self.n_parlays = tk.StringVar(value="3")
        self.stake = tk.StringVar(value="10")
        self.lo = tk.StringVar(value="60")
        self.hi = tk.StringVar(value="80")
        self.refresh = tk.StringVar(value="0")

        def field(col, text, var, width=6, values=None):
            tk.Label(top, text=text, bg=PANEL, fg=INK).grid(row=1, column=col, sticky="e", padx=(10, 2))
            if values:
                w = ttk.Combobox(top, textvariable=var, values=values, width=width)
            else:
                w = ttk.Entry(top, textvariable=var, width=width)
            w.grid(row=1, column=col + 1, sticky="w", pady=6)

        field(0, "Sport", self.sport, 7, ["nfl", "ncaaf"])
        field(2, "Games", self.when, 11, ["week", "today", date.today().isoformat()])
        field(4, "Singles", self.n_singles, 4)
        field(6, "Parlay legs", self.legs, 4)
        field(8, "Parlays", self.n_parlays, 4)
        field(10, "Wager $", self.stake, 6)
        tk.Label(top, text="Chance %", bg=PANEL, fg=INK).grid(row=2, column=0, sticky="e", padx=(10, 2))
        rng = tk.Frame(top, bg=PANEL)
        ttk.Entry(rng, textvariable=self.lo, width=4).pack(side="left")
        tk.Label(rng, text="to", bg=PANEL, fg=INK).pack(side="left", padx=3)
        ttk.Entry(rng, textvariable=self.hi, width=4).pack(side="left")
        rng.grid(row=2, column=1, sticky="w")
        tk.Label(top, text="Refresh every (min, 0 = off)", bg=PANEL, fg=INK).grid(
            row=2, column=2, columnspan=2, sticky="e")
        ttk.Entry(top, textvariable=self.refresh, width=4).grid(row=2, column=4, sticky="w")

        btns = tk.Frame(top, bg=PANEL)
        self.build_btn = tk.Button(btns, text="Build bets", command=self.build, bg=GOLD,
                                   fg="black", font=("Helvetica", 12, "bold"), padx=12)
        self.build_btn.pack(side="left", padx=4)
        tk.Button(btns, text="Save picks", command=self.save).pack(side="left", padx=4)
        tk.Button(btns, text="Grade saved bets", command=self.grade).pack(side="left", padx=4)
        btns.grid(row=2, column=5, columnspan=7, sticky="e", padx=10, pady=(0, 8))
        c.create_window(W / 2, 72, window=top, width=PANEL_W)

        # Output
        out = tk.Frame(c, bg=PANEL, highlightbackground=PANEL_EDGE, highlightthickness=2)
        self.view = tk.Canvas(out, bg=PANEL, highlightthickness=0)
        scroll = ttk.Scrollbar(out, command=self.view.yview)
        self.view.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.view.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.view, bg=PANEL)
        self.view.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>",
                       lambda e: self.view.configure(scrollregion=self.view.bbox("all")))
        for seq, step in (("<MouseWheel>", None), ("<Button-4>", -1), ("<Button-5>", 1)):
            root.bind_all(seq, lambda e, s=step: self.view.yview_scroll(
                s if s else (-1 if e.delta > 0 else 1) * (1 if sys.platform == "darwin" else 1), "units"))
        c.create_window(W / 2, 478, window=out, width=PANEL_W, height=570)

        # Price checker
        chk = tk.Frame(c, bg=PANEL, highlightbackground=PANEL_EDGE, highlightthickness=2)
        tk.Label(chk, text="Check FanDuel's price  (e.g. 3 -150  or  P1 +240):",
                 bg=PANEL, fg=GOLD, font=("Helvetica", 11, "bold")).pack(side="left", padx=8)
        self.check_entry = ttk.Entry(chk, width=14)
        self.check_entry.pack(side="left")
        self.check_entry.bind("<Return>", lambda e: self.check())
        tk.Button(chk, text="Check", command=self.check).pack(side="left", padx=6)
        self.verdict = tk.Label(chk, text="", bg=PANEL, fg=INK, anchor="w", justify="left",
                                wraplength=int(PANEL_W) - 470)
        self.verdict.pack(side="left", fill="x", expand=True, padx=6)
        c.create_window(W / 2, 836, window=chk, width=PANEL_W, height=50)

        self.say("Press  Build bets  to load this week's picks.\n")
        root.after(100, self.poll)
        if selftest:
            root.after(200, self.build)

    # -------------------------------------------------------------- actions
    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            sport=self.sport.get(), date=self.when.get(), singles=int(self.n_singles.get() or 10),
            legs=int(self.legs.get() or 3), parlays=int(self.n_parlays.get() or 0),
            stake=float(self.stake.get() or 10), min_prob=float(self.lo.get() or 60),
            max_prob=float(self.hi.get() or 80), per_game=3, allow_overlap=False, games_file=None)

    def clear(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        self.view.yview_moveto(0)

    def say(self, text: str, clear: bool = True) -> None:
        """Plain text (messages, grading results) in the output area."""
        if clear:
            self.clear()
        tk.Label(self.body, text=text, bg=PANEL, fg=INK, font=MONO, justify="left",
                 anchor="nw").pack(anchor="nw", padx=12, pady=10)

    def render(self, args, singles, parlays, previous) -> None:
        self.clear()
        head = tk.Frame(self.body, bg=PANEL)
        head.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(head, text=f"{args.sport.upper()}  ·  {len(singles)} single bets  ·  "
                            f"{len(parlays)} parlays  ·  {args.min_prob:.0f}–{args.max_prob:.0f}% "
                            "chance, likeliest first",
                 bg=PANEL, fg=MUTED, font=("Helvetica", 10)).pack(side="left")
        cols = tk.Frame(self.body, bg=PANEL)
        cols.pack(fill="both", padx=8)
        left, right = tk.Frame(cols, bg=PANEL), tk.Frame(cols, bg=PANEL)
        left.pack(side="left", anchor="n", padx=4)
        right.pack(side="left", anchor="n", padx=4)
        # Invisible struts fix each column's width so both fit the window.
        tk.Frame(left, bg=PANEL, width=470, height=1).pack()
        tk.Frame(right, bg=PANEL, width=375, height=1).pack()

        tk.Label(left, text="SINGLE BETS", bg=PANEL, fg=GOLD,
                 font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(4, 6))
        if not singles:
            tk.Label(left, text="None found. Try a wider chance range or another date.",
                     bg=PANEL, fg=INK).pack(anchor="w")
        for i, b in enumerate(singles, 1):
            self.single_card(left, i, b, previous)

        tk.Label(right, text=f"{args.legs}-LEG PARLAYS", bg=PANEL, fg=GOLD,
                 font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(4, 6))
        if args.parlays and not parlays:
            tk.Label(right, text="Not enough games for that many legs.", bg=PANEL,
                     fg=INK).pack(anchor="w")
        for i, p in enumerate(parlays, 1):
            self.parlay_card(right, i, p, args.stake)
        tk.Label(self.body, text="Odds shown are break-even: bet only if FanDuel pays that or "
                                 "better. Check a price at the bottom.",
                 bg=PANEL, fg=MUTED, font=("Helvetica", 9)).pack(anchor="w", padx=12, pady=8)

    def single_card(self, parent, i: int, b, previous) -> None:
        card = tk.Frame(parent, bg=CARD, highlightbackground=CARD_EDGE, highlightthickness=1)
        card.pack(fill="x", pady=4)
        tk.Label(card, text=str(i), bg=GOLD, fg="black", width=3,
                 font=("Helvetica", 12, "bold")).pack(side="left", fill="y")
        mid = tk.Frame(card, bg=CARD)
        mid.pack(side="left", fill="both", expand=True, padx=10, pady=6)
        top = tk.Frame(mid, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text=_slip_selection(b), bg=CARD, fg=INK,
                 font=("Helvetica", 12, "bold")).pack(side="left")
        old = previous.get(bets.label(b)) if previous else None
        if previous and old is None:
            pill(top, "NEW", NEW_BADGE).pack(side="left", padx=6)
        elif old is not None and abs(old - b.fair_prob) >= 0.02:
            pill(top, f"was {old:.0%}", MOVED_BADGE).pack(side="left", padx=6)
        tk.Label(mid, text=f"{_slip_market(b)}  ·  {b.game}  ·  {_slip_time(b)}", bg=CARD,
                 fg=MUTED, font=("Helvetica", 9), wraplength=300, justify="left").pack(anchor="w")
        chance_bar(mid, b.fair_prob, CARD).pack(anchor="w", pady=(4, 0))
        right = tk.Frame(card, bg=CARD)
        right.pack(side="right", padx=10)
        tk.Label(right, text=_fmt_american(decimal_to_american(1 / b.fair_prob)), bg=CARD,
                 fg=GOLD, font=("Helvetica", 16, "bold")).pack(anchor="e")
        tk.Label(right, text="break-even", bg=CARD, fg=MUTED,
                 font=("Helvetica", 8)).pack(anchor="e")
        if b.priced:
            tk.Label(right, text=f"consensus {_fmt_american(b.fd_price)}", bg=CARD, fg=MUTED,
                     font=("Helvetica", 8)).pack(anchor="e")

    def parlay_card(self, parent, i: int, p, wager: float) -> None:
        card = tk.Frame(parent, bg=CARD, highlightbackground=CARD_EDGE, highlightthickness=1)
        card.pack(fill="x", pady=(4, 10))
        odds = _fmt_american(decimal_to_american(1 / p.win_prob))
        head = tk.Frame(card, bg=SLIP_HEAD)
        head.pack(fill="x")
        tk.Label(head, text=f"P{i}  ·  {len(p.legs)} Leg Parlay", bg=SLIP_HEAD, fg="white",
                 font=("Helvetica", 12, "bold")).pack(side="left", padx=10, pady=6)
        tk.Label(head, text=odds, bg=SLIP_HEAD, fg="white",
                 font=("Helvetica", 15, "bold")).pack(side="right", padx=10)
        for b in p.legs:
            leg = tk.Frame(card, bg=CARD)
            leg.pack(fill="x", padx=10, pady=5)
            dot = tk.Canvas(leg, width=12, height=12, bg=CARD, highlightthickness=0)
            dot.create_oval(1, 1, 11, 11, outline=MUTED, width=2)
            dot.pack(side="left", anchor="n", pady=3)
            tk.Label(leg, text=_fmt_american(decimal_to_american(1 / b.fair_prob)), bg=CARD,
                     fg=INK, font=("Helvetica", 11, "bold")).pack(side="right", anchor="n")
            txt = tk.Frame(leg, bg=CARD)
            txt.pack(side="left", padx=8)
            tk.Label(txt, text=_slip_selection(b), bg=CARD, fg=INK, wraplength=250,
                     justify="left", font=("Helvetica", 11, "bold")).pack(anchor="w")
            tk.Label(txt, text=_slip_market(b), bg=CARD, fg=MUTED,
                     font=("Helvetica", 8, "bold")).pack(anchor="w")
            tk.Label(txt, text=f"{b.game}  ·  {_slip_time(b)}", bg=CARD, fg=MUTED,
                     font=("Helvetica", 8), wraplength=250, justify="left").pack(anchor="w")
        foot = tk.Frame(card, bg="#0b130c")
        foot.pack(fill="x")
        payout = wager / p.win_prob
        for title, value in (("Wager", f"${wager:,.2f}"), ("To win*", f"${payout - wager:,.2f}"),
                             ("Payout*", f"${payout:,.2f}")):
            box = tk.Frame(foot, bg="#0b130c")
            box.pack(side="left", expand=True, pady=6)
            tk.Label(box, text=title, bg="#0b130c", fg=MUTED, font=("Helvetica", 8)).pack()
            tk.Label(box, text=value, bg="#0b130c", fg=INK,
                     font=("Helvetica", 12, "bold")).pack()
        chance_bar(card, p.win_prob, CARD, width=200).pack(anchor="w", padx=10, pady=6)

    def build(self) -> None:
        try:
            args = self.args()
        except ValueError:
            self.say("Please use numbers for singles, legs, parlays, wager and chance.\n")
            return
        self.build_btn.configure(state="disabled", text="Loading...")

        def work():
            try:
                _, singles, parlays = bets.build(args)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    bets.show(args, singles, parlays, self.previous)
                self.results.put(("ok", args, singles, parlays, buf.getvalue()))
            except (data.DataError, ValueError) as e:
                self.results.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def poll(self) -> None:
        try:
            item = self.results.get_nowait()
        except queue.Empty:
            self.root.after(150, self.poll)
            return
        self.build_btn.configure(state="normal", text="Build bets")
        if item[0] == "error":
            self.say(f"Couldn't load data: {item[1]}\n")
        else:
            _, args, self.singles, self.parlays, text = item
            self.render(args, self.singles, self.parlays, self.previous)
            self.previous = {bets.label(b): b.fair_prob for b in self.singles}
            minutes = float(self.refresh.get() or 0)
            if minutes > 0:
                data.FRESH_SECONDS = min(data.FRESH_SECONDS, minutes * 60)
                self.root.after(int(minutes * 60_000), self.build)
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
        self.verdict.configure(text=bets.check_price(self.singles, self.parlays, entry))

    def save(self) -> None:
        if not self.singles and not self.parlays:
            self.verdict.configure(text="Build bets first.")
            return
        args = self.args()
        paths = bets.save(self.singles, self.parlays, args.sport, bets.season_for(date.today()))
        self.verdict.configure(text=f"Saved {len(paths)} file(s) to {bets.ROOT / 'bets'}")

    def grade(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bets.grade_saved("all")
        self.say(buf.getvalue().lstrip("\n") or "No saved bets yet.\n")


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
