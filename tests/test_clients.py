import copy
import logging

import pytest
from helpers import DATA_JSON, FIXTURES, labelled, load_samples, scrape

from intel_gpu_exporter.metrics import SampleCollector

CLIENTS = "igpu_clients_count"
WARNINGS = "igpu_parse_warnings_total"
FFMPEG = ("ffmpeg",)


def loaded_samples():
    return load_samples(FIXTURES / "loaded.json")


def idle_sample():
    return copy.deepcopy(load_samples(FIXTURES / "physical.json")[0])


def sample_with_clients(*names):
    sample = idle_sample()
    sample["clients"] = {str(i): {"name": name} for i, name in enumerate(names)}
    return sample


def counts(collector):
    return labelled(collector, CLIENTS)


def test_no_series_before_a_client_has_been_seen():
    collector = SampleCollector()
    collector.update(idle_sample())

    assert counts(collector) == {}


def test_capture_counts_the_client_then_reports_it_gone():
    collector = SampleCollector()
    seen = []

    for sample in loaded_samples():
        collector.update(sample)
        seen.append(counts(collector)[FFMPEG])

    assert seen == [1] * 10 + [0] * 5
    assert scrape(collector)[WARNINGS] == 0


def test_clients_sharing_a_name_are_counted_together():
    collector = SampleCollector()
    collector.update(sample_with_clients("ffmpeg", "ffmpeg", "vainfo"))

    assert counts(collector) == {FFMPEG: 2, ("vainfo",): 1}


def test_each_remembered_name_resets_on_its_own():
    collector = SampleCollector()
    collector.update(sample_with_clients("ffmpeg", "vainfo"))

    collector.update(sample_with_clients("vainfo"))

    assert counts(collector) == {FFMPEG: 0, ("vainfo",): 1}


def test_sample_with_no_clients_section_says_nothing_about_clients():
    collector = SampleCollector()
    collector.update(sample_with_clients("ffmpeg"))
    sample = idle_sample()
    del sample["clients"]

    collector.update(sample)
    assert counts(collector) == {}
    assert scrape(collector)[WARNINGS] == 0

    collector.update(idle_sample())
    assert counts(collector) == {FFMPEG: 0}


def test_absent_section_is_quiet(caplog):
    caplog.set_level(logging.INFO)
    collector = SampleCollector()

    collector.update(load_samples(DATA_JSON)[0])

    assert scrape(collector)[WARNINGS] == 0
    assert "'clients'" in caplog.text


def test_entry_with_no_name_warns_and_keeps_the_rest(caplog):
    sample = sample_with_clients("ffmpeg")
    sample["clients"]["7"] = {"pid": "1234"}
    collector = SampleCollector()

    collector.update(sample)

    assert counts(collector) == {FFMPEG: 1}
    assert scrape(collector)[WARNINGS] == 1
    assert "clients.7.name" in caplog.text


@pytest.mark.parametrize("name", [None, 5, "", ["ffmpeg"], "\ud800"])
def test_name_that_cannot_be_a_label_warns_and_scrape_succeeds(name):
    sample = sample_with_clients("ffmpeg")
    sample["clients"]["7"] = {"name": name}
    collector = SampleCollector()

    collector.update(sample)

    assert counts(collector) == {FFMPEG: 1}
    assert scrape(collector)[WARNINGS] == 1


def test_entry_that_is_not_an_object_warns_and_keeps_the_rest(caplog):
    sample = sample_with_clients("ffmpeg")
    sample["clients"]["7"] = "ffmpeg"
    collector = SampleCollector()

    collector.update(sample)

    assert counts(collector) == {FFMPEG: 1}
    assert scrape(collector)[WARNINGS] == 1
    assert "clients.7" in caplog.text


def test_section_that_is_not_an_object_warns_and_exports_nothing():
    sample = idle_sample()
    sample["clients"] = 5
    collector = SampleCollector()

    collector.update(sample)

    assert counts(collector) == {}
    assert scrape(collector)[WARNINGS] == 1


def test_label_value_is_escaped():
    collector = SampleCollector()
    collector.update(sample_with_clients('a"b\\c d'))

    assert counts(collector) == {('a"b\\c d',): 1}


def test_stale_data_hides_client_series():
    now = [1000.0]
    collector = SampleCollector(30.0, lambda: now[0])
    collector.update(sample_with_clients("ffmpeg"))
    assert counts(collector) == {FFMPEG: 1}

    now[0] += 31
    assert counts(collector) == {}

    collector.update(idle_sample())
    assert counts(collector) == {FFMPEG: 0}


def test_value_that_is_not_an_object_clears_client_series():
    collector = SampleCollector()
    collector.update(sample_with_clients("ffmpeg"))

    collector.update("garbage")

    assert counts(collector) == {}
