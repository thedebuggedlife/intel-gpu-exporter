from helpers import DATA_JSON, exposition, load_samples, scrape
from prometheus_client.parser import text_string_to_metric_families

from intel_gpu_exporter.metrics import SampleCollector

CONTRACT_METRICS = [
    "igpu_engines_blitter_0_busy",
    "igpu_engines_blitter_0_sema",
    "igpu_engines_blitter_0_wait",
    "igpu_engines_render_3d_0_busy",
    "igpu_engines_render_3d_0_sema",
    "igpu_engines_render_3d_0_wait",
    "igpu_engines_video_0_busy",
    "igpu_engines_video_0_sema",
    "igpu_engines_video_0_wait",
    "igpu_engines_video_enhance_0_busy",
    "igpu_engines_video_enhance_0_sema",
    "igpu_engines_video_enhance_0_wait",
    "igpu_frequency_actual",
    "igpu_frequency_requested",
    "igpu_imc_bandwidth_reads",
    "igpu_imc_bandwidth_writes",
    "igpu_interrupts",
    "igpu_period",
    "igpu_power_gpu",
    "igpu_power_package",
    "igpu_rc6",
]


def collector_with_full_sample():
    collector = SampleCollector()
    collector.update(load_samples(DATA_JSON)[0])
    return collector


def test_contract_metrics_present_as_gauges():
    families = text_string_to_metric_families(exposition(collector_with_full_sample()))

    gauges = {family.name for family in families if family.type == "gauge"}

    assert not set(CONTRACT_METRICS) - gauges


def test_full_sample_raises_no_warnings():
    assert scrape(collector_with_full_sample())["igpu_parse_warnings_total"] == 0
