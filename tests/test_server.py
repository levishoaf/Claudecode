import json
import os
import unittest
from unittest import mock

try:
    from fastapi.testclient import TestClient
except ImportError:  # the server's packages aren't on the app's build machines
    TestClient = None

SAMPLE = {
    "updated": "Wed Oct 14, 9:00 AM ET",
    "leagueTypes": {"nfl": ["All bets", "Moneyline"], "ncaaf": ["All bets"], "ufc": ["All bets"],
                    "mlb": ["All bets"]},
    "nfl": [{"week": 6, "label": "Week 6 · this week", "note": "", "singles":
             [{"label": f"Team {i} ML", "prob": 0.9 - i / 100} for i in range(10)],
             "parlays": [{"prob": 0.3, "legs": []}], "money": [{"prob": 0.45, "legs": []}],
             "types": {"Moneyline": {"singles": [{"label": "Team 0 ML", "prob": 0.9}],
                                     "parlays": [], "money": []}}}],
    "ncaaf": [], "ufc": [], "mlb": [],
    "multi": [{"prob": 0.25, "legs": []}],
    "history": [{"status": "won", "model": False, "created": "2026-10-10"},
                {"status": "lost", "model": False, "created": "2026-10-11"},
                {"status": "won", "model": True, "created": "2026-10-12"},
                {"status": "pending", "model": False, "created": "2026-10-13"}],
    "report": {"type": []},
}


@unittest.skipIf(TestClient is None, "fastapi isn't installed")
class ServerTest(unittest.TestCase):
    def setUp(self):
        os.environ["NO_BACKGROUND"] = "1"
        from server import app as server
        self.server = server
        server.SNAPSHOT.result = json.loads(json.dumps(SAMPLE))
        server._entitled.clear()
        self.client = TestClient(server.app)

    def test_free_users_see_a_preview(self):
        with mock.patch.dict(os.environ, {"ALLOW_ALL": "0"}, clear=False):
            os.environ.pop("REVENUECAT_SECRET", None)
            r = self.client.get("/v1/board/nfl").json()
        self.assertTrue(r["locked"])
        self.assertEqual(len(r["singles"]), self.server.FREE_SINGLES)
        self.assertEqual(r["lockedCounts"], {"singles": 7, "parlays": 1, "money": 1})
        self.assertEqual(self.client.get("/v1/multi").json()["lockedCount"], 1)

    def test_subscribers_see_everything(self):
        with mock.patch.dict(os.environ, {"ALLOW_ALL": "1"}):
            r = self.client.get("/v1/board/nfl?week=6&type=Moneyline").json()
            m = self.client.get("/v1/multi").json()
        self.assertFalse(r["locked"])
        self.assertEqual(len(r["singles"]), 1)
        self.assertEqual(len(m["parlays"]), 1)

    def test_revenuecat_decides_and_is_cached(self):
        answer = json.dumps({"subscriber": {"entitlements": {"pro": {
            "expires_date": "2999-01-01T00:00:00Z"}}}}).encode()

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, *a):
                return answer
        env = {"REVENUECAT_SECRET": "sk_test", "ALLOW_ALL": "0"}
        with mock.patch.dict(os.environ, env), \
                mock.patch("urllib.request.urlopen", return_value=Resp()) as call:
            a = self.client.get("/v1/board/nfl", headers={"X-User-ID": "u1"}).json()
            b = self.client.get("/v1/board/nfl", headers={"X-User-ID": "u1"}).json()
        self.assertFalse(a["locked"])
        self.assertFalse(b["locked"])
        self.assertEqual(call.call_count, 1)

    def test_menus_record_and_errors(self):
        leagues = self.client.get("/v1/leagues").json()["leagues"]
        self.assertEqual([x["id"] for x in leagues], ["nfl", "ncaaf", "ufc", "mlb"])
        self.assertEqual(leagues[0]["weeks"][0]["week"], 6)
        rec = self.client.get("/v1/record").json()
        self.assertEqual((rec["won"], rec["lost"], rec["pending"]), (1, 1, 1))  # model lines out
        self.assertEqual(self.client.get("/v1/board/mlb").status_code, 404)
        self.assertEqual(self.client.get("/v1/board/xyz").status_code, 404)
        self.server.SNAPSHOT.result = None
        self.assertEqual(self.client.get("/v1/leagues").status_code, 503)
        self.assertFalse(self.client.get("/v1/status").json()["ready"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(TestClient is None, "fastapi isn't installed")
class ValueTest(unittest.TestCase):
    def test_value_is_for_subscribers(self):
        os.environ["NO_BACKGROUND"] = "1"
        from server import app as server
        server.SNAPSHOT.result = json.loads(json.dumps(SAMPLE))
        offer = {"market": "player_pass_yds", "ev": 0.05, "check": False, "best": 110,
                 "fanduel": 100, "label": "Bo Nix Over 197.5 Passing Yds"}
        line = {"market": "h2h", "ev": -0.02, "check": False, "best": -140, "fanduel": -150,
                "label": "Broncos ML"}
        server.SNAPSHOT.value = {"nfl": [offer, line]}
        client = TestClient(server.app)
        with mock.patch.dict(os.environ, {"ALLOW_ALL": "0"}):
            os.environ.pop("REVENUECAT_SECRET", None)
            free = client.get("/v1/value/nfl").json()
        self.assertTrue(free["locked"])
        self.assertEqual(free["offers"], [])
        self.assertEqual(free["summary"], {"lines": 2, "plusEv": 1, "betterElsewhere": 2})
        with mock.patch.dict(os.environ, {"ALLOW_ALL": "1"}):
            props = client.get("/v1/value/nfl?kind=props").json()
            plus = client.get("/v1/value/nfl?min_ev=0").json()
        self.assertEqual([o["label"] for o in props["offers"]], ["Bo Nix Over 197.5 Passing Yds"])
        self.assertEqual(len(plus["offers"]), 1)
