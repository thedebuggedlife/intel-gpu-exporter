#!/usr/bin/env python3
"""Plans what a new image.lock means for the one rebuild PR, following the release design's table."""

import argparse
import base64
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from image_lock import LOCK_NAME, decide
from release import ReleaseError, parse_version

BRANCH = "rebuild/image-lock"
RAW = "Accept: application/vnd.github.raw+json"
ASSET = "Accept: application/octet-stream"
STUCK_AFTER = timedelta(days=1)
FLAGS = ("arm", "branch-exists", "commit", "disarm", "retitle", "stuck")


class GitHubError(Exception):
    pass


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GitHubError(f"gh {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def read(path: str, *options: str):
    return json.loads(gh("api", *options, path))


def read_lock(repo: str, ref: str) -> str:
    return gh("api", "-H", RAW, f"repos/{repo}/contents/{LOCK_NAME}?ref={ref}")


def published_lock(repo: str) -> str | None:
    pages = read(f"repos/{repo}/releases", "--paginate", "--slurp")
    newest = None
    for release in (entry for page in pages for entry in page):
        asset = next((a for a in release["assets"] if a["name"] == LOCK_NAME), None)
        try:
            version = parse_version(release["tag_name"])
        except ReleaseError:
            continue
        if release["draft"] or version.rc is not None or asset is None:
            continue
        if newest is None or newest[0] < version:
            newest = (version, asset["id"])
    if newest is None:
        return None
    return gh("api", "-H", ASSET, f"repos/{repo}/releases/assets/{newest[1]}")


def open_pull(repo: str) -> dict | None:
    # gh pr list --head also matches a fork's branch, which anyone can name the same
    owner = repo.split("/")[0]
    found = read(f"repos/{repo}/pulls?head={owner}:{BRANCH}&state=open")
    if not found:
        return None
    fields = "number,title,autoMergeRequest,files,commits"
    return json.loads(
        gh("pr", "view", str(found[0]["number"]), "--repo", repo, "--json", fields)
    )


def is_stuck(pull: dict) -> bool:
    latest = datetime.fromisoformat(pull["commits"][-1]["committedDate"])
    return datetime.now(UTC) - latest > STUCK_AFTER


def branch_exists(repo: str) -> bool:
    ref = f"refs/heads/{BRANCH}"
    existing = read(f"repos/{repo}/git/matching-refs/heads/{BRANCH}")
    return any(entry["ref"] == ref for entry in existing)


def plan(repo: str, new: str, built: str) -> dict:
    base = read(f"repos/{repo}/git/ref/heads/main")["object"]["sha"]
    if built != base:
        print(f"skipped: this run built {built}, and main is now {base}")
        return {}
    title = decide(new, read_lock(repo, base), published_lock(repo))
    pull = open_pull(repo)

    if pull is not None and {f["path"] for f in pull["files"]} - {LOCK_NAME}:
        number = pull["number"]
        print(f"holding #{number}, which changes more than {LOCK_NAME}")
        return {"operation": "hold", "number": number, "stuck": is_stuck(pull)}
    if title is None:
        if pull is None:
            print("no change")
            return {}
        print(f"closing #{pull['number']}")
        return {"operation": "close", "number": pull["number"]}

    current = pull is not None and read_lock(repo, BRANCH) == new
    retitle = pull is not None and pull["title"] != title
    if current and not retitle and is_stuck(pull):
        raise GitHubError(f"#{pull['number']} hasn't merged since an earlier run")

    armed = pull["autoMergeRequest"] if pull is not None else None
    disarm = armed is not None and armed["commitHeadline"] != title
    arm = armed is None or disarm
    if current and not (retitle or disarm or arm):
        print(f"#{pull['number']} is already armed")
        return {}
    fields = {
        "operation": "propose",
        "title": title,
        "arm": arm,
        "disarm": disarm,
        "retitle": retitle,
        "commit": not current,
    }
    if pull is not None:
        fields["number"] = pull["number"]
    if not current:
        encoded = base64.b64encode(new.encode()).decode()
        fields |= {"base": base, "lock": encoded, "branch-exists": branch_exists(repo)}
    print(f"{title}: {', '.join(k for k in FLAGS if fields.get(k)) or 'no writes'}")
    return fields


def as_output(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def write_outputs(path: str, fields: dict) -> None:
    outputs = {flag: False for flag in FLAGS} | {"operation": "none"} | fields
    with open(path, "a") as output:
        output.writelines(f"{k}={as_output(v)}\n" for k, v in outputs.items())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, help="the image.lock just generated")
    args = parser.parse_args(argv)
    try:
        lock = Path(args.lock).read_text()
        fields = plan(os.environ["GITHUB_REPOSITORY"], lock, os.environ["GITHUB_SHA"])
    except GitHubError as error:
        print(error, file=sys.stderr)
        return 1
    write_outputs(os.environ["GITHUB_OUTPUT"], fields)
    return 0


if __name__ == "__main__":
    sys.exit(main())
