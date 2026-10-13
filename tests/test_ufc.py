import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from nfl_edge import board, grade, ufc
from nfl_edge.picks import of_type


def bout(i, a="A", b="B", winner="a", rnd=3, clock=300.0, done=True, date="2024-01-01T00:00Z",
         event="UFC Fight Night: X vs. Y", rounds=3, state="post", weight="Lightweight"):
    return {"id": str(i), "event_id": "e1", "event": event, "date": date, "weight": weight,
            "rounds": rounds, "a": a, "a_name": f"{a} Fighter", "b": b, "b_name": f"{b} Fighter",
            "done": done, "state": state, "round": rnd, "clock": clock, "winner": winner}


class DistanceTest(unittest.TestCase):
    def test_decision_and_finish(self):
        self.assertTrue(ufc.went_distance(bout(1)))
        self.assertTrue(ufc.went_distance(bout(1, rnd=5, rounds=5)))
        self.assertFalse(ufc.went_distance(bout(1, rnd=2, clock=100)))
        self.assertFalse(ufc.went_distance(bout(1, rnd=3, clock=150)))  # finished in the last round
        self.assertIsNone(ufc.went_distance(bout(1, done=False)))


class GradeTest(unittest.TestCase):
    def test_fight_and_distance_legs(self):
        b = bout(7, winner="b", rnd=1, clock=90)
        games = {"7": b}
        won = {"game_id": "7", "type": "fight", "fighter": "B Fighter"}
        lost = {"game_id": "7", "type": "fight", "fighter": "A Fighter"}
        no_distance = {"game_id": "7", "type": "distance", "yes": False}
        self.assertEqual(grade.grade_leg(won, games, [])[0], "won")
        self.assertEqual(grade.grade_leg(lost, games, [])[0], "lost")
        self.assertEqual(grade.grade_leg(no_distance, games, [])[0], "won")
        self.assertIn("round 1", grade.grade_leg(won, games, [])[1])

    def test_draw_pushes_and_unfinished_waits(self):
        self.assertEqual(ufc.grade_leg({"type": "fight", "fighter": "A Fighter"},
                                       bout(1, winner=None))[0], "push")
        self.assertEqual(ufc.grade_leg({"type": "fight", "fighter": "A Fighter"},
                                       bout(1, done=False))[0], "pending")


class RatingsTest(unittest.TestCase):
    def test_winner_gains_and_features_favor_him(self):
        r = ufc.Ratings()
        for i in range(3):
            r.update(bout(i, date=f"2024-0{i + 1}-01T00:00Z"))
        self.assertGreater(r.rec("A").elo, ufc.START_ELO)
        self.assertLess(r.rec("B").elo, ufc.START_ELO)
        win, length = r.features(bout(9, date="2024-06-01T00:00Z", done=False))
        self.assertGreater(win["elo"], 0)
        self.assertEqual(length["bias"], 1.0)
        mirrored, _ = r.features(bout(9, a="B", b="A", date="2024-06-01T00:00Z", done=False))
        self.assertAlmostEqual(mirrored["elo"], -win["elo"])

    def test_age_and_reach_use_bios(self):
        bios = {"A": {"born": "01/01/1990", "reach": 74.0}, "B": {"born": "01/01/1995", "reach": 70.0}}
        win, _ = ufc.Ratings(bios).features(bout(1, done=False))
        self.assertGreater(win["age"], 0)  # A is older
        self.assertGreater(win["reach"], 0)


class CardsTest(unittest.TestCase):
    def test_tryouts_and_started_fights_left_out(self):
        soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%MZ")
        fights = [bout(1, done=False, state="pre", date=soon),
                  bout(2, done=False, state="in", date=soon),
                  dict(bout(3, done=False, state="pre", date=soon),
                       event_id="e2", event="Dana White's Contender Series: Week 1")]
        with mock.patch.object(ufc, "season", return_value=fights):
            cards = ufc.upcoming_cards()
        self.assertEqual([[b["id"] for b in bs] for _, _, bs in cards], [["1"]])


class BoardTest(unittest.TestCase):
    def test_board_makes_moneylines_and_distance_bets(self):
        soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%MZ")
        card = [("e1", "UFC Fight Night: X vs. Y", [bout(1, done=False, state="pre", date=soon)])]
        with mock.patch.object(ufc, "upcoming_cards", return_value=card), \
                mock.patch.object(ufc, "current_ratings", return_value=ufc.Ratings()), \
                mock.patch.object(ufc, "card_numbers", return_value={"1": 40}), \
                mock.patch.object(ufc, "bios", return_value={}), \
                mock.patch.object(ufc, "weights", return_value={
                    "win": {"elo": 0.4}, "distance": {"bias": 0.2},
                    "test": {"win": {"n": 100, "likelier_won": 0.63}}}):
            bets = board.ufc_board(0.0, 1.0)
        self.assertEqual(len(bets), 4)
        self.assertEqual(len(of_type(bets, "Moneyline")), 2)
        distance = of_type(bets, "Fight distance")
        self.assertEqual([b.pick for b in distance],
                         ["Fighter vs Fighter goes the distance",
                          "Fighter vs Fighter doesn't go the distance"])
        self.assertAlmostEqual(sum(b.fair_prob for b in distance), 1.0)
        self.assertEqual(board.NOTES["ufc_card"], "UFC Fight Night: X vs. Y")

    def test_saved_legs_grade_as_fights(self):
        import bets as launcher

        soon = datetime.now(timezone.utc) + timedelta(days=2)
        ml = board.make_bet("A Fighter vs B Fighter", soon, "h2h", "A Fighter", None, 0.6, "1",
                            "UFC model")
        dist = board.make_bet("A Fighter vs B Fighter", soon, "fight_distance",
                              "Fighter vs Fighter goes the distance", None, 0.55, "1", "UFC model")
        self.assertEqual(launcher.leg_record(ml, "ufc")["type"], "fight")
        self.assertEqual(launcher.leg_record(ml, "ufc")["fighter"], "A Fighter")
        self.assertTrue(launcher.leg_record(dist, "ufc")["yes"])


class WeightsTest(unittest.TestCase):
    def test_shipped_weights_were_tested(self):
        w = ufc.weights()
        self.assertTrue(w.get("win") and w.get("distance"))
        test = w["test"]
        # The model must beat a coin flip and the always-guess rate on fights it never saw.
        self.assertLess(test["win"]["log_loss"], 0.6931)
        self.assertLess(test["distance"]["log_loss"], test["distance_base_rate"]["log_loss"])


if __name__ == "__main__":
    unittest.main()
