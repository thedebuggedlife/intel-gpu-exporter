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


@pytest.mark.parametrize(
    ("workflow", "job"),
    [
        ("publish.yaml", "push"),
        ("release.yaml", "publish"),
        ("release.yaml", "auto-merge"),
        ("rebuild.yaml", "update"),
    ],
)
def test_no_condition_overrides_a_failed_dependency(workflow, job):
    condition = load(workflow)["jobs"][job]["if"]
    assert not re.search(r"always\(\)|cancelled\(\)|failure\(\)", condition)


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


def test_release_please_ignores_a_dispatch_from_another_branch():
    job = load("release.yaml")["jobs"]["release-please"]
    assert job["if"] == "github.ref == 'refs/heads/main'"


def test_only_publish_names_ghcr():
    for path in workflow_paths():
        if path.name != "publish.yaml":
            assert "ghcr.io" not in path.read_text(), path.name


def test_no_workflow_sets_the_switch():
    for path in workflow_paths():
        text = path.read_text()
        assert "variable set" not in text, path.name
        assert "PUBLISH_ENABLED:" not in text, path.name


AUTO_MERGE = "dependabot-auto-merge.yaml"


def auto_merge_job():
    jobs = load(AUTO_MERGE)["jobs"]
    assert list(jobs) == ["auto-merge"]
    return jobs["auto-merge"]


def auto_merge_step(text):
    return next(s for s in auto_merge_job()["steps"] if text in s.get("run", ""))


def test_auto_merge_uses_pull_request_target_only():
    triggers = load(AUTO_MERGE)[True]
    assert list(triggers) == ["pull_request_target"]
    assert triggers["pull_request_target"]["types"] == [
        "opened",
        "reopened",
        "synchronize",
    ]


def test_auto_merge_is_limited_to_dependabot():
    condition = auto_merge_job()["if"]
    assert "github.event.pull_request.user.login == 'dependabot[bot]'" in condition
    assert (
        "github.event.pull_request.head.repo.full_name == github.repository"
        in condition
    )
    assert "||" not in condition


def test_auto_merge_never_checks_out_the_pr():
    checkouts = [
        s for s in auto_merge_job()["steps"] if "actions/checkout" in s.get("uses", "")
    ]
    assert len(checkouts) == 1
    assert "ref" not in checkouts[0]["with"]
    assert "github.event.pull_request.head" not in str(checkouts)


def test_auto_merge_interpolates_nothing_from_the_pr_into_a_script():
    for step in auto_merge_job()["steps"]:
        assert "${{" not in step.get("run", ""), step["name"]


def test_auto_merge_sets_a_fixed_body():
    step = auto_merge_step("gh pr merge")
    assert '--body "Automated dependency update."' in step["run"]
    assert '--subject "$PR_TITLE"' in step["run"]
    assert step["env"]["PR_TITLE"] == "${{ github.event.pull_request.title }}"


def test_auto_merge_holds_a_pr_through_the_script():
    run = auto_merge_step("gh pr merge")["run"]
    assert 'scripts/hold-for-review.sh "$PR_NUMBER"' in run


def test_release_holds_a_pr_through_the_script():
    steps = load("release.yaml")["jobs"]["auto-merge"]["steps"]
    run = next(s for s in steps if "gh pr merge" in s.get("run", ""))["run"]
    assert 'scripts/hold-for-review.sh "$number"' in run


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_no_workflow_requests_review_without_disarming(path):
    assert "requested_reviewers" not in path.read_text()


def test_auto_merge_token_can_merge_action_updates():
    step = next(
        s
        for s in auto_merge_job()["steps"]
        if "create-github-app-token" in s.get("uses", "")
    )
    permissions = {k: v for k, v in step["with"].items() if k.startswith("permission-")}
    assert permissions == {
        "permission-contents": "write",
        "permission-pull-requests": "write",
        "permission-vulnerability-alerts": "read",
        "permission-workflows": "write",
    }


def test_fetch_metadata_looks_up_alerts():
    step = next(
        s
        for s in auto_merge_job()["steps"]
        if "dependabot/fetch-metadata" in s.get("uses", "")
    )
    assert step["with"]["alert-lookup"] is True
    assert step["with"]["github-token"] == "${{ steps.token.outputs.token }}"
    assert "skip-verification" not in step["with"]
    assert "skip-commit-verification" not in step["with"]


def test_auto_merge_checks_its_credentials_first():
    names = [s["name"] for s in auto_merge_job()["steps"]]
    assert names.index("Check the app credentials") < names.index("Create app token")


def test_release_pr_lookup_ignores_a_fork_using_the_same_branch_name():
    steps = load("release.yaml")["jobs"]["auto-merge"]["steps"]
    run = next(s for s in steps if "gh pr merge" in s.get("run", ""))["run"]
    assert (
        "repos/$GITHUB_REPOSITORY/pulls?head=${GITHUB_REPOSITORY_OWNER}:"
        "release-please--branches--main&state=open" in run
    )
    assert "gh pr list" not in run


def gate_steps():
    return steps_of(load("gate.yaml")["jobs"]["gate"])


def gate_step_index(text):
    return next(i for i, s in enumerate(gate_steps()) if text in str(s))


def artifact_upload(name):
    return next(
        s
        for s in gate_steps()
        if "upload-artifact" in s.get("uses", "") and s["with"]["name"] == name
    )


def test_gate_records_the_image_after_testing_it():
    assert gate_step_index("test-image.sh") < gate_step_index("image_lock.py")


def test_gate_records_the_tested_image():
    run = gate_steps()[gate_step_index("image_lock.py")]["run"]
    assert "scripts/image_lock.py intel-gpu-exporter:gate" in run


def test_gate_uploads_the_lock_and_fails_without_one():
    upload = artifact_upload("image-lock")
    assert upload["with"]["path"].endswith("/image.lock")
    assert upload["with"]["if-no-files-found"] == "error"


@pytest.mark.parametrize("name", ["image-lock", "sarif"])
def test_gate_artifacts_uploaded_before_it_can_fail_allow_a_re_run(name):
    assert artifact_upload(name)["with"]["overwrite"] is True


def test_gate_keeps_scan_results_when_the_scan_fails():
    upload = artifact_upload("sarif")
    assert upload["if"] == "${{ !cancelled() }}"
    assert gate_step_index("scan-image.sh") < gate_steps().index(upload)


def test_gate_scan_writes_the_uploaded_sarif_file():
    scan = gate_steps()[gate_step_index("scan-image.sh")]
    assert artifact_upload("sarif")["with"]["path"] in scan["run"]


def publish_steps():
    return load("publish.yaml")["jobs"]["push"]["steps"]


def test_published_release_carries_the_lock_the_gate_recorded():
    download = next(
        s
        for s in publish_steps()
        if "download-artifact" in s.get("uses", "")
        and s["with"]["name"] == "image-lock"
    )
    upload = next(s for s in publish_steps() if "gh release upload" in s.get("run", ""))
    assert f"{download['with']['path']}/image.lock" in upload["run"]
    assert "--clobber" in upload["run"]


def test_lock_is_attached_while_the_release_is_still_only_a_draft():
    runs = [s.get("run", "") for s in publish_steps()]
    attach = next(i for i, r in enumerate(runs) if "gh release upload" in r)
    push = next(i for i, r in enumerate(runs) if "release.py publish" in r)
    edit = next(i for i, r in enumerate(runs) if "--draft=false" in r)
    assert attach < push < edit


REBUILD = "rebuild.yaml"


def rebuild_job(name):
    return load(REBUILD)["jobs"][name]


def test_rebuild_runs_only_on_its_schedule_or_by_hand():
    triggers = load(REBUILD)[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert len(triggers["schedule"]) == 1


def test_rebuild_runs_one_at_a_time_without_cancelling():
    assert load(REBUILD)["concurrency"] == {
        "group": "rebuild",
        "cancel-in-progress": False,
    }


@pytest.mark.parametrize(
    "text",
    [
        "publish.yaml",
        "release.yaml",
        "release.py",
        "skopeo",
        "gh release",
        "PUBLISH_ENABLED",
        "packages:",
        "gh pr merge",
    ],
)
def test_rebuild_has_no_way_to_publish(text):
    assert text not in (WORKFLOWS / REBUILD).read_text()


def test_rebuild_only_calls_the_read_only_gate():
    job = rebuild_job("gate")
    assert job["uses"] == "./.github/workflows/gate.yaml"
    assert job["permissions"] == {"contents": "read"}
    assert "secrets" not in job
    called = [j["uses"] for j in load(REBUILD)["jobs"].values() if "uses" in j]
    assert called == ["./.github/workflows/gate.yaml"]


def test_rebuild_update_needs_the_gate_and_main():
    job = rebuild_job("update")
    assert job["needs"] == "gate"
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["permissions"] == {"contents": "read"}


def rebuild_update_step(text):
    return next(s for s in rebuild_job("update")["steps"] if text in str(s))


def test_rebuild_compares_the_lock_the_gate_recorded():
    download = rebuild_update_step("download-artifact")
    assert download["with"]["name"] == "image-lock"
    run = rebuild_update_step("rebuild_pr.py")["run"]
    assert f"--lock {download['with']['path']}/image.lock" in run


def test_rebuild_pr_is_opened_with_the_app_token():
    step = rebuild_update_step("rebuild_pr.py")
    assert step["env"]["GH_TOKEN"] == "${{ steps.token.outputs.token }}"


def test_rebuild_token_holds_only_what_the_pr_needs():
    step = rebuild_update_step("create-github-app-token")
    permissions = {k: v for k, v in step["with"].items() if k.startswith("permission-")}
    assert permissions == {
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }


def test_rebuild_checks_out_no_credentials():
    for name in ("update", "sarif", "track-failure"):
        checkout = next(
            s
            for s in rebuild_job(name)["steps"]
            if "actions/checkout" in s.get("uses", "")
        )
        assert checkout["with"]["persist-credentials"] is False


def test_hold_is_reached_through_the_shared_script():
    script = (REPO / "scripts" / "rebuild_pr.py").read_text()
    assert "hold-for-review.sh" in script
    assert "requested_reviewers" not in script


TRACKED = {
    "rebuild.yaml": {"gate", "update"},
    "release.yaml": {"release-please", "auto-merge", "publish"},
}


@pytest.mark.parametrize("workflow", TRACKED)
def test_tracker_watches_every_job_that_can_fail(workflow):
    job = load(workflow)["jobs"]["track-failure"]
    assert set(job["needs"]) == TRACKED[workflow]
    assert set(load(workflow)["jobs"]) - set(job["needs"]) <= {"track-failure", "sarif"}


@pytest.mark.parametrize("workflow", TRACKED)
def test_tracker_fires_on_a_failure_or_a_timeout(workflow):
    assert load(workflow)["jobs"]["track-failure"]["if"] == (
        "always() && github.ref == 'refs/heads/main' && "
        "(contains(needs.*.result, 'failure') || "
        "contains(needs.*.result, 'cancelled'))"
    )


@pytest.mark.parametrize("workflow", TRACKED)
def test_tracker_uses_the_runs_own_token(workflow):
    steps = load(workflow)["jobs"]["track-failure"]["steps"]
    step = next(s for s in steps if "track-failure.sh" in s.get("run", ""))
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"


@pytest.mark.parametrize("workflow", TRACKED)
def test_tracker_stays_red_when_it_fails(workflow):
    assert "continue-on-error" not in load(workflow)["jobs"]["track-failure"]


def test_two_workflows_never_report_at_the_same_moment():
    rebuild, release = (
        load(workflow)["jobs"]["track-failure"]["concurrency"] for workflow in TRACKED
    )
    assert rebuild == release == {"group": "track-failure", "cancel-in-progress": False}


def test_issues_write_only_in_the_tracker_jobs():
    assert set(permission_grants("issues")) == {
        ("rebuild.yaml", "track-failure"),
        ("release.yaml", "track-failure"),
    }


def test_security_events_write_only_in_the_sarif_job():
    assert set(permission_grants("security-events")) == {("rebuild.yaml", "sarif")}


def test_only_the_rebuild_uploads_sarif():
    uploads = [p.name for p in workflow_paths() if "upload-sarif" in p.read_text()]
    assert uploads == [REBUILD]


def test_sarif_runs_even_when_the_gate_fails():
    job = rebuild_job("sarif")
    assert job["needs"] == "gate"
    assert job["if"].startswith("always()")


def test_sarif_cannot_trip_the_tracker():
    assert rebuild_job("sarif")["continue-on-error"] is True
    assert "sarif" not in rebuild_job("track-failure")["needs"]
    assert "sarif" not in str(rebuild_job("update"))
