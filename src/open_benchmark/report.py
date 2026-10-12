"""Rendering: human tables for a single run, markdown for the leaderboard."""

from __future__ import annotations

from open_benchmark.analysis import (
    aggregate_by_device,
    device_label,
    metric_info,
    runs_by_device,
)
from open_benchmark.schema import Run
from open_benchmark.stats import format_value


def format_run(run: Run, *, show_samples: bool = False) -> str:
    """Compact text report for one run (goes to stdout; JSON goes to --json)."""
    dev = run.device
    lines: list[str] = []
    cpu = dev.cpu or {}
    mem = dev.memory or {}
    lines.append(f"open-benchmark {run.schema}  run_id={run.run_id}  suite={run.suite}")
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
    suites_available = sorted({run.suite for run in runs})
    selected = [run for run in runs if run.suite == suite] if suite else runs
    if suite and not selected:
        return f"no runs for suite {suite!r} (have: {', '.join(suites_available)})"
    grouped = runs_by_device(selected)
    if not grouped:
        return "no runs with a device fingerprint found"

    values = aggregate_by_device(selected, bound=bound)
    keys = sorted({k for v in values.values() for k in v})
    if metrics:
        keys = [k for k in keys if any(m in k for m in metrics)]

    labels = {fp: device_label(group) for fp, group in grouped.items()}
    header = "| metric | " + " | ".join(f"{labels[fp]}<br>`{fp}`" for fp in grouped) + " |"
    sep = "|---" * (len(grouped) + 1) + "|"
    rows = [header, sep]
    for key in keys:
        cells = []
        for fp in grouped:
            value = values[fp].get(key)
            cells.append(f"{value:,.2f}" if value is not None else "-")
        rows.append(f"| `{key}` | " + " | ".join(cells) + " |")

    units = sorted({metric_info(selected, k)[0] for k in keys})
    pooled = sorted({run.suite for run in selected})
    shown_bound = bound if bound not in (None, "all") else "all"
    footer = [
        "",
        f"_{len(selected)} run(s) over {len(grouped)} device(s); values are per-device medians; "
        f"bound={shown_bound}; suites: {', '.join(pooled)}; units: {', '.join(u for u in units if u)}_",
    ]
    if len(pooled) > 1:
        footer.append(
            "_multiple suites pooled: workloads differ between suites, so pass `--suite <name>` "
            "for comparable numbers_"
        )
    return "\n".join(rows + footer)


def format_comparison(result: dict) -> str:
    """Human table for a :func:`open_benchmark.analysis.compare_sets` payload."""
    base = result["base"]
    target = result["target"]
    lines = [
        f"base    {base['device']}  {base['label']}  ({base['runs']} run(s))",
        f"target  {target['device']}  {target['label']}  ({target['runs']} run(s))",
        f"bound={result['bound']}  suite={result['suite'] or '-'}  "
        f"tolerance=±{result['tolerance_pct']:g}%",
        "",
        f"{'metric':<36} {'base':>14} {'target':>14} {'delta':>12} {'%':>9}  verdict",
        "-" * 96,
    ]
    for delta in result["deltas"]:
        change = "-" if delta["delta"] is None else f"{delta['delta']:+,.2f}"
        pct = "-" if delta["delta_pct"] is None else f"{delta['delta_pct']:+.2f}%"
        lines.append(
            f"{delta['key']:<36} {_num(delta['base']):>14} {_num(delta['target']):>14} "
            f"{change:>12} {pct:>9}  {delta['verdict']}"
        )
    counts = result["counts"]
    lines.append("-" * 96)
    lines.append(" ".join(f"{name}={counts[name]}" for name in ("better", "worse", "same", "new", "missing")))
    return "\n".join(lines)


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f}"

