"""Pre-submission validation of run documents.

``submit`` copies JSONL into a leaderboard repository; this module is the gate
in front of it. It never trusts a file's self-description: the device
fingerprint is **re-derived** from the comparable fields (a mismatch means the
payload was edited, or the collector drifted), and every number must be finite,
unit-bearing and provenance-tagged. One bad run left unvalidated would poison
the leaderboard for everyone.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from open_benchmark.schema import BOUNDS, SCHEMA_VERSION, Device, Run, resolve_files

#: metric aggregation modes the schema documents
AGGREGATIONS = ("median", "best", "mean", "single")
#: result statuses the schema documents
STATUSES = ("ok", "skipped", "error")
#: units that can never be negative, so a negative value is a measurement bug
NON_NEGATIVE_UNITS = frozenset(
    {"MB/s", "GB/s", "IOPS", "us", "ns", "GFLOPS", "GOPS", "MHZ", "MHz", "%", "count", "passes/s"}
)
SCHEMA_PREFIX = "open-benchmark.run/"

ERROR = "error"
WARNING = "warning"


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    file: str = ""
    run_id: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "run_id": self.run_id,
        }


@dataclass
class ValidationReport:
    files: int = 0
    runs: int = 0
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == WARNING)

    @property
    def ok(self) -> bool:
        return self.errors == 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "files": self.files,
            "runs": self.runs,
            "errors": self.errors,
            "warnings": self.warnings,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def validate_run(
    run: Run,
    *,
    source: str = "",
    max_spread_pct: float = 10.0,
    known_suites: tuple[str, ...] = ("quick", "full"),
) -> list[Issue]:
    """Return every problem found in one run (empty list == clean)."""

    def issue(severity: str, code: str, message: str) -> Issue:
        return Issue(severity, code, message, file=source, run_id=run.run_id)

    issues: list[Issue] = []

    if not str(run.schema).startswith(SCHEMA_PREFIX):
        issues.append(issue(ERROR, "schema.unknown", f"unrecognised schema id {run.schema!r}"))
    elif run.schema != SCHEMA_VERSION:
        issues.append(
            issue(WARNING, "schema.version", f"schema {run.schema} != current {SCHEMA_VERSION}")
        )

    if not run.device.fingerprint:
        issues.append(issue(ERROR, "device.fingerprint.missing", "device fingerprint is empty"))
    else:
        recomputed = Device.from_dict(run.device.to_dict()).compute_fingerprint()
        if recomputed != run.device.fingerprint:
            issues.append(
                issue(
                    ERROR,
                    "device.fingerprint.mismatch",
                    f"fingerprint {run.device.fingerprint} does not match the hardware fields "
                    f"(recomputed {recomputed}); the payload was edited or the collector drifted",
                )
            )

    if known_suites and run.suite not in known_suites:
        issues.append(issue(WARNING, "suite.unknown", f"unknown suite {run.suite!r}"))

    if not run.results:
        issues.append(issue(ERROR, "results.empty", "run has no executor results"))

    for res in run.results:
        where = f"result {res.name!r}"
        if res.status not in STATUSES:
            issues.append(issue(ERROR, "result.status", f"{where}: unknown status {res.status!r}"))
        if res.status == "ok" and not res.metrics:
            issues.append(issue(ERROR, "result.metrics.empty", f"{where}: status ok but no metrics"))
        if res.status == "skipped" and not res.error:
            issues.append(issue(WARNING, "result.skip.reason", f"{where}: skipped without a reason"))

        seen: set[str] = set()
        for metric in res.metrics:
            label = f"{res.name}.{metric.key}"
            if metric.key in seen:
                issues.append(issue(ERROR, "metric.duplicate", f"{where}: duplicate metric {label}"))
            seen.add(metric.key)

            if metric.bound not in BOUNDS:
                issues.append(issue(ERROR, "metric.bound", f"{label}: unknown bound {metric.bound!r}"))
            if not isinstance(metric.value, (int, float)) or not math.isfinite(metric.value):
                issues.append(issue(ERROR, "metric.value", f"{label}: value is not a finite number"))
            elif metric.value < 0 and metric.unit in NON_NEGATIVE_UNITS:
                issues.append(
                    issue(ERROR, "metric.negative", f"{label}: negative value for {metric.unit} unit")
                )
            if not metric.unit:
                issues.append(issue(WARNING, "metric.unit", f"{label}: missing unit"))
            if metric.aggregation not in AGGREGATIONS:
                issues.append(
                    issue(
                        WARNING,
                        "metric.aggregation",
                        f"{label}: unknown aggregation {metric.aggregation!r}",
                    )
                )
            if metric.n != len(metric.samples):
                issues.append(
                    issue(
                        ERROR,
                        "metric.n",
                        f"{label}: n={metric.n} but {len(metric.samples)} raw samples",
                    )
                )
            if metric.n < 1:
                issues.append(issue(ERROR, "metric.n", f"{label}: n must be >= 1"))
            if metric.spread_pct is None:
                issues.append(issue(WARNING, "metric.spread", f"{label}: spread_pct missing"))
            elif metric.spread_pct > max_spread_pct:
                issues.append(
                    issue(
                        WARNING,
                        "metric.spread",
                        f"{label}: spread {metric.spread_pct:.1f}% > {max_spread_pct:.0f}% "
                        "(noisy; rerun or exclude)",
                    )
                )
    return issues


def validate_paths(
    paths: list[str | Path],
    *,
    max_spread_pct: float = 10.0,
    known_suites: tuple[str, ...] = ("quick", "full"),
) -> ValidationReport:
    """Validate every run reachable from ``paths`` (files, dirs or globs)."""
    report = ValidationReport()
    for path in resolve_files(paths):
        report.files += 1
        try:
            runs = _load_file(path)
        except Exception as exc:  # report, never abort the batch
            report.issues.append(
                Issue(ERROR, "file.unreadable", f"{type(exc).__name__}: {exc}", file=str(path))
            )
            continue
        report.runs += len(runs)
        for run in runs:
            report.issues.extend(
                validate_run(
                    run, source=str(path), max_spread_pct=max_spread_pct, known_suites=known_suites
                )
            )
    return report


def _load_file(path: Path) -> list[Run]:
    """Parse one file without importing the read_runs batch helper."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        return [Run.from_dict(item) for item in json.loads(text)]
    return [Run.from_dict(json.loads(line)) for line in text.splitlines() if line.strip()]


def format_report(report: ValidationReport) -> str:
    """Human-readable summary (the JSON form is ``report.to_dict()``)."""
    if not report.issues:
        return f"ok: {report.runs} run(s) across {report.files} file(s), no issues"
    lines = [f"{issue.severity.upper():<7} {issue.file or '-'}  {issue.code}: {issue.message}"
             for issue in report.issues]
    lines.append("-" * 72)
    lines.append(
        f"{report.runs} run(s) across {report.files} file(s): "
        f"ok={report.ok} errors={report.errors} warnings={report.warnings}"
    )
    return "\n".join(lines)
