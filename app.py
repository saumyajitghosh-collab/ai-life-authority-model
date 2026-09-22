"""AI Life Authority Model: prototype server.

Serves the control-plane UI and the v0.4 API surface. Each browser session gets its own
simulator, so a public deployment can host many independent demos. Everything is synthetic.
"""
import os
import secrets
import threading
from collections import OrderedDict

from flask import Flask, jsonify, request, send_from_directory

from ailife.children.posttrade import PostTradeCase, TRADE, OBL, clock

CASE_ID = f"CASE-{TRADE}"
MAX_SESSIONS = int(os.environ.get("AILIFE_MAX_SESSIONS", "300"))
app = Flask(__name__, static_folder="static", static_url_path="/static")
_sessions: "OrderedDict[str, PostTradeCase]" = OrderedDict()
_lock = threading.Lock()


def _session():
    sid = request.cookies.get("ailife_sid")
    with _lock:
        if not sid or sid not in _sessions:
            sid = sid or secrets.token_urlsafe(16)
            _sessions[sid] = PostTradeCase("AI_LIFE")
            while len(_sessions) > MAX_SESSIONS:
                _sessions.popitem(last=False)
        _sessions.move_to_end(sid)
        return sid, _sessions[sid]


def _reply(payload, sid, status=200):
    resp = jsonify(payload)
    resp.status_code = status
    resp.set_cookie("ailife_sid", sid, httponly=True, samesite="Lax", max_age=60 * 60 * 24)
    return resp


def _view(case, step=None):
    v = case.view()
    v["caseId"] = CASE_ID
    v["lastStep"] = step
    v["steps"] = [{k: s[k] for k in ("chapter", "title", "narrative", "clock", "calls")} for s in case.steps]
    return v


def _check_case(case_id):
    return case_id == CASE_ID


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- cases
@app.get("/api/cases")
def list_cases():
    sid, case = _session()
    o = case.k.s["obligations"].get(OBL)
    return _reply([{"caseId": CASE_ID, "trade": TRADE, "type": "SSI mismatch",
                    "state": o["status"] if o else "NOT_YET_DETECTED", "deadline": "15:30",
                    "architecture": case.architecture}], sid)


@app.post("/api/cases")
def create_case():
    sid, case = _session()
    arch = (request.get_json(silent=True) or {}).get("architecture", case.architecture)
    with _lock:
        _sessions[sid] = PostTradeCase(arch if arch in ("AI_LIFE", "BASELINE") else "AI_LIFE")
    return _reply(_view(_sessions[sid]), sid, 201)


@app.get("/api/cases/<case_id>")
def get_case(case_id):
    sid, case = _session()
    if not _check_case(case_id):
        return _reply({"error": "NO_SUCH_CASE"}, sid, 404)
    return _reply(_view(case), sid)


@app.post("/api/cases/<case_id>/commands")
def command(case_id):
    sid, case = _session()
    if not _check_case(case_id):
        return _reply({"error": "NO_SUCH_CASE"}, sid, 404)
    body = request.get_json(silent=True) or {}
    step = case.command(str(body.get("type", "")), body.get("agentId"), body.get("payload") or {})
    return _reply(_view(case, step), sid)


@app.get("/api/cases/<case_id>/events")
def events(case_id):
    sid, case = _session()
    if not _check_case(case_id):
        return _reply({"error": "NO_SUCH_CASE"}, sid, 404)
    evs = [dict(e, clock=clock(e["t"])) for e in case.k.log]
    return _reply(sorted(evs, key=lambda e: e["seq"]), sid)


@app.get("/api/cases/<case_id>/authority-lineage")
def lineage(case_id):
    sid, case = _session()
    if not _check_case(case_id):
        return _reply({"error": "NO_SUCH_CASE"}, sid, 404)
    return _reply(case.lineage(), sid)


@app.get("/api/cases/<case_id>/invariants")
def invariants(case_id):
    sid, case = _session()
    if not _check_case(case_id):
        return _reply({"error": "NO_SUCH_CASE"}, sid, 404)
    rows = case.invariant_catalogue() + case.domain_checks()
    return _reply({"healthy": sum(r["status"] == "PASS" for r in rows),
                   "failed": sum(r["status"] == "FAIL" for r in rows),
                   "unknown": sum(r["status"] == "UNKNOWN" for r in rows), "results": rows}, sid)


@app.post("/api/cases/<case_id>/failures")
def failures(case_id):
    sid, case = _session()
    if not _check_case(case_id):
        return _reply({"error": "NO_SUCH_CASE"}, sid, 404)
    body = request.get_json(silent=True) or {}
    step = case.inject(str(body.get("failureType", "")), body.get("targetId"))
    return _reply(_view(case, step), sid)


@app.post("/api/cases/<case_id>/reconcile")
def reconcile(case_id):
    sid, case = _session()
    if not _check_case(case_id):
        return _reply({"error": "NO_SUCH_CASE"}, sid, 404)
    step = case.command("RECONCILE")
    return _reply(_view(case, step), sid)


# ---------------------------------------------------------------- simulator
@app.post("/api/simulator/clock")
def sim_clock():
    sid, case = _session()
    body = request.get_json(silent=True) or {}
    if body.get("action") == "ADVANCE_TO_DEADLINE":
        minutes = max(0, case.view()["minutes_to_deadline"])
    else:
        minutes = max(0, min(600, int(body.get("minutes", 1))))
    step = case.command("ADVANCE_CLOCK", payload={"minutes": minutes})
    return _reply(_view(case, step), sid)


@app.post("/api/simulator/reset")
def sim_reset():
    return create_case()


@app.post("/api/scenario/next")
def scenario_next():
    sid, case = _session()
    step = case.next_chapter()
    return _reply(_view(case, step), sid)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
