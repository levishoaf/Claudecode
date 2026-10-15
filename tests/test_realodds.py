import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nfl_edge import lines, realodds
from nfl_edge.finder import Bet

FIX = Path(__file__).parent / "fixtures" / "odds"
MAIN = json.loads((FIX / "main.json").read_text())  # Seahawks @ Broncos, Elon @ Stanford
PROPS = json.loads((FIX / "props-nfl.json").read_text())  # Seahawks @ Broncos props
KICK = datetime(2026, 10, 16, 0, 20, tzinfo=timezone.utc)  # a few minutes off the feed's
GAME = "Seattle Seahawks @ Denver Broncos"


def model_bet(market, pick, point, prob, game=GAME):
    return Bet(game, KICK, market, pick, point, -110, prob, prob, prob, 0.0, 0.0, [],
               "g1", False, 7, confirm_line=True)


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.offers = lines.price(MAIN[0], "nfl") + lines.price(PROPS, "nfl")

    def test_game_lines_get_fanduels_price_and_the_market_chance(self):
        ml = model_bet("h2h", "Seattle Seahawks", None, 0.60)
        spread = model_bet("alternate_spreads", "Denver Broncos", 2.5, 0.52)
        total = model_bet("totals", "Over", 40.5, 0.55)
        out = realodds.apply([ml, spread, total], "nfl", self.offers, props=False)
        self.assertEqual(ml.fd_price, -146)
        self.assertAlmostEqual(ml.fair_prob, 0.572, places=3)  # the market's, not the model's
        self.assertEqual(ml.model_prob, 0.60)
        self.assertTrue(ml.priced and ml.on_fanduel and not ml.confirm_line)
        self.assertEqual((spread.fd_price, total.fd_price), (-105, -115))
        self.assertGreaterEqual(ml.best_price, ml.fd_price)
        self.assertEqual(len(out), 3)

    def test_unmatched_bets_are_left_alone(self):
        other = model_bet("h2h", "Dallas Cowboys", None, 0.6, game="Dallas Cowboys @ New York Giants")
        wrong_line = model_bet("spreads", "Denver Broncos", 6.5, 0.7)
        realodds.apply([other, wrong_line], "nfl", self.offers, props=False)
        for b in (other, wrong_line):
            self.assertFalse(b.priced)
            self.assertEqual(b.fair_prob, b.model_prob)
            self.assertTrue(b.confirm_line)

    def test_fanduels_props_replace_the_models(self):
        ml = model_bet("h2h", "Seattle Seahawks", None, 0.6)
        guess = model_bet("player_pass_yds_alternate", "Bo Nix Over", 199.5, 0.7)
        out = realodds.apply([ml, guess], "nfl", self.offers)
        self.assertNotIn(guess, out)  # FanDuel posts its own passing-yards line here
        nix = [b for b in out if b.market == "player_pass_tds" and b.pick == "Bo Nix Over"]
        self.assertEqual(len(nix), 1)
        self.assertEqual((nix[0].point, nix[0].fd_price), (1.5, 172))
        self.assertFalse(any(b.pick.endswith(" No") for b in out))
        td = [b for b in out if b.market == "player_anytime_td"]
        self.assertTrue(td and all(b.rough and b.point == 0.5 for b in td))

    def test_props_only_for_the_nfl(self):
        out = realodds.apply([model_bet("h2h", "Seattle Seahawks", None, 0.6)], "ncaaf",
                             self.offers)
        self.assertEqual(len(out), 1)

    def test_names_match_loosely(self):
        self.assertEqual(realodds.norm("Marvin Mims Jr."), "marvinmims")
        self.assertTrue(realodds._same("Iowa", "Iowa Hawkeyes"))
        self.assertTrue(realodds._same("Alex Pereira", "Alex Poatan Pereira"))
        self.assertFalse(realodds._same("Iowa", "Ohio State Buckeyes"))

    def test_value_list_puts_firm_value_first(self):
        ranked = realodds.value_list(self.offers)
        self.assertEqual(len(ranked), len(self.offers))
        firm = [o["ev"] > 0 and not o["rough"] and not o["check"] for o in ranked]
        self.assertEqual(firm, sorted(firm, reverse=True))


if __name__ == "__main__":
    unittest.main()
