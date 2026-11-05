import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from nfl_edge import grade, picks, tracker
from nfl_edge.context import name_key
from nfl_edge.finder import Bet

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
LEG = {"type": "player_stat", "stat": "receiving_yards", "min": 25, "player": "Marvin Harrison Jr.",
       "game_id": "2026_05_ARI_TEN", "kickoff": "2026-10-04T13:00:00-04:00"}
GAMES = {"2026_05_ARI_TEN": {"game_id": "2026_05_ARI_TEN", "home_team": "TEN", "away_team": "ARI",
                            "home_score": "20", "away_score": "23"}}
STATS = [{"game_id": "2026_05_ARI_TEN", "player_display_name": "Calvin Ridley", "receiving_yards": "40"}]


class DidNotPlayTest(unittest.TestCase):
    def setUp(self):
        grade._SNAPS.clear()

    def snaps(self, rows):
        return mock.patch.object(grade.data, "release", return_value=rows)

    def test_a_player_who_sat_out_is_refunded(self):
        with self.snaps([{"game_id": "2026_05_ARI_TEN", "player": "Calvin Ridley",
                          "offense_snaps": "60", "st_snaps": "0"}]):
            self.assertEqual(grade.grade_leg(dict(LEG), GAMES, STATS), ("push", grade.DNP))

    def test_a_player_who_played_and_did_nothing_loses(self):
        with self.snaps([{"game_id": "2026_05_ARI_TEN", "player": "Marvin Harrison Jr.",
                          "offense_snaps": "12", "st_snaps": "0"}]):
            self.assertEqual(grade.grade_leg(dict(LEG), GAMES, STATS)[0], "lost")

    def test_waits_for_snap_counts_then_refunds(self):
        recent = dict(LEG, kickoff=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        with self.snaps([]):
            self.assertEqual(grade.grade_leg(recent, GAMES, STATS)[0], "pending")
            self.assertEqual(grade.grade_leg(dict(LEG), GAMES, STATS)[0], "push")  # weeks ago

    def test_old_losses_are_looked_at_again(self):
        leg = dict(LEG, status="lost", detail=grade.OLD_DNP, id="x")
        ledger = {"placed": [], "generated": [{"id": "s", "kind": "single", "sport": "nfl",
                                                "season": 2026, "status": "lost", "legs": [leg]}]}
        with self.snaps([]), mock.patch.object(tracker.data, "read_csv", return_value=STATS), \
                mock.patch.object(tracker.data, "games", return_value=list(GAMES.values())):
            tracker.regrade(ledger)
        self.assertEqual((leg["status"], ledger["generated"][0]["status"]), ("push", "push"))


class WithdrawInjuredTest(unittest.TestCase):
    def entry(self, player, kickoff, sport="nfl"):
        return {"id": player, "kind": "single", "sport": sport, "status": "pending",
                "legs": [{"player": player, "status": "pending", "kickoff": kickoff.isoformat()}]}

    def test_drops_upcoming_picks_on_players_since_listed(self):
        soon, gone = NOW + timedelta(days=2), NOW - timedelta(hours=1)
        ledger = {"generated": [self.entry("Ladd McConkey", soon), self.entry("Puka Nacua", soon),
                                self.entry("Jordan Addison", gone)]}
        statuses = {name_key("Ladd McConkey"): "Questionable", name_key("Jordan Addison"): "Out"}
        self.assertEqual(tracker.withdraw_injured(ledger, statuses, NOW), 1)
        self.assertEqual([e["id"] for e in ledger["generated"]], ["Puka Nacua", "Jordan Addison"])


class ParlayLengthTest(unittest.TestCase):
    def bets(self, n=12):
        kick = NOW + timedelta(days=3)
        return [Bet(f"T{i} @ U{i}", kick, "h2h", f"T{i}", None, -200, 0.7 - i / 100, 0.7, 0.7,
                    0.0, 0.0, [], f"g{i}", False) for i in range(n)]

    def test_no_parlay_has_more_than_three_legs(self):
        found = (picks.payout_parlays(self.bets(), 4.0, 10, max_legs=6)
                 + picks.mixed_parlays(self.bets(), 3, 6, 10)
                 + picks.best_parlays(self.bets(), 5, 3)
                 + picks.money_maker_parlays(self.bets(), 5))
        self.assertTrue(found)
        self.assertLessEqual(max(len(p.legs) for p in found), picks.MAX_LEGS)
        self.assertEqual(picks.MULTI_LEGS[1], 3)


if __name__ == "__main__":
    unittest.main()
