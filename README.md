# FFL Agentic Bug Reporting — Azure AI Foundry (Pro-code)

Six real agents (P0 parent + A1–A5 children + A6 query) created and run on
**Azure AI Foundry Agent Service**. Each agent is a persistent Foundry agent —
visible in the Foundry portal under **Agents** — orchestrated from Python.

---

## 1. What to do in the Foundry portal (one time, ~5 minutes)

1. Go to **https://ai.azure.com** and sign in.
2. **Create a project** (Foundry project). Any region with model availability
   (Sweden Central / East US 2 work well). Note the project name.
3. In the project: **Models + endpoints → Deploy model → Deploy base model**
   - Pick **gpt-4.1** (best quality) or **gpt-4o-mini** (cheap, fine for demo).
   - Keep the deployment name simple, e.g. `gpt-4.1` — you'll put it in `.env`.
4. From the project **Overview** page, copy the **Project endpoint**.
   It looks like:
   `https://<your-resource>.services.ai.azure.com/api/projects/<your-project>`
5. **Access control (IAM)** on the project (or parent resource):
   make sure your account has the **Azure AI User** role
   (owners usually have it already).

That's all Foundry needs. No agents are created in the portal by hand —
the code creates them, and then you can SEE them in the portal.

## 2. Local setup (VS Code)

```bash
# in the project folder
python -m venv .venv
.venv\Scripts\activate        # Windows   (source .venv/bin/activate on mac/linux)
pip install -r requirements.txt

az login --tenant <your-tenant-id>   # auth = DefaultAzureCredential, no keys in code

copy .env.example .env        # then edit .env with your two values
```

`.env` needs exactly two values:
```
PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
MODEL_DEPLOYMENT=gpt-4.1
```

## 3. Run

```bash
python create_agents.py    # creates P0, A1..A6 in Foundry  -> writes agents.json
python pipeline.py         # runs the full bug flow on payload_sample.json
python query_agent.py      # A6 interactive chat over the register
python reset_register.py   # restore register.json to the seeded demo state
python delete_agents.py    # cleanup when finished
```

`pipeline.py` pauses at every **human gate** (you press Enter to confirm or
type a correction) — the "agents suggest, humans decide" rule, live in the console.

## 3a. Autonomous web demo (capture → agents → one review)

```bash
uvicorn app:app --port 8787     # then open http://localhost:8787
```

Keep the VS Code terminal visible while you demo — the same run streams into
both the page and the terminal, in colour.

**The flow:**

1. Click a control in the mock FFL app (Export PDF / Save Draft / Delete
   Customer) — it fails and shows the real console error.
2. Click the floating **🐞 bug icon** → capture mode. Hover highlights any
   element; click the broken one. The page auto-captures page, controlId, tag,
   xpath, console error and a region-scoped screenshot straight from the DOM.
3. Type a **description**, hit **⚡ Submit to Agents**.
4. The pipeline runs autonomously and resolves one agent at a time, live:
   **P0 → A1 → (A2 ‖ A3 ‖ A4 in parallel) → A5** → real database write
   (new ticket, or duplicate linked) → optional GitHub issue.
5. **A7 Consolidated Reviewer** writes the single executive summary.

**The lock.** Every agent output is 🔒 locked while the pipeline runs — no
human can nudge an agent mid-flight. At the final review you can:

- **🔓 Unlock & edit** — every agent field becomes editable (title, module,
  type, severity, root-cause hypothesis, owner), or
- **✓ Approve & lock** — accept the agents' work as-is.

Approving persists your edits to the register with an audit trail
(`humanEdits`, `reviewedBy`) and re-locks the record. Total human effort on a
clean run: **one click**.

The gated CLI (`pipeline.py`) and the autonomous web flow use the **same
agents** — two orchestration modes over one agent fleet.

## 3b. Real hands (the pipeline now acts, not just prints)

On the final accept, `hands.py` performs **real actions**:

- **New ticket** → appended to `register.json` with the next sequential FFL id.
  This is real persistence: rerun the pipeline with the same payload and A5
  will link to the ticket the previous run created. Run `reset_register.py`
  to restore the seeded 5-record state between demos.
- **Duplicate verdict** → the matched record's `reporters` count is bumped in
  `register.json` (no duplicate record created).
- **GitHub issue (optional)** → set `GITHUB_TOKEN` + `GITHUB_REPO` in `.env`
  and a final Product Owner gate offers to raise a **real issue via the GitHub
  API** — title `[FFL-xxxx] ...`, body with repro steps, severity, console
  error, and A4's root-cause hypothesis, labelled with severity + module.
  Not configured? The step is skipped with a note — no fake success output.

## 4. What to show the team

1. Run `create_agents.py`, then open **ai.azure.com → your project → Agents**:
   seven named agents exist. Real, persistent, inspectable.
2. Run `pipeline.py`: the captured visual payload flows P0 → A1 → A2 → A3 → A4 → A5,
   each stopping at its human gate, ending in a ticket (or a duplicate link to FFL-0387).
3. In the portal, open **Agents → (any agent) → Threads**: the actual runs and
   messages are visible server-side — proof this isn't a mock.
4. **The think-and-act moment**: change `controlId` in `payload_sample.json` to
   something new, run the pipeline (A5 says New, ticket written to the register),
   then run it **again unchanged** — A5 now links to the ticket the first run
   created. Reasoning + real state, live.
5. Run `query_agent.py` and ask: *"What P1s are open in Living Answers?"* —
   after a pipeline run, ask about the ticket it just created.

## Notes

- **SDK version**: written for `azure-ai-projects >= 2.0.0` (the current Foundry
  SDK generation): agents are created with `client.agents.create_version()` +
  `PromptAgentDefinition`, and runs go through the project's OpenAI-compatible
  endpoint (`get_openai_client()` + Responses API with an `agent_reference`).
  If Microsoft ships breaking changes later, check the package changelog.
- **Duplicate detection (A5)**: for the demo the register is a local JSON and the
  agent reasons over it in-prompt. Production upgrade: index the register in
  **Azure AI Search** and give A5/A6 retrieval instead of in-prompt data.
- **Costs**: each pipeline run = 6 short model calls. On gpt-4o-mini this is
  fractions of a cent; on gpt-4.1 still small.
