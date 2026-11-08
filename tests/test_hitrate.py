import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from nfl_edge import hitrate
from nfl_edge.board import make_bet

KICK = datetime.now(timezone.utc) + timedelta(days=2)
GAME = "Baltimore Ravens @ Atlanta Falcons"


def game(gid, season, home, away, hs, as_):
    return {"game_id": gid, "season": str(season), "game_type": "REG", "home_team": home,
            "away_team": away, "home_score": str(hs), "away_score": str(as_)}


GAMES = [game("1", 2026, "ATL", "TB", 24, 20),   # ATL won by 4, total 44
         game("2", 2026, "NO", "ATL", 30, 10),   # ATL lost by 20, total 40
         game("3", 2026, "BAL", "CLE", 27, 3),   # BAL game, total 30
         game("4", 2025, "ATL", "CAR", 17, 14),
         game("5", 2026, "ATL", "DAL", "", "")]  # not played yet


class HitRateTest(unittest.TestCase):
    def bet(self, market, pick, point=None):
        return make_bet(GAME, KICK, market, pick, point, 0.6, "g", "x")

    def test_team_bets(self):
        ml = hitrate.rates(self.bet("h2h", "Atlanta Falcons"), None, GAMES, 2026)
        self.assertEqual((ml[2026].hits, ml[2026].games), (1, 2))
        self.assertEqual((ml[2025].hits, ml[2025].games), (1, 1))
        spread = hitrate.rates(self.bet("spreads", "Atlanta Falcons", 3.5), None, GAMES, 2026)
        self.assertEqual(spread[2026].hits, 1)  # +4 +3.5 covers; -20 +3.5 doesn't
        total = hitrate.rates(self.bet("totals", "Over", 35.5), None, GAMES, 2026)
        self.assertEqual((total[2026].hits, total[2026].games), (2, 3))  # both teams' games
        tt = hitrate.rates(self.bet("team_totals", "Atlanta Falcons Over", 14.5), None, GAMES,
                           2026)
        self.assertEqual(tt[2026].hits, 1)

    def test_player_props(self):
        logs = [(2026, {"receiving_yards": "30", "receptions": "4", "rushing_tds": "0",
                        "receiving_tds": "1"}),
                (2026, {"receiving_yards": "12", "receptions": "2", "rushing_tds": "0",
                        "receiving_tds": "0"}),
                (2026, {"receiving_yards": "25", "receptions": "3", "rushing_tds": "1",
                        "receiving_tds": "0"})]
        players = SimpleNamespace(games_played={"drake london": logs})
        yds = hitrate.rates(self.bet("player_reception_yds_alternate", "Drake London Over", 24.5),
                            players, GAMES, 2026)
        self.assertEqual(yds[2026].text(), "2 of 3  (67%)")
        self.assertEqual(yds[2025].text(), "no games")
        td = hitrate.rates(self.bet("player_anytime_td", "Drake London Yes", 0.5), players,
                           GAMES, 2026)
        self.assertEqual(td[2026].hits, 2)


class HeadToHeadTest(unittest.TestCase):
    def test_record_against_this_opponent(self):
        games = [game("a", 2024, "ATL", "BAL", 20, 17), game("b", 2025, "BAL", "ATL", 30, 10),
                 game("c", 2026, "ATL", "BAL", 21, 14), game("d", 2023, "ATL", "BAL", 9, 3),
                 game("e", 2026, "ATL", "TB", 7, 3)]
        b = make_bet(GAME, KICK, "h2h", "Atlanta Falcons", None, 0.6, "g", "x")
        h = hitrate.head_to_head(b, games, 2026)
        self.assertEqual((h.hits, h.games), (2, 3))  # 2024-2026 only, Ravens games only
        self.assertEqual(h.results, ("2026 W 21-14", "2025 L 10-30", "2024 W 20-17"))
        self.assertEqual(hitrate.rates(b, None, games, 2026)["h2h"].games, 3)
