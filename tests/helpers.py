import os
import time
from pathlib import Path

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from intel_gpu_exporter.stream import StreamDecoder

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
FIXTURES = TESTS / "fixtures"
DATA_JSON = TESTS / "data.json"
WAIT_LIMIT = 10
FAST_TIMING = {
    "timeout": 0.5,
    "backoff_initial": 0.02,
    "backoff_cap": 0.16,
    "stop_grace": 0.3,
    "join_grace": 0.1,
    "poll": 0.01,
}


def load_samples(path):
    return StreamDecoder(lambda key, message: None).feed(path.read_bytes())


def exposition(collector):
    registry = CollectorRegistry()
    registry.register(collector)
    return generate_latest(registry).decode()


def scrape(collector):
    return {
        sample.name: sample.value
        for family in text_string_to_metric_families(exposition(collector))
        for sample in family.samples
        if sample.name.startswith("igpu_")
    }


def labelled(collector, name):
    return {
        tuple(sample.labels.values()): sample.value
        for family in text_string_to_metric_families(exposition(collector))
        for sample in family.samples
        if sample.name == name
    }


def wait_until(condition, limit=WAIT_LIMIT):
    deadline = time.monotonic() + limit
    while True:
        result = condition()
        if result:
            return result
        assert time.monotonic() < deadline, "Condition not reached in time"
        time.sleep(0.005)


def pid_is_gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False
