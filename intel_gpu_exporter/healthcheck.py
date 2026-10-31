import http.client
import sys

from intel_gpu_exporter.config import ConfigError, load_config

TIMEOUT_SECONDS = 3


def is_up(port):
    # http.client, because urllib would send this through any proxy in the environment
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_SECONDS)
    try:
        connection.request("GET", "/metrics")
        response = connection.getresponse()
        return response.status == 200 and b"igpu_up 1.0" in response.read().split(b"\n")
    except OSError, http.client.HTTPException:
        return False
    finally:
        connection.close()


def main():
    try:
        port = load_config().listen_port
    except ConfigError as error:
        sys.exit(str(error))
    if not is_up(port):
        sys.exit(f"igpu_up is not 1 on port {port}")


if __name__ == "__main__":
    main()
