# -*- coding: utf-8 -*-
"""
P0 orchestration: runs the captured payload through A1 -> A5 on real Foundry
agents, pausing at every human gate. On final accept it acts for real:
writes the ticket (or duplicate link) into register.json, and raises a
GitHub issue if GITHUB_TOKEN + GITHUB_REPO are configured.
"""
import json, sys
from foundry import client, run_agent, parse_json
import hands

C = {"amber":"\033[93m","teal":"\033[96m","ok":"\033[92m","red":"\033[91m",
     "dim":"\033[90m","b":"\033[1m","x":"\033[0m"}

def head(t):   print(f"\n{C['b']}{C['amber']}== {t} =={C['x']}")
def show(d):
    for k, v in d.items():
        if isinstance(v, list): v = " | ".join(str(i) for i in v)
        print(f"  {C['dim']}{k:<16}{C['x']}{v}")

def gate(who, prompt, current=None):
    """Human gate: Enter = accept, or type a correction."""
    print(f"{C['red']}  HUMAN GATE - {who}{C['x']}")
    ans = input(f"  {prompt} [Enter = accept{', or type new value' if current else ''}]: ").strip()
    if ans:
        print(f"  {C['ok']}amended by human -> {ans}{C['x']}")
        return ans
    print(f"  {C['ok']}accepted as suggested - decision recorded{C['x']}")
    return current

def main():
    payload  = json.load(open("payload_sample.json", encoding="utf-8"))
    register = json.load(open("register.json", encoding="utf-8"))
    ids      = json.load(open("agents.json", encoding="utf-8"))
    pc = client()

    head("CAPTURED VISUAL PAYLOAD")
    show(payload)

    # ---- P0 ----
    head("P0 - Parent Bug Reporting Agent")
    p0 = parse_json(run_agent(pc, ids["P0"], f"Payload:\n{json.dumps(payload)}"))
    show(p0)

    # ---- A1 ----
    head("A1 - Intake Structurer")
    a1 = parse_json(run_agent(pc, ids["A1"], f"Payload:\n{json.dumps(payload)}"))
    show(a1)
    gate("Reporter", "Confirm the structured draft?")

    # ---- A2 ----
    head("A2 - Classifier")
    a2 = parse_json(run_agent(pc, ids["A2"], f"Payload:\n{json.dumps(payload)}"))
    show(a2)
    a2["module"] = gate("Triage Lead", f"Module = {a2.get('module')}?", a2.get("module"))

    # ---- A3 ----
    head("A3 - Severity Assessor")
    a3 = parse_json(run_agent(pc, ids["A3"], f"Payload:\n{json.dumps(payload)}"))
    show(a3)
    final_sev = gate("Triage Lead", f"AISeverity = {a3.get('aiSeverity')} (suggestion). Final severity?", a3.get("aiSeverity"))

    # ---- A4 ----
    head("A4 - Root-cause Hinter")
    history = [r for r in register if r.get("status") == "Closed" or r.get("module") == a2.get("module")]
    a4 = parse_json(run_agent(pc, ids["A4"],
        f"Payload:\n{json.dumps(payload)}\n\nResolved-bug history for grounding:\n{json.dumps(history)}"))
    show(a4)
    gate("Developer", "Treat as hint; real root cause recorded at fix time. Acknowledge?")

    # ---- A5 ----
    head("A5 - Duplicate Detector")
    a5 = parse_json(run_agent(pc, ids["A5"],
        f"New payload:\n{json.dumps(payload)}\n\nOpen register:\n{json.dumps(register)}"))
    show(a5)
    verdict = gate("Triage Lead", f"Verdict = {a5.get('verdict')} (match: {a5.get('matchId')}). Confirm?", a5.get("verdict"))

    # ---- outcome: the real hands ----
    head("OUTCOME")
    if verdict and verdict.lower().startswith("dup") and a5.get("matchId"):
        rec = hands.link_duplicate(register, a5["matchId"])
        if rec:
            print(f"  {C['ok']}{C['b']}Linked to existing case {rec['id']}{C['x']} - no duplicate record created.")
            print(f"  register.json updated: reporters on {rec['id']} is now {rec['reporters']} (real write).")
        else:
            print(f"  {C['red']}A5 matched {a5['matchId']} but it is not in the register - recording as new instead.{C['x']}")
            verdict = "New"
    if not (verdict and verdict.lower().startswith("dup") and a5.get("matchId")):
        ticket = {
            "id": None,  # assigned by hands.create_ticket
            "title": a1.get("title") or payload.get("userNote", "Untitled bug"),
            "module": a2.get("module"), "type": a2.get("type"),
            "controlId": payload.get("controlId"), "page": payload.get("page"),
            "severity": final_sev, "status": "New", "reporters": 1,
            "reportedBy": payload.get("reportedBy"),
            "rootCauseHint": a4.get("hypothesis"), "owner": a4.get("suggestedOwner"),
        }
        tid = hands.create_ticket(register, ticket)
        print(f"  {C['ok']}{C['b']}Ticket created: {tid}{C['x']} - written to register.json (real write).")
        print(f"  module={a2.get('module')}  type={a2.get('type')}  severity={final_sev} (human-confirmed)  owner={a4.get('suggestedOwner')}")
        print(f"  {C['dim']}Rerun the pipeline with this same payload and A5 will now link to {tid}.{C['x']}")

        if hands.github_configured():
            print(f"{C['red']}  HUMAN GATE - Product Owner{C['x']}")
            go = input(f"  Raise a real GitHub issue in {hands.github_repo()}? [Enter = yes, n = skip]: ").strip().lower()
            if go in ("", "y", "yes"):
                body = (f"**Ticket:** {tid}\n**Module:** {a2.get('module')} | **Type:** {a2.get('type')} "
                        f"| **Severity:** {final_sev} (human-confirmed)\n\n"
                        f"**Steps to reproduce:**\n"
                        + "\n".join(f"1. {s}" for s in a1.get("stepsToReproduce", [])) + "\n\n"
                        f"**Expected:** {a1.get('expectedResult')}\n**Actual:** {a1.get('actualResult')}\n\n"
                        f"**Console:** `{'; '.join(payload.get('consoleErrors', []))}`\n"
                        f"**Root-cause hypothesis (A4, unverified):** {a4.get('hypothesis')}\n\n"
                        f"_Filed by the FFL agent pipeline after human triage._")
                url = hands.create_github_issue(f"[{tid}] {ticket['title']}", body,
                                                labels=[str(final_sev), a2.get("module") or "unclassified"])
                print(f"  {C['ok']}{C['b']}GitHub issue raised: {url}{C['x']}")
            else:
                print(f"  {C['dim']}GitHub issue skipped by human.{C['x']}")
        else:
            print(f"  {C['dim']}GitHub not configured - set GITHUB_TOKEN + GITHUB_REPO in .env to raise a real issue on accept.{C['x']}")

    print(f"\n  {C['dim']}Still stubbed: Dataverse/AI Search register, Teams approvals, the capture component.{C['x']}")
    print(f"  {C['dim']}Open ai.azure.com -> project -> Agents -> Threads to show these runs server-side.{C['x']}")
    print(f"  {C['dim']}Reset the demo register anytime: python reset_register.py{C['x']}\n")

if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        sys.exit(f"Missing file: {e.filename}. Run create_agents.py first (agents.json).")
