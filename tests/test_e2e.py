import os
import re
import signal
import time

import pytest
from helpers import FAST_TIMING, pid_is_gone, wait_until

UP = "igpu_up"
RESTARTS = "igpu_collector_restarts_total"
WARNINGS = "igpu_parse_warnings_total"
LAST_SAMPLE = "igpu_last_sample_timestamp_seconds"

STREAM = [["samples", None]]
ONE_SAMPLE_THEN_HANG = [["samples", 1], ["hang"]]
# Fake startup jitter must not decide a test that measures a child's time allowance
WIDE_TIMEOUT = {"timeout": 1}
LONGEST_LOG_LINE = 256 * 1024


def test_normal_stream_reaches_metrics(fake_gpu, start_exporter):
    fake_gpu.play(STREAM)

    metrics = start_exporter().wait_for_metrics(igpu_up=1)

    assert metrics["igpu_engines_video_1_busy"] == 0
    assert metrics[RESTARTS] == 0
    assert metrics[WARNINGS] == 0
    assert LAST_SAMPLE in metrics


def test_command_line_defaults(fake_gpu, start_exporter):
    fake_gpu.play(STREAM)

    start_exporter()

    assert fake_gpu.wait_for_spawns(1)[0]["argv"] == ["-J", "-p", "-s", "10000"]


def test_command_line_from_the_environment(fake_gpu, start_exporter):
    fake_gpu.play(STREAM)

    start_exporter(REFRESH_PERIOD_MS="250", DEVICE="pci:vendor=8086,card=0 x")

    assert fake_gpu.wait_for_spawns(1)[0]["argv"] == [
        "-J",
        "-p",
        "-s",
        "250",
        "-d",
        "pci:vendor=8086,card=0 x",
    ]


def test_sample_is_exported_without_waiting_for_the_next_one(fake_gpu, start_exporter):
    fake_gpu.play(ONE_SAMPLE_THEN_HANG)

    metrics = start_exporter({"timeout": 30}).wait_for_metrics(igpu_up=1)

    assert metrics["igpu_period"] == 51.296604


def test_hang_restarts_the_child_and_shows_a_gap(fake_gpu, start_exporter):
    fake_gpu.play(ONE_SAMPLE_THEN_HANG, [["wait", "go"], *STREAM])
    exporter = start_exporter()
    hung = exporter.wait_for_metrics(igpu_up=1) and fake_gpu.spawns[0]["pid"]

    stale = exporter.wait_for_metrics(igpu_up=0, igpu_collector_restarts_total=1)

    assert set(stale) == {UP, LAST_SAMPLE, RESTARTS, WARNINGS}
    assert pid_is_gone(hung)

    fake_gpu.release("go")
    recovered = exporter.wait_for_metrics(igpu_up=1)

    assert recovered[RESTARTS] == 1
    assert recovered["igpu_rc6"] == 0


def test_slow_starting_child_gets_a_full_timeout_after_a_hang(fake_gpu, start_exporter):
    fake_gpu.play(ONE_SAMPLE_THEN_HANG, [["wait", "go"], *STREAM])
    exporter = start_exporter(WIDE_TIMEOUT)
    exporter.wait_for_metrics(igpu_up=1)
    slow = fake_gpu.wait_for_spawns(2)[1]["pid"]

    time.sleep(0.2)

    assert not pid_is_gone(slow)
    assert len(fake_gpu.spawns) == 2

    fake_gpu.release("go")
    assert exporter.wait_for_metrics(igpu_up=1)[RESTARTS] == 1


def test_child_that_never_samples_is_killed_after_the_timeout(fake_gpu, start_exporter):
    fake_gpu.play([["hang"]])
    exporter = start_exporter(WIDE_TIMEOUT)

    first, second = fake_gpu.wait_for_spawns(2)[:2]

    assert pid_is_gone(first["pid"])
    assert second["time"] - first["time"] >= 0.5
    assert LAST_SAMPLE not in exporter.wait_for_metrics(igpu_up=0)


def test_stopped_child_is_killed(fake_gpu, start_exporter):
    fake_gpu.play([["ignore_sigterm"], *STREAM], STREAM)
    exporter = start_exporter()
    exporter.wait_for_metrics(igpu_up=1)
    stopped = fake_gpu.spawns[0]["pid"]

    os.kill(stopped, signal.SIGSTOP)

    fake_gpu.wait_for_spawns(2)
    assert pid_is_gone(stopped)
    exporter.wait_for_metrics(igpu_up=1, igpu_collector_restarts_total=1)


@pytest.mark.parametrize("code", [0, 3])
def test_exit_restarts_and_recovers(fake_gpu, start_exporter, code):
    exit_at_once = [["exit", code]]
    fake_gpu.play(exit_at_once, exit_at_once, exit_at_once, STREAM)

    start_exporter().wait_for_metrics(igpu_up=1, igpu_collector_restarts_total=3)


def test_restart_starts_with_a_new_decoder(fake_gpu, start_exporter):
    fake_gpu.play([["samples", 1], ["raw", '{"period": {"dura'], ["exit", 1]], STREAM)

    metrics = start_exporter().wait_for_metrics(
        igpu_up=1, igpu_collector_restarts_total=1
    )

    wait_until(lambda: len(fake_gpu.spawns) == 2)
    assert metrics[WARNINGS] == 0


def test_malformed_output_is_restarted_by_the_watchdog(fake_gpu, start_exporter):
    fake_gpu.play([["samples", 1], ["raw", "not json"], ["hang"]], STREAM)
    exporter = start_exporter()

    exporter.wait_for_metrics(igpu_collector_restarts_total=1)
    metrics = exporter.wait_for_metrics(igpu_up=1)

    assert metrics[WARNINGS] == 0


def test_stream_of_non_objects_does_not_count_as_samples(fake_gpu, start_exporter):
    fake_gpu.play([["raw", "1 2 3 "], ["hang"]], [["hang"]])
    exporter = start_exporter()

    metrics = exporter.wait_for_metrics(igpu_collector_restarts_total=1)

    assert metrics[UP] == 0
    assert metrics[WARNINGS] == 1


def test_missing_binary_keeps_serving_and_recovers(fake_gpu, start_exporter):
    exporter = start_exporter()

    metrics = exporter.wait_for_metrics(igpu_collector_restarts_total=2)
    assert metrics[UP] == 0

    fake_gpu.play(STREAM)
    exporter.wait_for_metrics(igpu_up=1)


def test_open_pipe_held_by_a_stray_process_does_not_block_a_restart(
    fake_gpu, start_exporter
):
    fake_gpu.play([["orphan"], *ONE_SAMPLE_THEN_HANG], STREAM)

    start_exporter().wait_for_metrics(igpu_up=1, igpu_collector_restarts_total=1)


def test_heavy_stderr_is_logged_and_does_not_block_the_child(fake_gpu, start_exporter):
    lines = 50_000
    fake_gpu.play([["stderr", "engine noise\n", lines], *STREAM])
    exporter = start_exporter({"timeout": 30})

    metrics = exporter.wait_for_metrics(igpu_up=1)

    assert metrics[WARNINGS] == 0
    wait_until(lambda: exporter.log().count("engine noise") == lines)


def test_stderr_without_a_newline_stays_bounded(fake_gpu, start_exporter):
    size = 3 * 1024 * 1024
    fake_gpu.play([["stderr", "x", size], *STREAM])
    exporter = start_exporter({"timeout": 30})

    exporter.wait_for_metrics(igpu_up=1)

    def logged_pieces():
        return [len(piece) for piece in re.findall("x{100,}", exporter.log())]

    wait_until(lambda: sum(logged_pieces()) >= size - LONGEST_LOG_LINE)
    assert max(logged_pieces()) <= LONGEST_LOG_LINE


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_stops_the_child_and_exits_zero(fake_gpu, start_exporter, signum):
    fake_gpu.play(STREAM)
    exporter = start_exporter()
    exporter.wait_for_metrics(igpu_up=1)

    assert exporter.wait_for_exit(signum) == 0

    spawns = fake_gpu.spawns
    assert len(spawns) == 1
    assert pid_is_gone(spawns[0]["pid"])


def test_signal_gives_the_child_sigterm_first(fake_gpu, start_exporter, tmp_path):
    fake_gpu.play([["trap_sigterm"], *STREAM])
    exporter = start_exporter()
    exporter.wait_for_metrics(igpu_up=1)

    assert exporter.wait_for_exit(signal.SIGTERM) == 0

    assert (tmp_path / "got-sigterm").exists()


def test_child_ignoring_sigterm_is_killed_after_the_grace_period(
    fake_gpu, start_exporter
):
    fake_gpu.play([["ignore_sigterm"], *STREAM])
    exporter = start_exporter()
    exporter.wait_for_metrics(igpu_up=1)
    started = time.monotonic()

    assert exporter.wait_for_exit(signal.SIGTERM) == 0

    assert time.monotonic() - started >= FAST_TIMING["stop_grace"]
    assert pid_is_gone(fake_gpu.spawns[0]["pid"])


def test_signal_during_backoff_exits_at_once_without_a_restart(
    fake_gpu, start_exporter
):
    fake_gpu.play([["exit", 1]])
    exporter = start_exporter({"backoff_initial": 60, "backoff_cap": 60})
    fake_gpu.wait_for_spawns(1)
    exporter.wait_for_metrics(igpu_up=0)

    assert exporter.wait_for_exit(signal.SIGTERM) == 0

    assert len(fake_gpu.spawns) == 1
