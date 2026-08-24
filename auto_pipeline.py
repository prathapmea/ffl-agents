# -*- coding: utf-8 -*-
"""
Autonomous orchestration - no human gates.
Flow: P0 -> A1 -> (A2 | A3 | A4 in parallel) -> A5 -> real hands -> A7 consolidated review.
Every step emits an event so a UI can stream the run live.
"""
import asyncio, json, time
from foundry import client, run_agent, parse_json
import hands

_pc = None
def _client():
    global _pc
    if _pc is None:
        _pc = client()
    return _pc


async def _call(emit, key, agent_name, message):
    """Run one agent on a worker thread, emitting running/done/error events."""
    await emit({"type": "stage", "agent": key, "status": "running"})
    t0 = time.monotonic()
    try:
        text = await asyncio.to_thread(run_agent, _client(), agent_name, message)
        out = parse_json(text)
        await emit({"type": "stage", "agent": key, "status": "done",
                    "ms": int((time.monotonic() - t0) * 1000), "output": out})
        return out
    except Exception as e:
        await emit({"type": "stage", "agent": key, "status": "error", "error": str(e)})
        raise


async def run_auto(payload, emit):
    """Full autonomous run. `emit` is an async callback receiving event dicts."""
    try:
        register = json.load(open("register.json", encoding="utf-8"))
        ids = json.load(open("agents.json", encoding="utf-8"))
        pj = json.dumps(payload)

        p0 = await _call(emit, "P0", ids["P0"], f"Payload:\n{pj}")
        a1 = await _call(emit, "A1", ids["A1"], f"Payload:\n{pj}")

        # parallel fan-out: classification, severity and root-cause are independent
        history = json.dumps([r for r in register if r.get("status") == "Closed" or r.get("rootCause")])
        a2, a3, a4 = await asyncio.gather(
            _call(emit, "A2", ids["A2"], f"Payload:\n{pj}"),
            _call(emit, "A3", ids["A3"], f"Payload:\n{pj}"),
            _call(emit, "A4", ids["A4"], f"Payload:\n{pj}\n\nResolved-bug history for grounding:\n{history}"),
        )

        a5 = await _call(emit, "A5", ids["A5"],
                         f"New payload:\n{pj}\n\nOpen register:\n{json.dumps(register)}")

        # ---- real hands, fully automated ----
        outcome = {"type": "outcome"}
        verdict = (a5.get("verdict") or "").lower()
        rec = hands.link_duplicate(register, a5["matchId"]) if verdict.startswith("dup") and a5.get("matchId") else None
        if rec:
            outcome.update(kind="duplicate", id=rec["id"], title=rec.get("title"),
                           reporters=rec["reporters"], severity=rec.get("severity"))
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
                           owner=ticket["owner"], securityFlag=a3.get("securityFlag", False))
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
                except Exception as e:
                    outcome["githubError"] = str(e)
        await emit(outcome)

        # ---- consolidated review (the one thing a human reads) ----
        a7 = await _call(emit, "A7", ids["A7"],
                         f"Payload:\n{pj}\n\nChild agent outputs:\n"
                         f"{json.dumps({'intake': a1, 'classification': a2, 'severity': a3, 'rootCause': a4, 'duplicates': a5})}\n\n"
                         f"Automated outcome:\n{json.dumps(outcome)}")
        await emit({"type": "review", "output": a7})
    except Exception as e:
        await emit({"type": "fatal", "error": str(e)})
    finally:
        await emit({"type": "done"})
