import logging
import os

from prometheus_client import REGISTRY, start_http_server

from intel_gpu_exporter import stream
from intel_gpu_exporter.metrics import SampleCollector


def main():
    debug = logging.DEBUG if os.getenv("DEBUG") else logging.INFO
    logging.basicConfig(format="%(asctime)s - %(message)s", level=debug)

    collector = SampleCollector()
    REGISTRY.register(collector)
    start_http_server(8080)

    stream.run(collector)


if __name__ == "__main__":
    main()
