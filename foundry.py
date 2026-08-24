# -*- coding: utf-8 -*-
"""Shared Foundry client + one-shot agent runner (azure-ai-projects 2.x)."""
import os, json, re
from dotenv import load_dotenv
load_dotenv()
ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "")
MODEL    = os.environ.get("MODEL_DEPLOYMENT", "gpt-4.1")
TENANT   = os.environ.get("AZURE_TENANT_ID", "")

def client():
    """Lazy import so utility functions stay testable without the SDK installed."""
    from azure.ai.projects import AIProjectClient
    if not ENDPOINT:
        raise SystemExit("PROJECT_ENDPOINT missing - copy .env.example to .env and fill it.")
    if TENANT:
        # pin the tenant so a drifted `az account set` default can't break runs
        from azure.identity import AzureCliCredential
        cred = AzureCliCredential(tenant_id=TENANT)
    else:
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
    return AIProjectClient(endpoint=ENDPOINT, credential=cred)

_openai = None
def openai_client(pc):
    """AAD-authenticated OpenAI client against the project's /openai/v1 endpoint."""
    global _openai
    if _openai is None:
        _openai = pc.get_openai_client()
    return _openai

def run_agent(pc, agent_name: str, user_message: str) -> str:
    """One run against a named Foundry agent via the Responses API. Returns final text."""
    resp = openai_client(pc).responses.create(
        input=user_message,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    )
    if getattr(resp, "output_text", None):
        return resp.output_text
    raise RuntimeError(f"agent {agent_name} returned no text (status={getattr(resp, 'status', '?')})")

def parse_json(text: str) -> dict:
    """Tolerant JSON extraction (strips fences, grabs outermost object)."""
    t = re.sub(r"```json|```", "", text).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise
