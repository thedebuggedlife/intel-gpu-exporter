import logging
import signal
import sys
import threading

from prometheus_client import REGISTRY, start_http_server

from intel_gpu_exporter.config import ConfigError, load_config
from intel_gpu_exporter.metrics import SampleCollector
from intel_gpu_exporter.supervisor import (
    Supervisor,
    Timing,
    build_command,
    stall_timeout,
)

log = logging.getLogger(__name__)


def main(port=None, timing=None):
    try:
        config = load_config()
    except ConfigError as error:
        sys.exit(str(error))

    level = logging.DEBUG if config.debug else logging.INFO
    logging.basicConfig(format="%(asctime)s - %(message)s", level=level)

    timing = timing or Timing(timeout=stall_timeout(config.refresh_period_ms))

    collector = SampleCollector(timing.timeout)
    REGISTRY.register(collector)
    server, _ = start_http_server(config.listen_port if port is None else port)
    log.info("Listening on port %d", server.server_port)

    requested = False

    def request_stop(*_):
        nonlocal requested
        requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    stop = threading.Event()
    supervisor = Supervisor(
        build_command(config.refresh_period_ms, config.device), collector, stop, timing
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

    if not requested:
        log.error("Watchdog ended with no stop requested")
        sys.exit(1)


if __name__ == "__main__":
    main()
