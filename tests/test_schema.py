"""Schema round-trip, fingerprint stability and JSONL IO."""

from __future__ import annotations

import json

from open_benchmark.schema import SCHEMA_VERSION, Device, Metric, Result, Run, read_runs, write_run


def _device(**overrides) -> Device:
    base = {
        "os": "windows",
        "arch": "AMD64",
        "cpu": {"model": "AMD Ryzen 9 5950X", "physical_cores": 16, "logical_cores": 32},
        "memory": {
            "total_bytes": 64 * 1024**3,
            "modules": [{"locator": "DIMM 0", "size_bytes": 32 * 1024**3, "speed_mtps": 3600}],
        },
        "disks": [{"model": "Samsung SSD 990 PRO", "size_bytes": 2 * 10**12, "media": "ssd"}],
        "system": {"model": "X570"},
    }
    base.update(overrides)
    device = Device(**base)
    device.compute_fingerprint()
    return device


def test_fingerprint_is_stable_and_ignores_hostname():
    a = _device(hostname="desktop")
    b = _device(hostname="other-name")
    assert a.fingerprint == b.fingerprint
    assert len(a.fingerprint) == 16


def test_fingerprint_changes_with_hardware():
    a = _device()
    b = _device(memory={"total_bytes": 128 * 1024**3, "modules": []})
    assert a.fingerprint != b.fingerprint


def test_metric_defaults_and_validation():
    metric = Metric(key="seq_read_mbs", value=7100.5, unit="MB/s")
    assert metric.samples == [7100.5]
    assert metric.n == 1
    assert metric.bound == "hardware"


def test_run_roundtrip_jsonl(tmp_path):
    run = Run(
        suite="quick",
        device=_device(),
        results=[
            Result(
                name="disk.seq",
                backend="win32-unbuffered",
                metrics=[Metric(key="seq_read_mbs", value=7100.5, unit="MB/s")],
            ),
            Result(name="native.memory", status="skipped", error="no C compiler found"),
        ],
    )
    run.ended_at = run.started_at
    path = tmp_path / "runs.jsonl"
    write_run(run, path)
    write_run(run, path)

    loaded = read_runs([str(path)])
    assert len(loaded) == 2
    assert loaded[0].schema == SCHEMA_VERSION
    assert loaded[0].device.fingerprint == run.device.fingerprint
    assert loaded[0].results[0].metric("seq_read_mbs").value == 7100.5
    assert loaded[0].results[1].status == "skipped"


def test_flat_metrics_filters_by_bound(tmp_path):
    run = Run(
        device=_device(),
        results=[
            Result(
                name="cpu.hash",
                metrics=[
                    Metric(key="sha256_mbs", value=2000, unit="MB/s"),
                    Metric(key="python_thing", value=1, unit="count", bound="interpreter"),
                ],
            )
        ],
    )
    assert run.flat_metrics(bound="hardware") == {"cpu.hash.sha256_mbs": 2000}
    assert "cpu.hash.python_thing" in run.flat_metrics(bound="interpreter")
    assert len(run.flat_metrics(bound=None)) == 2


def test_write_run_to_stdout_returns_payload():
    run = Run(device=_device())
    payload = write_run(run, None)
    assert json.loads(payload)["schema"] == SCHEMA_VERSION
