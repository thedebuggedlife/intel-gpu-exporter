import json

import pytest
from helpers import DATA_JSON, FIXTURES

from intel_gpu_exporter.stream import StreamDecoder

SAMPLE = b'{"period": {"duration": 1000.0}}'
DECODED = {"period": {"duration": 1000.0}}
MIB = 1024 * 1024


def decoder(warnings=None):
    warnings = [] if warnings is None else warnings
    return StreamDecoder(lambda key, message: warnings.append(key))


def decode(stream):
    return decoder().feed(stream)


def test_decodes_a_capture_with_brackets_and_no_commas():
    samples = decode((FIXTURES / "physical.json").read_bytes())

    assert [sample["period"]["duration"] for sample in samples] == [
        51.296604,
        2027.216537,
        2025.241135,
        2025.311108,
    ]


def test_decodes_a_capture_separated_by_commas():
    samples = decode(DATA_JSON.read_bytes())

    assert samples[0]["rc6"]["value"] == 92.505022


def test_capture_gives_the_same_samples_whatever_the_chunk_size():
    stream = (FIXTURES / "physical.json").read_bytes()
    bytewise = decoder()

    samples = [
        value for i in range(len(stream)) for value in bytewise.feed(stream[i : i + 1])
    ]

    assert samples == decode(stream)


def test_sample_is_returned_at_its_closing_brace():
    stream = decoder()

    assert stream.feed(b"[\n" + SAMPLE[:-1]) == []
    assert stream.feed(b"}") == [DECODED]


def test_separators_between_values_are_skipped():
    assert decode(b" [\n\t" + SAMPLE + b" ,\r\n" + SAMPLE + b"\n]\n") == [
        DECODED,
        DECODED,
    ]


def test_character_split_across_chunks_survives():
    encoded = '{"name": "é"}'.encode()
    split = encoded.index(b"\xc3") + 1
    stream = decoder()

    assert stream.feed(encoded[:split]) == []
    assert stream.feed(encoded[split:]) == [{"name": "é"}]


def test_invalid_bytes_are_replaced():
    assert decode(b'{"name": "\xff"}') == [{"name": "�"}]


@pytest.mark.parametrize(
    ("stream", "expected"),
    [(b"5 ", [5]), (b'"text" ', ["text"]), (b"null ", [None])],
)
def test_top_level_value_that_is_not_an_object_is_returned(stream, expected):
    assert decode(stream) == expected


def test_truncated_stream_waits_for_more_data():
    stream = decoder()

    samples = stream.feed((FIXTURES / "handwritten-truncated.json").read_bytes())

    assert len(samples) == 1
    assert stream.feed(b"") == []


def test_malformed_stream_stops_producing_samples():
    stream = decoder()

    samples = stream.feed((FIXTURES / "handwritten-malformed.json").read_bytes())

    assert len(samples) == 1
    assert stream.feed(SAMPLE) == []


@pytest.mark.parametrize(
    "poison",
    [
        pytest.param(b'{"deep": ' + b"[" * 200_000, id="nesting-too-deep"),
        pytest.param(b'{"huge": ' + b"1" * 10_000 + b"}", id="integer-too-long"),
    ],
)
def test_input_python_refuses_to_decode_does_not_raise(poison):
    assert decode(poison) == []


def test_overflow_drops_the_buffer_and_warns():
    warnings = []
    stream = decoder(warnings)

    stream.feed(b'{"x": "' + b"a" * (MIB + 1))
    assert warnings == ["stream-buffer"]

    assert stream.feed(b"\n" + SAMPLE) == [DECODED]


def test_buffer_up_to_the_limit_does_not_warn():
    warnings = []
    stream = decoder(warnings)
    opening = b'{"x": "'

    assert stream.feed(opening + b"a" * (MIB - len(opening))) == []
    assert len(stream.feed(b'"}')) == 1
    assert warnings == []


def test_buffer_holds_only_the_unfinished_value():
    warnings = []
    stream = decoder(warnings)

    for _ in range(4):
        assert len(stream.feed(json.dumps({"x": "a" * (MIB // 2)}).encode())) == 1

    assert warnings == []
