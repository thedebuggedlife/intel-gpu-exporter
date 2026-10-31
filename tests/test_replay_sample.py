from helpers import DATA_JSON, load_samples, scrape

from intel_gpu_exporter.metrics import SampleCollector


def test_replayed_sample_reaches_metrics():
    collector = SampleCollector()

    collector.update(load_samples(DATA_JSON)[0])

    assert scrape(collector)["igpu_rc6"] == 92.505022
