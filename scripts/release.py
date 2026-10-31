#!/usr/bin/env python3
"""Decides which image tags a release gets, and pushes one tested archive to them."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

NUMBER = "(0|[1-9][0-9]*)"
VERSION = re.compile(rf"v?{NUMBER}\.{NUMBER}\.{NUMBER}(?:-rc\.([1-9][0-9]*))?")
NOT_FOUND = ("manifest unknown", "name unknown")


class ReleaseError(Exception):
    pass


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    rc: int | None = None

    def __str__(self):
        core = f"{self.major}.{self.minor}.{self.patch}"
        return core if self.rc is None else f"{core}-rc.{self.rc}"

    def __lt__(self, other):
        return self._order() < other._order()

    def __le__(self, other):
        return self._order() <= other._order()

    def _order(self):
        return (self.major, self.minor, self.patch, self.rc is None, self.rc or 0)


def parse_version(text: str) -> Version:
    match = VERSION.fullmatch(text)
    if match is None:
        raise ReleaseError(f"not a release version: {text!r}")
    major, minor, patch, rc = match.groups()
    return Version(int(major), int(minor), int(patch), None if rc is None else int(rc))


def tags_for(version: Version, existing: Iterable[Version]) -> list[str]:
    tags = [str(version)]
    if version.rc is not None:
        return tags
    stable = [v for v in existing if v.rc is None]
    if all(
        v <= version
        for v in stable
        if (v.major, v.minor) == (version.major, version.minor)
    ):
        tags.append(f"{version.major}.{version.minor}")
    if all(v <= version for v in stable):
        tags.append("latest")
    return tags


def check(version_text: str, manifest_version: str | None = None) -> None:
    version = parse_version(version_text)
    if manifest_version is None:
        return
    current = parse_version(manifest_version)
    allowed = 1 if current == Version(0, 0, 0) else current.major
    if version.major > allowed:
        raise ReleaseError(
            f"{version} raises the major version from {current}, which needs a person"
        )


def skopeo(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["skopeo", *args], capture_output=True, check=False)


def digest_of(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def tls_flag(repository: str, name: str) -> list[str]:
    host = repository.split("/", 1)[0].split(":", 1)[0]
    return [f"{name}=false"] if host == "localhost" else []


def archive_digest(archive: str) -> str:
    result = skopeo("inspect", "--raw", f"oci-archive:{archive}")
    if result.returncode != 0:
        raise ReleaseError(f"can't read {archive}: {result.stderr.decode().strip()}")
    return digest_of(result.stdout)


def registry_digest(repository: str, tag: str) -> str | None:
    result = skopeo(
        "inspect",
        "--raw",
        *tls_flag(repository, "--tls-verify"),
        f"docker://{repository}:{tag}",
    )
    if result.returncode == 0:
        return digest_of(result.stdout)
    error = result.stderr.decode().strip()
    if any(text in error.lower() for text in NOT_FOUND):
        return None
    raise ReleaseError(f"can't read {repository}:{tag}: {error}")


def copy_tag(archive: str, repository: str, tag: str, expected_digest: str) -> None:
    target = f"docker://{repository}:{tag}"
    tls = tls_flag(repository, "--dest-tls-verify")
    result = skopeo(
        "copy", "--all", "--preserve-digests", *tls, f"oci-archive:{archive}", target
    )
    if result.returncode != 0:
        raise ReleaseError(
            f"can't push {repository}:{tag}: {result.stderr.decode().strip()}"
        )
    pushed = registry_digest(repository, tag)
    if pushed != expected_digest:
        raise ReleaseError(
            f"{repository}:{tag} holds {pushed} after the push, not {expected_digest}"
        )


def publish(
    archive: str,
    repository: str,
    tag: str,
    expected_digest: str,
    existing: Iterable[Version],
) -> None:
    tags = tags_for(parse_version(tag), existing)
    actual = archive_digest(archive)
    if actual != expected_digest:
        raise ReleaseError(f"{archive} is {actual}, not the tested {expected_digest}")
    exact, *aliases = tags
    present = registry_digest(repository, exact)
    if present is not None and present != expected_digest:
        raise ReleaseError(f"{repository}:{exact} already holds {present}")
    if present is None:
        copy_tag(archive, repository, exact, expected_digest)
    for alias in aliases:
        copy_tag(archive, repository, alias, expected_digest)


def list_git_tags() -> list[str]:
    result = subprocess.run(
        ["git", "tag", "--list", "v*"], capture_output=True, text=True, check=True
    )
    return result.stdout.split()


def existing_versions() -> list[Version]:
    versions = []
    for tag in list_git_tags():
        try:
            versions.append(parse_version(tag))
        except ReleaseError:
            continue
    return versions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    check_parser = commands.add_parser("check")
    check_parser.add_argument("version")
    check_parser.add_argument(
        "--manifest", help="also apply the auto-merge rule against this manifest"
    )

    publish_parser = commands.add_parser("publish")
    publish_parser.add_argument("--archive", required=True)
    publish_parser.add_argument("--repository", required=True)
    publish_parser.add_argument("--tag", required=True)
    publish_parser.add_argument("--expected-digest", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            manifest = (
                args.manifest and json.loads(Path(args.manifest).read_text())["."]
            )
            check(args.version, manifest)
        else:
            publish(
                args.archive,
                args.repository,
                args.tag,
                args.expected_digest,
                existing_versions(),
            )
    except ReleaseError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
