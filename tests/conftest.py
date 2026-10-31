import http.client
import json
import os
import re
import shlex
import signal
import subprocess
import sys

import pytest
from helpers import (
    FAST_TIMING,
    FIXTURES,
    REPO,
    TESTS,
    WAIT_LIMIT,
    load_samples,
    wait_until,
)
from prometheus_client.parser import text_string_to_metric_families

FAKE = TESTS / "fake_intel_gpu_top.py"
CONFIG_VARIABLES = {"LISTEN_PORT", "REFRESH_PERIOD_MS", "DEVICE", "DEBUG"}
LAUNCHER = """
{prelude}
from intel_gpu_exporter.__main__ import main
from intel_gpu_exporter.supervisor import Timing
main(port=0, timing=Timing(**{timing!r}))
"""


class FakeGpuTop:
    def __init__(self, root):
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir()
        sample = load_samples(FIXTURES / "physical.json")[0]
        (root / "sample.json").write_text(json.dumps(sample, indent=1))

    def install(self):
        executable = self.bin / "intel_gpu_top"
        python = shlex.quote(sys.executable)
        executable.write_text(
            f'#!/bin/sh\nexec {python} -I -S {shlex.quote(str(FAKE))} "$@"\n'
        )
        executable.chmod(0o755)

    def play(self, *scripts):
        (self.root / "scenario.json").write_text(json.dumps(scripts))
        self.install()

    def release(self, name):
        (self.root / name).touch()

    @property
    def spawns(self):
        path = self.root / "spawns.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def wait_for_spawns(self, count):
        return wait_until(lambda: len(self.spawns) >= count and self.spawns)


class Exporter:
    def __init__(self, process, log_path):
        self.process = process
        self.log_path = log_path
        match = wait_until(lambda: re.search(r"Listening on port (\d+)", self.log()))
        self.port = int(match[1])

    def log(self):
        return self.log_path.read_text()

    def get(self, path="/metrics"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.read().decode()
        finally:
            connection.close()

    def metrics(self):
        status, body = self.get()
        assert status == 200
        return {
            sample.name: sample.value
            for family in text_string_to_metric_families(body)
            for sample in family.samples
            if sample.name.startswith("igpu_")
        }

    def wait_for_metrics(self, **expected):
        def matches():
            metrics = self.metrics()
            if all(metrics.get(name) == value for name, value in expected.items()):
                return metrics
            return None

        return wait_until(matches)

    def wait_for_restarts(self, count):
        def reached():
            metrics = self.metrics()
            return (
                metrics if metrics["igpu_collector_restarts_total"] >= count else None
            )

        return wait_until(reached)

    def wait_for_exit(self, *signums):
        for signum in signums:
            self.process.send_signal(signum)
        return self.process.wait(WAIT_LIMIT)


@pytest.fixture
def fake_gpu(tmp_path):
    return FakeGpuTop(tmp_path)


@pytest.fixture
def exporter_env(fake_gpu, tmp_path):
    inherited = {k: v for k, v in os.environ.items() if k not in CONFIG_VARIABLES}
    return {**inherited, "PATH": str(fake_gpu.bin), "FAKE_GPU_DIR": str(tmp_path)}


@pytest.fixture
def start_exporter(exporter_env, tmp_path):
    processes = []

    def start(timing=None, prelude="", module=False, **env):
        log_path = tmp_path / "exporter.log"
        timing = {**FAST_TIMING, **(timing or {})}
        launcher = LAUNCHER.format(prelude=prelude, timing=timing)
        args = ["-m", "intel_gpu_exporter"] if module else ["-c", launcher]
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [sys.executable, *args],
                cwd=REPO,
                env={**exporter_env, **env},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        processes.append(process)
        return Exporter(process, log_path)

    yield start

    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()
