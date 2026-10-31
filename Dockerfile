FROM docker.io/library/alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a AS base

FROM base AS tools

RUN apk add --no-cache \
        --repository=https://dl-cdn.alpinelinux.org/alpine/edge/testing \
        igt-gpu-tools

FROM base AS python

RUN apk add --no-cache python3 && python3 -m venv /opt/venv

COPY requirements.txt /tmp/requirements.txt

RUN \
    /opt/venv/bin/pip install --no-cache-dir --require-hashes --requirement /tmp/requirements.txt \
    && /opt/venv/bin/pip uninstall --yes pip

FROM base

# eudev-libs is installed rather than copied so that scanners and the SBOM can see it
RUN \
    apk upgrade --no-cache \
    && apk add --no-cache catatonit eudev-libs python3 \
    && rm -rf /usr/lib/python3.*/ensurepip

COPY --from=tools /usr/bin/intel_gpu_top /usr/bin/intel_gpu_top
COPY --from=python /opt/venv /opt/venv
COPY intel_gpu_exporter /app/intel_gpu_exporter

ENV \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=5s --retries=3 \
    CMD ["/opt/venv/bin/python", "-m", "intel_gpu_exporter.healthcheck"]

ENTRYPOINT ["/usr/bin/catatonit", "--", "/opt/venv/bin/python"]
CMD ["-m", "intel_gpu_exporter"]

LABEL \
    org.opencontainers.image.title="intel-gpu-exporter" \
    org.opencontainers.image.description="Prometheus exporter for Intel GPUs" \
    org.opencontainers.image.source="https://github.com/thedebuggedlife/intel-gpu-exporter" \
    org.opencontainers.image.url="https://github.com/thedebuggedlife/intel-gpu-exporter"
