#!/bin/bash
# Double-click on a Mac to build FanDuel single bets and parlays.
cd "$(dirname "$0")" || exit 1
python3 bets.py
