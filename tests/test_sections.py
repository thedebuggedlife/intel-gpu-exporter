import copy
import logging

import pytest
from helpers import DATA_JSON, FIXTURES, load_samples, scrape

from intel_gpu_exporter.metrics import SampleCollector

WARNINGS = "igpu_parse_warnings_total"


def full_sample():
    return copy.deepcopy(load_samples(DATA_JSON)[0])


def warning_records(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


def test_real_zero_is_exported():
    collector = SampleCollector()
    collector.update(load_samples(FIXTURES / "physical.json")[0])

    metrics = scrape(collector)

    assert metrics["igpu_rc6"] == 0
    assert metrics["igpu_frequency_actual"] == 0


def test_period_is_taken_from_the_sample():
    collector = SampleCollector()
    for sample in load_samples(FIXTURES / "physical.json"):
        collector.update(sample)
        assert scrape(collector)["igpu_period"] == sample["period"]["duration"]


def test_missing_required_section_warns(caplog):
    sample = full_sample()
    del sample["frequency"]
    collector = SampleCollector()

    collector.update(sample)

    metrics = scrape(collector)
    assert "igpu_frequency_actual" not in metrics
    assert "igpu_frequency_requested" not in metrics
    assert metrics[WARNINGS] == 1
    assert len(warning_records(caplog)) == 1


@pytest.mark.parametrize("section", ["imc-bandwidth", "power"])
def test_missing_optional_section_is_quiet(caplog, section):
    caplog.set_level(logging.INFO)
    sample = full_sample()
    del sample[section]
    collector = SampleCollector()

    collector.update(sample)
    collector.update(sample)

    metrics = scrape(collector)
    prefix = "igpu_imc_bandwidth" if section == "imc-bandwidth" else "igpu_power"
    assert not [name for name in metrics if name.startswith(prefix)]
    assert metrics[WARNINGS] == 0
    assert not warning_records(caplog)
    notices = [r for r in caplog.records if section in r.getMessage()]
    assert [r.levelno for r in notices] == [logging.INFO]


def test_missing_field_warns():
    sample = full_sample()
    del sample["frequency"]["actual"]
    collector = SampleCollector()

    collector.update(sample)

    metrics = scrape(collector)
    assert "igpu_frequency_actual" not in metrics
    assert "igpu_frequency_requested" in metrics
    assert metrics[WARNINGS] == 1


@pytest.mark.parametrize("value", [None, "12", True])
def test_non_numeric_values_warn_and_scrape_succeeds(value):
    sample = full_sample()
    sample["rc6"]["value"] = value
    collector = SampleCollector()

    collector.update(sample)

    metrics = scrape(collector)
    assert "igpu_rc6" not in metrics
    assert metrics[WARNINGS] == 1


@pytest.mark.parametrize(
    "sample",
    [[], "x", None, 5, {**load_samples(DATA_JSON)[0], "frequency": 5}],
)
def test_wrong_shapes_do_not_raise(sample):
    collector = SampleCollector()

    collector.update(sample)

    metrics = scrape(collector)
    assert metrics[WARNINGS] == 1
    assert "igpu_frequency_actual" not in metrics


def test_non_object_sample_clears_the_previous_sample():
    collector = SampleCollector()
    collector.update(full_sample())

    collector.update("garbage")

    metrics = scrape(collector)
    assert "igpu_rc6" not in metrics
    assert metrics[WARNINGS] == 1


def test_number_too_large_for_a_float_warns_and_scrape_succeeds():
    sample = full_sample()
    sample["rc6"]["value"] = 10**400
    collector = SampleCollector()

    collector.update(sample)

    metrics = scrape(collector)
    assert "igpu_rc6" not in metrics
    assert metrics[WARNINGS] == 1


def test_repeated_problem_counts_once(caplog):
    sample = full_sample()
    del sample["rc6"]
    collector = SampleCollector()

    collector.update(sample)
    collector.update(sample)

    assert scrape(collector)[WARNINGS] == 1
    assert len(warning_records(caplog)) == 1


def test_distinct_problems_count_separately():
    sample = full_sample()
    del sample["frequency"]
    del sample["rc6"]
    collector = SampleCollector()

    collector.update(sample)

    assert scrape(collector)[WARNINGS] == 2


def test_series_absent_from_next_sample_disappears():
    sample = full_sample()
    collector = SampleCollector()
    collector.update(sample)
    assert "igpu_imc_bandwidth_reads" in scrape(collector)

    del sample["imc-bandwidth"]
    collector.update(sample)

    assert "igpu_imc_bandwidth_reads" not in scrape(collector)
