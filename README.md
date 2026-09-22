# AI Life Authority Model — v0.4 prototype

**What happens to financial authority and obligations when the agent performing the work fails?**

An executable simulator of the AI Life Authority Model on its reference domain, post-trade
settlement exception repair (trade T12345, 100,000 ABC, SSI mismatch, deadline 15:30).
It demonstrates that an agent may disappear but no obligation, authority history, external
commitment or recovery responsibility disappears with it.

> Research prototype. Synthetic data only. No connection to production financial infrastructure.

## Run locally

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python3 app.py                      # http://localhost:5000
    python3 -m unittest -v              # 47 tests

**Hostile scenario:** open *Demo Story* and press *Next step* (or *Auto run*) through 13 chapters.
**Baseline comparison:** switch the top-right toggle to *Baseline* and run the same story.
**Reset:** *Reset* on the Demo Story or Failure Lab screens, or `POST /api/simulator/reset`.

## Deploy to Render

The repository includes `render.yaml`. Push to GitHub, create a Blueprint in Render, done.
Each browser gets its own in-memory simulator (cookie session), so concurrent visitors do not
interfere. Keep a single worker: sessions live in process memory.

## Screens

| Screen | Purpose |
|---|---|
| Control Tower | Active obligations, recovery cases, uncertain commitments, blocked actions, invariant health, recent control decisions |
| Case | Business obligation, processing state (owner, workers, recovery assignment, proposals and gate decisions), control state (commitment → execution → evidence, external truth) and operator commands |
| Authority | Authority lineage (edges mean *delegated from*) with an inspector for source, scope, permitted and forbidden actions, limit, expiry, delegation and status |
| Timeline | Every event by sequence number, with filters and a payload inspector; hash-chain status |
| Invariants | I01–I20 plus two checks against the gateway's own records; PASS, FAIL and UNKNOWN kept distinct |
| Failure Lab | 14 failure injections, external response modes, simulated clock, failure history |
| Demo Story | The 13-chapter hostile scenario, in AI Life or Baseline mode |
| Architecture | AI Life, Gate Symphony and the execution boundary |

## API (v0.4 section 35–41)

    GET  /api/cases                          POST /api/cases              {architecture}
    GET  /api/cases/{id}                     POST /api/cases/{id}/commands {type, agentId, payload}
    GET  /api/cases/{id}/events              POST /api/cases/{id}/failures {failureType, targetId}
    GET  /api/cases/{id}/authority-lineage   POST /api/cases/{id}/reconcile
    GET  /api/cases/{id}/invariants          POST /api/simulator/clock    {action: ADVANCE|ADVANCE_TO_DEADLINE, minutes}
    POST /api/scenario/next                  POST /api/simulator/reset    {architecture}

There is no endpoint that sets state directly. Command types: `PROPOSE_REPAIR`, `APPROVE_REPAIR`,
`SUBMIT_INSTRUCTION`, `RECONCILE`, `RESOLVE_OBLIGATION`, `CLOSE_OBLIGATION`, `ADVANCE_CLOCK`,
`SET_EXTERNAL_MODE`. Failure types: `KILL_AGENT`, `KILL_CONTROLLER`, `REVOKE_AUTHORITY`,
`EXPIRE_AUTHORITY`, `LOSE_ACKNOWLEDGEMENT`, `CHANGE_SSI`, `CREATE_CONFLICTING_PROPOSAL`,
`ATTEMPT_QUANTITY_ESCALATION`, `ATTEMPT_DUPLICATE_SUBMISSION`, `USE_STALE_AUTHORIZATION`,
`FAKE_SUCCESS`, `DOUBLE_RESERVE_RESOURCE`, `REMOVE_OWNER`, `DISCONNECT_STATUS_API`, `RECONNECT_STATUS_API`.

## Architecture

    ailife/kernel.py                 Mother kernel: deterministic, transactional, hash-chained
    ailife/children/posttrade.py     This reference domain: gateway simulator, mock Gate Symphony,
                                     hostile scenario, failure injection, I01–I20 catalogue
    ailife/children/atomic_dvp.py    Same kernel: tokenized bond vs wholesale CBDC settlement
    ailife/children/persona_life.py  Same kernel: the original persona model
    app.py                           Flask API and per-session simulators
    static/                          UI (vanilla JS, no build step)
    tests/                           Kernel, post-trade, DvP, persona and API tests

Every command is an atomic transaction: it commits all its effects and the events describing them,
or rolls back and leaves only a `COMMAND_REJECTED` event. Invariants are evaluated after every
transaction; in AI Life mode a transaction that would break one is refused.

## Deliberate deviations from the v0.4 build contract

| Contract | This prototype | Why |
|---|---|---|
| Next.js, Prisma, PostgreSQL, React Flow | Flask, in-memory state, vanilla JS, SVG | The contract allows a Python backend. One deterministic core is shared with the DvP and persona models; deploys to Render with no build step |
| State store rebuilt by projecting events | State rebuilt by replaying the command journal; the replay must reproduce the event hash chain exactly (I14, tested) | Same guarantee (state is derivable, history is authoritative) with less code |
| Separate `CREATED` and `EFFECT_CONFIRMED` commitment states | `SENT → OUTCOME_UNKNOWN → RECONCILED` with an outcome, plus a separate `Execution` record holding `EFFECT_CONFIRMED` / `EFFECT_REJECTED` | Keeps commitment and execution distinct as the model requires |
| Agent states `INITIALIZED/ACTIVE/SUSPENDED/FAILED/TERMINATED` | Kernel `ALIVE/DEAD`, shown as `ACTIVE/FAILED` | A failed identity is never resumed; replacements are new identities |
| Authority states include `PENDING/SUSPENDED/CONSUMED` | `ACTIVE/REVOKED/EXPIRED` | Not needed by the reference scenario; no invented states |

## Spec issues fixed during implementation

1. **May-do vs may-grant.** Under "child actions ⊆ parent actions" the seeded Exception Controller
   could not delegate `PROPOSE_REPAIR`. Grants now carry `actions` (may do) and `grant_actions` (may grant).
2. **I08 is a precondition.** As coded in v0.4 it fails whenever any commitment is `SENT`. Duplicate
   prevention is a submission guard; the state invariant is "at most one unresolved commitment per intent".
3. **I06 checks a living owner**, not merely a non-empty owner field.
4. **Stale authorization is detectable.** Proposals and gate decisions record the case revision they were
   issued on; reference-data changes bump the revision and expire live proposals.
5. **Reservation uncertainty follows the commitment**, including commitments on other obligations that
   declare they affect the reservation.
6. **The hard death is tested.** *Kill Exception Controller* removes the obligation owner itself;
   ownership passes up to the principal, and every authority derived from the controller is revoked.

## What this prototype does not claim

It does not predict settlement failure, optimise collateral, replace a CSD or custody platform,
determine legal title or settlement finality, perform real trades or payments, give investment
advice, or prove regulatory compliance. Metrics are simulator results, not evidence of real-world
risk reduction. The baseline is a constructed experimental comparison, not a claim about any
commercial agent system. Invariants are tested, not formally model-checked.
