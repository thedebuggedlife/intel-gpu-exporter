#!/usr/bin/env python3
"""Decides whether a Dependabot PR merges by itself or waits for a person."""

import json
import os
import re
import subprocess
import sys
from urllib.parse import quote

PATCH = "version-update:semver-patch"
MINOR = "version-update:semver-minor"
# An Action runs beside the key, so every update to one needs a person
MERGEABLE_ECOSYSTEMS = {"docker", "pip"}
MERGEABLE_FILES = {"Dockerfile", "requirements-dev.txt", "requirements.txt"}
SUBJECT = re.compile(r"(chore|fix)\(deps\): .+")
# update-type is unreliable for digests: real PRs show semver-patch or nothing at all
DIGEST_ONLY = re.compile(r"from `[0-9a-f]{7,}` to `[0-9a-f]{7,}`$", re.IGNORECASE)
TAG_CHANGE = re.compile(r"from (\d+\.\d+)\S* to (\d+\.\d+)\S*$")


def stays_on_the_same_series(title: str) -> bool:
    tags = TAG_CHANGE.search(title)
    return tags is not None and tags[1] == tags[2]


def decide(
    title: str, ecosystem: str, files: list[str], update_type: str, ghsa_id: str
) -> str:
    if ecosystem not in MERGEABLE_ECOSYSTEMS or not SUBJECT.fullmatch(title):
        return "review"
    if not files or not MERGEABLE_FILES.issuperset(files):
        return "review"
    if ecosystem == "docker":
        if DIGEST_ONLY.search(title):
            return "merge"
        if not stays_on_the_same_series(title):
            return "review"
    if update_type == PATCH:
        return "merge"
    if ghsa_id and update_type == MINOR:
        return "merge"
    return "review"


def api(path: str, *options: str) -> str:
    result = subprocess.run(
        ["gh", "api", *options, path], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        sys.exit(f"gh api {path}: {result.stderr.strip()}")
    return result.stdout


def changed_files(repo: str, number: str) -> list[str]:
    path = f"repos/{repo}/pulls/{number}/files"
    return api(path, "--paginate", "--jq", ".[].filename").split()


# REST, because the GraphQL equivalent returns no alerts rather than an error without access
def open_alert(repo: str, dependency: str, files: list[str]) -> str:
    if not dependency:
        return ""
    query = f"state=open&package={quote(dependency)}&manifest={quote(','.join(files))}"
    path = f"repos/{repo}/dependabot/alerts?{query}"
    return api(path, "--jq", ".[0].security_advisory.ghsa_id // empty").strip()


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    number = os.environ["PR_NUMBER"]
    title = os.environ["PR_TITLE"]
    files = changed_files(repo, number)
    print(
        json.dumps(
            {
                "decision": decide(
                    title,
                    os.environ["ECOSYSTEM"],
                    files,
                    os.environ.get("UPDATE_TYPE", ""),
                    open_alert(repo, os.environ.get("DEPENDENCY", ""), files),
                ),
                "number": int(number),
                "sha": os.environ["HEAD_SHA"],
                "title": title,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
