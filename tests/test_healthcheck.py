import socket
import subprocess
import sys
import threading

import pytest
from helpers import REPO, WAIT_LIMIT

STREAM = [["samples", None]]
UNREACHABLE_PROXY = "http://127.0.0.1:1"


def probe(env, port):
    return subprocess.run(
        [sys.executable, "-m", "intel_gpu_exporter.healthcheck"],
        check=False,
        cwd=REPO,
        env={**env, "LISTEN_PORT": str(port)},
        capture_output=True,
        text=True,
        timeout=WAIT_LIMIT,
    )


@pytest.fixture
def respond_with():
    with socket.create_server(("127.0.0.1", 0)) as server:

        def start(response):
            def answer():
                connection, _ = server.accept()
                with connection:
                    connection.recv(4096)
                    connection.sendall(response)

            threading.Thread(target=answer, daemon=True).start()
            return server.getsockname()[1]

        yield start


def test_healthy_while_samples_are_current(fake_gpu, start_exporter, exporter_env):
    fake_gpu.play(STREAM)
    exporter = start_exporter()
    exporter.wait_for_metrics(igpu_up=1)

    assert probe(exporter_env, exporter.port).returncode == 0


def test_unhealthy_without_a_sample(fake_gpu, start_exporter, exporter_env):
    fake_gpu.play([["hang"]])
    exporter = start_exporter()
    exporter.wait_for_metrics(igpu_up=0)

    assert probe(exporter_env, exporter.port).returncode == 1


def test_unhealthy_when_nothing_listens(exporter_env):
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]

    result = probe(exporter_env, port)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_a_proxy_in_the_environment_is_not_used(fake_gpu, start_exporter, exporter_env):
    fake_gpu.play(STREAM)
    exporter = start_exporter()
    exporter.wait_for_metrics(igpu_up=1)
    env = {**exporter_env, "http_proxy": UNREACHABLE_PROXY}

    assert probe(env, exporter.port).returncode == 0


def test_invalid_port_fails_with_the_config_error(exporter_env):
    result = probe(exporter_env, "nonsense")

    assert result.returncode == 1
    assert "LISTEN_PORT" in result.stderr
    assert "Traceback" not in result.stderr


def test_unhealthy_on_an_error_status(exporter_env, respond_with):
    port = respond_with(
        b"HTTP/1.1 503 Unavailable\r\nContent-Length: 12\r\n\r\nigpu_up 1.0\n"
    )

    assert probe(exporter_env, port).returncode == 1


def test_only_the_igpu_up_line_counts(exporter_env, respond_with):
    body = b"# igpu_up 1.0\nnot_igpu_up 1.0\nigpu_up 0.0\n"
    head = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body)

    assert probe(exporter_env, respond_with(head + body)).returncode == 1


def test_unhealthy_when_the_listener_is_not_http(exporter_env, respond_with):
    result = probe(exporter_env, respond_with(b"not http\r\n\r\n"))

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
