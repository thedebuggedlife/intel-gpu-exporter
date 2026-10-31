#!/usr/bin/env python3
"""Stands in for docker on PATH. FAKE_DOCKER_STATE names the exported tar and logs each argv."""

import json
import os
import shutil
import sys

state_path = os.environ["FAKE_DOCKER_STATE"]
with open(state_path) as f:
    state = json.load(f)

argv = sys.argv[1:]
state["calls"].append(argv)
with open(state_path, "w") as f:
    json.dump(state, f)

if argv[0] in state.get("fail", []):
    sys.exit(f"fake docker: {argv[0]} failed")
if argv[0] == "create":
    print("fake-container")
elif argv[0] == "export":
    shutil.copy(state["archive"], argv[argv.index("--output") + 1])
elif argv[0] != "rm":
    sys.exit(f"fake docker: unexpected command {argv[0]}")
