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
SECURITY = "fix(deps): [security] bump a from 1.0.0 to 1.1.0"


@pytest.mark.parametrize(
    ("title", "ecosystem", "update_type", "alert_state", "ghsa_id", "expected"),
    [
        ("chore(deps): bump a from 1.0.0 to 1.0.1", "pip", PATCH, "", "", "merge"),
        ("ci(deps): bump a from 1 to 2", "github_actions", PATCH, "", "", "merge"),
        (PIP, "pip", MINOR, "", "", "review"),
        ("fix(deps): bump a from 1.0.0 to 2.0.0", "pip", MAJOR, "", "", "review"),
        (PIP, "pip", "", "", "", "review"),
        (PIP, "pip", "something-new", "", "", "review"),
        (DIGEST, "docker", "", "", "", "merge"),
        (DIGEST, "docker", PATCH, "", "", "merge"),
        (DIGEST, "docker", MAJOR, "", "", "merge"),
        (
            "fix(deps): Bump alpine from `294b683` to `be26560`",
            "docker",
            "",
            "",
            "",
            "merge",
        ),
        (
            "fix(deps): [security] bump alpine from `294b683` to `be26560`",
            "docker",
            "",
            "",
            "",
            "merge",
        ),
        (DIGEST, "pip", "", "", "", "review"),
        (TAG_CHANGE, "docker", MINOR, "", "", "review"),
        (TAG_CHANGE, "docker", "", "", "", "review"),
        (
            "fix(deps): bump alpine from 3.24 to `be26560`",
            "docker",
            "",
            "",
            "",
            "review",
        ),
        (
            "fix(deps): bump alpine from 3.24.1 to 3.24.2",
            "docker",
            PATCH,
            "",
            "",
            "merge",
        ),
        (SECURITY, "pip", MINOR, "OPEN", "GHSA-x", "merge"),
        (SECURITY, "pip", PATCH, "OPEN", "GHSA-x", "merge"),
        (SECURITY, "pip", MAJOR, "OPEN", "GHSA-x", "review"),
        (SECURITY, "pip", "", "OPEN", "GHSA-x", "review"),
        (SECURITY, "pip", MINOR, "FIXED", "GHSA-x", "review"),
        (SECURITY, "pip", MINOR, "DISMISSED", "GHSA-x", "review"),
        (SECURITY, "pip", MINOR, "OPEN", "", "review"),
        (SECURITY, "pip", MINOR, "", "", "review"),
    ],
)
def test_decide(title, ecosystem, update_type, alert_state, ghsa_id, expected):
    assert decide(title, ecosystem, update_type, alert_state, ghsa_id) == expected


def test_cli_reads_the_environment(monkeypatch, capsys):
    monkeypatch.setenv("PR_TITLE", DIGEST)
    monkeypatch.setenv("ECOSYSTEM", "docker")
    monkeypatch.setenv("UPDATE_TYPE", "")
    monkeypatch.setenv("ALERT_STATE", "")
    monkeypatch.setenv("GHSA_ID", "")
    assert dependabot_policy.main() == 0
    assert capsys.readouterr().out.strip() == "merge"


def test_cli_treats_missing_outputs_as_empty(monkeypatch, capsys):
    monkeypatch.setenv("PR_TITLE", PIP)
    monkeypatch.setenv("ECOSYSTEM", "pip")
    for name in ("UPDATE_TYPE", "ALERT_STATE", "GHSA_ID"):
        monkeypatch.delenv(name, raising=False)
    assert dependabot_policy.main() == 0
    assert capsys.readouterr().out.strip() == "review"


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
