# intel-gpu-exporter

A Prometheus exporter for Intel GPUs. It runs `intel_gpu_top` and serves the latest sample as metrics at `/metrics` on port 8080.

This is a maintained fork of [onedr0p/intel-gpu-exporter](https://github.com/onedr0p/intel-gpu-exporter), which is archived. It aims to be:

- **A drop-in replacement.** Existing metric names, the default port and the existing environment variables keep working. See [Moving from upstream](#moving-from-upstream).
- **Correct.** Every engine is reported, and a broken data source is visible instead of reading as 0.
- **Minimal.** The image holds the exporter, `intel_gpu_top` and what they need to run, and it runs with one added capability.
- **Kept current automatically.** Dependency updates and weekly rebuilds become versioned, scanned releases. See [Update policy](#update-policy).

Images are built for `linux/amd64` only.

## Quick start

```bash
docker run --detach --name intel-gpu-exporter --restart unless-stopped \
  --cap-drop ALL --cap-add PERFMON --device /dev/dri \
  --publish 127.0.0.1:8080:8080 \
  ghcr.io/thedebuggedlife/intel-gpu-exporter:1.0

curl --silent http://127.0.0.1:8080/metrics | grep '^igpu_up'
```

Give it a few seconds to start. `igpu_up 1.0` means samples are arriving. If it stays at `igpu_up 0.0`, see [Permissions](#permissions).

### Docker Compose

```yaml
services:
  intel-gpu-exporter:
    image: ghcr.io/thedebuggedlife/intel-gpu-exporter:1.0
    container_name: intel-gpu-exporter
    restart: unless-stopped
    cap_drop:
      - ALL
    cap_add:
      - PERFMON
    devices:
      - /dev/dri:/dev/dri
    ports:
      - 127.0.0.1:8080:8080
```

To also get client counts, read [Permissions](#permissions) first, then use these settings:

```yaml
    cap_add:
      - PERFMON
      - SYS_PTRACE
    pid: host
```

If Prometheus runs on the same Docker network, remove `ports` and use the target `intel-gpu-exporter:8080`. If it runs on another machine, publish the port on an address only your trusted network can reach.

> [!WARNING]
> The listening port has no authentication and no encryption. Anyone who can reach it can read the metrics, including process names if client counts are on. Keep it on a trusted network.

## Permissions

`intel_gpu_top` reads the GPU's performance counters through the kernel, so the exporter needs more than a default container. These results come from one host with an Intel iGPU, at `perf_event_paranoid` 2:

| Setup | Metrics | Clients |
| --- | --- | --- |
| `--privileged --pid=host` | Yes | Yes |
| `--privileged` | Yes | No |
| `--cap-drop ALL --cap-add PERFMON --device /dev/dri` | Yes | No |
| `--cap-drop ALL --cap-add PERFMON --cap-add SYS_PTRACE --device /dev/dri --pid=host` | Yes | Yes |
| The same, with `--read-only --security-opt no-new-privileges` | Yes | Yes |
| `--cap-add PERFMON --device /dev/dri --pid=host --user 65534` | No | No |
| `--device /dev/dri` with no added capability | No | No |

**Use the least privilege: `CAP_PERFMON`, as root, for metrics.** That's the setup in the quick start.

**Client counts cost much more than they give.** `igpu_clients_count` also needs `CAP_SYS_PTRACE` and the host's PID namespace, which together let the container see and inspect every process on the host. Leave them out unless you want this metric.

**Non-root doesn't work at `perf_event_paranoid` 2,** because Docker gives a non-root user no effective capabilities. Lower values weren't tested.

**`/dev/dri` isn't strictly needed for metrics:** `CAP_PERFMON` with no device mapped also collected them. The examples keep it because that's the combination tested with `--cap-drop ALL`.

If permissions are wrong, the exporter keeps running and serves `igpu_up 0`, the container reports unhealthy, and its log has a line such as `intel_gpu_top: Failed to initialize PMU! (Permission denied)`. A host with no GPU does the same, but logs `No device filter specified and no discrete/integrated i915 devices found`.

## Configuration

Everything is set through environment variables. An invalid number prints one line and exits non-zero before the port is bound.

| Variable | Default | Effect |
| --- | --- | --- |
| `LISTEN_PORT` | `8080` | Port for `/metrics`, from 1 to 65535. |
| `REFRESH_PERIOD_MS` | `10000` | `intel_gpu_top` sample period, from 1 to 2147483. Keep Prometheus's `scrape_interval` in line with it. |
| `DEVICE` | Unset | Passed to `intel_gpu_top -d` as a device filter, such as `drm:/dev/dri/card1`. Empty counts as unset. |
| `DEBUG` | Off | Enables debug logging. Trimmed and case-insensitive: unset, empty, `false`, `0` and `no` are off, and anything else is on. |

The exporter restarts `intel_gpu_top` if it exits, or if no sample arrives for 3 periods or 30 seconds, whichever is longer. `igpu_up` uses the same limit.

## Metrics

The image also serves the `python_*` and `process_*` metrics of the Prometheus client library.

### GPU

These are all gauges.

| Metric | Unit | Meaning |
| --- | --- | --- |
| `igpu_engines_<class>_<instance>_busy` | % | Time the engine was busy |
| `igpu_engines_<class>_<instance>_sema` | % | Time the engine waited on a semaphore |
| `igpu_engines_<class>_<instance>_wait` | % | Time the engine waited on an event |
| `igpu_frequency_actual` | MHz | Actual frequency |
| `igpu_frequency_requested` | MHz | Requested frequency |
| `igpu_rc6` | % | Time in the RC6 power-saving state. See [Known limitations](#known-limitations) |
| `igpu_interrupts` | Interrupts/s | Interrupt rate |
| `igpu_period` | ms | Length of the sample |
| `igpu_power_gpu` | W | GPU power |
| `igpu_power_package` | W | Package power |
| `igpu_imc_bandwidth_reads` | MiB/s | Memory controller reads |
| `igpu_imc_bandwidth_writes` | MiB/s | Memory controller writes |
| `igpu_gt_frequency_actual{gt="N"}` | MHz | Actual frequency of each GT, on multi-GT devices |
| `igpu_gt_frequency_requested{gt="N"}` | MHz | Requested frequency of each GT, on multi-GT devices |
| `igpu_gt_rc6{gt="N"}` | % | RC6 of each GT, on multi-GT devices |
| `igpu_clients_count{name="..."}` | Processes | Processes with this name using the GPU |

`<class>` is `render_3d`, `blitter`, `video`, `video_enhance` or `compute`, and `<instance>` is the engine's number, so a second video engine is `igpu_engines_video_1_busy`. Every instance `intel_gpu_top` reports is exported. A class the exporter doesn't recognise is exported under a sanitised name and counts as a parse warning.

### Health

| Metric | Type | Meaning |
| --- | --- | --- |
| `igpu_up` | Gauge | 1 if a sample arrived within the timeout, otherwise 0 |
| `igpu_last_sample_timestamp_seconds` | Gauge | Unix time the last sample arrived. Absent until the first sample |
| `igpu_collector_restarts_total` | Counter | `intel_gpu_top` restarts since the exporter started, for any reason |
| `igpu_parse_warnings_total` | Counter | Distinct parse warnings logged since the exporter started |

`igpu_parse_warnings_total` stays at 0 on healthy hardware, so any other value means `intel_gpu_top` printed something the exporter didn't expect. [`examples/prometheus.md`](examples/prometheus.md) has alert rules for it and for `igpu_up`.

### Reading the metrics

**Missing data is a gap, not 0.** A series exists only if `intel_gpu_top` reported the value, and a real 0 stays 0. `igpu_imc_bandwidth_*` and `igpu_power_*` are absent on hardware that doesn't report them. If the tool prints a section but can't read one of its counters, it prints 0 for that field, and the exporter can't tell that from a real 0.

**While `igpu_up` is 0, only the health metrics are served,** so a hang shows as a gap rather than a flat line. The data comes back as soon as samples resume. `igpu_up` depends only on the age of the last sample, so it stays 1 through a quick restart. A hang that a restart cures can pass between two scrapes, so `igpu_collector_restarts_total` is what shows those.

**Load can appear on either video engine.** The kernel picks the engine, so the same work can show on `video_0` or `video_1`. Dashboards should show both.

**On a device with a separate media GT, `igpu_frequency_actual` reflects GT 0,** so it stays low during transcoding. The `igpu_gt_*` series show the rest. GT 0 fills `igpu_frequency_*` and `igpu_rc6`, and every GT also gets its own `igpu_gt_*` series. Those series don't exist on single-GT output.

**`igpu_clients_count` stays empty without host PID visibility.** That's expected. It counts processes, not DRM clients, and a name seen earlier reads 0 once no process has it. A sample with no client data produces no client series.

**Values are the last sample, with no smoothing.** The first sample after `intel_gpu_top` starts covers about 50 ms whatever the period is, so its rates are noisy.

### Known limitations

- **`igpu_rc6` can read 0 when the GPU's real residency is higher, including at idle,** because that's what `intel_gpu_top` reports. The cause is unknown.
- **A process name containing `"` makes `intel_gpu_top` emit invalid JSON.** So does a backslash that doesn't start a valid JSON escape. This shows as `igpu_up` 0 for as long as that process runs.
- **`igpu_gt_*` has only been tested against a hand-written fixture,** because no multi-GT hardware was available.
- **Long-run memory use hasn't been measured.** A 5-minute run used about the same memory as upstream, which is too short to rule out slow growth.
- **Real hardware testing ran once,** before the first release, on one Intel iGPU with two video engines and a single GT. See [Update policy](#update-policy).

## Examples

- [`examples/prometheus.md`](examples/prometheus.md) has a scrape config and alert rules.
- [`examples/simple-grafana-dash.json`](examples/simple-grafana-dash.json) is a Grafana dashboard, including both video engines and `igpu_up`. After importing it, pick the data source and instance from the variables at the top. Panels stay empty for metrics your hardware doesn't report.

## Moving from upstream

Change the image to `ghcr.io/thedebuggedlife/intel-gpu-exporter`. The `rolling` tag is discontinued, and the tags are now `X.Y.Z`, `X.Y` and `latest`.

**What keeps working, for every `1.x` release.** Every metric upstream served keeps its name, unit and meaning, whenever the hardware reports it. Port 8080, `/metrics`, `REFRESH_PERIOD_MS`, `DEVICE` and `DEBUG` also keep working. Upstream's `privileged: true` with `pid: host` still works too, though [Permissions](#permissions) shows that you can drop most of that.

**What changes.**

- `IS_DOCKER` is no longer used, and you can remove it.
- `igpu_imc_bandwidth_*` disappears on hardware that never reported it, because the 0 upstream served there was invented. More generally, a value that's missing is now a gap, so check any query or alert that expects a 0.
- `DEBUG=false` now really means off. Upstream turned debug logging on for any non-empty value.
- A stalled `intel_gpu_top` now shows as a gap with `igpu_up` 0, and it's restarted. Upstream kept serving the last values.
- New series appear, such as `igpu_engines_video_1_*` and the health metrics.

## Update policy

The aim is that the image stays patched with no routine work, and that every automatic change leaves a reviewable diff.

### What happens to each change

| Change | Found by | Merged by | Release |
| --- | --- | --- | --- |
| Python dependencies or the Alpine base image: a patch, a digest, or a security update that's a patch or minor | Dependabot | Itself, once checks pass | Patch |
| Python dependencies or the Alpine base image: anything else, including any major update or a new Alpine `X.Y` | Dependabot | A person | Patch |
| Dev tools in `requirements-dev.txt` | Dependabot | The same rules | None |
| GitHub Actions | Dependabot | Always a person | None |
| Alpine packages, including `intel_gpu_top` | Weekly rebuild | Itself, once checks pass | Patch, or none if the last release already has them |

Dependabot runs weekly, and security updates don't wait for its usual 7-day cooldown. It can't see Alpine packages or `intel_gpu_top`, so a weekly rebuild covers them. It builds a fresh image and compares its contents with [`image.lock`](image.lock), a record of every package and version, plus the tool's hash. If nothing changed, nothing happens. If something changed, it opens a PR with the new lock, so the diff shows what changed.

Merging a release PR is automatic too, unless it raises the major version, which always needs a person.

### Every release

Each release runs the tests, builds one image, tests that image and scans it with [grype](https://github.com/anchore/grype). A fixable critical or high finding stops the release, unless a reviewed entry in [`grype-ignore.toml`](grype-ignore.toml), with a reason and an expiry date, allows it. The image pushed is the image that was tested.

A failed check publishes nothing, and that version number isn't reused, so version numbers can skip.

**There are no release candidates.** Automated validation is the standard for every release, so a security patch never waits for hardware testing. Testing on real hardware ran once before the first release. A problem only real hardware can show gets reported and fixed in the next release.

### Tags

| Tag | Moves? |
| --- | --- |
| `X.Y.Z` | Never. A published version is never overwritten |
| `X.Y` | Forwards only, to the newest patch |
| `latest` | Forwards only, and only on a stable release |

Use `X.Y` to get patches, or `X.Y.Z` to choose when to update. Breaking what [Moving from upstream](#moving-from-upstream) says keeps working would take a `2.0.0`.

### What the image comes with

Each image has a provenance attestation and an SBOM. Images aren't signed. To read the attestations:

```bash
docker buildx imagetools inspect ghcr.io/thedebuggedlife/intel-gpu-exporter:1.0 --format '{{ json .Provenance }}'
docker buildx imagetools inspect ghcr.io/thedebuggedlife/intel-gpu-exporter:1.0 --format '{{ json .SBOM }}'
```

Each GitHub release also has that image's `image.lock`.

**Scanners can't see inside `intel_gpu_top`.** It's a binary copied into the image, with no package entry, so it isn't in the SBOM or in any scan, including the release scan. A vulnerability in `igt-gpu-tools`, the package it comes from, won't stop a release. `image.lock` records the tool's version and hash, so a change to it is visible, but a new vulnerability in an unchanged version isn't.

## Security

See [`SECURITY.md`](SECURITY.md) to report a vulnerability.

## Attribution and licence

This project is a fork of [onedr0p/intel-gpu-exporter](https://github.com/onedr0p/intel-gpu-exporter), and the original exporter is that project's work. Upstream publishes no licence file, and its README invites forking.

This repository has no licence file either. A licence on this fork would also have to cover the inherited code, and that isn't this fork's to grant, so the question stays open.
