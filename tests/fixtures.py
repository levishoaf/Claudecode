"""Small synthetic nflverse-style rows for tests."""

COLUMNS = ["season", "game_type", "week", "gameday", "away_team", "away_score", "home_team",
           "home_score", "location", "away_rest", "home_rest", "spread_line", "total_line",
           "div_game", "roof", "temp", "wind", "away_qb_id", "home_qb_id", "away_qb_name",
           "home_qb_name", "stadium_id"]

STADIUM = {"KC": "KAN00", "BUF": "BUF00", "GB": "GNB00", "CHI": "CHI98", "MIA": "MIA00"}
QB = {"KC": ("kc1", "KC Starter"), "BUF": ("buf1", "BUF Starter"), "GB": ("gb1", "GB Starter"),
      "CHI": ("chi1", "CHI Starter"), "MIA": ("mia1", "MIA Starter")}


def game(week, away, home, away_score=None, home_score=None, **kw):
    row = dict.fromkeys(COLUMNS, "")
    row.update(
        season="2026", game_type="REG", week=str(week), gameday=f"2026-09-{week + 9:02d}",
        away_team=away, home_team=home, location="Home", away_rest="7", home_rest="7",
        div_game="0", roof="outdoors", stadium_id=STADIUM[home],
        away_score="" if away_score is None else str(away_score),
        home_score="" if home_score is None else str(home_score),
    )
    if home_score is not None:
        row.update(away_qb_id=QB[away][0], away_qb_name=QB[away][1],
                   home_qb_id=QB[home][0], home_qb_name=QB[home][1])
    row.update({k: str(v) for k, v in kw.items()})
    return row


def season():
    """KC is dominant; week 5 is upcoming."""
    teams = ["BUF", "GB", "CHI", "MIA"]
    rows = []
    for w in range(1, 5):
        rows.append(game(w, teams[w % 4], "KC", 13, 30))
        a, b = teams[(w + 1) % 4], teams[(w + 2) % 4]
        rows.append(game(w, a, b, 20, 21))
    rows.append(game(5, "BUF", "KC"))
    return rows


def snaps(team, player, position, off, dfn, weeks=range(1, 5)):
    return [{"game_type": "REG", "team": team, "week": str(w), "player": player,
             "position": position, "offense_pct": str(off), "defense_pct": str(dfn)}
            for w in weeks]


def injury(team, week, name, position, status, gsis_id=""):
    return {"game_type": "REG", "team": team, "week": str(week), "full_name": name,
            "position": position, "report_status": status, "gsis_id": gsis_id}
