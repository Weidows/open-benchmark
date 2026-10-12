"""Pre-submission validation: a bad run must never reach the leaderboard."""

from __future__ import annotations

from open_benchmark.schema import Device, Metric, Result, Run, write_run
from open_benchmark.validate import validate_paths, validate_run


def _device(**overrides) -> Device:
    base = {
        "os": "windows",
        "arch": "AMD64",
        "cpu": {"model": "AMD Ryzen 9 5950X", "physical_cores": 16, "logical_cores": 32},
        "memory": {"total_bytes": 64 * 1024**3, "modules": []},
        "disks": [],
        "system": {"model": "X570"},
    }
    base.update(overrides)
    device = Device(**base)
    device.compute_fingerprint()
    return device


def _metric(key="sha256_mbs", value=3000.0, unit="MB/s", **extra) -> Metric:
    extra.setdefault("spread_pct", 1.0)
    return Metric(key=key, value=value, unit=unit, **extra)


def _run(**overrides) -> Run:
    run = Run(
        suite="quick",
        device=_device(),
        results=[Result(name="cpu.hash", backend="openssl", metrics=[_metric()])],
    )
    for key, value in overrides.items():
        setattr(run, key, value)
    return run


def test_clean_run_has_no_issues():
    assert validate_run(_run()) == []


def test_missing_fingerprint_is_an_error():
    run = _run()
    run.device.fingerprint = ""
    codes = {issue.code for issue in validate_run(run)}
    assert "device.fingerprint.missing" in codes


def test_tampered_hardware_field_fails_the_fingerprint_check():
    run = _run()
    run.device.cpu["logical_cores"] = 999  # hardware now disagrees with the recorded fingerprint
    assert "device.fingerprint.mismatch" in {issue.code for issue in validate_run(run)}


def test_non_finite_value_and_sample_count_mismatch():
    run = _run()
    run.results[0].metrics = [
        _metric(key="a", value=float("nan")),
        _metric(key="b", value=1.0, n=3, samples=[1.0]),
    ]
    codes = {issue.code for issue in validate_run(run)}
    assert {"metric.value", "metric.n"} <= codes


def test_negative_throughput_and_noisy_spread_are_flagged():
    run = _run()
    run.results[0].metrics = [_metric(value=-5.0, spread_pct=42.0)]
    codes = {issue.code for issue in validate_run(run)}
    assert {"metric.negative", "metric.spread"} <= codes


def test_duplicate_metric_key_is_an_error():
    run = _run()
    run.results[0].metrics = [_metric(key="a"), _metric(key="a", value=2.0)]
    assert "metric.duplicate" in {issue.code for issue in validate_run(run)}


def test_status_ok_without_metrics_is_an_error():
    run = _run()
    run.results[0].metrics = []
    assert "result.metrics.empty" in {issue.code for issue in validate_run(run)}


def test_empty_results_is_an_error():
    run = _run()
    run.results = []
    assert "results.empty" in {issue.code for issue in validate_run(run)}


def test_unknown_schema_is_a_warning_but_clean():
    run = _run(schema="open-benchmark.run/v0")
    report_issues = validate_run(run)
    assert {issue.code for issue in report_issues} == {"schema.version"}
    assert all(issue.severity == "warning" for issue in report_issues)


def test_validate_paths_reports_unreadable_file(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json}", encoding="utf-8")
    report = validate_paths([str(bad)])
    assert report.files == 1
    assert not report.ok
    assert report.issues[0].code == "file.unreadable"


def test_validate_paths_aggregates_a_clean_directory(tmp_path):
    path = tmp_path / "ok.jsonl"
    write_run(_run(), path)
    write_run(_run(), path)
    report = validate_paths([str(tmp_path)])
    assert report.ok
    assert report.runs == 2
    assert report.errors == 0
    assert report.to_dict()["ok"] is True


def test_warnings_do_not_fail_without_strict(tmp_path):
    path = tmp_path / "noisy.jsonl"
    run = _run()
    run.results[0].metrics = [_metric(spread_pct=50.0)]
    write_run(run, path)
    report = validate_paths([str(path)])
    assert report.ok
    assert report.warnings == 1
