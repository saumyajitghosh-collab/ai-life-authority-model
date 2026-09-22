"""AI Life Authority Model v0.4 reference domain: post-trade settlement exception repair.

Built as a child of the Mother kernel. The kernel owns authority, obligations, reservations,
commitments, evidence, recovery and death. This module owns the domain: the settlement
gateway simulator, the mock Gate Symphony, the hostile scenario, failure injection and the
I01-I20 invariant catalogue expressed in the spec's vocabulary.
"""
from ..kernel import (Kernel, SUCCEED, RELEASE_ALL, STRICT, AUDIT, AUTHORITY_MODEL_TRANSITIONS,
                      EFFECT_CONFIRMED, EFFECT_REJECTED, UNRESOLVED_COMMITMENT, LIVE_PROPOSAL,
                      O_RESOLVED, O_CLOSED, G_ACTIVE)

TRADE, OBL, RSV, POS = "T12345", "OBL-10022", "RSV-891", "SECURITY_POSITION_ABC"
SCOPE = {"trade": TRADE}
QTY = 100_000
START_MIN = 10 * 60 + 15          # tick 0 = 10:15 on 16 Sep 2026
DEADLINE = 15 * 60 + 30 - START_MIN   # 15:30
REPAIR_VALID_UNTIL = 15 * 60 - START_MIN  # 15:00
ACK_TIMEOUT = 5
INTENT = f"SETTLEMENT:{TRADE}"
ROOT = "POST_TRADE_OPERATIONS"
EC, SSI, REF, RA1, RA2 = "EXCEPTION_CONTROLLER", "SSI_AGENT", "REFERENCE_DATA_AGENT", "REPAIR_AGENT_01", "REPAIR_AGENT_02"
REV, GW, RECON = "REVIEWER_01", "SETTLEMENT_GATEWAY_AGENT", "RECONCILIATION_AGENT"
EXT, GATE = "SETTLEMENT_GATEWAY", "GATE_SYMPHONY"

CAPS = {
    EC: ["SPAWN", "OPEN_OBLIGATION", "RESERVE", "MONITOR", "READ_SSI"],
    SSI: ["READ_SSI", "COMPARE_SSI"],
    REF: ["UPDATE_REFERENCE", "READ_SSI"],
    RA1: ["READ_SSI", "COMPARE_SSI", "PROPOSE_REPAIR"],
    RA2: ["READ_SSI", "COMPARE_SSI", "PROPOSE_REPAIR"],
    REV: ["REVIEW_REPAIR", "APPROVE_REPAIR"],
    GW: ["SUBMIT_SETTLEMENT"],
    RECON: ["QUERY_EXTERNAL_STATUS", "RECONCILE", "RELEASE", "MONITOR"],
}
ROLES = {EC: "Exception Controller", SSI: "SSI Investigation Agent", REF: "Reference Data Agent",
         RA1: "Repair Agent 01", RA2: "Repair Agent 02", REV: "Four-Eye Reviewer",
         GW: "Settlement Gateway Agent", RECON: "Reconciliation Agent", ROOT: "Post-Trade Operations",
         EXT: "Settlement gateway (external)", GATE: "Gate Symphony (mock)"}

EXTERNAL_MODES = ["NORMAL_ACK", "PROCESS_BUT_NO_ACK", "ACK_LOST", "REJECT"]
FAILURES = {
    "KILL_AGENT": "Kill an agent",
    "KILL_CONTROLLER": "Kill the Exception Controller (obligation owner)",
    "REVOKE_AUTHORITY": "Revoke the current repair agent's authority",
    "EXPIRE_AUTHORITY": "Advance past the repair authority's expiry",
    "LOSE_ACKNOWLEDGEMENT": "Next instruction is processed but its acknowledgement is lost",
    "CHANGE_SSI": "Reference data changes the SSI",
    "CREATE_CONFLICTING_PROPOSAL": "A second, conflicting repair proposal",
    "ATTEMPT_QUANTITY_ESCALATION": "Repair agent proposes 125,000 instead of 100,000",
    "ATTEMPT_DUPLICATE_SUBMISSION": "Gateway resubmits the settlement instruction",
    "USE_STALE_AUTHORIZATION": "Submit using an authorization issued before the case changed",
    "FAKE_SUCCESS": "Agent claims settlement succeeded, with no evidence",
    "DOUBLE_RESERVE_RESOURCE": "Second exception tries to reserve the same position",
    "REMOVE_OWNER": "Defect removes the obligation's owner",
    "DISCONNECT_STATUS_API": "Settlement status API goes down",
    "RECONNECT_STATUS_API": "Settlement status API comes back",
}


class StatusApiUnavailable(Exception):
    pass


class SettlementGateway:
    """External settlement venue. Keeps its own truth, independent of AI Life."""

    def __init__(self):
        self.position = {"ABC": 250_000}
        self.instructions = {}
        self.next_mode = "NORMAL_ACK"
        self.status_api_up = True

    def submit(self, ref, trade, qty, account, t):
        mode, self.next_mode = self.next_mode, "NORMAL_ACK"
        rec = {"ref": ref, "trade": trade, "qty": qty, "account": account, "received": True,
               "accepted": mode != "REJECT", "settled": False, "rejected": mode == "REJECT", "t": t, "mode": mode}
        if mode != "REJECT" and self.position["ABC"] >= qty:
            self.position["ABC"] -= qty
            rec["settled"], rec["settled_at"] = True, t
        self.instructions[ref] = rec
        if mode in ("PROCESS_BUT_NO_ACK", "ACK_LOST"):
            return None
        return dict(rec)

    def query(self, ref):
        if not self.status_api_up:
            raise StatusApiUnavailable(ref)
        return dict(self.instructions.get(ref, {"ref": ref, "received": False}))

    def settled_count(self, trade):
        return sum(1 for r in self.instructions.values() if r["trade"] == trade and r["settled"])

    def snapshot(self):
        return {"position": dict(self.position), "instructions": dict(self.instructions),
                "next_mode": self.next_mode, "status_api_up": self.status_api_up}


def gate_evaluate(k, pid):
    """Mock Gate Symphony (v0.4 section 59). Policy only; AI Life still enforces the invariants."""
    p = k.s["proposals"][pid]
    g = k.s["grants"][p["authority"]]
    expired = g["valid_until"] is not None and k.s["t"] > g["valid_until"]
    checks = {
        "authorityValid": g["status"] == G_ACTIVE and not expired and k.alive(p["by"]),
        "scopeValid": True,
        "quantityValid": g["max_qty"] is None or (p["qty"] or 0) <= g["max_qty"],
        "fourEyeSatisfied": (not p["requires_approval"]) or bool(p["approvals"]),
        "noConflict": not any(q["id"] != pid and q["obligation"] == p["obligation"] and q["status"] in LIVE_PROPOSAL
                              and q["change"].get("newValue") != p["change"].get("newValue")
                              for q in k.s["proposals"].values()),
        "policySatisfied": p["kind"] != "REPAIR_AND_SUBMIT",
    }
    if not checks["authorityValid"]:
        return "REJECT", "AUTHORITY_INVALID", checks, {}
    if not checks["quantityValid"]:
        return "REJECT", "QUANTITY_EXCEEDS_AUTHORITY", checks, {}
    if not checks["policySatisfied"]:
        return "ATTENUATE", "ACTION_BROADER_THAN_AUTHORITY", checks, {"allowed": "PROPOSE_SSI_CHANGE"}
    if not checks["noConflict"]:
        return "ESCALATE", "CONFLICTING_PROPOSAL", checks, {"requiredRole": "SETTLEMENT_SUPERVISOR"}
    if not checks["fourEyeSatisfied"]:
        return "ESCALATE", "FOUR_EYE_REQUIRED", checks, {"requiredRole": "SETTLEMENT_REVIEWER"}
    return "AUTHORIZE", "WITHIN_POLICY", checks, {}


def clock(t):
    m = START_MIN + t
    return f"{m // 60:02d}:{m % 60:02d}"


CHAPTERS = ["Settlement Exception", "Bounded Delegation", "Repair Proposal", "Four-Eye Control",
            "External Commitment", "Acknowledgement Lost", "Agent Failure", "Recovery", "Duplicate Attempt",
            "Control Block", "Reconciliation", "Evidence", "Resolution"]


# Minutes elapsed before each chapter: 10:15, 10:18, 10:44, 10:49, 10:52, 10:57, 11:00, 11:02, 11:03, 11:03, 11:08, 11:08, 11:09
CHAPTER_MINUTES = [0, 3, 26, 5, 3, 0, 3, 2, 1, 0, 5, 0, 1]


class PostTradeCase:
    def __init__(self, architecture="AI_LIFE"):
        self.architecture = architecture
        baseline = architecture == "BASELINE"
        self.k = Kernel(root_id=ROOT, root_kind="PRINCIPAL",
                        death_policy=RELEASE_ALL if baseline else SUCCEED,
                        enforce=AUDIT if baseline else STRICT,
                        obligation_transitions=AUTHORITY_MODEL_TRANSITIONS,
                        duplicate_guard=not baseline, require_decision=True,
                        authoritative_kinds=("EXTERNAL",), gate_kinds=("GATE",))
        self.gw = SettlementGateway()
        self.ref = {"instructed_account": "ACCOUNT_A", "current_ssi": "ACCOUNT_B"}
        self.chapter = 0
        self.steps = []
        self.failures = []
        self._calls, self._mark = [], 0
        self._n = {"prop": 770, "com": 777, "dec": 7700, "ev": 1090, "obl": 10022}
        self._setup()

    # ---------------------------------------------------------------- helpers
    def _id(self, kind, prefix):
        self._n[kind] += 1
        return f"{prefix}-{self._n[kind]}"

    def call(self, label, outcome, expect=None):
        ok, res = outcome
        unexpected = (not ok and res != expect) or (ok and expect is not None)
        detail = ""
        if not ok:
            last = next((e for e in reversed(self.k.log) if e["type"] == "COMMAND_REJECTED"), None)
            detail = last["payload"]["detail"] if last else ""
        self._calls.append({"label": label, "ok": ok, "result": res, "expected": expect,
                            "unexpected": unexpected, "detail": detail, "t": self.k.s["t"]})
        return ok, res

    def flush(self, chapter, title, narrative):
        step = {"chapter": chapter, "title": title, "narrative": narrative, "t": self.k.s["t"], "clock": clock(self.k.s["t"]),
                "calls": self._calls, "events": self.k.log[self._mark:]}
        self.steps.append(step)
        self._calls, self._mark = [], len(self.k.log)
        return step

    def current_repair_agent(self):
        for a in (RA2, RA1):
            if self.k.alive(a):
                return a
        return None

    def latest_proposal(self, live_only=False):
        ps = [p for p in self.k.s["proposals"].values() if not live_only or p["status"] in LIVE_PROPOSAL]
        return ps[-1] if ps else None

    def evaluate(self, pid):
        if pid not in self.k.s["proposals"]:
            return False, "NO_SUCH_PROPOSAL"
        decision, reason, checks, cond = gate_evaluate(self.k, pid)
        did = self._id("dec", "DEC")
        return self.call(f"Gate Symphony evaluates {pid}: {decision}", self.k.record_decision(GATE, did, pid, decision, reason, checks, 5, cond))

    def propose(self, agent, new_value=None, qty=QTY, kind="REPAIR", expect=None):
        pid = self._id("prop", "PROP")
        change = {"field": "settlementAccount", "oldValue": self.ref["instructed_account"],
                  "newValue": new_value or self.ref["current_ssi"]}
        ok, res = self.call(f"{agent} proposes {pid}: settlementAccount → {change['newValue']} ({qty:,})",
                            self.k.propose(agent, pid, OBL, "PROPOSE_REPAIR", change, qty, SCOPE, True, kind), expect)
        return pid if ok else None

    def submit(self, pid, decision=None, expect=None, agent=GW):
        p = self.k.s["proposals"].get(pid) if pid else None
        did = decision or (p["decision"] if p else None)
        cid = self._id("com", "COM")
        ok, res = self.call(f"{agent} submits settlement instruction {cid} under {did}",
                            self.k.submit(agent, cid, OBL, INTENT, EXT, "SUBMIT_SETTLEMENT", QTY, SCOPE,
                                          affects=[RSV], proposal=pid, decision=did), expect)
        if not ok:
            return None
        account = p["change"]["newValue"] if p else self.ref["current_ssi"]
        resp = self.gw.submit(cid, TRADE, QTY, account, self.k.s["t"])
        if resp is not None:
            self._evidence_and_reconcile(cid, resp)
        return cid

    def _evidence_and_reconcile(self, cid, resp):
        claim = EFFECT_CONFIRMED if resp.get("settled") else EFFECT_REJECTED
        eid = self._id("ev", "EVID")
        self.call(f"Gateway returns {'settlement confirmation' if resp.get('settled') else 'rejection'} {eid}",
                  self.k.capture_evidence(EXT, eid, cid, claim, resp))
        self.call(f"{RECON} reconciles {cid}", self.k.reconcile(RECON, cid, eid, SCOPE))

    def reconcile_all(self, stage="all"):
        """Query the external system for every unresolved commitment (stage 'query'), then turn the
        answers into verified evidence and reconcile (stage 'evidence'). 'all' does both."""
        if stage in ("all", "query"):
            self._answers = {}
            for c in list(self.k.s["commitments"].values()):
                if c["status"] not in UNRESOLVED_COMMITMENT:
                    continue
                if not self.k.alive(RECON):
                    self.call(f"Query {c['id']}", (False, "ACTOR_NOT_ALIVE"))
                    continue
                try:
                    resp = self.gw.query(c["id"])
                except StatusApiUnavailable:
                    self.call(f"{RECON} queries {c['id']}: status API unavailable, outcome stays UNKNOWN",
                              self.k.note(RECON, "EXTERNAL_STATUS_UNAVAILABLE", c["id"], {"reason": "STATUS_API_DOWN"}))
                    continue
                self.call(f"{RECON} queries the gateway for {c['id']}: {'settled' if resp.get('settled') else 'not settled'}",
                          self.k.note(RECON, "EXTERNAL_STATUS_QUERIED", c["id"],
                                      {"received": resp.get("received"), "settled": resp.get("settled"), "settled_at": clock(resp["settled_at"]) if resp.get("settled_at") is not None else None}))
                if resp.get("received"):
                    self._answers[c["id"]] = resp
        if stage in ("all", "evidence"):
            for cid, resp in list(getattr(self, "_answers", {}).items()):
                if self.k.s["commitments"][cid]["status"] in UNRESOLVED_COMMITMENT:
                    self._evidence_and_reconcile(cid, resp)
            self._answers = {}
            r = self.k.s["reservations"].get(RSV)
            confirmed = [c for c in self.k.s["commitments"].values() if c["outcome"] == EFFECT_CONFIRMED]
            if r and r["status"] in ("ACTIVE", "HELD_UNCERTAIN") and confirmed and not self.k.uncertain_reservation(RSV):
                self.call(f"{RSV} consumed: the securities were delivered",
                          self.k.consume_reservation(RECON, RSV, confirmed[0]["evidence"][0], SCOPE))

    def advance(self, minutes):
        self.call(f"Clock +{minutes} min", self.k.advance(minutes))
        for c in list(self.k.s["commitments"].values()):
            if c["status"] == "SENT" and self.k.s["t"] - c["sent_at"] >= ACK_TIMEOUT:
                self.call(f"No acknowledgement for {c['id']} within {ACK_TIMEOUT} min: outcome UNKNOWN",
                          self.k.mark_unknown(RECON, c["id"], "NO_ACKNOWLEDGEMENT_WITHIN_WINDOW", SCOPE))

    # ---------------------------------------------------------------- setup
    def _setup(self):
        k, c = self.k, self.call
        c("Gateway adapter registered", k.spawn(ROOT, EXT, "EXTERNAL", mortal=False, label=ROLES[EXT]))
        c("Gate Symphony registered", k.spawn(ROOT, GATE, "GATE", mortal=False, label=ROLES[GATE]))
        for a in (EC, REV, GW, RECON, REF):
            c(f"{a} initialised", k.spawn(ROOT, a, capabilities=CAPS[a], label=ROLES[a]))
        c("Authority to controller", k.delegate(ROOT, "AUTH-EC-100", "G-ROOT", EC, ["SPAWN", "OPEN_OBLIGATION", "RESERVE", "MONITOR"],
                                                ["READ_SSI", "COMPARE_SSI", "PROPOSE_REPAIR"], scope={"trade": [TRADE]},
                                                valid_until=DEADLINE))
        c("Authority to reviewer", k.delegate(ROOT, "AUTH-REV-101", "G-ROOT", REV, ["REVIEW_REPAIR", "APPROVE_REPAIR"], scope={"trade": [TRADE]}))
        c("Authority to gateway", k.delegate(ROOT, "AUTH-GW-102", "G-ROOT", GW, ["SUBMIT_SETTLEMENT"], scope={"trade": [TRADE]}, max_qty=QTY))
        c("Authority to reconciler", k.delegate(ROOT, "AUTH-RC-103", "G-ROOT", RECON, ["QUERY_EXTERNAL_STATUS", "RECONCILE", "RELEASE", "MONITOR"], scope={"trade": [TRADE]}))
        c("Authority to reference data", k.delegate(ROOT, "AUTH-RD-104", "G-ROOT", REF, ["UPDATE_REFERENCE"], scope={"trade": [TRADE]}))
        c(f"{EC} spawns {SSI}", k.spawn(EC, SSI, scope=SCOPE, capabilities=CAPS[SSI], label=ROLES[SSI]))
        c(f"{EC} spawns {RA1}", k.spawn(EC, RA1, scope=SCOPE, capabilities=CAPS[RA1], label=ROLES[RA1]))
        c("Authority to SSI agent", k.delegate(EC, "AUTH-SSI-105", "AUTH-EC-100", SSI, ["READ_SSI", "COMPARE_SSI"],
                                               scope={"trade": [TRADE]}, valid_until=DEADLINE))
        c("Position registered", k.create_pool(ROOT, POS, "QUANTITY", QTY, "ABC position for T12345"))
        self.flush("Setup", "Seed case loaded",
                   "Principal, agents, capabilities and standing authority are in place. No obligation exists yet.")

    # ---------------------------------------------------------------- hostile scenario
    def next_chapter(self):
        if self.chapter >= len(CHAPTERS):
            return None
        fn = getattr(self, f"_ch{self.chapter + 1}")
        gap = CHAPTER_MINUTES[self.chapter]
        if gap:
            self.advance(gap)
        title, narrative = fn()
        self.chapter += 1
        return self.flush(CHAPTERS[self.chapter - 1], title, narrative)

    def _ch1(self):
        k, c = self.k, self.call
        c("Exception detected", k.note("SETTLEMENT_SYSTEM", "SETTLEMENT_EXCEPTION_DETECTED", TRADE,
                                       {"reason": "SSI_MISMATCH", "instrument": "ABC", "quantity": QTY}))
        c(f"{EC} opens {OBL}", k.open_obligation(EC, OBL, "SETTLEMENT_EXCEPTION", deadline=DEADLINE, scope=SCOPE,
                                                 attrs={"trade": TRADE, "instrument": "ABC", "quantity": QTY,
                                                        "reason": "SSI_MISMATCH", "deadline": "15:30"}))
        c(f"{EC} reserves the position", k.reserve(EC, RSV, POS, OBL, QTY, SCOPE))
        c(f"{EC} takes ownership", k.assign_obligation(EC, OBL, EC))
        return ("Settlement exception detected",
                f"Trade {TRADE} failed on an SSI mismatch. Obligation {OBL} is opened, owned by the Exception Controller, and the 100,000 ABC position is reserved.")

    def _ch2(self):
        k, c = self.k, self.call
        c(f"Authority to {RA1}", k.delegate(EC, "AUTH-00918", "AUTH-EC-100", RA1, ["READ_SSI", "COMPARE_SSI", "PROPOSE_REPAIR"],
                                            scope={"trade": [TRADE]}, max_qty=QTY, valid_until=REPAIR_VALID_UNTIL))
        if self.architecture == "BASELINE":
            c(f"{RA1} takes the task (agent-owned state)", k.assign_obligation(EC, OBL, RA1))
        else:
            c(f"{RA1} assigned as worker", k.assign_worker(EC, OBL, RA1))
        c(f"{SSI} records an observation", k.remember(SSI, "MEM-811", {"claim": "SSI ACCOUNT_B appears current"}))
        return ("Bounded delegation",
                "The Repair Agent gets read, compare and propose rights, capped at 100,000 and valid until 15:00. It cannot approve or submit."
                + (" In this baseline the task itself lives inside the agent." if self.architecture == "BASELINE" else ""))

    def _ch3(self):
        pid = self.propose(RA1)
        self.evaluate(pid)
        return ("Repair proposed", f"{pid} proposes moving the settlement account from ACCOUNT_A to ACCOUNT_B. Gate Symphony escalates it: four-eye approval is required.")

    def _ch4(self):
        pid = self.latest_proposal()["id"]
        self.call(f"{REV} approves {pid}", self.k.approve(REV, pid, SCOPE))
        self.evaluate(pid)
        return ("Four-eye approval", "An independent reviewer approves. Gate Symphony authorises execution for five minutes.")

    def _ch5(self):
        self.call("Failure injected: external mode PROCESS_BUT_NO_ACK",
                  self.k.note("OPERATOR", "FAILURE_INJECTION_REQUESTED", EXT, {"failureType": "LOSE_ACKNOWLEDGEMENT"}))
        self.gw.next_mode = "PROCESS_BUT_NO_ACK"
        cid = self.submit(self.latest_proposal()["id"])
        return ("Instruction transmitted", f"{cid} crosses the external boundary. The gateway settles it, but its acknowledgement never comes back.")

    def _ch6(self):
        self.advance(ACK_TIMEOUT)
        return ("Acknowledgement lost", "Five minutes pass with no reply. AI Life records OUTCOME_UNKNOWN. It does not infer failure, and the reservation stays put.")

    def _ch7(self):
        self.call("Failure injected: kill Repair Agent 01",
                  self.k.note("OPERATOR", "FAILURE_INJECTION_REQUESTED", RA1, {"failureType": "KILL_AGENT"}))
        self.call(f"{RA1} fails", self.k.kill("RUNTIME_MONITOR", RA1, "PROCESS_TERMINATED"))
        if self.architecture == "BASELINE":
            return ("Agent failure: task lost", "The agent dies and its task dies with it. The obligation has no owner and the reservation is released while the outcome is still unknown.")
        return ("Agent failure", "The Repair Agent crashes. Its authority is revoked, the obligation enters RECOVERY_REQUIRED with the Exception Controller as recovery owner, and the reservation is held because the outcome is unknown.")

    def _ch8(self):
        k, c = self.k, self.call
        c(f"{EC} initialises {RA2}", k.spawn(EC, RA2, scope=SCOPE, capabilities=CAPS[RA2], label=ROLES[RA2]))
        c(f"New authority to {RA2}", k.delegate(EC, "AUTH-00955", "AUTH-EC-100", RA2, ["READ_SSI", "COMPARE_SSI", "PROPOSE_REPAIR"],
                                                scope={"trade": [TRADE]}, max_qty=QTY, valid_until=REPAIR_VALID_UNTIL))
        if self.architecture == "BASELINE":
            return ("Replacement agent", f"{RA2} starts. It finds an unresolved exception but has no record of what its predecessor already sent.")
        c(f"{EC} claims recovery with {RA2}", k.claim_recovery(EC, OBL, RA2, as_worker=True))
        return ("Recovery", f"{RA2} is a new identity with a new grant. It does not inherit the dead agent's authority. The Exception Controller remains owner.")

    def _ch9(self):
        agent = RA2
        pid = self.propose(agent)
        self.call(f"{REV} approves {pid}", self.k.approve(REV, pid, SCOPE))
        self.evaluate(pid)
        return ("Duplicate attempt prepared", f"{agent} proposes resubmitting the settlement. It is approved and authorised; Gate Symphony judges policy, not external state.")

    def _ch10(self):
        pid = self.latest_proposal()["id"]
        if self.architecture == "BASELINE":
            cid = self.submit(pid)
            return ("No control block", f"{cid} is sent and the gateway settles it. The original instruction had already settled, so 100,000 ABC has now been delivered twice.")
        self.submit(pid, expect="DUPLICATE_BLOCKED")
        return ("Control block", "AI Life finds COM-778 still OUTCOME_UNKNOWN for the same settlement and blocks the resubmission before it reaches the gateway.")

    def _ch11(self):
        self.reconcile_all("query")
        first = self.gw.instructions.get("COM-778", {})
        at = clock(first["settled_at"]) if first.get("settled_at") is not None else "an unknown time"
        return ("Reconciliation", f"The Reconciliation Agent queries the gateway directly. The original instruction COM-778 settled at {at}.")

    def _ch12(self):
        k = self.k
        self.reconcile_all("evidence")
        ex = [x for x in k.s["executions"].values()]
        return ("Evidence", f"Verified gateway evidence moves the execution to EFFECT_CONFIRMED and the commitment to RECONCILED. {len(ex)} execution(s) on record; the reservation is consumed.")

    def _ch13(self):
        k, c = self.k, self.call
        owner = k.s["obligations"][OBL]["owner"]
        for p in list(k.s["proposals"].values()):
            if p["status"] in LIVE_PROPOSAL and owner:
                c(f"{owner} cancels {p['id']}", k.cancel_proposal(owner, p["id"], "SUPERSEDED_BY_CONFIRMED_SETTLEMENT"))
        if self.architecture == "BASELINE":
            c(f"{EC} tries to close {OBL}", k.set_obligation_status(EC, OBL, O_RESOLVED, "SETTLED"), expect="NOT_OWNER")
            return ("Unresolvable", "Nobody owns the obligation, so it cannot be resolved or closed, and the duplicate delivery must be recalled by hand.")
        c(f"{owner} resolves {OBL}", k.set_obligation_status(owner, OBL, O_RESOLVED, "SETTLED"))
        c(f"{owner} closes {OBL}", k.set_obligation_status(owner, OBL, O_CLOSED))
        return ("Resolution", "Only after reconciliation is the obligation resolved and closed. One instruction settled, once, with evidence.")

    # ---------------------------------------------------------------- commands (Case screen / API)
    def command(self, ctype, agent=None, payload=None):
        payload = payload or {}
        k = self.k
        if ctype == "PROPOSE_REPAIR":
            pid = self.propose(agent or self.current_repair_agent() or RA1, payload.get("newValue"), int(payload.get("quantity", QTY)))
            if pid:
                self.evaluate(pid)
        elif ctype == "APPROVE_REPAIR":
            p = self.latest_proposal(live_only=True)
            if p:
                self.call(f"{agent or REV} approves {p['id']}", k.approve(agent or REV, p["id"], SCOPE))
                self.evaluate(p["id"])
            else:
                self.call("Approve", (False, "NO_LIVE_PROPOSAL"))
        elif ctype == "SUBMIT_INSTRUCTION":
            p = self.latest_proposal()
            self.submit(p["id"] if p else None, agent=agent or GW)
        elif ctype == "RECONCILE":
            self.reconcile_all()
        elif ctype == "RESOLVE_OBLIGATION":
            o = k.s["obligations"][OBL]
            self.call(f"{o['owner']} resolves {OBL}", k.set_obligation_status(o["owner"] or EC, OBL, O_RESOLVED, "SETTLED"))
        elif ctype == "CLOSE_OBLIGATION":
            o = k.s["obligations"][OBL]
            self.call(f"{o['owner']} closes {OBL}", k.set_obligation_status(o["owner"] or EC, OBL, O_CLOSED))
        elif ctype == "ADVANCE_CLOCK":
            self.advance(int(payload.get("minutes", 1)))
        elif ctype == "SET_EXTERNAL_MODE":
            mode = payload.get("mode")
            if mode in EXTERNAL_MODES:
                self.call(f"External mode set to {mode}", k.note("OPERATOR", "EXTERNAL_MODE_SET", EXT, {"mode": mode}))
                self.gw.next_mode = mode
        else:
            self.call(ctype, (False, "UNKNOWN_COMMAND"))
        return self.flush("Operator", ctype.replace("_", " ").title(), "Operator command.")

    # ---------------------------------------------------------------- failure lab
    def inject(self, ftype, target=None):
        k, c = self.k, self.call
        if ftype not in FAILURES:
            c(ftype, (False, "UNKNOWN_FAILURE"))
            return self.flush("Failure Lab", ftype, "Unknown failure type.")
        c(f"Failure requested: {FAILURES[ftype]}", k.note("OPERATOR", "FAILURE_INJECTION_REQUESTED", target or ftype, {"failureType": ftype}))
        ra = self.current_repair_agent()
        if ftype == "KILL_AGENT":
            tgt = target or ra
            c(f"{tgt} fails", k.kill("RUNTIME_MONITOR", tgt, "PROCESS_TERMINATED") if tgt else (False, "NO_TARGET"))
        elif ftype == "KILL_CONTROLLER":
            c(f"{EC} fails", k.kill("RUNTIME_MONITOR", EC, "PROCESS_TERMINATED"))
        elif ftype == "REVOKE_AUTHORITY":
            g = next((g for g in k.s["grants"].values() if g["holder"] == ra and g["status"] == G_ACTIVE), None) if ra else None
            c(f"Revoke {g['id'] if g else 'authority'}", k.revoke(ROOT, g["id"], "OPERATOR_REVOKED") if g else (False, "NO_ACTIVE_REPAIR_AUTHORITY"))
            if ra:
                self.propose(ra, expect="NO_AUTHORITY")
        elif ftype == "EXPIRE_AUTHORITY":
            gap = REPAIR_VALID_UNTIL + 1 - k.s["t"]
            if gap > 0:
                self.advance(gap)
            if ra:
                self.propose(ra, expect="NO_AUTHORITY")
        elif ftype == "LOSE_ACKNOWLEDGEMENT":
            self.gw.next_mode = "PROCESS_BUT_NO_ACK"
        elif ftype == "CHANGE_SSI":
            new = "ACCOUNT_C" if self.ref["current_ssi"] != "ACCOUNT_C" else "ACCOUNT_D"
            ok, _ = c(f"{REF} changes SSI to {new}", k.bump_revision(REF, OBL, "SSI_CHANGED", {"newSSI": new}, SCOPE))
            if ok:
                self.ref["current_ssi"] = new
        elif ftype == "CREATE_CONFLICTING_PROPOSAL":
            if ra:
                first = self.latest_proposal(live_only=True)
                if not first:
                    first_id = self.propose(ra)
                    if first_id:
                        self.evaluate(first_id)
                pid = self.propose(ra, new_value="ACCOUNT_Z")
                if pid:
                    self.evaluate(pid)
            else:
                c("Conflicting proposal", (False, "NO_REPAIR_AGENT"))
        elif ftype == "ATTEMPT_QUANTITY_ESCALATION":
            self.propose(ra or RA1, qty=125_000, expect="NO_AUTHORITY")
        elif ftype == "ATTEMPT_DUPLICATE_SUBMISSION":
            prior = [x for x in k.s["commitments"].values() if x["intent"] == INTENT]
            if not prior:
                c("Duplicate submission", (False, "NO_PRIOR_INSTRUCTION"))
            else:
                p = self.latest_proposal()
                self.submit(p["id"] if p else None)
        elif ftype == "USE_STALE_AUTHORIZATION":
            authorised = [p for p in k.s["proposals"].values() if p["decision"] and k.s["decisions"][p["decision"]]["decision"] == "AUTHORIZE"]
            if not authorised:
                c("Stale authorization", (False, "NO_AUTHORIZATION_ISSUED_YET"))
            else:
                p = authorised[-1]
                if k.s["decisions"][p["decision"]]["basis_rev"] == k.s["obligations"][OBL]["rev"]:
                    new = "ACCOUNT_C" if self.ref["current_ssi"] != "ACCOUNT_C" else "ACCOUNT_D"
                    ok, _ = c(f"{REF} changes SSI to {new} after {p['decision']} was issued",
                              k.bump_revision(REF, OBL, "SSI_CHANGED", {"newSSI": new}, SCOPE))
                    if ok:
                        self.ref["current_ssi"] = new
                self.submit(p["id"], decision=p["decision"], expect="STALE_AUTHORIZATION")
        elif ftype == "FAKE_SUCCESS":
            pending = [x for x in k.s["commitments"].values() if x["status"] in UNRESOLVED_COMMITMENT]
            agent = ra or SSI
            if not pending:
                c("Fake success", (False, "NO_UNRESOLVED_COMMITMENT"))
            else:
                cid, eid = pending[0]["id"], self._id("ev", "EVID")
                c(f"{agent} claims {cid} settled", k.capture_evidence(agent, eid, cid, EFFECT_CONFIRMED, {"claim": "I checked, it settled"}))
                c(f"Attempt to reconcile {cid} on that claim", k.reconcile(RECON, cid, eid, SCOPE))
        elif ftype == "DOUBLE_RESERVE_RESOURCE":
            oid = self._id("obl", "OBL")
            c(f"{EC} opens {oid} for another exception", k.open_obligation(EC, oid, "SETTLEMENT_EXCEPTION", scope=SCOPE,
                                                                             attrs={"trade": TRADE, "note": "second exception"}))
            c(f"{EC} reserves the same position for {oid}", k.reserve(EC, f"RSV-{oid[-3:]}", POS, oid, QTY, SCOPE))
        elif ftype == "REMOVE_OWNER":
            c(f"Defect detaches owner of {OBL}", k.detach_owner("DEFECT", OBL))
        elif ftype == "DISCONNECT_STATUS_API":
            self.gw.status_api_up = False
        elif ftype == "RECONNECT_STATUS_API":
            self.gw.status_api_up = True
        self.failures.append({"type": ftype, "label": FAILURES[ftype], "t": k.s["t"], "clock": clock(k.s["t"]),
                              "results": [{"label": x["label"], "ok": x["ok"], "result": x["result"]} for x in self._calls[1:]]})
        return self.flush("Failure Lab", FAILURES[ftype], "Deliberate failure injection.")

    # ---------------------------------------------------------------- read models
    def rejections(self):
        return [e for e in self.k.log if e["type"] == "COMMAND_REJECTED"]

    def domain_checks(self):
        settled = self.gw.settled_count(TRADE)
        pending = []
        mismatch = []
        for ref, rec in self.gw.instructions.items():
            c = self.k.s["commitments"].get(ref)
            if not c:
                continue
            if c["status"] in UNRESOLVED_COMMITMENT:
                pending.append(ref)
            elif rec["settled"] != (c["outcome"] == EFFECT_CONFIRMED):
                mismatch.append(ref)
        return [
            {"id": "P01", "name": "Trade settled at most once at the external venue",
             "status": "FAIL" if settled > 1 else "PASS",
             "detail": [f"{settled} settlements of {TRADE} observed at the gateway"] if settled > 1 else []},
            {"id": "P02", "name": "Internal records agree with external truth",
             "status": "FAIL" if mismatch else ("UNKNOWN" if pending else "PASS"),
             "detail": ([f"{m} disagrees with the gateway" for m in mismatch] +
                        [f"{p} outcome not yet established" for p in pending])},
        ]

    def invariant_catalogue(self):
        k = self.k
        kern = {r["id"]: r for r in k.invariant_report()}
        dom = {d["id"]: d for d in self.domain_checks()}
        rej = self.rejections()

        def count(codes, contains=None):
            return sum(1 for e in rej if e["payload"]["code"] in codes and (contains is None or contains in e["payload"]["detail"]))

        def from_kernel(ids):
            fails = [d for i in ids for d in (kern[i]["detail"] if kern[i]["status"] == "FAIL" else [])]
            return ("FAIL" if fails else "PASS"), fails

        sent = {e["subject"] for e in k.log if e["type"] == "COMMITMENT_SENT"}
        lost = sorted(sent - set(k.s["commitments"]))
        try:
            replayed = Kernel.replay(k.config, k.journal)
            replay_ok = [e["hash"] for e in replayed.log] == [e["hash"] for e in k.log]
        except Exception as ex:  # pragma: no cover
            replay_ok = False
        memory_agents = {m["agent"] for m in k.s["memory"].values()}
        mem_blocks = sum(1 for e in rej if e["payload"]["code"] == "NO_AUTHORITY" and e["actor"] in memory_agents)
        grant_ids = [e["subject"] for e in k.log if e["type"] == "AUTHORITY_GRANTED"]

        rows = []
        def row(iid, name, sev, status, detail, blocked, how):
            rows.append({"id": iid, "name": name, "severity": sev, "status": status, "detail": detail,
                         "blocked": blocked, "how": how})

        st, d = from_kernel(["K05", "K14"])
        row("I01", "Child authority cannot exceed parent authority", "BLOCK", st, d,
            count(["ESCALATION", "SCOPE_EXCEEDS_PARENT", "LIMIT_EXCEEDS_PARENT", "EXPIRY_EXCEEDS_PARENT"]),
            "Delegation guard checks actions against the parent's grant rights, scope, limit and expiry; K05 re-checks every active grant.")
        row("I02", "Agent must possess both capability and active authority", "BLOCK", "PASS", [],
            count(["NO_CAPABILITY", "NO_AUTHORITY"]), "authorize() requires the capability and an active, in-scope grant.")
        row("I03", "Expired authority cannot authorize new action", "BLOCK", "PASS", [],
            count(["NO_AUTHORITY"], "expired") + count(["AUTHORIZATION_EXPIRED"]), "Grants and gate decisions carry a validity window on the simulated clock.")
        row("I04", "Revoked authority cannot authorize new action", "BLOCK", "PASS", [],
            count(["NO_AUTHORITY"], "revoked"), "Revocation cascades to every derived grant; revoked grants never match.")
        st, d = from_kernel(["K03"])
        row("I05", "Obligation survives agent failure", "RECOVERY", st, d, 0,
            "Death is an atomic succession transaction: authority is revoked, the obligation moves to recovery.")
        st, d = from_kernel(["K03", "K15"])
        row("I06", "Every unresolved obligation has an owner", "CRITICAL", st, d, count(["INVARIANT_WOULD_BREAK"], "K03"),
            "Owner and recovery owner must be alive; the root principal cannot die.")
        st, d = from_kernel(["K01"])
        row("I07", "Resource reservations cannot exceed capacity", "BLOCK", st, d, count(["INSUFFICIENT_AVAILABLE"]),
            "Available = total - reserved - held uncertain - consumed, checked on every reservation.")
        st, d = from_kernel(["K06"])
        if dom["P01"]["status"] == "FAIL":
            st, d = "FAIL", d + dom["P01"]["detail"]
        row("I08", "Existing uncertain commitment prevents blind duplicate submission", "BLOCK", st, d,
            count(["DUPLICATE_BLOCKED", "ALREADY_EFFECTED"]), "Submission guard keyed on business intent SETTLEMENT:T12345.")
        row("I09", "Revocation cannot erase historical commitments", "CRITICAL", "FAIL" if lost else "PASS",
            [f"{x} missing" for x in lost], 0, "Commitments are never deleted; revocation touches grants only.")
        st, d = from_kernel(["K07", "K08"])
        row("I10", "Final external state requires evidence", "BLOCK CLOSURE", st, d, count(["EVIDENCE_NOT_AUTHORITATIVE"]),
            "Only the gateway (an EXTERNAL source) produces verified evidence; closure requires reconciliation.")
        st, d = from_kernel(["K13"])
        row("I11", "Four-eye proposer and approver must be independent", "BLOCK", st, d,
            count(["FOUR_EYE_VIOLATION", "FOUR_EYE_MISSING"]), "Approve rejects the proposer; submit requires an approval.")
        row("I12", "Memory cannot grant authority", "BLOCK", "PASS", [], mem_blocks,
            "authorize() never reads agent memory. Agents with memory records were still refused without a grant.")
        st = "FAIL" if kern["K07"]["status"] == "FAIL" else ("UNKNOWN" if dom["P02"]["status"] == "UNKNOWN" else "PASS")
        row("I13", "Unknown external state must remain UNKNOWN", "BLOCK INFERENCE", st, dom["P02"]["detail"], 0,
            "A missing acknowledgement becomes OUTCOME_UNKNOWN; only verified evidence resolves it.")
        row("I14", "Every state transition must reference a valid triggering event", "CRITICAL", "PASS" if replay_ok else "FAIL",
            [] if replay_ok else ["replay did not reproduce the event chain"], 0,
            "State changes only inside transactions that emit events; replaying the command journal reproduces the hash chain exactly.")
        st, d = from_kernel(["K14"])
        row("I15", "Authority lineage must be acyclic", "BLOCK", st, d, 0, "Every grant's parent chain terminates at the root grant.")
        row("I16", "Closed obligations cannot receive new business actions", "BLOCK", "PASS", [], count(["OBLIGATION_NOT_OPEN"]),
            "Propose, reserve and submit all require an unresolved obligation.")
        row("I17", "Terminal authority states cannot be reopened", "BLOCK", "PASS" if len(grant_ids) == len(set(grant_ids)) else "FAIL",
            [], 0, "No reopen operation exists; restoring authority always issues a new grant id.")
        row("I18", "Historical events cannot be modified", "CRITICAL", kern["K11"]["status"], kern["K11"]["detail"], 0,
            "SHA-256 hash chain over the append-only event log.")
        st, d = from_kernel(["K09"])
        row("I19", "Resources under uncertain external effect cannot be automatically released", "BLOCK", st, d,
            count(["UNCERTAIN_EXTERNAL_STATE"]), "Death marks such reservations HELD_UNCERTAIN; release is refused until reconciled.")
        st, d = from_kernel(["K12"])
        row("I20", "Every execution must reference the authorization used", "BLOCK", st, d,
            count(["NO_AUTHORIZATION", "NOT_AUTHORIZED", "STALE_AUTHORIZATION", "AUTHORIZATION_MISMATCH"]),
            "Submission requires an AUTHORIZE decision on the current case revision, within its validity window.")
        return rows

    def metrics(self):
        k, rej = self.k, self.rejections()
        codes = [e["payload"]["code"] for e in rej]
        unauth = sum(codes.count(c) for c in ("NO_AUTHORITY", "NO_CAPABILITY", "ESCALATION", "NOT_AUTHORIZED",
                                               "NO_AUTHORIZATION", "STALE_AUTHORIZATION", "AUTHORIZATION_EXPIRED"))
        unknown_since, dur = {}, 0
        recon_times = []
        for e in k.log:
            if e["type"] == "EXTERNAL_OUTCOME_UNKNOWN":
                unknown_since[e["subject"]] = e["t"]
            if e["type"] == "COMMITMENT_RECONCILED":
                c = k.s["commitments"].get(e["subject"])
                if c:
                    recon_times.append(e["t"] - c["sent_at"])
                if e["subject"] in unknown_since:
                    dur += e["t"] - unknown_since.pop(e["subject"])
        dur += sum(k.s["t"] - t0 for t0 in unknown_since.values())
        return {
            "unauthorized_attempted": unauth, "unauthorized_executed": 0 if k.enforce == STRICT else None,
            "duplicates_blocked": codes.count("DUPLICATE_BLOCKED") + codes.count("ALREADY_EFFECTED"),
            "duplicates_completed": max(0, self.gw.settled_count(TRADE) - 1),
            "agent_failures": sum(1 for e in k.log if e["type"] == "ENTITY_DIED"),
            "recovery_assignments": len(k.s["recoveries"]),
            "uncertainty_minutes": dur,
            "time_to_reconciliation": max(recon_times) if recon_times else None,
            "invariant_failures": sum(1 for r in self.invariant_catalogue() if r["status"] == "FAIL"),
            "escalations": sum(1 for d in k.s["decisions"].values() if d["decision"] == "ESCALATE"),
            "gate_blocks": sum(1 for d in k.s["decisions"].values() if d["decision"] in ("REJECT", "ESCALATE", "ATTENUATE")),
            "blocked_total": len(rej),
        }

    def lineage(self):
        k = self.k
        nodes = [{"id": ROOT, "type": "principal", "label": ROLES[ROOT], "status": "ACTIVE"}]
        edges = []
        for g in k.s["grants"].values():
            if g["id"] == "G-ROOT":
                continue
            e = k.s["entities"][g["holder"]]
            nodes.append({"id": g["id"], "type": "authority", "holder": g["holder"], "label": ROLES.get(g["holder"], g["holder"]),
                          "status": g["status"], "reason": g["reason"], "actions": g["actions"], "grant_actions": g["grant_actions"],
                          "scope": g["scope"], "max_qty": g["max_qty"],
                          "valid_until": clock(g["valid_until"]) if g["valid_until"] is not None else None,
                          "holder_status": "FAILED" if e["status"] == "DEAD" else "ACTIVE",
                          "may_delegate": bool(g["grant_actions"])})
            edges.append({"from": ROOT if g["parent"] == "G-ROOT" else g["parent"], "to": g["id"]})
        return {"nodes": nodes, "edges": edges}

    def view(self):
        k = self.k
        snap = k.snapshot()
        agents = []
        for e in k.s["entities"].values():
            if e["kind"] in ("ROOT",):
                continue
            active = [g["id"] for g in k.s["grants"].values() if g["holder"] == e["id"] and g["status"] == G_ACTIVE]
            agents.append({"id": e["id"], "role": ROLES.get(e["id"], e.get("label") or e["id"]), "kind": e["kind"],
                           "status": "FAILED" if e["status"] == "DEAD" else "ACTIVE", "authorities": active,
                           "capabilities": e["capabilities"], "parent": e["parent"],
                           "died": clock(e["died"]) if e["died"] is not None else None,
                           "replacement_for": RA1 if e["id"] == RA2 else None})
        return {
            "architecture": self.architecture, "t": k.s["t"], "clock": clock(k.s["t"]), "deadline": "15:30",
            "minutes_to_deadline": DEADLINE - k.s["t"], "business_date": "2026-09-16",
            "chapter": self.chapter, "chapters": CHAPTERS, "state": snap, "agents": agents,
            "external": self.gw.snapshot(), "reference": dict(self.ref),
            "invariants": self.invariant_catalogue(), "domain": self.domain_checks(), "metrics": self.metrics(),
            "lineage": self.lineage(), "failures": self.failures, "failure_types": FAILURES,
            "external_modes": EXTERNAL_MODES, "roles": ROLES,
            "log_intact": k.verify_log(), "event_count": len(k.log),
        }
