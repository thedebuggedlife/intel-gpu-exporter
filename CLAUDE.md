# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Prometheus exporter for Intel GPUs. The code is the `intel_gpu_exporter` package, run with `python -m intel_gpu_exporter`: it runs `intel_gpu_top -J -p` as a supervised subprocess, parses the JSON stream from its stdout, and serves the latest sample as gauges on `LISTEN_PORT`, default 8080. `docs/specs/2026-10-30-exporter.md` is the design for the exporter, including work not done yet.

`origin` is the `thedebuggedlife` fork. `upstream` (`onedr0p`) is marked unmaintained in the README, so don't expect changes to flow from there.

## Commands

`.github/workflows/ci.yaml` runs `ruff` and `pytest` on Python 3.13, matching the `Dockerfile`, on every PR and on pushes to `main`. Its check is named `lint-and-test`. Every job declares explicit `permissions:` because default tokens are read-only, and every Action is pinned by full commit SHA with the version in a comment.

Dev tools are pinned in `requirements-dev.txt`, apart from the runtime `requirements.txt`. Install the two files with separate `pip` commands and don't `-r` one from the other: hash-locking the runtime file would make pip reject the unhashed dev pins.

`pyproject.toml` holds the `ruff` and `pytest` settings. `tests/fixtures/` holds captures of current `intel_gpu_top` output, and its README records where each one came from. `loaded.json` is the only one with clients, and its bytes must not change, because its framing is under test. `tests/data.json` is an older capture and the only one with `imc-bandwidth`. `tests/test_contract.py` lists every contract metric by hand and covers the configuration contract, so keep it independent of the code. `.dockerignore` excludes `tests/` from the image.

`intel_gpu_top` needs Linux and an Intel GPU, so tests replay captures or use `tests/fake_intel_gpu_top.py`, which stands in for it on `PATH` and plays a script per spawn. `tests/test_e2e.py` runs the real exporter as a subprocess, using `main(port=0, timing=...)` for an ephemeral port and short timeouts. Those arguments exist for tests and aren't configuration. Teardown kills the exporter's process group, so a test can't leave a child behind. Backoff sequences are asserted in `tests/test_supervisor.py` with no sleeping, because the fake's startup time varies too much to measure them end to end.

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

**Metrics are built from the latest sample, not declared as gauges.** `SampleCollector` in `metrics.py` turns each sample into a new snapshot in `update()`, and `collect()` renders that snapshot on each scrape. Don't add module-level `Gauge`s: an unlabelled one exports 0 from the moment it is registered. Section metrics are listed in `SECTION_METRICS`, and engine metrics are derived from each `Name/N` key, so every instance is exported. Labelled series live beside the plain gauges in the snapshot. On multi-GT output, GT 0 fills the contract metrics and every GT also gets `igpu_gt_*{gt="N"}`. Labels only ever go on new names, because adding one to a contract metric would change its series. Adding or renaming a metric also means updating the README's Metrics section and `examples/simple-grafana-dash.json`, which references metrics by name. `igpu_gt_*` and `igpu_clients_count` are missing from both until the docs PR.

**Missing data is a gap, never a 0.** A value that isn't in the sample produces no series, and a real 0 stays 0. `igpu_clients_count{name}` reports 0 for a name seen earlier only if the sample has a `clients` section, because then the 0 is what the sample says. Unknown input logs one warning per distinct key and raises `igpu_parse_warnings_total`, which must stay at 0 on healthy hardware. So `power`, `imc-bandwidth` and `clients`, which some hardware legitimately omits, log at INFO instead. `update()` must not raise on sample content.

**There is one stream parser, `StreamDecoder` in `stream.py`.** It reads `stdout` in chunks and calls `raw_decode` over a text buffer, skipping whitespace, `[`, `]` and `,` between values. It never reads by line, because a sample's closing `}` has no trailing newline, and a line reader would deliver every sample one period late. Truncated and malformed input look the same to `raw_decode`, so the decoder waits for more data and the watchdog restarts the child. There is no resynchronisation, and each child gets a new decoder.

**`supervisor.py` owns the child.** The watchdog thread runs `Supervisor.run()`, with one `stdout` and one `stderr` reader per child. Any exit, kill or failed spawn goes through the same capped backoff. The stall deadline runs from the later of this child's spawn and the last sample, so a new child always gets a full timeout. `igpu_up` uses the age of the last sample overall, and only an object counts as a sample. Only the current child's reader may write to the collector, which is why `update()` needs no lock.

**While `igpu_up` is 0, only the four health metrics are served,** so a hang shows as a gap rather than a flat line. `SampleCollector` keeps the last sample, and `collect()` decides what to serve at scrape time. That covers labelled series too.

**Signal handlers only record the request.** The main thread checks it in a timed join on the watchdog and sets the stop event itself. A signal taken by another thread doesn't wake an untimed join, and `Event.set()` can deadlock if a second handler interrupts it. Don't simplify either away: both were real hangs, and `tests/test_e2e.py` covers them. If the watchdog ends with no stop requested, the process exits non-zero, so a restart policy that acts on failure can bring it back.

## Configuration

Set through environment variables, none of which are documented in the README. `config.py` reads them once at startup into a frozen dataclass. An invalid number prints one line and exits non-zero before the port is bound or the child is started.

| Variable | Effect |
| --- | --- |
| `LISTEN_PORT` | Port for `/metrics`, from 1 to 65535, default `8080`. |
| `REFRESH_PERIOD_MS` | `intel_gpu_top -s` sample period, from 1 to 2147483, default `10000`. Keep Prometheus's `scrape_interval` in line with it. |
| `DEVICE` | Passed to `intel_gpu_top -d` as a device filter; omitted if unset or empty. |
| `DEBUG` | Enables debug logging. Trimmed and case-insensitive: unset, empty, `false`, `0` and `no` are off, and anything else is on. |

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
