import json
from datetime import date

import pytest

from scripts import vuln_gate
from scripts.vuln_gate import GateError, Ignore, blocking, load_ignores

TODAY = date(2026, 11, 1)
IMAGE_ID = "sha256:" + "a" * 64


def match(identifier="CVE-2026-1", severity="High", state="fixed", related=()):
    return {
        "vulnerability": {
            "id": identifier,
            "severity": severity,
            "fix": {"state": state, "versions": []},
        },
        "relatedVulnerabilities": [{"id": r} for r in related],
        "artifact": {"name": "openssl", "version": "3.0.0"},
    }


def report(*matches):
    return {"matches": list(matches), "source": {"target": {"imageID": IMAGE_ID}}}


def ignore(identifier="CVE-2026-1", expires=date(2026, 12, 31)):
    return Ignore(identifier, "reviewed", expires)


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.delenv("SCAN_FORCE_FAIL", raising=False)

    def invoke(body, ignore_text=""):
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(body))
        ignore_path = tmp_path / "ignore.toml"
        ignore_path.write_text(ignore_text)
        return vuln_gate.main(
            [
                "--report",
                str(report_path),
                "--ignore-file",
                str(ignore_path),
                "--image-id",
                IMAGE_ID,
            ]
        )

    return invoke


def load(tmp_path, text):
    path = tmp_path / "ignore.toml"
    path.write_text(text)
    return load_ignores(path)


@pytest.mark.parametrize("severity", ["High", "Critical"])
def test_fixable_high_and_critical_block(severity):
    assert blocking(report(match(severity=severity)), [], TODAY)


@pytest.mark.parametrize("state", ["not-fixed", "unknown", "wont-fix", ""])
def test_unfixable_high_passes(state):
    assert blocking(report(match(state=state)), [], TODAY) == []


@pytest.mark.parametrize("severity", ["Medium", "Low", "Negligible", "Unknown"])
def test_lower_severities_pass(severity):
    assert blocking(report(match(severity=severity)), [], TODAY) == []


@pytest.mark.parametrize("severity", ["high", "CRITICAL"])
def test_severity_is_case_insensitive(severity):
    assert blocking(report(match(severity=severity)), [], TODAY)


def test_finding_names_the_package():
    assert "openssl" in blocking(report(match()), [], TODAY)[0]


def test_live_entry_removes_the_finding():
    assert blocking(report(match()), [ignore()], TODAY) == []


def test_entry_for_another_id_changes_nothing():
    assert blocking(report(match()), [ignore("CVE-2026-2")], TODAY)


def test_entry_works_on_its_expiry_day():
    assert blocking(report(match()), [ignore(expires=TODAY)], TODAY) == []


def test_entry_stops_the_day_after():
    expired = ignore(expires=date(2026, 10, 31))
    assert blocking(report(match()), [expired], TODAY)


def test_entry_matches_a_related_id():
    finding = match("GHSA-xxxx", related=["CVE-2026-1"])
    assert blocking(report(finding), [ignore()], TODAY) == []


def test_valid_file_loads(tmp_path):
    entries = load(
        tmp_path,
        '[[ignore]]\nid = "CVE-1"\nreason = "reviewed"\nexpires = 2027-01-31\n',
    )
    assert entries == [Ignore("CVE-1", "reviewed", date(2027, 1, 31))]


@pytest.mark.parametrize(
    "entry",
    [
        'id = "CVE-1"\nexpires = 2027-01-31\n',
        'id = "CVE-1"\nreason = ""\nexpires = 2027-01-31\n',
        'id = "CVE-1"\nreason = "   "\nexpires = 2027-01-31\n',
        'reason = "r"\nexpires = 2027-01-31\n',
        'id = "CVE-1"\nreason = "r"\n',
        'id = "CVE-1"\nreason = "r"\nexpires = "2027-01-31"\n',
        'id = "CVE-1"\nreason = "r"\nexpires = 2027-01-31T00:00:00\n',
        'id = "CVE-1"\nreason = "r"\nexpires = 2027-01-31\npackage = "x"\n',
        'id = 5\nreason = "r"\nexpires = 2027-01-31\n',
    ],
)
def test_malformed_entries_fail(tmp_path, entry):
    with pytest.raises(GateError):
        load(tmp_path, "[[ignore]]\n" + entry)


def test_unparseable_toml_fails(tmp_path):
    with pytest.raises(GateError):
        load(tmp_path, "[[ignore")


def test_unknown_top_level_key_fails(tmp_path):
    with pytest.raises(GateError):
        load(tmp_path, 'ignored = "x"\n')


def test_empty_ignore_file_passes(tmp_path):
    assert load(tmp_path, "# nothing reviewed yet\n") == []


def test_clean_report_passes(run):
    assert run(report()) == 0


def test_finding_fails_the_gate(run):
    assert run(report(match())) == 1


def test_malformed_ignore_file_fails_the_gate(run):
    assert run(report(), "[[ignore]]\n") == 1


def test_expired_entry_is_named(run, capsys):
    text = '[[ignore]]\nid = "CVE-1"\nreason = "r"\nexpires = 2020-01-01\n'
    assert run(report(), text) == 0
    assert "CVE-1" in capsys.readouterr().out


def test_image_id_mismatch_fails(run):
    body = report()
    body["source"]["target"]["imageID"] = "sha256:" + "b" * 64
    assert run(body) == 1


def test_report_without_matches_key_fails(run):
    body = report()
    del body["matches"]
    assert run(body) == 1


def test_force_fail_fails_a_clean_report(run, monkeypatch):
    monkeypatch.setenv("SCAN_FORCE_FAIL", "true")
    assert run(report()) == 1


@pytest.mark.parametrize("value", ["", "false", "1", "yes"])
def test_force_fail_other_values_do_nothing(run, monkeypatch, value):
    monkeypatch.setenv("SCAN_FORCE_FAIL", value)
    assert run(report()) == 0
