"""Client for The Odds API (https://the-odds-api.com)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"


class OddsAPIError(RuntimeError):
    pass


def fetch_nfl_odds(
    api_key: str,
    markets: list[str],
    regions: list[str],
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
        with urllib.request.urlopen(f"{BASE_URL}?{params}", timeout=timeout) as resp:
            remaining = resp.headers.get("x-requests-remaining")
            return json.load(resp), remaining
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise OddsAPIError(f"HTTP {e.code} from The Odds API: {body}") from e
    except urllib.error.URLError as e:
        raise OddsAPIError(f"Could not reach The Odds API: {e.reason}") from e
