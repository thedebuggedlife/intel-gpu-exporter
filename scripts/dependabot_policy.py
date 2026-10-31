#!/usr/bin/env python3
"""Decides whether a Dependabot PR merges by itself or waits for a person."""

import os
import re
import sys

PATCH = "version-update:semver-patch"
MINOR = "version-update:semver-minor"
# update-type is unreliable for digests: real PRs show semver-patch or nothing at all
DIGEST_ONLY = re.compile(r"from `[0-9a-f]{7,}` to `[0-9a-f]{7,}`$", re.IGNORECASE)
TAG_CHANGE = re.compile(r"from (\d+\.\d+)\S* to (\d+\.\d+)\S*$")


def stays_on_the_same_series(title: str) -> bool:
    tags = TAG_CHANGE.search(title)
    return tags is not None and tags[1] == tags[2]


def decide(
    title: str, ecosystem: str, update_type: str, alert_state: str, ghsa_id: str
) -> str:
    if ecosystem == "docker":
        if DIGEST_ONLY.search(title):
            return "merge"
        if not stays_on_the_same_series(title):
            return "review"
    if update_type == PATCH:
        return "merge"
    security = alert_state == "OPEN" and bool(ghsa_id)
    if security and update_type == MINOR:
        return "merge"
    return "review"


def main() -> int:
    print(
        decide(
            os.environ["PR_TITLE"],
            os.environ["ECOSYSTEM"],
            os.environ.get("UPDATE_TYPE", ""),
            os.environ.get("ALERT_STATE", ""),
            os.environ.get("GHSA_ID", ""),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
