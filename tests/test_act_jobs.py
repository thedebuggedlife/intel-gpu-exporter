import json
import os
import stat
import sys
from pathlib import Path

import pytest
import yaml
from helpers import REPO, TESTS, condition_holds, read_outputs, run_job

WORKFLOWS = REPO / ".github" / "workflows"
REPOSITORY = "thedebuggedlife/intel-gpu-exporter"
SHA = "a" * 40
OTHER_SHA = "b" * 40
TITLE = "fix(deps): bump a from 1.0.0 to 1.0.1"
TOKEN = {"steps.token.outputs.token": "app-token"}


def job_of(workflow, name):
    return yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"][name]


class GitHub:
    def __init__(self, path, bin_dir):
        self.path = path
        self.bin_dir = bin_dir
        self.outputs = path.with_name("outputs")
        self.state = {
            "calls": [],
            "fail": [],
            "decision": {"decision": "merge", "number": 42, "sha": SHA, "title": TITLE},
            "pull_details": {"42": self.dependabot_pull()},
            "pulls": [self.listed_pull()],
            "review_requests": [],
        }

    @staticmethod
    def dependabot_pull(**changes):
        pull = {
            "user": {"login": "dependabot[bot]"},
            "base": {"ref": "main"},
            "head": {"repo": {"full_name": REPOSITORY}, "sha": SHA},
            "state": "open",
        }
        return pull | changes

    @staticmethod
    def listed_pull(title="chore(main): release 1.0.0"):
        return {
            "number": 42,
            "title": title,
            "head": "release-please--branches--main",
            "sha": SHA,
            "state": "open",
            "isCrossRepository": False,
            "autoMergeRequest": None,
        }

    def environment(self, cwd):
        return os.environ | {
            "PATH": os.pathsep.join(
                [
                    str(self.bin_dir),
                    str(Path(sys.executable).parent),
                    os.environ["PATH"],
                ]
            ),
            "FAKE_GITHUB_STATE": str(self.path),
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_REPOSITORY_OWNER": "thedebuggedlife",
            "GITHUB_OUTPUT": str(self.outputs),
        }

    def run(self, job, values=None, cwd=None):
        self.outputs.unlink(missing_ok=True)
        self.path.write_text(json.dumps(self.state))
        result = run_job(
            job,
            (values or {}) | TOKEN,
            self.environment(cwd),
            cwd or self.path.parent,
        )
        self.state = json.loads(self.path.read_text())
        return result

    @property
    def merges(self):
        return [c for c in self.state["calls"] if c[:2] == ["pr", "merge"]]


@pytest.fixture
def github(tmp_path):
    shim = tmp_path / "bin" / "gh"
    shim.parent.mkdir()
    shim.write_text((TESTS / "fake_github.py").read_text())
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return GitHub(tmp_path / "github.json", shim.parent)


def verify(github):
    return github.run(job_of("dependabot-merge.yaml", "verify"))


def test_verify_passes_on_a_decision_that_still_applies(github):
    assert verify(github).returncode == 0
    assert read_outputs(github.outputs) == {
        "decision": "merge",
        "number": "42",
        "sha": SHA,
        "title": TITLE,
    }


def test_verify_makes_no_write(github):
    verify(github)
    assert not any(
        "--method" in c or c[:2] == ["pr", "merge"] for c in github.state["calls"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"head": {"repo": {"full_name": REPOSITORY}, "sha": OTHER_SHA}},
        {"head": {"repo": {"full_name": "someone/fork"}, "sha": SHA}},
        {"head": {"repo": None, "sha": SHA}},
        {"user": {"login": "someone"}},
        {"base": {"ref": "other"}},
        {"state": "closed"},
    ],
    ids=str,
)
def test_verify_drops_a_decision_about_a_pr_that_changed(github, change):
    github.state["pull_details"]["42"] = github.dependabot_pull(**change)
    assert verify(github).returncode == 0
    assert read_outputs(github.outputs)["decision"] == "none"


@pytest.mark.parametrize(
    "change",
    [
        {"decision": "publish"},
        {"decision": ""},
        {"number": 0},
        {"number": "42 --admin"},
        {"sha": "main"},
        {"title": "feat!: a"},
        {"title": f"{TITLE}\nnumber=1"},
    ],
    ids=str,
)
def test_verify_refuses_a_malformed_decision(github, change):
    github.state["decision"] |= change
    assert verify(github).returncode != 0
    assert not github.outputs.exists()


def test_verify_ignores_the_subject_of_a_held_pr(github):
    github.state["decision"] |= {"decision": "review", "title": "ci(deps): bump a"}
    assert verify(github).returncode == 0
    assert read_outputs(github.outputs)["decision"] == "review"


def act_values(**changes):
    return {
        f"needs.verify.outputs.{k}": v
        for k, v in (
            {"decision": "merge", "number": "42", "sha": SHA, "title": TITLE} | changes
        ).items()
    }


def act(github, **changes):
    return github.run(job_of("dependabot-merge.yaml", "act"), act_values(**changes))


def test_a_merge_is_tied_to_the_commit_that_was_decided(github):
    assert act(github).returncode == 0
    call = github.merges[0]
    assert call[call.index("--match-head-commit") + 1] == SHA


def test_a_merge_sets_a_fixed_body_and_the_validated_subject(github):
    act(github)
    call = github.merges[0]
    assert call[call.index("--subject") + 1] == TITLE
    assert call[call.index("--body") + 1] == "Automated dependency update."


def test_a_review_decision_never_arms(github):
    assert act(github, decision="review").returncode == 0
    assert not any("--auto" in c for c in github.merges)
    assert github.state["review_requests"] == [42]


@pytest.mark.parametrize(
    "change",
    [
        {"decision": "publish"},
        {"decision": ""},
        {"number": "1 2"},
        {"number": "$(id)"},
        {"sha": "main"},
        {"title": "feat!: a"},
    ],
    ids=str,
)
def test_the_job_holding_the_key_refuses_a_bad_decision(github, change):
    result = act(github, **change)
    assert result.returncode != 0
    assert "refusing" in result.stderr
    assert github.state["calls"] == []


def test_a_decision_of_none_never_reaches_the_key():
    job = job_of("dependabot-merge.yaml", "act")
    assert not condition_holds(job["if"], act_values(decision="none"))
    assert condition_holds(job["if"], act_values(decision="review"))


def release_decide(github):
    return github.run(job_of("release.yaml", "decide"), cwd=REPO)


def test_release_decide_merges_the_first_release(github):
    assert release_decide(github).returncode == 0
    assert read_outputs(github.outputs) == {
        "decision": "merge",
        "number": "42",
        "sha": SHA,
    }


def test_release_decide_holds_a_major_version(github):
    github.state["pulls"] = [github.listed_pull("chore(main): release 2.0.0")]
    assert release_decide(github).returncode == 0
    assert read_outputs(github.outputs)["decision"] == "review"


def test_release_decide_holds_a_title_it_cant_read(github):
    github.state["pulls"] = [github.listed_pull("chore(main): release soon")]
    assert release_decide(github).returncode == 0
    assert read_outputs(github.outputs)["decision"] == "review"


def test_release_decide_finds_nothing_with_no_release_pr(github):
    github.state["pulls"] = []
    assert release_decide(github).returncode == 0
    assert read_outputs(github.outputs) == {"decision": "none"}


def test_release_decide_ignores_a_fork_using_the_branch_name(github):
    github.state["pulls"] = [github.listed_pull() | {"isCrossRepository": True}]
    release_decide(github)
    assert read_outputs(github.outputs) == {"decision": "none"}


def test_release_decide_writes_nothing(github):
    release_decide(github)
    assert not any(
        "--method" in c or c[:2] == ["pr", "merge"] for c in github.state["calls"]
    )


def release_act(github, **changes):
    outputs = {"decision": "merge", "number": "42", "sha": SHA} | changes
    values = {f"needs.decide.outputs.{k}": v for k, v in outputs.items()}
    return github.run(job_of("release.yaml", "auto-merge"), values)


def test_release_act_is_tied_to_the_commit_that_was_decided(github):
    assert release_act(github).returncode == 0
    call = github.merges[0]
    assert call[call.index("--match-head-commit") + 1] == SHA


def test_release_act_holds_without_arming(github):
    assert release_act(github, decision="review").returncode == 0
    assert not any("--auto" in c for c in github.merges)
    assert github.state["review_requests"] == [42]


@pytest.mark.parametrize(
    "change",
    [{"decision": "none"}, {"number": "x"}, {"sha": "main"}],
    ids=str,
)
def test_release_act_refuses_a_bad_decision(github, change):
    result = release_act(github, **change)
    assert result.returncode != 0
    assert github.state["calls"] == []
