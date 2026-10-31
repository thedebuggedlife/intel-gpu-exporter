import logging
import os
import signal
import threading

from prometheus_client import REGISTRY, start_http_server

from intel_gpu_exporter.metrics import SampleCollector
from intel_gpu_exporter.supervisor import (
    Supervisor,
    Timing,
    build_command,
    stall_timeout,
)

log = logging.getLogger(__name__)


def main(port=8080, timing=None):
    debug = logging.DEBUG if os.getenv("DEBUG") else logging.INFO
    logging.basicConfig(format="%(asctime)s - %(message)s", level=debug)

    period = int(os.getenv("REFRESH_PERIOD_MS", "10000"))
    timing = timing or Timing(timeout=stall_timeout(period))

    collector = SampleCollector(timing.timeout)
    REGISTRY.register(collector)
    server, _ = start_http_server(port)
    log.info("Listening on port %d", server.server_port)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    supervisor = Supervisor(
        build_command(period, os.getenv("DEVICE")), collector, stop, timing
    )
    watchdog = threading.Thread(target=supervisor.run, name="watchdog", daemon=True)
    watchdog.start()
    # Stay out of Event.wait() here: Event.set() in a handler can deadlock against it
    watchdog.join()


if __name__ == "__main__":
    main()
