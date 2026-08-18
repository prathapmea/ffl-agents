# -*- coding: utf-8 -*-
"""Removes the FFL demo agents from the Foundry project."""
import json, os
from foundry import client

def main():
    pc = client()
    if os.path.exists("agents.json"):
        names = json.load(open("agents.json", encoding="utf-8"))
        for key, name in names.items():
            try:
                pc.agents.delete(agent_name=name)
                print(f"  deleted {key} ({name})")
            except Exception as e:
                print(f"  skip {key}: {e}")
        os.remove("agents.json")
    else:
        # fallback: delete by name prefix
        for a in pc.agents.list():
            if a.name and a.name.startswith("ffl-"):
                pc.agents.delete(agent_name=a.name)
                print(f"  deleted {a.name}")
    print("cleanup done.")

if __name__ == "__main__":
    main()
