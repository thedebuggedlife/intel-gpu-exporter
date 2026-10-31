import time

import pytest
from helpers import DATA_JSON, exposition, load_samples, scrape
from prometheus_client.parser import text_string_to_metric_families

from intel_gpu_exporter.metrics import SampleCollector

TIMEOUT = 30.0
UP = "igpu_up"
LAST_SAMPLE = "igpu_last_sample_timestamp_seconds"
RESTARTS = "igpu_collector_restarts_total"
WARNINGS = "igpu_parse_warnings_total"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def collector(clock):
    return SampleCollector(TIMEOUT, clock)


def sample():
    return load_samples(DATA_JSON)[0]


def test_before_the_first_sample(collector):
    assert scrape(collector) == {UP: 0, RESTARTS: 0, WARNINGS: 0}


def test_fresh_sample_reports_up_with_its_arrival_time(collector):
    before = time.time()
    collector.update(sample())
    after = time.time()

    metrics = scrape(collector)

    assert metrics[UP] == 1
    assert before <= metrics[LAST_SAMPLE] <= after
    assert metrics["igpu_rc6"] == 92.505022


def test_up_lasts_until_the_timeout_has_passed(collector, clock):
    collector.update(sample())

    clock.now += TIMEOUT
    assert scrape(collector)[UP] == 1

    clock.now += 0.001
    assert scrape(collector)[UP] == 0


def test_stale_data_is_a_gap(collector, clock):
    collector.update(sample())
    last_sample = scrape(collector)[LAST_SAMPLE]

    clock.now += TIMEOUT + 1

    assert scrape(collector) == {
        UP: 0,
        LAST_SAMPLE: last_sample,
        RESTARTS: 0,
        WARNINGS: 0,
    }


def test_data_returns_with_the_next_sample(collector, clock):
    collector.update(sample())
    clock.now += TIMEOUT + 1
    assert "igpu_rc6" not in scrape(collector)

    collector.update(sample())

    metrics = scrape(collector)
    assert metrics[UP] == 1
    assert metrics["igpu_rc6"] == 92.505022


def test_value_that_is_not_an_object_is_not_a_sample(collector, clock):
    collector.update(sample())
    arrival = collector.last_sample

    clock.now += 1
    collector.update("garbage")
    assert collector.last_sample == arrival

    clock.now += TIMEOUT
    assert scrape(collector)[UP] == 0


def test_first_value_that_is_not_an_object_leaves_up_at_zero(collector):
    collector.update("garbage")

    assert scrape(collector) == {UP: 0, RESTARTS: 0, WARNINGS: 1}


def test_last_sample_is_the_monotonic_arrival_time(collector, clock):
    assert collector.last_sample is None

    clock.now += 5
    collector.update(sample())

    assert collector.last_sample == clock.now


def test_restarts_are_counted(collector):
    collector.count_restart()
    collector.count_restart()

    assert scrape(collector)[RESTARTS] == 2


def test_warn_counts_once_per_key(collector):
    collector.warn("stream-buffer", "dropped")
    collector.warn("stream-buffer", "dropped")

    assert scrape(collector)[WARNINGS] == 1


def test_metric_types(collector):
    collector.update(sample())

    families = text_string_to_metric_families(exposition(collector))
    types = {family.name: family.type for family in families}

    assert types[UP] == "gauge"
    assert types[LAST_SAMPLE] == "gauge"
    assert types["igpu_collector_restarts"] == "counter"
