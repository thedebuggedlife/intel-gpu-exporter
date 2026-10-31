# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Prometheus exporter for Intel GPUs. The code is the `intel_gpu_exporter` package, run with `python -m intel_gpu_exporter`: it runs `intel_gpu_top -J` as a subprocess, parses the JSON stream from its stdout, and serves the latest sample as gauges on port 8080 (hardcoded). `docs/specs/2026-10-30-exporter.md` is the design for the exporter, including work not done yet.

`origin` is the `thedebuggedlife` fork. `upstream` (`onedr0p`) is marked unmaintained in the README, so don't expect changes to flow from there.

## Commands

`.github/workflows/ci.yaml` runs `ruff` and `pytest` on Python 3.13, matching the `Dockerfile`, on every PR and on pushes to `main`. Its check is named `lint-and-test`. Every job declares explicit `permissions:` because default tokens are read-only, and every Action is pinned by full commit SHA with the version in a comment.

Dev tools are pinned in `requirements-dev.txt`, apart from the runtime `requirements.txt`. Install the two files with separate `pip` commands and don't `-r` one from the other: hash-locking the runtime file would make pip reject the unhashed dev pins.

`pyproject.toml` holds the `ruff` and `pytest` settings. `tests/fixtures/` holds captures of current `intel_gpu_top` output, and its README records where each one came from. `tests/data.json` is an older capture and the only one with `imc-bandwidth`. `tests/test_contract.py` lists every contract metric by hand, so keep it independent of the code. `.dockerignore` excludes `tests/` from the image.

`intel_gpu_top` needs Linux and an Intel GPU, so on any other machine the tests that replay captures are the only way to exercise the code. They cover `SampleCollector` but not the stream handling in `stream.py`.

```bash
# Paseo runs these three when it creates a worktree. uv is required: it provides CI's Python, as pytest needs a newer one than some systems ship
uv venv --clear --seed --python 3.13 .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt

# The same checks CI runs
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest

# Build the image (linux/amd64 is the only target)
docker build --platform linux/amd64 -t intel-gpu-exporter .

# Run on a host with an Intel GPU; privileged and host PID are required
docker run --rm --privileged --pid=host -p 8080:8080 -v /dev/dri:/dev/dri intel-gpu-exporter
```

## Architecture

**Metrics are built from the latest sample, not declared as gauges.** `SampleCollector` in `metrics.py` turns each sample into a new snapshot in `update()`, and `collect()` renders that snapshot on each scrape. Don't add module-level `Gauge`s: an unlabelled one exports 0 from the moment it is registered. Section metrics are listed in `SECTION_METRICS`, and engine metrics are derived from each `Name/N` key, so every instance is exported. Adding or renaming a metric also means updating the README's Metrics section and `examples/simple-grafana-dash.json`, which references every metric by name.

**Missing data is a gap, never a 0.** A value that isn't in the sample produces no series, and a real 0 stays 0. Unknown input logs one warning per distinct key and raises `igpu_parse_warnings_total`, which must stay at 0 on healthy hardware. So `power`, `imc-bandwidth` and `clients`, which some hardware legitimately omits, log at INFO instead. `update()` must not raise on sample content.

**There are two stream parsers in `stream.py`, selected by `IS_DOCKER`.** The Dockerfile sets it, so the image always uses the first one:
- Docker path: strips each line and retries `json.loads` on the accumulated buffer until it parses.
- Native path: buffers lines until one is exactly `},\n`, then parses the buffer without that trailing comma. This relies on `intel_gpu_top`'s indentation, since nested closing braces are tab-indented and don't match.

Both expect the shape in `tests/data.json`: objects separated by `},` with no enclosing array. The `intel_gpu_top` 2.3 output in `tests/fixtures/` has a leading `[` and no commas, which breaks both: neither path ever parses a sample, so no sample metrics appear. Neither passes `-p` yet, so a parsed 2.x sample would have no instance suffixes, which warn and export no engine series. The image's `intel_gpu_top` 2.0 binary contains a `[\n` string, so check this first if sample metrics are missing.

## Configuration

Set through environment variables, none of which are documented in the README:

| Variable | Effect |
| --- | --- |
| `REFRESH_PERIOD_MS` | `intel_gpu_top -s` sample period, default `10000`. Keep Prometheus's `scrape_interval` in line with it. |
| `DEVICE` | Passed to `intel_gpu_top -d` as a device filter; omitted if unset. |
| `DEBUG` | Enables debug logging. Any non-empty value counts, including `false` and `0`. |
| `IS_DOCKER` | Selects the parser, with the same truthiness as `DEBUG`. |

## Release

Nothing publishes images: no workflow pushes to `ghcr.io`, and there are no versioned tags. Nothing updates dependencies automatically either, so the `prometheus_client` pin, the dev tool pins, the Python base image and the Action SHAs only change by hand.

## Agent workflow (superpowers + Paseo)

These are declared preferences; they override the superpowers skills' defaults.

- **Worktrees are owned by Paseo** (`paseo.json` creates `.venv` from `requirements.txt` and `requirements-dev.txt` on creation; they live under `~/.paseo/worktrees/`). If already in a linked worktree, work there. Never use `EnterWorktree` or `git worktree add`. From the main checkout, create a Paseo worktree (`paseo worktree create --mode branch-off --new-branch <name>`, or `mcp__paseo__create_workspace`) — the main checkout may be shared with other agents. Don't bump the `requirements.txt` pin unless that is the task; if dependencies are missing, run `.venv/bin/pip install -r requirements.txt` and `.venv/bin/pip install -r requirements-dev.txt`.
- **Baseline / verification** is `.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest`, the same checks as CI and Paseo's `test` script. Run `docker build` only when changing the `Dockerfile`, `.dockerignore` or `requirements.txt`.
- **Image contents:** the Dockerfile's `COPY . .` takes everything `.dockerignore` doesn't exclude, so add new non-runtime files there.
- **Finishing a branch:** always push and open a PR against `origin` (`thedebuggedlife`), never `upstream`; never merge into `main` locally. Archive the worktree through Paseo, then delete the merged branch. If a worktree was removed outside Paseo, run `scripts/lumen-prune.sh`.
- **Specs** go in `docs/specs/YYYY-MM-DD-<feature>.md`, committed as `docs:` on the feature branch.
- **Plans** and other agent workflow artifacts **never** get committed into the tree.
