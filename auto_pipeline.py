# -*- coding: utf-8 -*-
"""
Autonomous orchestration - no human gates during the run.
Flow: P0 -> A1 -> (A2 | A3 | A4 in parallel) -> A5 -> real hands -> A7 consolidated review.
Every agent output is LOCKED while the pipeline runs; the human unlocks and edits
once, at the final review, and approves.

Everything is emitted twice: to the web UI (SSE) and to the VS Code terminal (ANSI).
"""
import asyncio, json, time
from foundry import client, run_agent, parse_json
from agents_def import AGENTS
import hands

C = {"amber": "\033[93m", "teal": "\033[96m", "ok": "\033[92m", "red": "\033[91m",
     "violet": "\033[95m", "blue": "\033[94m", "dim": "\033[90m", "b": "\033[1m", "x": "\033[0m"}
W = 78

_pc = None
def _client():
    global _pc
    if _pc is None:
        _pc = client()
    return _pc


def _stamp():
    return time.strftime("%H:%M:%S")


def _box(title, lines):
    print(f"\n{C['violet']}╭{'─' * W}╮{C['x']}")
    print(f"{C['violet']}│{C['x']} {C['b']}{title:<{W-2}}{C['x']} {C['violet']}│{C['x']}")
    for ln in lines:
        print(f"{C['violet']}│{C['x']} {C['dim']}{ln[:W-2]:<{W-2}}{C['x']} {C['violet']}│{C['x']}")
    print(f"{C['violet']}╰{'─' * W}╯{C['x']}", flush=True)


async def _log(emit, line, level="info", color=None, term_line=None):
    """Emit a line to the browser terminal AND print it to the VS Code terminal."""
    print(f"{C['dim']}{_stamp()}{C['x']}  {color or ''}{term_line or line}{C['x']}", flush=True)
    await emit({"type": "log", "ts": _stamp(), "line": line, "level": level})


async def _call(emit, key, message):
    """Run one agent on a worker thread, emitting running/done/error + terminal lines."""
    spec = AGENTS[key]
    name, display = spec["name"], spec["display"]
    await emit({"type": "stage", "agent": key, "status": "running"})
    await _log(emit, f"[{key}] {display} → invoking {name} …", "agent", C["amber"],
               term_line=f"▶ {key:<3} {display:<28} invoking {name} …")
    t0 = time.monotonic()
    try:
        text = await asyncio.to_thread(run_agent, _client(), name, message)
        out = parse_json(text)
        ms = int((time.monotonic() - t0) * 1000)
        await emit({"type": "stage", "agent": key, "status": "done", "ms": ms, "output": out})
        await _log(emit, f"[{key}] done in {ms/1000:.1f}s · output locked 🔒", "ok", C["ok"],
                   term_line=f"✔ {key:<3} {display:<28} {ms/1000:>6.1f}s  🔒 locked")
        first = next(iter(out.items()), None)
        if first:
            v = first[1] if not isinstance(first[1], list) else " · ".join(map(str, first[1]))
            await _log(emit, f"       {first[0]}: {v}", "dim", C["dim"],
                       term_line=f"      {C['dim']}{first[0]}: {str(v)[:60]}")
        return out
    except Exception as e:
        await emit({"type": "stage", "agent": key, "status": "error", "error": str(e)})
        await _log(emit, f"[{key}] ERROR {e}", "err", C["red"],
                   term_line=f"✖ {key:<3} {display:<28} ERROR: {str(e)[:40]}")
        raise


def norm_escalations(a8):
    """A8 may return escalations as an array, a single object, a bare name, or nothing.
    Normalise every shape into a list of {name, email, reason}."""
    raw = a8.get("escalations")
    if raw in (None, "", [], "null", "none"):
        raw = a8.get("escalateTo")
    if raw in (None, "", [], "null", "none"):
        return []
    reasons = a8.get("escalationReason")
    reasons = [reasons] if isinstance(reasons, str) else (reasons if isinstance(reasons, list) else [])
    if isinstance(raw, (str, dict)):
        raw = [raw]
    out = []
    for i, x in enumerate(raw):
        r = reasons[i] if i < len(reasons) else (reasons[0] if len(reasons) == 1 else None)
        if isinstance(x, str):
            out.append({"name": x, "email": a8.get("escalateToEmail"), "reason": r})
        elif isinstance(x, dict):
            out.append({"name": x.get("name"), "email": x.get("email"),
                        "reason": x.get("reason") or x.get("escalationReason") or r})
    return [e for e in out if e.get("name")]


def build_problems(payload, a1, a2, a3, a4, a5, outcome):
    """Everything the agents actually detected, as a VS Code-style problem list."""
    loc = f"{payload.get('page','?')} › {payload.get('controlId','?')}"
    items = []
    for err in payload.get("consoleErrors") or []:
        items.append({"severity": "error", "message": err, "source": "Runtime capture", "location": loc})
    if a3.get("securityFlag"):
        items.append({"severity": "error", "message": f"Security risk: {a3.get('reason', 'flagged by severity agent')}",
                      "source": "A3 Severity", "location": loc})
    sev = (a3.get("aiSeverity") or "").upper()
    if sev in ("P1", "P2"):
        items.append({"severity": "error" if sev == "P1" else "warning",
                      "message": f"{sev} — {a3.get('reason','')}", "source": "A3 Severity", "location": loc})
    if a4.get("hypothesis"):
        items.append({"severity": "warning", "message": f"{a4.get('category','Root cause')}: {a4['hypothesis']}",
                      "source": "A4 Root Cause", "location": loc})
    if (a5.get("verdict") or "").lower().startswith("dup") and a5.get("matchId"):
        items.append({"severity": "warning", "message": f"Duplicate of {a5['matchId']} — {a5.get('matchBasis','')}",
                      "source": "A5 Duplicates", "location": loc})
    if str(a2.get("confidence", "")).lower() == "low":
        items.append({"severity": "warning", "message": f"Low-confidence classification: {a2.get('module')} / {a2.get('type')}",
                      "source": "A2 Classifier", "location": loc})
    for q in (a1.get("missingInfo") or []):
        items.append({"severity": "info", "message": f"Missing info: {q}", "source": "A1 Intake", "location": loc})
    if outcome.get("kind") == "ticket":
        items.append({"severity": "info", "message": f"Ticket {outcome['id']} created and awaiting human review",
                      "source": "Pipeline", "location": "register.json"})
    return items


async def run_auto(payload, emit):
    """Full autonomous run. `emit` is an async callback receiving event dicts."""
    t_start = time.monotonic()
    try:
        full_auto = bool(payload.pop("fullAuto", True))
        register = json.load(open("register.json", encoding="utf-8"))
        owners = hands.load_owners()
        pj = json.dumps(payload)

        _box("FFL AUTONOMOUS BUG PIPELINE — new capture received", [
            f"element   {payload.get('element')}",
            f"page      {payload.get('page')}",
            f"control   {payload.get('controlId')}   tag: {payload.get('tag')}",
            f"console   {'; '.join(payload.get('consoleErrors') or []) or 'none captured'}",
            f"note      \"{payload.get('userNote','')}\"",
            "mode      " + ("FULL AUTO — routed, assigned and notified with no human click"
                            if full_auto else "AUTONOMOUS — one human review at the end"),
        ])
        await _log(emit, f"capture received: {payload.get('element')} @ {payload.get('page')}", "human", C["teal"],
                   term_line="")
        await _log(emit, f'reporter note: "{payload.get("userNote","")}"', "dim", C["dim"], term_line="")
        await _log(emit, "mode: " + ("FULL AUTO — route, assign, notify and approve with no human click" if full_auto else "REVIEW MODE — one human review at the end"), "human", C["violet"], term_line="")

        p0 = await _call(emit, "P0", f"Payload:\n{pj}")
        a1 = await _call(emit, "A1", f"Payload:\n{pj}")

        await _log(emit, "parallel fan-out → A2 ‖ A3 ‖ A4 dispatched concurrently", "info", C["violet"],
                   term_line=f"{C['violet']}⇉ parallel fan-out — A2 ‖ A3 ‖ A4 dispatched concurrently")
        history = json.dumps([r for r in register if r.get("status") == "Closed" or r.get("rootCause")])
        a2, a3, a4 = await asyncio.gather(
            _call(emit, "A2", f"Payload:\n{pj}"),
            _call(emit, "A3", f"Payload:\n{pj}"),
            _call(emit, "A4", f"Payload:\n{pj}\n\nResolved-bug history for grounding:\n{history}"),
        )

        a5 = await _call(emit, "A5", f"New payload:\n{pj}\n\nOpen register:\n{json.dumps(register)}")

        # ---- real hands, fully automated ----
        outcome = {"type": "outcome"}
        verdict = (a5.get("verdict") or "").lower()
        rec = hands.link_duplicate(register, a5["matchId"]) if verdict.startswith("dup") and a5.get("matchId") else None
        if rec:
            outcome.update(kind="duplicate", id=rec["id"], title=rec.get("title"),
                           reporters=rec["reporters"], severity=rec.get("severity"))
            await _log(emit, f"DB WRITE → linked to {rec['id']}, reporters now {rec['reporters']}", "ok", C["teal"],
                       term_line=f"{C['teal']}⛁ DB WRITE  linked to {rec['id']} · reporters → {rec['reporters']}")
        else:
            ticket = {
                "id": None,
                "title": a1.get("title") or payload.get("userNote", "Untitled bug"),
                "module": a2.get("module"), "type": a2.get("type"),
                "controlId": payload.get("controlId"), "page": payload.get("page"),
                "severity": a3.get("aiSeverity"), "status": "Auto-Triaged",
                "reporters": 1, "reportedBy": payload.get("reportedBy"),
                "rootCauseHint": a4.get("hypothesis"), "owner": a4.get("suggestedOwner"),
            }
            tid = hands.create_ticket(register, ticket)
            outcome.update(kind="ticket", id=tid, title=ticket["title"],
                           severity=ticket["severity"], module=ticket["module"],
                           type=ticket["type"], owner=ticket["owner"],
                           rootCauseHint=ticket["rootCauseHint"],
                           securityFlag=a3.get("securityFlag", False))
            await _log(emit, f"DB WRITE → ticket {tid} created ({ticket['severity']}, {ticket['module']})", "ok", C["teal"],
                       term_line=f"{C['teal']}⛁ DB WRITE  ticket {tid} created · {ticket['severity']} · {ticket['module']}")
            if hands.github_configured():
                try:
                    body = (f"**Ticket:** {tid} | **Severity:** {ticket['severity']} | "
                            f"**Module:** {ticket['module']}\n\n"
                            f"**Expected:** {a1.get('expectedResult')}\n**Actual:** {a1.get('actualResult')}\n\n"
                            f"**Root-cause hypothesis (unverified):** {a4.get('hypothesis')}\n\n"
                            f"_Filed autonomously by the FFL agent pipeline; pending human review._")
                    url = await asyncio.to_thread(
                        hands.create_github_issue, f"[{tid}] {ticket['title']}", body,
                        [str(ticket["severity"]), ticket["module"] or "unclassified"])
                    outcome["github"] = url
                    await _log(emit, f"GitHub issue raised: {url}", "ok", C["teal"])
                except Exception as e:
                    outcome["githubError"] = str(e)
                    await _log(emit, f"GitHub issue failed: {e}", "warn", C["red"])
        await emit(outcome)

        # ---- A8: route to the concerned person, then notify them ----
        a8 = await _call(emit, "A8",
                         f"Classification:\n{json.dumps(a2)}\n\nSeverity assessment:\n{json.dumps(a3)}\n\n"
                         f"Outcome:\n{json.dumps(outcome)}\n\nOWNER DIRECTORY:\n{json.dumps(owners)}")

        escs = norm_escalations(a8)
        a8["escalations"] = escs
        assignment = {"type": "assignment", "escalations": escs,
                      **{k: a8.get(k) for k in ("assignee", "assigneeEmail", "team", "sla", "notification")}}
        if outcome.get("kind") == "ticket":
            rec2 = hands.assign_ticket(register, outcome["id"], a8, auto=full_auto)
            if rec2:
                await _log(emit, f"ASSIGNED → {a8.get('assignee')} ({a8.get('team')}) · SLA {a8.get('sla')}", "ok", C["teal"],
                           term_line=f"{C['teal']}👤 ASSIGNED  {outcome['id']} → {a8.get('assignee')} <{a8.get('assigneeEmail')}> · SLA {a8.get('sla')}")
        else:
            await _log(emit, f"duplicate — notifying existing owner {a8.get('assignee')}", "info", C["teal"],
                       term_line=f"{C['teal']}👤 NOTIFY    existing owner {a8.get('assignee')} <{a8.get('assigneeEmail')}>")
        for e in escs:
            await _log(emit, f"ESCALATED → {e['name']} ({e.get('reason') or 'escalation rule'})", "warn", C["red"],
                       term_line=f"{C['red']}⇧ ESCALATED {e['name']} <{e.get('email') or 'n/a'}> — {e.get('reason') or 'escalation rule'}")

        note = (f"[FFL {outcome.get('id')}] {outcome.get('severity','')} · {outcome.get('module','')}\n"
                f"{outcome.get('title','')}\n{a8.get('notification','')}\nSLA: {a8.get('sla')}"
                + (("\nEscalated to: " + ", ".join(e["name"] for e in escs)) if escs else ""))
        if hands.notify_configured():
            try:
                status = await asyncio.to_thread(hands.send_notification, note)
                assignment["notified"] = f"webhook {status}"
                await _log(emit, f"NOTIFIED → webhook accepted ({status})", "ok", C["teal"],
                           term_line=f"{C['teal']}✉ NOTIFIED  Teams/Slack webhook → {status}")
            except Exception as e:
                assignment["notifyError"] = str(e)
                await _log(emit, f"notification failed: {e}", "warn", C["red"])
        else:
            assignment["notified"] = None
            await _log(emit, "notification not sent — NOTIFY_WEBHOOK not configured (message drafted below)", "warn", C["amber"],
                       term_line=f"{C['amber']}✉ NOT SENT  set NOTIFY_WEBHOOK in .env to deliver this to Teams/Slack")
            print(f"{C['dim']}{note}{C['x']}", flush=True)
        await emit(assignment)

        # ---- problems panel ----
        problems = build_problems(payload, a1, a2, a3, a4, a5, outcome)
        await emit({"type": "problems", "items": problems})

        # ---- consolidated review ----
        a7 = await _call(emit, "A7",
                         f"Payload:\n{pj}\n\nChild agent outputs:\n"
                         f"{json.dumps({'intake': a1, 'classification': a2, 'severity': a3, 'rootCause': a4, 'duplicates': a5})}\n\n"
                         f"Automated outcome:\n{json.dumps(outcome)}")
        await emit({"type": "review", "output": a7, "ticketId": outcome.get("id"),
                    "kind": outcome.get("kind"), "fullAuto": full_auto})

        # ---- auto-approve: close the loop with no human click ----
        if full_auto:
            locked = None
            if outcome.get("kind") == "ticket":
                locked, _ = hands.update_ticket(
                    register, outcome["id"], {},
                    reviewer=f"auto-approved via A7 consolidated review ({a7.get('riskLevel','?')} risk)")
            await emit({"type": "finalized", "auto": True, "ticketId": outcome.get("id"),
                        "assignee": a8.get("assignee"), "team": a8.get("team"),
                        "escalations": escs, "sla": a8.get("sla"),
                        "record": locked})
            await _log(emit, f"AUTO-APPROVED and locked — {outcome.get('id')} now owned by {a8.get('assignee')}", "human", C["ok"],
                       term_line=f"{C['ok']}🔒 AUTO-APPROVED  {outcome.get('id')} locked · owner {a8.get('assignee')} · no human click required")

        # ---- terminal summary ----
        errs = sum(1 for p in problems if p["severity"] == "error")
        warns = sum(1 for p in problems if p["severity"] == "warning")
        infos = sum(1 for p in problems if p["severity"] == "info")
        total = time.monotonic() - t_start
        head = ("🎫 " + f"{outcome['id']} created · {outcome.get('severity')} · {outcome.get('module')}"
                if outcome.get("kind") == "ticket"
                else "🔗 " + f"linked to {outcome.get('id')} · reporters {outcome.get('reporters')}")
        print(f"\n{C['teal']}{'─' * W}{C['x']}")
        print(f"  {C['b']}OUTCOME {C['x']}  {head}")
        print(f"  {C['b']}REVIEW  {C['x']}  {a7.get('headline','')}  ({a7.get('riskLevel','?')} risk)")
        print(f"  {C['b']}PROBLEMS{C['x']}  {C['red']}{errs} errors{C['x']} · {C['amber']}{warns} warnings{C['x']} · {C['dim']}{infos} info{C['x']}")
        print(f"  {C['b']}OWNER   {C['x']}  {a8.get('assignee')} · {a8.get('team')} · SLA {a8.get('sla')}"
              + ((" · escalated to " + ", ".join(e["name"] for e in escs)) if escs else ""))
        print(f"  {C['b']}TOTAL   {C['x']}  {total:.1f}s · 8 agents · " + ("0 human clicks (full auto)" if full_auto else "1 human review"))
        print(f"{C['teal']}{'─' * W}{C['x']}")
        print(("  " + C["ok"] + "closed the loop end to end — no human click required" + C["x"] + "\n")
              if full_auto else
              ("  " + C["amber"] + "awaiting the single human review — unlock to edit, or approve" + C["x"] + "\n"), flush=True)
        await _log(emit, "pipeline complete" + ("" if full_auto else " — awaiting the single human review"), "human", C["amber"], term_line="")
    except Exception as e:
        await emit({"type": "fatal", "error": str(e)})
        await _log(emit, f"FATAL {e}", "err", C["red"])
    finally:
        await emit({"type": "done"})
