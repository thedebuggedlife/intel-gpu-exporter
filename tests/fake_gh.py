#!/usr/bin/env python3
"""Stands in for gh on PATH. FAKE_GH_STATE holds the PR's state and a log of each argv."""

import json
import os
import sys

state_path = os.environ["FAKE_GH_STATE"]
with open(state_path) as f:
    state = json.load(f)

argv = sys.argv[1:]
state["calls"].append(argv)
with open(state_path, "w") as f:
    json.dump(state, f)

command = argv[:2]
if command in state.get("fail", []):
    sys.exit(f"fake gh: {' '.join(command)} failed")
if command == ["pr", "view"]:
    print("true" if state["armed"] else "false")
elif command not in (["pr", "merge"], ["api", "--method"]):
    sys.exit(f"fake gh: unexpected command {command}")
