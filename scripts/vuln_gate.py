#!/usr/bin/env python3
"""Fails on a fixable critical or high grype finding that no live ignore entry covers."""

import argparse
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

BLOCKING_SEVERITIES = {"critical", "high"}
ENTRY_KEYS = {"id", "reason", "expires"}


class GateError(Exception):
    pass


@dataclass(frozen=True)
class Ignore:
    id: str
    reason: str
    expires: date


def parse_entry(entry: dict) -> Ignore:
    if set(entry) != ENTRY_KEYS:
        raise GateError(f"ignore entry must have exactly {sorted(ENTRY_KEYS)}: {entry}")
    identifier, reason, expires = entry["id"], entry["reason"], entry["expires"]
    if not isinstance(identifier, str) or not identifier.strip():
        raise GateError(f"ignore entry needs an id: {entry}")
    if not isinstance(reason, str) or not reason.strip():
        raise GateError(f"ignore entry needs a reason: {entry}")
    if not isinstance(expires, date) or isinstance(expires, datetime):
        raise GateError(f"ignore entry needs expires as a TOML date: {entry}")
    return Ignore(identifier, reason, expires)


def load_ignores(path: Path) -> list[Ignore]:
    try:
        document = tomllib.loads(Path(path).read_text())
    except tomllib.TOMLDecodeError as error:
        raise GateError(f"{path}: {error}") from error
    if set(document) - {"ignore"}:
        raise GateError(f"{path}: only [[ignore]] entries are allowed")
    return [parse_entry(entry) for entry in document.get("ignore", [])]


def match_ids(match: dict) -> set[str]:
    related = match.get("relatedVulnerabilities", [])
    return {match["vulnerability"]["id"], *(r["id"] for r in related)}


def blocking(report: dict, ignores: list[Ignore], today: date) -> list[str]:
    live = {entry.id for entry in ignores if entry.expires >= today}
    findings = []
    for match in report["matches"]:
        vulnerability = match["vulnerability"]
        if vulnerability["severity"].lower() not in BLOCKING_SEVERITIES:
            continue
        if vulnerability["fix"]["state"] != "fixed":
            continue
        if match_ids(match) & live:
            continue
        artifact = match["artifact"]
        findings.append(
            f"{vulnerability['id']} {vulnerability['severity']}: "
            f"{artifact['name']} {artifact['version']}"
        )
    return findings


def run_gate(report_path: str, ignore_path: str, image_id: str) -> None:
    if os.environ.get("SCAN_FORCE_FAIL") == "true":
        raise GateError("SCAN_FORCE_FAIL is set")
    ignores = load_ignores(Path(ignore_path))
    report = json.loads(Path(report_path).read_text())
    try:
        scanned = report["source"]["target"]["imageID"]
        if scanned != image_id:
            raise GateError(f"grype scanned {scanned}, not {image_id}")
        today = datetime.now(UTC).date()
        for entry in ignores:
            if entry.expires < today:
                print(f"expired ignore entry: {entry.id} ({entry.expires})")
        findings = blocking(report, ignores, today)
    except (KeyError, TypeError) as error:
        raise GateError(f"unexpected grype report: {error!r}") from error
    if findings:
        raise GateError("blocking findings:\n" + "\n".join(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--ignore-file", required=True)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args(argv)
    try:
        run_gate(args.report, args.ignore_file, args.image_id)
    except GateError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
