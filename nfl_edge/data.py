"""Download and cache nflverse data files."""

from __future__ import annotations

import csv
import gzip
import http.client
import io
import os
import time
import urllib.request
from pathlib import Path

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
RELEASE_URL = "https://github.com/nflverse/nflverse-data/releases/download/{kind}/{kind}_{season}.csv"

# Files for finished seasons never change; current ones update several times a week.
FRESH_SECONDS = 3 * 3600


class DataError(RuntimeError):
    pass


def cache_dir() -> Path:
    path = Path(os.environ.get("NFL_EDGE_CACHE", Path.home() / ".cache" / "nfl_edge"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            raw = resp.read()
    except (OSError, http.client.HTTPException) as e:  # URL errors, timeouts, dropped connections
        raise DataError(f"Could not download {url}: {e}") from e
    if raw[:2] == b"\x1f\x8b":  # gzip
        raw = gzip.decompress(raw)
    return raw.decode()


def read_csv(source: str | Path, *, permanent: bool = False,
             max_age: float | None = None) -> list[dict]:
    """Rows from a local path or URL. URLs are cached; `permanent` files never
    expire, others are refreshed after `max_age` seconds (default FRESH_SECONDS)."""
    source = str(source)
    if "://" not in source:
        try:
            return list(csv.DictReader(open(source, newline="")))
        except OSError as e:
            raise DataError(f"Could not read {source}: {e}") from e

    cached = cache_dir() / source.rsplit("/", 1)[-1]
    fresh = FRESH_SECONDS if max_age is None else min(max_age, FRESH_SECONDS)
    if cached.exists() and (permanent or time.time() - cached.stat().st_mtime < fresh):
        text = cached.read_text()
    else:
        try:
            text = _download(source)
        except DataError:
            if not cached.exists():
                raise
            text = cached.read_text()  # stale beats nothing
        else:
            cached.write_text(text)
    return list(csv.DictReader(io.StringIO(text)))


def games(source: str | Path | None = None) -> list[dict]:
    """Every NFL game since 1999, including this season's upcoming schedule."""
    return read_csv(source or GAMES_URL)


def read_json(url: str, name: str, max_age: float) -> object:
    """JSON from a URL, cached for `max_age` seconds under `name`."""
    import json

    cached = cache_dir() / name
    if cached.exists() and time.time() - cached.stat().st_mtime < max_age:
        return json.loads(cached.read_text())
    text = _download(url)
    cached.write_text(text)
    return json.loads(text)


def release(kind: str, season: int, current_season: int,
            data_dir: str | Path | None = None, max_age: float | None = None) -> list[dict]:
    """An nflverse per-season release file such as injuries or snap_counts.
    Returns [] if the file doesn't exist yet."""
    if data_dir:
        local = Path(data_dir) / f"{kind}_{season}.csv"
        if local.exists():
            return read_csv(local)
    try:
        return read_csv(RELEASE_URL.format(kind=kind, season=season),
                        permanent=season < current_season, max_age=max_age)
    except DataError:
        return []
