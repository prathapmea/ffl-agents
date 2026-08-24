# -*- coding: utf-8 -*-
"""
The real hands: actions that actually change state after the human accepts.
- register writes: new ticket appended / duplicate reporter count bumped in register.json
- GitHub issue: real POST to the GitHub API (optional, set GITHUB_TOKEN + GITHUB_REPO in .env)
"""
import json, os, re, urllib.request
from dotenv import load_dotenv
load_dotenv()

REGISTER_FILE = "register.json"


def save_register(register):
    with open(REGISTER_FILE, "w", encoding="utf-8") as f:
        json.dump(register, f, indent=2, ensure_ascii=False)
        f.write("\n")


def next_ffl_id(register) -> str:
    nums = [int(m.group(1)) for r in register
            if (m := re.match(r"FFL-(\d+)", r.get("id", "")))]
    return f"FFL-{(max(nums) + 1 if nums else 1):04d}"


def create_ticket(register, ticket) -> str:
    """Append the accepted ticket to the register and persist. Returns its id."""
    ticket["id"] = next_ffl_id(register)
    register.append(ticket)
    save_register(register)
    return ticket["id"]


def update_ticket(register, ticket_id, edits, reviewer="human.reviewer@ffl.internal"):
    """Apply the human reviewer's edits to a record and persist. Returns (record, changed_fields)."""
    for r in register:
        if r.get("id") == ticket_id:
            changed = []
            for k, v in (edits or {}).items():
                if v not in (None, "") and str(r.get(k, "")) != str(v):
                    changed.append(f"{k}: '{r.get(k)}' -> '{v}'")
                    r[k] = v
            r["status"] = r.get("status") or "Auto-Triaged"
            r["reviewedBy"] = reviewer
            if changed:
                r["humanEdits"] = changed
            save_register(register)
            return r, changed
    return None, []


def link_duplicate(register, match_id):
    """Add this reporter to the existing case and persist. Returns the record or None."""
    for r in register:
        if r.get("id") == match_id:
            r["reporters"] = r.get("reporters", 1) + 1
            save_register(register)
            return r
    return None


def github_repo():
    return os.environ.get("GITHUB_REPO", "").strip()


def github_configured() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN", "").strip() and github_repo())


def create_github_issue(title: str, body: str, labels=None) -> str:
    """Create a real GitHub issue. Returns its html_url. Raises on API errors."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{github_repo()}/issues",
        data=json.dumps({"title": title, "body": body, "labels": labels or []}).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN'].strip()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ffl-bug-pipeline",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["html_url"]
