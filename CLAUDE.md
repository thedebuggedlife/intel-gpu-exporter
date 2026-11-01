# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Prometheus exporter for Intel GPUs. The code is the `intel_gpu_exporter` package, run with `python -m intel_gpu_exporter`: it runs `intel_gpu_top -J -p` as a supervised subprocess, parses the JSON stream from its stdout, and serves the latest sample as gauges on `LISTEN_PORT`, default 8080. `docs/specs/2026-10-30-exporter.md` is the design for the exporter, including work not done yet.

`origin` is the `thedebuggedlife` fork. `upstream` (`onedr0p`) is marked unmaintained in the README, so don't expect changes to flow from there.

## Commands

`.github/workflows/ci.yaml` runs on every PR and on pushes to `main`, with two checks. `lint-and-test` runs `ruff` and `pytest` on Python 3.14, the version of Alpine's `python3` in the `Dockerfile`, so the two move together. Those steps live in the composite action `.github/actions/lint-and-test`, which the release gate shares. `image-test` builds the image and runs `scripts/test-image.sh`. Every job declares explicit `permissions:` because default tokens are read-only, and every Action is pinned by full commit SHA with the version in a comment. `tests/test_workflows.py` enforces this and the release safety properties by reading the YAML, which is why PyYAML is a dev pin.

Dev tools are pinned in `requirements-dev.txt`, apart from the runtime `requirements.txt`. Install the two files with separate `pip` commands and don't `-r` one from the other: `requirements.txt` is hash-locked, which would make pip reject the unhashed dev pins. It lists the wheel and sdist hashes by hand, with no `.in` file, in a format Dependabot's requirements updater supports. A new required dependency would need its own pin and hashes, and pip fails without them.

`pyproject.toml` holds the `ruff` and `pytest` settings. `tests/fixtures/` holds captures of current `intel_gpu_top` output, and its README records where each one came from. `loaded.json` is the only one with clients, and its bytes must not change, because its framing is under test. `tests/data.json` is an older capture and the only one with `imc-bandwidth`. `tests/test_contract.py` lists every contract metric by hand and covers the configuration contract, so keep it independent of the code. `.dockerignore` denies everything, then allows `intel_gpu_exporter` and `requirements.txt`.

`intel_gpu_top` needs Linux and an Intel GPU, so tests replay captures or use `tests/fake_intel_gpu_top.py`, which stands in for it on `PATH` and plays a script per spawn. `tests/test_e2e.py` runs the real exporter as a subprocess, using `main(port=0, timing=...)` for an ephemeral port and short timeouts. Those arguments exist for tests and aren't configuration. Teardown kills the exporter's process group, so a test can't leave a child behind. Backoff sequences are asserted in `tests/test_supervisor.py` with no sleeping, because the fake's startup time varies too much to measure them end to end.

```bash
# Paseo runs these three when it creates a worktree. uv is required: it provides CI's Python, as pytest needs a newer one than some systems ship
uv venv --clear --seed --python 3.14 .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt

# The same checks as CI's lint-and-test
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest

# Lint the workflows. actionlint's permission list is behind GitHub's, which does accept this key
uvx --from actionlint-py actionlint -ignore 'unknown permission scope "vulnerability-alerts"' .github/workflows/*.yaml

# Build the image (linux/amd64 is the only target) and run CI's image test on it
docker build --platform linux/amd64 -t intel-gpu-exporter .
scripts/test-image.sh intel-gpu-exporter

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

**The image has three build stages on a shared `base` stage, which holds the one pinned Alpine digest,** and the final one takes only what it needs. `tools` installs `igt-gpu-tools` from `edge/testing`, unpinned because `edge` drops old versions, and only the binary and `intel_gpu_top.version`, which records the package's version, are copied out. `eudev-libs`, which is all it links besides musl, is installed in the final stage rather than copied, because a copied file is invisible to scanners and the SBOM. `intel_gpu_top` itself stays invisible to them. `python` builds a venv and removes its `pip`. The final stage also deletes `ensurepip`, because Alpine's `python3` bundles a pip wheel there, and runs `apk upgrade`, so a rebuild picks up fixes to packages in the base image. A new library in the tool breaks `intel_gpu_top -h` in `image-test` until it's added there.

**`HEALTHCHECK` runs `intel_gpu_exporter.healthcheck`,** which gets `/metrics` on `127.0.0.1` at `LISTEN_PORT` and passes only on the line `igpu_up 1.0`. It uses TCP rather than `/proc` because PID 1 is the host's init under `--pid=host`, and `http.client` because `urllib` would honour a proxy in the environment. A host with no GPU reports unhealthy while the process keeps running, which is expected. `scripts/test-image.sh` covers both states on the real image, with `--pid=host` and a non-default port on the healthy one.

## Configuration

Set through environment variables, none of which are documented in the README. `config.py` reads them once at startup into a frozen dataclass. An invalid number prints one line and exits non-zero before the port is bound or the child is started.

| Variable | Effect |
| --- | --- |
| `LISTEN_PORT` | Port for `/metrics`, from 1 to 65535, default `8080`. |
| `REFRESH_PERIOD_MS` | `intel_gpu_top -s` sample period, from 1 to 2147483, default `10000`. Keep Prometheus's `scrape_interval` in line with it. |
| `DEVICE` | Passed to `intel_gpu_top -d` as a device filter; omitted if unset or empty. |
| `DEBUG` | Enables debug logging. Trimmed and case-insensitive: unset, empty, `false`, `0` and `no` are off, and anything else is on. |

## Release

`docs/specs/2026-10-31-release.md` is the design.

**The repository variable `PUBLISH_ENABLED` switches publishing on, and only the value `true` does, in any case.** While it's off, release-please only opens and updates its PR, with no tag or release, nothing auto-merges that PR, and nothing pushes. Dependabot auto-merge ignores the switch. The coordinator owns it.

The path is `release.yaml` on every push to `main`: release-please creates a draft release and a real tag from a merged release PR, then `publish.yaml` calls `gate.yaml` and its `push` job copies the tested image to `ghcr.io/thedebuggedlife/intel-gpu-exporter` and publishes the draft. A failed gate leaves a draft and a tag, and that version is burned: the next releasable commit takes the next number, and a tag is never moved. There are no release candidates, so the first version is `1.0.0`, and automated validation is the standard for every release.

**The gate builds one OCI archive and everything derives from it.** `docker load` rejects that archive and the classic image store can't hold attestations, so `skopeo` converts it for the image test. grype scans the archive itself, and the push is `skopeo copy --all --preserve-digests`. Each step asserts the digest, so don't add a second build. The gate is three jobs. `gate-tests` installs the Python tools, and `gate-build` installs nothing and restores no cache, because its file is what gets published. The digest the push trusts is a `gate-build` output, read straight after the build. `gate-verify` then tests and scans the downloaded artifact. No gate job may be named `lint-and-test` or `image-test`, because those are the required checks from `ci.yaml`. `gate.yaml` holds no write permission or secret, so dispatching it is the dry run. Only `push` in `publish.yaml` and the job calling it in `release.yaml` may grant `packages: write`, because a called workflow can't exceed its caller.

`scripts/release.py` decides tags and pushes: `X.Y.Z` is never overwritten, `latest` and `X.Y` only move forwards, and auto-merge refuses a release PR that raises the major version, or a first release above major 1. The `Hold the PR for review` step then turns off any auto-merge already enabled, because GitHub won't when the app pushes, and requests review. `scripts/vuln_gate.py` fails on a fixable critical or high finding. `grype-ignore.toml` allows reviewed ones, each with a reason and an expiry date. `SCAN_FORCE_FAIL=true` forces that failure, for testing. The scan also writes SARIF, which `report.yaml` uploads only for `rebuild` runs. `scripts/dependabot_policy.py` decides Dependabot PRs: for `pip` and `docker`, touching only the manifests, patch, digest-only, and security patch or minor updates merge, and everything else is held the same way. That includes every `github-actions` update, because an Action runs beside the key. `update-type` can't identify a digest update, so the title does, and any other Docker update must stay on the same `X.Y`. `dependabot-merge.yaml` acts on the decision from `workflow_run`, and re-checks the PR first, because a fork's run can carry the same workflow name.

**The key stays contained, and `tests/test_workflows.py` enforces each rule.** A compromised Action steals whatever its job can read, including from later steps, so:

- `RELEASE_BOT_PRIVATE_KEY` is a secret of the `release-bot` environment, limited to `main`. Only `release-please` and `auto-merge` in `release.yaml`, `update` in `rebuild.yaml` and `act` in `dependabot-merge.yaml` declare it, the workflows pass it only to the token action's `private-key`, and no other file names it. Code in a job can still read a secret from the runner's memory, which is why these jobs run nothing else. Don't add a preflight that puts it in `env`: the token action already fails loudly on an empty key.
- **Decide, then act.** A read-only job with no key decides and passes plain outputs. The act job has `permissions: {}`, no checkout, no `setup-*`, no pip, npm or Docker, and no artifact. Its steps are a check of what it received, the token action, and `gh` with `jq`. Values arrive through `env`, never in a script, and are checked against a number, a commit or a fixed set. `auto-merge` and `update` also re-read the PR with the token before acting on it, because a compromised action in a decider could forge outputs of the right shape.
- `release-please-action` is the only third-party action that gets a write token, and it's the accepted exception to the rule above. `check-tag` must pass before `publish`, because its outputs aren't trusted. Each job mints one token with only what it needs, and none asks for `workflows`. A stolen key isn't limited by those scopes, so keep the app's own grant small.
- Download every artifact to its own directory, never over the checkout, because a replaced artifact could otherwise replace the scripts that check it.
- An act job has no script to unit test, so tests run its YAML through `run_job` in `tests/helpers.py`. The `Hold the PR for review` step must stay identical in all three jobs that have it.
- No file that names the key reports or uploads. `report.yaml` does that from `workflow_run`.
- `pull_request_target` appears only in `dependabot-decide.yaml`, which is read-only. `dependabot/fetch-metadata` runs alone in its job there, with no alert access, because `alert-lookup`'s GraphQL query returns an empty list rather than an error when access is missing. Alerts come from REST in the `decide` job. It must never check out PR code. GitHub blocks `pull_request_target` on public repositories from December 2, 2026 unless an Actions event policy allows it, and a blocked run reports nothing.

**Commit bodies are input to release-please.** A line starting `Release-As:`, `BREAKING CHANGE:` or a commit type like `feat:` counts, even inside the `<blockquote>` of a Dependabot PR. Keep them out of PR bodies, and clear the body when merging a held Dependabot PR by hand.

**The weekly rebuild can only open a PR.** `rebuild.yaml` calls the gate, then `scripts/rebuild_pr.py`, which can only read, compares the gate's `image-lock` artifact with the committed `image.lock` and the newest published release's asset and writes a plan to its outputs. The `update` job applies that plan with `gh`. Same as committed does nothing. Different, but same as that asset, is `chore(deps): record image contents`, which merges with no release. Otherwise it's `fix(deps): update image packages`. Those titles are constants in `scripts/image_lock.py`, repeated in `update`'s check, and the PR body is a fixed sentence there, because both are commit input. There is one PR, on `rebuild/image-lock`, which later runs reconcile rather than duplicate, and a PR that hasn't merged by a later run fails that run so the tracker reports it. `gh pr list --head` also matches forks, so look a PR up with REST's `pulls?head=<owner>:<branch>`, and keep the owner, because GitHub ignores a `head` without one and lists every PR. `image.lock` is a record, not a build input, and `scripts/image_lock.py` reads it from `docker export` without running the image. Keep it free of anything that changes between identical builds, because any difference is a PR.

**`track-failure` can't ask for `conclusion == 'failure'`.** A job that hits `timeout-minutes` concludes `cancelled`, for the job and the run. The job in `report.yaml` fires on anything but `success`, `skipped` and `neutral`. A queued run dropped by `concurrency` also concludes `cancelled`, so `scripts/track-failure.sh` counts its jobs and reports nothing if there are none. It keeps one open issue labelled `automation-failure`, and a person closes it. The SARIF job must stay out of what it depends on. A new workflow that holds the key or publishes must join the watch list, and a renamed one must be updated there.

`requirements.txt` and the Alpine digest update as `fix(deps):`, which gives a patch release. `requirements-dev.txt`, which Dependabot tells apart by the `dev` in its name, and Actions don't produce a release, and an Action update always needs a person. Alpine packages, including `igt-gpu-tools`, change with any rebuild. Pinned by hand: grype's version and SHA-256 in `scripts/scan-image.sh` (verify the release's `checksums.txt` with `cosign verify-blob` first), and the BuildKit, SBOM generator and `registry` digests in `gate.yaml`.

## Agent workflow (superpowers + Paseo)

These are declared preferences; they override the superpowers skills' defaults.

- **Worktrees are owned by Paseo** (`paseo.json` creates `.venv` from `requirements.txt` and `requirements-dev.txt` on creation; they live under `~/.paseo/worktrees/`). If already in a linked worktree, work there. Never use `EnterWorktree` or `git worktree add`. From the main checkout, create a Paseo worktree (`paseo worktree create --mode branch-off --new-branch <name>`, or `mcp__paseo__create_workspace`) — the main checkout may be shared with other agents. Don't bump the `requirements.txt` pin unless that is the task; if dependencies are missing, run `.venv/bin/pip install -r requirements.txt` and `.venv/bin/pip install -r requirements-dev.txt`.
- **Baseline / verification** is `.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest`, the same checks as CI's `lint-and-test` and Paseo's `test` script. Run `docker build` and `scripts/test-image.sh` only when changing the `Dockerfile`, `.dockerignore`, `requirements.txt`, the healthcheck or that script.
- **Image contents:** the `Dockerfile` copies explicit paths and `.dockerignore` is an allowlist, so a new runtime path needs adding to both.
- **Finishing a branch:** always push and open a PR against `origin` (`thedebuggedlife`), never `upstream`; never merge into `main` locally. Archive the worktree through Paseo, then delete the merged branch. If a worktree was removed outside Paseo, run `scripts/lumen-prune.sh`.
- **Specs** go in `docs/specs/YYYY-MM-DD-<feature>.md`, committed as `docs:` on the feature branch.
- **Plans** and other agent workflow artifacts **never** get committed into the tree.
