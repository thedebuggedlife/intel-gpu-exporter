import dataclasses

import pytest

from intel_gpu_exporter.config import Config, ConfigError, load_config

TOO_MANY_DIGITS_FOR_A_FLOAT = "9" * 400


def test_defaults():
    assert load_config({}) == Config(
        listen_port=8080, refresh_period_ms=10000, device=None, debug=False
    )


def test_values_from_the_environment():
    config = load_config(
        {
            "LISTEN_PORT": "9100",
            "REFRESH_PERIOD_MS": "250",
            "DEVICE": "card0",
            "DEBUG": "1",
        }
    )

    assert config == Config(
        listen_port=9100, refresh_period_ms=250, device="card0", debug=True
    )


def test_config_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        load_config({}).listen_port = 1


def test_empty_device_counts_as_unset():
    assert load_config({"DEVICE": ""}).device is None


@pytest.mark.parametrize(
    "value", ["", "  ", "false", "FALSE", " False ", "0", " 0 ", "no", "NO"]
)
def test_debug_off_values(value):
    assert load_config({"DEBUG": value}).debug is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "off", "anything"])
def test_debug_on_values(value):
    assert load_config({"DEBUG": value}).debug is True


@pytest.mark.parametrize("value", ["1", "65535"])
def test_port_limits_are_accepted(value):
    assert load_config({"LISTEN_PORT": value}).listen_port == int(value)


@pytest.mark.parametrize("value", ["1", "2147483"])
def test_period_limits_are_accepted(value):
    assert load_config({"REFRESH_PERIOD_MS": value}).refresh_period_ms == int(value)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("LISTEN_PORT", "0"),
        ("LISTEN_PORT", "65536"),
        ("LISTEN_PORT", "-1"),
        ("LISTEN_PORT", "abc"),
        ("LISTEN_PORT", ""),
        ("LISTEN_PORT", "80.5"),
        ("REFRESH_PERIOD_MS", "0"),
        ("REFRESH_PERIOD_MS", "-5"),
        ("REFRESH_PERIOD_MS", "often"),
        ("REFRESH_PERIOD_MS", ""),
        ("REFRESH_PERIOD_MS", "1e3"),
        ("REFRESH_PERIOD_MS", "2147484"),
        ("REFRESH_PERIOD_MS", TOO_MANY_DIGITS_FOR_A_FLOAT),
    ],
)
def test_invalid_number_names_the_variable_and_its_value(variable, value):
    with pytest.raises(ConfigError) as error:
        load_config({variable: value})

    message = str(error.value)
    assert variable in message
    assert repr(value) in message
    assert "\n" not in message
