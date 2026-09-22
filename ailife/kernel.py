"""AI Life Mother Model: deterministic kernel.

The kernel owns invariants and lifecycle semantics. Child models own domain policy.
Every command runs as an atomic transaction: it either commits all of its effects
plus the events describing them, or is rolled back and leaves only a
COMMAND_REJECTED event. State is plain dicts so it can be snapshotted and replayed.

Holding types and their death semantics
  ACCESS       (authority grant, persona slot)   -> revoked at death, slot returns
  RESERVATION  (resource earmarked for a case)   -> never released by death;
                                                   HELD_UNCERTAIN if an external
                                                   outcome is unresolved
  OBLIGATION   (something that must be resolved) -> passes to a successor under
                                                   death_policy SUCCEED
"""
from __future__ import annotations

import copy
import hashlib
import json

ALL = "*"

# Entity
ALIVE, DEAD = "ALIVE", "DEAD"
# Grant
G_ACTIVE, G_REVOKED, G_EXPIRED = "ACTIVE", "REVOKED", "EXPIRED"
# Reservation
R_ACTIVE, R_HELD, R_CONSUMED, R_RELEASED = "ACTIVE", "HELD_UNCERTAIN", "CONSUMED", "RELEASED"
# Obligation
O_OPEN, O_PROGRESS, O_WAITING = "OPEN", "IN_PROGRESS", "WAITING_EXTERNAL"
O_ASSIGNED, O_COMMITTED = "ASSIGNED", "COMMITTED"
O_RECOVERY, O_RESOLVED, O_CLOSED = "RECOVERY_REQUIRED", "RESOLVED", "CLOSED"
# Commitment
C_SENT, C_ACK, C_UNKNOWN, C_RECONCILED = "SENT", "ACKNOWLEDGED", "OUTCOME_UNKNOWN", "RECONCILED"
UNRESOLVED_COMMITMENT = {C_SENT, C_ACK, C_UNKNOWN}
EFFECT_CONFIRMED, EFFECT_REJECTED = "EFFECT_CONFIRMED", "EFFECT_REJECTED"

OBLIGATION_TRANSITIONS = {
    O_OPEN: {O_PROGRESS, O_RECOVERY},
    O_PROGRESS: {O_WAITING, O_RECOVERY, O_RESOLVED},
    O_WAITING: {O_PROGRESS, O_RECOVERY, O_RESOLVED},
    O_RECOVERY: {O_PROGRESS, O_WAITING, O_RESOLVED},
    O_RESOLVED: {O_CLOSED},
    O_CLOSED: set(),
}
UNRESOLVED_OBLIGATION = {O_OPEN, O_ASSIGNED, O_PROGRESS, O_COMMITTED, O_WAITING, O_RECOVERY}

# Stricter lifecycle used by the post-trade Authority Model (spec v0.2, section 11).
AUTHORITY_MODEL_TRANSITIONS = {
    O_OPEN: {O_ASSIGNED},
    O_ASSIGNED: {O_PROGRESS, O_RECOVERY},
    O_PROGRESS: {O_COMMITTED, O_RECOVERY},
    O_COMMITTED: {O_WAITING, O_RECOVERY},
    O_WAITING: {O_RESOLVED, O_RECOVERY},
    O_RECOVERY: {O_ASSIGNED, O_WAITING, O_RESOLVED},
    O_RESOLVED: {O_CLOSED},
    O_CLOSED: set(),
}

# Proposal / decision vocabulary (spec v0.2 sections 13-14)
P_PROPOSED, P_AUTHORIZED, P_ATTENUATED, P_ESCALATED = "PROPOSED", "AUTHORIZED", "ATTENUATED", "ESCALATED"
P_REJECTED, P_EXPIRED, P_EXECUTED, P_CANCELLED = "REJECTED", "EXPIRED", "EXECUTED", "CANCELLED"
LIVE_PROPOSAL = {P_PROPOSED, P_AUTHORIZED, P_ATTENUATED, P_ESCALATED}
DECISIONS = {"AUTHORIZE": P_AUTHORIZED, "ATTENUATE": P_ATTENUATED, "ESCALATE": P_ESCALATED,
             "REJECT": P_REJECTED, "DEFER": P_PROPOSED}

SUCCEED, RELEASE_ALL = "SUCCEED", "RELEASE_ALL"
STRICT, AUDIT = "STRICT", "AUDIT"


class Rejected(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _permits(allowed, action):
    return ALL in allowed or action in allowed


def _subset(child, parent):
    return ALL in parent or (ALL not in child and set(child) <= set(parent))


class Kernel:
    def __init__(self, root_id="I", death_policy=SUCCEED, enforce=STRICT,
                 authoritative_kinds=("EXTERNAL",), gate_kinds=("GATE",), obligation_transitions=None,
                 duplicate_guard=True, require_decision=False, root_kind="ROOT"):
        self.config = dict(root_id=root_id, death_policy=death_policy, enforce=enforce,
                           authoritative_kinds=tuple(authoritative_kinds), gate_kinds=tuple(gate_kinds),
                           obligation_transitions=obligation_transitions, duplicate_guard=duplicate_guard,
                           require_decision=require_decision, root_kind=root_kind)
        self.death_policy, self.enforce = death_policy, enforce
        self.authoritative_kinds = set(authoritative_kinds)
        self.gate_kinds = set(gate_kinds)
        self.T = obligation_transitions or OBLIGATION_TRANSITIONS
        self.duplicate_guard, self.require_decision = duplicate_guard, require_decision
        self.root = root_id
        self.journal = []
        self.s = {"t": 0, "entities": {}, "grants": {}, "pools": {}, "reservations": {},
                  "obligations": {}, "commitments": {}, "evidence": {}, "executions": {},
                  "proposals": {}, "decisions": {}, "recoveries": {}, "memory": {}}
        self.log = []
        self._pending = []
        self._known_violations = set()
        self.s["entities"][root_id] = self._entity(root_id, "ROOT", None, mortal=False)
        self.s["entities"][root_id]["label"] = root_kind
        self.s["grants"]["G-ROOT"] = {
            "id": "G-ROOT", "holder": root_id, "parent": None, "actions": [ALL],
            "grant_actions": [ALL], "scope": {}, "max_qty": None, "valid_until": None,
            "status": G_ACTIVE, "slot_pool": None, "transferable": False, "reason": None}
        self._append("KERNEL", "ROOT_CREATED", root_id, {"death_policy": death_policy, "enforce": enforce})

    # ------------------------------------------------------------------ plumbing
    def _entity(self, eid, kind, parent, mortal=True, capabilities=None):
        lineage = None
        if parent is not None:
            p = self.s["entities"][parent]
            lineage = eid if p["kind"] == "ROOT" else p["lineage"]
        return {"id": eid, "kind": kind, "parent": parent, "lineage": lineage, "status": ALIVE, "mortal": mortal,
                "born": self.s["t"], "died": None, "capabilities": list(capabilities) if capabilities is not None else None}

    def _append(self, actor, etype, subject, payload):
        prev = self.log[-1]["hash"] if self.log else "GENESIS"
        body = {"seq": len(self.log) + 1, "t": self.s["t"], "type": etype, "actor": actor,
                "subject": subject, "payload": payload, "prev": prev}
        body["hash"] = hashlib.sha256((prev + json.dumps(body, sort_keys=True)).encode()).hexdigest()[:16]
        self.log.append(body)

    def emit(self, actor, etype, subject, payload=None):
        self._pending.append((actor, etype, subject, payload or {}))

    def _step(self, o, target, reason=None):
        """Automatic lifecycle move, applied only if the configured state machine allows it."""
        if target in self.T.get(o["status"], set()):
            prev, o["status"] = o["status"], target
            self.emit("KERNEL", "OBLIGATION_STATUS", o["id"], {"from": prev, "to": target, "reason": reason})
            if target == O_RESOLVED:
                self._complete_recoveries(o["id"])
            return True
        return False

    def _complete_recoveries(self, oid):
        for r in self.s["recoveries"].values():
            if r["obligation"] == oid and r["status"] in ("OPEN", "CLAIMED"):
                r["status"] = "COMPLETE"
                self.emit("KERNEL", "RECOVERY_COMPLETED", r["id"], {"obligation": oid})

    def _open_recovery(self, o, owner, trigger, of):
        rid = f"REC-{len(self.s['recoveries']) + 1:03d}"
        self.s["recoveries"][rid] = {"id": rid, "obligation": o["id"], "owner": owner, "trigger": trigger, "of": of,
                                     "status": "OPEN", "at": self.s["t"], "worker": None,
                                     "required_actions": (["DETERMINE_EXTERNAL_STATE"] if self.unresolved_commitments(o["id"]) else [])
                                     + ["RESTORE_PROCESSING_OWNERSHIP"]}
        o["recovery"] = rid
        self.emit("KERNEL", "RECOVERY_CREATED", rid, {"obligation": o["id"], "owner": owner, "trigger": trigger, "of": of})
        return rid

    def _journal(self, method, *args, **kwargs):
        self.journal.append({"method": method, "args": copy.deepcopy(list(args)), "kwargs": copy.deepcopy(kwargs)})

    @classmethod
    def replay(cls, config, journal):
        """Rebuild state by re-executing the command journal. Determinism means the rebuilt
        event log must reproduce the original hash chain exactly."""
        k = cls(**config)
        for j in journal:
            getattr(k, j["method"])(*j["args"], **j["kwargs"])
        return k

    def tx(self, actor, name, fn):
        """Run fn atomically. Returns (ok, result_or_rejection_code)."""
        snapshot = copy.deepcopy(self.s)
        self._pending = []
        try:
            result = fn()
            violations = self.check_invariants()
            if violations and self.enforce == STRICT:
                raise Rejected("INVARIANT_WOULD_BREAK", "; ".join(f"{c}: {m}" for c, m in violations))
        except Rejected as e:
            self.s = snapshot
            self._pending = []
            self._append(actor, "COMMAND_REJECTED", name, {"code": e.code, "detail": e.detail})
            return False, e.code
        for ev in self._pending:
            self._append(*ev)
        self._pending = []
        current = set(violations)
        for code, msg in violations:
            if (code, msg) not in self._known_violations:
                self._append("KERNEL", "INVARIANT_VIOLATED", code, {"detail": msg})
        self._known_violations = current
        return True, result

    # ------------------------------------------------------------------ queries
    def alive(self, eid):
        e = self.s["entities"].get(eid)
        return bool(e and e["status"] == ALIVE)

    def _require_alive(self, eid):
        if not self.alive(eid):
            raise Rejected("ACTOR_NOT_ALIVE", eid)

    def authorize(self, actor, action, scope=None, qty=None):
        """Return the grant that permits action, or raise. Memory never grants authority:
        only an ACTIVE, unexpired grant held by a living entity does."""
        self._require_alive(actor)
        caps = self.s["entities"][actor]["capabilities"]
        if caps is not None and action not in caps:
            raise Rejected("NO_CAPABILITY", f"{actor} is not capable of {action}; capability is not permission, but both are required")
        scope = scope or {}
        reasons = []
        for g in self.s["grants"].values():
            if g["holder"] != actor:
                continue
            if g["status"] != G_ACTIVE:
                if _permits(g["actions"], action):
                    reasons.append(f"{g['id']} {g['status'].lower()}")
                continue
            if g["valid_until"] is not None and self.s["t"] > g["valid_until"]:
                reasons.append(f"{g['id']} expired"); continue
            if not _permits(g["actions"], action):
                continue
            if any(scope.get(k) not in v for k, v in g["scope"].items()):
                reasons.append(f"{g['id']} out of scope"); continue
            if g["max_qty"] is not None and qty is not None and qty > g["max_qty"]:
                reasons.append(f"{g['id']} limit {g['max_qty']} < {qty}"); continue
            return g
        raise Rejected("NO_AUTHORITY", f"{actor} may not {action}" + (f" ({'; '.join(reasons)})" if reasons else ""))

    def successor(self, eid):
        p = self.s["entities"][eid]["parent"]
        while p is not None:
            if self.alive(p):
                return p
            p = self.s["entities"][p]["parent"]
        return None

    def unresolved_commitments(self, obligation_id):
        return [c for c in self.s["commitments"].values()
                if c["obligation"] == obligation_id and c["status"] in UNRESOLVED_COMMITMENT]

    def uncertain_reservation(self, rid):
        """A reservation is uncertain if any unresolved commitment is on its obligation or
        declares that it affects this reservation (cross-obligation legs, e.g. DvP claims)."""
        r = self.s["reservations"][rid]
        return [c for c in self.s["commitments"].values() if c["status"] in UNRESOLVED_COMMITMENT
                and (c["obligation"] == r["obligation"] or rid in c["affects"])]

    def pool_usage(self, pool_id):
        pool = self.s["pools"][pool_id]
        if pool["kind"] == "SLOTS":
            used = sum(1 for g in self.s["grants"].values() if g["slot_pool"] == pool_id and g["status"] == G_ACTIVE)
            return {"total": pool["total"], "used": used, "available": pool["total"] - used}
        res = [r for r in self.s["reservations"].values() if r["pool"] == pool_id]
        reserved = sum(r["qty"] for r in res if r["status"] == R_ACTIVE)
        held = sum(r["qty"] for r in res if r["status"] == R_HELD)
        consumed = sum(r["qty"] for r in res if r["status"] == R_CONSUMED)
        return {"total": pool["total"], "reserved": reserved, "held_uncertain": held,
                "consumed": consumed, "available": pool["total"] - reserved - held - consumed}

    # ------------------------------------------------------------------ entities
    def spawn(self, actor, eid, kind="AGENT", mortal=True, scope=None, capabilities=None, label=None):
        self._journal("spawn", actor, eid, kind, mortal, scope, capabilities, label)
        def fn():
            self.authorize(actor, "SPAWN", scope)
            if eid in self.s["entities"]:
                raise Rejected("ID_EXISTS", eid)
            self.s["entities"][eid] = self._entity(eid, kind, actor, mortal, capabilities)
            self.s["entities"][eid]["label"] = label
            self.emit(actor, "ENTITY_SPAWNED", eid, {"kind": kind, "parent": actor})
            return eid
        return self.tx(actor, "SPAWN", fn)

    def kill(self, actor, target, cause):
        """Death is an atomic succession transaction, never a silent release."""
        self._journal("kill", actor, target, cause)
        def fn():
            e = self.s["entities"].get(target)
            if not e or e["status"] != ALIVE:
                raise Rejected("NOT_ALIVE", target)
            if not e["mortal"]:
                raise Rejected("IMMORTAL", f"{target} is the recovery owner of last resort")
            self.emit(actor, "DEATH_REQUESTED", target, {"cause": cause})
            succ = self.successor(target)
            if self.death_policy == SUCCEED and succ is None:
                raise Rejected("NO_SUCCESSOR", target)
            e["status"], e["died"] = DEAD, self.s["t"]
            self.emit("KERNEL", "ENTITY_DIED", target, {"cause": cause, "policy": self.death_policy})
            for g in list(self.s["grants"].values()):
                if g["holder"] == target and g["status"] == G_ACTIVE:
                    self._revoke_cascade(g["id"], "HOLDER_DIED")
            for o in self.s["obligations"].values():
                if o["status"] not in UNRESOLVED_OBLIGATION:
                    continue
                worker_died = target in o["assignees"]
                if worker_died:
                    o["assignees"].remove(target)
                if o["owner"] != target and not worker_died:
                    continue
                if self.death_policy == SUCCEED:
                    if o["owner"] == target:
                        o["owner"] = succ
                        self.emit("KERNEL", "OBLIGATION_SUCCEEDED", o["id"], {"from": target, "to": succ})
                    self._step(o, O_RECOVERY, "AGENT_FAILURE")
                    self._open_recovery(o, o["owner"], "AGENT_FAILURE", target)
                    for r in self.s["reservations"].values():
                        if r["obligation"] == o["id"] and r["status"] == R_ACTIVE and self.uncertain_reservation(r["id"]):
                            r["status"] = R_HELD
                            self.emit("KERNEL", "RESERVATION_HELD_UNCERTAIN", r["id"], {"obligation": o["id"]})
                elif o["owner"] != target:
                    self.emit("KERNEL", "WORKER_LOST", o["id"], {"worker": target, "note": "agent-local task state lost"})
                else:  # RELEASE_ALL: original persona semantics / agent-owned-state baseline
                    o["owner"] = None
                    self.emit("KERNEL", "OBLIGATION_ABANDONED", o["id"], {"from": target})
                    for r in self.s["reservations"].values():
                        if r["obligation"] == o["id"] and r["status"] in (R_ACTIVE, R_HELD):
                            r["status"] = R_RELEASED
                            self.emit("KERNEL", "RESERVATION_RELEASED", r["id"], {"reason": "HOLDER_DIED"})
            return succ
        return self.tx(actor, "KILL", fn)

    # ------------------------------------------------------------------ authority / access
    def delegate(self, actor, gid, from_grant, to, actions, grant_actions=(), scope=None,
                 max_qty=None, valid_until=None, slot_pool=None, transferable=False):
        """IAcquire generalised. Distinguishes may-do (actions) from may-grant (grant_actions)."""
        self._journal("delegate", actor, gid, from_grant, to, list(actions), list(grant_actions), scope, max_qty, valid_until, slot_pool, transferable)
        def fn():
            self._require_alive(actor)
            p = self.s["grants"].get(from_grant)
            if not p or p["holder"] != actor or p["status"] != G_ACTIVE:
                raise Rejected("BAD_PARENT_GRANT", from_grant)
            if p["valid_until"] is not None and self.s["t"] > p["valid_until"]:
                raise Rejected("PARENT_EXPIRED", from_grant)
            if not self.alive(to):
                raise Rejected("GRANTEE_NOT_ALIVE", to)
            if gid in self.s["grants"]:
                raise Rejected("ID_EXISTS", gid)
            if not _subset(actions, p["grant_actions"]):
                raise Rejected("ESCALATION", f"{from_grant} may not grant {sorted(set(actions) - set(p['grant_actions']))}")
            if not _subset(grant_actions, p["grant_actions"]):
                raise Rejected("ESCALATION", "grant rights exceed parent")
            sc = dict(p["scope"]) if scope is None else scope
            for k, v in p["scope"].items():
                if k not in sc or not set(sc[k]) <= set(v):
                    raise Rejected("SCOPE_EXCEEDS_PARENT", k)
            if p["max_qty"] is not None and (max_qty is None or max_qty > p["max_qty"]):
                raise Rejected("LIMIT_EXCEEDS_PARENT", str(max_qty))
            if p["valid_until"] is not None and (valid_until is None or valid_until > p["valid_until"]):
                raise Rejected("EXPIRY_EXCEEDS_PARENT", str(valid_until))
            if slot_pool is not None:
                pool = self.s["pools"].get(slot_pool)
                if not pool or pool["kind"] != "SLOTS":
                    raise Rejected("NO_SUCH_SLOT_POOL", slot_pool)
                if any(g["holder"] == to and g["slot_pool"] == slot_pool and g["status"] == G_ACTIVE
                       for g in self.s["grants"].values()):
                    raise Rejected("ALREADY_HOLDS_SLOT", slot_pool)
                if self.pool_usage(slot_pool)["available"] <= 0:
                    raise Rejected("CAPACITY_FULL", slot_pool)
            self.s["grants"][gid] = {
                "id": gid, "holder": to, "parent": from_grant, "actions": list(actions),
                "grant_actions": list(grant_actions), "scope": {k: list(v) for k, v in sc.items()},
                "max_qty": max_qty, "valid_until": valid_until, "status": G_ACTIVE,
                "slot_pool": slot_pool, "transferable": transferable, "reason": None}
            self.emit(actor, "AUTHORITY_GRANTED", gid, {"to": to, "actions": list(actions),
                      "grant_actions": list(grant_actions), "slot_pool": slot_pool})
            return gid
        return self.tx(actor, "DELEGATE", fn)

    def _revoke_cascade(self, gid, reason):
        g = self.s["grants"][gid]
        if g["status"] != G_ACTIVE:
            return
        g["status"], g["reason"] = G_REVOKED, reason
        self.emit("KERNEL", "AUTHORITY_REVOKED", gid, {"holder": g["holder"], "reason": reason})
        for child in self.s["grants"].values():
            if child["parent"] == gid:
                self._revoke_cascade(child["id"], f"PARENT_{reason}")

    def revoke(self, actor, gid, reason="REVOKED"):
        """IRelease(alpha). The holder may release its own grant; any ancestor holder may revoke."""
        self._journal("revoke", actor, gid, reason)
        def fn():
            self._require_alive(actor)
            g = self.s["grants"].get(gid)
            if not g or g["status"] != G_ACTIVE:
                raise Rejected("NOT_ACTIVE", gid)
            chain, p = [], g["id"]
            while p is not None:
                chain.append(self.s["grants"][p]["holder"])
                p = self.s["grants"][p]["parent"]
            if actor not in chain:
                raise Rejected("NOT_IN_AUTHORITY_CHAIN", actor)
            self._revoke_cascade(gid, reason)
        return self.tx(actor, "REVOKE", fn)

    def transfer_grant(self, actor, gid, to):
        """Inheritance by transfer: the holding moves, capacity is unchanged."""
        self._journal("transfer_grant", actor, gid, to)
        def fn():
            self._require_alive(actor)
            g = self.s["grants"].get(gid)
            if not g or g["holder"] != actor or g["status"] != G_ACTIVE:
                raise Rejected("NOT_HOLDER", gid)
            if not g["transferable"]:
                raise Rejected("NOT_TRANSFERABLE", gid)
            if not self.alive(to):
                raise Rejected("GRANTEE_NOT_ALIVE", to)
            if g["slot_pool"] and any(x["holder"] == to and x["slot_pool"] == g["slot_pool"] and x["status"] == G_ACTIVE
                                      for x in self.s["grants"].values()):
                raise Rejected("ALREADY_HOLDS_SLOT", g["slot_pool"])
            g["holder"] = to
            self.emit(actor, "AUTHORITY_TRANSFERRED", gid, {"to": to})
        return self.tx(actor, "TRANSFER_GRANT", fn)

    def advance(self, ticks=1):
        self._journal("advance", ticks)
        def fn():
            self.s["t"] += ticks
            for g in self.s["grants"].values():
                if g["status"] == G_ACTIVE and g["valid_until"] is not None and self.s["t"] > g["valid_until"]:
                    g["status"], g["reason"] = G_EXPIRED, "VALIDITY_ENDED"
                    self.emit("KERNEL", "AUTHORITY_EXPIRED", g["id"], {"holder": g["holder"]})
            self.emit("KERNEL", "CLOCK_ADVANCED", "clock", {"t": self.s["t"]})
        return self.tx("KERNEL", "ADVANCE", fn)

    # ------------------------------------------------------------------ resources
    def create_pool(self, actor, pid, kind, total, label=""):
        self._journal("create_pool", actor, pid, kind, total, label)
        def fn():
            self.authorize(actor, "CREATE_POOL")
            if kind not in ("SLOTS", "QUANTITY") or total < 0:
                raise Rejected("BAD_POOL", pid)
            self.s["pools"][pid] = {"id": pid, "kind": kind, "total": total, "label": label}
            self.emit(actor, "POOL_CREATED", pid, {"kind": kind, "total": total})
        return self.tx(actor, "CREATE_POOL", fn)

    def reserve(self, actor, rid, pool, obligation, qty, scope=None):
        self._journal("reserve", actor, rid, pool, obligation, qty, scope)
        def fn():
            self.authorize(actor, "RESERVE", scope, qty)
            o = self.s["obligations"].get(obligation)
            if not o or o["status"] not in UNRESOLVED_OBLIGATION:
                raise Rejected("OBLIGATION_NOT_OPEN", obligation)
            if self.pool_usage(pool)["available"] < qty:
                raise Rejected("INSUFFICIENT_AVAILABLE", pool)
            self.s["reservations"][rid] = {"id": rid, "pool": pool, "obligation": obligation, "qty": qty, "status": R_ACTIVE}
            self.emit(actor, "RESOURCE_RESERVED", rid, {"pool": pool, "obligation": obligation, "qty": qty})
        return self.tx(actor, "RESERVE", fn)

    def release_reservation(self, actor, rid, evidence_id=None, scope=None):
        self._journal("release_reservation", actor, rid, evidence_id, scope)
        def fn():
            self.authorize(actor, "RELEASE", scope)
            r = self.s["reservations"].get(rid)
            if not r or r["status"] not in (R_ACTIVE, R_HELD):
                raise Rejected("NOT_RELEASABLE", rid)
            pending = self.uncertain_reservation(rid)
            if pending:
                raise Rejected("UNCERTAIN_EXTERNAL_STATE", f"{rid} backs unresolved {[c['id'] for c in pending]}")
            r["status"] = R_RELEASED
            self.emit(actor, "RESOURCE_RELEASED", rid, {"evidence": evidence_id})
        return self.tx(actor, "RELEASE", fn)

    def consume_reservation(self, actor, rid, evidence_id, scope=None):
        self._journal("consume_reservation", actor, rid, evidence_id, scope)
        def fn():
            self.authorize(actor, "RECONCILE", scope)
            r = self.s["reservations"].get(rid)
            if not r or r["status"] not in (R_ACTIVE, R_HELD):
                raise Rejected("NOT_CONSUMABLE", rid)
            ev = self.s["evidence"].get(evidence_id)
            if not ev or not ev["verified"]:
                raise Rejected("EVIDENCE_NOT_AUTHORITATIVE", str(evidence_id))
            r["status"] = R_CONSUMED
            self.emit(actor, "RESOURCE_CONSUMED", rid, {"evidence": evidence_id})
        return self.tx(actor, "CONSUME", fn)

    # ------------------------------------------------------------------ obligations
    def open_obligation(self, actor, oid, otype, parent=None, deadline=None, attrs=None, scope=None):
        self._journal("open_obligation", actor, oid, otype, parent, deadline, attrs, scope)
        def fn():
            self.authorize(actor, "OPEN_OBLIGATION", scope)
            if oid in self.s["obligations"]:
                raise Rejected("ID_EXISTS", oid)
            self.s["obligations"][oid] = {"id": oid, "type": otype, "owner": actor, "parent": parent,
                                          "status": O_OPEN, "deadline": deadline, "attrs": attrs or {},
                                          "recovery": None, "outcome": None, "rev": 0, "assignees": []}
            self.emit(actor, "OBLIGATION_OPENED", oid, {"type": otype, "parent": parent, "deadline": deadline})
        return self.tx(actor, "OPEN_OBLIGATION", fn)

    def assign_obligation(self, actor, oid, to):
        self._journal("assign_obligation", actor, oid, to)
        def fn():
            self._require_alive(actor)
            o = self.s["obligations"].get(oid)
            if not o or o["owner"] != actor:
                raise Rejected("NOT_OWNER", oid)
            if o["status"] not in UNRESOLVED_OBLIGATION:
                raise Rejected("OBLIGATION_NOT_OPEN", oid)
            if not self.alive(to):
                raise Rejected("ASSIGNEE_NOT_ALIVE", to)
            o["owner"] = to
            self.emit(actor, "OBLIGATION_ASSIGNED", oid, {"to": to})
            self._step(o, O_ASSIGNED, "OWNER_ASSIGNED")
        return self.tx(actor, "ASSIGN_OBLIGATION", fn)

    def claim_recovery(self, actor, oid, to=None, as_worker=False):
        """The recovery owner restores processing: either hands ownership to `to`, or (as_worker)
        keeps ownership and assigns `to` as the replacement worker."""
        self._journal("claim_recovery", actor, oid, to, as_worker)
        def fn():
            self._require_alive(actor)
            o = self.s["obligations"].get(oid)
            if not o or o["owner"] != actor or o["status"] != O_RECOVERY:
                raise Rejected("NOT_RECOVERY_OWNER", oid)
            target = to or actor
            if not self.alive(target):
                raise Rejected("ASSIGNEE_NOT_ALIVE", target)
            if as_worker:
                if target not in o["assignees"]:
                    o["assignees"].append(target)
            else:
                o["owner"] = target
            if self.unresolved_commitments(oid):
                self._step(o, O_WAITING, "RECOVERY_CLAIMED")
            elif not self._step(o, O_ASSIGNED, "RECOVERY_CLAIMED"):
                self._step(o, O_PROGRESS, "RECOVERY_CLAIMED")
            rec = self.s["recoveries"].get(o["recovery"]) if o["recovery"] else None
            if rec and rec["status"] == "OPEN":
                rec["status"], rec["worker"] = "CLAIMED", target
            self.emit(actor, "RECOVERY_CLAIMED", oid, {"worker": target, "status": o["status"], "recovery": o["recovery"]})
        return self.tx(actor, "CLAIM_RECOVERY", fn)

    def assign_worker(self, actor, oid, worker):
        self._journal("assign_worker", actor, oid, worker)
        def fn():
            self._require_alive(actor)
            o = self.s["obligations"].get(oid)
            if not o or o["owner"] != actor:
                raise Rejected("NOT_OWNER", oid)
            if o["status"] not in UNRESOLVED_OBLIGATION:
                raise Rejected("OBLIGATION_NOT_OPEN", oid)
            if not self.alive(worker):
                raise Rejected("ASSIGNEE_NOT_ALIVE", worker)
            if worker not in o["assignees"]:
                o["assignees"].append(worker)
            self.emit(actor, "WORKER_ASSIGNED", oid, {"worker": worker})
            if o["status"] == O_ASSIGNED:
                self._step(o, O_PROGRESS, "WORKER_ASSIGNED")
        return self.tx(actor, "ASSIGN_WORKER", fn)

    def detach_owner(self, actor, oid):
        """Failure injection: simulate a defect that drops an obligation's owner.
        The invariant engine is the last line of defence and must refuse it."""
        self._journal("detach_owner", actor, oid)
        def fn():
            o = self.s["obligations"].get(oid)
            if not o:
                raise Rejected("NO_SUCH_OBLIGATION", oid)
            o["owner"] = None
            self.emit(actor, "OWNER_DETACHED", oid, {})
        return self.tx(actor, "DETACH_OWNER", fn)

    def note(self, actor, etype, subject, payload=None):
        """Record an operator or environment event (e.g. FAILURE_INJECTION_REQUESTED)."""
        self._journal("note", actor, etype, subject, payload)
        def fn():
            self.emit(actor, etype, subject, payload or {})
        return self.tx(actor, etype, fn)

    def remember(self, agent, mid, content, evidence_refs=()):
        """Agent memory. Stored and shown, never consulted by authorize(): memory is not authority."""
        self._journal("remember", agent, mid, content, list(evidence_refs))
        def fn():
            self._require_alive(agent)
            self.s["memory"][mid] = {"id": mid, "agent": agent, "content": content,
                                     "evidence": list(evidence_refs), "at": self.s["t"]}
            self.emit(agent, "MEMORY_RECORDED", mid, {"content": content})
        return self.tx(agent, "REMEMBER", fn)

    # ------------------------------------------------------------------ proposals & gate decisions
    def propose(self, actor, pid, obligation, required_action, change, qty=None, scope=None,
                requires_approval=False, kind="REPAIR"):
        self._journal("propose", actor, pid, obligation, required_action, change, qty, scope, requires_approval, kind)
        def fn():
            g = self.authorize(actor, required_action, scope, qty)
            o = self.s["obligations"].get(obligation)
            if not o or o["status"] not in UNRESOLVED_OBLIGATION:
                raise Rejected("OBLIGATION_NOT_OPEN", obligation)
            if pid in self.s["proposals"]:
                raise Rejected("ID_EXISTS", pid)
            self.s["proposals"][pid] = {"id": pid, "obligation": obligation, "by": actor, "authority": g["id"],
                                        "kind": kind, "change": change, "qty": qty, "status": P_PROPOSED,
                                        "requires_approval": requires_approval, "approvals": [],
                                        "basis_rev": o["rev"], "decision": None, "at": self.s["t"], "reason": None}
            self.emit(actor, "ACTION_PROPOSED", pid, {"obligation": obligation, "change": change, "qty": qty,
                                                       "authority": g["id"]})
        return self.tx(actor, "PROPOSE", fn)

    def approve(self, actor, pid, scope=None, action="APPROVE_REPAIR"):
        self._journal("approve", actor, pid, scope, action)
        def fn():
            self.authorize(actor, action, scope)
            p = self.s["proposals"].get(pid)
            if not p or p["status"] not in LIVE_PROPOSAL:
                raise Rejected("PROPOSAL_NOT_LIVE", pid)
            if actor == p["by"]:
                raise Rejected("FOUR_EYE_VIOLATION", "proposer cannot approve its own proposal")
            p["approvals"].append({"by": actor, "at": self.s["t"]})
            self.emit(actor, "FOUR_EYE_APPROVED", pid, {"proposer": p["by"]})
        return self.tx(actor, "APPROVE", fn)

    def record_decision(self, gate, did, pid, decision, reason, checks, valid_for=5, conditions=None):
        """Persist a decision returned by the external gate (Gate Symphony). Only a GATE entity may decide."""
        self._journal("record_decision", gate, did, pid, decision, reason, checks, valid_for, conditions)
        def fn():
            self._require_alive(gate)
            if self.s["entities"][gate]["kind"] not in self.gate_kinds:
                raise Rejected("NOT_A_GATE", gate)
            p = self.s["proposals"].get(pid)
            if not p or p["status"] not in LIVE_PROPOSAL:
                raise Rejected("PROPOSAL_NOT_LIVE", pid)
            if decision not in DECISIONS:
                raise Rejected("BAD_DECISION", decision)
            o = self.s["obligations"][p["obligation"]]
            self.s["decisions"][did] = {"id": did, "proposal": pid, "decision": decision, "reason": reason,
                                        "checks": checks, "conditions": conditions or {}, "at": self.s["t"],
                                        "valid_until": self.s["t"] + valid_for, "basis_rev": o["rev"]}
            p["status"], p["decision"] = DECISIONS[decision], did
            self.emit(gate, f"PROPOSAL_{p['status']}", pid, {"decision": did, "reason": reason})
        return self.tx(gate, "RECORD_DECISION", fn)

    def cancel_proposal(self, actor, pid, reason):
        self._journal("cancel_proposal", actor, pid, reason)
        def fn():
            self._require_alive(actor)
            p = self.s["proposals"].get(pid)
            if not p or p["status"] not in LIVE_PROPOSAL:
                raise Rejected("PROPOSAL_NOT_LIVE", pid)
            o = self.s["obligations"][p["obligation"]]
            if actor not in (p["by"], o["owner"]):
                raise Rejected("NOT_PROPOSER_OR_OWNER", actor)
            p["status"], p["reason"] = P_CANCELLED, reason
            self.emit(actor, "PROPOSAL_CANCELLED", pid, {"reason": reason})
        return self.tx(actor, "CANCEL_PROPOSAL", fn)

    def bump_revision(self, actor, oid, reason, detail=None, scope=None, action="UPDATE_REFERENCE"):
        """Case-relevant reference data changed. Every live proposal becomes stale."""
        self._journal("bump_revision", actor, oid, reason, detail, scope, action)
        def fn():
            self.authorize(actor, action, scope)
            o = self.s["obligations"].get(oid)
            if not o or o["status"] not in UNRESOLVED_OBLIGATION:
                raise Rejected("OBLIGATION_NOT_OPEN", oid)
            o["rev"] += 1
            self.emit(actor, "REFERENCE_DATA_CHANGED", oid, {"reason": reason, "rev": o["rev"], "detail": detail or {}})
            for p in self.s["proposals"].values():
                if p["obligation"] == oid and p["status"] in LIVE_PROPOSAL:
                    p["status"], p["reason"] = P_EXPIRED, "STALE_AFTER_REFERENCE_CHANGE"
                    self.emit("KERNEL", "PROPOSAL_EXPIRED", p["id"], {"reason": "STALE_AFTER_REFERENCE_CHANGE"})
        return self.tx(actor, "BUMP_REVISION", fn)

    def set_obligation_status(self, actor, oid, status, outcome=None):
        self._journal("set_obligation_status", actor, oid, status, outcome)
        def fn():
            self._require_alive(actor)
            o = self.s["obligations"].get(oid)
            if not o:
                raise Rejected("NO_SUCH_OBLIGATION", oid)
            if o["owner"] != actor:
                raise Rejected("NOT_OWNER", f"{oid} owned by {o['owner']}")
            if status not in self.T[o["status"]]:
                raise Rejected("ILLEGAL_TRANSITION", f"{o['status']} -> {status}")
            if status == O_RESOLVED and self.unresolved_commitments(oid):
                raise Rejected("UNRESOLVED_COMMITMENTS", oid)
            if status == O_CLOSED:
                if any(r["obligation"] == oid and r["status"] in (R_ACTIVE, R_HELD) for r in self.s["reservations"].values()):
                    raise Rejected("RESERVATIONS_OUTSTANDING", oid)
                if any(c["parent"] == oid and c["status"] != O_CLOSED for c in self.s["obligations"].values()):
                    raise Rejected("CHILD_OBLIGATIONS_OPEN", oid)
            prev, o["status"] = o["status"], status
            if outcome is not None:
                o["outcome"] = outcome
            self.emit(actor, "OBLIGATION_STATUS", oid, {"from": prev, "to": status, "outcome": outcome})
            if status == O_RESOLVED:
                self._complete_recoveries(oid)
        return self.tx(actor, "SET_OBLIGATION_STATUS", fn)

    # ------------------------------------------------------------------ commitments & evidence
    def submit(self, actor, cid, obligation, intent, system, action, qty=None, scope=None,
               idempotent=False, ext_ref=None, affects=(), proposal=None, decision=None):
        """Record that an instruction is about to cross the external boundary.
        Duplicate business intent is blocked while an earlier attempt is unresolved,
        unless both attempts are declared idempotent by the external protocol."""
        self._journal("submit", actor, cid, obligation, intent, system, action, qty, scope, idempotent, ext_ref,
                      list(affects), proposal, decision)
        def fn():
            g = self.authorize(actor, action, scope, qty)
            o = self.s["obligations"].get(obligation)
            if not o or o["status"] not in UNRESOLVED_OBLIGATION:
                raise Rejected("OBLIGATION_NOT_OPEN", obligation)
            for c in self.s["commitments"].values():
                if not self.duplicate_guard:
                    break
                if c["intent"] != intent:
                    continue
                if c["status"] in UNRESOLVED_COMMITMENT and not (idempotent and c["idempotent"]):
                    raise Rejected("DUPLICATE_BLOCKED", f"{c['id']} for {intent} is {c['status']}")
                if c["status"] == C_RECONCILED and c["outcome"] == EFFECT_CONFIRMED:
                    raise Rejected("ALREADY_EFFECTED", f"{c['id']} already confirmed {intent}")
            if self.require_decision or decision is not None:
                d = self.s["decisions"].get(decision)
                p = self.s["proposals"].get(proposal)
                if not d or not p or d["proposal"] != proposal:
                    raise Rejected("NO_AUTHORIZATION", "an external instruction must reference an authorized proposal")
                if p["obligation"] != obligation:
                    raise Rejected("AUTHORIZATION_MISMATCH", f"{proposal} is for {p['obligation']}")
                if d["basis_rev"] != o["rev"] or p["basis_rev"] != o["rev"]:
                    raise Rejected("STALE_AUTHORIZATION", f"case changed since {decision} (rev {d['basis_rev']} vs {o['rev']})")
                if d["decision"] != "AUTHORIZE" or p["status"] != P_AUTHORIZED:
                    raise Rejected("NOT_AUTHORIZED", f"{proposal} is {p['status']}")
                if self.s["t"] > d["valid_until"]:
                    raise Rejected("AUTHORIZATION_EXPIRED", f"{decision} valid until t={d['valid_until']}")
                if p["requires_approval"] and not p["approvals"]:
                    raise Rejected("FOUR_EYE_MISSING", proposal)
            if cid in self.s["commitments"]:
                raise Rejected("ID_EXISTS", cid)
            self.s["commitments"][cid] = {"id": cid, "obligation": obligation, "intent": intent, "system": system,
                                          "action": action, "qty": qty, "by": actor, "authority": g["id"],
                                          "idempotent": idempotent, "affects": list(affects), "status": C_SENT, "outcome": None,
                                          "evidence": [], "ext_ref": ext_ref, "sent_at": self.s["t"],
                                          "proposal": proposal, "decision": decision}
            if proposal:
                self.s["proposals"][proposal]["status"] = P_EXECUTED
                self.emit("KERNEL", "PROPOSAL_EXECUTED", proposal, {"commitment": cid})
            self.emit(actor, "COMMITMENT_SENT", cid, {"intent": intent, "system": system, "authority": g["id"],
                                                      "qty": qty, "decision": decision})
            if o["status"] in (O_OPEN, O_RECOVERY, O_ASSIGNED):
                self._step(o, O_PROGRESS, "INSTRUCTION_SUBMITTED")
            if not self._step(o, O_COMMITTED, "INSTRUCTION_SUBMITTED"):
                self._step(o, O_WAITING, "INSTRUCTION_SUBMITTED")
            return cid
        return self.tx(actor, "SUBMIT", fn)

    def mark_unknown(self, actor, cid, reason, scope=None):
        self._journal("mark_unknown", actor, cid, reason, scope)
        def fn():
            self.authorize(actor, "MONITOR", scope)
            c = self.s["commitments"].get(cid)
            if not c or c["status"] not in (C_SENT, C_ACK):
                raise Rejected("NOT_PENDING", cid)
            c["status"] = C_UNKNOWN
            self.emit(actor, "EXTERNAL_OUTCOME_UNKNOWN", cid, {"reason": reason})
            o = self.s["obligations"][c["obligation"]]
            if o["status"] == O_COMMITTED:
                self._step(o, O_WAITING, "OUTCOME_UNKNOWN")
        return self.tx(actor, "MARK_UNKNOWN", fn)

    def capture_evidence(self, source, eid, supports, claim, detail=None):
        """Anyone may record a claim. Only authoritative sources produce verified evidence;
        an agent's own statement is kept as an observation and cannot settle anything."""
        self._journal("capture_evidence", source, eid, supports, claim, detail)
        def fn():
            self._require_alive(source)
            kind = self.s["entities"][source]["kind"]
            verified = kind in self.authoritative_kinds
            self.s["evidence"][eid] = {"id": eid, "source": source, "supports": supports, "claim": claim,
                                       "verified": verified, "detail": detail or {}, "at": self.s["t"]}
            self.emit(source, "EVIDENCE_CAPTURED" if verified else "OBSERVATION_RECORDED", eid,
                      {"supports": supports, "claim": claim, "verified": verified})
        return self.tx(source, "CAPTURE_EVIDENCE", fn)

    def reconcile(self, actor, cid, evidence_id, scope=None):
        self._journal("reconcile", actor, cid, evidence_id, scope)
        def fn():
            self.authorize(actor, "RECONCILE", scope)
            c = self.s["commitments"].get(cid)
            if not c or c["status"] not in UNRESOLVED_COMMITMENT:
                raise Rejected("NOT_UNRESOLVED", cid)
            ev = self.s["evidence"].get(evidence_id)
            if not ev or ev["supports"] != cid:
                raise Rejected("EVIDENCE_MISMATCH", str(evidence_id))
            if not ev["verified"]:
                raise Rejected("EVIDENCE_NOT_AUTHORITATIVE", f"{evidence_id} from {ev['source']}")
            if ev["claim"] not in (EFFECT_CONFIRMED, EFFECT_REJECTED):
                raise Rejected("NOT_AN_OUTCOME", ev["claim"])
            c["status"], c["outcome"] = C_RECONCILED, ev["claim"]
            c["evidence"].append(evidence_id)
            xid = f"EXEC-{cid}"
            self.s["executions"][xid] = {"id": xid, "commitment": cid, "status": ev["claim"], "evidence": [evidence_id],
                                         "observed_at": self.s["t"], "detail": ev["detail"]}
            self.emit(actor, "COMMITMENT_RECONCILED", cid, {"outcome": ev["claim"], "evidence": evidence_id, "execution": xid})
            o = self.s["obligations"][c["obligation"]]
            if o["status"] == O_COMMITTED:
                self._step(o, O_WAITING, "OUTCOME_ESTABLISHED")
            for r in self.s["reservations"].values():
                if r["status"] == R_HELD and not self.uncertain_reservation(r["id"]):
                    r["status"] = R_ACTIVE
                    self.emit("KERNEL", "RESERVATION_CERTAIN", r["id"], {"obligation": r["obligation"]})
        return self.tx(actor, "RECONCILE", fn)

    # ------------------------------------------------------------------ invariants
    INVARIANTS = {
        "K01": "Quantity and slot conservation",
        "K02": "At most one slot per holder per pool",
        "K03": "Every unresolved obligation has a living owner",
        "K04": "No dead entity holds active authority",
        "K05": "Every active grant is attenuated from an active parent",
        "K06": "At most one unresolved commitment per business intent",
        "K07": "Every reconciled commitment is backed by verified evidence",
        "K08": "Closed obligations have no open reservations or commitments",
        "K09": "No reservation released while its external outcome is unresolved",
        "K10": "Root I is alive",
        "K11": "Event log hash chain is intact",
        "K12": "Every decision-bound instruction references an AUTHORIZE decision for its proposal",
        "K13": "Approver is independent of proposer",
        "K14": "Authority lineage is acyclic and rooted in root I",
        "K15": "Every obligation in recovery has an open recovery assignment with a living owner",
    }

    def check_invariants(self):
        s, v = self.s, []
        for pid, pool in s["pools"].items():
            u = self.pool_usage(pid)
            if u["available"] < 0:
                v.append(("K01", f"{pid} over-allocated: {u}"))
            if pool["kind"] == "SLOTS":
                seen = set()
                for g in s["grants"].values():
                    if g["slot_pool"] == pid and g["status"] == G_ACTIVE:
                        if g["holder"] in seen:
                            v.append(("K02", f"{g['holder']} holds two slots of {pid}"))
                        seen.add(g["holder"])
        for o in s["obligations"].values():
            if o["status"] in UNRESOLVED_OBLIGATION and not (o["owner"] and self.alive(o["owner"])):
                v.append(("K03", f"{o['id']} is {o['status']} with owner {o['owner']}"))
        for g in s["grants"].values():
            if g["status"] != G_ACTIVE:
                continue
            if not self.alive(g["holder"]):
                v.append(("K04", f"{g['id']} held by dead {g['holder']}"))
            if g["parent"] is not None:
                p = s["grants"][g["parent"]]
                if p["status"] != G_ACTIVE or not _subset(g["actions"], p["grant_actions"]) \
                        or not _subset(g["grant_actions"], p["grant_actions"]):
                    v.append(("K05", f"{g['id']} not attenuated from active {p['id']}"))
        intents = {}
        for c in s["commitments"].values():
            if c["status"] in UNRESOLVED_COMMITMENT and not c["idempotent"]:
                intents.setdefault(c["intent"], []).append(c["id"])
            if c["status"] == C_RECONCILED and not any(
                    s["evidence"].get(e, {}).get("verified") for e in c["evidence"]):
                v.append(("K07", f"{c['id']} reconciled without verified evidence"))
        for intent, ids in intents.items():
            if len(ids) > 1:
                v.append(("K06", f"{intent} has unresolved {ids}"))
        for o in s["obligations"].values():
            if o["status"] == O_CLOSED and (self.unresolved_commitments(o["id"]) or any(
                    r["obligation"] == o["id"] and r["status"] in (R_ACTIVE, R_HELD) for r in s["reservations"].values())):
                v.append(("K08", f"{o['id']} closed with open items"))
        for r in s["reservations"].values():
            if r["status"] == R_RELEASED and self.uncertain_reservation(r["id"]):
                v.append(("K09", f"{r['id']} released while an external outcome affecting it is unresolved"))
        if not self.alive(self.root):
            v.append(("K10", "root is dead"))
        for c in s["commitments"].values():
            if c.get("decision"):
                d = s["decisions"].get(c["decision"])
                if not d or d["decision"] != "AUTHORIZE" or d["proposal"] != c.get("proposal"):
                    v.append(("K12", f"{c['id']} does not reference a valid authorization"))
            elif self.require_decision:
                v.append(("K12", f"{c['id']} has no authorization"))
        for p in s["proposals"].values():
            if any(a["by"] == p["by"] for a in p["approvals"]):
                v.append(("K13", f"{p['id']} approved by its proposer"))
        for g in s["grants"].values():
            seen, cur = set(), g["id"]
            while cur is not None:
                if cur in seen:
                    v.append(("K14", f"cycle through {g['id']}")); break
                seen.add(cur)
                parent = s["grants"][cur]["parent"]
                if parent is None and cur != "G-ROOT":
                    v.append(("K14", f"{g['id']} not rooted in G-ROOT")); break
                cur = parent
        for o in s["obligations"].values():
            if o["status"] == O_RECOVERY:
                r = s["recoveries"].get(o["recovery"]) if o["recovery"] else None
                if not r or r["status"] not in ("OPEN", "CLAIMED") or not self.alive(r["owner"]):
                    v.append(("K15", f"{o['id']} in recovery without a live recovery owner"))
        return v

    def verify_log(self):
        prev = "GENESIS"
        for ev in self.log:
            body = {k: ev[k] for k in ("seq", "t", "type", "actor", "subject", "payload", "prev")}
            h = hashlib.sha256((prev + json.dumps(body, sort_keys=True)).encode()).hexdigest()[:16]
            if ev["prev"] != prev or ev["hash"] != h:
                return False
            prev = ev["hash"]
        return True

    def invariant_report(self):
        failing = {}
        for code, msg in self.check_invariants():
            failing.setdefault(code, []).append(msg)
        if not self.verify_log():
            failing.setdefault("K11", []).append("hash chain broken")
        return [{"id": k, "name": n, "status": "FAIL" if k in failing else "PASS", "detail": failing.get(k, [])}
                for k, n in self.INVARIANTS.items()]

    def snapshot(self):
        s = copy.deepcopy(self.s)
        s["pool_usage"] = {pid: self.pool_usage(pid) for pid in s["pools"]}
        s["invariants"] = self.invariant_report()
        return s
