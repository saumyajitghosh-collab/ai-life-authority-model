import unittest
from ailife.children import atomic_dvp as dvp, persona_life


def final_fails(p):
    last = p.steps[-1]
    return sorted(i["id"] for i in last["state"]["invariants"] + last["domain"] if i["status"] == "FAIL")


def unexpected(steps):
    return [c for st in steps for c in st["calls"] if c["unexpected"]]


class Scenarios(unittest.TestCase):
    def test_all_scenarios_behave_as_scripted(self):
        self.assertEqual(unexpected(persona_life.scenario()["steps"]), [])
        for sid, _, fn in dvp.SCENARIOS:
            p = fn()
            with self.subTest(sid):
                self.assertEqual(unexpected(p.steps), [])
                self.assertTrue(p.k.verify_log())
                if sid != "s4":
                    self.assertEqual(final_fails(p), [])
                    self.assertEqual(p.k.check_invariants(), [])

    def test_succession_settles_atomically(self):
        p = dvp.s3_death_succession()
        sec, cash = p.ledgers["SEC_DLT"], p.ledgers["WCBDC"]
        self.assertEqual(sec.balances["BUYER"]["BOND"], dvp.BOND)
        self.assertEqual(cash.balances["SELLER"]["WCBDC"], dvp.CASH)
        self.assertTrue(all(o["status"] == "CLOSED" for o in p.k.s["obligations"].values()))

    def test_baseline_breaks_atomicity(self):
        p = dvp.s4_baseline()
        self.assertEqual(final_fails(p), ["D01", "K03"])
        self.assertEqual(p.ledgers["SEC_DLT"].balances["SELLER"]["BOND"], dvp.BOND)
        self.assertEqual(p.ledgers["WCBDC"].balances["SELLER"]["WCBDC"], dvp.CASH)

    def test_lost_response_locks_cash_once(self):
        p = dvp.s5_lost_response()
        self.assertEqual(len(p.ledgers["WCBDC"].locks), 1)
        self.assertEqual(p.ledgers["WCBDC"].balances["BUYER"]["WCBDC"], 25_000_000 - dvp.CASH)

    def test_unwind_moves_nothing(self):
        p = dvp.s2_unwind()
        self.assertEqual(p.ledgers["SEC_DLT"].balances["SELLER"]["BOND"], dvp.BOND)
        self.assertEqual(p.k.s["obligations"]["OB-DVP"]["outcome"], "UNWOUND")


if __name__ == "__main__":
    unittest.main()
