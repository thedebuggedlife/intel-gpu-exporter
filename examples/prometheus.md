# Prometheus config example

```yaml
scrape_configs:
  - job_name: "intel-gpu_exporter"
    scrape_interval: 10s ## Change this to the same value as REFRESH_PERIOD_MS (or remove this line)
    static_configs:
      - targets:
          - "gpu-host.example.com:8080"
```

# Alert rules example

```yaml
groups:
  - name: intel-gpu-exporter
    rules:
      - alert: IntelGpuExporterDown
        expr: igpu_up == 0 or up{job="intel-gpu_exporter"} == 0
        for: 5m
      - alert: IntelGpuExporterParseWarnings
        expr: igpu_parse_warnings_total > 0
```

`up` covers a target Prometheus can't scrape at all, which `igpu_up == 0` alone would miss. Keep its `job` the same as `job_name` above.
