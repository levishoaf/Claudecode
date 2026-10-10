#!/usr/bin/env python3
"""Record which keyless public odds hosts are reachable from THIS machine (one GET each, no retries,
no workarounds). Writes data/source_probe.json. A 403/tunnel failure is recorded and left alone."""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app import pipeline
from app.providers import USER_AGENT, describe_error

URLS = [
    ("ESPN site API scoreboard", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"),
    ("ESPN site.web header (odds block)", "https://site.web.api.espn.com/apis/v2/scoreboard/header?sport=football&league=nfl"),
    ("ESPN core API per-event odds", "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/401872981/competitions/401872981/odds"),
    ("ESPN cdn scoreboard", "https://cdn.espn.com/core/nfl/scoreboard?xhr=1"),
    ("FanDuel sportsbook API", "https://sbapi.nj.sportsbook.fanduel.com/api/content-managed-page?page=CUSTOM&customPageId=nfl"),
    ("DraftKings sportsbook API", "https://sportsbook-nash.draftkings.com/sites/US-SB/api/v5/eventgroups/88808"),
    ("Action Network scoreboard", "https://api.actionnetwork.com/web/v1/scoreboard/nfl"),
    ("VegasInsider odds page", "https://www.vegasinsider.com/nfl/odds/"),
    ("Covers matchups page", "https://www.covers.com/sports/nfl/matchups"),
    ("OddsShark odds page", "https://www.oddsshark.com/nfl/odds"),
    ("Sportsbook Review odds page", "https://www.sportsbookreview.com/betting-odds/nfl-football/"),
]
rows = []
for name, url in URLS:
    host = url.split("/")[2]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows.append({"name": name, "host": host, "status": r.status, "reachable": True})
    except Exception as exc:
        rows.append({"name": name, "host": host, "status": describe_error(exc), "reachable": False})
    print(f"{rows[-1]['status']!s:45} {name} ({host})")
    time.sleep(1.5)
(pipeline.DATA / "source_probe.json").write_text(json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "results": rows}, indent=1))
