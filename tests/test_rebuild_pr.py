import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from helpers import REPO, TESTS

from scripts.image_lock import CHORE_TITLE, FIX_TITLE

SCRIPT = REPO / "scripts" / "rebuild_pr.py"
BRANCH = "rebuild/image-lock"
REPOSITORY = "thedebuggedlife/intel-gpu-exporter"
COMMITTED = "apk musl 1.0-r0\n"
NEW = "apk musl 1.1-r0\n"
NEWER = "apk musl 1.2-r0\n"
# The prefixes release-please acts on, from its conventional commit parser
COMMIT_INPUT = re.compile(
    r"^\s*(release-as|breaking[ -]change|[a-z]+(\([^)]*\))?!?):", re.IGNORECASE
)


class Repository:
    def __init__(self, path, bin_dir):
        self.path = path
        self.bin_dir = bin_dir
        self.lock_file = path.with_name("new.lock")
        self.state = {
            "calls": [],
            "fail": [],
            "next": 0,
            "next_pull": 50,
            "refs": {"main": "main-sha"},
            "locks": {"main-sha": COMMITTED},
            "trees": {},
            "commits": {},
            "pulls": [],
            "releases": [],
            "assets": {},
            "labels": [],
            "issues": [],
            "review_requests": [],
        }

    def release(self, tag, lock=None, draft=False):
        assets = []
        if lock is not None:
            identifier = str(len(self.state["assets"]) + 1)
            self.state["assets"][identifier] = lock
            assets = [{"name": "image.lock", "id": int(identifier)}]
        self.state["releases"].append(
            {"tag_name": tag, "draft": draft, "assets": assets}
        )

    def pull(self, **fields):
        self.state["refs"].setdefault(BRANCH, "branch-sha")
        self.state["locks"].setdefault("branch-sha", NEW)
        self.state["pulls"].append(
            {
                "number": 7,
                "title": FIX_TITLE,
                "body": "",
                "head": BRANCH,
                "state": "open",
                "isCrossRepository": False,
                "autoMergeRequest": None,
                "files": [{"path": "image.lock"}],
                "commits": [{"committedDate": datetime.now(UTC).isoformat()}],
            }
            | fields
        )

    def run(self, lock=NEW, built=None):
        self.lock_file.write_text(lock)
        self.path.write_text(json.dumps(self.state))
        env = os.environ | {
            "PATH": os.pathsep.join(
                [
                    str(self.bin_dir),
                    str(Path(sys.executable).parent),
                    os.environ["PATH"],
                ]
            ),
            "FAKE_GITHUB_STATE": str(self.path),
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_SHA": built or self.state["refs"]["main"],
        }
        result = subprocess.run(
            [str(SCRIPT), "--lock", str(self.lock_file)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.state = json.loads(self.path.read_text())
        return result

    @property
    def open_pulls(self):
        return [p for p in self.state["pulls"] if p["state"] == "open"]

    @property
    def branch_lock(self):
        return self.state["locks"][self.state["refs"][BRANCH]]

    def writes(self):
        reads = ("pr view",)
        for call in self.state["calls"]:
            joined = " ".join(call)
            if joined.startswith(reads) or (
                call[0] == "api" and "--method" not in call
            ):
                continue
            yield joined


def days_ago(days):
    moment = datetime.now(UTC) - timedelta(days=days)
    return [{"committedDate": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}]


@pytest.fixture
def repository(tmp_path):
    shim = tmp_path / "bin" / "gh"
    shim.parent.mkdir()
    shim.write_text((TESTS / "fake_github.py").read_text())
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return Repository(tmp_path / "github.json", shim.parent)


def test_unchanged_image_writes_nothing(repository):
    result = repository.run(COMMITTED)
    assert result.returncode == 0
    assert list(repository.writes()) == []


def test_unchanged_image_ignores_a_different_published_asset(repository):
    repository.release("v1.0.0", NEW)
    repository.run(COMMITTED)
    assert list(repository.writes()) == []


def test_change_already_published_records_it_with_no_release(repository):
    repository.release("v1.0.0", NEW)
    assert repository.run(NEW).returncode == 0
    assert [p["title"] for p in repository.open_pulls] == [CHORE_TITLE]


def test_change_not_yet_published_takes_a_patch_release(repository):
    repository.release("v1.0.0", NEWER)
    assert repository.run(NEW).returncode == 0
    assert [p["title"] for p in repository.open_pulls] == [FIX_TITLE]


def test_first_run_with_no_release_takes_a_patch_release(repository):
    assert repository.run(NEW).returncode == 0
    assert [p["title"] for p in repository.open_pulls] == [FIX_TITLE]


def test_a_draft_release_does_not_count(repository):
    repository.release("v1.0.0", NEW, draft=True)
    repository.run(NEW)
    assert [p["title"] for p in repository.open_pulls] == [FIX_TITLE]


def test_a_release_candidate_does_not_count(repository):
    repository.release("v2.0.0-rc.1", NEW)
    repository.run(NEW)
    assert [p["title"] for p in repository.open_pulls] == [FIX_TITLE]


def test_a_release_with_no_asset_does_not_count(repository):
    repository.release("v1.0.0")
    repository.run(NEW)
    assert [p["title"] for p in repository.open_pulls] == [FIX_TITLE]


def test_the_newest_version_is_compared_whatever_the_listing_order(repository):
    repository.release("v1.0.9", NEWER)
    repository.release("v1.0.10", NEW)
    repository.release("v1.0.2", COMMITTED)
    repository.run(NEW)
    assert [p["title"] for p in repository.open_pulls] == [CHORE_TITLE]


def test_new_pr_changes_only_the_lock_on_top_of_main(repository):
    repository.run(NEW)
    assert repository.branch_lock == NEW
    commit = repository.state["commits"][repository.state["refs"][BRANCH]]
    assert commit["parents"] == ["main-sha"]
    tree = repository.state["trees"][commit["tree"]]
    assert tree["base_tree"] == "tree-of-main-sha"


def test_new_pr_arms_auto_merge_with_a_fixed_message(repository):
    repository.run(NEW)
    pull = repository.open_pulls[0]
    assert pull["autoMergeRequest"]["commitHeadline"] == FIX_TITLE
    assert pull["autoMergeRequest"]["commitBody"] == pull["body"]


def test_body_holds_nothing_release_please_acts_on(repository):
    repository.run(NEW)
    pull = repository.open_pulls[0]
    assert pull["body"]
    for line in pull["body"].splitlines():
        assert not COMMIT_INPUT.search(line), line


def test_commit_message_is_the_title_alone(repository):
    repository.run(NEW)
    commit = repository.state["commits"][repository.state["refs"][BRANCH]]
    assert commit["message"] == FIX_TITLE


def test_repeat_run_with_the_same_change_reuses_the_pr(repository):
    repository.run(NEW)
    first = list(repository.writes())
    assert repository.run(NEW).returncode == 0
    assert len(repository.state["pulls"]) == 1
    assert list(repository.writes()) == first


def test_new_change_updates_the_open_pr(repository):
    repository.run(NEW)
    assert repository.run(NEWER).returncode == 0
    assert len(repository.state["pulls"]) == 1
    assert repository.branch_lock == NEWER


def test_update_starts_again_from_current_main(repository):
    repository.run(NEW)
    repository.state["refs"]["main"] = "later-sha"
    repository.state["locks"]["later-sha"] = COMMITTED
    repository.run(NEWER)
    commit = repository.state["commits"][repository.state["refs"][BRANCH]]
    assert commit["parents"] == ["later-sha"]


def test_armed_pr_with_the_right_subject_is_left_armed(repository):
    repository.pull(autoMergeRequest={"commitHeadline": FIX_TITLE})
    assert repository.run(NEW).returncode == 0
    assert list(repository.writes()) == []


def test_unarmed_open_pr_is_armed(repository):
    repository.pull()
    repository.run(NEW)
    subject = repository.open_pulls[0]["autoMergeRequest"]["commitHeadline"]
    assert subject == FIX_TITLE


def test_row_change_replaces_the_armed_subject(repository):
    repository.pull(autoMergeRequest={"commitHeadline": FIX_TITLE})
    repository.release("v1.0.0", NEW)
    assert repository.run(NEW).returncode == 0
    pull = repository.open_pulls[0]
    assert pull["title"] == CHORE_TITLE
    assert pull["autoMergeRequest"]["commitHeadline"] == CHORE_TITLE


def test_armed_subject_is_removed_before_anything_else_changes(repository):
    repository.pull(autoMergeRequest={"commitHeadline": CHORE_TITLE}, title=CHORE_TITLE)
    repository.state["locks"]["branch-sha"] = NEWER
    repository.run(NEW)
    writes = list(repository.writes())
    assert "--disable-auto" in writes[0]
    assert "--auto" in writes[-1]
    assert FIX_TITLE in writes[-1]


def test_stale_pr_is_closed_when_the_image_matches_main_again(repository):
    repository.pull(autoMergeRequest={"commitHeadline": FIX_TITLE})
    assert repository.run(COMMITTED).returncode == 0
    assert repository.open_pulls == []
    assert BRANCH not in repository.state["refs"]


def test_pr_from_an_earlier_run_that_has_not_merged_fails_the_run(repository):
    repository.pull(autoMergeRequest={"commitHeadline": FIX_TITLE}, commits=days_ago(7))
    result = repository.run(NEW)
    assert result.returncode != 0
    assert "#7" in result.stderr
    assert list(repository.writes()) == []


def test_stuck_pr_is_not_armed_again_after_a_person_disarmed_it(repository):
    repository.pull(commits=days_ago(7))
    assert repository.run(NEW).returncode != 0
    assert repository.open_pulls[0]["autoMergeRequest"] is None


def test_new_change_on_an_old_pr_is_an_update_not_a_stuck_pr(repository):
    repository.pull(commits=days_ago(7))
    assert repository.run(NEWER).returncode == 0
    assert repository.branch_lock == NEWER


def test_row_change_on_an_old_pr_is_an_update_not_a_stuck_pr(repository):
    repository.pull(autoMergeRequest={"commitHeadline": FIX_TITLE}, commits=days_ago(7))
    repository.release("v1.0.0", NEW)
    assert repository.run(NEW).returncode == 0
    assert repository.open_pulls[0]["title"] == CHORE_TITLE


def test_held_pr_fails_the_run_once_it_has_waited(repository):
    repository.pull(files=[{"path": "Dockerfile"}], commits=days_ago(7))
    assert repository.run(NEW).returncode != 0
    assert repository.state["review_requests"] == [7]


def test_a_build_of_an_older_commit_changes_nothing(repository):
    result = repository.run(NEW, built="older-sha")
    assert result.returncode == 0
    assert "older-sha" in result.stdout
    assert list(repository.writes()) == []


def test_a_build_of_an_older_commit_does_not_close_a_pr(repository):
    repository.pull()
    repository.run(COMMITTED, built="older-sha")
    assert len(repository.open_pulls) == 1


def test_a_branch_with_a_longer_name_is_not_mistaken_for_ours(repository):
    repository.state["refs"][f"{BRANCH}-old"] = "other-sha"
    repository.state["locks"]["other-sha"] = NEWER
    assert repository.run(NEW).returncode == 0
    assert repository.state["refs"][f"{BRANCH}-old"] == "other-sha"
    assert repository.branch_lock == NEW


def test_closed_pr_is_not_reused(repository):
    repository.pull(state="closed")
    assert repository.run(NEW).returncode == 0
    assert [p["number"] for p in repository.open_pulls] == [50]


def test_fork_pr_on_the_same_branch_name_is_never_touched(repository):
    repository.pull(number=66, isCrossRepository=True, title="fix(deps): trust me")
    repository.state["refs"].pop(BRANCH)
    repository.state["locks"].pop("branch-sha")
    assert repository.run(NEW).returncode == 0
    fork = next(p for p in repository.state["pulls"] if p["number"] == 66)
    assert fork["autoMergeRequest"] is None
    assert fork["title"] == "fix(deps): trust me"
    assert [p["number"] for p in repository.state["pulls"]] == [66, 50]


def test_leftover_branch_with_no_pr_is_moved(repository):
    repository.state["refs"][BRANCH] = "old-sha"
    repository.state["locks"]["old-sha"] = NEWER
    assert repository.run(NEW).returncode == 0
    assert repository.branch_lock == NEW
    assert len(repository.open_pulls) == 1


def test_pr_with_other_files_is_held_and_not_rewritten(repository):
    repository.pull(
        autoMergeRequest={"commitHeadline": FIX_TITLE},
        files=[{"path": "image.lock"}, {"path": "Dockerfile"}],
    )
    assert repository.run(NEWER).returncode == 0
    assert repository.open_pulls[0]["autoMergeRequest"] is None
    assert repository.state["review_requests"] == [7]
    assert repository.branch_lock == NEW


def test_pr_with_other_files_is_not_closed_either(repository):
    repository.pull(files=[{"path": "Dockerfile"}])
    assert repository.run(COMMITTED).returncode == 0
    assert len(repository.open_pulls) == 1
    assert repository.state["review_requests"] == [7]


@pytest.mark.parametrize(
    "failure",
    ["repos/thedebuggedlife/intel-gpu-exporter/releases", "contents/image.lock"],
)
def test_a_failed_lookup_is_not_read_as_nothing_there(repository, failure):
    repository.state["fail"] = [failure]
    result = repository.run(NEW)
    assert result.returncode != 0
    assert list(repository.writes()) == []


def test_a_failed_arm_fails_the_run(repository):
    repository.state["fail"] = ["pr merge"]
    assert repository.run(NEW).returncode != 0


def test_a_failed_hold_fails_the_run(repository):
    repository.pull(files=[{"path": "Dockerfile"}])
    repository.state["fail"] = ["requested_reviewers"]
    assert repository.run(NEW).returncode != 0
