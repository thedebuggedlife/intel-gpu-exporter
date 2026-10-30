# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Prometheus exporter for Intel GPUs. All the code is in `intel-gpu-exporter.py`: it runs `intel_gpu_top -J` as a subprocess, parses the JSON stream from its stdout, and serves the latest sample as gauges on port 8080 (hardcoded).

`origin` is the `thedebuggedlife` fork. `upstream` (`onedr0p`) is marked unmaintained in the README, so don't expect changes to flow from there.

## Commands

There is no test suite, linter, or PR CI. `tests/data.json` is a captured sample that nothing runs automatically, and it is excluded from the image by `.dockerignore`.

`intel_gpu_top` needs Linux and an Intel GPU, so on any other machine the sample-replay check below is the only way to exercise the code. It covers `update()` but not the stream parsing in `__main__`.

```bash
# Keep the venv outside the repo: there is no .gitignore, and the Dockerfile's `COPY . .` would pick it up
python3 -m venv /tmp/igpu-venv
/tmp/igpu-venv/bin/pip install -r requirements.txt

# Replay the first captured sample through update() and print the resulting metrics
/tmp/igpu-venv/bin/python -c "
import importlib.util, json
from prometheus_client import generate_latest
spec = importlib.util.spec_from_file_location('exporter', 'intel-gpu-exporter.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)
exporter.update(json.JSONDecoder().raw_decode(open('tests/data.json').read())[0])
print(generate_latest().decode())
"

# Build the image (CI only publishes linux/amd64)
docker build --platform linux/amd64 -t intel-gpu-exporter .

# Run on a host with an Intel GPU; privileged and host PID are required
docker run --rm --privileged --pid=host -p 8080:8080 -v /dev/dri:/dev/dri intel-gpu-exporter
```

## Architecture

**Metrics are declared by hand.** Each one is a module-level `Gauge` plus a matching `.set()` in `update()`. Adding or renaming a metric also means updating the README's Metrics section and `examples/simple-grafana-dash.json`, which references every metric by name.

**`update()` reports missing fields as 0**, so a zero can mean either "idle" or "not in the data". Only instance 0 of each engine is exported; the sample's `Video/1` is silently dropped.

**There are two stream parsers, selected by `IS_DOCKER`.** The Dockerfile sets it, so the image always uses the first one:
- Docker path: strips each line and retries `json.loads` on the accumulated buffer until it parses.
- Native path: buffers lines until one is exactly `},\n`, then parses the buffer without that trailing comma. This relies on `intel_gpu_top`'s indentation, since nested closing braces are tab-indented and don't match.

Both expect the shape in `tests/data.json`: objects separated by `},` with no enclosing array. A leading `[` breaks both: the Docker path never parses anything and leaves every metric at 0, and the native path raises `JSONDecodeError`. The image's `intel_gpu_top` 2.0 binary contains a `[\n` string, so check this first if metrics stay at 0. It hasn't been confirmed against real GPU output.

## Configuration

Set through environment variables, none of which are documented in the README:

| Variable | Effect |
| --- | --- |
| `REFRESH_PERIOD_MS` | `intel_gpu_top -s` sample period, default `10000`. Keep Prometheus's `scrape_interval` in line with it. |
| `DEVICE` | Passed to `intel_gpu_top -d` as a device filter; omitted if unset. |
| `DEBUG` | Enables debug logging. Any non-empty value counts, including `false` and `0`. |
| `IS_DOCKER` | Selects the parser, with the same truthiness as `DEBUG`. |

## Release

Pushes publish images to `ghcr.io/<repo>`: `main` to `:rolling` and `dev` to `:rolling-dev`. There are no versioned tags. Renovate manages the `prometheus_client` pin, the Python base image, and the GitHub Actions versions.
