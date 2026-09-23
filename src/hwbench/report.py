"""Rendering: human tables for a single run, markdown for the leaderboard."""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from hwbench.schema import Run
from hwbench.stats import format_value


def format_run(run: Run, *, show_samples: bool = False) -> str:
    """Compact text report for one run (goes to stdout; JSON goes to --json)."""
    dev = run.device
    lines: list[str] = []
    cpu = dev.cpu or {}
    mem = dev.memory or {}
    lines.append(f"hwbench {run.schema}  run_id={run.run_id}  suite={run.suite}")
    lines.append(
        f"device  {dev.fingerprint}  {dev.os} {dev.os_version} {dev.arch}  class={dev.machine_class}"
    )
    lines.append(
        f"cpu     {cpu.get('model') or '?'}  "
        f"({cpu.get('physical_cores')}C/{cpu.get('logical_cores')}T"
        + (f", l3={cpu.get('l3_kb')}KB" if cpu.get("l3_kb") else "")
        + ")"
    )
    total = mem.get("total_bytes")
    lines.append(
        f"memory  {total / 1024**3:.1f} GiB" if total else "memory  ?"
    )
    for disk in (dev.disks or [])[:4]:
        size = disk.get("size_bytes") or 0
        bus = disk.get("bus") or ""
        lines.append(
            f"disk    {disk.get('model')}  {size / 1024**3:.0f} GB  {disk.get('media')} {bus}"
        )
    lines.append("")
    lines.append(f"{'executor':<22} {'metric':<20} {'value':>16}  {'spread':>7}  bound")
    lines.append("-" * 82)
    for res in run.results:
        if res.status != "ok":
            lines.append(f"{res.name:<22} {res.status.upper():<20} {res.error[:40]:>16}")
            continue
        for i, metric in enumerate(res.metrics):
            name = res.name if i == 0 else ""
            spread = f"{metric.spread_pct:.1f}%" if metric.spread_pct is not None else "-"
            lines.append(
                f"{name:<22} {metric.key:<20} {format_value(metric.value, metric.unit):>16}  "
                f"{spread:>7}  {metric.bound}"
            )
        if show_samples:
            for metric in res.metrics:
                lines.append(f"{'':<22}   samples {metric.key}: {metric.samples}")
    counts = run.status_counts()
    lines.append("-" * 82)
    lines.append(f"ok={counts['ok']} skipped={counts['skipped']} error={counts['error']}")
    return "\n".join(lines)


def leaderboard(
    runs: list[Run],
    *,
    bound: str = "hardware",
    metrics: list[str] | None = None,
    suite: str | None = None,
) -> str:
    """Aggregate many runs into a markdown leaderboard keyed on device fingerprint."""
    if bound == "all":
        bound = None  # type: ignore[assignment]
    suites_available = sorted({run.suite for run in runs})
    if suite:
        runs = [run for run in runs if run.suite == suite]
        if not runs:
            return f"no runs for suite {suite!r} (have: {', '.join(suites_available)})"
    by_device: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        if run.device.fingerprint:
            by_device[run.device.fingerprint].append(run)
    if not by_device:
        return "no runs with a device fingerprint found"

    values: dict[str, dict[str, float]] = {}
    for fp, device_runs in by_device.items():
        per_metric: dict[str, list[float]] = defaultdict(list)
        for run in device_runs:
            for key, value in run.flat_metrics(bound=bound).items():
                per_metric[key].append(value)
        values[fp] = {k: median(v) for k, v in per_metric.items() if v}

    keys = sorted({k for v in values.values() for k in v})
    if metrics:
        keys = [k for k in keys if any(m in k for m in metrics)]

    labels = {fp: _device_label(runs) for fp, runs in by_device.items()}
    header = "| metric | " + " | ".join(f"{labels[fp]}<br>`{fp}`" for fp in by_device) + " |"
    sep = "|---" * (len(by_device) + 1) + "|"
    rows = [header, sep]
    for key in keys:
        cells = []
        for fp in by_device:
            value = values[fp].get(key)
            cells.append(f"{value:,.2f}" if value is not None else "-")
        rows.append(f"| `{key}` | " + " | ".join(cells) + " |")

    units = sorted({_unit_of(runs, k) for k in keys})
    pooled = sorted({run.suite for run in runs})
    footer = [
        "",
        f"_{len(runs)} run(s) over {len(by_device)} device(s); values are per-device medians; "
        f"bound={bound or 'all'}; suites: {', '.join(pooled)}; units: {', '.join(u for u in units if u)}_",
    ]
    if len(pooled) > 1:
        footer.append(
            "_multiple suites pooled: workloads differ between suites, so pass `--suite <name>` "
            "for comparable numbers_"
        )
    return "\n".join(rows + footer)


def _unit_of(runs: list[Run], key: str) -> str:
    for run in runs:
        for res in run.results:
            for metric in res.metrics:
                if f"{res.name}.{metric.key}" == key:
                    return metric.unit
    return ""


def _device_label(runs: list[Run]) -> str:
    device = runs[0].device
    cpu = (device.cpu or {}).get("model") or "unknown cpu"
    total = (device.memory or {}).get("total_bytes")
    mem = f" / {total / 1024**3:.0f} GiB" if total else ""
    return f"{cpu}{mem}"
