#!/usr/bin/env python3
"""Applies a new image.lock to the one rebuild PR, following the release design's table."""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from image_lock import LOCK_NAME, decide
from release import ReleaseError, parse_version

BRANCH = "rebuild/image-lock"
BODY = "Automated rebuild. The diff of `image.lock` shows what changed in the image."
CLOSING_NOTE = "The rebuilt image matches the committed `image.lock` again."
RAW = "Accept: application/vnd.github.raw+json"
ASSET = "Accept: application/octet-stream"
HOLD = Path(__file__).with_name("hold-for-review.sh")
STUCK_AFTER = timedelta(days=1)


class GitHubError(Exception):
    pass


def gh(*args: str, payload: dict | None = None) -> str:
    result = subprocess.run(
        ["gh", *args],
        input=None if payload is None else json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitHubError(f"gh {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def read(path: str, *options: str):
    return json.loads(gh("api", *options, path))


def write(method: str, path: str, payload: dict):
    return json.loads(
        gh("api", "--method", method, path, "--input", "-", payload=payload)
    )


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


def commit_lock(repo: str, base: str, lock: str, message: str) -> str:
    base_tree = read(f"repos/{repo}/git/commits/{base}")["tree"]["sha"]
    entry = {"path": LOCK_NAME, "mode": "100644", "type": "blob", "content": lock}
    tree = write(
        "POST", f"repos/{repo}/git/trees", {"base_tree": base_tree, "tree": [entry]}
    )
    commit = write(
        "POST",
        f"repos/{repo}/git/commits",
        {"message": message, "tree": tree["sha"], "parents": [base]},
    )
    return commit["sha"]


def point_branch(repo: str, commit: str) -> None:
    ref = f"refs/heads/{BRANCH}"
    existing = read(f"repos/{repo}/git/matching-refs/heads/{BRANCH}")
    if any(entry["ref"] == ref for entry in existing):
        write(
            "PATCH",
            f"repos/{repo}/git/{ref}",
            {"sha": commit, "force": True},
        )
    else:
        write("POST", f"repos/{repo}/git/refs", {"ref": ref, "sha": commit})


def hold(number: int) -> None:
    result = subprocess.run([HOLD, str(number)], check=False)
    if result.returncode != 0:
        raise GitHubError(f"couldn't hold #{number} for review")


def set_auto_merge(repo: str, number: int, title: str) -> None:
    gh(
        "pr",
        "merge",
        str(number),
        "--repo",
        repo,
        "--auto",
        "--squash",
        "--subject",
        title,
        "--body",
        BODY,
    )


def disarm(repo: str, number: int) -> None:
    gh("pr", "merge", str(number), "--repo", repo, "--disable-auto")


def close(repo: str, number: int) -> None:
    gh(
        "pr",
        "close",
        str(number),
        "--repo",
        repo,
        "--delete-branch",
        "--comment",
        CLOSING_NOTE,
    )


def apply(repo: str, new: str, built: str) -> str:
    base = read(f"repos/{repo}/git/ref/heads/main")["object"]["sha"]
    if built != base:
        return f"skipped: this run built {built}, and main is now {base}"
    title = decide(new, read_lock(repo, base), published_lock(repo))
    pull = open_pull(repo)

    if pull is not None and {f["path"] for f in pull["files"]} - {LOCK_NAME}:
        number = pull["number"]
        hold(number)
        if is_stuck(pull):
            raise GitHubError(f"#{number} is held, which blocks the rebuild")
        return f"held #{number}, which changes more than {LOCK_NAME}"
    if title is None:
        if pull is None:
            return "no change"
        close(repo, pull["number"])
        return f"closed #{pull['number']}"

    current = pull is not None and read_lock(repo, BRANCH) == new
    if current and pull["title"] == title and is_stuck(pull):
        raise GitHubError(f"#{pull['number']} hasn't merged since an earlier run")

    armed = pull["autoMergeRequest"] if pull is not None else None
    if armed is not None and armed["commitHeadline"] != title:
        disarm(repo, pull["number"])
        armed = None
    if not current:
        point_branch(repo, commit_lock(repo, base, new, title))
    if pull is None:
        created = write(
            "POST",
            f"repos/{repo}/pulls",
            {"head": BRANCH, "base": "main", "title": title, "body": BODY},
        )
        number = created["number"]
    else:
        number = pull["number"]
        if pull["title"] != title:
            write("PATCH", f"repos/{repo}/pulls/{number}", {"title": title})
    if armed is None:
        set_auto_merge(repo, number, title)
    return f"#{number}: {title}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, help="the image.lock just generated")
    args = parser.parse_args(argv)
    try:
        lock = Path(args.lock).read_text()
        print(apply(os.environ["GITHUB_REPOSITORY"], lock, os.environ["GITHUB_SHA"]))
    except GitHubError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
