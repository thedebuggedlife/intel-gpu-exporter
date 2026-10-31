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
