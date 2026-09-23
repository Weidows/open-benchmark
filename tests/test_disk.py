"""Raw I/O layer tests.

These exist because a size/buffer mismatch in the unbuffered path fails only at
runtime with a Windows error (1784) or EINVAL on Linux - exactly the kind of bug
that a "it ran, so it works" check misses.
"""

from __future__ import annotations

import sys

import pytest

from open_benchmark.executors.base import Context
from open_benchmark.executors.disk import BLOCK_RAND, DiskRandom, DiskSequential, open_raw

pytestmark = pytest.mark.skipif(sys.platform == "darwin", reason="O_DIRECT semantics differ on macOS")


def test_open_raw_reports_backend_and_honours_alignment(tmp_path):
    path = tmp_path / "raw.bin"
    raw = open_raw(path, unbuffered=True, block=BLOCK_RAND)
    try:
        assert raw.backend.startswith(("win32-", "posix-"))
        raw.preallocate(8 * 1024 * 1024)  # uses the large scratch buffer
        written = raw.write_at(4096, BLOCK_RAND)
        assert written == BLOCK_RAND
        assert raw.read_at(4096, BLOCK_RAND) == BLOCK_RAND
        raw.flush()
        assert path.stat().st_size >= 8 * 1024 * 1024
    finally:
        raw.close(delete=True)
    assert not path.exists()


def test_preallocate_grows_beyond_single_block(tmp_path):
    """Regression: preallocation must not write more than its scratch buffer."""
    path = tmp_path / "grow.bin"
    raw = open_raw(path, unbuffered=False, block=BLOCK_RAND)
    try:
        raw.preallocate(9 * 1024 * 1024)
        assert path.stat().st_size == 9 * 1024 * 1024
    finally:
        raw.close(delete=True)


def test_disk_seq_runs_and_cleans_up(tmp_path):
    ctx = Context(suite="quick", repeat=1, scale=0.01, workdir=tmp_path, disk_path=tmp_path)
    result = DiskSequential().run(ctx)
    assert result.status == "ok", result.error
    read = result.metric("seq_read_mbs")
    write = result.metric("seq_write_mbs")
    assert read and write
    assert read.value > 0 and write.value > 0
    assert read.bound == "hardware"
    assert not list(tmp_path.glob("open-benchmark-seq-*.bin"))


def test_disk_random_runs_with_small_region(tmp_path):
    ctx = Context(suite="quick", repeat=1, scale=0.005, workdir=tmp_path, disk_path=tmp_path)
    random_exec = DiskRandom()
    random_exec.region_mb = 32
    result = random_exec.run(ctx)
    assert result.status == "ok", result.error
    assert result.metric("rand_read_iops").value > 0
    assert result.metric("rand_write_iops").value > 0
    assert result.metric("rand_read_p99_us").value >= result.metric("rand_read_p50_us").value
    assert not list(tmp_path.glob("open-benchmark-rand-*.bin"))
