import json
from pathlib import Path

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"
DATA_JSON = TESTS / "data.json"


def load_samples(path):
    text = path.read_text()
    decoder = json.JSONDecoder()
    samples = []
    position = 0
    while True:
        while position < len(text) and text[position] in " \t\r\n,[]":
            position += 1
        if position == len(text):
            return samples
        sample, position = decoder.raw_decode(text, position)
        samples.append(sample)


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
