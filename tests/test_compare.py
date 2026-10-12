"""Cross-run comparison: verdicts must respect direction, tolerance and provenance."""

from __future__ import annotations

import json

import pytest

from open_benchmark.analysis import aggregate_by_device, compare_sets, pick_device
from open_benchmark.cli import main
from open_benchmark.schema import Device, Metric, Result, Run, write_run


def _device(cpu: str = "AMD Ryzen 9 5950X", cores: int = 16, mem: int = 64) -> Device:
    device = Device(
        os="windows",
        arch="AMD64",
        cpu={"model": cpu, "physical_cores": cores, "logical_cores": cores * 2},
        memory={"total_bytes": mem * 1024**3, "modules": []},
        disks=[],
        system={"model": "X570"},
    )
    device.compute_fingerprint()
    return device


def _metric(key: str, value: float, unit: str = "MB/s", higher: bool = True) -> Metric:
    return Metric(
        key=key, value=value, unit=unit, higher_is_better=higher, spread_pct=1.0, samples=[value]
    )


def _run(device: Device, *metrics: Metric, suite: str = "quick") -> Run:
    return Run(
        suite=suite,
        device=device,
        results=[Result(name="cpu.hash", backend="openssl", metrics=list(metrics))],
    )


def _verdicts(result: dict) -> dict[str, str]:
    return {delta["key"]: delta["verdict"] for delta in result["deltas"]}


def test_verdict_respects_direction_and_tolerance():
    device = _device()
    base = _run(device, _metric("throughput", 1000.0), _metric("lat", 100.0, "ns", higher=False),
                _metric("stable", 500.0))
    target = _run(device, _metric("throughput", 1200.0), _metric("lat", 80.0, "ns", higher=False),
                  _metric("stable", 505.0))
    result = compare_sets([base], [target])
    verdicts = _verdicts(result)
    assert verdicts["cpu.hash.throughput"] == "better"
    assert verdicts["cpu.hash.lat"] == "better"  # lower latency is better
    assert verdicts["cpu.hash.stable"] == "same"  # inside the default 5% tolerance
    assert result["counts"]["better"] == 2
    assert result["counts"]["same"] == 1


def test_latency_increase_is_a_regression():
    device = _device()
    result = compare_sets(
        [_run(device, _metric("lat", 80.0, "ns", higher=False))],
        [_run(device, _metric("lat", 120.0, "ns", higher=False))],
    )
    assert _verdicts(result)["cpu.hash.lat"] == "worse"
    assert result["deltas"][0]["delta_pct"] == pytest.approx(50.0)


def test_new_and_missing_metrics_are_labelled():
    device = _device()
    result = compare_sets(
        [_run(device, _metric("old", 10.0))],
        [_run(device, _metric("new", 10.0))],
    )
    verdicts = _verdicts(result)
    assert verdicts["cpu.hash.new"] == "new"
    assert verdicts["cpu.hash.old"] == "missing"


def test_tolerance_widens_and_narrows():
    device = _device()
    base, target = [_run(device, _metric("x", 1000.0))], [_run(device, _metric("x", 1030.0))]
    assert _verdicts(compare_sets(base, target, tolerance_pct=5.0))["cpu.hash.x"] == "same"
    assert _verdicts(compare_sets(base, target, tolerance_pct=1.0))["cpu.hash.x"] == "better"


def test_pick_device_needs_a_selector_when_ambiguous():
    runs = [_run(_device(cpu="A", cores=4, mem=16)), _run(_device(cpu="B", cores=8, mem=32))]
    aggregated = aggregate_by_device(runs)
    assert len(aggregated) == 2
    with pytest.raises(KeyError):
        pick_device(aggregated)
    first = next(iter(aggregated))
    assert pick_device(aggregated, "0") == first
    assert pick_device(aggregated, first[:8]) == first


def test_cli_compare_json_and_regression_gate(tmp_path, capsys):
    device = _device()
    base_path = tmp_path / "base.jsonl"
    target_path = tmp_path / "target.jsonl"
    write_run(_run(device, _metric("throughput", 1000.0)), base_path)
    write_run(_run(device, _metric("throughput", 800.0)), target_path)

    assert main(["compare", str(base_path), str(target_path), "--json", "-"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["worse"] == 1
    assert payload["deltas"][0]["verdict"] == "worse"
    assert payload["deltas"][0]["delta_pct"] == pytest.approx(-20.0)

    assert main(["compare", str(base_path), str(target_path), "--fail-on-regression"]) == 1


def test_compare_suite_filter_keeps_workloads_apart():
    device = _device()
    base = [
        _run(device, _metric("x", 1000.0), suite="quick"),
        _run(device, _metric("x", 100.0), suite="full"),
    ]
    target = [
        _run(device, _metric("x", 500.0), suite="quick"),
        _run(device, _metric("x", 50.0), suite="full"),
    ]
    quick = compare_sets(base, target, suite="quick")
    assert quick["deltas"][0]["base"] == 1000.0
    assert quick["deltas"][0]["target"] == 500.0
    # pooled, the medians mix both workloads and no longer mean anything
    assert compare_sets(base, target)["deltas"][0]["base"] != 1000.0

