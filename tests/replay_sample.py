import importlib.util
import json
from pathlib import Path

from prometheus_client import generate_latest

root = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("exporter", root / "intel-gpu-exporter.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

sample = (root / "tests" / "data.json").read_text()
exporter.update(json.JSONDecoder().raw_decode(sample)[0])

metrics = generate_latest().decode()
print(metrics)

# update() reports missing fields as 0, so a broken lookup would not raise
assert "igpu_rc6 92.505022" in metrics
