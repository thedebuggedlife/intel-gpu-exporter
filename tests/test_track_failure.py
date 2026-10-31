import json
import os
import stat
import subprocess

import pytest
from helpers import REPO, TESTS

SCRIPT = REPO / "scripts" / "track-failure.sh"
LABEL = "automation-failure"
RUN_URL = "https://github.com/thedebuggedlife/intel-gpu-exporter/actions/runs/123"


class Tracker:
    def __init__(self, path, bin_dir):
        self.path = path
        self.bin_dir = bin_dir
        self.state = {"calls": [], "fail": [], "labels": [], "issues": []}

    def issue(self, state="open", labels=(LABEL,)):
        number = len(self.state["issues"]) + 100
        self.state["issues"].append(
            {
                "number": number,
                "state": state,
                "labels": list(labels),
                "title": "existing",
                "body": "existing",
                "comments": [],
            }
        )
        return number

    def run(self, workflow="rebuild"):
        self.path.write_text(json.dumps(self.state))
        env = os.environ | {
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GITHUB_STATE": str(self.path),
            "GITHUB_REPOSITORY": "thedebuggedlife/intel-gpu-exporter",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SHA": "abc123",
            "GITHUB_WORKFLOW": workflow,
        }
        result = subprocess.run(
            [str(SCRIPT)], env=env, capture_output=True, text=True, check=False
        )
        self.state = json.loads(self.path.read_text())
        return result

    @property
    def open_issues(self):
        return [i for i in self.state["issues"] if i["state"] == "open"]


@pytest.fixture
def tracker(tmp_path):
    shim = tmp_path / "bin" / "gh"
    shim.parent.mkdir()
    shim.write_text((TESTS / "fake_github.py").read_text())
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return Tracker(tmp_path / "github.json", shim.parent)


def test_first_failure_opens_a_labelled_issue(tracker):
    assert tracker.run().returncode == 0
    assert len(tracker.open_issues) == 1
    assert tracker.open_issues[0]["labels"] == [LABEL]


def test_issue_links_the_run_and_names_the_workflow(tracker):
    tracker.run("release")
    body = tracker.open_issues[0]["body"]
    assert RUN_URL in body
    assert "`release`" in body


def test_issue_notifies_the_maintainer(tracker):
    tracker.run()
    assert "@avargaskun" in tracker.open_issues[0]["body"]


def test_repeat_comments_instead_of_opening_another(tracker):
    tracker.run("rebuild")
    assert tracker.run("release").returncode == 0
    assert len(tracker.open_issues) == 1
    comment = tracker.open_issues[0]["comments"]
    assert len(comment) == 1
    assert RUN_URL in comment[0]
    assert "`release`" in comment[0]


def test_a_closed_issue_is_not_reused(tracker):
    closed = tracker.issue(state="closed")
    tracker.run()
    assert len(tracker.open_issues) == 1
    assert tracker.open_issues[0]["number"] != closed


def test_an_issue_with_another_label_is_not_reused(tracker):
    tracker.issue(labels=("bug",))
    tracker.run()
    labelled = [i for i in tracker.open_issues if LABEL in i["labels"]]
    assert len(labelled) == 1
    assert labelled[0]["comments"] == []


def test_a_person_closing_the_issue_is_the_only_way_it_closes(tracker):
    tracker.issue()
    tracker.run()
    assert len(tracker.open_issues) == 1
    assert not any(c[:2] == ["issue", "close"] for c in tracker.state["calls"])


def test_oldest_open_issue_gets_the_comment_if_two_exist(tracker):
    oldest = tracker.issue()
    tracker.issue()
    tracker.run()
    target = next(i for i in tracker.open_issues if i["number"] == oldest)
    assert len(target["comments"]) == 1


def test_a_failed_label_update_does_not_stop_the_report(tracker):
    tracker.state["labels"] = [LABEL]
    tracker.state["fail"] = ["label create"]
    assert tracker.run().returncode == 0
    assert len(tracker.open_issues) == 1


def test_a_failed_lookup_opens_nothing_and_fails_loudly(tracker):
    tracker.state["fail"] = ["issue list"]
    result = tracker.run()
    assert result.returncode != 0
    assert tracker.open_issues == []


def test_a_failed_report_names_the_run_in_the_log(tracker):
    tracker.state["fail"] = ["issue list"]
    assert RUN_URL in tracker.run().stdout
