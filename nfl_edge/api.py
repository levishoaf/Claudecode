"""Client for The Odds API (https://the-odds-api.com)."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
SPORT_KEYS = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf"}


class OddsAPIError(RuntimeError):
    pass


def fetch_odds(
    api_key: str,
    markets: list[str],
    regions: list[str],
    sport: str = "nfl",
    timeout: float = 20,
) -> tuple[list[dict], str | None]:
    """Return (events, requests_remaining).

    Each call costs len(markets) * len(regions) credits on your plan.
    """
    params = urllib.parse.urlencode(
        {
            "apiKey": api_key,
            "regions": ",".join(regions),
            "markets": ",".join(markets),
            "oddsFormat": "american",
        }
    )
    try:
        url = BASE_URL.format(sport=SPORT_KEYS[sport])
        with urllib.request.urlopen(f"{url}?{params}", timeout=timeout) as resp:
            remaining = resp.headers.get("x-requests-remaining")
            return json.load(resp), remaining
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise OddsAPIError(f"HTTP {e.code} from The Odds API: {body}") from e
    except (OSError, http.client.HTTPException) as e:  # URL errors, timeouts, dropped connections
        raise OddsAPIError(f"Could not reach The Odds API: {getattr(e, 'reason', e)}") from e


EVENT_URL = "https://api.the-odds-api.com/v4/sports/{sport}/events/{event_id}/odds"


def fetch_event_odds(
    api_key: str,
    event_id: str,
    markets: list[str],
    sport: str = "nfl",
    bookmakers: tuple[str, ...] = ("fanduel", "pinnacle", "draftkings", "betmgm"),
    timeout: float = 20,
) -> tuple[dict, str | None]:
    """Props and alternate lines for one game. Costs about one credit per
    market returned (up to 10 bookmakers count as one region)."""
    params = urllib.parse.urlencode({
        "apiKey": api_key, "markets": ",".join(markets),
        "bookmakers": ",".join(bookmakers), "oddsFormat": "american",
    })
    url = EVENT_URL.format(sport=SPORT_KEYS[sport], event_id=event_id)
    try:
        with urllib.request.urlopen(f"{url}?{params}", timeout=timeout) as resp:
            return json.load(resp), resp.headers.get("x-requests-remaining")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise OddsAPIError(f"HTTP {e.code} from The Odds API: {body}") from e
    except (OSError, http.client.HTTPException) as e:  # URL errors, timeouts, dropped connections
        raise OddsAPIError(f"Could not reach The Odds API: {getattr(e, 'reason', e)}") from e

