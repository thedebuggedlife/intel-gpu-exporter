#!/usr/bin/env python3
"""Records what an image contains, and decides what a change in that record means."""

# test-image.sh runs this on the host's python3, which can be older than the image's
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tarfile
import tempfile
from email.parser import HeaderParser
from pathlib import Path

LOCK_NAME = "image.lock"
CHORE_TITLE = "chore(deps): record image contents"
FIX_TITLE = "fix(deps): update image packages"

APK_DATABASE = "lib/apk/db/installed"
PYTHON_METADATA = re.compile(
    r"opt/venv/lib/python[^/]+/site-packages/[^/]+\.dist-info/METADATA"
)
TOOL_BINARY = "usr/bin/intel_gpu_top"
TOOL_VERSION = "usr/bin/intel_gpu_top.version"
TOOL_PACKAGE = re.compile(r"igt-gpu-tools-([0-9]\S*-r[0-9]+)")


class LockError(Exception):
    pass


def apk_lines(database: str) -> list[str]:
    lines = []
    for entry in database.split("\n\n"):
        if not entry.strip():
            continue
        name = re.search(r"^P:(.+)$", entry, re.MULTILINE)
        version = re.search(r"^V:(.+)$", entry, re.MULTILINE)
        if name is None or version is None:
            raise LockError(f"{APK_DATABASE} has an entry with no name or version")
        lines.append(f"apk {name[1]} {version[1]}")
    return lines


def python_line(metadata: str) -> str:
    headers = HeaderParser().parsestr(metadata)
    if headers["Name"] is None or headers["Version"] is None:
        raise LockError("a Python package has no name or version")
    name = re.sub(r"[-_.]+", "-", headers["Name"]).lower()
    return f"python {name} {headers['Version']}"


def tool_lines(version_file: str, binary: bytes) -> list[str]:
    package = TOOL_PACKAGE.fullmatch(version_file.strip())
    if package is None:
        raise LockError(f"{TOOL_VERSION} reads {version_file!r}")
    return [
        f"tool igt-gpu-tools {package[1]}",
        f"tool intel_gpu_top sha256:{hashlib.sha256(binary).hexdigest()}",
    ]


def lock_from_archive(archive: Path) -> str:
    files = {}
    python = []
    with tarfile.open(archive) as tar:
        for member in tar:
            if not member.isreg():
                continue
            if PYTHON_METADATA.fullmatch(member.name):
                python.append(python_line(tar.extractfile(member).read().decode()))
            elif member.name in (APK_DATABASE, TOOL_BINARY, TOOL_VERSION):
                files[member.name] = tar.extractfile(member).read()
    for required in (APK_DATABASE, TOOL_BINARY, TOOL_VERSION):
        if required not in files:
            raise LockError(f"the image has no {required}")
    apk = apk_lines(files[APK_DATABASE].decode())
    if not apk:
        raise LockError(f"{APK_DATABASE} is empty")
    if not python:
        raise LockError("the image has no Python packages")
    tool = tool_lines(files[TOOL_VERSION].decode(), files[TOOL_BINARY])
    return "".join(f"{line}\n" for line in sorted([*apk, *python, *tool]))


def docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise LockError(f"docker {args[0]}: {result.stderr.strip()}")
    return result.stdout


def generate(image: str) -> str:
    with tempfile.TemporaryDirectory() as work:
        archive = Path(work) / "image.tar"
        container = docker("create", image).strip()
        try:
            docker("export", "--output", str(archive), container)
            return lock_from_archive(archive)
        finally:
            docker("rm", container)


def decide(new: str, committed: str, published: str | None) -> str | None:
    if new == committed:
        return None
    if new == published:
        return CHORE_TITLE
    return FIX_TITLE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(generate(args.image))
    except LockError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
