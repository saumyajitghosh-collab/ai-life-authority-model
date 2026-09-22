import unittest
from app import app, CASE_ID


class Api(unittest.TestCase):
    def setUp(self):
        self.c = app.test_client()

    def test_full_story_and_endpoints(self):
        self.assertEqual(self.c.get("/healthz").json, {"ok": True})
        self.assertEqual(self.c.post("/api/simulator/reset", json={"architecture": "AI_LIFE"}).status_code, 201)
        for _ in range(13):
            r = self.c.post("/api/scenario/next")
            self.assertEqual(r.status_code, 200)
        v = self.c.get(f"/api/cases/{CASE_ID}").json
        self.assertEqual(v["state"]["obligations"]["OBL-10022"]["status"], "CLOSED")
        inv = self.c.get(f"/api/cases/{CASE_ID}/invariants").json
        self.assertEqual(inv["failed"], 0)
        evs = self.c.get(f"/api/cases/{CASE_ID}/events").json
        self.assertEqual([e["seq"] for e in evs], sorted(e["seq"] for e in evs))
        lin = self.c.get(f"/api/cases/{CASE_ID}/authority-lineage").json
        self.assertTrue(lin["nodes"] and lin["edges"])

    def test_failures_commands_clock(self):
        self.c.post("/api/simulator/reset", json={})
        for _ in range(6):
            self.c.post("/api/scenario/next")
        r = self.c.post(f"/api/cases/{CASE_ID}/failures", json={"failureType": "ATTEMPT_DUPLICATE_SUBMISSION"}).json
        self.assertIn("DUPLICATE_BLOCKED", [x["result"] for x in r["lastStep"]["calls"]])
        r = self.c.post(f"/api/cases/{CASE_ID}/reconcile").json
        self.assertEqual(r["state"]["commitments"]["COM-778"]["status"], "RECONCILED")
        r = self.c.post("/api/simulator/clock", json={"action": "ADVANCE", "minutes": 30}).json
        self.assertEqual(r["clock"], "11:27")
        r = self.c.post(f"/api/cases/{CASE_ID}/commands", json={"type": "SET_EXTERNAL_MODE", "payload": {"mode": "REJECT"}}).json
        self.assertEqual(r["external"]["next_mode"], "REJECT")

    def test_sessions_are_isolated(self):
        a, b = app.test_client(), app.test_client()
        a.post("/api/scenario/next")
        self.assertEqual(b.get(f"/api/cases/{CASE_ID}").json["chapter"], 0)
        self.assertEqual(a.get(f"/api/cases/{CASE_ID}").json["chapter"], 1)

    def test_unknown_case(self):
        self.assertEqual(self.c.get("/api/cases/NOPE").status_code, 404)


if __name__ == "__main__":
    unittest.main()
