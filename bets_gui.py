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

W, H = 1180, 870
PANEL_W = W - 2 * W * 0.08 - 90  # leaves turf visible on both sides
NUMBER_ROWS = (163, 792)  # yard numbers sit in the gaps between panels
TURF = ("#2f7d32", "#2a7130")  # alternating 5-yard stripes
ENDZONE = "#1d4d20"
PANEL, PANEL_EDGE, INK, GOLD = "#0f1a10", "#e8e2c8", "#f4f1e4", "#f2c14e"
MONO = ("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10)


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
        self.text = tk.Text(out, bg="#0b130c", fg=INK, insertbackground=INK, font=MONO,
                            wrap="none", relief="flat", padx=10, pady=8)
        scroll = ttk.Scrollbar(out, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
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

    def say(self, text: str, clear: bool = True) -> None:
        if clear:
            self.text.delete("1.0", "end")
        self.text.insert("end", text)

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
            self.say(text.lstrip("\n"))
            self.previous = {bets.label(b): b.fair_prob for b in self.singles}
            minutes = float(self.refresh.get() or 0)
            if minutes > 0:
                data.FRESH_SECONDS = min(data.FRESH_SECONDS, minutes * 60)
                self.root.after(int(minutes * 60_000), self.build)
        if self.selftest:
            print(self.text.get("1.0", "end")[:400])
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
