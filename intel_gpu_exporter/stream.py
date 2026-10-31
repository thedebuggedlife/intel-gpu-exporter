import json
import logging
import os
import subprocess

log = logging.getLogger(__name__)


def run(collector):
    period = os.getenv("REFRESH_PERIOD_MS", "10000")
    device = os.getenv("DEVICE")

    if device is not None:
        cmd = f"intel_gpu_top -J -s {int(period)} -d {device}"
    else:
        cmd = f"intel_gpu_top -J -s {int(period)}"

    process = subprocess.Popen(
        cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    log.info("Started %s", cmd)
    output = ""

    if os.getenv("IS_DOCKER"):
        for line in process.stdout:
            line = line.decode("utf-8").strip()
            output += line

            try:
                data = json.loads(output.strip(","))
                log.debug(data)
                collector.update(data)
                output = ""
            except json.JSONDecodeError:
                continue
    else:
        while process.poll() is None:
            read = process.stdout.readline()
            output += read.decode("utf-8")
            log.debug(output)
            if read == b"},\n":
                collector.update(json.loads(output[:-2]))
                output = ""

    process.kill()

    if process.returncode != 0:
        log.error("Error: %s", process.stderr.read().decode("utf-8"))

    log.info("Finished")
