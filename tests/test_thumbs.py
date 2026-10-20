import unittest
from datetime import datetime, timedelta, timezone

from nfl_edge import picks
from nfl_edge.board import make_bet
from nfl_edge.parlays import Parlay

KICK = datetime.now(timezone.utc) + timedelta(days=2)


def bet(i, p=0.6, price=-110, ev=0.03, **kw):
    b = make_bet(f"Away{i} @ Home{i}", KICK, "h2h", f"Home{i}", None, p, f"g{i}", "x")
    b.fd_price, b.ev, b.on_fanduel, b.books_count = price, ev, True, 12
    for k, v in kw.items():
        setattr(b, k, v)
    return b


class ThumbsTest(unittest.TestCase):
    def test_up_only_when_fanduel_pays_clearly_more_than_it_is_worth(self):
        self.assertTrue(picks.worth_it(bet(1, ev=0.03))[0])
        self.assertFalse(picks.worth_it(bet(1, ev=0.005))[0])  # too small to trust
        self.assertFalse(picks.worth_it(bet(1, ev=-0.02))[0])

    def test_unsure_is_thumbs_down(self):
        no_price = make_bet("A @ B", KICK, "h2h", "B", None, 0.8, "g", "x")
        for b in (no_price, bet(1, rough=True), bet(2, check=True), bet(3, books_count=2),
                  bet(4, note="Questionable"), bet(5, tier="red")):
            ok, why = picks.worth_it(b)
            self.assertFalse(ok)
            self.assertTrue(why.startswith("Unsure"), why)

    def test_parlays_need_every_leg_trustworthy_and_value(self):
        good = Parlay([bet(1, p=0.6, price=+110), bet(2, p=0.6, price=+110)])  # 36% at +341
        self.assertTrue(picks.worth_parlay(good)[0])
        shaky = Parlay([bet(1, p=0.6, price=+110), bet(2, p=0.6, price=+110, rough=True)])
        self.assertFalse(picks.worth_parlay(shaky)[0])

    def test_value_lines(self):
        self.assertTrue(picks.worth_offer({"ev": 0.03, "books": 5})[0])
        self.assertFalse(picks.worth_offer({"ev": 0.03, "books": 5, "rough": True})[0])
        self.assertFalse(picks.worth_offer({"ev": 0.03, "books": 2})[0])


if __name__ == "__main__":
    unittest.main()
