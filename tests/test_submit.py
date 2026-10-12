"""Redaction: results must be publishable without leaking identifiers."""

from __future__ import annotations

import json
from pathlib import Path

from open_benchmark.schema import Device, Metric, Result, Run
from open_benchmark.submit import submit


def _device_with_identifiers() -> Device:
    device = Device(
        os="windows",
        arch="AMD64",
        hostname="my-secret-desktop",
        cpu={"model": "AMD Ryzen 9 5950X", "physical_cores": 16, "logical_cores": 32},
        memory={"total_bytes": 64 * 1024**3, "modules": [{"locator": "DIMM 0", "size_bytes": 32 * 1024**3}]},
        disks=[{"model": "Samsung 990 PRO", "size_bytes": 2 * 10**12, "media": "ssd", "serial": "S1234ABCD"}],
        system={"model": "X570"},
        raw={"cim": {"serial": "leaky"}},
    )
    device.compute_fingerprint()
    return device


def _valid_run(**overrides) -> Run:
    """A minimal but schema-valid run: submit validates before touching git."""
    return Run(
        device=_device_with_identifiers(),
        results=[
            Result(
                name="cpu.hash",
                backend="openssl",
                metrics=[Metric(key="sha256_mbs", value=3000.0, unit="MB/s", spread_pct=1.0)],
            )
        ],
        **overrides,
    )



def test_redact_keeps_hardware_but_drops_identifiers():
    device = _device_with_identifiers()
    fingerprint = device.fingerprint
    device.redact()
    assert device.hostname == ""
    assert device.raw == {}
    assert "serial" not in device.disks[0]
    assert device.cpu["model"] == "AMD Ryzen 9 5950X"
    assert device.fingerprint == fingerprint  # fingerprint must not depend on redacted fields


def test_submit_redacts_before_writing(tmp_path, monkeypatch):
    """submit() must write redacted JSONL even if the input file still has identifiers."""
    run = _valid_run()
    source = tmp_path / "run.jsonl"
    source.write_text(json.dumps(run.to_dict()), encoding="utf-8")

    repo = tmp_path / "results-repo"
    (repo / ".git").mkdir(parents=True)

    monkeypatch.setattr("open_benchmark.submit.shutil.which", lambda name: "/usr/bin/gh")
    calls: list[list[str]] = []

    class _Proc:
        stdout = "https://github.com/Weidows/open-benchmark-results/pull/1"
        returncode = 0

    def fake_run(cmd, check=True):
        calls.append(cmd)
        if cmd[:2] == ["git", "-C"] and "remote" in cmd:
            return _Proc()
        return _Proc()

    monkeypatch.setattr("open_benchmark.submit._run", fake_run)
    url = submit([source], repo_dir=repo, dry_run=False)
    assert url.endswith("/pull/1")
    written = list((repo / "results").glob("*.jsonl"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text(encoding="utf-8").strip())
    assert payload["device"]["hostname"] == ""
    assert payload["device"]["raw"] == {}
    assert "serial" not in payload["device"]["disks"][0]
    # the destination path is committed, not the local source file
    assert any(str(written[0].relative_to(repo)) in call for call in calls if "add" in call)


def test_submit_dry_run_does_not_touch_git(tmp_path):
    source = tmp_path / "run.jsonl"
    source.write_text(json.dumps(_valid_run().to_dict()), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    plan = submit([source], repo_dir=repo, dry_run=True)
    assert plan.startswith("[dry-run]")
    assert not (repo / "results").exists()


def test_submit_rejects_non_checkout(tmp_path):
    source = tmp_path / "run.jsonl"
    source.write_text(json.dumps(_valid_run().to_dict()), encoding="utf-8")
    try:
        submit([source], repo_dir=Path(tmp_path))
    except RuntimeError as exc:
        assert "not a git checkout" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_submit_refuses_a_run_that_fails_validation(tmp_path):
    """An invalid run must be rejected before any git work, unless --force."""
    run = _valid_run()
    run.results[0].metrics = [Metric(key="x", value=1.0, unit="MB/s", n=9, samples=[1.0])]
    source = tmp_path / "run.jsonl"
    source.write_text(json.dumps(run.to_dict()), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    try:
        submit([source], repo_dir=repo)
    except RuntimeError as exc:
        assert "refusing to submit" in str(exc)
        assert "metric.n" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")
    # --force bypasses the gate and reaches the dry-run plan
    assert submit([source], repo_dir=repo, dry_run=True, force=True).startswith("[dry-run]")
