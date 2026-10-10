import io
import json
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone

from nfl_edge import fdfeed
from nfl_edge.board import make_bet

KICK = datetime.now(timezone.utc) + timedelta(days=2)
GAME = "Baltimore Ravens @ Atlanta Falcons"


def runner(name, odds, handicap=None):
    r = {"runnerName": name, "runnerStatus": "ACTIVE",
         "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": odds}}}
    if handicap is not None:
        r["handicap"] = handicap
    return r


LEAGUE = {"attachments": {
    "events": {"101": {"eventId": 101, "name": "Baltimore Ravens @ Atlanta Falcons"}},
    "markets": {
        "1": {"eventId": 101, "marketName": "Moneyline", "marketType": "MONEY_LINE",
              "marketStatus": "OPEN", "runners": [runner("Baltimore Ravens", "+140"),
                                                  runner("Atlanta Falcons", "-166")]},
        "2": {"eventId": 101, "marketName": "Spread", "marketType": "MATCH_HANDICAP_(2-WAY)",
              "marketStatus": "OPEN", "runners": [runner("Baltimore Ravens", "-110", 3.5),
                                                  runner("Atlanta Falcons", "-110", -3.5)]},
        "3": {"eventId": 101, "marketName": "Total Points", "marketType": "TOTAL_POINTS_(OVER/UNDER)",
              "marketStatus": "OPEN", "runners": [runner("Over", "-112", 43.5),
                                                  runner("Under", "-108", 43.5)]},
        "4": {"eventId": 101, "marketName": "Suspended market", "marketType": "MONEY_LINE",
              "marketStatus": "SUSPENDED", "runners": [runner("Baltimore Ravens", "+500")]}}}}
EVENT = {"layout": {}, "attachments": {
    "events": {"101": {"eventId": 101, "name": "Baltimore Ravens @ Atlanta Falcons"}},
    "markets": {
        "9": {"eventId": 101, "marketName": "Any Time Touchdown Scorer",
              "marketType": "ANY_TIME_TOUCHDOWN_SCORER", "marketStatus": "OPEN",
              "runners": [runner("Derrick Henry", "-185"), runner("Bijan Robinson", "-200")]},
        "11": {"eventId": 101, "marketName": "Drake London - Alt Receptions",
               "marketType": "PLAYER_ALT_RECEPTIONS", "marketStatus": "OPEN",
               "runners": [runner("Drake London 3+ Receptions", "-400"),
                           runner("Drake London 4+ Receptions", "-220"),
                           runner("Drake London 6+ Receptions", "+150")]},
        "12": {"eventId": 101, "marketName": "Alt Receiving Yds", "marketType": "ALT_RECEIVING_YARDS",
               "marketStatus": "OPEN",
               "runners": [runner("Derrick Henry 25+ Yds", "-150")]},
        "10": {"eventId": 101, "marketName": "Atlanta Falcons Total Points",
               "marketType": "AWAY_TOTAL_POINTS", "marketStatus": "OPEN",
               "runners": [runner("Over", "-115", 23.5), runner("Under", "-105", 23.5)]}}}}


def opener(pages):
    """A stand-in for urlopen that serves the league page or the event page."""
    def open_(req, timeout=None):
        url = req.full_url
        if "content-managed-page" in url:
            body = pages["league"]
        elif "event-page" in url:
            body = pages.get("event", {})
        else:
            raise AssertionError(url)
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(json.dumps(body).encode())
    return open_


def bet(market, pick, point, prob):
    return make_bet(GAME, KICK, market, pick, point, prob, "g1", "x", week=5)


class VerifyTest(unittest.TestCase):
    def setUp(self):
        fdfeed._cache.clear()

    def test_keeps_only_listed_bets_at_fanduel_prices(self):
        bets = [bet("h2h", "Atlanta Falcons", None, 0.62),
                bet("spreads", "Atlanta Falcons", -3.5, 0.52),
                bet("spreads", "Atlanta Falcons", -3.0, 0.55),       # FanDuel has -3.5, not -3
                bet("totals", "Under", 43.5, 0.51),
                bet("team_totals", "Atlanta Falcons Over", 23.5, 0.5),
                bet("team_totals", "Baltimore Ravens Over", 20.5, 0.5),  # not listed
                bet("player_anytime_td", "Derrick Henry Yes", 0.5, 0.6),
                bet("player_anytime_td", "Mark Andrews Yes", 0.5, 0.3),  # not listed
                bet("alternate_spreads", "Atlanta Falcons", -10.5, 0.2)]  # not listed
        kept, note = fdfeed.verify(bets, "NJ", opener=opener({"league": LEAGUE, "event": EVENT}))
        got = {(b.market, b.pick, b.point): b.fd_price for b in kept}
        self.assertEqual(got, {("h2h", "Atlanta Falcons", None): -166,
                               ("spreads", "Atlanta Falcons", -3.5): -110,
                               ("totals", "Under", 43.5): -108,
                               ("team_totals", "Atlanta Falcons Over", 23.5): -115,
                               ("player_anytime_td", "Derrick Henry Yes", 0.5): -185})
        self.assertTrue(all(b.on_fanduel and b.priced for b in kept))
        falcons = next(b for b in kept if b.market == "h2h")
        self.assertAlmostEqual(falcons.ev, 0.62 * (1 + 100 / 166) - 1)
        self.assertIn("5 bets confirmed, 4 not found and hidden", note)

    def test_suspended_markets_dont_count(self):
        kept, _ = fdfeed.verify([bet("h2h", "Baltimore Ravens", None, 0.4)], "nj",
                                opener=opener({"league": LEAGUE}))
        self.assertEqual(kept[0].fd_price, 140)  # the open market's price, not the suspended one

    def test_failures_confirm_nothing(self):
        blocked = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with self.assertRaises(fdfeed.FeedError) as e:
            fdfeed.verify([bet("h2h", "Atlanta Falcons", None, 0.6)], "nj",
                          opener=opener({"league": blocked}))
        self.assertIn("location", str(e.exception))
        with self.assertRaises(fdfeed.FeedError):  # an unrecognised page: no games
            fdfeed.verify([], "nj", opener=opener({"league": {"something": "else"}}))
        with self.assertRaises(fdfeed.FeedError):
            fdfeed.verify([], "xx", opener=opener({"league": LEAGUE}))

    def test_missing_event_tab_only_drops_those_bets(self):
        kept, _ = fdfeed.verify([bet("h2h", "Atlanta Falcons", None, 0.6),
                                 bet("player_anytime_td", "Derrick Henry Yes", 0.5, 0.6)], "nj",
                                opener=opener({"league": LEAGUE,
                                               "event": urllib.error.URLError("down")}))
        self.assertEqual([b.market for b in kept], ["h2h"])

    def test_prop_ladders(self):
        bets = [bet("player_receptions_alternate", "Drake London Over", 3.5, 0.75),     # 4+
                bet("player_receptions_alternate", "Drake London Over", 4.5, 0.55),     # 5+: not listed
                bet("player_reception_yds_alternate", "Derrick Henry Over", 24.5, 0.6),  # 25+
                bet("player_rush_yds_alternate", "Derrick Henry Over", 24.5, 0.9)]      # wrong stat
        kept, _ = fdfeed.verify(bets, "nj", opener=opener({"league": LEAGUE, "event": EVENT}))
        self.assertEqual([(b.pick, b.point, b.fd_price) for b in kept],
                         [("Drake London Over", 3.5, -220), ("Derrick Henry Over", 24.5, -150)])


if __name__ == "__main__":
    unittest.main()
