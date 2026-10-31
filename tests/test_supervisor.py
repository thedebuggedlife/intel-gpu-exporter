import logging
import os
import sys
import threading

import pytest
from helpers import scrape, wait_until

from intel_gpu_exporter.metrics import SampleCollector
from intel_gpu_exporter.supervisor import (
    Backoff,
    Supervisor,
    Timing,
    build_command,
    read_stderr,
    stall_timeout,
)

MISSING_BINARY = ["/nonexistent/intel_gpu_top"]
SAMPLE_THEN_EXIT = [sys.executable, "-I", "-S", "-c", "print('{}', end='')"]


class RecordingStop(threading.Event):
    """Records each backoff instead of sleeping through it, and stops after `limit` of them."""

    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.delays = []

    def wait(self, timeout=None):
        if timeout < 1:
            return super().wait(timeout)
        self.delays.append(timeout)
        if len(self.delays) == self.limit:
            self.set()
        return self.is_set()


def run_supervisor(argv, stop, collector=None):
    timing = Timing(timeout=30, poll=0.001)
    Supervisor(argv, collector or SampleCollector(), stop, timing).run()


@pytest.mark.parametrize(
    ("period_ms", "device", "expected"),
    [
        (10000, None, ["intel_gpu_top", "-J", "-p", "-s", "10000"]),
        (250, "card0", ["intel_gpu_top", "-J", "-p", "-s", "250", "-d", "card0"]),
        (250, "a b", ["intel_gpu_top", "-J", "-p", "-s", "250", "-d", "a b"]),
    ],
)
def test_build_command(period_ms, device, expected):
    assert build_command(period_ms, device) == expected


@pytest.mark.parametrize(
    ("period_ms", "expected"),
    [(1000, 30), (10000, 30), (10001, 30.003), (20000, 60)],
)
def test_stall_timeout_is_three_periods_with_a_30_second_floor(period_ms, expected):
    assert stall_timeout(period_ms) == expected


def test_backoff_doubles_to_the_cap():
    backoff = Backoff(1, 60)

    assert [backoff.next() for _ in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]


def test_backoff_reset_starts_over():
    backoff = Backoff(1, 60)
    backoff.next()
    backoff.next()

    backoff.reset()

    assert backoff.next() == 1


def test_failed_spawn_takes_the_exit_path():
    stop = RecordingStop(limit=8)
    collector = SampleCollector()

    run_supervisor(MISSING_BINARY, stop, collector)

    assert stop.delays == [1, 2, 4, 8, 16, 32, 60, 60]
    assert scrape(collector)["igpu_collector_restarts_total"] == 7


def test_sample_resets_the_backoff():
    stop = RecordingStop(limit=3)

    run_supervisor(SAMPLE_THEN_EXIT, stop)

    assert stop.delays == [1, 1, 1]


def test_child_that_exits_without_a_sample_keeps_backing_off():
    stop = RecordingStop(limit=3)

    run_supervisor([sys.executable, "-I", "-S", "-c", ""], stop)

    assert stop.delays == [1, 2, 4]


def test_sample_from_an_earlier_child_does_not_reset_the_backoff(
    fake_gpu, monkeypatch, tmp_path
):
    monkeypatch.setenv("FAKE_GPU_DIR", str(tmp_path))
    fake_gpu.play([["samples", 1]], [["exit", 1]])
    stop = RecordingStop(limit=3)

    run_supervisor([str(fake_gpu.bin / "intel_gpu_top")], stop)

    assert stop.delays == [1, 2, 4]


def test_no_spawn_once_stopped(tmp_path):
    marker = tmp_path / "spawned"
    stop = RecordingStop(limit=1)
    argv = [sys.executable, "-I", "-S", "-c", f"open({str(marker)!r}, 'a').write('x')"]

    run_supervisor(argv, stop)

    assert marker.read_text() == "x"


@pytest.fixture
def stderr_reader():
    read_end, write_end = os.pipe()
    stream = os.fdopen(read_end, "rb", 0)
    reader = threading.Thread(target=read_stderr, args=(stream,), daemon=True)
    reader.start()
    return reader, write_end


def test_stderr_is_logged_as_it_arrives(caplog, stderr_reader):
    reader, write_end = stderr_reader

    os.write(write_end, b"first\nsecond\n\npartial")
    wait_until(lambda: len(caplog.records) == 2)
    os.write(write_end, b" line\xff")
    os.close(write_end)
    reader.join(5)

    assert [record.getMessage() for record in caplog.records] == [
        "intel_gpu_top: first",
        "intel_gpu_top: second",
        "intel_gpu_top: partial line\ufffd",
    ]
    assert {record.levelno for record in caplog.records} == {logging.WARNING}


def test_stderr_without_a_newline_is_logged_before_the_stream_ends(
    caplog, stderr_reader
):
    reader, write_end = stderr_reader
    block = b"x" * 4096
    blocks = 256

    for _ in range(blocks):
        os.write(write_end, block)
    wait_until(lambda: caplog.records)
    os.close(write_end)
    reader.join(5)

    pieces = [
        len(record.getMessage().removeprefix("intel_gpu_top: "))
        for record in caplog.records
    ]
    assert sum(pieces) == blocks * len(block)
    assert max(pieces) <= 256 * 1024
