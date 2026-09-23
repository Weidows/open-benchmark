"""Smoke tests for the CLI: fast executors only, no disk or native kernels."""

from __future__ import annotations

import json

from open_benchmark.cli import main
from open_benchmark.executors import SUITES, resolve


def test_suites_reference_existing_executors():
    from open_benchmark.executors import BY_NAME

    for suite, members in SUITES.items():
        assert members, suite
        for name in members:
            assert name in BY_NAME, name


def test_resolve_prefix_and_exclude():
    picked = {ex.name for ex in resolve(["cpu"], suite="full")}
    assert {"cpu.hash", "cpu.compress"} <= picked
    remaining = {ex.name for ex in resolve(["disk"], ["disk.rand"], suite="full")}
    assert remaining == {"disk.seq"}


def test_run_quick_subset_to_stdout(capsys):
    code = main(
        [
            "run",
            "--only",
            "cpu.python_sieve",
            "--repeat",
            "1",
            "--scale",
            "0.1",
            "--no-native",
            "--json",
            "-",
            "--quiet",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"].startswith("open-benchmark.run/")
    assert payload["device"]["fingerprint"]
    assert payload["results"][0]["name"] == "cpu.python_sieve"
    assert payload["results"][0]["metrics"][0]["bound"] == "interpreter"


def test_devices_command(capsys):
    assert main(["devices"]) == 0
    device = json.loads(capsys.readouterr().out)
    assert "cpu" in device and "os" in device
    assert device["fingerprint"]


def test_leaderboard_reads_jsonl(tmp_path, capsys):
    import subprocess
    import sys

    runs = tmp_path / "runs.jsonl"
    code = main(
        [
            "run",
            "--only",
            "cpu.python_sieve",
            "--repeat",
            "1",
            "--scale",
            "0.1",
            "--no-native",
            "--json",
            str(runs),
            "--quiet",
        ]
    )
    assert code == 0
    assert main(["leaderboard", str(runs), "--bound", "interpreter"]) == 0
    out = capsys.readouterr().out
    assert "cpu.python_sieve.passes_per_s" in out
    assert "| metric |" in out
    assert subprocess.run([sys.executable, "-c", "import open_benchmark"], check=False).returncode == 0
