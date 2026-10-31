import json
import re

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


def loaded_capture():
    return (FIXTURES / "loaded.json").read_bytes()


def test_decodes_a_capture_with_mixed_framing():
    stream = loaded_capture()

    assert b"},\n{" in stream
    assert b"}\n{" in stream
    assert stream.endswith(b"}")
    assert len(decode(stream)) == 15


def test_mixed_framing_capture_bytewise():
    stream = loaded_capture()
    bytewise = decoder()

    samples = [
        value for i in range(len(stream)) for value in bytewise.feed(stream[i : i + 1])
    ]

    assert samples == decode(stream)


@pytest.mark.parametrize(
    ("marker", "offset"),
    [
        pytest.param(b"},\n{", 1, id="after-a-closing-brace-before-its-comma"),
        pytest.param(b"},\n{", 2, id="after-a-comma"),
        pytest.param(b"}\n{", 1, id="after-a-closing-brace-with-no-comma"),
        pytest.param(b"}\n{", 2, id="between-samples-with-no-comma"),
        pytest.param(b'"engine-classes"', 7, id="inside-a-key"),
        pytest.param(b'"4292774542"', 5, id="inside-a-client-id"),
        pytest.param(b'"ffmpeg"', 3, id="inside-a-client-name"),
        pytest.param(b"59.521598", 3, id="inside-a-number"),
    ],
)
def test_mixed_framing_capture_split_at_every_awkward_point(marker, offset):
    stream = loaded_capture()
    expected = decode(stream)
    points = [m.start() + offset for m in re.finditer(re.escape(marker), stream)]
    assert points
    pieces = [
        stream[start:end]
        for start, end in zip([0, *points], [*points, len(stream)], strict=True)
    ]

    split = decoder()

    assert [value for piece in pieces for value in split.feed(piece)] == expected


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
