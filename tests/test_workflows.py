import json
import re

import pytest
import yaml
from helpers import REPO

from scripts.image_lock import CHORE_TITLE, FIX_TITLE

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


def gate_job(name):
    return load("gate.yaml")["jobs"][name]


def test_gate_builds_for_one_platform_with_attestations():
    build = next(
        s
        for s in steps_of(gate_job("gate-build"))
        if "docker/build-push-action" in s.get("uses", "")
    )["with"]
    assert build["platforms"] == "linux/amd64"
    assert build["push"] is False
    assert build["no-cache"] is True
    assert build["pull"] is True
    assert build["outputs"] == "type=oci,dest=image.tar"
    assert "mode=max" in build["attests"]
    assert "type=sbom" in build["attests"]


def test_gate_output_is_the_digest_read_in_the_build_job():
    gate = load("gate.yaml")
    outputs = gate[True]["workflow_call"]["outputs"]
    assert outputs["digest"]["value"] == "${{ jobs.gate-build.outputs.digest }}"
    assert (
        gate_job("gate-build")["outputs"]["digest"]
        == "${{ steps.identity.outputs.digest }}"
    )


def test_the_digest_is_read_straight_after_the_build_and_before_the_upload():
    names = [s["name"] for s in steps_of(gate_job("gate-build"))]
    assert names[-3:] == [
        "Build image",
        "Read the image's identity",
        "Upload the image",
    ]


def test_the_build_job_installs_nothing_and_restores_no_cache():
    job = gate_job("gate-build")
    assert "needs" not in job
    text = yaml.safe_dump(job)
    for word in ("pip", "setup-python", "npm", "uv ", "cache@", "./.github/actions"):
        assert word not in text, word
    for step in steps_of(job):
        assert "cache-from" not in step.get("with", {})


def test_the_build_job_is_the_only_one_that_uploads_the_image():
    uploads = [
        name
        for name, job in load("gate.yaml")["jobs"].items()
        for step in steps_of(job)
        if "upload-artifact" in step.get("uses", "") and step["with"]["name"] == "image"
    ]
    assert uploads == ["gate-build"]


def test_lint_and_test_runs_in_its_own_job():
    uses = [
        name
        for name, job in load("gate.yaml")["jobs"].items()
        if "./.github/actions/lint-and-test" in str(job)
    ]
    assert uses == ["gate-tests"]


def test_nothing_on_the_release_path_restores_a_cache():
    paths = [
        *WORKFLOWS.glob("gate.yaml"),
        *WORKFLOWS.glob("publish.yaml"),
        *ACTIONS.rglob("action.yml"),
    ]
    for path in paths:
        assert not re.search(
            r"^\s*cache[:-]|actions/cache", path.read_text(), re.MULTILINE
        )


def test_verify_checks_out_the_commit_that_was_built():
    checkout = steps_of(gate_job("gate-verify"))[0]
    assert checkout["with"]["ref"] == "${{ needs.gate-build.outputs.revision }}"
    assert gate_job("gate-verify")["needs"] == "gate-build"


def test_verify_compares_the_download_with_the_build_jobs_digest():
    job = gate_job("gate-verify")
    assert job["env"]["DIGEST"] == "${{ needs.gate-build.outputs.digest }}"
    assert job["env"]["CONFIG_DIGEST"] == "${{ needs.gate-build.outputs.config }}"
    run = gate_steps()[gate_step_index("docker load")]["run"]
    assert '"$index" != "$DIGEST"' in run
    assert '"$loaded" != "$CONFIG_DIGEST"' in run
    assert run.index('"$index" != "$DIGEST"') < run.index("skopeo copy")


def test_no_gate_job_shares_a_required_checks_name():
    names = {job["name"] for job in load("gate.yaml")["jobs"].values()}
    assert not names & {"lint-and-test", "image-test"}


def test_required_checks_keep_their_names():
    jobs = load("ci.yaml")["jobs"]
    assert {job["name"] for job in jobs.values()} == {"lint-and-test", "image-test"}


def test_force_fail_reaches_the_scan():
    scan = next(s for s in gate_steps() if "scan-image.sh" in s.get("run", ""))
    assert scan["env"]["SCAN_FORCE_FAIL"] == "${{ vars.SCAN_FORCE_FAIL }}"


def test_gate_rehearses_only_on_localhost():
    runs = [s.get("run", "") for s in gate_steps()]
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
        ("release.yaml", "decide"),
        ("release.yaml", "auto-merge"),
        ("rebuild.yaml", "plan"),
        ("rebuild.yaml", "update"),
        ("dependabot-merge.yaml", "act"),
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


def test_auto_merge_jobs_require_the_switch():
    jobs = load("release.yaml")["jobs"]
    assert jobs["decide"]["if"] == "vars.PUBLISH_ENABLED == 'true'"
    assert jobs["auto-merge"]["if"].startswith("vars.PUBLISH_ENABLED == 'true' && ")
    assert "||" not in jobs["auto-merge"]["if"]


def release_decide_run():
    steps = load("release.yaml")["jobs"]["decide"]["steps"]
    return next(s for s in steps if "release.py check" in s.get("run", ""))["run"]


def test_auto_merge_applies_the_guard_with_the_manifest():
    run = release_decide_run()
    assert "--manifest .release-please-manifest.json" in run
    assert "gh pr merge" not in run


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


DECIDE = "dependabot-decide.yaml"
MERGE = "dependabot-merge.yaml"


def decide_job(name):
    return load(DECIDE)["jobs"][name]


def test_only_the_decide_workflow_uses_pull_request_target():
    users = [p.name for p in workflow_paths() if "pull_request_target" in triggers(p)]
    assert users == [DECIDE]


def triggers(path):
    return yaml.safe_load(path.read_text())[True]


def test_decide_runs_on_the_same_pr_events_as_before():
    on = load(DECIDE)[True]
    assert list(on) == ["pull_request_target"]
    assert on["pull_request_target"]["types"] == ["opened", "reopened", "synchronize"]


def test_decide_is_limited_to_dependabot_prs_in_this_repository():
    condition = decide_job("metadata")["if"]
    assert "github.event.pull_request.user.login == 'dependabot[bot]'" in condition
    assert (
        "github.event.pull_request.head.repo.full_name == github.repository"
        in condition
    )
    assert "||" not in condition
    assert decide_job("decide")["needs"] == "metadata"


def test_pull_request_target_holds_no_key_or_write_token():
    text = (WORKFLOWS / DECIDE).read_text()
    assert "secrets." not in text
    assert "environment:" not in text
    for job in load(DECIDE)["jobs"].values():
        assert set(job["permissions"].values()) == {"read"}


def test_decide_never_checks_out_the_pr():
    checkouts = [
        s
        for job in load(DECIDE)["jobs"].values()
        for s in steps_of(job)
        if "actions/checkout" in s.get("uses", "")
    ]
    assert len(checkouts) == 1
    assert checkouts[0]["with"] == {"persist-credentials": False}


def test_decide_interpolates_nothing_from_the_pr_into_a_script():
    for job in load(DECIDE)["jobs"].values():
        for step in steps_of(job):
            assert "${{" not in step.get("run", ""), step["name"]


def test_fetch_metadata_runs_alone_with_only_read_permissions():
    job = decide_job("metadata")
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert len(job["steps"]) == 1
    assert "dependabot/fetch-metadata" in job["steps"][0]["uses"]


def test_fetch_metadata_keeps_its_verification_and_gets_no_alert_access():
    action = decide_job("metadata")["steps"][0]["with"]
    assert action == {"github-token": "${{ github.token }}"}


def test_only_the_decide_workflow_runs_fetch_metadata():
    users = [p.name for p in workflow_paths() if "fetch-metadata" in p.read_text()]
    assert users == [DECIDE]


def test_alerts_are_readable_only_where_the_policy_runs():
    grants = {
        (path, name)
        for path, name, job in all_jobs()
        if "vulnerability-alerts" in job["permissions"]
    }
    assert grants == {(DECIDE, "decide")}


def test_merge_only_trusts_a_run_of_main_s_decide_workflow():
    job = load(MERGE)["jobs"]["verify"]
    condition = job["if"]
    for clause in (
        "github.event.workflow_run.event == 'pull_request_target'",
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.path == '.github/workflows/dependabot-decide.yaml'",
    ):
        assert clause in condition
    assert "||" not in condition


def test_merge_follows_the_decide_workflow():
    assert load(MERGE)[True]["workflow_run"] == {
        "workflows": ["dependabot-decide"],
        "types": ["completed"],
    }


def test_merge_verifies_with_read_only_permissions_and_no_checkout():
    job = load(MERGE)["jobs"]["verify"]
    assert job["permissions"] == {"actions": "read", "pull-requests": "read"}
    assert not any("uses" in step for step in job["steps"])


@pytest.mark.parametrize(
    ("workflow", "job", "name"),
    [
        (MERGE, "act", "Merge the update"),
        ("release.yaml", "auto-merge", "Merge the release PR"),
    ],
)
def test_a_merge_is_bound_to_the_commit_that_was_decided(workflow, job, name):
    step = next(s for s in load(workflow)["jobs"][job]["steps"] if s["name"] == name)
    assert '--match-head-commit "$HEAD_SHA"' in step["run"]
    assert "--auto --squash" in step["run"]


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_no_workflow_requests_review_without_disarming(path):
    text = path.read_text()
    if "requested_reviewers" in text:
        assert "--disable-auto" in text
        assert text.index("--disable-auto") < text.index("requested_reviewers")


def test_release_pr_lookup_ignores_a_fork_using_the_same_branch_name():
    run = release_decide_run()
    assert (
        "repos/$GITHUB_REPOSITORY/pulls?head=${GITHUB_REPOSITORY_OWNER}:"
        "release-please--branches--main&state=open" in run
    )
    assert "gh pr list" not in run


def gate_steps():
    return steps_of(gate_job("gate-verify"))


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
    ],
)
def test_rebuild_has_no_way_to_publish(text):
    assert text not in (WORKFLOWS / REBUILD).read_text()


def test_rebuild_can_only_arm_or_disarm_its_pr():
    merges = re.findall(r"gh pr merge .*", (WORKFLOWS / REBUILD).read_text())
    assert merges
    for merge in merges:
        assert "--auto --squash" in merge or "--disable-auto" in merge


def test_rebuild_only_calls_the_read_only_gate():
    job = rebuild_job("gate")
    assert job["uses"] == "./.github/workflows/gate.yaml"
    assert job["permissions"] == {"contents": "read"}
    assert "secrets" not in job
    called = [j["uses"] for j in load(REBUILD)["jobs"].values() if "uses" in j]
    assert called == ["./.github/workflows/gate.yaml"]


def test_rebuild_plan_needs_the_gate_and_main():
    job = rebuild_job("plan")
    assert job["needs"] == "gate"
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}


def test_rebuild_update_needs_the_plan_and_main():
    job = rebuild_job("update")
    assert job["needs"] == "plan"
    assert job["if"] == (
        "github.ref == 'refs/heads/main' && needs.plan.outputs.operation != 'none'"
    )


def rebuild_plan_step(text):
    return next(s for s in rebuild_job("plan")["steps"] if text in str(s))


def test_rebuild_compares_the_lock_the_gate_recorded():
    download = rebuild_plan_step("download-artifact")
    assert download["with"]["name"] == "image-lock"
    run = rebuild_plan_step("rebuild_pr.py")["run"]
    assert f"--lock {download['with']['path']}/image.lock" in run


def test_rebuild_plans_with_the_runs_own_read_only_token():
    step = rebuild_plan_step("rebuild_pr.py")
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_rebuild_titles_match_the_constants():
    check = next(
        s for s in rebuild_job("update")["steps"] if s["name"] == "Check the plan"
    )
    assert f'"$TITLE" = "{CHORE_TITLE}"' in check["run"]
    assert f'"$TITLE" = "{FIX_TITLE}"' in check["run"]


def test_rebuild_applies_writes_only_with_the_app_token():
    for step in rebuild_job("update")["steps"]:
        if "gh " in step.get("run", ""):
            assert step["env"]["GH_TOKEN"] == "${{ steps.token.outputs.token }}"


def test_rebuild_pr_script_can_only_read():
    script = (REPO / "scripts" / "rebuild_pr.py").read_text()
    for word in ("--method", "--input", "--auto", "--disable-auto", '"pr", "close"'):
        assert word not in script


def test_rebuild_update_passes_on_the_plan_it_needs():
    outputs = set(rebuild_job("plan")["outputs"])
    text = yaml.safe_dump(rebuild_job("update"))
    assert outputs == set(re.findall(r"needs\.plan\.outputs\.([\w-]+)", text))


REPORT = "report.yaml"
WATCHED = {"dependabot-decide", "dependabot-merge", "rebuild", "release"}


def report_job(name):
    return load(REPORT)["jobs"][name]


def conclusion_is_reported(conclusion):
    match = re.search(
        r"!contains\(fromJSON\('(.+?)'\), (.+?)\)", report_job("track-failure")["if"]
    )
    assert match[2] == "github.event.workflow_run.conclusion"
    return conclusion not in json.loads(match[1])


def test_report_runs_once_a_watched_workflow_finishes():
    assert load(REPORT)[True] == {
        "workflow_run": {"workflows": sorted(WATCHED), "types": ["completed"]}
    }


def test_every_watched_name_is_a_real_workflow():
    names = {yaml.safe_load(p.read_text())["name"] for p in workflow_paths()}
    assert WATCHED <= names


def test_every_workflow_that_acts_or_publishes_is_watched():
    for path, name, job in all_jobs():
        if environment_of(job) or "publish.yaml" in str(job.get("uses", "")):
            assert yaml.safe_load((WORKFLOWS / path).read_text())["name"] in WATCHED


@pytest.mark.parametrize(
    "conclusion",
    [
        "failure",
        "cancelled",
        "timed_out",
        "startup_failure",
        "action_required",
        "stale",
    ],
)
def test_tracker_fires_on_anything_but_a_clean_finish(conclusion):
    assert conclusion_is_reported(conclusion)


@pytest.mark.parametrize("conclusion", ["success", "skipped", "neutral"])
def test_tracker_stays_quiet_on_a_clean_finish(conclusion):
    assert not conclusion_is_reported(conclusion)


def test_tracker_ignores_a_fork_and_a_dry_run_from_another_branch():
    condition = report_job("track-failure")["if"]
    assert "head_repository.full_name == github.repository" in condition
    assert "head_branch == 'main'" in condition


def tracker_step():
    return next(s for s in report_job("track-failure")["steps"] if "run" in s)


def test_tracker_reports_the_run_that_finished_not_its_own():
    env = tracker_step()["env"]
    assert env["FAILED_RUN_ID"] == "${{ github.event.workflow_run.id }}"
    assert env["FAILED_SHA"] == "${{ github.event.workflow_run.head_sha }}"
    assert env["FAILED_WORKFLOW"] == "${{ github.event.workflow_run.name }}"
    assert env["FAILED_CONCLUSION"] == "${{ github.event.workflow_run.conclusion }}"


def test_tracker_uses_the_runs_own_token():
    assert tracker_step()["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_tracker_stays_red_when_it_fails():
    assert "continue-on-error" not in report_job("track-failure")


def test_two_reports_never_run_at_the_same_moment():
    assert report_job("track-failure")["concurrency"] == {
        "group": "track-failure",
        "cancel-in-progress": False,
    }


def test_issues_write_only_in_the_tracker_job():
    assert set(permission_grants("issues")) == {(REPORT, "track-failure")}


def test_security_events_write_only_in_the_sarif_job():
    assert set(permission_grants("security-events")) == {(REPORT, "sarif")}


def test_only_the_report_uploads_sarif():
    uploads = [p.name for p in workflow_paths() if "upload-sarif" in p.read_text()]
    assert uploads == [REPORT]


def test_sarif_reads_the_rebuild_run_that_finished():
    job = report_job("sarif")
    assert "github.event.workflow_run.name == 'rebuild'" in job["if"]
    assert "head_branch == 'main'" in job["if"]
    download = next(s for s in job["steps"] if "download-artifact" in s.get("uses", ""))
    assert download["with"]["run-id"] == "${{ github.event.workflow_run.id }}"
    upload = next(s for s in job["steps"] if "upload-sarif" in s.get("uses", ""))
    assert upload["with"]["sha"] == "${{ github.event.workflow_run.head_sha }}"
    assert upload["with"]["ref"] == "refs/heads/main"


def test_sarif_runs_whatever_the_rebuild_concluded():
    assert "conclusion" not in report_job("sarif")["if"]


def test_sarif_cannot_trip_the_tracker():
    assert report_job("sarif")["continue-on-error"] is True
    assert "sarif" not in str(report_job("track-failure"))


KEY = "RELEASE_BOT_PRIVATE_KEY"
TOKEN_ACTION = "actions/create-github-app-token"
TOKEN_SCOPES = {
    ("dependabot-merge.yaml", "act"): {"contents", "pull-requests"},
    ("rebuild.yaml", "update"): {"contents", "pull-requests"},
    ("release.yaml", "auto-merge"): {"contents", "pull-requests"},
    ("release.yaml", "release-please"): {"contents", "issues", "pull-requests"},
}
KEY_JOBS = set(TOKEN_SCOPES)
THIRD_PARTY = {("release.yaml", "release-please"): "googleapis/release-please-action"}
DECIDERS = {
    ("dependabot-decide.yaml", "decide"),
    ("dependabot-decide.yaml", "metadata"),
    ("dependabot-merge.yaml", "verify"),
    ("rebuild.yaml", "plan"),
    ("release.yaml", "decide"),
}
FORBIDDEN_IN_ACT_JOBS = re.compile(
    r"\b(pip3?|npm|npx|yarn|python3?|node|docker|curl|wget|sudo|eval|source|uv)\b"
    r"|\bgit +[a-z]|scripts/|\.sh\b|\./|\$\{\{"
)


def environment_of(job):
    value = job.get("environment")
    return value.get("name") if isinstance(value, dict) else value


def key_jobs():
    return [
        (path, name, job) for path, name, job in all_jobs() if (path, name) in KEY_JOBS
    ]


def app_token_step(job):
    tokens = [s for s in job["steps"] if TOKEN_ACTION in s.get("uses", "")]
    assert len(tokens) == 1
    return tokens[0]


def test_only_the_allowlisted_jobs_use_the_environment():
    users = {(path, name) for path, name, job in all_jobs() if environment_of(job)}
    assert users == KEY_JOBS


def test_the_environment_is_the_release_bot_one():
    for path, name, job in key_jobs():
        assert environment_of(job) == "release-bot", (path, name)


def test_only_the_allowlisted_jobs_mention_the_key():
    users = {(p, n) for p, n, job in all_jobs() if KEY in yaml.safe_dump(job)}
    assert users == KEY_JOBS


def test_the_key_never_sits_outside_a_job():
    for path in workflow_paths():
        workflow = yaml.safe_load(path.read_text())
        workflow.pop("jobs")
        assert KEY not in yaml.safe_dump(workflow), path.name


def test_the_key_only_reaches_the_token_action():
    for path, name, job in key_jobs():
        assert KEY not in yaml.safe_dump(job.get("env", {})), (path, name)
        step = app_token_step(job)
        assert step["with"]["private-key"] == f"${{{{ secrets.{KEY} }}}}"
        for other in job["steps"]:
            if other is not step:
                assert KEY not in yaml.safe_dump(other), (path, name, other["name"])


def test_no_other_file_names_the_key():
    tree = [*REPO.joinpath(".github").rglob("*"), *REPO.joinpath("scripts").rglob("*")]
    allowed = {WORKFLOWS / path for path, _ in KEY_JOBS}
    for path in tree:
        if path.is_file() and path not in allowed and "__pycache__" not in path.parts:
            assert KEY not in path.read_text(), path


def test_no_workflow_reads_another_secret_or_inherits_them():
    for path in workflow_paths():
        text = path.read_text()
        assert set(re.findall(r"secrets\.(\w+)", text)) <= {KEY}, path.name
        assert "secrets: inherit" not in text, path.name


def test_a_called_workflow_never_receives_the_key():
    for path, name, job in all_jobs():
        if "uses" in job:
            assert "secrets" not in job, (path, name)
            assert "environment" not in job, (path, name)


def test_each_job_with_the_key_mints_one_token_with_only_what_it_needs():
    for path, name, job in key_jobs():
        arguments = app_token_step(job)["with"]
        scopes = {k: v for k, v in arguments.items() if k.startswith("permission-")}
        expected = {f"permission-{s}": "write" for s in TOKEN_SCOPES[(path, name)]}
        assert scopes == expected, (path, name)


def test_no_token_can_write_workflows():
    for path in workflow_paths():
        assert "permission-workflows" not in path.read_text(), path.name


def test_jobs_with_the_key_hold_no_github_token_permission():
    for path, name, job in key_jobs():
        assert job["permissions"] == {}, (path, name)


def test_jobs_with_the_key_run_only_the_token_action_and_their_one_exception():
    for path, name, job in key_jobs():
        allowed = {TOKEN_ACTION, THIRD_PARTY.get((path, name))}
        for step in job["steps"]:
            if "uses" in step:
                assert step["uses"].split("@")[0] in allowed, (path, name, step["uses"])


def test_jobs_with_the_key_run_no_tools_and_no_repository_code():
    for path, name, job in key_jobs():
        for step in job["steps"]:
            found = FORBIDDEN_IN_ACT_JOBS.search(step.get("run", ""))
            assert found is None, (path, name, step["name"], found and found[0])


def test_jobs_with_the_key_use_no_services_or_containers():
    for path, name, job in key_jobs():
        assert not {"services", "container"} & set(job), (path, name)


def test_only_release_please_hands_a_write_token_to_an_action():
    for path, name, job in key_jobs():
        for step in job["steps"]:
            if "uses" not in step or TOKEN_ACTION in step["uses"]:
                continue
            assert (path, name) in THIRD_PARTY
            assert step["uses"].split("@")[0] == THIRD_PARTY[(path, name)]


def checked_job(path, name):
    return (path, name) in KEY_JOBS - set(THIRD_PARTY)


def test_a_job_checks_what_it_received_before_the_key_is_used():
    for path, name, job in key_jobs():
        if not checked_job(path, name):
            continue
        names = [s["name"] for s in job["steps"]]
        token = names.index(app_token_step(job)["name"])
        assert token == 1, (path, name)
        assert names[0].startswith("Check the "), (path, name)
        check = job["steps"][0]["run"]
        for variable, value in job["env"].items():
            if "needs." in str(value):
                assert f"${variable}" in check or f'"${variable}"' in check, variable


def test_decisions_reach_a_job_with_the_key_through_env():
    for path, name, job in key_jobs():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), (path, name, step["name"])


def test_deciders_hold_no_key_and_write_nothing():
    jobs = {(p, n): job for p, n, job in all_jobs()}
    for key in DECIDERS:
        job = jobs[key]
        assert set(job["permissions"].values()) <= {"read"}, key
        assert environment_of(job) is None, key
        assert "secrets." not in yaml.safe_dump(job), key


def test_a_job_with_the_key_only_follows_a_decider():
    for path, name, job in key_jobs():
        if not checked_job(path, name):
            continue
        assert (path, job["needs"]) in DECIDERS, (path, name)


def test_only_release_please_runs_with_the_key_and_no_decider():
    undecided = {(p, n) for p, n, job in key_jobs() if "needs" not in job}
    assert undecided == set(THIRD_PARTY)


def test_no_file_with_the_key_reports_or_uploads():
    for path, _ in KEY_JOBS:
        text = (WORKFLOWS / path).read_text()
        for word in ("track-failure", "upload-sarif", "security-events"):
            assert word not in text, (path, word)
        assert not re.search(r"^\s+issues: ", text, re.MULTILINE), path


def test_nothing_with_the_key_can_fail_quietly():
    for path, name, job in key_jobs():
        assert "continue-on-error" not in job, (path, name)
        for step in job["steps"]:
            assert "continue-on-error" not in step, (path, name, step["name"])
