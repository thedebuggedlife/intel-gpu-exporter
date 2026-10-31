#!/usr/bin/env bash
# Tests a built image with no GPU. Usage: scripts/test-image.sh [image]
set -euo pipefail

image=${1:-intel-gpu-exporter}
repo=$(cd "$(dirname "$0")/.." && pwd)
wait_limit=60
listing=$(mktemp)
containers=()

cleanup() {
    rm -f "$listing"
    if [ "${#containers[@]}" -gt 0 ]; then
        docker rm --force "${containers[@]}" >/dev/null
    fi
}
trap cleanup EXIT

fail() {
    echo "FAIL: $1" >&2
    if [ -n "${2:-}" ]; then
        docker logs --tail 20 "$2" >&2
    fi
    exit 1
}

pass() {
    echo "ok: $1"
}

scrape() {
    local port
    port=$(docker port "$1" "$2/tcp" | head -n 1 | sed 's/.*://')
    curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$port/metrics"
}

has_line() {
    grep --quiet --line-regexp "$2" <<<"$1"
}

metric_reads() {
    local body
    body=$(scrape "$1" "$2" 2>/dev/null) && has_line "$body" "$3"
}

health_is() {
    [ "$(docker inspect --format '{{.State.Health.Status}}' "$1")" = "$2" ]
}

is_running() {
    [ "$(docker inspect --format '{{.State.Running}}' "$1")" = "true" ]
}

wait_for() {
    local description=$1 container=$2
    shift 2
    for _ in $(seq "$wait_limit"); do
        if "$@"; then
            pass "$description"
            return
        fi
        is_running "$container" || fail "exited while waiting: $description" "$container"
        sleep 1
    done
    fail "timed out: $description" "$container"
}

short_health=(--health-interval=1s --health-start-period=1s --health-start-interval=1s --health-retries=2)

usage=$(docker run --rm --entrypoint /usr/bin/intel_gpu_top "$image" -h) ||
    fail "intel_gpu_top -h exited non-zero"
grep --quiet "Usage:" <<<"$usage" || fail "intel_gpu_top -h printed no usage"
pass "intel_gpu_top -h runs"

filesystem=$(docker create "$image")
containers+=("$filesystem")
docker export "$filesystem" | tar -t >"$listing"
has_line "$(cat "$listing")" "usr/bin/intel_gpu_top" || fail "the listing has no intel_gpu_top"
if grep --extended-regexp '(^|/)\.git(/|$)' "$listing"; then
    fail ".git is in the image"
fi
if grep --extended-regexp '(^|/)tests(/|$)' "$listing"; then
    fail "tests are in the image"
fi
if grep '^app/' "$listing" | grep --invert-match --extended-regexp '^app/(intel_gpu_exporter/.*)?$'; then
    fail "/app holds more than the exporter package"
fi
if grep --extended-regexp '(^|/)(pip[0-9.]*|pip-[0-9][^/]*|ensurepip)(/|$)' "$listing"; then
    fail "pip is in the image"
fi
pass "no .git, tests or pip in the filesystem"

no_gpu=$(docker run --detach --publish 127.0.0.1::8080 "${short_health[@]}" "$image")
containers+=("$no_gpu")
wait_for "no GPU: /metrics reports igpu_up 0" "$no_gpu" metric_reads "$no_gpu" 8080 'igpu_up 0\.0'
wait_for "no GPU: the child is restarted" "$no_gpu" metric_reads "$no_gpu" 8080 'igpu_collector_restarts_total [2-9]\.0'
wait_for "no GPU: the container reports unhealthy" "$no_gpu" health_is "$no_gpu" unhealthy
is_running "$no_gpu" || fail "no GPU: the process stopped" "$no_gpu"
pass "no GPU: the process keeps running"

docker stop -t 20 "$no_gpu" >/dev/null
[ "$(docker inspect --format '{{.State.ExitCode}}' "$no_gpu")" = "0" ] ||
    fail "SIGTERM through catatonit did not exit cleanly" "$no_gpu"
pass "SIGTERM through catatonit exits 0"

# Stands in for intel_gpu_top with real captured output, in the mode the image documents
replay='printf "#!/bin/sh\ncat /sample.json\nexec sleep 600\n" >/usr/bin/intel_gpu_top
chmod +x /usr/bin/intel_gpu_top
exec /usr/bin/catatonit -- /opt/venv/bin/python -m intel_gpu_exporter'
sampled=$(docker run --detach --pid=host --env LISTEN_PORT=9100 --publish 127.0.0.1::9100 \
    --volume "$repo/tests/fixtures/physical.json:/sample.json:ro" \
    "${short_health[@]}" --entrypoint /bin/sh "$image" -c "$replay")
containers+=("$sampled")
wait_for "sampled: /metrics reports igpu_up 1" "$sampled" metric_reads "$sampled" 9100 'igpu_up 1\.0'
wait_for "sampled: the container reports healthy" "$sampled" health_is "$sampled" healthy
has_line "$(scrape "$sampled" 9100)" 'igpu_engines_video_1_busy [0-9.]*' ||
    fail "sampled: no engine series" "$sampled"
pass "sampled: engine series are exported"
