# -*- coding: utf-8 -*-
"""Creates (or versions-up) the seven FFL agents in Azure AI Foundry."""
import json
from foundry import client, MODEL
from agents_def import AGENTS

def main():
    from azure.ai.projects.models import PromptAgentDefinition
    pc = client()
    names = {}
    for key, spec in AGENTS.items():
        # create_version creates the agent if new, otherwise adds a fresh version
        v = pc.agents.create_version(
            agent_name=spec["name"],
            definition=PromptAgentDefinition(model=MODEL, instructions=spec["instructions"]),
            description=spec["display"],
        )
        names[key] = spec["name"]
        print(f"  created  {key}  {spec['display']:28s}  {spec['name']}  v{getattr(v, 'version', '?')}")
    with open("agents.json", "w", encoding="utf-8") as f:
        json.dump(names, f, indent=2)
    print("\nagents.json written. Open ai.azure.com -> your project -> Agents to see them.")

if __name__ == "__main__":
    main()
