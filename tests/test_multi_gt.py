import copy

import pytest
from helpers import FIXTURES, labelled, load_samples, scrape

from intel_gpu_exporter.metrics import SampleCollector

WARNINGS = "igpu_parse_warnings_total"
CONTRACT = ["igpu_frequency_actual", "igpu_frequency_requested", "igpu_rc6"]
PER_GT = ["igpu_gt_frequency_actual", "igpu_gt_frequency_requested", "igpu_gt_rc6"]


def multi_gt_sample():
    return copy.deepcopy(load_samples(FIXTURES / "handwritten-multi-gt.json")[0])


def collect(sample):
    collector = SampleCollector()
    collector.update(sample)
    return collector


def test_gt_0_fills_the_contract_metrics():
    metrics = scrape(collect(multi_gt_sample()))

    assert metrics["igpu_frequency_actual"] == 350
    assert metrics["igpu_frequency_requested"] == 400
    assert metrics["igpu_rc6"] == 85.5
    assert metrics[WARNINGS] == 0


def test_every_gt_has_labelled_series():
    collector = collect(multi_gt_sample())

    assert labelled(collector, "igpu_gt_frequency_actual") == {
        ("0",): 350,
        ("1",): 1100,
    }
    assert labelled(collector, "igpu_gt_frequency_requested") == {
        ("0",): 400,
        ("1",): 1200,
    }
    assert labelled(collector, "igpu_gt_rc6") == {("0",): 85.5, ("1",): 12.25}


def test_capture_values_follow_the_latest_sample():
    collector = SampleCollector()
    for sample in load_samples(FIXTURES / "handwritten-multi-gt.json"):
        collector.update(sample)

    assert labelled(collector, "igpu_gt_rc6") == {("0",): 100, ("1",): 0}
    assert scrape(collector)["igpu_frequency_actual"] == 0


def test_contract_metrics_have_no_labels():
    collector = collect(multi_gt_sample())

    for name in CONTRACT:
        assert labelled(collector, name).keys() == {()}


@pytest.mark.parametrize("name", PER_GT)
def test_single_gt_output_has_no_labelled_series(name):
    sample = load_samples(FIXTURES / "physical.json")[0]

    assert labelled(collect(sample), name) == {}


def test_a_later_gt_alone_satisfies_the_required_sections():
    sample = multi_gt_sample()
    del sample["frequency-gt0"]
    del sample["rc6-gt0"]

    collector = collect(sample)

    metrics = scrape(collector)
    assert not set(CONTRACT) & set(metrics)
    assert metrics[WARNINGS] == 0
    assert labelled(collector, "igpu_gt_rc6") == {("1",): 12.25}


def test_malformed_gt_sections_still_satisfy_the_required_section():
    sample = multi_gt_sample()
    sample["rc6-gt0"] = 5
    sample["rc6-gt1"] = 5

    assert scrape(collect(sample))[WARNINGS] == 2


def test_each_section_is_required_on_its_own(caplog):
    sample = multi_gt_sample()
    del sample["frequency-gt0"]
    del sample["frequency-gt1"]

    assert scrape(collect(sample))[WARNINGS] == 1
    assert "'frequency'" in caplog.text


def test_missing_field_warns_and_keeps_the_rest(caplog):
    sample = multi_gt_sample()
    del sample["frequency-gt1"]["actual"]

    collector = collect(sample)

    assert labelled(collector, "igpu_gt_frequency_actual") == {("0",): 350}
    assert ("1",) in labelled(collector, "igpu_gt_frequency_requested")
    assert scrape(collector)[WARNINGS] == 1
    assert "frequency-gt1.actual" in caplog.text


def test_missing_field_in_gt_0_leaves_a_gap_in_the_contract_metric():
    sample = multi_gt_sample()
    del sample["rc6-gt0"]["value"]

    metrics = scrape(collect(sample))

    assert "igpu_rc6" not in metrics
    assert metrics[WARNINGS] == 1


def test_wrong_shape_warns_once_and_does_not_raise(caplog):
    sample = multi_gt_sample()
    sample["rc6-gt0"] = 5

    collector = collect(sample)

    assert scrape(collector)[WARNINGS] == 1
    assert "rc6-gt0" in caplog.text
    assert labelled(collector, "igpu_gt_rc6") == {("1",): 12.25}


@pytest.mark.parametrize("key", ["rc6-gt", "rc6-gtX", "rc6-gt٣", "rc6-gt1-extra"])
def test_key_that_is_not_a_gt_section_is_ignored(key):
    sample = load_samples(FIXTURES / "physical.json")[0]
    sample[key] = {"value": 50.0}

    collector = collect(sample)

    assert labelled(collector, "igpu_gt_rc6") == {}
    assert scrape(collector)[WARNINGS] == 0


def test_stale_data_hides_labelled_series():
    now = [1000.0]
    collector = SampleCollector(30.0, lambda: now[0])
    collector.update(multi_gt_sample())

    now[0] += 31

    for name in PER_GT:
        assert labelled(collector, name) == {}


def test_labelled_series_disappear_with_their_section():
    collector = SampleCollector()
    collector.update(multi_gt_sample())

    collector.update(load_samples(FIXTURES / "physical.json")[0])

    assert labelled(collector, "igpu_gt_rc6") == {}
