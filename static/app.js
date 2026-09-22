/* AI Life Authority Model — control-plane UI. No build step, no framework. */
const CASE = 'CASE-T12345';
let V = null, EV = [], busy = false, auto = null;
const ui = { authSel: null, invSel: 'I08', evSel: null, evFilter: 'all' };
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>]/g, c => ({ '&': '&', '<': '<', '>': '>', '"': '"' }[c]));
const fmt = n => typeof n === 'number' ? n.toLocaleString('en-US') : esc(n ?? '—');
const hm = t => { const m = 615 + t; return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); };

const ROUTES = [
  ['tower', 'Control Tower'], ['case', 'Case'], ['authority', 'Authority'], ['timeline', 'Timeline'],
  ['invariants', 'Invariants'], ['lab', 'Failure Lab'], ['story', 'Demo Story'], ['architecture', 'Architecture']];

function tone(s) {
  const m = {
    ACTIVE: 'ok', ACCEPTED: 'ok', PASS: 'ok', CLOSED: 'ok', RESOLVED: 'ok', RECONCILED: 'ok', CONFIRMED: 'ok', EFFECT_CONFIRMED: 'ok', CONSUMED: 'ok',
    AUTHORIZED: 'ok', AUTHORIZE: 'ok', VERIFIED: 'ok', COMPLETE: 'ok', EXECUTED: 'ok', SETTLED: 'ok',
    FAILED: 'bad', FAIL: 'bad', REJECTED: 'bad', REJECT: 'bad', BLOCKED: 'bad', EFFECT_REJECTED: 'bad', CRITICAL: 'bad',
    UNKNOWN: 'unk', OUTCOME_UNKNOWN: 'unk', HELD_UNCERTAIN: 'unk',
    RECOVERY_REQUIRED: 'warn', ESCALATED: 'warn', ESCALATE: 'warn', ATTENUATED: 'warn', SENT: 'warn', OPEN: 'warn', CLAIMED: 'warn', WARN: 'warn', UNVERIFIED: 'warn',
    REVOKED: 'dead', EXPIRED: 'dead', RELEASED: 'mut', CANCELLED: 'mut'
  };
  return m[s] || 'mut';
}
const st = (s, label) => `<span class="st ${tone(s)}">${esc((label || s || '').replace(/_/g, ' '))}</span>`;
const role = id => (V && V.roles && V.roles[id]) || id || '—';

async function api(method, path, body) {
  if (busy) return;
  busy = true;
  try {
    const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined, credentials: 'same-origin' });
    const data = await r.json();
    if (data && data.state) V = data;
    EV = await (await fetch(`/api/cases/${CASE}/events`, { credentials: 'same-origin' })).json();
    return data;
  } catch (e) {
    toast('The server did not respond. Is app.py running?');
  } finally { busy = false; render(); }
}
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 3200); }
function summarise(step) {
  if (!step || !step.calls) return;
  const rej = step.calls.filter(c => !c.ok);
  if (rej.length) toast(`Blocked: ${rej.map(c => c.result.replace(/_/g, ' ').toLowerCase()).join(', ')}`);
  else if (step.calls.length) toast(step.title);
}

/* ---------------------------------------------------------------- event language */
function describe(e) {
  const p = e.payload || {};
  const D = {
    ROOT_CREATED: () => `Principal ${role(e.subject)} established`,
    ENTITY_SPAWNED: () => `${role(e.subject)} initialised`,
    AUTHORITY_GRANTED: () => `Authority ${e.subject} granted to ${role(p.to)}: ${(p.actions || []).join(', ')}`,
    AUTHORITY_REVOKED: () => `Authority ${e.subject} revoked (${String(p.reason || '').replace(/_/g, ' ').toLowerCase()})`,
    AUTHORITY_EXPIRED: () => `Authority ${e.subject} expired`,
    POOL_CREATED: () => `Position ${e.subject} registered: ${fmt(p.total)}`,
    SETTLEMENT_EXCEPTION_DETECTED: () => `Settlement exception detected on ${e.subject}: ${String(p.reason || '').replace(/_/g, ' ')}`,
    OBLIGATION_OPENED: () => `Obligation ${e.subject} created`,
    OBLIGATION_ASSIGNED: () => `Obligation ${e.subject} owned by ${role(p.to)}`,
    WORKER_ASSIGNED: () => `${role(p.worker)} assigned to ${e.subject}`,
    OBLIGATION_STATUS: () => `Obligation ${e.subject}: ${String(p.from).replace(/_/g, ' ')} → ${String(p.to).replace(/_/g, ' ')}`,
    RESOURCE_RESERVED: () => `${fmt(p.qty)} reserved as ${e.subject}`,
    RESOURCE_CONSUMED: () => `Reservation ${e.subject} consumed on verified evidence`,
    RESOURCE_RELEASED: () => `Reservation ${e.subject} released`,
    RESERVATION_RELEASED: () => `Reservation ${e.subject} released when its holder died`,
    RESERVATION_HELD_UNCERTAIN: () => `Reservation ${e.subject} held: external outcome uncertain`,
    RESERVATION_CERTAIN: () => `Reservation ${e.subject} no longer uncertain`,
    MEMORY_RECORDED: () => `${role(e.actor)} recorded an observation (memory, not authority)`,
    ACTION_PROPOSED: () => `${role(e.actor)} proposed ${e.subject}: ${p.change ? p.change.field + ' → ' + p.change.newValue : ''}`,
    PROPOSAL_ESCALATED: () => `Gate Symphony escalated ${e.subject}: ${String(p.reason || '').replace(/_/g, ' ').toLowerCase()}`,
    PROPOSAL_AUTHORIZED: () => `Gate Symphony authorised ${e.subject}`,
    PROPOSAL_REJECTED: () => `Gate Symphony rejected ${e.subject}: ${String(p.reason || '').replace(/_/g, ' ').toLowerCase()}`,
    PROPOSAL_ATTENUATED: () => `Gate Symphony attenuated ${e.subject}`,
    PROPOSAL_EXPIRED: () => `Proposal ${e.subject} expired: case changed`,
    PROPOSAL_EXECUTED: () => `Proposal ${e.subject} used by ${p.commitment}`,
    PROPOSAL_CANCELLED: () => `Proposal ${e.subject} cancelled`,
    FOUR_EYE_APPROVED: () => `Four-eye approval of ${e.subject} by ${role(e.actor)}`,
    COMMITMENT_SENT: () => `Instruction ${e.subject} transmitted to the settlement gateway`,
    EXTERNAL_OUTCOME_UNKNOWN: () => `External outcome of ${e.subject} marked UNKNOWN`,
    EVIDENCE_CAPTURED: () => `Verified evidence ${e.subject} from ${role(e.actor)}`,
    OBSERVATION_RECORDED: () => `Unverified claim ${e.subject} from ${role(e.actor)}: not evidence`,
    COMMITMENT_RECONCILED: () => `${e.subject} reconciled: ${String(p.outcome).replace(/_/g, ' ').toLowerCase()}`,
    DEATH_REQUESTED: () => `Failure of ${role(e.subject)} reported`,
    ENTITY_DIED: () => `${role(e.subject)} FAILED`,
    OBLIGATION_SUCCEEDED: () => `Obligation ${e.subject} passed from ${role(p.from)} to ${role(p.to)}`,
    OBLIGATION_ABANDONED: () => `Obligation ${e.subject} abandoned: no owner`,
    WORKER_LOST: () => `Worker ${role(p.worker)} lost; agent-local task state gone`,
    RECOVERY_CREATED: () => `Recovery ${e.subject} opened, owner ${role(p.owner)}`,
    RECOVERY_CLAIMED: () => `Recovery claimed: ${role(p.worker)} now working ${e.subject}`,
    RECOVERY_COMPLETED: () => `Recovery ${e.subject} complete`,
    REFERENCE_DATA_CHANGED: () => `Reference data changed: ${p.detail && p.detail.newSSI ? 'SSI → ' + p.detail.newSSI : ''}`,
    EXTERNAL_STATUS_QUERIED: () => `Gateway queried for ${e.subject}: ${p.settled ? 'SETTLED' + (p.settled_at ? ' at ' + p.settled_at : '') : 'not settled'}`,
    EXTERNAL_STATUS_UNAVAILABLE: () => `Status API unavailable for ${e.subject}; outcome stays UNKNOWN`,
    FAILURE_INJECTION_REQUESTED: () => `Failure injected: ${String(p.failureType || '').replace(/_/g, ' ').toLowerCase()}`,
    EXTERNAL_MODE_SET: () => `External mode set to ${p.mode}`,
    OWNER_DETACHED: () => `Owner detached from ${e.subject}`,
    COMMAND_REJECTED: () => `BLOCKED ${String(e.subject).replace(/_/g, ' ').toLowerCase()}: ${String(p.code).replace(/_/g, ' ')}`,
    INVARIANT_VIOLATED: () => `Invariant ${e.subject} violated`,
    CLOCK_ADVANCED: () => `Clock advanced to ${hm(p.t)}`,
  };
  return (D[e.type] || (() => e.type.replace(/_/g, ' ').toLowerCase()))();
}
const CONTROL = new Set(['COMMAND_REJECTED', 'INVARIANT_VIOLATED', 'PROPOSAL_ESCALATED', 'PROPOSAL_AUTHORIZED', 'PROPOSAL_REJECTED', 'PROPOSAL_ATTENUATED', 'PROPOSAL_EXPIRED', 'FOUR_EYE_APPROVED', 'AUTHORITY_REVOKED', 'AUTHORITY_EXPIRED', 'RESERVATION_HELD_UNCERTAIN']);
const FAILEV = new Set(['FAILURE_INJECTION_REQUESTED', 'ENTITY_DIED', 'DEATH_REQUESTED', 'EXTERNAL_OUTCOME_UNKNOWN', 'OWNER_DETACHED', 'OBLIGATION_ABANDONED', 'WORKER_LOST', 'EXTERNAL_STATUS_UNAVAILABLE']);

/* ---------------------------------------------------------------- shared pieces */
const S = () => V.state;
const obl = () => S().obligations['OBL-10022'];
function invCounts() { const all = [...V.invariants, ...V.domain]; return { pass: all.filter(r => r.status === 'PASS').length, fail: all.filter(r => r.status === 'FAIL').length, unk: all.filter(r => r.status === 'UNKNOWN').length, total: all.length }; }
function callsList(calls) {
  if (!calls || !calls.length) return '<p class="note">No commands in this step.</p>';
  return `<ul class="calls">${calls.filter(c => !c.label.startsWith('Clock +')).map(c => `<li>${c.ok ? st('ACCEPTED', 'Accepted') : st(c.expected ? 'BLOCKED' : 'REJECTED', 'Blocked')}<span>${esc(c.label)}${c.ok ? '' : `<span class="why"><span class="mono">${esc(c.result)}</span>${c.detail ? ' · ' + esc(c.detail) : ''}</span>`}</span></li>`).join('')}</ul>`;
}
function agentsCard() {
  return `<div class="card"><h2>Agents</h2>${V.agents.filter(a => a.kind === 'AGENT').map(a => `<div class="agent ${a.status === 'FAILED' ? 'failed' : ''}"><div><b>${esc(a.role)}</b><small>${a.authorities.length ? 'Authority ' + a.authorities.join(', ') : 'No active authority'}${a.replacement_for ? ' · replaces ' + esc(role(a.replacement_for)) : ''}${a.died ? ' · failed at ' + a.died : ''}</small></div>${st(a.status)}</div>`).join('')}</div>`;
}
function resourceBlock() {
  const pool = S().pools['SECURITY_POSITION_ABC'], u = S().pool_usage['SECURITY_POSITION_ABC'];
  if (!pool) return '';
  const w = x => (100 * x / pool.total) + '%';
  return `<h3>Resource: ABC position</h3><div class="bar"><i style="width:${w(u.consumed)};background:var(--ok)"></i><i style="width:${w(u.reserved)};background:var(--navy)"></i><i style="width:${w(u.held_uncertain)};background:var(--unk)"></i></div>
  <div class="legend"><span style="--c:var(--navy)">Reserved ${fmt(u.reserved)}</span><span style="--c:var(--unk)">Held uncertain ${fmt(u.held_uncertain)}</span><span style="--c:var(--ok)">Delivered ${fmt(u.consumed)}</span><span style="--c:var(--soft)">Available ${fmt(u.available)}</span></div>
  ${Object.values(S().reservations).map(r => `<div class="agent"><div><b class="mono">${esc(r.id)}</b><small>${esc(r.obligation)} · ${fmt(r.qty)}</small></div>${st(r.status)}</div>`).join('')}`;
}
function commitmentChain() {
  const cs = Object.values(S().commitments);
  if (!cs.length) return '<p class="note">No instruction has crossed the external boundary.</p>';
  return `<div class="chain">${cs.map(c => {
    const ex = S().executions['EXEC-' + c.id];
    const evs = (c.evidence || []).map(id => S().evidence[id]).filter(Boolean);
    const extRec = V.external.instructions[c.id];
    return `<div class="link"><div class="top2"><b class="mono">${esc(c.id)}</b>${st(c.status)}</div><small>by ${esc(role(c.by))} under ${esc(c.decision || 'no decision')} · sent ${hm(c.sent_at)}</small>
      ${ex ? `<div class="top2" style="margin-top:6px"><span>Execution <span class="mono">${esc(ex.id)}</span></span>${st(ex.status)}</div>` : ''}
      ${evs.map(e => `<div class="top2"><small>Evidence <span class="mono">${esc(e.id)}</span> from ${esc(role(e.source))}</small>${st(e.verified ? 'VERIFIED' : 'UNVERIFIED')}</div>`).join('')}
      ${extRec ? `<div class="top2" style="margin-top:6px"><small>External truth</small>${st(extRec.settled ? 'SETTLED' : extRec.rejected ? 'REJECTED' : 'UNKNOWN', extRec.settled ? 'Settled at gateway' : extRec.rejected ? 'Rejected at gateway' : 'Not settled')}</div>` : '<small>Not received by gateway</small>'}</div>`;
  }).join('')}</div>`;
}

/* ---------------------------------------------------------------- views */
function vHome() {
  return `<section class="hero"><div class="eyebrow">AI Life Authority Model v0.4 · Reference domain: post-trade exception repair</div>
  <h1>What happens to financial authority and obligations when the agent performing the work fails?</h1>
  <p class="lede">AI Life separates agent capability from authority, obligations from workers, external commitments from assumed outcomes, and execution from evidence. This prototype runs a settlement exception through a hostile sequence of failures and shows whether those boundaries hold.</p>
  <div class="btns"><a class="btn primary" href="#/story">Run hostile scenario</a><a class="btn" href="#/lab">Open Failure Lab</a><a class="btn" href="#/tower">Control Tower</a></div></section>
  <div class="grid g2" style="margin-top:26px">
   <div class="card"><h2>Hypothesis</h2><p class="quote">Financial workflows involving autonomous agents are more resilient when obligation state, delegated authority, resource reservations, external commitments and recovery ownership persist independently of individual agents.</p>
    <p class="note" style="margin-top:12px">Switch the top-right toggle to <b>Baseline</b> to run the same hostile scenario with agent-owned state: no succession, no duplicate guard, no central invariant enforcement. The baseline is a constructed experimental comparison, not a claim about any commercial agent system.</p>
    <div class="btns" style="margin-top:10px"><a class="btn primary" href="#/story">Run experiment</a></div></div>
   <div class="card"><h2>Acceptance condition</h2><p class="quote">An agent may disappear, but no financial obligation, authority history, external commitment or recovery responsibility disappears with it.</p>
    <h3>This prototype does not</h3><p class="note">Predict settlement failure, optimise collateral, replace a CSD or custody platform, determine legal title or settlement finality, perform real trades or payments, give investment advice, or prove regulatory compliance.</p></div>
  </div>`;
}

function vTower() {
  const m = V.metrics, ic = invCounts(), o = obl();
  const obs = Object.values(S().obligations);
  const active = obs.filter(x => !['RESOLVED', 'CLOSED'].includes(x.status)).length;
  const rec = obs.filter(x => x.status === 'RECOVERY_REQUIRED').length;
  const unc = Object.values(S().commitments).filter(c => ['SENT', 'ACKNOWLEDGED', 'OUTCOME_UNKNOWN'].includes(c.status));
  const decisions = EV.filter(e => CONTROL.has(e.type)).slice(-8).reverse();
  return `<div class="pagehead"><div><h1>Control Tower</h1><p>Active obligations and the health of the control plane.</p></div></div>
  <div class="grid g5">
   <div class="card metric"><div class="k">Active obligations</div><div class="v">${active}</div><div class="d">${obs.length} on record</div></div>
   <div class="card metric"><div class="k">Recovery cases</div><div class="v ${rec ? 'warnc' : ''}">${rec}</div><div class="d">${m.recovery_assignments} recovery assignments</div></div>
   <div class="card metric"><div class="k">Uncertain commitments</div><div class="v ${unc.length ? 'unkc' : ''}">${unc.length}</div><div class="d">${m.uncertainty_minutes} min spent unknown</div></div>
   <div class="card metric"><div class="k">Blocked actions</div><div class="v">${m.blocked_total}</div><div class="d">${m.duplicates_blocked} duplicates · ${m.unauthorized_attempted} unauthorised</div></div>
   <div class="card metric"><div class="k">Invariant health</div><div class="v ${ic.fail ? 'badc' : 'okc'}">${ic.pass}/${ic.total}</div><div class="d">${ic.fail} failing · ${ic.unk} unknown</div></div>
  </div>
  <div class="grid g2" style="margin-top:14px">
   <div class="card"><h2>Current cases</h2><div class="scroll"><table><thead><tr><th>Case</th><th>Type</th><th>State</th><th>Owner</th><th>Deadline</th></tr></thead><tbody>
    ${o ? obs.map(x => `<tr class="click" onclick="location.hash='#/case'"><td class="mono">${x.id === 'OBL-10022' ? 'T12345' : esc(x.id)}</td><td>SSI mismatch</td><td>${st(x.status)}</td><td>${esc(role(x.owner) || 'none')}</td><td>15:30</td></tr>`).join('') : '<tr><td colspan="5" class="note">No exception detected yet. Start the Demo Story or open the Failure Lab.</td></tr>'}
    </tbody></table></div></div>
   <div class="card"><h2>External uncertainty</h2>${unc.length ? unc.map(c => `<div class="agent"><div><b class="mono">${esc(c.id)}</b><small>Instruction to settlement gateway, sent ${hm(c.sent_at)}</small></div>${st(c.status)}</div>`).join('') : '<p class="note">No unresolved external commitments.</p>'}
    <h3>Simulator metrics</h3><dl class="kv"><dt>Agent failures</dt><dd>${m.agent_failures}</dd><dt>Gate escalations</dt><dd>${m.escalations}</dd><dt>Duplicate executions completed</dt><dd class="${m.duplicates_completed ? 'badc' : ''}">${m.duplicates_completed}</dd><dt>Time to reconciliation</dt><dd>${m.time_to_reconciliation == null ? '—' : m.time_to_reconciliation + ' min'}</dd></dl>
    <p class="note">Simulator results only. They do not demonstrate real-world risk reduction.</p></div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Recent control decisions</h2>${decisions.length ? `<div class="scroll"><table><tbody>${decisions.map(e => `<tr><td class="mono">${String(e.seq).padStart(3, '0')}</td><td>${hm(e.t)}</td><td>${esc(describe(e))}${e.payload && e.payload.detail ? `<div class="note">${esc(e.payload.detail)}</div>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '<p class="note">None yet.</p>'}</div>`;
}

function vCase() {
  const o = obl();
  if (!o) return `<div class="pagehead"><div><h1>Case T12345</h1><p>No settlement exception has been detected yet.</p></div></div><div class="card"><p>Run the first chapter of the Demo Story to create the obligation.</p><div class="btns"><button class="btn primary" data-action="next">Run chapter 1: Settlement exception</button></div></div>`;
  const rec = o.recovery ? S().recoveries[o.recovery] : null;
  const props = Object.values(S().proposals);
  const open = !['RESOLVED', 'CLOSED'].includes(o.status);
  return `<div class="pagehead"><div><h1>Case T12345</h1><p>Business obligation on the left, processing in the centre, control state on the right.</p></div>
   <div class="btns"><button class="btn" data-action="cmd" data-type="PROPOSE_REPAIR" ${open ? '' : 'disabled'}>Propose repair</button><button class="btn" data-action="cmd" data-type="APPROVE_REPAIR" ${open ? '' : 'disabled'}>Approve</button><button class="btn" data-action="cmd" data-type="SUBMIT_INSTRUCTION" ${open ? '' : 'disabled'}>Submit instruction</button><button class="btn" data-action="reconcile">Reconcile</button><button class="btn" data-action="cmd" data-type="RESOLVE_OBLIGATION" ${open ? '' : 'disabled'}>Resolve</button><button class="btn" data-action="cmd" data-type="CLOSE_OBLIGATION">Close</button><button class="btn" data-action="clock" data-min="1">+1 min</button><button class="btn" data-action="clock" data-min="5">+5 min</button></div></div>
  <div class="grid g3">
   <div class="card"><h2>Business obligation</h2><dl class="kv"><dt>Trade</dt><dd class="mono">T12345</dd><dt>Security</dt><dd>ABC</dd><dt>Quantity</dt><dd>${fmt(100000)}</dd><dt>Issue</dt><dd>SSI mismatch</dd><dt>Instructed account</dt><dd class="mono">${esc(V.reference.instructed_account)}</dd><dt>Current SSI</dt><dd class="mono">${esc(V.reference.current_ssi)}</dd><dt>Deadline</dt><dd>15:30 · <span class="${V.minutes_to_deadline < 30 ? 'badc' : ''}">${V.minutes_to_deadline} min left</span></dd></dl>
    <h3>Obligation <span class="mono">${esc(o.id)}</span></h3><p>${st(o.status)} ${o.outcome ? st('SETTLED', o.outcome) : ''}</p>${resourceBlock()}</div>
   <div class="card"><h2>Processing state</h2><dl class="kv"><dt>Current owner</dt><dd class="${o.owner ? '' : 'badc'}">${esc(o.owner ? role(o.owner) : 'None')}</dd><dt>Workers</dt><dd>${o.assignees.length ? o.assignees.map(role).map(esc).join(', ') : '—'}</dd><dt>Case revision</dt><dd>${o.rev}</dd></dl>
    ${rec ? `<h3>Recovery assignment <span class="mono">${esc(rec.id)}</span></h3><dl class="kv"><dt>Status</dt><dd>${st(rec.status)}</dd><dt>Owner</dt><dd>${esc(role(rec.owner))}</dd><dt>Trigger</dt><dd>${esc(role(rec.of))} failed at ${hm(rec.at)}</dd><dt>Replacement</dt><dd>${esc(rec.worker ? role(rec.worker) : '—')}</dd><dt>Required</dt><dd>${rec.required_actions.map(x => x.replace(/_/g, ' ').toLowerCase()).join('; ')}</dd></dl>` : ''}
    <h3>Proposals</h3>${props.length ? props.map(p => { const d = p.decision ? S().decisions[p.decision] : null; return `<div class="link" style="margin-bottom:6px"><div class="top2"><b class="mono">${esc(p.id)}</b>${st(p.status)}</div><small>${esc(role(p.by))}: ${esc(p.change.field)} ${esc(p.change.oldValue)} → ${esc(p.change.newValue)} · ${fmt(p.qty)}${p.approvals.length ? ' · approved by ' + p.approvals.map(a => role(a.by)).join(', ') : ''}</small>${d ? `<div class="top2"><small>Gate ${esc(d.id)}: ${esc(d.reason.replace(/_/g, ' ').toLowerCase())}${d.decision === 'AUTHORIZE' ? ' · valid to ' + hm(d.valid_until) : ''}</small>${st(d.decision)}</div>` : ''}</div>`; }).join('') : '<p class="note">None.</p>'}</div>
   <div class="card"><h2>Control state</h2><h3 style="margin-top:0">External commitments</h3>${commitmentChain()}
    <h3>Invariants</h3>${(() => { const ic = invCounts(); return `<p>${st(ic.fail ? 'FAIL' : 'PASS', ic.fail ? ic.fail + ' failing' : 'All holding')} ${ic.unk ? st('UNKNOWN', ic.unk + ' unknown') : ''} <a href="#/invariants" class="note">Open monitor</a></p>`; })()}
    <h3>Last step</h3>${callsList(V.lastStep && V.lastStep.calls)}</div>
  </div><div style="margin-top:14px">${agentsCard()}</div>`;
}

function layoutTree(L) {
  /* Indented outline: depth sets x, reading order sets y. Fits any width and reads top-down. */
  const kids = {}; L.edges.forEach(e => (kids[e.from] = kids[e.from] || []).push(e.to));
  const byId = Object.fromEntries(L.nodes.map(n => [n.id, n]));
  const pos = {}, W = 360, H = 66, GAP = 12, INDENT = 44; let row = 0;
  (function place(id, d) { pos[id] = { x: d * INDENT, y: row * (H + GAP), d }; row++; (kids[id] || []).forEach(c => place(c, d + 1)); })(L.nodes[0].id, 0);
  const maxX = Math.max(...Object.values(pos).map(p => p.x)) + W;
  return { pos, byId, W, H, width: maxX + 24, height: row * (H + GAP) + 12, kids };
}
function vAuthority() {
  const L = V.lineage, T = layoutTree(L);
  const sel = T.byId[ui.authSel] || null;
  const allActions = [...new Set(L.nodes.flatMap(n => n.actions || []))];
  const edges = L.edges.map(e => { const a = T.pos[e.from], b = T.pos[e.to]; if (!a || !b) return ''; const x1 = a.x + 10 + 18, y1 = a.y + 10 + T.H, x2 = b.x + 10, y2 = b.y + 10 + T.H / 2; return `<path class="edge" d="M${x1},${y1} V${y2} H${x2}"/>`; }).join('');
  const nodes = L.nodes.map(n => { const p = T.pos[n.id]; if (!p) return ''; const cls = [n.status === 'REVOKED' ? 'revoked' : '', n.status === 'EXPIRED' ? 'expired' : '', n.holder_status === 'FAILED' ? 'failed' : '', ui.authSel === n.id ? 'sel' : ''].join(' ');
    return `<g class="node ${cls}" data-action="auth" data-id="${esc(n.id)}" transform="translate(${p.x + 10},${p.y + 10})" tabindex="0" role="button" aria-label="${esc(n.label)} ${esc(n.id)}"><rect width="${T.W}" height="${T.H}" rx="10"/><text class="t" x="12" y="22">${esc(n.label)}</text><text class="s" x="12" y="40">${esc(n.type === 'principal' ? 'Principal · origin of authority' : n.id + ' · ' + n.status + (n.holder_status === 'FAILED' ? ' · agent failed' : ''))}</text><text class="s" x="12" y="57">${esc(n.type === 'principal' ? '' : (n.actions || []).slice(0, 3).join(', ') + ((n.actions || []).length > 3 ? '…' : ''))}</text></g>`; }).join('');
  return `<div class="pagehead"><div><h1>Authority lineage</h1><p>Edges mean <i>delegated from</i>, never organisational reporting. Dashed boxes are revoked or expired; red borders mark grants held by failed agents.</p></div></div>
  <div class="grid" style="grid-template-columns:minmax(0,1fr) 360px"><div class="svgwrap"><svg width="${T.width}" height="${T.height}" viewBox="0 0 ${T.width} ${T.height}" role="img" aria-label="Authority lineage graph">${edges}${nodes}</svg></div>
  <div class="card"><h2>Inspector</h2>${sel && sel.type === 'authority' ? `<dl class="kv"><dt>Authority</dt><dd class="mono">${esc(sel.id)}</dd><dt>Holder</dt><dd>${esc(sel.label)} ${st(sel.holder_status)}</dd><dt>Source</dt><dd class="mono">${esc((L.edges.find(e => e.to === sel.id) || {}).from)}</dd><dt>Status</dt><dd>${st(sel.status)} ${sel.reason ? '<span class="note">' + esc(sel.reason.replace(/_/g, ' ').toLowerCase()) + '</span>' : ''}</dd><dt>Scope</dt><dd>${Object.entries(sel.scope).map(([k, v]) => esc(k) + ': ' + esc(v.join(', '))).join('; ') || 'unrestricted'}</dd><dt>Permitted actions</dt><dd>${esc(sel.actions.join(', '))}</dd><dt>Forbidden actions</dt><dd>${esc(allActions.filter(a => !sel.actions.includes(a)).join(', ') || '—')}</dd><dt>Quantity limit</dt><dd>${sel.max_qty ? fmt(sel.max_qty) : 'none'}</dd><dt>Expiry</dt><dd>${esc(sel.valid_until || 'none')}</dd><dt>May delegate</dt><dd>${sel.may_delegate ? 'Yes: ' + esc(sel.grant_actions.join(', ')) : 'No'}</dd></dl>` : '<p class="note">Select an authority in the graph to see its source, scope, permitted and forbidden actions, limit, expiry, delegation permission and status.</p>'}</div></div>`;
}

function vTimeline() {
  const f = ui.evFilter;
  const list = EV.filter(e => e.type !== 'CLOCK_ADVANCED' && (f === 'all' || (f === 'control' && CONTROL.has(e.type)) || (f === 'failures' && FAILEV.has(e.type))));
  const sel = EV.find(e => e.seq === ui.evSel);
  return `<div class="pagehead"><div><h1>Event timeline</h1><p>Append-only and hash-chained. Sorted by sequence number, not timestamp. ${V.log_intact ? st('PASS', 'Hash chain intact') : st('FAIL', 'Hash chain broken')}</p></div>
   <div class="filters" role="group" aria-label="Filter">${[['all', 'All events'], ['control', 'Control decisions'], ['failures', 'Failures']].map(([k, l]) => `<button data-action="evfilter" data-f="${k}" aria-pressed="${f === k}">${l}</button>`).join('')}</div></div>
  <div class="grid" style="grid-template-columns:minmax(0,1fr) 340px"><div class="card scroll"><table><thead><tr><th>Seq</th><th>Time</th><th>Event</th><th>Actor</th><th>Result</th></tr></thead><tbody>
   ${list.map(e => `<tr class="click ${ui.evSel === e.seq ? 'sel' : ''}" data-action="ev" data-seq="${e.seq}"><td class="mono">${String(e.seq).padStart(3, '0')}</td><td>${hm(e.t)}</td><td>${esc(describe(e))}</td><td>${esc(role(e.actor))}</td><td>${e.type === 'COMMAND_REJECTED' ? st('BLOCKED') : e.type === 'INVARIANT_VIOLATED' ? st('FAIL', 'Violation') : e.type === 'ENTITY_DIED' ? st('FAILED') : e.type === 'EXTERNAL_OUTCOME_UNKNOWN' ? st('UNKNOWN') : ''}</td></tr>`).join('')}
  </tbody></table></div>
  <div class="card"><h2>Event inspector</h2>${sel ? `<dl class="kv"><dt>Sequence</dt><dd class="mono">${sel.seq}</dd><dt>Simulated time</dt><dd>${hm(sel.t)}</dd><dt>Type</dt><dd class="mono">${esc(sel.type)}</dd><dt>Actor</dt><dd>${esc(role(sel.actor))}</dd><dt>Subject</dt><dd class="mono">${esc(sel.subject)}</dd><dt>Hash</dt><dd class="mono">${esc(sel.hash)}</dd><dt>Previous</dt><dd class="mono">${esc(sel.prev)}</dd></dl><h3>Payload</h3><pre class="mono" style="white-space:pre-wrap;background:var(--soft);padding:10px;border-radius:8px;margin:0">${esc(JSON.stringify(sel.payload, null, 2))}</pre>` : '<p class="note">Select an event.</p>'}</div></div>`;
}

function vInvariants() {
  const rows = [...V.invariants, ...V.domain.map(d => ({ ...d, severity: 'DOMAIN', blocked: 0, how: 'Evaluated against the settlement gateway’s own records, not the control plane.' }))];
  const ic = invCounts(), sel = rows.find(r => r.id === ui.invSel) || rows[0];
  return `<div class="pagehead"><div><h1>Invariant monitor</h1><p>I01–I20 from the specification, plus two checks against external truth. UNKNOWN is shown separately from PASS.</p></div>
   <div class="btns">${st('PASS', ic.pass + ' healthy')} ${st('FAIL', ic.fail + ' failed')} ${st('UNKNOWN', ic.unk + ' unknown')}</div></div>
  <div class="grid" style="grid-template-columns:minmax(0,1fr) 380px"><div class="card">${rows.map(r => `<div class="inv ${sel.id === r.id ? 'sel' : ''}" data-action="inv" data-id="${r.id}" tabindex="0" role="button"><span class="mono">${r.id}</span><span>${esc(r.name)}${r.blocked ? `<span class="blk"> · blocked ${r.blocked}×</span>` : ''}</span>${st(r.status)}</div>`).join('')}</div>
  <div class="card"><h2><span class="mono">${sel.id}</span> ${esc(sel.name)}</h2><dl class="kv"><dt>Status</dt><dd>${st(sel.status)}</dd><dt>Severity</dt><dd>${esc(sel.severity)}</dd><dt>Attempts blocked</dt><dd>${sel.blocked}</dd></dl>
   <h3>How it is enforced</h3><p class="note" style="font-size:.9rem">${esc(sel.how)}</p>${sel.detail && sel.detail.length ? `<h3>Detail</h3><ul>${sel.detail.map(d => `<li class="${sel.status === 'FAIL' ? 'badc' : 'unkc'}">${esc(d)}</li>`).join('')}</ul>` : ''}
   ${(() => { const blocks = EV.filter(e => e.type === 'COMMAND_REJECTED').slice(-3).reverse(); return blocks.length ? `<h3>Most recent blocks</h3>${blocks.map(e => `<div class="link" style="margin-bottom:6px"><div class="top2"><b class="mono">${esc(e.payload.code)}</b><small>${hm(e.t)}</small></div><small>${esc(role(e.actor))}: ${esc(e.payload.detail)}</small></div>`).join('')}` : ''; })()}</div></div>`;
}

const FTITLE = { KILL_AGENT: 'Kill repair agent', KILL_CONTROLLER: 'Kill Exception Controller', REVOKE_AUTHORITY: 'Revoke authority', EXPIRE_AUTHORITY: 'Expire authority', LOSE_ACKNOWLEDGEMENT: 'Lose acknowledgement', CHANGE_SSI: 'Change SSI', CREATE_CONFLICTING_PROPOSAL: 'Conflicting repair', ATTEMPT_QUANTITY_ESCALATION: 'Increase quantity', ATTEMPT_DUPLICATE_SUBMISSION: 'Duplicate submission', USE_STALE_AUTHORIZATION: 'Stale authorization', FAKE_SUCCESS: 'Fake confirmation', DOUBLE_RESERVE_RESOURCE: 'Double reserve', REMOVE_OWNER: 'Remove owner', DISCONNECT_STATUS_API: 'Disconnect external status', RECONNECT_STATUS_API: 'Reconnect external status' };
const CONSEQ = new Set(['OBLIGATION_SUCCEEDED', 'OBLIGATION_ABANDONED', 'AUTHORITY_REVOKED', 'AUTHORITY_EXPIRED', 'RECOVERY_CREATED', 'RESERVATION_HELD_UNCERTAIN', 'RESERVATION_RELEASED', 'PROPOSAL_EXPIRED', 'OBLIGATION_STATUS', 'PROPOSAL_ESCALATED', 'PROPOSAL_REJECTED', 'OBSERVATION_RECORDED', 'EXTERNAL_OUTCOME_UNKNOWN', 'WORKER_LOST', 'INVARIANT_VIOLATED']);
function consequences(step) {
  const evs = (step && step.events || []).filter(e => CONSEQ.has(e.type));
  return evs.length ? `<h3>What the control plane did in response</h3><ul class="calls">${evs.map(e => `<li>${st(e.type === 'INVARIANT_VIOLATED' ? 'FAIL' : 'ACTIVE', e.type === 'INVARIANT_VIOLATED' ? 'Violation' : 'Kernel')}<span>${esc(describe(e))}</span></li>`).join('')}</ul>` : '';
}
function vLab() {
  const ft = V.failure_types, ext = V.external;
  const order = ['KILL_AGENT', 'KILL_CONTROLLER', 'REVOKE_AUTHORITY', 'EXPIRE_AUTHORITY', 'LOSE_ACKNOWLEDGEMENT', 'CHANGE_SSI', 'CREATE_CONFLICTING_PROPOSAL', 'ATTEMPT_QUANTITY_ESCALATION', 'ATTEMPT_DUPLICATE_SUBMISSION', 'USE_STALE_AUTHORIZATION', 'FAKE_SUCCESS', 'DOUBLE_RESERVE_RESOURCE', 'REMOVE_OWNER', ext.status_api_up ? 'DISCONNECT_STATUS_API' : 'RECONNECT_STATUS_API'];
  return `<div class="pagehead"><div><h1>Failure Lab</h1><p>Deliberate chaos. Every injection is itself recorded as an event, so even failures are auditable. No confirmation dialogues: this is a demo environment.</p></div><div class="btns"><button class="btn" data-action="reset">Reset simulator</button></div></div>
  ${!obl() ? '<div class="card" style="margin-bottom:14px"><b>Tip:</b> most failures need a live case. Run the Demo Story to chapter 4 or 6 first, then come back here.</div>' : ''}
  <div class="grid" style="grid-template-columns:minmax(0,1fr) 340px"><div><div class="lab">${order.map(k => `<button class="fbtn" data-action="fail" data-f="${k}"><b>${esc(FTITLE[k] || k)}</b><small>${esc(ft[k])}</small></button>`).join('')}</div>
   <div class="card" style="margin-top:14px"><h2>Result of last injection</h2>${V.lastStep && V.lastStep.chapter === 'Failure Lab' ? callsList(V.lastStep.calls) + consequences(V.lastStep) : '<p class="note">Inject a failure to see how the control plane responds.</p>'}</div></div>
  <div><div class="card"><h2>Simulation clock</h2><div class="metric"><div class="v">${V.clock}</div><div class="d">Deadline 15:30 · ${V.minutes_to_deadline} min left · repair authority expires 15:00</div></div>
   <div class="btns" style="margin-top:10px"><button class="btn" data-action="clock" data-min="1">+1m</button><button class="btn" data-action="clock" data-min="5">+5m</button><button class="btn" data-action="clock" data-min="30">+30m</button><button class="btn danger" data-action="deadline">Advance to deadline</button></div></div>
   <div class="card" style="margin-top:14px"><h2>External gateway</h2><dl class="kv"><dt>Next response</dt><dd>${st(ext.next_mode === 'NORMAL_ACK' ? 'ACTIVE' : 'WARN', ext.next_mode)}</dd><dt>Status API</dt><dd>${st(ext.status_api_up ? 'ACTIVE' : 'FAILED', ext.status_api_up ? 'Up' : 'Down')}</dd><dt>Firm ABC position</dt><dd>${fmt(ext.position.ABC)}</dd><dt>Instructions received</dt><dd>${Object.keys(ext.instructions).length}</dd></dl>
   <label class="note" for="mode">Set next response</label><div class="btns"><select id="mode">${V.external_modes.map(m => `<option ${m === ext.next_mode ? 'selected' : ''}>${m}</option>`).join('')}</select><button class="btn" data-action="mode">Apply</button></div></div>
   <div class="card" style="margin-top:14px"><h2>Failure history</h2>${V.failures.length ? V.failures.slice().reverse().map(f => `<div class="link" style="margin-bottom:6px"><div class="top2"><b>${esc(f.label)}</b><small>${f.clock}</small></div>${f.results.map(r => `<small>${r.ok ? '✓' : '✕ ' + esc(r.result)} ${esc(r.label)}</small><br>`).join('')}</div>`).join('') : '<p class="note">No failures injected yet.</p>'}</div></div></div>`;
}

const STORY_BASE = {
  7: 'In the baseline the task lived inside the agent. It dies, the obligation has no owner, and the reservation is released while the outcome is unknown.',
  8: 'The replacement finds an unresolved exception but has no record of what its predecessor already sent.',
  10: 'With no central duplicate guard, the resubmission reaches the gateway. The original had already settled.',
  13: 'Nobody owns the obligation, so it cannot be resolved or closed. The duplicate delivery must be recalled by hand.'
};
function vStory() {
  const n = V.chapter, ch = V.chapters, steps = V.steps.filter(s => ch.includes(s.chapter));
  const cur = steps[steps.length - 1];
  const o = obl();
  return `<div class="pagehead"><div><h1>Demo Story: hostile scenario</h1><p>${V.architecture === 'BASELINE' ? '<b class="badc">Baseline mode.</b> Agent-owned state, no succession, no duplicate guard, invariants recorded but not enforced.' : 'AI Life mode. The control plane owns obligations, authority, commitments and recovery.'}</p></div>
   <div class="btns"><button class="btn primary" data-action="next" ${n >= ch.length ? 'disabled' : ''}>Next step</button><button class="btn" data-action="auto">${auto ? 'Stop auto run' : 'Auto run'}</button><button class="btn" data-action="reset">Reset</button></div></div>
  <div class="story"><div class="card"><ol class="chapters">${ch.map((c, i) => `<li class="${i < n ? 'done' : ''} ${i === n - 1 ? 'cur' : ''}"><span class="n">${i + 1}</span>${esc(c)}</li>`).join('')}</ol></div>
  <div class="grid">
   <div class="card narr">${cur ? `<div class="eyebrow">Chapter ${n} of ${ch.length} · ${esc(cur.chapter)} · ${cur.clock}</div><h2>${esc(cur.title)}</h2><p>${esc(cur.narrative)}</p>` : `<div class="eyebrow">Ready · 10:15 · 16 September 2026</div><h2>Trade T12345 is about to fail settlement</h2><p>100,000 ABC, SSI mismatch, deadline 15:30. Press <b>Next step</b> to walk through thirteen chapters: normal processing, then acknowledgement loss, agent failure, a duplicate attempt, and recovery.</p>`}</div>
   <div class="grid g3">
    <div class="card"><h2>Obligation</h2>${o ? `<p class="mono">${o.id}</p><p>${st(o.status)}</p><dl class="kv"><dt>Owner</dt><dd class="${o.owner ? '' : 'badc'}">${esc(o.owner ? role(o.owner) : 'None')}</dd><dt>Worker</dt><dd>${esc(o.assignees.map(role).join(', ') || '—')}</dd></dl>${resourceBlock()}` : '<p class="note">Not yet created.</p>'}</div>
    <div class="card"><h2>External commitment</h2>${commitmentChain()}</div>
    <div class="card"><h2>What the control plane did</h2>${callsList(cur && cur.calls)}${V.lastStep && cur && V.lastStep.title === cur.title ? consequences(V.lastStep) : ''}</div>
   </div>
   <div class="grid g2">${agentsCard()}<div class="card"><h2>Invariants</h2>${[...V.invariants, ...V.domain].filter(r => r.status !== 'PASS' || r.blocked).map(r => `<div class="inv" data-action="goinv" data-id="${r.id}"><span class="mono">${r.id}</span><span>${esc(r.name)}${r.blocked ? `<span class="blk"> · blocked ${r.blocked}×</span>` : ''}</span>${st(r.status)}</div>`).join('') || '<p class="note">All twenty invariants and both external checks hold. Nothing has been blocked yet.</p>'}<p class="note" style="margin-top:8px">${invCounts().pass} of ${invCounts().total} holding. <a href="#/invariants">Full monitor</a></p></div></div>
  </div></div>`;
}

function vArchitecture() {
  return `<div class="pagehead"><div><h1>Architecture</h1><p>AI Life decides who may act and what must survive failure. Gate Symphony decides whether a specific action is permissible. They meet only at the execution boundary.</p></div></div>
  <div class="grid g2"><div class="card"><div class="arch">
   <div class="box">Agents<small>Repair, SSI, reference data, reviewer, gateway, reconciliation</small></div><div class="dn">↓ proposals</div>
   <div class="box core">AI Life control plane<small>Mother kernel · deterministic · event-sourced</small><ul><li>Authority</li><li>Obligations</li><li>Resources</li><li>Invariants</li><li>Commitments</li><li>Recovery</li></ul></div><div class="dn">↓ proposal + state</div>
   <div class="box">Gate Symphony<small>authorise · reject · attenuate · escalate · defer (mocked)</small></div><div class="dn">↓ authorised instruction</div>
   <div class="box">Execution adapter</div><div class="dn">↓</div><div class="box">External system<small>Settlement gateway simulator, its own truth</small></div><div class="dn">↓</div>
   <div class="box">Evidence<small>verified only from the external source</small></div><div class="dn">↑ back into AI Life</div></div></div>
  <div class="card"><h2>What the model distinguishes</h2><dl class="kv"><dt>What an agent CAN do</dt><dd>Capability</dd><dt>What an agent MAY do</dt><dd>Authority grant</dd><dt>What MUST be resolved</dt><dd>Obligation</dd><dt>What is scarce</dt><dd>Resource and reservation</dd><dt>What an agent wants</dt><dd>Proposal</dd><dt>What control permits</dt><dd>Gate decision</dd><dt>What crossed the boundary</dt><dd>Commitment</dd><dt>What actually happened</dt><dd>Execution</dd><dt>Why we believe it</dt><dd>Evidence</dd><dt>What happens after failure</dt><dd>Recovery assignment</dd><dt>How it connects</dt><dd>Lineage</dd></dl>
  <h3>Implementation notes</h3><p class="note" style="font-size:.9rem">The control plane is the AI Life Mother kernel: every command is an atomic transaction that commits its effects and events together or rolls back leaving a rejection event. State is rebuilt by replaying the command journal, which must reproduce the event hash chain exactly (invariant I14). No language model decides authority, state transitions, commitment status, recovery ownership or invariant outcomes.</p></div></div>`;
}

/* ---------------------------------------------------------------- render & events */
function route() { return (location.hash.replace(/^#\/?/, '') || '').split('?')[0]; }
function render() {
  if (!V) return;
  const r = route();
  $('#nav').innerHTML = ROUTES.map(([k, l]) => `<a href="#/${k}" ${r === k ? 'aria-current="page"' : ''}>${l}</a>`).join('');
  $('#arch-ai').setAttribute('aria-pressed', V.architecture === 'AI_LIFE');
  $('#arch-base').setAttribute('aria-pressed', V.architecture === 'BASELINE');
  $('#clock').innerHTML = `${V.clock}<small>16 Sep 2026 · deadline 15:30</small>`;
  const views = { '': vHome, tower: vTower, case: vCase, authority: vAuthority, timeline: vTimeline, invariants: vInvariants, lab: vLab, story: vStory, architecture: vArchitecture };
  $('#view').innerHTML = (views[r] || vHome)();
}
async function nextChapter() {
  const d = await api('POST', '/api/scenario/next');
  if (d && d.lastStep) summarise(d.lastStep);
  if (d && !d.lastStep && auto) stopAuto();
}
function stopAuto() { clearInterval(auto); auto = null; render(); }
document.addEventListener('click', async ev => {
  const t = ev.target.closest('[data-action]'); if (!t) return;
  const a = t.dataset.action;
  if (a === 'next') nextChapter();
  else if (a === 'auto') { if (auto) stopAuto(); else { auto = setInterval(() => { if (V.chapter >= V.chapters.length) stopAuto(); else nextChapter(); }, 2600); nextChapter(); } }
  else if (a === 'reset') { if (auto) stopAuto(); await api('POST', '/api/simulator/reset', { architecture: V.architecture }); toast('Simulator reset to the canonical seed case'); }
  else if (a === 'arch') { if (auto) stopAuto(); await api('POST', '/api/cases', { architecture: t.dataset.arch }); toast(t.dataset.arch === 'BASELINE' ? 'Baseline: agent-owned state. Case reset.' : 'AI Life control plane. Case reset.'); }
  else if (a === 'fail') { const d = await api('POST', `/api/cases/${CASE}/failures`, { failureType: t.dataset.f }); if (d) summarise(d.lastStep); }
  else if (a === 'cmd') { const d = await api('POST', `/api/cases/${CASE}/commands`, { type: t.dataset.type }); if (d) summarise(d.lastStep); }
  else if (a === 'reconcile') { const d = await api('POST', `/api/cases/${CASE}/reconcile`); if (d) summarise(d.lastStep); }
  else if (a === 'clock') { await api('POST', '/api/simulator/clock', { action: 'ADVANCE', minutes: +t.dataset.min }); }
  else if (a === 'deadline') { await api('POST', '/api/simulator/clock', { action: 'ADVANCE_TO_DEADLINE' }); }
  else if (a === 'mode') { const d = await api('POST', `/api/cases/${CASE}/commands`, { type: 'SET_EXTERNAL_MODE', payload: { mode: $('#mode').value } }); if (d) summarise(d.lastStep); }
  else if (a === 'auth') { ui.authSel = t.dataset.id; render(); }
  else if (a === 'inv') { ui.invSel = t.dataset.id; render(); }
  else if (a === 'goinv') { ui.invSel = t.dataset.id; location.hash = '#/invariants'; }
  else if (a === 'ev') { ui.evSel = +t.dataset.seq; render(); }
  else if (a === 'evfilter') { ui.evFilter = t.dataset.f; render(); }
});
document.addEventListener('keydown', ev => { if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches('[role="button"][data-action]')) { ev.preventDefault(); ev.target.click(); } });
window.addEventListener('hashchange', () => { render(); $('#view').focus({ preventScroll: true }); window.scrollTo(0, 0); });
(async () => { await api('GET', `/api/cases/${CASE}`); })();
