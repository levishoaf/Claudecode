import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from nfl_edge import data, fanduel
from nfl_edge.stats import TEAM_ABBR

SAMPLE = json.loads((Path(fanduel.__file__).with_name("sample_odds.json")).read_text())
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def schedule():
    """A week-5 schedule row for each sample game, plus one week-6 game."""
    rows = [{"game_id": f"g{i}", "week": "5", "home_team": TEAM_ABBR[e["home_team"]],
             "away_team": TEAM_ABBR[e["away_team"]]} for i, e in enumerate(SAMPLE)]
    rows.append({"game_id": "later", "week": "6", "home_team": "KC", "away_team": "BUF"})
    return rows


class FanDuelBoardTest(unittest.TestCase):
    def setUp(self):
        fanduel._cache.clear()
        self.calls = []

    def fetch(self, key, markets, regions, sport):
        self.calls.append(("main", markets))
        return SAMPLE, "480"

    def board(self, **kw):
        with mock.patch("nfl_edge.board.upcoming_nfl", return_value=schedule()), \
                mock.patch("nfl_edge.cli.build_model", side_effect=data.DataError("offline")):
            return fanduel.fanduel_board("KEY", "nfl", now=NOW, props=False, fetch=self.fetch, **kw)

    def test_only_fanduel_bets_with_fanduel_prices(self):
        bets, note = self.board()
        self.assertTrue(bets)
        fd = {(o["name"], o.get("point"), o["price"]) for e in SAMPLE for b in e["bookmakers"]
              if b["key"] == "fanduel" for m in b["markets"] for o in m["outcomes"]}
        for b in bets:
            self.assertIn((b.pick, b.point, b.fd_price), fd)  # a real FanDuel selection and price
            self.assertTrue(b.priced)
            self.assertEqual(b.week, 5)
            self.assertTrue(b.game_id.startswith("g"))
        self.assertIn("480 API credits left", note)

    def test_chance_range_and_cache(self):
        bets, _ = self.board(lo=0.6, hi=0.8)
        self.assertTrue(all(0.6 <= b.fair_prob <= 0.8 for b in bets))
        self.board(lo=0.6, hi=0.8)  # same check again within 30 minutes: no new API call
        self.assertEqual(len(self.calls), 1)

    def test_other_weeks_games_left_out(self):
        events = [dict(SAMPLE[0], home_team="Kansas City Chiefs", away_team="Buffalo Bills")]
        with mock.patch("nfl_edge.board.upcoming_nfl", return_value=schedule()):
            bets, note = fanduel.fanduel_board("KEY", "nfl", now=NOW, props=False,
                                               fetch=lambda *a: (events, "5"))
        self.assertEqual(bets, [])
        self.assertIn("no lines yet", note)

    def test_props_drop_out_players_and_flag_questionable(self):
        from nfl_edge.board import make_bet
        ev = SAMPLE[0]
        game = f"{ev['away_team']} @ {ev['home_team']}"
        kick = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)
        props = [make_bet(game, kick, "player_receptions_alternate", f"{name} Over", 3.5, 0.7,
                          "x", "player logs", price=-200) for name in ("Ruled Out", "Maybe Q", "Healthy")]
        statuses = {"ruled out": "Out", "maybe q": "Questionable"}
        with mock.patch("nfl_edge.board.upcoming_nfl", return_value=schedule()), \
                mock.patch("nfl_edge.cli.build_model", side_effect=data.DataError("offline")), \
                mock.patch("nfl_edge.data.games", return_value=[]), \
                mock.patch("nfl_edge.fanduel.LineDistribution"), \
                mock.patch("nfl_edge.fanduel.load_player_model"), \
                mock.patch("nfl_edge.fanduel.injury_statuses", return_value=statuses), \
                mock.patch("nfl_edge.fanduel.price_event",
                           side_effect=lambda e, *a: props if e is ev else []):
            bets, _ = fanduel.fanduel_board("KEY", "nfl", now=NOW, fetch=self.fetch,
                                            fetch_event=lambda *a: ({}, "300"))
        names = {b.pick: b.note for b in bets if b.market.startswith("player_")}
        self.assertEqual(names, {"Maybe Q Over": "Questionable", "Healthy Over": ""})


if __name__ == "__main__":
    unittest.main()
