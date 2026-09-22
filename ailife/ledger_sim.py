"""Synthetic external ledger. It keeps its own truth, independent of the control plane.

Supports hash time-locked escrow (LOCK / CLAIM, automatic reversion to the sender at
timeout), plain TRANSFER, and single-transaction ATOMIC_DVP for a unified ledger.
Finality is immediate once a transaction is applied (deterministic-finality ledger).
Response modes let a scenario make the ledger process a transaction but lose the reply.
"""
import hashlib


def hashlock(preimage: str) -> str:
    return hashlib.sha256(preimage.encode()).hexdigest()[:16]


class Ledger:
    def __init__(self, name, balances):
        self.name = name
        self.t = 0
        self.balances = {acct: dict(assets) for acct, assets in balances.items()}
        self.locks = {}
        self.txs = {}
        self.revealed = {}
        self.next_response = "NORMAL"   # NORMAL | DROP_RESPONSE | REJECT

    def _move(self, frm, to, asset, amt):
        self.balances.setdefault(frm, {}).setdefault(asset, 0)
        self.balances.setdefault(to, {}).setdefault(asset, 0)
        self.balances[frm][asset] -= amt
        self.balances[to][asset] += amt

    def _bal(self, acct, asset):
        return self.balances.get(acct, {}).get(asset, 0)

    def submit(self, txid, op, args):
        mode, self.next_response = self.next_response, "NORMAL"
        if txid in self.txs:  # a ledger dedupes an identical transaction id
            rec = self.txs[txid]
            return None if mode == "DROP_RESPONSE" else dict(rec)
        status, reason = "FINAL", None
        if mode == "REJECT":
            status, reason = "FAILED", "REJECTED_BY_LEDGER"
        elif op == "LOCK":
            if self._bal(args["frm"], args["asset"]) < args["amount"]:
                status, reason = "FAILED", "INSUFFICIENT_BALANCE"
            else:
                self._move(args["frm"], f"ESCROW:{args['lock_id']}", args["asset"], args["amount"])
                self.locks[args["lock_id"]] = {**args, "status": "LOCKED"}
        elif op == "CLAIM":
            lk = self.locks.get(args["lock_id"])
            if not lk or lk["status"] != "LOCKED":
                status, reason = "FAILED", f"LOCK_{lk['status'] if lk else 'MISSING'}"
            elif self.t >= lk["timeout"]:
                status, reason = "FAILED", "LOCK_EXPIRED"
            elif hashlock(args["preimage"]) != lk["hashlock"]:
                status, reason = "FAILED", "BAD_PREIMAGE"
            else:
                self._move(f"ESCROW:{lk['lock_id']}", lk["to"], lk["asset"], lk["amount"])
                lk["status"] = "CLAIMED"
                self.revealed[lk["hashlock"]] = args["preimage"]
        elif op == "TRANSFER":
            if self._bal(args["frm"], args["asset"]) < args["amount"]:
                status, reason = "FAILED", "INSUFFICIENT_BALANCE"
            else:
                self._move(args["frm"], args["to"], args["asset"], args["amount"])
        elif op == "ATOMIC_DVP":
            legs = args["legs"]
            if any(self._bal(l["frm"], l["asset"]) < l["amount"] for l in legs):
                status, reason = "FAILED", "INSUFFICIENT_BALANCE_ALL_LEGS_VOID"
            else:
                for l in legs:
                    self._move(l["frm"], l["to"], l["asset"], l["amount"])
        self.txs[txid] = {"txid": txid, "op": op, "status": status, "reason": reason, "t": self.t}
        return None if mode == "DROP_RESPONSE" else dict(self.txs[txid])

    def query(self, txid):
        rec = self.txs.get(txid)
        return dict(rec) if rec else {"txid": txid, "status": "NOT_FOUND"}

    def tick(self, t):
        self.t = t
        reverted = []
        for lk in self.locks.values():
            if lk["status"] == "LOCKED" and t >= lk["timeout"]:
                self._move(f"ESCROW:{lk['lock_id']}", lk["frm"], lk["asset"], lk["amount"])
                lk["status"] = "REVERTED"
                reverted.append(lk["lock_id"])
        return reverted

    def snapshot(self):
        return {"name": self.name, "t": self.t,
                "balances": {a: {k: v for k, v in b.items() if v} for a, b in self.balances.items()
                             if any(b.values())},
                "locks": {k: {x: lk[x] for x in ("asset", "amount", "frm", "to", "timeout", "status")}
                          for k, lk in self.locks.items()},
                "txs": dict(self.txs), "revealed": list(self.revealed.keys())}
