import json
import re

from helpers import DATA_JSON, FIXTURES, REPO, load_samples, scrape

from intel_gpu_exporter.metrics import SampleCollector

README = (REPO / "README.md").read_text()
PROMETHEUS_EXAMPLE = (REPO / "examples" / "prometheus.md").read_text()
DASHBOARD = json.loads((REPO / "examples" / "simple-grafana-dash.json").read_text())
METRIC = re.compile(r"igpu_[a-z0-9_]+")
ENGINE_SERIES = re.compile(r"igpu_engines_[a-z0-9_]+_[0-9]+_(busy|sema|wait)")
CAPTURES = (
    FIXTURES / "physical.json",
    FIXTURES / "loaded.json",
    FIXTURES / "handwritten-multi-gt.json",
    DATA_JSON,
)


def served_metrics():
    samples = [sample for path in CAPTURES for sample in load_samples(path)]
    # No capture has a compute engine
    samples.append({"engines": {"Compute/0": {"busy": 0, "sema": 0, "wait": 0}}})

    served = set()
    for sample in samples:
        collector = SampleCollector()
        collector.update(sample)
        served |= scrape(collector).keys()
    return served


SERVED = served_metrics()


def is_served(name):
    # A trailing _ is a name cut short by a wildcard or placeholder, such as igpu_gt_*
    if name.endswith("_"):
        return any(metric.startswith(name) for metric in SERVED)
    return name in SERVED


def test_every_served_metric_is_in_the_readme():
    documented = {
        f"igpu_engines_<class>_<instance>_{match[1]}" if match else name
        for name, match in ((name, ENGINE_SERIES.fullmatch(name)) for name in SERVED)
    }

    assert {name for name in documented if f"`{name}" not in README} == set()


def test_every_metric_the_docs_name_is_served():
    named = METRIC.findall(README) + METRIC.findall(PROMETHEUS_EXAMPLE)

    assert {name for name in named if not is_served(name)} == set()


def test_every_metric_the_dashboard_uses_is_served():
    used = METRIC.findall(json.dumps(DASHBOARD))

    assert used
    assert set(used) <= SERVED


def test_dashboard_panels_have_unique_ids():
    def ids(panels):
        for panel in panels:
            yield panel["id"]
            yield from ids(panel.get("panels", []))

    found = list(ids(DASHBOARD["panels"]))

    assert len(found) == len(set(found))


def test_dashboard_saves_no_instance_of_its_own():
    assert all(not v.get("current") for v in DASHBOARD["templating"]["list"])
