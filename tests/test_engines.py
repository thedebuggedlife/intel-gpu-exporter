import copy
import re

import pytest
from helpers import DATA_JSON, FIXTURES, exposition, load_samples, scrape

from intel_gpu_exporter.metrics import SampleCollector, sanitise_class

WARNINGS = "igpu_parse_warnings_total"


def entry(busy=1.5):
    return {"busy": busy, "sema": 0.0, "wait": 0.0, "unit": "%"}


def collect_engines(engines):
    sample = copy.deepcopy(load_samples(DATA_JSON)[0])
    sample["engines"] = engines
    collector = SampleCollector()
    collector.update(sample)
    return collector


def engine_metrics(collector):
    metrics = scrape(collector)
    return {k: v for k, v in metrics.items() if k.startswith("igpu_engines_")}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Render/3D", "render_3d"),
        ("Blitter", "blitter"),
        ("Video", "video"),
        ("VideoEnhance", "video_enhance"),
        ("Compute", "compute"),
        ("[unknown]", "unknown"),
        ("Foo Bar", "foo_bar"),
    ],
)
def test_sanitise_class(name, expected):
    assert sanitise_class(name) == expected


def test_engine_key_publishes_all_fields():
    collector = collect_engines({"Video/1": entry()})

    assert engine_metrics(collector) == {
        "igpu_engines_video_1_busy": 1.5,
        "igpu_engines_video_1_sema": 0.0,
        "igpu_engines_video_1_wait": 0.0,
    }


def test_render_key_keeps_its_slash_in_the_class():
    collector = collect_engines({"Render/3D/0": entry()})

    assert engine_metrics(collector)["igpu_engines_render_3d_0_busy"] == 1.5


def test_compute_is_a_known_class():
    metrics = scrape(collect_engines({"Compute/0": entry()}))

    assert metrics["igpu_engines_compute_0_busy"] == 1.5
    assert metrics[WARNINGS] == 0


def test_second_video_engine_from_capture():
    for sample in load_samples(FIXTURES / "physical.json"):
        collector = SampleCollector()
        collector.update(sample)

        metrics = scrape(collector)

        assert metrics["igpu_engines_video_1_busy"] == 0
        assert "igpu_imc_bandwidth_reads" not in metrics
        assert metrics[WARNINGS] == 0


def test_unknown_class_is_exported_and_warned(caplog):
    metrics = scrape(collect_engines({"[unknown]/0": entry()}))

    assert metrics["igpu_engines_unknown_0_busy"] == 1.5
    assert metrics[WARNINGS] == 1
    assert "[unknown]/0" in caplog.text


def test_each_unknown_instance_warns():
    collector = collect_engines({"Foo/0": entry(), "Foo/1": entry()})

    assert scrape(collector)[WARNINGS] == 2


def test_key_without_instance_warns_and_exports_nothing(caplog):
    collector = collect_engines({"Video": entry()})

    assert engine_metrics(collector) == {}
    assert scrape(collector)[WARNINGS] == 1
    assert "-p" in caplog.text


def test_class_capture_exports_no_engines():
    collector = SampleCollector()
    for sample in load_samples(FIXTURES / "class.json"):
        collector.update(sample)

    metrics = scrape(collector)

    assert engine_metrics(collector) == {}
    assert metrics[WARNINGS] == 4
    assert {"igpu_rc6", "igpu_period", "igpu_power_package"} <= set(metrics)


def test_empty_class_name_warns():
    collector = collect_engines({"[]/0": entry()})

    assert engine_metrics(collector) == {}
    assert scrape(collector)[WARNINGS] == 1


def test_collision_keeps_first_and_warns():
    collector = collect_engines({"Foo-Bar/0": entry(2.5), "Foo_Bar/0": entry(7.5)})

    metrics = scrape(collector)

    assert metrics["igpu_engines_foo_bar_0_busy"] == 2.5
    assert metrics[WARNINGS] == 2


def test_known_class_wins_collision_regardless_of_order():
    collector = collect_engines(
        {"Video-Enhance/0": entry(9.0), "VideoEnhance/0": entry()}
    )

    assert scrape(collector)["igpu_engines_video_enhance_0_busy"] == 1.5


def test_broken_known_entry_still_holds_its_name_against_unknown_class():
    collector = collect_engines({"Video/0": 5, "Video-/0": entry(9.0)})

    assert engine_metrics(collector) == {}


def test_missing_engine_field_warns(caplog):
    engine = entry()
    del engine["sema"]

    metrics = scrape(collect_engines({"Video/0": engine}))

    assert metrics["igpu_engines_video_0_busy"] == 1.5
    assert metrics["igpu_engines_video_0_wait"] == 0
    assert "igpu_engines_video_0_sema" not in metrics
    assert metrics[WARNINGS] == 1
    assert "engines.Video/0.sema" in caplog.text


def test_engine_entry_wrong_shape():
    collector = collect_engines({"Video/0": 5})

    assert engine_metrics(collector) == {}
    assert scrape(collector)[WARNINGS] == 1


def test_empty_engines_section_is_silent():
    collector = collect_engines({})

    assert engine_metrics(collector) == {}
    assert scrape(collector)[WARNINGS] == 0


def test_non_ascii_class_exports_valid_name():
    metrics = scrape(collect_engines({"Föö/0": entry()}))

    assert metrics[WARNINGS] == 1
    assert len(metrics) > 1
    assert all(re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", name) for name in metrics)


def test_non_ascii_instance_is_not_an_instance():
    collector = collect_engines({"Video/٣": entry()})

    assert engine_metrics(collector) == {}
    assert scrape(collector)[WARNINGS] == 1


def test_help_text_keeps_existing_wording():
    collector = collect_engines({"VideoEnhance/0": entry(), "Render/3D/0": entry()})

    text = exposition(collector)

    assert "Video Enhance 0 busy utilisation %" in text
    assert "Render 3D 0 busy utilisation %" in text
