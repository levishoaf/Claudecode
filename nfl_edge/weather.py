"""Kickoff weather forecasts from Open-Meteo (free, no key)."""

from __future__ import annotations

import http.client
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .venues import STADIUMS

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
RETRY_SECONDS = 600  # after a failed forecast, skip weather this long rather than wait on each game
_down_until = 0.0


def kickoff_forecast(stadium_id: str, kickoff: datetime) -> dict | None:
    """{'temp': F, 'wind': mph, 'precip': in/hr} at kickoff, or None if
    unavailable (unknown venue, too far ahead, or network error)."""
    global _down_until
    if stadium_id not in STADIUMS or time.monotonic() < _down_until:
        return None
    lat, lon = STADIUMS[stadium_id]
    params = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,precipitation",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "precipitation_unit": "inch", "timezone": "GMT", "forecast_days": 16,
    })
    try:
        with urllib.request.urlopen(f"{FORECAST_URL}?{params}", timeout=20) as resp:
            hourly = json.load(resp)["hourly"]
    except (OSError, http.client.HTTPException, KeyError, ValueError):
        # Timeouts and dropped connections too: the picks go ahead without weather.
        _down_until = time.monotonic() + RETRY_SECONDS
        return None
    target = kickoff.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
    try:
        i = hourly["time"].index(target)
    except ValueError:
        return None
    return {"temp": hourly["temperature_2m"][i], "wind": hourly["wind_speed_10m"][i],
            "precip": hourly["precipitation"][i]}
