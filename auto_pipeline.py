# -*- coding: utf-8 -*-
"""
Autonomous orchestration - no human gates during the run.
Flow: P0 -> A1 -> (A2 | A3 | A4 in parallel) -> A5 -> real hands -> A7 consolidated review.
Every agent output is LOCKED while the pipeline runs; the human unlocks and edits
once, at the final review, and approves.

Every step is emitted twice: to the web UI (SSE) and to the VS Code terminal (ANSI).
"""
import asyncio, json, time
from foundry import client, run_agent, parse_json
from agents_def import AGENTS
import hands

C = {"amber": "\033[93m", "teal": "\033[96m", "ok": "\033[92m", "red": "\033[91m",
     "violet": "\033[95m", "dim": "\033[90m", "b": "\033[1m", "x": "\033[0m"}

_pc = None
def _client():
    global _pc
    if _pc is None:
        _pc = client()
    return _pc


def _stamp():
    return time.strftime("%H:%M:%S")


async def _log(emit, line, level="info", color=None):
    """Emit a terminal line to the browser AND print it to the VS Code terminal."""
    print(f"{C['dim']}{_stamp()}{C['x']}  {color or ''}{line}{C['x']}", flush=True)
    await emit({"type": "log", "ts": _stamp(), "line": line, "level": level})


async def _call(emit, key, message):
    """Run one agent on a worker thread, emitting running/done/error + terminal logs."""
    spec = AGENTS[key]
    name, display = spec["name"], spec["display"]
    await emit({"type": "stage", "agent": key, "status": "running"})
    await _log(emit, f"[{key}] {display} -> invoking {name} on Foundry ...", "agent", C["amber"])
    t0 = time.monotonic()
    try:
        text = await asyncio.to_thread(run_agent, _client(), name, message)
        out = parse_json(text)
        ms = int((time.monotonic() - t0) * 1000)
        await emit({"type": "stage", "agent": key, "status": "done", "ms": ms, "output": out})
        await _log(emit, f"[{key}] done in {ms/1000:.1f}s  {json.dumps(out)[:160]}", "ok", C["ok"])
        await _log(emit, f"[{key}] output LOCKED (agent-authored, editable only at final review)", "lock", C["dim"])
        return out
    except Exception as e:
        await emit({"type": "stage", "agent": key, "status": "error", "error": str(e)})
        await _log(emit, f"[{key}] ERROR {e}", "err", C["red"])
        raise


async def run_auto(payload, emit):
    """Full autonomous run. `emit` is an async callback receiving event dicts."""
    try:
        register = json.load(open("register.json", encoding="utf-8"))
        pj = json.dumps(payload)

        print(f"\n{C['b']}{C['violet']}{'='*78}\n  FFL AUTONOMOUS BUG PIPELINE — new capture received\n{'='*78}{C['x']}")
        await _log(emit, f"capture received: {payload.get('element')} on {payload.get('page')}", "info", C["teal"])
        await _log(emit, f"reporter note: \"{payload.get('userNote','')}\"", "info", C["dim"])
        await _log(emit, f"console: {'; '.join(payload.get('consoleErrors') or []) or 'none captured'}", "info", C["dim"])
        await _log(emit, "mode: AUTONOMOUS — no human gates until the final review", "info", C["violet"])

        p0 = await _call(emit, "P0", f"Payload:\n{pj}")
        a1 = await _call(emit, "A1", f"Payload:\n{pj}")

        # parallel fan-out: classification, severity and root-cause are independent
        await _log(emit, "parallel fan-out: A2 | A3 | A4 dispatched concurrently", "info", C["violet"])
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
            await _log(emit, f"DB WRITE: linked to {rec['id']}, reporters -> {rec['reporters']}", "ok", C["teal"])
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
            await _log(emit, f"DB WRITE: ticket {tid} created ({ticket['severity']}, {ticket['module']})", "ok", C["teal"])
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

        # ---- consolidated review (the one thing a human reads) ----
        a7 = await _call(emit, "A7",
                         f"Payload:\n{pj}\n\nChild agent outputs:\n"
                         f"{json.dumps({'intake': a1, 'classification': a2, 'severity': a3, 'rootCause': a4, 'duplicates': a5})}\n\n"
                         f"Automated outcome:\n{json.dumps(outcome)}")
        await emit({"type": "review", "output": a7, "ticketId": outcome.get("id"),
                    "kind": outcome.get("kind")})
        await _log(emit, "pipeline complete — awaiting single human review (unlock to edit, or approve)", "info", C["amber"])
    except Exception as e:
        await emit({"type": "fatal", "error": str(e)})
        await _log(emit, f"FATAL {e}", "err", C["red"])
    finally:
        await emit({"type": "done"})
