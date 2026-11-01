import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import REPO, TESTS

SCRIPT = REPO / "scripts" / "track-failure.sh"
LABEL = "automation-failure"
RUN_URL = "https://github.com/thedebuggedlife/intel-gpu-exporter/actions/runs/123"


class Tracker:
    def __init__(self, path, bin_dir):
        self.path = path
        self.bin_dir = bin_dir
        self.state = {"calls": [], "fail": [], "labels": [], "issues": [], "jobs": 1}

    def issue(self, state="open", labels=(LABEL,), **fields):
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
            | fields
        )
        return number

    def run(self, workflow="rebuild", conclusion="failure"):
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
            "GITHUB_REPOSITORY": "thedebuggedlife/intel-gpu-exporter",
            "GITHUB_SERVER_URL": "https://github.com",
            "FAILED_CONCLUSION": conclusion,
            "FAILED_RUN_ID": "123",
            "FAILED_SHA": "abc123",
            "FAILED_WORKFLOW": workflow,
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


def test_a_labelled_pull_request_is_not_the_tracking_issue(tracker):
    pull = tracker.issue(pull_request={"url": "a pull request"})
    tracker.run()
    assert tracker.state["issues"][0]["number"] == pull
    assert tracker.state["issues"][0]["comments"] == []
    assert len(tracker.open_issues) == 2


def test_a_failed_label_update_does_not_stop_the_report(tracker):
    tracker.state["labels"] = [LABEL]
    tracker.state["fail"] = ["label create"]
    assert tracker.run().returncode == 0
    assert len(tracker.open_issues) == 1


def test_a_failed_lookup_opens_nothing_and_fails_loudly(tracker):
    tracker.state["fail"] = ["/issues?"]
    result = tracker.run()
    assert result.returncode != 0
    assert tracker.open_issues == []


def test_a_failed_report_names_the_run_in_the_log(tracker):
    tracker.state["fail"] = ["/issues?"]
    assert RUN_URL in tracker.run().stdout


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure"])
def test_any_other_conclusion_is_reported_without_counting_jobs(tracker, conclusion):
    assert tracker.run(conclusion=conclusion).returncode == 0
    assert len(tracker.open_issues) == 1
    assert not any("/jobs" in " ".join(c) for c in tracker.state["calls"])


def test_a_timeout_concludes_cancelled_and_is_reported(tracker):
    tracker.state["jobs"] = 1
    assert tracker.run(conclusion="cancelled").returncode == 0
    assert len(tracker.open_issues) == 1


def test_a_run_dropped_from_the_queue_reports_nothing(tracker):
    tracker.state["jobs"] = 0
    result = tracker.run(conclusion="cancelled")
    assert result.returncode == 0
    assert tracker.open_issues == []
    assert "::error::" not in result.stdout


def test_a_failed_job_count_still_reports_a_cancelled_run(tracker):
    tracker.state["jobs"] = 0
    tracker.state["fail"] = ["/jobs"]
    assert tracker.run(conclusion="cancelled").returncode == 0
    assert len(tracker.open_issues) == 1


def test_the_report_names_the_run_that_failed_not_the_tracker(tracker):
    tracker.run("release")
    body = tracker.open_issues[0]["body"]
    assert "abc123" in body
    assert RUN_URL in body
