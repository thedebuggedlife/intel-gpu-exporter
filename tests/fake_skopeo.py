#!/usr/bin/env python3
"""Stands in for skopeo on PATH. FAKE_SKOPEO_STATE holds the registry and a log of each argv."""

import json
import os
import sys

state_path = os.environ["FAKE_SKOPEO_STATE"]
with open(state_path) as f:
    state = json.load(f)


def save():
    with open(state_path, "w") as f:
        json.dump(state, f)


argv = sys.argv[1:]
state["calls"].append(argv)
save()

if argv[0] == "inspect":
    reference = argv[-1]
    if reference.startswith("oci-archive:"):
        sys.stdout.write(state["archive"])
        sys.exit(0)
    failure = state.get("read_failure")
    if failure:
        sys.stderr.write(failure)
        sys.exit(1)
    tag = reference.rsplit(":", 1)[1]
    if tag not in state["registry"]:
        sys.stderr.write(f"reading manifest {tag}: manifest unknown\n")
        sys.exit(1)
    sys.stdout.write(state["registry"][tag])
elif argv[0] == "copy":
    tag = argv[-1].rsplit(":", 1)[1]
    state["registry"][tag] = state.get("corrupt_copy") or state["archive"]
    save()
else:
    sys.exit(f"fake skopeo: unexpected command {argv[0]}")
