import os
from dataclasses import dataclass

MAX_PORT = 65535
# intel_gpu_top multiplies -s by 1000 into a C int
MAX_PERIOD_MS = 2_147_483
DEBUG_OFF = frozenset({"", "false", "0", "no"})


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    listen_port: int
    refresh_period_ms: int
    device: str | None
    debug: bool


def load_config(environ=os.environ):
    return Config(
        listen_port=_integer(environ, "LISTEN_PORT", 8080, MAX_PORT),
        refresh_period_ms=_integer(environ, "REFRESH_PERIOD_MS", 10000, MAX_PERIOD_MS),
        device=environ.get("DEVICE") or None,
        debug=environ.get("DEBUG", "").strip().lower() not in DEBUG_OFF,
    )


def _integer(environ, name, default, maximum):
    if name not in environ:
        return default
    raw = environ[name]
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not 1 <= value <= maximum:
        raise ConfigError(f"{name} must be an integer from 1 to {maximum}, got {raw!r}")
    return value
