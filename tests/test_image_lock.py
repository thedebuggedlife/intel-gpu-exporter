import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile

import pytest
from helpers import REPO, TESTS

from scripts.image_lock import (
    CHORE_TITLE,
    FIX_TITLE,
    LockError,
    decide,
    lock_from_archive,
)

SCRIPT = REPO / "scripts" / "image_lock.py"
DATABASE = "lib/apk/db/installed"
TOOL_BINARY = "usr/bin/intel_gpu_top"
TOOL_VERSION = "usr/bin/intel_gpu_top.version"
BINARY = b"\x7fELF a real binary"
DIST_INFO = "opt/venv/lib/python3.14/site-packages/prometheus_client-0.21.1.dist-info"
LOCK_LINE = re.compile(
    r"(apk|python) \S+ \S+|tool igt-gpu-tools \S+|tool intel_gpu_top sha256:[0-9a-f]{64}"
)

APK_DATABASE = """C:Q18Pz9wvTaBYr2RzvEXE6rYpFiJbI=
P:musl
V:1.2.6-r2
A:x86_64
t:1774381254
c:60a7585bbab2fa0f762504eb617dbca90216e31f
F:lib
R:ld-musl-x86_64.so.1
Z:Q1SLkS9hBidUbPwwrw+XR0Whv3ww8=

C:Q1bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
P:python3
V:3.14.3-r0
A:x86_64
t:1774381999
c:0000000000000000000000000000000000000000

"""


def contents():
    return {
        DATABASE: APK_DATABASE,
        f"{DIST_INFO}/METADATA": (
            "Metadata-Version: 2.4\nName: prometheus_client\nVersion: 0.21.1\n\n"
            "Name: not-a-header\n"
        ),
        f"{DIST_INFO}/RECORD": "ignored\n",
        TOOL_BINARY: BINARY,
        TOOL_VERSION: "igt-gpu-tools-2.3-r0\n",
        "etc/hostname": "unrelated\n",
    }


def archive(path, files, mtime=1000, reverse=False):
    with tarfile.open(path, "w") as tar:
        for name, content in sorted(files.items(), reverse=reverse):
            data = content.encode() if isinstance(content, str) else content
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mtime = mtime
            tar.addfile(member, io.BytesIO(data))
    return path


@pytest.fixture
def lock(tmp_path):
    def build(files=None, **options):
        return lock_from_archive(
            archive(tmp_path / "image.tar", files or contents(), **options)
        )

    return build


def test_lock_lists_every_kind_of_content(lock):
    binary_hash = hashlib.sha256(BINARY).hexdigest()
    assert lock() == (
        "apk musl 1.2.6-r2\n"
        "apk python3 3.14.3-r0\n"
        "python prometheus-client 0.21.1\n"
        "tool igt-gpu-tools 2.3-r0\n"
        f"tool intel_gpu_top sha256:{binary_hash}\n"
    )


def test_same_content_in_another_order_or_time_gives_identical_bytes(lock):
    assert lock() == lock(mtime=2000, reverse=True)


def test_database_order_does_not_change_the_lock(lock):
    first, second, _ = APK_DATABASE.split("\n\n")
    reordered = contents() | {DATABASE: f"{second}\n\n{first}\n\n"}
    assert lock(reordered) == lock()


def test_build_identifiers_in_the_database_do_not_change_the_lock(lock):
    rebuilt = re.sub(
        r"^([tcCZ]):.*$", r"\1:another-build", APK_DATABASE, flags=re.MULTILINE
    )
    assert rebuilt != APK_DATABASE
    assert lock(contents() | {DATABASE: rebuilt}) == lock()


def test_new_apk_version_changes_the_lock(lock):
    changed = APK_DATABASE.replace("3.14.3-r0", "3.14.3-r1")
    assert "apk python3 3.14.3-r1\n" in lock(contents() | {DATABASE: changed})


def test_new_tool_version_changes_the_lock(lock):
    changed = contents() | {TOOL_VERSION: "igt-gpu-tools-2.3-r1\n"}
    assert "tool igt-gpu-tools 2.3-r1\n" in lock(changed)


def test_rebuilt_tool_keeping_its_version_changes_the_lock(lock):
    rebuilt = contents() | {TOOL_BINARY: b"\x7fELF rebuilt, same version"}
    assert lock(rebuilt) != lock()
    assert "tool igt-gpu-tools 2.3-r0\n" in lock(rebuilt)


def test_python_names_are_normalised(lock):
    metadata = "Name: Some_Package.Name\nVersion: 1.0\n"
    files = contents() | {f"{DIST_INFO}/METADATA": metadata}
    assert "python some-package-name 1.0\n" in lock(files)


@pytest.mark.parametrize(
    "path",
    [DATABASE, f"{DIST_INFO}/METADATA", TOOL_BINARY, TOOL_VERSION],
)
def test_an_incomplete_image_is_refused(lock, path):
    files = contents()
    del files[path]
    with pytest.raises(LockError):
        lock(files)


@pytest.mark.parametrize(
    "version", ["", "\n", "igt-gpu-tools\n", "something-else-2.3-r0\n"]
)
def test_a_bad_tool_version_is_refused(lock, version):
    with pytest.raises(LockError):
        lock(contents() | {TOOL_VERSION: version})


def test_an_empty_apk_database_is_refused(lock):
    with pytest.raises(LockError):
        lock(contents() | {DATABASE: ""})


def test_an_apk_entry_without_a_version_is_refused(lock):
    with pytest.raises(LockError):
        lock(contents() | {DATABASE: "P:musl\nA:x86_64\n\n"})


def test_python_metadata_without_a_version_is_refused(lock):
    with pytest.raises(LockError):
        lock(contents() | {f"{DIST_INFO}/METADATA": "Name: x\n"})


def test_the_tool_entry_is_never_read_from_the_apk_database(lock):
    database = APK_DATABASE + "P:igt-gpu-tools\nV:9.9-r9\n\n"
    locked = lock(contents() | {DATABASE: database})
    assert "apk igt-gpu-tools 9.9-r9\n" in locked
    assert "tool igt-gpu-tools 2.3-r0\n" in locked


@pytest.mark.parametrize(
    ("new", "committed", "published", "expected"),
    [
        ("same", "same", "same", None),
        ("same", "same", "older", None),
        ("same", "same", None, None),
        ("new", "old", "new", CHORE_TITLE),
        ("new", "old", "other", FIX_TITLE),
        ("new", "old", "old", FIX_TITLE),
        ("new", "old", None, FIX_TITLE),
    ],
)
def test_decision_table(new, committed, published, expected):
    assert decide(new, committed, published) == expected


def test_titles_are_exactly_the_tables():
    assert CHORE_TITLE == "chore(deps): record image contents"
    assert FIX_TITLE == "fix(deps): update image packages"


@pytest.fixture
def docker(tmp_path):
    shim = tmp_path / "bin" / "docker"
    shim.parent.mkdir()
    shim.write_text((TESTS / "fake_docker.py").read_text())
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "docker.json"

    def run(files=None, fail=()):
        tar = archive(tmp_path / "exported.tar", files or contents())
        state.write_text(
            json.dumps({"archive": str(tar), "fail": list(fail), "calls": []})
        )
        env = os.environ | {
            "PATH": f"{shim.parent}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DOCKER_STATE": str(state),
        }
        result = subprocess.run(
            [str(SCRIPT), "some-image"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, json.loads(state.read_text())["calls"]

    return run


def test_cli_prints_the_lock_of_the_named_image(docker, lock):
    result, calls = docker()
    assert result.returncode == 0
    assert result.stdout == lock()
    assert calls[0] == ["create", "some-image"]


def test_cli_removes_the_container(docker):
    _, calls = docker()
    assert calls[-1] == ["rm", "fake-container"]


def test_cli_removes_the_container_after_a_bad_image(docker):
    files = contents()
    del files[TOOL_BINARY]
    result, calls = docker(files)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "intel_gpu_top" in result.stderr
    assert calls[-1] == ["rm", "fake-container"]


def test_cli_fails_when_docker_fails(docker):
    result, _ = docker(fail=["create"])
    assert result.returncode != 0
    assert result.stdout == ""


def test_committed_lock_is_canonical():
    text = (REPO / "image.lock").read_text()
    lines = text.splitlines()
    assert text.endswith("\n")
    assert lines == sorted(set(lines))
    for line in lines:
        assert LOCK_LINE.fullmatch(line), line
    assert {line.split()[0] for line in lines} == {"apk", "python", "tool"}
