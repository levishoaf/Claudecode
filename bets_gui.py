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
import tkinter as tk
import tkinter.font as tkfont
from datetime import date
from tkinter import ttk

import bets  # sets up paths for the standalone build
from nfl_edge import data
from nfl_edge.cli import _fmt_american, _slip_market, _slip_selection, _slip_time
from nfl_edge.odds import decimal_to_american

W, H = 1180, 870
PANEL_W = W - 2 * W * 0.08 - 90  # leaves turf visible on both sides
NUMBER_ROWS = (163, 792)  # yard numbers sit in the gaps between panels

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
    box = tk.Frame(parent, bg=bg, padx=12, pady=6, highlightbackground="#c7dbff",
                   highlightthickness=1)
    tk.Label(box, text=text, bg=bg, fg=BLUE, font=f_big).pack()
    tk.Label(box, text=caption, bg=bg, fg=MUTED, font=f_small).pack()
    return box


class App:
    def __init__(self, root: tk.Tk, selftest: bool = False):
        self.root, self.selftest = root, selftest
        self.singles, self.parlays, self.previous = [], [], {}
        self.results: queue.Queue = queue.Queue()
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
        self.header(c)
        self.output(c)
        self.checker(c)

        self.say("Press  Build bets  to load this week's picks.")
        root.after(100, self.poll)
        if selftest:
            root.after(200, self.build)

    # -------------------------------------------------------------- layout
    def style(self) -> None:
        s = ttk.Style(self.root)
        s.theme_use("clam")
        for name in ("TEntry", "TCombobox"):
            s.configure(name, fieldbackground=WHITE, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=WHITE, darkcolor=WHITE, insertcolor=TEXT, padding=5,
                        arrowcolor=BLUE, background=WHITE)
        s.map("TCombobox", fieldbackground=[("readonly", WHITE)])
        s.configure("Vertical.TScrollbar", troughcolor=BG, background="#c3ccd8",
                    bordercolor=BG, arrowcolor=MUTED, lightcolor="#c3ccd8", darkcolor="#c3ccd8")

    def header(self, c: tk.Canvas) -> None:
        top = tk.Frame(c, bg=NAVY)
        row1 = tk.Frame(top, bg=NAVY)
        row1.pack(fill="x", padx=18, pady=(12, 4))
        tk.Label(row1, text="Bet Builder", bg=NAVY, fg=WHITE, font=self.f["title"]).pack(side="left")
        tk.Label(row1, text="   Free data  ·  odds shown are break-even: bet only if FanDuel pays that "
                            "or better", bg=NAVY, fg="#9fb0c8", font=self.f["small"]).pack(side="left")

        self.sport = tk.StringVar(value="nfl")
        seg = tk.Frame(row1, bg=NAVY_2)
        seg.pack(side="right")
        self.seg_btns = {}
        for key, text in (("nfl", "NFL"), ("ncaaf", "College")):
            b = FlatButton(seg, text, lambda k=key: self.pick_sport(k), "ghost", self.f["bold"])
            b.pack(side="left")
            self.seg_btns[key] = b
        self.pick_sport("nfl")

        row2 = tk.Frame(top, bg=NAVY)
        row2.pack(fill="x", padx=18, pady=(6, 14))
        self.when = tk.StringVar(value="week")
        self.n_singles, self.legs, self.n_parlays = (tk.StringVar(value=v) for v in ("10", "3", "3"))
        self.stake, self.lo, self.hi, self.refresh = (tk.StringVar(value=v)
                                                       for v in ("10", "60", "80", "0"))

        def field(label, var, width, values=None):
            box = tk.Frame(row2, bg=NAVY)
            box.pack(side="left", padx=(0, 8))
            tk.Label(box, text=label.upper(), bg=NAVY, fg="#9fb0c8",
                     font=self.f["label"]).pack(anchor="w")
            if values:
                ttk.Combobox(box, textvariable=var, values=values, width=width,
                             font=self.f["body"]).pack(anchor="w")
            else:
                ttk.Entry(box, textvariable=var, width=width, font=self.f["body"]).pack(anchor="w")

        field("Games", self.when, 10, ["week", "today", date.today().isoformat()])
        field("Singles", self.n_singles, 4)
        field("Legs", self.legs, 3)
        field("Parlays", self.n_parlays, 3)
        field("Wager $", self.stake, 5)
        field("Min %", self.lo, 3)
        field("Max %", self.hi, 3)
        field("Refresh", self.refresh, 3)

        btns = tk.Frame(row2, bg=NAVY)
        btns.pack(side="right", anchor="s")
        self.build_btn = FlatButton(btns, "Build bets", self.build, "primary", self.f["bold"])
        self.build_btn.pack(side="left", padx=(0, 6))
        FlatButton(btns, "Save", self.save, "secondary", self.f["bold"]).pack(side="left", padx=(0, 6))
        FlatButton(btns, "Grade", self.grade, "secondary", self.f["bold"]).pack(side="left")
        c.create_window(W / 2, 72, window=top, width=PANEL_W)

    def pick_sport(self, key: str) -> None:
        self.sport.set(key)
        for k, b in self.seg_btns.items():
            b.set_kind("primary" if k == key else "ghost")

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
        c.create_window(W / 2, 478, window=out, width=PANEL_W, height=570)

    def checker(self, c: tk.Canvas) -> None:
        chk = tk.Frame(c, bg=NAVY)
        tk.Label(chk, text="Check FanDuel's price", bg=NAVY, fg=WHITE,
                 font=self.f["bold"]).pack(side="left", padx=(14, 8))
        self.check_entry = ttk.Entry(chk, width=12, font=self.f["body"])
        self.check_entry.pack(side="left")
        self.check_entry.bind("<Return>", lambda e: self.check())
        FlatButton(chk, "Check", self.check, "primary", self.f["bold"]).pack(side="left", padx=8)
        self.verdict = tk.Label(chk, text="e.g.  3 -150   or   P1 +240", bg=NAVY, fg="#9fb0c8",
                                anchor="w", justify="left", font=self.f["small"],
                                wraplength=int(PANEL_W) - 420)
        self.verdict.pack(side="left", fill="x", expand=True, padx=6)
        c.create_window(W / 2, 836, window=chk, width=PANEL_W, height=52)

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
        self.clear()
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 6))
        sport = "NFL" if args.sport == "nfl" else "College Football"
        tk.Label(head, text=f"{sport}  ·  {bets.week_label(singles, parlays)}", bg=BG, fg=TEXT,
                 font=self.f["h2"]).pack(side="left")
        tk.Label(head, text=f"   {len(singles)} single bets  ·  {len(parlays)} parlays  ·  "
                            f"{args.min_prob:.0f}–{args.max_prob:.0f}% chance, likeliest first",
                 bg=BG, fg=MUTED, font=self.f["small"]).pack(side="left", pady=(3, 0))

        cols = tk.Frame(self.body, bg=BG)
        cols.pack(fill="both", padx=10)
        left, right = tk.Frame(cols, bg=BG), tk.Frame(cols, bg=BG)
        left.pack(side="left", anchor="n", padx=6)
        right.pack(side="left", anchor="n", padx=6)
        tk.Frame(left, bg=BG, width=468, height=1).pack()
        tk.Frame(right, bg=BG, width=372, height=1).pack()

        tk.Label(left, text="SINGLE BETS", bg=BG, fg=MUTED, font=self.f["label"]).pack(
            anchor="w", pady=(6, 4))
        if not singles:
            tk.Label(left, text="None found. Try a wider chance range or another date.", bg=BG,
                     fg=TEXT, font=self.f["body"]).pack(anchor="w")
        for i, b in enumerate(singles, 1):
            self.single_card(left, i, b, previous)

        tk.Label(right, text=f"{args.legs}-LEG PARLAYS", bg=BG, fg=MUTED,
                 font=self.f["label"]).pack(anchor="w", pady=(6, 4))
        if args.parlays and not parlays:
            tk.Label(right, text="Not enough games for that many legs.", bg=BG, fg=TEXT,
                     font=self.f["body"]).pack(anchor="w")
        for i, p in enumerate(parlays, 1):
            self.parlay_card(right, i, p, args.stake)
        tk.Label(self.body, text="Odds shown are break-even: bet only if FanDuel pays that or "
                                 "better. Check any price at the bottom.",
                 bg=BG, fg=MUTED, font=self.f["tiny"]).pack(anchor="w", padx=16, pady=10)

    def card(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="x", pady=5)
        return frame

    def single_card(self, parent, i: int, b, previous) -> None:
        card = self.card(parent)
        badge = tk.Canvas(card, width=30, height=30, bg=CARD, highlightthickness=0)
        badge.create_oval(2, 2, 28, 28, fill=NAVY, outline="")
        badge.create_text(15, 15, text=str(i), fill=WHITE, font=self.f["tinyb"])
        badge.pack(side="left", anchor="n", padx=(12, 4), pady=12)

        odds_button(card, _fmt_american(decimal_to_american(1 / b.fair_prob)), "break-even",
                    self.f["odds"], self.f["tiny"]).pack(side="right", padx=12, pady=12)

        mid = tk.Frame(card, bg=CARD)
        mid.pack(side="left", fill="both", expand=True, padx=6, pady=10)
        top = tk.Frame(mid, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text=_slip_selection(b), bg=CARD, fg=TEXT, font=self.f["pick"],
                 wraplength=270, justify="left").pack(side="left")
        old = previous.get(bets.label(b)) if previous else None
        if previous and old is None:
            tk.Label(top, text=" NEW ", bg=NEW_BADGE, fg=WHITE, font=self.f["tinyb"]).pack(
                side="left", padx=6)
        elif old is not None and abs(old - b.fair_prob) >= 0.02:
            tk.Label(top, text=f" was {old:.0%} ", bg=MOVED_BADGE, fg=WHITE,
                     font=self.f["tinyb"]).pack(side="left", padx=6)
        tk.Label(mid, text=_slip_market(b), bg=CARD, fg=BLUE, font=self.f["tinyb"]).pack(anchor="w")
        meta = f"{b.game}  ·  {_slip_time(b)}"
        if b.priced:
            meta += f"  ·  consensus {_fmt_american(b.fd_price)}"
        tk.Label(mid, text=meta, bg=CARD, fg=MUTED, font=self.f["tiny"], wraplength=290,
                 justify="left").pack(anchor="w")
        bar(mid, b.fair_prob, CARD, 150, self.f["tinyb"]).pack(anchor="w", pady=(6, 0))

    def parlay_card(self, parent, i: int, p, wager: float) -> None:
        card = self.card(parent)
        head = tk.Frame(card, bg=NAVY)
        head.pack(fill="x")
        tk.Label(head, text=f"Parlay {i}", bg=NAVY, fg=WHITE, font=self.f["bold"]).pack(
            side="left", padx=(12, 4), pady=8)
        tk.Label(head, text=f"{len(p.legs)} legs", bg=NAVY, fg="#9fb0c8",
                 font=self.f["small"]).pack(side="left")
        tk.Label(head, text=f" {_fmt_american(decimal_to_american(1 / p.win_prob))} ", bg=BLUE,
                 fg=WHITE, font=self.f["bold"]).pack(side="right", padx=10)
        for n, b in enumerate(p.legs):
            if n:
                tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=12)
            leg = tk.Frame(card, bg=CARD)
            leg.pack(fill="x", padx=12, pady=7)
            tk.Label(leg, text=_fmt_american(decimal_to_american(1 / b.fair_prob)), bg=CARD,
                     fg=BLUE, font=self.f["bold"]).pack(side="right", anchor="n")
            dot = tk.Canvas(leg, width=12, height=12, bg=CARD, highlightthickness=0)
            dot.create_oval(1, 1, 11, 11, outline=BLUE, width=2)
            dot.pack(side="left", anchor="n", pady=3)
            txt = tk.Frame(leg, bg=CARD)
            txt.pack(side="left", padx=8)
            tk.Label(txt, text=_slip_selection(b), bg=CARD, fg=TEXT, font=self.f["bold"],
                     wraplength=240, justify="left").pack(anchor="w")
            tk.Label(txt, text=_slip_market(b), bg=CARD, fg=BLUE, font=self.f["tinyb"]).pack(anchor="w")
            tk.Label(txt, text=f"{b.game}  ·  {_slip_time(b)}", bg=CARD, fg=MUTED,
                     font=self.f["tiny"], wraplength=240, justify="left").pack(anchor="w")
        foot = tk.Frame(card, bg="#f6f8fb")
        foot.pack(fill="x")
        payout = wager / p.win_prob
        for title, value in (("Wager", f"${wager:,.2f}"), ("To win*", f"${payout - wager:,.2f}"),
                             ("Payout*", f"${payout:,.2f}")):
            box = tk.Frame(foot, bg="#f6f8fb")
            box.pack(side="left", expand=True, pady=8)
            tk.Label(box, text=title.upper(), bg="#f6f8fb", fg=MUTED, font=self.f["tinyb"]).pack()
            tk.Label(box, text=value, bg="#f6f8fb", fg=TEXT, font=self.f["pick"]).pack()
        bar(card, p.win_prob, CARD, 190, self.f["tinyb"]).pack(anchor="w", padx=12, pady=8)

    # -------------------------------------------------------------- actions
    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            sport=self.sport.get(), date=self.when.get(), singles=int(self.n_singles.get() or 10),
            legs=int(self.legs.get() or 3), parlays=int(self.n_parlays.get() or 0),
            stake=float(self.stake.get() or 10), min_prob=float(self.lo.get() or 60),
            max_prob=float(self.hi.get() or 80), per_game=3, allow_overlap=False, games_file=None)

    def build(self) -> None:
        try:
            args = self.args()
        except ValueError:
            self.say("Please use numbers for singles, legs, parlays, wager and chance.")
            return
        self.build_btn.configure(text="Loading...")

        def work():
            try:
                _, singles, parlays = bets.build(args)
                self.results.put(("ok", args, singles, parlays))
            except (data.DataError, ValueError) as e:
                self.results.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

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
            _, args, self.singles, self.parlays = item
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
        self.verdict.configure(text=bets.check_price(self.singles, self.parlays, entry), fg=WHITE)

    def save(self) -> None:
        if not self.singles and not self.parlays:
            self.verdict.configure(text="Build bets first.")
            return
        args = self.args()
        paths = bets.save(self.singles, self.parlays, args.sport, bets.season_for(date.today()))
        self.verdict.configure(text=f"Saved {len(paths)} file(s) to {bets.ROOT / 'bets'}", fg=WHITE)

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
