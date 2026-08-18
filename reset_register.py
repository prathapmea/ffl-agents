# -*- coding: utf-8 -*-
"""Restore register.json to the seeded demo state (register.seed.json)."""
import shutil
shutil.copyfile("register.seed.json", "register.json")
print("register.json reset to seed (5 records, FFL-0387..FFL-0419).")
