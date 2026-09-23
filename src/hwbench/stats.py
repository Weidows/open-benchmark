"""Tiny statistics helpers (no numpy, on purpose).

Repeated runs are the only way to get a trustworthy number out of a noisy
machine, so every executor samples N times and reports median + spread.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def median(xs: Sequence[float]) -> float:
    return _quantile(sorted(xs), 0.5)


def best(xs: Sequence[float], higher_is_better: bool = True) -> float:
    return max(xs) if higher_is_better else min(xs)


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def stdev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def mad(xs: Sequence[float]) -> float:
    """Median absolute deviation - robust spread for skewed samples."""
    m = median(xs)
    return median([abs(x - m) for x in xs])


def percentile(xs: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100)."""
    if not xs:
        raise ValueError("empty sample")
    if not 0 <= p <= 100:
        raise ValueError("p must be in 0..100")
    return _quantile(sorted(xs), p / 100.0)


def _quantile(sorted_xs: Sequence[float], q: float) -> float:
    if not sorted_xs:
        raise ValueError("empty sample")
    if len(sorted_xs) == 1:
        return float(sorted_xs[0])
    pos = q * (len(sorted_xs) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_xs[lo])
    frac = pos - lo
    return float(sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac)


def spread_pct(xs: Sequence[float], higher_is_better: bool = True) -> float:
    """Relative dispersion of a sample set, in percent of the reported value."""
    if len(xs) < 2:
        return 0.0
    ref = best(xs, higher_is_better)
    if ref == 0:
        return 0.0
    return round(abs(max(xs) - min(xs)) / abs(ref) * 100.0, 3)


def summarize(
    xs: Iterable[float],
    *,
    unit: str,
    key: str,
    higher_is_better: bool = True,
    bound: str = "hardware",
    aggregation: str = "median",
    notes: str = "",
):
    """Build a :class:`hwbench.schema.Metric` from raw samples."""
    from hwbench.schema import Metric

    samples = [float(x) for x in xs]
    if not samples:
        raise ValueError("no samples")
    if aggregation == "best":
        value = best(samples, higher_is_better)
    elif aggregation == "mean":
        value = mean(samples)
    elif aggregation == "single":
        value = samples[-1]
    else:
        value = median(samples)
    return Metric(
        key=key,
        value=round(value, 4),
        unit=unit,
        bound=bound,
        higher_is_better=higher_is_better,
        aggregation=aggregation,
        samples=[round(s, 4) for s in samples],
        n=len(samples),
        spread_pct=spread_pct(samples, higher_is_better),
        notes=notes,
    )


def format_value(value: float, unit: str) -> str:
    """Human-readable rendering used by the CLI/leaderboard."""
    if unit in ("MB/s", "GB/s"):
        return f"{value:,.2f} {unit}"
    if unit in ("IOPS",):
        return f"{value:,.0f} IOPS"
    if unit in ("us",):
        return f"{value:,.1f} us"
    if unit in ("ns",):
        return f"{value:,.2f} ns"
    if unit in ("GFLOPS", "GOPS"):
        return f"{value:,.2f} {unit}"
    return f"{value:,.3f} {unit}".rstrip()
