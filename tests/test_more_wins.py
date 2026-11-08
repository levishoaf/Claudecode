import unittest
from datetime import datetime, timedelta, timezone

from nfl_edge import learn, picks, tracker
from nfl_edge.board import make_bet

KICK = datetime.now(timezone.utc) + timedelta(days=2)


def bet(i, p, market="h2h", pick=None):
    return make_bet(f"Away{i} @ Home{i}", KICK, market, pick or f"Home{i}", None, p, f"g{i}", "x")


class MinimumChanceTest(unittest.TestCase):
    def test_default_singles_are_70_percent_or_better(self):
        bets = [bet(i, p) for i, p in enumerate([0.85, 0.74, 0.70, 0.69, 0.55, 0.45])]
        shown = picks.rank_singles(bets, 30, picks.SINGLE_MIN_PROB)
        self.assertEqual([round(b.fair_prob, 2) for b in shown], [0.85, 0.74, 0.70])


class UfcLearningTest(unittest.TestCase):
    def test_ufc_bets_learn_under_the_names_ufc_legs_are_graded_as(self):
        ml, dist = bet(1, 0.6), bet(2, 0.6, "fight_distance", "A vs B goes the distance")
        self.assertEqual(learn.group_of_bet(ml, "ufc"), learn.group_of_leg({"type": "fight"}, "ufc"))
        self.assertEqual(learn.group_of_bet(dist, "ufc"),
                         learn.group_of_leg({"type": "distance"}, "ufc"))


class SinglesNoteTest(unittest.TestCase):
    def entries(self, kind, won, lost):
        legs = [{"type": "moneyline", "status": "won"}]
        return ([{"kind": kind, "status": "won", "legs": legs}] * won
                + [{"kind": kind, "status": "lost", "legs": legs}] * lost)

    def test_says_how_much_more_often_singles_won(self):
        note = tracker.singles_vs_parlays(self.entries("single", 31, 19) + self.entries("parlay", 4, 16))
        self.assertIn("62%", note)
        self.assertIn("20%", note)

    def test_quiet_until_there_are_enough(self):
        self.assertIsNone(tracker.singles_vs_parlays(self.entries("single", 5, 2) + self.entries("parlay", 1, 3)))


if __name__ == "__main__":
    unittest.main()
