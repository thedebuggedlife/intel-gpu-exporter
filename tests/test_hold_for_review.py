import json
import os
import stat
import subprocess

import pytest
from helpers import REPO, TESTS

SCRIPT = REPO / "scripts" / "hold-for-review.sh"
REPOSITORY = "thedebuggedlife/intel-gpu-exporter"


@pytest.fixture
def hold(tmp_path):
    shim = tmp_path / "bin" / "gh"
    shim.parent.mkdir()
    shim.write_text((TESTS / "fake_gh.py").read_text())
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state.json"

    def run(armed, fail=()):
        state.write_text(json.dumps({"armed": armed, "fail": list(fail), "calls": []}))
        env = os.environ | {
            "PATH": f"{shim.parent}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_STATE": str(state),
            "GITHUB_REPOSITORY": REPOSITORY,
        }
        result = subprocess.run(
            [str(SCRIPT), "42"], env=env, capture_output=True, check=False
        )
        return result.returncode, json.loads(state.read_text())["calls"]

    return run


def disarms(call):
    return "--disable-auto" in call


def requests_review(call):
    return call[0] == "api" and any("requested_reviewers" in a for a in call)


def test_armed_pr_is_disarmed_before_review_is_requested(hold):
    code, calls = hold(armed=True)
    assert code == 0
    assert [disarms(c) for c in calls].count(True) == 1
    assert next(i for i, c in enumerate(calls) if disarms(c)) < next(
        i for i, c in enumerate(calls) if requests_review(c)
    )


def test_disarm_targets_the_pr(hold):
    _, calls = hold(armed=True)
    call = next(c for c in calls if disarms(c))
    assert call[:3] == ["pr", "merge", "42"]
    assert REPOSITORY in call


def test_unarmed_pr_only_requests_review(hold):
    code, calls = hold(armed=False)
    assert code == 0
    assert not any(disarms(c) for c in calls)
    assert any(requests_review(c) for c in calls)


def test_review_is_requested_from_the_maintainer(hold):
    _, calls = hold(armed=False)
    call = next(c for c in calls if requests_review(c))
    assert f"repos/{REPOSITORY}/pulls/42/requested_reviewers" in call
    assert "reviewers[]=avargaskun" in call


def test_arming_is_read_from_the_pr(hold):
    _, calls = hold(armed=False)
    assert calls[0][:3] == ["pr", "view", "42"]
    assert "autoMergeRequest" in calls[0]


def test_failed_disarm_fails_loudly(hold):
    code, calls = hold(armed=True, fail=[["pr", "merge"]])
    assert code != 0
    assert not any(requests_review(c) for c in calls)


def test_failed_lookup_doesnt_assume_unarmed(hold):
    code, calls = hold(armed=True, fail=[["pr", "view"]])
    assert code != 0
    assert not any(requests_review(c) for c in calls)


def test_failed_review_request_still_leaves_the_pr_disarmed(hold):
    code, calls = hold(armed=True, fail=[["api", "--method"]])
    assert code != 0
    assert any(disarms(c) for c in calls)
