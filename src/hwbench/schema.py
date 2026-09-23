"""Machine-readable result schema.

Everything an agent needs to reason about a run lives here: a stable schema id,
a device fingerprint, and per-metric provenance (``bound`` tells you whether the
number measures hardware or the interpreter that produced it).

The schema is intentionally stdlib-only and JSON-native: one run == one JSON
object, many runs == JSONL, which is what leaderboard aggregation consumes.
"""

from __future__ import annotations

import hashlib
import json
import platform
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "hwbench.run/v1"

#: How a metric was produced. Agents/leaderboards must filter on this:
#: - ``hardware``      real device throughput measured by native code (syscalls, OpenSSL, zlib)
#: - ``interpreter``   dominated by the Python interpreter, only comparable to other Python runs
#: - ``mixed``         native library + interpreter glue; comparable with care
BOUNDS = ("hardware", "interpreter", "mixed")


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Metric:
    """One measured number, with provenance and dispersion."""

    key: str
    value: float
    unit: str
    bound: str = "hardware"
    higher_is_better: bool = True
    aggregation: str = "median"
    samples: list[float] = field(default_factory=list)
    n: int = 0
    spread_pct: float | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.bound not in BOUNDS:
            raise ValueError(f"bound must be one of {BOUNDS}, got {self.bound!r}")
        if not self.samples:
            self.samples = [self.value]
        if not self.n:
            self.n = len(self.samples)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Metric":
        return _from_dict(cls, data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Result:
    """One executor run: a group of metrics sharing parameters and a backend."""

    name: str
    status: str = "ok"  # ok | skipped | error
    error: str = ""
    backend: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    metrics: list[Metric] = field(default_factory=list)
    duration_s: float = 0.0
    tool_versions: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Result":
        data = dict(data)
        data["metrics"] = [Metric.from_dict(m) for m in data.get("metrics", [])]
        return _from_dict(cls, data)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["metrics"] = [m.to_dict() for m in self.metrics]
        return out

    def metric(self, key: str) -> Metric | None:
        return next((m for m in self.metrics if m.key == key), None)


@dataclass
class Device:
    """Hardware fingerprint: the thing a leaderboard row is keyed on."""

    fingerprint: str = ""
    os: str = ""
    os_version: str = ""
    arch: str = ""
    hostname: str = ""
    machine_class: str = "unknown"  # physical | vm | container | unknown
    cpu: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    disks: list[dict[str, Any]] = field(default_factory=list)
    gpus: list[dict[str, Any]] = field(default_factory=list)
    system: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def compute_fingerprint(self) -> str:
        """Stable id over the fields that make results comparable.

        Deliberately excludes hostname / serial numbers / timestamps: two
        identical machines must land in the same leaderboard row.
        """
        cpu = self.cpu or {}
        memory = self.memory or {}
        modules = [
            {
                "size_bytes": m.get("size_bytes"),
                "speed_mtps": m.get("speed_mtps") or m.get("configured_speed_mtps"),
            }
            for m in sorted(memory.get("modules", []), key=lambda m: str(m.get("locator", "")))
        ]
        disks = sorted(
            f"{d.get('model')}|{d.get('size_bytes')}|{d.get('media')}" for d in (self.disks or [])
        )
        key = {
            "os": self.os,
            "arch": self.arch,
            "cpu": cpu.get("model"),
            "physical_cores": cpu.get("physical_cores"),
            "logical_cores": cpu.get("logical_cores"),
            "mem_total": memory.get("total_bytes"),
            "mem_modules": modules,
            "disks": disks,
            "system_model": (self.system or {}).get("model"),
        }
        blob = json.dumps(key, sort_keys=True, ensure_ascii=False, default=str)
        self.fingerprint = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
        return self.fingerprint

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Device":
        return _from_dict(cls, data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Run:
    """A complete benchmark session; one line in a JSONL results file."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    schema: str = SCHEMA_VERSION
    suite: str = "quick"
    started_at: str = field(default_factory=_utc)
    ended_at: str = ""
    device: Device = field(default_factory=Device)
    results: list[Result] = field(default_factory=list)
    env: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Run":
        data = dict(data)
        data["device"] = Device.from_dict(data.get("device", {}))
        data["results"] = [Result.from_dict(r) for r in data.get("results", [])]
        return _from_dict(cls, data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "suite": self.suite,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "device": self.device.to_dict(),
            "env": self.env,
            "results": [r.to_dict() for r in self.results],
            "notes": self.notes,
        }

    # ---- helpers ---------------------------------------------------------
    def flat_metrics(self, bound: str | None = "hardware") -> dict[str, float]:
        """``{"executor.metric": value}``, filtered by provenance."""
        out: dict[str, float] = {}
        for res in self.results:
            if res.status != "ok":
                continue
            for met in res.metrics:
                if bound is not None and met.bound != bound:
                    continue
                out[f"{res.name}.{met.key}"] = met.value
        return out

    def status_counts(self) -> dict[str, int]:
        counts = {"ok": 0, "skipped": 0, "error": 0}
        for res in self.results:
            counts[res.status] = counts.get(res.status, 0) + 1
        return counts


def _from_dict(cls: type, data: dict[str, Any]):
    """Build a dataclass from a dict, ignoring unknown keys (forward compatible)."""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


# ---- io ------------------------------------------------------------------


def write_run(run: Run, path: str | Path | None, *, jsonl: bool = True, pretty: bool = True) -> str:
    """Append (jsonl) or write a run. ``path`` may be ``-`` for stdout.

    Returns the serialized payload so callers can print it without re-encoding.
    """
    payload = json.dumps(run.to_dict(), ensure_ascii=False, indent=None if jsonl else 2)
    if path is None or str(path) == "-":
        return payload
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if jsonl else "w", encoding="utf-8") as fh:
        fh.write(payload + ("\n" if jsonl else ""))
    return payload


def read_runs(paths: list[str | Path]) -> list[Run]:
    """Read runs from JSONL / JSON files or directories."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.jsonl")) + sorted(p.rglob("*.json")))
        elif p.exists():
            files.append(p)
        else:  # treat as glob
            files.extend(sorted(Path().glob(str(raw))))
    runs: list[Run] = []
    for f in files:
        text = f.read_text(encoding="utf-8").strip()
        if not text:
            continue
        if text.startswith("["):  # array of runs
            for item in json.loads(text):
                runs.append(Run.from_dict(item))
            continue
        for line in text.splitlines():
            line = line.strip()
            if line:
                runs.append(Run.from_dict(json.loads(line)))
    return runs


def runtime_env() -> dict[str, Any]:
    """Runtime context that explains a result (interpreter, machine name)."""
    return {
        "python": platform.python_version(),
        "python_impl": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpus": _cpu_count(),
    }


def _cpu_count() -> int:
    import os

    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except AttributeError:
        return os.cpu_count() or 1
