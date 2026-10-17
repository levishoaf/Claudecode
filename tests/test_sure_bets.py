import argparse
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import bets as launcher
from nfl_edge import board
from nfl_edge.board import make_bet

KICK = datetime.now(timezone.utc) + timedelta(days=2)
G = "Baltimore Ravens @ Atlanta Falcons"
ALL = [make_bet(G, KICK, "h2h", "Atlanta Falcons", None, 0.7, "g", "x"),
       make_bet(G, KICK, "spreads", "Atlanta Falcons", -3.5, 0.6, "g", "x"),
       make_bet(G, KICK, "alternate_spreads", "Atlanta Falcons", 6.5, 0.75, "g", "x"),
       make_bet(G, KICK, "team_totals", "Atlanta Falcons Over", 20.5, 0.65, "g", "x"),
       make_bet(G, KICK, "player_reception_yds_alternate", "Drake London Over", 24.5, 0.8, "g", "x"),
       make_bet(G, KICK, "player_anytime_td", "Bijan Robinson Yes", 0.5, 0.62, "g", "x")]


def args(**kw):
    base = dict(sport="nfl", date="week", week=None, singles=30, legs=3, min_legs=3, max_legs=5,
                parlays=0, stake=10, min_prob=1, max_prob=99, per_game=3, allow_overlap=False,
                games_file=None)
    return argparse.Namespace(**{**base, **kw})


class SureBetsTest(unittest.TestCase):
    def test_sure_markets_have_no_line(self):
        self.assertEqual(board.SURE_MARKETS, {"h2h", "player_anytime_td"})

    def test_free_board_asks_for_sure_bets_only(self):
        with mock.patch("bets.nfl_board", return_value=ALL[:1]) as nb:
            launcher.build(args())
        self.assertTrue(nb.call_args.kwargs.get("always_offered", True))

    def test_other_bet_types_use_every_market_marked_to_confirm(self):
        with mock.patch("bets.nfl_board", return_value=list(ALL)) as nb:
            _, singles, _ = launcher.build(args(bet_type="Spread"))
        self.assertFalse(nb.call_args.kwargs["always_offered"])
        self.assertEqual({b.market for b in singles}, {"spreads", "alternate_spreads"})
        self.assertTrue(all(b.confirm_line for b in singles))


if __name__ == "__main__":
    unittest.main()
