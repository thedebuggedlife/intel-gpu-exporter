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

    requested = False

    def request_stop(*_):
        nonlocal requested
        requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    stop = threading.Event()
    supervisor = Supervisor(
        build_command(period, os.getenv("DEVICE")), collector, stop, timing
    )
    watchdog = threading.Thread(target=supervisor.run, name="watchdog", daemon=True)
    watchdog.start()
    # Timed, because a signal taken by another thread doesn't wake an untimed join
    while watchdog.is_alive() and not requested:
        watchdog.join(timing.poll)
    # Outside the handler, because Event.set() can deadlock if a handler re-enters it
    stop.set()
    # Bounded, so a child stuck in the kernel can't hold up exit
    watchdog.join(2 * timing.stop_grace)


if __name__ == "__main__":
    main()
