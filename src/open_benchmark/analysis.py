"""Cross-run aggregation and comparison.

``leaderboard`` answers "how do many machines rank". ``compare`` answers the
question an agent asks right after a change: *is this run better or worse than
that one, and by how much*. Both reduce runs to one median per
(device, metric) **before** doing anything else - ranking before averaging is
a well-known way to get the wrong answer on noisy benchmark data.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from open_benchmark.schema import Run

#: verdicts a metric delta can carry
BETTER = "better"
WORSE = "worse"
SAME = "same"
NEW = "new"  # only present in the target side
MISSING = "missing"  # only present in the base side


def runs_by_device(runs: list[Run], *, suite: str | None = None) -> dict[str, list[Run]]:
    """Group runs by device fingerprint, dropping runs without one."""
    if suite:
        runs = [run for run in runs if run.suite == suite]
    grouped: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        if run.device.fingerprint:
            grouped[run.device.fingerprint].append(run)
    return dict(grouped)


def aggregate_by_device(
    runs: list[Run], *, bound: str | None = "hardware", suite: str | None = None
) -> dict[str, dict[str, float]]:
    """Collapse runs into ``{fingerprint: {"<executor>.<metric>": median}}``.

    ``bound="all"`` (or ``None``) keeps every provenance class; ``suite``
    filters to a single workload so quick and full numbers never mix.
    """
    if bound == "all":
        bound = None
    grouped = runs_by_device(runs, suite=suite)
    out: dict[str, dict[str, float]] = {}
    for fingerprint, device_runs in grouped.items():
        per_metric: dict[str, list[float]] = defaultdict(list)
        for run in device_runs:
            for key, value in run.flat_metrics(bound=bound).items():
                per_metric[key].append(value)
        out[fingerprint] = {key: median(values) for key, values in per_metric.items() if values}
    return out


def device_label(runs: list[Run]) -> str:
    """Short human label for a device: CPU model + rounded RAM."""
    device = runs[0].device
    cpu = (device.cpu or {}).get("model") or "unknown cpu"
    total = (device.memory or {}).get("total_bytes")
    mem = f" / {total / 1024**3:.0f} GiB" if total else ""
    return f"{cpu}{mem}"


def metric_info(runs: list[Run], key: str) -> tuple[str, bool]:
    """``(unit, higher_is_better)`` for an ``<executor>.<metric>`` key."""
    for run in runs:
        for res in run.results:
            for metric in res.metrics:
                if f"{res.name}.{metric.key}" == key:
                    return metric.unit, metric.higher_is_better
    return "", True


def pick_device(aggregated: dict[str, dict[str, float]], selector: str | None = None) -> str:
    """Choose one fingerprint out of an aggregated side.

    A single device is picked automatically; otherwise the caller must pass an
    index or a unique fingerprint prefix, so a comparison is never silently run
    against the wrong machine.
    """
    fingerprints = list(aggregated)
    if not fingerprints:
        raise KeyError("no devices with a fingerprint found in the given runs")
    if selector is None:
        if len(fingerprints) == 1:
            return fingerprints[0]
        raise KeyError(
            "multiple devices present, pick one with --base-device/--target-device: "
            + ", ".join(fingerprints)
        )
    selector = selector.strip()
    if selector.isdigit():
        index = int(selector)
        if not 0 <= index < len(fingerprints):
            raise KeyError(f"device index {index} out of range 0..{len(fingerprints) - 1}")
        return fingerprints[index]
    matches = [fp for fp in fingerprints if fp.startswith(selector)]
    if not matches:
        raise KeyError(f"no device fingerprint matches {selector!r}")
    if len(matches) > 1:
        raise KeyError(f"ambiguous fingerprint prefix {selector!r}: {', '.join(matches)}")
    return matches[0]


@dataclass
class Delta:
    """One metric compared between the base and target device."""

    key: str
    unit: str
    higher_is_better: bool
    base: float | None
    target: float | None
    delta: float | None
    delta_pct: float | None
    verdict: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
            "base": self.base,
            "target": self.target,
            "delta": None if self.delta is None else round(self.delta, 4),
            "delta_pct": None if self.delta_pct is None else round(self.delta_pct, 3),
            "verdict": self.verdict,
        }


def _verdict(
    base: float | None, target: float | None, higher_is_better: bool, tolerance_pct: float
) -> str:
    if base is None:
        return NEW
    if target is None:
        return MISSING
    if base == 0:
        return SAME
    delta_pct = (target - base) / abs(base) * 100.0
    if abs(delta_pct) <= tolerance_pct:
        return SAME
    improved = (delta_pct > 0) == higher_is_better
    return BETTER if improved else WORSE


def compare_sets(
    base_runs: list[Run],
    target_runs: list[Run],
    *,
    base_device: str | None = None,
    target_device: str | None = None,
    bound: str | None = "hardware",
    suite: str | None = None,
    metrics: list[str] | None = None,
    tolerance_pct: float = 5.0,
) -> dict:
    """Compare one device from ``base_runs`` against one from ``target_runs``.

    Returns a JSON-ready mapping: ``base`` / ``target`` metadata plus a sorted
    ``deltas`` list, each carrying a verdict that already accounts for whether
    higher is better for that metric and for the noise ``tolerance_pct``.
    """
    base_agg = aggregate_by_device(base_runs, bound=bound, suite=suite)
    target_agg = aggregate_by_device(target_runs, bound=bound, suite=suite)
    base_fp = pick_device(base_agg, base_device)
    target_fp = pick_device(target_agg, target_device)

    base_values = base_agg[base_fp]
    target_values = target_agg[target_fp]
    keys = sorted(set(base_values) | set(target_values))
    if metrics:
        keys = [key for key in keys if any(fragment in key for fragment in metrics)]

    deltas: list[Delta] = []
    for key in keys:
        base = base_values.get(key)
        target = target_values.get(key)
        unit, higher_is_better = metric_info(base_runs + target_runs, key)
        delta = None if base is None or target is None else target - base
        delta_pct = None if delta is None or not base else delta / abs(base) * 100.0
        deltas.append(
            Delta(
                key=key,
                unit=unit,
                higher_is_better=higher_is_better,
                base=base,
                target=target,
                delta=delta,
                delta_pct=delta_pct,
                verdict=_verdict(base, target, higher_is_better, tolerance_pct),
            )
        )

    counts = dict.fromkeys((BETTER, WORSE, SAME, NEW, MISSING), 0)
    for delta in deltas:
        counts[delta.verdict] += 1

    return {
        "base": {
            "device": base_fp,
            "label": device_label(_runs_for(base_runs, base_fp)),
            "runs": len(_runs_for(base_runs, base_fp)),
        },
        "target": {
            "device": target_fp,
            "label": device_label(_runs_for(target_runs, target_fp)),
            "runs": len(_runs_for(target_runs, target_fp)),
        },
        "bound": bound or "all",
        "suite": suite,
        "tolerance_pct": tolerance_pct,
        "counts": counts,
        "deltas": [delta.to_dict() for delta in deltas],
    }


def _runs_for(runs: list[Run], fingerprint: str) -> list[Run]:
    matched = [run for run in runs if run.device.fingerprint == fingerprint]
    return matched or runs
