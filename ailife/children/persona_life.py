"""Child model: the original AI Life persona model on the Mother kernel.

Persona access slots are a SLOTS pool. IAcquire is a slot-drawing grant from root I,
IRelease(alpha) is revoke, inheritance by transfer is transfer_grant, and death uses
RELEASE_ALL: everything returns to the pool. Under STRICT enforcement the kernel refuses
that death while the individual still owns an unresolved obligation.
"""
from ..kernel import Kernel, RELEASE_ALL, STRICT


def scenario():
    k = Kernel(death_policy=RELEASE_ALL, enforce=STRICT)
    steps, mark, calls = [], 0, []

    def c(label, out, expect=None):
        unexpected = (not out[0] and out[1] != expect) or (out[0] and expect is not None)
        calls.append({"label": label, "ok": out[0], "result": out[1], "expected": expect, "unexpected": unexpected})

    def step(chapter, title, narrative):
        nonlocal mark, calls
        steps.append({"chapter": chapter, "title": title, "narrative": narrative, "t": k.s["t"],
                      "events": k.log[mark:], "calls": calls, "state": k.snapshot(), "ledgers": {}, "domain": []})
        mark, calls = len(k.log), []

    c("pool", k.create_pool("I", "PERSONA-ANALYST", "SLOTS", 2, "Persona Pα: Analyst (capacity 2)"))
    for i in ("I1", "I2", "I3"):
        c(f"spawn {i}", k.spawn("I", i, "INDIVIDUAL"))
    step("Birth", "I spawns three individuals", "One persona with two access slots and three individuals competing for it.")
    c("I1 acquires", k.delegate("I", "G-I1", "G-ROOT", "I1", ["USE_ANALYST"], slot_pool="PERSONA-ANALYST", transferable=True))
    c("I2 acquires", k.delegate("I", "G-I2", "G-ROOT", "I2", ["USE_ANALYST"], slot_pool="PERSONA-ANALYST", transferable=True))
    c("I3 acquires", k.delegate("I", "G-I3", "G-ROOT", "I3", ["USE_ANALYST"], slot_pool="PERSONA-ANALYST", transferable=True), "CAPACITY_FULL")
    step("IAcquire", "I1 and I2 acquire; I3 is refused", "Capacity is full, and the kernel never evicts an incumbent.")
    c("grant spawn", k.delegate("I", "G-I1-SPAWN", "G-ROOT", "I1", ["SPAWN"]))
    c("I1 spawns child", k.spawn("I1", "I1.1", "INDIVIDUAL"))
    c("transfer to child", k.transfer_grant("I1", "G-I1", "I1.1"))
    step("Inheritance", "I1 spawns I1.1 and transfers its slot", "The holding moves and capacity is unchanged. The child has a new identity and I1's lineage.")
    c("I2 takes a case", k.delegate("I", "G-I2-CASE", "G-ROOT", "I2", ["OPEN_OBLIGATION"]))
    c("I2 opens obligation", k.open_obligation("I2", "OB-TASK", "TASK"))
    c("kill I2 with open obligation", k.kill("RUNTIME", "I2", "LIFESPAN_ENDED"), "INVARIANT_WOULD_BREAK")
    step("Death refused", "I2 cannot die while it owns an open obligation",
         "Under RELEASE_ALL death would orphan OB-TASK, so invariant K03 would break and the transaction rolls back. This is the rule that death is impossible while the case exists.")
    c("I2 hands task to I", k.assign_obligation("I2", "OB-TASK", "I"))
    c("kill I2", k.kill("RUNTIME", "I2", "LIFESPAN_ENDED"))
    c("I3 acquires", k.delegate("I", "G-I3", "G-ROOT", "I3", ["USE_ANALYST"], slot_pool="PERSONA-ANALYST", transferable=True))
    step("IRelease", "I2 hands over its case, dies, and I3 takes the freed slot",
         "With no obligations left, death releases everything to the pool. This is the original model's semantics.")
    return {"id": "s0", "title": "Persona model on the same kernel", "death_policy": k.death_policy,
            "enforce": k.enforce, "steps": steps}
