import logging
import re

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

log = logging.getLogger(__name__)

ENGINE_KEY = re.compile(r"(.*)/([0-9]+)")
ENGINE_FIELDS = ("busy", "sema", "wait")
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


def sanitise_class(name):
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name).lower()
    return re.sub(r"[^a-z0-9]+", "_", words).strip("_")


class SampleCollector:
    def __init__(self):
        self._snapshot = {}
        self._warned = set()
        self._noted_absent = set()

    def update(self, sample):
        if not isinstance(sample, dict):
            self._warn("sample", f"Ignoring a sample that isn't an object: {sample!r}")
            return

        snapshot = {}
        for section, fields in SECTION_METRICS.items():
            values = self._section(sample, section)
            if values is None:
                continue
            for field, (name, help_text) in fields.items():
                value = self._number(values, field, f"{section}.{field}")
                if value is not None:
                    snapshot[name] = (help_text, value)

        engines = self._section(sample, "engines")
        if engines is not None:
            self._engines(engines, snapshot)

        self._section(sample, "clients")

        self._snapshot = snapshot

    def collect(self):
        for name, (help_text, value) in self._snapshot.items():
            yield GaugeMetricFamily(name, help_text, value=value)
        yield CounterMetricFamily(
            "igpu_parse_warnings",
            "Distinct parse warnings logged since start",
            value=len(self._warned),
        )

    def describe(self):
        return []

    def _engines(self, engines, snapshot):
        prefixes = set()
        # Known classes go first so an unknown class can't displace a contract metric
        for key in sorted(
            engines, key=lambda k: k.rpartition("/")[0] not in KNOWN_CLASSES
        ):
            match = ENGINE_KEY.fullmatch(key)
            if match is None:
                self._warn(
                    key,
                    f"Engine key {key!r} has no instance suffix; "
                    "is intel_gpu_top running without -p?",
                )
                continue

            raw_class, instance = match.groups()
            engine_class = sanitise_class(raw_class)
            if not engine_class:
                self._warn(key, f"Engine key {key!r} has no usable class name")
                continue

            values = engines[key]
            if not isinstance(values, dict):
                self._warn(f"engines.{key}", f"Engine {key!r} isn't an object")
                continue

            prefix = f"igpu_engines_{engine_class}_{instance}"
            if prefix in prefixes:
                self._warn(key, f"Engine key {key!r} collides with another key")
                continue
            prefixes.add(prefix)

            if raw_class not in KNOWN_CLASSES:
                self._warn(key, f"Unknown engine class in {key!r}; exporting {prefix}")

            label = engine_class.replace("_", " ").title()
            for field in ENGINE_FIELDS:
                value = self._number(values, field, f"engines.{key}.{field}")
                if value is not None:
                    help_text = f"{label} {instance} {field} utilisation %"
                    snapshot[f"{prefix}_{field}"] = (help_text, value)

    def _section(self, sample, name):
        if name not in sample:
            if name not in OPTIONAL_SECTIONS:
                self._warn(name, f"Expected section {name!r} is missing")
            elif name not in self._noted_absent:
                self._noted_absent.add(name)
                log.info("Section %r is absent from intel_gpu_top output", name)
            return None

        section = sample[name]
        if isinstance(section, dict):
            return section
        self._warn(name, f"Section {name!r} isn't an object: {section!r}")
        return None

    def _number(self, values, field, key):
        value = values.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return value
        self._warn(key, f"Expected a number for {key!r}, got {value!r}")
        return None

    def _warn(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)
