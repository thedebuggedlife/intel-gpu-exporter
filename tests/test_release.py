import hashlib
import json
import os
import stat

import pytest
from helpers import TESTS

from scripts import release
from scripts.release import ReleaseError, check, parse_version, publish, tags_for

REPOSITORY = "ghcr.io/thedebuggedlife/intel-gpu-exporter"
ARCHIVE_BODY = '{"manifests": ["tested"]}'
OTHER_BODY = '{"manifests": ["another build"]}'


def digest_of(body):
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


class FakeSkopeo:
    def __init__(self, path):
        self.path = path

    def write(self, **state):
        self.path.write_text(
            json.dumps({"archive": ARCHIVE_BODY, "registry": {}, "calls": []} | state)
        )

    @property
    def state(self):
        return json.loads(self.path.read_text())

    @property
    def copied_tags(self):
        return [c[-1].rsplit(":", 1)[1] for c in self.state["calls"] if c[0] == "copy"]


@pytest.fixture
def skopeo(tmp_path, monkeypatch):
    shim = tmp_path / "bin" / "skopeo"
    shim.parent.mkdir()
    shim.write_text((TESTS / "fake_skopeo.py").read_text())
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    state = tmp_path / "state.json"
    monkeypatch.setenv("FAKE_SKOPEO_STATE", str(state))
    fake = FakeSkopeo(state)
    fake.write()
    return fake


def run_publish(tag="v1.2.3", expected=None, existing=(), repository=REPOSITORY):
    publish("image.tar", repository, tag, expected or digest_of(ARCHIVE_BODY), existing)


@pytest.mark.parametrize(
    "text",
    [
        "1.2",
        "1.0.1-rc",
        "1.2.3-beta.1",
        "v1.2.3.4",
        "1.2.3 ",
        "01.2.3",
        "1.02.3",
        "1.2.3-rc.0",
        "",
        "latest",
    ],
)
def test_malformed_versions_are_refused(text):
    with pytest.raises(ReleaseError):
        parse_version(text)


def test_a_leading_v_is_accepted():
    assert parse_version("v1.2.3") == parse_version("1.2.3")


def test_rc_orders_below_its_release():
    assert parse_version("2.0.0-rc.1") < parse_version("2.0.0")


def test_rc_numbers_compare_numerically():
    assert parse_version("2.0.0-rc.9") < parse_version("2.0.0-rc.10")


@pytest.mark.parametrize(
    ("version", "existing", "tags"),
    [
        ("1.2.3", [], ["1.2.3", "1.2", "latest"]),
        ("1.2.3", ["1.2.2", "1.1.9", "0.9.0"], ["1.2.3", "1.2", "latest"]),
        ("1.2.3", ["1.3.0"], ["1.2.3", "1.2"]),
        ("1.2.3", ["1.2.4"], ["1.2.3"]),
        ("1.2.3", ["1.2.4", "1.3.0"], ["1.2.3"]),
        ("1.2.3", ["2.0.0-rc.1"], ["1.2.3", "1.2", "latest"]),
        ("1.2.3", ["1.2.3"], ["1.2.3", "1.2", "latest"]),
        ("2.0.0-rc.1", [], ["2.0.0-rc.1"]),
        ("2.0.0-rc.1", ["1.2.3"], ["2.0.0-rc.1"]),
    ],
)
def test_tags_for(version, existing, tags):
    assert (
        tags_for(parse_version(version), [parse_version(e) for e in existing]) == tags
    )


def test_check_accepts_a_plain_version():
    check("1.0.1")


def test_check_refuses_a_malformed_version():
    with pytest.raises(ReleaseError):
        check("1.0.1-rc")


def test_auto_merge_refuses_a_major_bump():
    with pytest.raises(ReleaseError, match="major"):
        check("2.0.0", manifest_version="1.4.2")


def test_auto_merge_allows_the_first_release():
    check("1.0.0", manifest_version="0.0.0")


def test_auto_merge_allows_a_minor_bump():
    check("1.5.0", manifest_version="1.4.2")


def test_push_doesnt_apply_the_major_rule():
    check("2.0.0")


def test_absent_tag_pushes_exact_then_aliases_then_latest(skopeo):
    run_publish()
    assert skopeo.copied_tags == ["1.2.3", "1.2", "latest"]


def test_every_tag_ends_up_holding_the_tested_archive(skopeo):
    run_publish()
    assert skopeo.state["registry"] == dict.fromkeys(
        ["1.2.3", "1.2", "latest"], ARCHIVE_BODY
    )


def test_copies_preserve_every_manifest_byte_for_byte(skopeo):
    run_publish()
    copy = next(c for c in skopeo.state["calls"] if c[0] == "copy")
    assert "--all" in copy
    assert "--preserve-digests" in copy
    assert copy[-2] == "oci-archive:image.tar"


def test_prerelease_pushes_only_its_exact_tag(skopeo):
    run_publish(tag="v2.0.0-rc.1")
    assert skopeo.copied_tags == ["2.0.0-rc.1"]


def test_existing_tag_with_other_digest_fails_before_any_copy(skopeo):
    skopeo.write(registry={"1.2.3": OTHER_BODY})
    with pytest.raises(ReleaseError, match="1.2.3"):
        run_publish()
    assert skopeo.copied_tags == []
    assert skopeo.state["registry"]["1.2.3"] == OTHER_BODY


def test_existing_tag_with_same_digest_continues_to_aliases(skopeo):
    skopeo.write(registry={"1.2.3": ARCHIVE_BODY})
    run_publish()
    assert skopeo.copied_tags == ["1.2", "latest"]


def test_auth_error_on_read_fails_before_any_copy(skopeo):
    skopeo.write(
        read_failure="reading manifest 1.2.3: unauthorized: authentication required\n"
    )
    with pytest.raises(ReleaseError, match="unauthorized"):
        run_publish()
    assert skopeo.copied_tags == []


def test_server_error_on_read_fails_before_any_copy(skopeo):
    skopeo.write(
        read_failure="reading manifest 1.2.3: received unexpected HTTP status: 503\n"
    )
    with pytest.raises(ReleaseError):
        run_publish()
    assert skopeo.copied_tags == []


def test_unknown_repository_reads_as_absent(skopeo):
    skopeo.write(read_failure="name unknown: repository name not known to registry\n")
    assert release.registry_digest(REPOSITORY, "1.2.3") is None


def test_denied_read_fails_rather_than_reading_as_absent(skopeo):
    skopeo.write(read_failure="reading manifest 1.2.3: denied: permission_denied\n")
    with pytest.raises(ReleaseError, match="denied"):
        run_publish()
    assert skopeo.copied_tags == []


def test_archive_digest_mismatch_copies_nothing(skopeo):
    with pytest.raises(ReleaseError, match="tested"):
        run_publish(expected=digest_of(OTHER_BODY))
    assert skopeo.copied_tags == []


def test_digest_mismatch_after_copy_fails(skopeo):
    skopeo.write(corrupt_copy=OTHER_BODY)
    with pytest.raises(ReleaseError, match="after"):
        run_publish()
    assert skopeo.copied_tags == ["1.2.3"]


def test_older_retry_leaves_latest_alone(skopeo):
    run_publish(existing=[parse_version("v1.3.0")])
    assert skopeo.copied_tags == ["1.2.3", "1.2"]


def test_malformed_tag_pushes_nothing(skopeo):
    with pytest.raises(ReleaseError):
        run_publish(tag="v1.2")
    assert skopeo.state["calls"] == []


def test_localhost_disables_tls_verify(skopeo):
    run_publish(repository="localhost:5000/rehearsal")
    calls = skopeo.state["calls"]
    assert any(c[0] == "copy" and "--dest-tls-verify=false" in c for c in calls)
    assert any(c[0] == "inspect" and "--tls-verify=false" in c for c in calls)


@pytest.mark.parametrize(
    "repository", [REPOSITORY, "localhost.example.com/x", "evil.io/localhost:5000/x"]
)
def test_other_registries_never_disable_tls_verify(skopeo, repository):
    run_publish(repository=repository)
    assert not any(
        a.endswith("tls-verify=false") for c in skopeo.state["calls"] for a in c
    )


def test_cli_check_applies_the_manifest(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{".": "1.4.2"}')
    assert release.main(["check", "2.0.0", "--manifest", str(manifest)]) == 1
    assert "major" in capsys.readouterr().err
    assert release.main(["check", "1.5.0", "--manifest", str(manifest)]) == 0


def test_cli_publish_failure_exits_non_zero(skopeo, capsys):
    argv = [
        "publish",
        "--archive",
        "image.tar",
        "--repository",
        REPOSITORY,
        "--tag",
        "v1.2.3",
    ]
    assert release.main([*argv, "--expected-digest", digest_of(OTHER_BODY)]) == 1
    assert skopeo.copied_tags == []


def test_git_tags_that_dont_parse_are_skipped(monkeypatch):
    monkeypatch.setattr(release, "list_git_tags", lambda: ["v1.0.0", "vnext", "v2.0"])
    assert release.existing_versions() == [parse_version("1.0.0")]
