import json
import subprocess

import pytest
import yaml
from helpers import REPO

from scripts import dependabot_policy
from scripts.dependabot_policy import decide

PATCH = "version-update:semver-patch"
MINOR = "version-update:semver-minor"
MAJOR = "version-update:semver-major"
DIGEST = "fix(deps): bump library/alpine from `294b683` to `be26560`"
TAG_CHANGE = "fix(deps): bump alpine from 3.24 to 3.25"
PIP = "fix(deps): bump a from 1.0.0 to 1.1.0"
PATCH_TITLE = "fix(deps): bump a from 1.0.0 to 1.0.1"
REPOSITORY = "thedebuggedlife/intel-gpu-exporter"
SECURITY = "fix(deps): [security] bump a from 1.0.0 to 1.1.0"


REQUIREMENTS = ["requirements.txt"]
DEV_REQUIREMENTS = ["requirements-dev.txt"]
DOCKERFILE = ["Dockerfile"]
WORKFLOW = [".github/workflows/ci.yaml"]
COMPOSITE = [".github/actions/lint-and-test/action.yml"]


@pytest.mark.parametrize(
    ("title", "ecosystem", "files", "update_type", "ghsa_id", "expected"),
    [
        (
            "chore(deps): bump a from 1.0.0 to 1.0.1",
            "pip",
            DEV_REQUIREMENTS,
            PATCH,
            "",
            "merge",
        ),
        (PATCH_TITLE, "pip", REQUIREMENTS, PATCH, "", "merge"),
        (PIP, "pip", REQUIREMENTS, MINOR, "", "review"),
        (
            "fix(deps): bump a from 1.0.0 to 2.0.0",
            "pip",
            REQUIREMENTS,
            MAJOR,
            "",
            "review",
        ),
        (PIP, "pip", REQUIREMENTS, "", "", "review"),
        (PIP, "pip", REQUIREMENTS, "something-new", "", "review"),
        (DIGEST, "docker", DOCKERFILE, "", "", "merge"),
        (DIGEST, "docker", DOCKERFILE, PATCH, "", "merge"),
        (DIGEST, "docker", DOCKERFILE, MAJOR, "", "merge"),
        (
            "fix(deps): Bump alpine from `294b683` to `be26560`",
            "docker",
            DOCKERFILE,
            "",
            "",
            "merge",
        ),
        (
            "fix(deps): [security] bump alpine from `294b683` to `be26560`",
            "docker",
            DOCKERFILE,
            "",
            "",
            "merge",
        ),
        (DIGEST, "pip", REQUIREMENTS, "", "", "review"),
        (TAG_CHANGE, "docker", DOCKERFILE, MINOR, "", "review"),
        (TAG_CHANGE, "docker", DOCKERFILE, "", "", "review"),
        (TAG_CHANGE, "docker", DOCKERFILE, PATCH, "", "review"),
        (
            "fix(deps): bump alpine from 3.24 to 4.0",
            "docker",
            DOCKERFILE,
            PATCH,
            "",
            "review",
        ),
        (
            "fix(deps): [security] bump alpine from 3.24 to 3.25",
            "docker",
            DOCKERFILE,
            MINOR,
            "GHSA-x",
            "review",
        ),
        (
            "fix(deps): bump alpine from 3.24 to `be26560`",
            "docker",
            DOCKERFILE,
            "",
            "",
            "review",
        ),
        (
            "fix(deps): bump alpine from 3.24.1 to 3.24.2",
            "docker",
            DOCKERFILE,
            PATCH,
            "",
            "merge",
        ),
        (SECURITY, "pip", REQUIREMENTS, MINOR, "GHSA-x", "merge"),
        (SECURITY, "pip", REQUIREMENTS, PATCH, "GHSA-x", "merge"),
        (SECURITY, "pip", REQUIREMENTS, MAJOR, "GHSA-x", "review"),
        (SECURITY, "pip", REQUIREMENTS, "", "GHSA-x", "review"),
        (SECURITY, "pip", REQUIREMENTS, MINOR, "", "review"),
    ],
)
def test_decide(title, ecosystem, files, update_type, ghsa_id, expected):
    assert decide(title, ecosystem, files, update_type, ghsa_id) == expected


@pytest.mark.parametrize("update_type", [PATCH, MINOR, MAJOR, ""])
@pytest.mark.parametrize("ghsa_id", ["", "GHSA-x"])
def test_every_action_update_waits_for_a_person(update_type, ghsa_id):
    title = "ci(deps): bump a from 1 to 2"
    assert decide(title, "github_actions", WORKFLOW, update_type, ghsa_id) == "review"
    assert decide(title, "github_actions", COMPOSITE, update_type, ghsa_id) == "review"


@pytest.mark.parametrize("update_type", [PATCH, MINOR])
def test_the_ecosystem_alone_holds_an_action_update(update_type):
    assert (
        decide(PATCH_TITLE, "github_actions", REQUIREMENTS, update_type, "") == "review"
    )


@pytest.mark.parametrize("ecosystem", ["", "npm_and_yarn", "submodules", "bundler"])
def test_an_ecosystem_nobody_chose_waits_for_a_person(ecosystem):
    assert decide(PATCH_TITLE, ecosystem, REQUIREMENTS, PATCH, "") == "review"


@pytest.mark.parametrize(
    "files",
    [
        WORKFLOW,
        COMPOSITE,
        [*REQUIREMENTS, *COMPOSITE],
        ["scripts/release.py"],
        [],
    ],
)
def test_files_outside_the_manifests_wait_whatever_the_metadata_says(files):
    assert decide(PATCH_TITLE, "pip", files, PATCH, "") == "review"
    assert decide(DIGEST, "docker", files, "", "") == "review"


@pytest.mark.parametrize(
    "title",
    [
        "ci(deps): bump a from 1.0.0 to 1.0.1",
        "feat: bump a from 1.0.0 to 1.0.1",
        "fix(deps)!: bump a from 1.0.0 to 1.0.1",
        "Bump a from 1.0.0 to 1.0.1",
        "fix(deps): bump a\nRelease-As: 9.0.0",
    ],
)
def test_a_subject_outside_the_expected_form_waits_for_a_person(title):
    assert decide(title, "pip", REQUIREMENTS, PATCH, "") == "review"


@pytest.fixture
def github(monkeypatch):
    responses = {"/files": "requirements.txt\n", "/dependabot/alerts": ""}
    calls = []

    def answer(path, *options):
        calls.append((path, options))
        return next(body for key, body in responses.items() if key in path)

    monkeypatch.setattr(dependabot_policy, "api", answer)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("PR_NUMBER", "42")
    monkeypatch.setenv("PR_TITLE", PATCH_TITLE)
    monkeypatch.setenv("HEAD_SHA", "a" * 40)
    monkeypatch.setenv("ECOSYSTEM", "pip")
    monkeypatch.setenv("UPDATE_TYPE", PATCH)
    monkeypatch.setenv("DEPENDENCY", "a-b")
    return responses, calls


def test_cli_prints_the_decision_and_what_it_covers(github, capsys):
    assert dependabot_policy.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "decision": "merge",
        "number": 42,
        "sha": "a" * 40,
        "title": PATCH_TITLE,
    }


def test_cli_reads_the_files_from_the_api(github, capsys):
    responses, _ = github
    responses["/files"] = ".github/actions/lint-and-test/action.yml\n"
    dependabot_policy.main()
    assert json.loads(capsys.readouterr().out)["decision"] == "review"


def test_cli_treats_missing_outputs_as_empty(github, monkeypatch, capsys):
    for name in ("UPDATE_TYPE", "DEPENDENCY"):
        monkeypatch.delenv(name)
    dependabot_policy.main()
    assert json.loads(capsys.readouterr().out)["decision"] == "review"


def test_an_open_alert_makes_it_a_security_update(github, monkeypatch, capsys):
    responses, _ = github
    responses["/dependabot/alerts"] = "GHSA-x\n"
    monkeypatch.setenv("UPDATE_TYPE", MINOR)
    dependabot_policy.main()
    assert json.loads(capsys.readouterr().out)["decision"] == "merge"


def test_only_open_alerts_for_this_package_count(github):
    dependabot_policy.main()
    _, calls = github
    path = next(p for p, _ in calls if "/dependabot/alerts" in p)
    assert path == (
        f"repos/{REPOSITORY}/dependabot/alerts"
        "?state=open&package=a-b&manifest=requirements.txt"
    )


def test_a_failed_alert_lookup_fails_instead_of_reading_as_no_alert(monkeypatch):
    monkeypatch.setattr(
        dependabot_policy.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "HTTP 403"),
    )
    with pytest.raises(SystemExit) as failure:
        dependabot_policy.open_alert(REPOSITORY, "a", ["requirements.txt"])
    assert "HTTP 403" in str(failure.value)


@pytest.fixture(scope="module")
def updates():
    config = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text())
    assert config["version"] == 2
    return {entry["package-ecosystem"]: entry for entry in config["updates"]}


def test_every_ecosystem_is_covered(updates):
    assert set(updates) == {"pip", "docker", "github-actions"}


def test_runtime_updates_release_and_dev_tools_dont(updates):
    assert updates["pip"]["commit-message"] == {
        "prefix": "fix(deps)",
        "prefix-development": "chore(deps)",
    }


def test_the_base_image_releases(updates):
    assert updates["docker"]["commit-message"] == {"prefix": "fix(deps)"}


def test_actions_dont_release(updates):
    assert updates["github-actions"]["commit-message"] == {"prefix": "ci(deps)"}


@pytest.mark.parametrize("ecosystem", ["pip", "docker", "github-actions"])
def test_each_ecosystem_is_weekly_with_a_cooldown(updates, ecosystem):
    entry = updates[ecosystem]
    assert entry["schedule"] == {"interval": "weekly"}
    assert entry["cooldown"] == {"default-days": 7}
    assert "groups" not in entry


@pytest.mark.parametrize("ecosystem", ["pip", "docker"])
def test_manifests_are_at_the_root(updates, ecosystem):
    assert updates[ecosystem]["directory"] == "/"


def test_actions_cover_workflows_and_composite_actions(updates):
    assert updates["github-actions"]["directories"] == ["/", "/.github/actions/*"]
