import logging
import subprocess
import threading
import time
from dataclasses import dataclass

from intel_gpu_exporter.metrics import DEFAULT_TIMEOUT
from intel_gpu_exporter.stream import StreamDecoder

log = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class Timing:
    timeout: float
    backoff_initial: float = 1.0
    backoff_cap: float = 60.0
    stop_grace: float = 5.0
    join_grace: float = 1.0
    poll: float = 0.25


def stall_timeout(period_ms):
    return max(3 * period_ms / 1000, DEFAULT_TIMEOUT)


def build_command(period_ms, device):
    argv = ["intel_gpu_top", "-J", "-p", "-s", str(period_ms)]
    if device is not None:
        argv += ["-d", device]
    return argv


def read_stderr(stream):
    pending = b""
    with stream:
        while chunk := stream.read(CHUNK_SIZE):
            *lines, pending = (pending + chunk).split(b"\n")
            if len(pending) >= CHUNK_SIZE:
                lines.append(pending)
                pending = b""
            for line in lines:
                _log_stderr(line)
    _log_stderr(pending)


def _log_stderr(line):
    if line:
        log.warning("intel_gpu_top: %s", line.decode("utf-8", errors="replace"))


class Backoff:
    def __init__(self, initial, cap):
        self._initial = initial
        self._cap = cap
        self.reset()

    def reset(self):
        self._next = self._initial

    def next(self):
        delay = self._next
        self._next = min(delay * 2, self._cap)
        return delay


class Supervisor:
    def __init__(self, argv, collector, stop, timing):
        self._argv = argv
        self._collector = collector
        self._stop = stop
        self._timing = timing
        self._child = None

    def run(self):
        backoff = Backoff(self._timing.backoff_initial, self._timing.backoff_cap)
        self._run_child(backoff)
        while not self._stop.wait(backoff.next()):
            self._collector.count_restart()
            self._run_child(backoff)

    def _run_child(self, backoff):
        spawned = time.monotonic()
        try:
            process = subprocess.Popen(
                self._argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
            )
        except OSError as error:
            log.error("Can't start intel_gpu_top: %s", error)
            return
        log.info("Started %s", self._argv)
        self._child = process

        readers = [
            threading.Thread(target=self._read_stdout, args=(process,), daemon=True),
            threading.Thread(target=read_stderr, args=(process.stderr,), daemon=True),
        ]
        for reader in readers:
            reader.start()

        self._watch(process, spawned)

        for reader in readers:
            reader.join(self._timing.join_grace)
        self._child = None

        last_sample = self._collector.last_sample
        if last_sample is not None and last_sample >= spawned:
            backoff.reset()

    def _read_stdout(self, process):
        decoder = StreamDecoder(self._collector.warn)
        with process.stdout as stream:
            while (chunk := stream.read(CHUNK_SIZE)) and process is self._child:
                for value in decoder.feed(chunk):
                    log.debug(value)
                    self._collector.update(value)

    def _watch(self, process, spawned):
        while process.poll() is None:
            if self._stop.is_set():
                self._terminate(process)
                return

            # A sample from an earlier child must not count against this one
            quiet_since = max(spawned, self._collector.last_sample or spawned)
            remaining = quiet_since + self._timing.timeout - time.monotonic()
            if remaining <= 0:
                log.warning(
                    "No sample for %s s, killing intel_gpu_top", self._timing.timeout
                )
                process.kill()
                process.wait()
                return
            self._stop.wait(min(remaining, self._timing.poll))

        log.warning("intel_gpu_top exited with code %s", process.returncode)

    def _terminate(self, process):
        process.terminate()
        try:
            process.wait(self._timing.stop_grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
