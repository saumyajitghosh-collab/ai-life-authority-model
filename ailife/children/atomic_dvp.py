"""Child model: delivery-versus-payment of a tokenized bond against wholesale CBDC.

Two settlement architectures on the same Mother kernel:
  * cross-ledger HTLC: bond locked on a securities DLT, cash locked on a wCBDC ledger,
    linked by one hashlock; locks revert automatically to the sender at timeout.
  * unified ledger: both legs in one ATOMIC_DVP transaction.

Domain invariant D01 is evaluated against the ledgers' own truth, not the control plane:
a claimed leg must never coexist with a reverted leg.
"""
from ..kernel import (Kernel, SUCCEED, RELEASE_ALL, STRICT, AUDIT, EFFECT_CONFIRMED, EFFECT_REJECTED,
                      O_PROGRESS, O_RESOLVED, O_CLOSED)
from ..ledger_sim import Ledger, hashlock

TRADE = "DVP-001"
SCOPE = {"trade": TRADE}
BOND, CASH = 1_000_000, 9_850_000
SECRET = "preimage-DVP-001-7f3a"
T_BOND, T_CASH = 30, 15


class Platform:
    def __init__(self, title, death_policy=SUCCEED, enforce=STRICT, unified=False):
        self.title = title
        self.k = Kernel(death_policy=death_policy, enforce=enforce)
        self.unified = unified
        if unified:
            self.ledgers = {"UNIFIED_LEDGER": Ledger("Unified ledger (tokenized bond + wCBDC)", {
                "SELLER": {"BOND": BOND}, "BUYER": {"WCBDC": 25_000_000}})}
        else:
            self.ledgers = {
                "SEC_DLT": Ledger("Securities DLT (tokenized bond)", {"SELLER": {"BOND": BOND}}),
                "WCBDC": Ledger("Wholesale CBDC ledger", {"BUYER": {"WCBDC": 25_000_000}}),
            }
        self.steps, self._mark, self.results = [], 0, []

    # ---------------------------------------------------------------- tracing
    def call(self, label, outcome, expect=None):
        ok, res = outcome
        unexpected = (not ok and res != expect) or (ok and expect is not None)
        self.results.append({"label": label, "ok": ok, "result": res, "expected": expect, "unexpected": unexpected})
        return ok, res

    def step(self, chapter, title, narrative):
        k = self.k
        self.steps.append({
            "chapter": chapter, "title": title, "narrative": narrative, "t": k.s["t"],
            "events": k.log[self._mark:], "calls": self.results,
            "state": k.snapshot(), "ledgers": {n: l.snapshot() for n, l in self.ledgers.items()},
            "domain": self.domain_checks()})
        self._mark, self.results = len(k.log), []

    def domain_checks(self):
        if self.unified:
            l = self.ledgers["UNIFIED_LEDGER"]
            moved_bond = l.balances.get("BUYER", {}).get("BOND", 0)
            moved_cash = l.balances.get("SELLER", {}).get("WCBDC", 0)
            atomic = (moved_bond == 0) == (moved_cash == 0)
            return [{"id": "D01", "name": "Legs settle together or not at all (ledger truth)",
                     "status": "PASS" if atomic else "FAIL",
                     "detail": [] if atomic else [f"bond moved {moved_bond}, cash moved {moved_cash}"]},
                    {"id": "D02", "name": "No principal exposure (one leg final, other open)",
                     "status": "PASS", "detail": []}]
        b = self.ledgers["SEC_DLT"].locks.get("L-BOND", {}).get("status", "NONE")
        c = self.ledgers["WCBDC"].locks.get("L-CASH", {}).get("status", "NONE")
        broken = {b, c} == {"CLAIMED", "REVERTED"}
        exposed = ("CLAIMED" in (b, c)) and ("LOCKED" in (b, c))
        return [{"id": "D01", "name": "Legs settle together or not at all (ledger truth)",
                 "status": "FAIL" if broken else "PASS",
                 "detail": [f"bond lock {b}, cash lock {c}: one party paid, the other did not deliver"] if broken else []},
                {"id": "D02", "name": "No principal exposure (one leg final, other open)",
                 "status": "WARN" if exposed else "PASS",
                 "detail": [f"bond lock {b}, cash lock {c}: deadline t={T_BOND if b == 'LOCKED' else T_CASH}"] if exposed else []}]

    # ---------------------------------------------------------------- mechanics
    def advance(self, n):
        self.call(f"advance {n}", self.k.advance(n))
        for name, led in self.ledgers.items():
            for lock_id in led.tick(self.k.s["t"]):
                cid = {"L-BOND": "C-LOCK-BOND", "L-CASH": "C-LOCK-CASH"}[lock_id]
                self.call(f"{name} reverted {lock_id}", self.k.capture_evidence(
                    name, f"EV-REVERT-{lock_id}", cid, "LOCK_REVERTED", {"lock": lock_id}))

    def ledger_op(self, agent, cid, obligation, intent, ledger, action, op, args, qty, idempotent=False, expect=None,
                  affects=()):
        ok, res = self.call(f"{agent} submit {cid}", self.k.submit(
            agent, cid, obligation, f"{TRADE}:{intent}", ledger, action, qty, SCOPE, idempotent, affects=affects), expect)
        if not ok:
            return "REJECTED:" + res
        resp = self.ledgers[ledger].submit(cid, op, args)
        if resp is None:
            return "NO_RESPONSE"
        self.evidence_and_reconcile(ledger, cid, resp)
        return resp["status"]

    def evidence_and_reconcile(self, ledger, cid, resp):
        claim = EFFECT_CONFIRMED if resp["status"] == "FINAL" else EFFECT_REJECTED
        eid = f"EV-{cid}"
        self.call(f"{ledger} evidence {eid}", self.k.capture_evidence(ledger, eid, cid, claim, resp))
        self.call(f"RECON reconcile {cid}", self.k.reconcile("RECON", cid, eid, SCOPE))
        return eid

    def query_and_reconcile(self, cid):
        ledger = self.k.s["commitments"][cid]["system"]
        resp = self.ledgers[ledger].query(cid)
        return self.evidence_and_reconcile(ledger, cid, resp)

    # ---------------------------------------------------------------- setup
    def setup(self):
        k, c = self.k, self.call
        for name in self.ledgers:
            c(f"spawn {name}", k.spawn("I", name, "EXTERNAL", mortal=False))
        c("spawn ORCH", k.spawn("I", "ORCH"))
        c("spawn RECON", k.spawn("I", "RECON"))
        c("grant ORCH", k.delegate("I", "G-ORCH", "G-ROOT", "ORCH",
                                   ["SPAWN", "OPEN_OBLIGATION", "RESERVE", "MONITOR"],
                                   ["LOCK_SECURITY", "CLAIM_CASH", "LOCK_CASH", "CLAIM_SECURITY", "ATOMIC_DVP"],
                                   scope={"trade": [TRADE]}))
        c("grant RECON", k.delegate("I", "G-RECON", "G-ROOT", "RECON", ["RECONCILE", "RELEASE", "MONITOR"],
                                    scope={"trade": [TRADE]}))
        c("pool bond", k.create_pool("I", "POS-SELLER-BOND", "QUANTITY", BOND, "Seller bond position (units)"))
        c("pool cash", k.create_pool("I", "POS-BUYER-WCBDC", "QUANTITY", CASH, "Buyer wCBDC earmarked for trade"))
        c("open DVP", k.open_obligation("ORCH", "OB-DVP", "DVP_SETTLEMENT", deadline=T_BOND,
                                        attrs={"bond": BOND, "cash": CASH}, scope=SCOPE))
        c("DVP in progress", k.set_obligation_status("ORCH", "OB-DVP", O_PROGRESS))
        if self.unified:
            c("reserve bond", k.reserve("ORCH", "RSV-BOND", "POS-SELLER-BOND", "OB-DVP", BOND, SCOPE))
            c("reserve cash", k.reserve("ORCH", "RSV-CASH", "POS-BUYER-WCBDC", "OB-DVP", CASH, SCOPE))
            c("spawn DVP_AGENT", k.spawn("ORCH", "DVP_AGENT", scope=SCOPE))
            c("grant DVP_AGENT", k.delegate("ORCH", "G-DVP", "G-ORCH", "DVP_AGENT", ["ATOMIC_DVP"],
                                            scope={"trade": [TRADE]}, max_qty=CASH, valid_until=40))
            c("assign DVP", k.assign_obligation("ORCH", "OB-DVP", "DVP_AGENT"))
            return
        c("open seller leg", k.open_obligation("ORCH", "OB-SELLER", "SELLER_SIDE", "OB-DVP", T_BOND,
                                               {"deliver": f"{BOND} BOND", "receive": f"{CASH} WCBDC"}, SCOPE))
        c("open buyer leg", k.open_obligation("ORCH", "OB-BUYER", "BUYER_SIDE", "OB-DVP", T_BOND,
                                              {"deliver": f"{CASH} WCBDC", "receive": f"{BOND} BOND"}, SCOPE))
        c("reserve bond", k.reserve("ORCH", "RSV-BOND", "POS-SELLER-BOND", "OB-SELLER", BOND, SCOPE))
        c("reserve cash", k.reserve("ORCH", "RSV-CASH", "POS-BUYER-WCBDC", "OB-BUYER", CASH, SCOPE))
        c("spawn SELLER_LEG", k.spawn("ORCH", "SELLER_LEG", scope=SCOPE))
        c("spawn BUYER_LEG", k.spawn("ORCH", "BUYER_LEG", scope=SCOPE))
        c("grant seller", k.delegate("ORCH", "G-SELLER", "G-ORCH", "SELLER_LEG", ["LOCK_SECURITY", "CLAIM_CASH"],
                                     scope={"trade": [TRADE]}, max_qty=CASH, valid_until=40))
        c("grant buyer", k.delegate("ORCH", "G-BUYER", "G-ORCH", "BUYER_LEG", ["LOCK_CASH", "CLAIM_SECURITY"],
                                    scope={"trade": [TRADE]}, max_qty=CASH, valid_until=40))
        c("assign seller", k.assign_obligation("ORCH", "OB-SELLER", "SELLER_LEG"))
        c("assign buyer", k.assign_obligation("ORCH", "OB-BUYER", "BUYER_LEG"))

    # ---------------------------------------------------------------- leg actions
    def lock_bond(self, agent="SELLER_LEG"):
        return self.ledger_op(agent, "C-LOCK-BOND", "OB-SELLER", "LOCK_BOND", "SEC_DLT", "LOCK_SECURITY", "LOCK",
                              {"lock_id": "L-BOND", "asset": "BOND", "amount": BOND, "frm": "SELLER", "to": "BUYER",
                               "hashlock": hashlock(SECRET), "timeout": T_BOND}, BOND, affects=["RSV-BOND"])

    def lock_cash(self, agent="BUYER_LEG", cid="C-LOCK-CASH", expect=None):
        return self.ledger_op(agent, cid, "OB-BUYER", "LOCK_CASH", "WCBDC", "LOCK_CASH", "LOCK",
                              {"lock_id": "L-CASH" if cid == "C-LOCK-CASH" else f"L-CASH-{cid}", "asset": "WCBDC",
                               "amount": CASH, "frm": "BUYER", "to": "SELLER", "hashlock": hashlock(SECRET),
                               "timeout": T_CASH}, CASH, expect=expect, affects=["RSV-CASH"])

    def consume_if_final(self, rid, cid):
        r = self.k.s["reservations"][rid]
        c = self.k.s["commitments"].get(cid)
        if r["status"] in ("ACTIVE", "HELD_UNCERTAIN") and c and c["outcome"] == EFFECT_CONFIRMED:
            self.call(f"consume {rid}", self.k.consume_reservation("RECON", rid, f"EV-{cid}", SCOPE))

    def claim_cash(self, agent="SELLER_LEG"):
        res = self._claim_cash(agent)
        self.consume_if_final("RSV-CASH", "C-CLAIM-CASH")
        return res

    def claim_bond(self, agent="BUYER_LEG", cid="C-CLAIM-BOND", expect=None):
        res = self._claim_bond(agent, cid, expect)
        self.consume_if_final("RSV-BOND", cid)
        return res

    def _claim_cash(self, agent):
        return self.ledger_op(agent, "C-CLAIM-CASH", "OB-SELLER", "CLAIM_CASH", "WCBDC", "CLAIM_CASH", "CLAIM",
                              {"lock_id": "L-CASH", "preimage": SECRET}, CASH, idempotent=True, affects=["RSV-CASH"])

    def _claim_bond(self, agent, cid, expect):
        # The preimage is public once the cash claim is on the wCBDC ledger. Knowing it is
        # memory, not authority: the claim still needs an active CLAIM_SECURITY grant.
        pre = self.ledgers["WCBDC"].revealed.get(hashlock(SECRET))
        return self.ledger_op(agent, cid, "OB-BUYER", "CLAIM_BOND", "SEC_DLT", "CLAIM_SECURITY", "CLAIM",
                              {"lock_id": "L-BOND", "preimage": pre or "unknown"}, BOND, idempotent=True, expect=expect, affects=["RSV-BOND"])

    def finish_htlc(self, seller="SELLER_LEG", buyer="BUYER_LEG"):
        k, c = self.k, self.call
        self.consume_if_final("RSV-CASH", "C-CLAIM-CASH")
        self.consume_if_final("RSV-BOND", "C-CLAIM-BOND")
        for owner, ob in ((seller, "OB-SELLER"), (buyer, "OB-BUYER")):
            c(f"resolve {ob}", k.set_obligation_status(owner, ob, O_RESOLVED, "SETTLED"))
            c(f"close {ob}", k.set_obligation_status(owner, ob, O_CLOSED))
        c("resolve DVP", k.set_obligation_status(k.s["obligations"]["OB-DVP"]["owner"], "OB-DVP", O_RESOLVED, "SETTLED"))
        c("close DVP", k.set_obligation_status(k.s["obligations"]["OB-DVP"]["owner"], "OB-DVP", O_CLOSED))


# ==================================================================== scenarios
def s1_happy():
    p = Platform("Cross-ledger HTLC settlement, no failures")
    p.setup()
    p.step("Setup", "Root I creates the case",
           "I spawns the orchestrator, reconciler and two ledger adapters, then delegates attenuated authority. "
           "The orchestrator opens a parent DvP obligation with seller-side and buyer-side children and reserves both positions.")
    p.lock_bond(); p.advance(2)
    p.step("Leg 1", "Seller locks 1,000,000 bond tokens",
           f"Hash-locked on the securities DLT for the buyer, reverting to the seller at t={T_BOND}. Evidence comes from the ledger, not the agent.")
    p.lock_cash(); p.advance(2)
    p.step("Leg 2", "Buyer locks 9,850,000 wCBDC",
           f"Same hashlock, shorter timeout t={T_CASH}. Both legs are now locked and nothing has moved yet.")
    p.claim_cash(); p.advance(2)
    p.step("Exposure", "Seller claims the cash and reveals the preimage",
           "Cash is final. The bond leg is still open, so the buyer is exposed until it claims the bonds before t=30.")
    p.claim_bond(); p.advance(1)
    p.step("Settled", "Buyer claims the bonds with the revealed preimage", "Both legs are final on their ledgers.")
    p.finish_htlc()
    p.step("Closed", "Obligations closed",
           "Each reservation was consumed only against verified ledger evidence of its claim. Children close before the parent.")
    return p


def s2_unwind():
    p = Platform("Cash leg rejected, both legs unwind")
    p.setup(); p.step("Setup", "Root I creates the case", "Same case, same authority tree.")
    p.lock_bond(); p.advance(2)
    p.step("Leg 1", "Seller locks the bonds", "Bond lock confirmed by the securities DLT.")
    p.ledgers["WCBDC"].next_response = "REJECT"
    p.lock_cash(); p.advance(2)
    p.step("Rejected", "wCBDC ledger rejects the cash lock",
           "The ledger's rejection is verified evidence, so the commitment reconciles as EFFECT_REJECTED. The seller never sees a cash lock, so it never reveals the preimage.")
    p.advance(T_BOND - p.k.s["t"])
    p.step("Timeout", f"Bond lock reverts to the seller at t={T_BOND}",
           "Nothing moved on either ledger. Atomicity holds because neither leg settled.")
    k, c = p.k, p.call
    c("release bond", k.release_reservation("RECON", "RSV-BOND", "EV-REVERT-L-BOND", SCOPE))
    c("release cash", k.release_reservation("RECON", "RSV-CASH", "EV-C-LOCK-CASH", SCOPE))
    for owner, ob in (("SELLER_LEG", "OB-SELLER"), ("BUYER_LEG", "OB-BUYER"), ("ORCH", "OB-DVP")):
        c(f"resolve {ob}", k.set_obligation_status(owner, ob, O_RESOLVED, "UNWOUND"))
        c(f"close {ob}", k.set_obligation_status(owner, ob, O_CLOSED))
    p.step("Closed", "Reservations released, case closed as UNWOUND",
           "Release is allowed because no commitment is unresolved and the reversion is evidenced.")
    return p


def _to_exposure(p):
    p.setup(); p.step("Setup", "Root I creates the case", "Same case, same authority tree.")
    p.lock_bond(); p.advance(2); p.lock_cash(); p.advance(2)
    p.step("Locked", "Both legs locked", f"Bond reverts at t={T_BOND}, cash at t={T_CASH}.")
    p.claim_cash(); p.advance(2)
    p.step("Exposure", "Seller claims the cash, preimage now public",
           "The buyer has paid. It must claim the bonds before t=30 or they revert to the seller.")


def s3_death_succession():
    p = Platform("Buyer agent dies during exposure: succession")
    _to_exposure(p)
    k, c = p.k, p.call
    c("kill BUYER_LEG", k.kill("RUNTIME_MONITOR", "BUYER_LEG", "PROCESS_TERMINATED"))
    p.step("Death", "BUYER_LEG crashes while the bond leg is open",
           "Death is one atomic transaction. The agent's authority is revoked, and OB-BUYER passes to its spawner ORCH as RECOVERY_REQUIRED. The obligation outlives the worker.")
    c("kill root", k.kill("RUNTIME_MONITOR", "I", "TEST"), "IMMORTAL")
    p.advance(3)
    p.step("Root", "Attempt to kill root I is rejected",
           "Root I cannot die. It is the recovery owner of last resort, so an obligation can never become ownerless.")
    c("spawn replacement", k.spawn("ORCH", "BUYER_LEG_2", scope=SCOPE))
    c("grant replacement", k.delegate("ORCH", "G-BUYER-2", "G-ORCH", "BUYER_LEG_2", ["CLAIM_SECURITY"],
                                      scope={"trade": [TRADE]}, max_qty=CASH, valid_until=40))
    c("hand over", k.claim_recovery("ORCH", "OB-BUYER", "BUYER_LEG_2"))
    p.step("Recovery", "ORCH spawns BUYER_LEG_2 with a new, narrower grant",
           "The replacement does not inherit the dead agent's authority. It gets only CLAIM_SECURITY, attenuated from ORCH.")
    p.claim_bond("BUYER_LEG_2"); p.advance(1)
    p.step("Settled", f"Replacement claims the bonds at t={k.s['t'] - 1}, before t={T_BOND}", "Both legs are final.")
    p.finish_htlc(buyer="BUYER_LEG_2")
    p.step("Closed", "Case closed", "Obligation, authority lineage and evidence are all intact across the death.")
    return p


def s4_baseline():
    p = Platform("Same death, baseline: death releases everything", death_policy=RELEASE_ALL, enforce=AUDIT)
    _to_exposure(p)
    k, c = p.k, p.call
    c("kill BUYER_LEG", k.kill("RUNTIME_MONITOR", "BUYER_LEG", "PROCESS_TERMINATED"))
    p.step("Death", "BUYER_LEG crashes; its obligation dies with it",
           "Constructed baseline using the original persona death rule. OB-BUYER is left with no owner, and nobody is responsible for claiming the bonds.")
    p.advance(T_BOND - k.s["t"])
    p.step("Loss", f"t={T_BOND}: bond lock reverts to the seller",
           "The buyer paid 9,850,000 wCBDC and received no bonds. The ledgers are individually correct, but the settlement is not atomic.")
    return p


def s5_lost_response():
    p = Platform("Lost ledger response, crash, duplicate blocked")
    p.setup(); p.step("Setup", "Root I creates the case", "Same case, same authority tree.")
    p.lock_bond(); p.advance(2)
    p.step("Leg 1", "Seller locks the bonds", "Confirmed.")
    k, c = p.k, p.call
    p.ledgers["WCBDC"].next_response = "DROP_RESPONSE"
    p.lock_cash(); p.advance(4)
    c("mark unknown", k.mark_unknown("ORCH", "C-LOCK-CASH", "NO_RESPONSE_WITHIN_4_TICKS", SCOPE))
    p.step("Unknown", "wCBDC ledger locks the cash but the response is lost",
           "No reply after 4 ticks. The kernel records OUTCOME_UNKNOWN, not FAILED; the ledger actually holds the lock.")
    c("kill BUYER_LEG", k.kill("RUNTIME_MONITOR", "BUYER_LEG", "PROCESS_TERMINATED"))
    c("spawn replacement", k.spawn("ORCH", "BUYER_LEG_2", scope=SCOPE))
    c("grant replacement", k.delegate("ORCH", "G-BUYER-2", "G-ORCH", "BUYER_LEG_2", ["LOCK_CASH", "CLAIM_SECURITY"],
                                      scope={"trade": [TRADE]}, max_qty=CASH, valid_until=40))
    c("hand over", k.claim_recovery("ORCH", "OB-BUYER", "BUYER_LEG_2"))
    p.step("Crash", "BUYER_LEG crashes and a replacement takes over",
           "Because the cash commitment is unresolved, RSV-CASH becomes HELD_UNCERTAIN and cannot return to the pool.")
    res = p.lock_cash("BUYER_LEG_2", "C-LOCK-CASH-RETRY", expect="DUPLICATE_BLOCKED")
    c("release cash while unknown", k.release_reservation("RECON", "RSV-CASH", None, SCOPE), "UNCERTAIN_EXTERNAL_STATE")
    p.step("Blocked", "Replacement tries to lock the cash again",
           f"Result: {res}. A new transaction id would have locked a second 9,850,000 wCBDC. Releasing the held reservation is also refused.")
    p.query_and_reconcile("C-LOCK-CASH")
    p.step("Reconciled", "RECON queries the ledger: the original lock is final",
           "Verified ledger evidence resolves the unknown, and the reservation returns from HELD_UNCERTAIN to ACTIVE.")
    p.claim_cash(); p.advance(1); p.claim_bond("BUYER_LEG_2"); p.advance(1)
    p.finish_htlc(buyer="BUYER_LEG_2")
    p.step("Closed", "Settlement completes exactly once", "One cash lock, one bond delivery, all evidenced.")
    return p


def s6_attacks():
    p = Platform("Control attacks against the kernel")
    p.setup(); p.lock_bond(); p.lock_cash(); p.advance(2)
    p.step("Setup", "Both legs locked", "Now each control is attacked in turn.")
    k, c = p.k, p.call
    c("buyer self-escalates", k.delegate("BUYER_LEG", "G-EVIL", "G-BUYER", "BUYER_LEG", ["CLAIM_CASH"]), "ESCALATION")
    c("seller exceeds limit", k.submit("SELLER_LEG", "C-BIG", "OB-SELLER", f"{TRADE}:BIG", "WCBDC", "CLAIM_CASH",
                                       12_000_000, SCOPE), "NO_AUTHORITY")
    c("out-of-scope trade", k.submit("SELLER_LEG", "C-OTHER", "OB-SELLER", "DVP-999:LOCK", "SEC_DLT", "LOCK_SECURITY",
                                     BOND, {"trade": "DVP-999"}), "NO_AUTHORITY")
    p.step("Authority", "Escalation, over-limit and out-of-scope instructions",
           "BUYER_LEG has no grant rights, so it cannot give itself CLAIM_CASH. The seller's cap is 9,850,000, and its grant is scoped to DVP-001.")
    p.ledgers["WCBDC"].next_response = "DROP_RESPONSE"
    p.claim_cash()
    c("agent fakes success", k.capture_evidence("SELLER_LEG", "EV-FAKE", "C-CLAIM-CASH", EFFECT_CONFIRMED))
    c("reconcile on fake", k.reconcile("RECON", "C-CLAIM-CASH", "EV-FAKE", SCOPE), "EVIDENCE_NOT_AUTHORITATIVE")
    c("release while uncertain", k.release_reservation("RECON", "RSV-CASH", None, SCOPE), "UNCERTAIN_EXTERNAL_STATE")
    p.step("Evidence", "Seller claims the cash, the reply is lost, and the seller asserts success itself",
           "The agent's statement is stored as an unverified observation. It cannot reconcile the commitment, and the cash reservation cannot be released while the outcome is unresolved.")
    c("revoke buyer", k.revoke("ORCH", "G-BUYER", "OPERATOR_REVOKED"))
    p.claim_bond(expect="NO_AUTHORITY")
    c("kill root", k.kill("RUNTIME_MONITOR", "I", "TEST"), "IMMORTAL")
    p.step("Revocation", "Buyer authority revoked, then the buyer tries to claim the bonds",
           "No active grant means no instruction, even though the buyer knows the preimage. Killing root I is refused.")
    c("kill ORCH", k.kill("RUNTIME_MONITOR", "ORCH", "PROCESS_TERMINATED"))
    p.step("Cascade", "ORCH dies holding the parent obligation",
           "OB-DVP passes to root I. Every grant derived from ORCH is revoked in cascade, so both leg agents are alive but powerless.")
    p.query_and_reconcile("C-CLAIM-CASH")
    c("I regrants buyer", k.delegate("I", "G-BUYER-R", "G-ROOT", "BUYER_LEG", ["CLAIM_SECURITY"],
                                      scope={"trade": [TRADE]}, max_qty=BOND, valid_until=40))
    p.claim_bond(); p.advance(1)
    c("I takes recovery", k.claim_recovery("I", "OB-DVP"))
    p.finish_htlc()
    p.step("Closed", "Root I re-authorises the buyer and the case closes",
           "Recovery ran through the authority lineage. Nothing was lost or duplicated.")
    return p


def s7_unified():
    p = Platform("Unified ledger: single atomic DvP transaction", unified=True)
    p.setup()
    p.step("Setup", "Root I creates the case on a unified ledger",
           "Both assets live on one ledger. A single ATOMIC_DVP transaction moves both legs or neither, so there is no exposure window.")
    k, c = p.k, p.call
    p.ledgers["UNIFIED_LEDGER"].next_response = "DROP_RESPONSE"
    legs = [{"frm": "SELLER", "to": "BUYER", "asset": "BOND", "amount": BOND},
            {"frm": "BUYER", "to": "SELLER", "asset": "WCBDC", "amount": CASH}]
    p.ledger_op("DVP_AGENT", "C-DVP", "OB-DVP", "ATOMIC_DVP", "UNIFIED_LEDGER", "ATOMIC_DVP", "ATOMIC_DVP",
                {"legs": legs}, CASH, affects=["RSV-BOND", "RSV-CASH"])
    p.advance(3)
    c("mark unknown", k.mark_unknown("ORCH", "C-DVP", "NO_RESPONSE_WITHIN_3_TICKS", SCOPE))
    c("kill DVP_AGENT", k.kill("RUNTIME_MONITOR", "DVP_AGENT", "PROCESS_TERMINATED"))
    p.step("Unknown", "The atomic transaction settles, the reply is lost, and the agent dies",
           "Atomicity on the ledger does not remove uncertainty in the control plane. The kernel still has to know whether it happened.")
    c("spawn replacement", k.spawn("ORCH", "DVP_AGENT_2", scope=SCOPE))
    c("grant replacement", k.delegate("ORCH", "G-DVP-2", "G-ORCH", "DVP_AGENT_2", ["ATOMIC_DVP"],
                                      scope={"trade": [TRADE]}, max_qty=CASH, valid_until=40))
    c("hand over", k.claim_recovery("ORCH", "OB-DVP", "DVP_AGENT_2"))
    c("replacement retries", k.submit("DVP_AGENT_2", "C-DVP-RETRY", "OB-DVP", f"{TRADE}:ATOMIC_DVP", "UNIFIED_LEDGER",
                                      "ATOMIC_DVP", CASH, SCOPE), "DUPLICATE_BLOCKED")
    p.query_and_reconcile("C-DVP")
    p.step("Reconciled", "Retry blocked, query confirms settlement",
           "ORCH hands the case to DVP_AGENT_2. Same kernel, same rule: its retry is blocked as a duplicate, and the outcome is taken from ledger evidence.")
    c("consume bond", k.consume_reservation("RECON", "RSV-BOND", "EV-C-DVP", SCOPE))
    c("consume cash", k.consume_reservation("RECON", "RSV-CASH", "EV-C-DVP", SCOPE))
    c("resolve", k.set_obligation_status("DVP_AGENT_2", "OB-DVP", O_RESOLVED, "SETTLED"))
    c("close", k.set_obligation_status("DVP_AGENT_2", "OB-DVP", O_CLOSED))
    p.step("Closed", "Case closed", "Both legs moved together, exactly once.")
    return p


SCENARIOS = [
    ("s1", "Cross-ledger HTLC, no failures", s1_happy),
    ("s2", "Cash leg rejected, both legs unwind", s2_unwind),
    ("s3", "Agent dies mid-settlement: succession", s3_death_succession),
    ("s4", "Same death, baseline: obligation dies with agent", s4_baseline),
    ("s5", "Lost ledger response, crash, duplicate blocked", s5_lost_response),
    ("s6", "Control attacks", s6_attacks),
    ("s7", "Unified ledger atomic DvP", s7_unified),
]
