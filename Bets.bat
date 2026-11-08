@echo off
rem Double-click on Windows to build FanDuel single bets and parlays.
cd /d "%~dp0"
py -3 bets.py %*
if errorlevel 9009 python bets.py %*
pause
