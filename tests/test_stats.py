"""Stats helpers."""

from __future__ import annotations

import pytest

from open_benchmark.stats import best, mad, median, percentile, spread_pct, stdev, summarize


def test_median_odd_and_even():
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5


def test_percentile_nearest_rank():
    xs = list(range(1, 101))
    assert percentile(xs, 50) == 50.5
    assert percentile(xs, 99) == 99.01
    assert percentile(xs, 0) == 1
    assert percentile(xs, 100) == 100


def test_best_respects_direction():
    assert best([1, 5, 3], higher_is_better=True) == 5
    assert best([1, 5, 3], higher_is_better=False) == 1


def test_spread_and_mad():
    assert spread_pct([100, 100]) == 0
    assert spread_pct([90, 100]) == pytest.approx(10.0, abs=0.01)
    assert mad([1, 2, 3, 100]) == 1


def test_stdev_single_sample():
    assert stdev([42]) == 0.0


def test_summarize_builds_metric_with_provenance():
    metric = summarize(
        [10.0, 12.0, 11.0],
        key="x",
        unit="MB/s",
        aggregation="median",
        bound="hardware",
    )
    assert metric.value == 11.0
    assert metric.n == 3
    assert metric.samples == [10.0, 12.0, 11.0]
    assert metric.spread_pct == pytest.approx(16.67, abs=0.01)
    assert metric.bound == "hardware"


def test_summarize_rejects_unknown_bound():
    with pytest.raises(ValueError):
        summarize([1.0], key="x", unit="MB/s", bound="magic")
