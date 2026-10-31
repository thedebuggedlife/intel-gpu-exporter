"""Stands in for intel_gpu_top: each spawn plays the next script from scenario.json."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ["FAKE_GPU_DIR"])
SPAWNS = ROOT / "spawns.log"
SAMPLE_INTERVAL = 0.02


def write(fd, data):
    while data:
        data = data[os.write(fd, data) :]


def record_spawn():
    count = len(SPAWNS.read_text().splitlines()) if SPAWNS.exists() else 0
    with SPAWNS.open("a") as spawns:
        print(
            json.dumps({"pid": os.getpid(), "argv": sys.argv[1:], "time": time.time()}),
            file=spawns,
        )
    return count


class Script:
    def __init__(self):
        self.sample = (ROOT / "sample.json").read_bytes().strip()
        self.opened = False

    def samples(self, count):
        remaining = count
        while remaining is None or remaining > 0:
            # 2.3 framing: the newline arrives with the next sample, never after a closing brace
            write(1, b"\n" + self.sample if self.opened else b"[\n\n" + self.sample)
            self.opened = True
            remaining = None if count is None else remaining - 1
            if remaining != 0:
                time.sleep(SAMPLE_INTERVAL)

    def raw(self, text):
        write(1, text.encode())

    def stderr(self, text, repeat):
        write(2, text.encode() * repeat)

    def wait(self, name):
        while not (ROOT / name).exists():
            time.sleep(0.005)

    def hang(self):
        while True:
            time.sleep(60)

    def exit(self, code):
        sys.exit(code)

    def ignore_sigterm(self):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def trap_sigterm(self):
        def leave(*_):
            (ROOT / "got-sigterm").touch()
            os._exit(0)

        signal.signal(signal.SIGTERM, leave)

    def noise(self):
        while True:
            write(1, b"1 ")
            time.sleep(SAMPLE_INTERVAL)

    def orphan(self):
        subprocess.Popen(["/bin/sleep", "30"])

    def writing_orphan(self):
        subprocess.Popen(
            ["/bin/sh", "-c", "while :; do printf '{}'; /bin/sleep 0.02; done"]
        )


def main():
    index = record_spawn()
    scripts = json.loads((ROOT / "scenario.json").read_text())
    script = Script()
    for name, *args in scripts[min(index, len(scripts) - 1)]:
        getattr(script, name)(*args)


if __name__ == "__main__":
    main()
