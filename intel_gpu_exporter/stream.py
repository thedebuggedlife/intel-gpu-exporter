import codecs
import json

MAX_BUFFER = 1024 * 1024
SEPARATORS = " \t\r\n,[]"


class StreamDecoder:
    def __init__(self, warn):
        self._warn = warn
        self._text = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._json = json.JSONDecoder()
        self._buffer = ""

    def feed(self, chunk):
        self._buffer += self._text.decode(chunk)

        values = []
        position = 0
        while True:
            while position < len(self._buffer) and self._buffer[position] in SEPARATORS:
                position += 1
            try:
                value, position = self._json.raw_decode(self._buffer, position)
            # ValueError covers truncated and malformed input and over-long integers
            except (ValueError, RecursionError):
                break
            values.append(value)
        self._buffer = self._buffer[position:]

        if len(self._buffer) > MAX_BUFFER:
            self._buffer = ""
            self._warn(
                "stream-buffer",
                f"Dropped over {MAX_BUFFER} bytes of undecodable output",
            )
        return values
