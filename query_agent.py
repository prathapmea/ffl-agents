# -*- coding: utf-8 -*-
"""A6 - Bug Query Agent: interactive read-only chat over the register."""
import json
from foundry import client, run_agent

def main():
    ids      = json.load(open("agents.json", encoding="utf-8"))
    register = json.load(open("register.json", encoding="utf-8"))
    pc = client()
    print("A6 Bug Query Agent (read-only). Ask about the register. Blank line to exit.")
    print('Try: "What P1s are open in Living Answers?"\n')
    while True:
        q = input("you > ").strip()
        if not q:
            break
        msg = f"REGISTER DATA:\n{json.dumps(register)}\n\nQUESTION: {q}"
        print("A6  >", run_agent(pc, ids["A6"], msg), "\n")

if __name__ == "__main__":
    main()
