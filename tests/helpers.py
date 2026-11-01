import os
import re
import subprocess
import time
from pathlib import Path

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from intel_gpu_exporter.stream import StreamDecoder

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
FIXTURES = TESTS / "fixtures"
DATA_JSON = TESTS / "data.json"
WAIT_LIMIT = 10
EXPRESSION = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")
COMPARISON = re.compile(r"([\w.\-]+) (==|!=) '([^']*)'")
FAST_TIMING = {
    "timeout": 0.5,
    "backoff_initial": 0.02,
    "backoff_cap": 0.16,
    "stop_grace": 0.3,
    "join_grace": 0.1,
    "poll": 0.01,
}


def load_samples(path):
    return StreamDecoder(lambda key, message: None).feed(path.read_bytes())


def exposition(collector):
    registry = CollectorRegistry()
    registry.register(collector)
    return generate_latest(registry).decode()


def scrape(collector):
    return {
        sample.name: sample.value
        for family in text_string_to_metric_families(exposition(collector))
        for sample in family.samples
        if sample.name.startswith("igpu_")
    }


def labelled(collector, name):
    return {
        tuple(sample.labels.values()): sample.value
        for family in text_string_to_metric_families(exposition(collector))
        for sample in family.samples
        if sample.name == name
    }


def wait_until(condition, limit=WAIT_LIMIT):
    deadline = time.monotonic() + limit
    while True:
        result = condition()
        if result:
            return result
        assert time.monotonic() < deadline, "Condition not reached in time"
        time.sleep(0.005)


def pid_is_gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def expression_values(values, text):
    return EXPRESSION.sub(lambda match: values.get(match[1], ""), str(text))


def condition_holds(condition, values):
    for clause in filter(None, condition.split(" && ")):
        comparison = COMPARISON.fullmatch(clause.strip())
        assert comparison, f"can't evaluate {clause!r}"
        name, operator, expected = comparison.groups()
        if (values.get(name, "") == expected) != (operator == "=="):
            return False
    return True


def run_step(step, values, environment, cwd=REPO):
    env = environment | {
        name: expression_values(values, value)
        for name, value in step.get("env", {}).items()
    }
    # A step with no shell runs under bash -e, and only shell: bash adds pipefail
    flags = ["-eo", "pipefail"] if step.get("shell") == "bash" else ["-e"]
    return subprocess.run(
        ["bash", "--noprofile", "--norc", *flags, "-c", step["run"]],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def run_job(job, values, environment, cwd=REPO):
    """Runs a job's run: steps as a runner would, skipping uses: and stopping on a failure."""
    env = environment | {
        name: expression_values(values, value)
        for name, value in job.get("env", {}).items()
    }
    stdout, stderr, code = "", "", 0
    for step in job["steps"]:
        if "run" not in step or not condition_holds(step.get("if", ""), values):
            continue
        result = run_step(step, values, env, cwd)
        stdout += result.stdout
        stderr += result.stderr
        code = result.returncode
        if code != 0:
            break
    return subprocess.CompletedProcess(job["name"], code, stdout, stderr)


def read_outputs(path):
    lines = path.read_text().splitlines() if path.exists() else []
    return dict(line.split("=", 1) for line in lines)
