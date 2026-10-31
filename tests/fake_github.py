#!/usr/bin/env python3
"""Stands in for gh on PATH, modelling one repository and the writes GitHub refuses.

FAKE_GITHUB_STATE holds the repository and a log of each argv. A "fail" entry fails any
command whose joined argv contains it.
"""

import json
import os
import sys

state_path = os.environ["FAKE_GITHUB_STATE"]
with open(state_path) as f:
    state = json.load(f)

argv = sys.argv[1:]
state["calls"].append(argv)


def save():
    with open(state_path, "w") as f:
        json.dump(state, f)


def refuse(message):
    save()
    sys.exit(f"gh: {message}")


def option(name):
    return argv[argv.index(name) + 1] if name in argv else None


def next_sha():
    state["next"] += 1
    return f"sha-{state['next']}"


def open_pulls(head):
    return [p for p in state["pulls"] if p["head"] == head and p["state"] == "open"]


def pull(number):
    return next(p for p in state["pulls"] if p["number"] == int(number))


def api():
    method = option("--method") or "GET"
    payload = json.load(sys.stdin) if option("--input") == "-" else None
    path = next(word for word in argv if word.startswith("repos/"))
    route = path.split("/", 3)[3]
    if method == "GET":
        return api_get(route)
    return api_write(method, route, payload)


def api_get(route):
    if route == "git/ref/heads/main":
        return {"object": {"sha": state["refs"]["main"]}}
    if route.startswith("contents/image.lock?ref="):
        ref = route.split("=", 1)[1]
        sys.stdout.write(state["locks"][state["refs"].get(ref, ref)])
        return None
    if route == "releases":
        assert "--paginate" in argv and "--slurp" in argv
        return [state["releases"]]
    if route.startswith("releases/assets/"):
        sys.stdout.write(state["assets"][route.rsplit("/", 1)[1]])
        return None
    if route.startswith("git/commits/"):
        return {"tree": {"sha": f"tree-of-{route.rsplit('/', 1)[1]}"}}
    if route.startswith("git/matching-refs/heads/"):
        name = route.removeprefix("git/matching-refs/heads/")
        return [{"ref": f"refs/heads/{r}"} for r in state["refs"] if r.startswith(name)]
    return refuse(f"unexpected read {route}")


def api_write(method, route, payload):
    if route == "git/trees":
        assert payload["base_tree"].startswith("tree-of-")
        sha = next_sha()
        state["trees"][sha] = payload
        return {"sha": sha}
    if route == "git/commits":
        sha = next_sha()
        tree = state["trees"][payload["tree"]]
        assert [e["path"] for e in tree["tree"]] == ["image.lock"]
        state["locks"][sha] = tree["tree"][0]["content"]
        state["commits"][sha] = payload
        return {"sha": sha}
    if route == "git/refs":
        name = payload["ref"].removeprefix("refs/heads/")
        if name in state["refs"]:
            refuse("Reference already exists (HTTP 422)")
        state["refs"][name] = payload["sha"]
        return {}
    if route.startswith("git/refs/heads/") and method == "PATCH":
        name = route.removeprefix("git/refs/heads/")
        if name not in state["refs"]:
            refuse("Reference does not exist (HTTP 422)")
        if not payload.get("force"):
            refuse("Update is not a fast forward (HTTP 422)")
        state["refs"][name] = payload["sha"]
        return {}
    if route == "pulls":
        head = payload["head"]
        if [p for p in open_pulls(head) if not p["isCrossRepository"]]:
            refuse("A pull request already exists (HTTP 422)")
        if state["locks"][state["refs"][head]] == state["locks"][state["refs"]["main"]]:
            refuse("No commits between main and head (HTTP 422)")
        created = {
            "number": state["next_pull"],
            "title": payload["title"],
            "body": payload["body"],
            "head": head,
            "state": "open",
            "isCrossRepository": False,
            "autoMergeRequest": None,
            "files": [{"path": "image.lock"}],
        }
        state["next_pull"] += 1
        state["pulls"].append(created)
        return {"number": created["number"]}
    if route.startswith("pulls/") and route.endswith("/requested_reviewers"):
        state["review_requests"].append(int(route.split("/")[1]))
        return {}
    if route.startswith("pulls/") and method == "PATCH":
        pull(route.split("/")[1]).update(payload)
        return {}
    return refuse(f"unexpected write {method} {route}")


def pr():
    if argv[1] == "list":
        keep = argv[argv.index("--json") + 1].split(",")
        found = open_pulls(option("--head"))
        print(json.dumps([{k: p[k] for k in keep} for p in found]))
    elif argv[1] == "view":
        print("true" if pull(argv[2])["autoMergeRequest"] else "false")
    elif argv[1] == "merge" and "--disable-auto" in argv:
        pull(argv[2])["autoMergeRequest"] = None
    elif argv[1] == "merge":
        assert "--auto" in argv and "--squash" in argv
        target = pull(argv[2])
        target["autoMergeRequest"] = {
            "commitHeadline": option("--subject"),
            "commitBody": option("--body"),
        }
    elif argv[1] == "close":
        target = pull(argv[2])
        target["state"] = "closed"
        target["autoMergeRequest"] = None
        target["close_comment"] = option("--comment")
        if "--delete-branch" in argv:
            del state["refs"][target["head"]]
    else:
        refuse(f"unexpected pr command {argv[1]}")


def label():
    if argv[1] != "create":
        refuse(f"unexpected label command {argv[1]}")
    state["labels"].append(argv[2])


def issue():
    label_name = option("--label")
    if argv[1] == "list":
        found = [
            i["number"]
            for i in state["issues"]
            if i["state"] == "open" and label_name in i["labels"]
        ]
        if found:
            print(min(found))
    elif argv[1] == "create":
        if label_name not in state["labels"]:
            refuse(f"could not add label: '{label_name}' not found")
        state["issues"].append(
            {
                "number": len(state["issues"]) + 100,
                "state": "open",
                "labels": [label_name],
                "title": option("--title"),
                "body": option("--body"),
                "comments": [],
            }
        )
    elif argv[1] == "comment":
        target = next(i for i in state["issues"] if i["number"] == int(argv[2]))
        target["comments"].append(option("--body"))
    else:
        refuse(f"unexpected issue command {argv[1]}")


joined = " ".join(argv)
if any(text in joined for text in state["fail"]):
    refuse(f"HTTP 500 on {joined}")

output = {"api": api, "pr": pr, "label": label, "issue": issue}[argv[0]]()
save()
if output is not None:
    print(json.dumps(output))
