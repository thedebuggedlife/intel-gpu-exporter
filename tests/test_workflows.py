import re

import pytest
import yaml
from helpers import REPO

WORKFLOWS = REPO / ".github" / "workflows"
ACTIONS = REPO / ".github" / "actions"
PINNED = re.compile(r"uses:\s+\S+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")


def workflow_paths():
    return sorted(WORKFLOWS.glob("*.yaml"))


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def steps_of(job):
    return job.get("steps", [])


def all_jobs():
    for path in workflow_paths():
        for name, job in yaml.safe_load(path.read_text())["jobs"].items():
            yield path.name, name, job


def uses_lines():
    for path in [*workflow_paths(), *ACTIONS.rglob("action.yml")]:
        for line in path.read_text().splitlines():
            if re.match(r"\s*(- )?uses:", line) and "uses: ./" not in line:
                yield path.name, line.strip()


def test_every_action_is_pinned():
    lines = list(uses_lines())
    assert lines
    for path, line in lines:
        assert PINNED.search(line), f"{path}: {line}"


def test_every_job_declares_permissions():
    for path, name, job in all_jobs():
        assert "permissions" in job, f"{path}: {name}"


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_workflows_default_to_no_permissions(path):
    assert yaml.safe_load(path.read_text())["permissions"] == {}


def test_gate_has_no_write_permission():
    for job in load("gate.yaml")["jobs"].values():
        assert set(job["permissions"].values()) == {"read"}


def test_gate_holds_no_secret():
    assert "secrets." not in (WORKFLOWS / "gate.yaml").read_text()


def test_gate_builds_for_one_platform_with_attestations():
    build = next(
        s
        for s in steps_of(load("gate.yaml")["jobs"]["gate"])
        if "docker/build-push-action" in s.get("uses", "")
    )["with"]
    assert build["platforms"] == "linux/amd64"
    assert build["push"] is False
    assert build["no-cache"] is True
    assert build["pull"] is True
    assert build["outputs"] == "type=oci,dest=image.tar"
    assert "mode=max" in build["attests"]
    assert "type=sbom" in build["attests"]


def test_gate_output_is_the_tested_digest():
    gate = load("gate.yaml")
    outputs = gate[True]["workflow_call"]["outputs"]
    assert outputs["digest"]["value"] == "${{ jobs.gate.outputs.digest }}"


def test_force_fail_reaches_the_scan():
    scan = next(
        s
        for s in steps_of(load("gate.yaml")["jobs"]["gate"])
        if "scan-image.sh" in s.get("run", "")
    )
    assert scan["env"]["SCAN_FORCE_FAIL"] == "${{ vars.SCAN_FORCE_FAIL }}"


def test_gate_rehearses_only_on_localhost():
    runs = [s.get("run", "") for s in steps_of(load("gate.yaml")["jobs"]["gate"])]
    pushes = [r for r in runs if "release.py publish" in r]
    assert len(pushes) == 1
    assert "--repository localhost:5000/rehearsal" in pushes[0]
    assert "ghcr.io" not in (WORKFLOWS / "gate.yaml").read_text()


def permission_grants(permission):
    for path, name, job in all_jobs():
        if job["permissions"].get(permission) == "write":
            yield path, name


def test_packages_write_only_in_the_two_named_jobs():
    assert set(permission_grants("packages")) == {
        ("publish.yaml", "push"),
        ("release.yaml", "publish"),
    }


def test_contents_write_only_in_the_two_named_jobs():
    assert set(permission_grants("contents")) == {
        ("publish.yaml", "push"),
        ("release.yaml", "publish"),
    }


def test_push_needs_the_gate():
    assert load("publish.yaml")["jobs"]["push"]["needs"] == "gate"


def test_gate_is_called_at_the_tag():
    gate = load("publish.yaml")["jobs"]["gate"]
    assert gate["uses"] == "./.github/workflows/gate.yaml"
    assert gate["with"]["ref"] == "${{ inputs.tag }}"


def test_push_requires_switch_and_main():
    condition = load("publish.yaml")["jobs"]["push"]["if"]
    assert "vars.PUBLISH_ENABLED == 'true'" in condition
    assert "github.ref == 'refs/heads/main'" in condition
    assert "||" not in condition


def test_push_uses_the_digest_the_gate_tested():
    steps = load("publish.yaml")["jobs"]["push"]["steps"]
    step = next(s for s in steps if "release.py publish" in s.get("run", ""))
    assert step["env"]["DIGEST"] == "${{ needs.gate.outputs.digest }}"
    assert "--expected-digest" in step["run"]


def test_draft_is_published_only_after_the_push():
    runs = [s.get("run", "") for s in load("publish.yaml")["jobs"]["push"]["steps"]]
    push = next(i for i, r in enumerate(runs) if "release.py publish" in r)
    edit = next(i for i, r in enumerate(runs) if "--draft=false" in r)
    assert push < edit


def test_release_calls_publish_only_with_the_switch():
    job = load("release.yaml")["jobs"]["publish"]
    assert job["uses"] == "./.github/workflows/publish.yaml"
    assert job["needs"] == "release-please"
    assert "vars.PUBLISH_ENABLED == 'true'" in job["if"]
    assert "needs.release-please.outputs.created == 'true'" in job["if"]
    assert "||" not in job["if"]


def test_auto_merge_job_requires_the_switch():
    job = load("release.yaml")["jobs"]["auto-merge"]
    assert job["if"] == "vars.PUBLISH_ENABLED == 'true'"


def test_auto_merge_applies_the_guard_with_the_manifest():
    steps = load("release.yaml")["jobs"]["auto-merge"]["steps"]
    run = next(s for s in steps if "gh pr merge" in s.get("run", ""))["run"]
    assert "release.py check" in run
    assert "--manifest .release-please-manifest.json" in run
    assert run.index("release.py check") < run.index("gh pr merge")


def test_skip_github_release_follows_the_switch():
    steps = load("release.yaml")["jobs"]["release-please"]["steps"]
    action = next(s for s in steps if "release-please-action" in s.get("uses", ""))
    assert (
        action["with"]["skip-github-release"] == "${{ vars.PUBLISH_ENABLED != 'true' }}"
    )
    assert action["with"]["config-file"] == "release-please-config.json"
    assert action["with"]["manifest-file"] == ".release-please-manifest.json"


def test_release_runs_only_from_main():
    triggers = load("release.yaml")[True]
    assert triggers["push"] == {"branches": ["main"]}
    assert set(triggers) == {"push", "workflow_dispatch"}


def test_only_publish_names_ghcr():
    for path in workflow_paths():
        if path.name != "publish.yaml":
            assert "ghcr.io" not in path.read_text(), path.name


def test_no_workflow_sets_the_switch():
    for path in workflow_paths():
        text = path.read_text()
        assert "variable set" not in text, path.name
        assert "PUBLISH_ENABLED:" not in text, path.name
