#!/usr/bin/env bash
# Scans an OCI archive with a verified grype and applies the gate.
# Usage: scripts/scan-image.sh <archive> <image-id>
set -euo pipefail

grype_version=0.121.0
grype_sha256=64e07133d3486115de647a66581461dccf5c732748a0a3119077395565f15519

archive=$1
image_id=$2
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

tarball="grype_${grype_version}_linux_amd64.tar.gz"
curl --fail --silent --show-error --location --retry 3 --output "$work/$tarball" \
    "https://github.com/anchore/grype/releases/download/v$grype_version/$tarball"
echo "$grype_sha256  $work/$tarball" | sha256sum --check --quiet
tar --extract --gzip --file "$work/$tarball" --directory "$work" grype

GRYPE_CHECK_FOR_APP_UPDATE=false "$work/grype" "oci-archive:$archive" \
    --platform linux/amd64 --output table --output "json=$work/report.json"

"$repo/scripts/vuln_gate.py" --report "$work/report.json" \
    --ignore-file "$repo/grype-ignore.toml" --image-id "$image_id"
