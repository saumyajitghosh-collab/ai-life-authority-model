"""Definition-of-done tests from the v0.4 build contract (section 68), on the post-trade child."""
import unittest
from ailife.kernel import Kernel
from ailife.children.posttrade import (PostTradeCase, OBL, RSV, RA1, RA2, EC, REV, GW, ROOT, INTENT)


def run_to(n, arch="AI_LIFE"):
    c = PostTradeCase(arch)
    for _ in range(n):
        c.next_chapter()
    return c


def last_codes(case):
    return [x["result"] for x in case.steps[-1]["calls"] if not x["ok"]]


def inv(case, iid):
    return next(r for r in case.invariant_catalogue() if r["id"] == iid)


class HostileScenario(unittest.TestCase):
    def test_golden_preserves_obligation_and_prevents_duplicate(self):
        """v0.4 section 34 / 50."""
        c = run_to(6)
        self.assertEqual(c.k.s["commitments"]["COM-778"]["status"], "OUTCOME_UNKNOWN")
        self.assertTrue(c.gw.instructions["COM-778"]["settled"])           # external truth: executed
        c.next_chapter()                                                   # kill Repair Agent 01
        o = c.k.s["obligations"][OBL]
        self.assertEqual(o["status"], "RECOVERY_REQUIRED")
        self.assertTrue(o["owner"] and c.k.alive(o["owner"]))
        self.assertEqual(c.k.s["reservations"][RSV]["status"], "HELD_UNCERTAIN")
        self.assertIn("COM-778", c.k.s["commitments"])
        c.next_chapter(); c.next_chapter(); c.next_chapter()               # replacement, proposal, attempt
        self.assertIn("DUPLICATE_BLOCKED", last_codes(c))
        self.assertEqual(c.gw.settled_count("T12345"), 1)
        self.assertEqual(inv(c, "I08")["status"], "PASS")
        c.next_chapter(); c.next_chapter(); c.next_chapter()
        self.assertEqual(c.k.s["commitments"]["COM-778"]["status"], "RECONCILED")
        self.assertEqual(c.k.s["obligations"][OBL]["status"], "CLOSED")
        self.assertEqual([r for r in c.invariant_catalogue() if r["status"] != "PASS"], [])
        self.assertFalse(any(x["unexpected"] for s in c.steps for x in s["calls"]))

    def test_final_assertions(self):
        c = run_to(13)
        m = c.metrics()
        self.assertEqual(m["unauthorized_executed"], 0)
        self.assertEqual(m["duplicates_completed"], 0)
        self.assertEqual(c.k.check_invariants(), [])
        self.assertTrue(c.k.verify_log())

    def test_anti_demo_agent_claims_without_evidence(self):
        """v0.3 section 51."""
        c = run_to(6)
        c.inject("FAKE_SUCCESS")
        self.assertIn("EVIDENCE_NOT_AUTHORITATIVE", last_codes(c))
        self.assertEqual(c.k.s["commitments"]["COM-778"]["status"], "OUTCOME_UNKNOWN")
        c.inject("ATTEMPT_DUPLICATE_SUBMISSION")
        self.assertIn("DUPLICATE_BLOCKED", last_codes(c))

    def test_baseline_duplicates_and_orphans(self):
        c = run_to(13, "BASELINE")
        self.assertEqual(c.gw.settled_count("T12345"), 2)
        self.assertIsNone(c.k.s["obligations"][OBL]["owner"])
        self.assertEqual({r["id"] for r in c.invariant_catalogue() if r["status"] == "FAIL"}, {"I05", "I06", "I08"})


class Authority(unittest.TestCase):
    def test_containment(self):
        c = run_to(2)
        ok, code = c.k.delegate(EC, "AUTH-X", "AUTH-EC-100", RA1, ["APPROVE_REPAIR"], scope={"trade": ["T12345"]})
        self.assertEqual(code, "ESCALATION")
        ok, code = c.k.delegate(EC, "AUTH-X", "AUTH-EC-100", RA1, ["PROPOSE_REPAIR"], scope={"trade": ["T99999"]})
        self.assertEqual(code, "SCOPE_EXCEEDS_PARENT")

    def test_capability_is_not_permission(self):
        c = run_to(2)
        ok, code = c.k.propose(REV, "PROP-X", OBL, "PROPOSE_REPAIR", {"newValue": "ACCOUNT_B"}, 100, {"trade": "T12345"})
        self.assertEqual(code, "NO_CAPABILITY")

    def test_expiry(self):
        c = run_to(2)
        c.inject("EXPIRE_AUTHORITY")
        self.assertIn("NO_AUTHORITY", last_codes(c))
        self.assertGreaterEqual(inv(c, "I03")["blocked"], 1)

    def test_revocation(self):
        c = run_to(2)
        c.inject("REVOKE_AUTHORITY")
        self.assertIn("NO_AUTHORITY", last_codes(c))
        self.assertGreaterEqual(inv(c, "I04")["blocked"], 1)

    def test_quantity_escalation(self):
        c = run_to(2)
        c.inject("ATTEMPT_QUANTITY_ESCALATION")
        self.assertIn("NO_AUTHORITY", last_codes(c))


class Obligations(unittest.TestCase):
    def test_persistence_and_recovery_owner(self):
        c = run_to(6)
        c.inject("KILL_CONTROLLER")
        o = c.k.s["obligations"][OBL]
        self.assertEqual(o["owner"], ROOT)
        self.assertEqual(c.k.s["recoveries"][o["recovery"]]["owner"], ROOT)
        self.assertEqual(c.k.s["grants"]["AUTH-00918"]["status"], "REVOKED")   # cascade from controller

    def test_remove_owner_is_refused(self):
        c = run_to(1)
        c.inject("REMOVE_OWNER")
        self.assertIn("INVARIANT_WOULD_BREAK", last_codes(c))
        self.assertEqual(c.k.s["obligations"][OBL]["owner"], EC)

    def test_closed_obligation_takes_no_actions(self):
        c = run_to(13)
        ok, code = c.k.propose(RA2, "PROP-Z", OBL, "PROPOSE_REPAIR", {"newValue": "ACCOUNT_B"}, 100, {"trade": "T12345"})
        self.assertEqual(code, "OBLIGATION_NOT_OPEN")


class ResourcesAndCommitments(unittest.TestCase):
    def test_resource_conservation(self):
        c = run_to(1)
        c.inject("DOUBLE_RESERVE_RESOURCE")
        self.assertIn("INSUFFICIENT_AVAILABLE", last_codes(c))

    def test_uncertain_resource_not_released(self):
        c = run_to(7)
        ok, code = c.k.release_reservation("RECONCILIATION_AGENT", RSV, None, {"trade": "T12345"})
        self.assertEqual(code, "UNCERTAIN_EXTERNAL_STATE")

    def test_unknown_state_is_preserved(self):
        c = run_to(6)
        self.assertEqual(c.k.s["commitments"]["COM-778"]["status"], "OUTCOME_UNKNOWN")
        self.assertEqual(inv(c, "I13")["status"], "UNKNOWN")
        c.inject("DISCONNECT_STATUS_API")
        c.command("RECONCILE")
        self.assertEqual(c.k.s["commitments"]["COM-778"]["status"], "OUTCOME_UNKNOWN")
        c.inject("RECONNECT_STATUS_API")
        c.command("RECONCILE")
        self.assertEqual(c.k.s["commitments"]["COM-778"]["status"], "RECONCILED")

    def test_evidence_required(self):
        c = run_to(13)
        ex = c.k.s["executions"]["EXEC-COM-778"]
        self.assertEqual(ex["status"], "EFFECT_CONFIRMED")
        self.assertTrue(all(c.k.s["evidence"][e]["verified"] for e in ex["evidence"]))

    def test_stale_authorization(self):
        c = run_to(4)
        c.inject("USE_STALE_AUTHORIZATION")
        self.assertIn("STALE_AUTHORIZATION", last_codes(c))
        self.assertEqual(c.gw.instructions, {})

    def test_conflicting_proposals_escalate(self):
        c = run_to(3)
        c.inject("CREATE_CONFLICTING_PROPOSAL")
        d = c.k.s["decisions"][c.latest_proposal()["decision"]]
        self.assertEqual((d["decision"], d["reason"]), ("ESCALATE", "CONFLICTING_PROPOSAL"))


class FourEye(unittest.TestCase):
    def test_proposer_cannot_approve(self):
        c = run_to(3)
        c.k.delegate(ROOT, "AUTH-SELF", "G-ROOT", RA1, ["APPROVE_REPAIR"], scope={"trade": ["T12345"]})
        c.k.s["entities"][RA1]["capabilities"].append("APPROVE_REPAIR")
        ok, code = c.k.approve(RA1, c.latest_proposal()["id"], {"trade": "T12345"})
        self.assertEqual(code, "FOUR_EYE_VIOLATION")

    def test_submit_requires_approval(self):
        c = run_to(3)                                  # proposal escalated, not approved
        cid = c.submit(c.latest_proposal()["id"], expect="NOT_AUTHORIZED")
        self.assertIsNone(cid)


class Replay(unittest.TestCase):
    def test_journal_replay_reproduces_hash_chain(self):
        for arch in ("AI_LIFE", "BASELINE"):
            c = run_to(13, arch)
            c.inject("CHANGE_SSI")
            r = Kernel.replay(c.k.config, c.k.journal)
            self.assertEqual([e["hash"] for e in r.log], [e["hash"] for e in c.k.log])
            self.assertEqual(repr(r.s), repr(c.k.s))


if __name__ == "__main__":
    unittest.main()
