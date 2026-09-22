import unittest
from ailife.kernel import (Kernel, SUCCEED, RELEASE_ALL, STRICT, EFFECT_CONFIRMED,
                           O_RESOLVED, O_CLOSED, O_RECOVERY, R_HELD, R_ACTIVE)


def base(**kw):
    k = Kernel(**kw)
    k.spawn("I", "EXT", "EXTERNAL", mortal=False)
    k.spawn("I", "BOSS")
    k.delegate("I", "G-BOSS", "G-ROOT", "BOSS", ["SPAWN", "OPEN_OBLIGATION", "RESERVE", "RECONCILE", "RELEASE", "MONITOR"],
               ["PAY", "SPAWN"], scope={"case": ["C1"]}, max_qty=100, valid_until=50)
    k.spawn("BOSS", "W", scope={"case": "C1"})
    k.create_pool("I", "CASH", "QUANTITY", 100)
    k.open_obligation("BOSS", "OB", "PAYMENT", scope={"case": "C1"})
    return k


class Authority(unittest.TestCase):
    def test_escalation_rejected(self):
        k = base()
        ok, code = k.delegate("BOSS", "G-W", "G-BOSS", "W", ["RELEASE"], scope={"case": ["C1"]}, max_qty=10, valid_until=10)
        self.assertEqual((ok, code), (False, "ESCALATION"))

    def test_may_do_is_not_may_grant(self):
        k = base()
        k.delegate("BOSS", "G-W", "G-BOSS", "W", ["PAY"], scope={"case": ["C1"]}, max_qty=10, valid_until=10)
        k.spawn("BOSS", "W2", scope={"case": "C1"})
        ok, code = k.delegate("W", "G-W2", "G-W", "W2", ["PAY"], scope={"case": ["C1"]}, max_qty=10, valid_until=10)
        self.assertEqual(code, "ESCALATION")

    def test_scope_limit_expiry(self):
        k = base()
        self.assertEqual(k.delegate("BOSS", "G1", "G-BOSS", "W", ["PAY"], scope={"case": ["C2"]}, max_qty=10, valid_until=10)[1], "SCOPE_EXCEEDS_PARENT")
        self.assertEqual(k.delegate("BOSS", "G1", "G-BOSS", "W", ["PAY"], scope={"case": ["C1"]}, max_qty=500, valid_until=10)[1], "LIMIT_EXCEEDS_PARENT")
        self.assertEqual(k.delegate("BOSS", "G1", "G-BOSS", "W", ["PAY"], scope={"case": ["C1"]}, max_qty=10, valid_until=99)[1], "EXPIRY_EXCEEDS_PARENT")
        k.delegate("BOSS", "G1", "G-BOSS", "W", ["PAY"], scope={"case": ["C1"]}, max_qty=10, valid_until=5)
        self.assertTrue(k.submit("W", "X1", "OB", "C1:PAY", "EXT", "PAY", 10, {"case": "C1"})[0])
        k.advance(6)
        self.assertEqual(k.s["grants"]["G1"]["status"], "EXPIRED")
        self.assertEqual(k.submit("W", "X2", "OB", "C1:PAY2", "EXT", "PAY", 10, {"case": "C1"})[1], "NO_AUTHORITY")

    def test_revocation_cascades(self):
        k = base()
        k.delegate("BOSS", "G1", "G-BOSS", "W", ["PAY"], ["PAY"], scope={"case": ["C1"]}, max_qty=10, valid_until=5)
        k.revoke("I", "G-BOSS")
        self.assertEqual(k.s["grants"]["G1"]["status"], "REVOKED")


class Death(unittest.TestCase):
    def test_succession(self):
        k = base()
        k.delegate("BOSS", "G1", "G-BOSS", "W", ["PAY"], scope={"case": ["C1"]}, max_qty=10, valid_until=20)
        k.assign_obligation("BOSS", "OB", "W")
        ok, succ = k.kill("MON", "W", "CRASH")
        self.assertTrue(ok)
        self.assertEqual(succ, "BOSS")
        self.assertEqual(k.s["obligations"]["OB"]["owner"], "BOSS")
        self.assertEqual(k.s["grants"]["G1"]["status"], "REVOKED")

    def test_root_immortal(self):
        self.assertEqual(base().kill("MON", "I", "X")[1], "IMMORTAL")

    def test_succession_skips_dead_ancestors(self):
        k = base()
        k.delegate("I", "G-W-SPAWN", "G-ROOT", "W", ["SPAWN", "OPEN_OBLIGATION"])
        k.spawn("W", "W1")
        k.delegate("I", "G-W1", "G-ROOT", "W1", ["OPEN_OBLIGATION"])
        k.open_obligation("W1", "OB2", "TASK")
        k.kill("MON", "W", "CRASH")
        k.kill("MON", "BOSS", "CRASH")
        k.kill("MON", "W1", "CRASH")
        self.assertEqual(k.s["obligations"]["OB2"]["owner"], "I")

    def test_release_all_strict_refuses_orphaning_death(self):
        k = base(death_policy=RELEASE_ALL, enforce=STRICT)
        self.assertEqual(k.kill("MON", "BOSS", "AGE")[1], "INVARIANT_WOULD_BREAK")
        self.assertTrue(k.alive("BOSS"))

    def test_reservation_held_then_certain(self):
        k = base()
        k.delegate("BOSS", "G1", "G-BOSS", "W", ["PAY"], scope={"case": ["C1"]}, max_qty=50, valid_until=20)
        k.reserve("BOSS", "R1", "CASH", "OB", 40, {"case": "C1"})
        k.assign_obligation("BOSS", "OB", "W")
        k.submit("W", "X1", "OB", "C1:PAY", "EXT", "PAY", 40, {"case": "C1"})
        k.kill("MON", "W", "CRASH")
        self.assertEqual(k.s["reservations"]["R1"]["status"], R_HELD)
        self.assertEqual(k.s["obligations"]["OB"]["status"], O_RECOVERY)
        self.assertEqual(k.release_reservation("BOSS", "R1", None, {"case": "C1"})[1], "UNCERTAIN_EXTERNAL_STATE")
        k.capture_evidence("EXT", "E1", "X1", EFFECT_CONFIRMED)
        k.reconcile("BOSS", "X1", "E1", {"case": "C1"})
        self.assertEqual(k.s["reservations"]["R1"]["status"], R_ACTIVE)


class Commitments(unittest.TestCase):
    def setUp(self):
        self.k = base()
        self.k.delegate("BOSS", "G1", "G-BOSS", "W", ["PAY"], scope={"case": ["C1"]}, max_qty=50, valid_until=20)

    def test_duplicate_blocked_idempotent_allowed(self):
        k = self.k
        k.submit("W", "X1", "OB", "C1:PAY", "EXT", "PAY", 10, {"case": "C1"})
        self.assertEqual(k.submit("W", "X2", "OB", "C1:PAY", "EXT", "PAY", 10, {"case": "C1"})[1], "DUPLICATE_BLOCKED")
        k.submit("W", "Y1", "OB", "C1:CLAIM", "EXT", "PAY", 10, {"case": "C1"}, idempotent=True)
        self.assertTrue(k.submit("W", "Y2", "OB", "C1:CLAIM", "EXT", "PAY", 10, {"case": "C1"}, idempotent=True)[0])

    def test_confirmed_effect_cannot_repeat(self):
        k = self.k
        k.submit("W", "X1", "OB", "C1:PAY", "EXT", "PAY", 10, {"case": "C1"})
        k.capture_evidence("EXT", "E1", "X1", EFFECT_CONFIRMED)
        k.reconcile("BOSS", "X1", "E1", {"case": "C1"})
        self.assertEqual(k.submit("W", "X2", "OB", "C1:PAY", "EXT", "PAY", 10, {"case": "C1"})[1], "ALREADY_EFFECTED")

    def test_agent_claim_is_not_evidence(self):
        k = self.k
        k.submit("W", "X1", "OB", "C1:PAY", "EXT", "PAY", 10, {"case": "C1"})
        k.capture_evidence("W", "E-FAKE", "X1", EFFECT_CONFIRMED)
        self.assertFalse(k.s["evidence"]["E-FAKE"]["verified"])
        self.assertEqual(k.reconcile("BOSS", "X1", "E-FAKE", {"case": "C1"})[1], "EVIDENCE_NOT_AUTHORITATIVE")

    def test_close_requires_clean_state(self):
        k = self.k
        k.submit("W", "X1", "OB", "C1:PAY", "EXT", "PAY", 10, {"case": "C1"})
        self.assertEqual(k.set_obligation_status("BOSS", "OB", O_RESOLVED)[1], "UNRESOLVED_COMMITMENTS")


class Accounting(unittest.TestCase):
    def test_quantity_conservation(self):
        k = base()
        k.reserve("BOSS", "R1", "CASH", "OB", 80, {"case": "C1"})
        self.assertEqual(k.reserve("BOSS", "R2", "CASH", "OB", 30, {"case": "C1"})[1], "INSUFFICIENT_AVAILABLE")

    def test_slots_capacity_transfer_release(self):
        k = Kernel(death_policy=RELEASE_ALL)
        k.create_pool("I", "P", "SLOTS", 1)
        for e in ("A", "B"):
            k.spawn("I", e)
        k.delegate("I", "GA", "G-ROOT", "A", ["USE"], slot_pool="P", transferable=True)
        self.assertEqual(k.delegate("I", "GB", "G-ROOT", "B", ["USE"], slot_pool="P")[1], "CAPACITY_FULL")
        k.transfer_grant("A", "GA", "B")
        self.assertEqual(k.pool_usage("P")["used"], 1)
        k.kill("MON", "B", "AGE")
        self.assertEqual(k.pool_usage("P")["available"], 1)

    def test_rejection_rolls_back(self):
        k = base()
        before = repr(k.s)
        k.reserve("BOSS", "R1", "CASH", "OB", 1000, {"case": "C1"})
        self.assertEqual(before, repr(k.s))
        self.assertEqual(k.log[-1]["type"], "COMMAND_REJECTED")

    def test_hash_chain_tamper_detected(self):
        k = base()
        self.assertTrue(k.verify_log())
        k.log[3]["payload"] = {"tampered": True}
        self.assertFalse(k.verify_log())


if __name__ == "__main__":
    unittest.main()
