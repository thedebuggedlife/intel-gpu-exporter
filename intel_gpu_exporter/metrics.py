import logging
import re
import time
from collections import Counter

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

log = logging.getLogger(__name__)

ENGINE_KEY = re.compile(r"(.*)/([0-9]+)")
ENGINE_FIELDS = ("busy", "sema", "wait")
GT_SECTION = re.compile(r"(frequency|rc6)-gt([0-9]+)")
KNOWN_CLASSES = frozenset({"Render/3D", "Blitter", "Video", "VideoEnhance", "Compute"})

SECTION_METRICS = {
    "period": {"duration": ("igpu_period", "Period ms")},
    "frequency": {
        "actual": ("igpu_frequency_actual", "Frequency actual MHz"),
        "requested": ("igpu_frequency_requested", "Frequency requested MHz"),
    },
    "interrupts": {"count": ("igpu_interrupts", "Interrupts/s")},
    "rc6": {"value": ("igpu_rc6", "RC6 %")},
    "power": {
        "GPU": ("igpu_power_gpu", "GPU power W"),
        "Package": ("igpu_power_package", "Package power W"),
    },
    "imc-bandwidth": {
        "reads": ("igpu_imc_bandwidth_reads", "IMC reads MiB/s"),
        "writes": ("igpu_imc_bandwidth_writes", "IMC writes MiB/s"),
    },
}
OPTIONAL_SECTIONS = frozenset({"power", "imc-bandwidth", "clients"})
DEFAULT_TIMEOUT = 30.0


def sanitise_class(name):
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name).lower()
    return re.sub(r"[^a-z0-9]+", "_", words).strip("_")


class SampleCollector:
    def __init__(self, timeout=DEFAULT_TIMEOUT, clock=time.monotonic):
        self._timeout = timeout
        self._clock = clock
        self._snapshot = ({}, {})
        self._arrival = None
        self._restarts = 0
        self._warned = set()
        self._noted_absent = set()
        self._client_names = set()

    def update(self, sample):
        if not isinstance(sample, dict):
            self.warn("sample", f"Sample isn't an object: {sample!r}")
            self._snapshot = ({}, {})
            return

        gauges, labelled = {}, {}
        per_gt = self._gt_sections(sample, gauges, labelled)
        for section, fields in SECTION_METRICS.items():
            if section in per_gt:
                continue
            values = self._section(sample, section)
            if values is None:
                continue
            for name, help_text, value in self._fields(values, section, fields):
                gauges[name] = (help_text, value)

        engines = self._section(sample, "engines")
        if engines is not None:
            self._engines(engines, gauges)

        clients = self._section(sample, "clients")
        if clients is not None:
            self._clients(clients, labelled)

        self._snapshot = (gauges, labelled)
        self._arrival = (self._clock(), time.time())

    @property
    def last_sample(self):
        return self._arrival[0] if self._arrival else None

    def count_restart(self):
        self._restarts += 1

    def collect(self):
        arrival = self._arrival
        up = arrival is not None and self._clock() - arrival[0] <= self._timeout
        if up:
            gauges, labelled = self._snapshot
            for name, (help_text, value) in gauges.items():
                yield GaugeMetricFamily(name, help_text, value=value)
            for name, (help_text, label, values) in labelled.items():
                family = GaugeMetricFamily(name, help_text, labels=[label])
                for label_value, value in values.items():
                    family.add_metric([label_value], value)
                yield family
        yield GaugeMetricFamily(
            "igpu_up", "1 if a sample arrived within the timeout", value=int(up)
        )
        if arrival is not None:
            yield GaugeMetricFamily(
                "igpu_last_sample_timestamp_seconds",
                "Unix time the last sample arrived",
                value=arrival[1],
            )
        yield CounterMetricFamily(
            "igpu_collector_restarts",
            "intel_gpu_top restarts since start",
            value=self._restarts,
        )
        yield CounterMetricFamily(
            "igpu_parse_warnings",
            "Distinct parse warnings logged since start",
            value=len(self._warned),
        )

    def describe(self):
        return []

    def _gt_sections(self, sample, gauges, labelled):
        sections = set()
        for key in sample:
            match = GT_SECTION.fullmatch(key)
            if match is None:
                continue

            section, gt = match.groups()
            sections.add(section)
            values = self._section(sample, key)
            if values is None:
                continue

            fields = SECTION_METRICS[section]
            for name, help_text, value in self._fields(values, key, fields):
                if gt == "0":
                    gauges[name] = (help_text, value)
                family = name.replace("igpu_", "igpu_gt_", 1)
                labelled.setdefault(family, (f"{help_text} by GT", "gt", {}))
                labelled[family][2][gt] = value
        return sections

    def _engines(self, engines, gauges):
        prefixes = set()
        # Known classes go first so an unknown class can't displace a contract metric
        for key in sorted(
            engines, key=lambda k: k.rpartition("/")[0] not in KNOWN_CLASSES
        ):
            match = ENGINE_KEY.fullmatch(key)
            if match is None:
                self.warn(
                    key,
                    f"Engine key {key!r} has no instance suffix; "
                    "is intel_gpu_top running without -p?",
                )
                continue

            raw_class, instance = match.groups()
            engine_class = sanitise_class(raw_class)
            if not engine_class:
                self.warn(key, f"Engine key {key!r} has no usable class name")
                continue

            prefix = f"igpu_engines_{engine_class}_{instance}"
            if prefix in prefixes:
                self.warn(key, f"Engine key {key!r} collides with another key")
                continue
            prefixes.add(prefix)

            values = engines[key]
            if not isinstance(values, dict):
                self.warn(f"engines.{key}", f"Engine {key!r} isn't an object")
                continue

            if raw_class not in KNOWN_CLASSES:
                self.warn(key, f"Unknown engine class in {key!r}; exporting {prefix}")

            label = engine_class.replace("_", " ").title()
            for field in ENGINE_FIELDS:
                value = self._number(values, field, f"engines.{key}.{field}")
                if value is not None:
                    help_text = f"{label} {instance} {field} utilisation %"
                    gauges[f"{prefix}_{field}"] = (help_text, value)

    def _clients(self, clients, labelled):
        counts = Counter()
        for client_id, entry in clients.items():
            key = f"clients.{client_id}"
            if not isinstance(entry, dict):
                self.warn(key, f"Expected an object for {key!r}, got {entry!r}")
                continue

            name = entry.get("name")
            if isinstance(name, str) and name:
                # A lone surrogate from a JSON escape can't be encoded by a scrape
                counts[name.encode(errors="backslashreplace").decode()] += 1
            else:
                name_key = f"{key}.name"
                self.warn(name_key, f"Expected a name for {name_key!r}, got {name!r}")

        self._client_names.update(counts)
        if self._client_names:
            values = {name: float(counts[name]) for name in self._client_names}
            labelled["igpu_clients_count"] = (
                "Processes with this name using the GPU",
                "name",
                values,
            )

    def _section(self, sample, name):
        if name not in sample:
            if name not in OPTIONAL_SECTIONS:
                self.warn(name, f"Expected section {name!r} is missing")
            elif name not in self._noted_absent:
                self._noted_absent.add(name)
                log.info("Section %r is absent from intel_gpu_top output", name)
            return None

        section = sample[name]
        if isinstance(section, dict):
            return section
        self.warn(name, f"Section {name!r} isn't an object: {section!r}")
        return None

    def _fields(self, values, section, fields):
        for field, (name, help_text) in fields.items():
            value = self._number(values, field, f"{section}.{field}")
            if value is not None:
                yield name, help_text, value

    def _number(self, values, field, key):
        value = values.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            try:
                return float(value)
            except OverflowError:
                pass
        self.warn(key, f"Expected a number for {key!r}, got {value!r}")
        return None

    def warn(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)
