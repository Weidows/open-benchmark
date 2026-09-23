"""Memory executors.

CPython bottoms out in libc for bulk memory work, so these executors call the
same primitives the interpreter itself uses - with two refinements learned the
hard way:

* ``bytes[:]`` returns the *same object* (immutable, full slice), so it measures
  nothing; a real copy needs a destination buffer.
* freshly mapped pages fault on first touch, which dominates a timed pass, so
  every buffer gets a warm-up pass before sampling.

Latency measurements are not possible from Python (interpreter overhead exceeds
DRAM latency); use ``native.memory`` for those. Anything not measuring hardware
is tagged ``bound="interpreter"``.
"""

from __future__ import annotations

import array
import ctypes
import random
import sys
import time

from hwbench.executors.base import BaseExecutor, Context, aligned_buffer, new_buffer
from hwbench.schema import Result
from hwbench.stats import summarize

_LIBC = None


def _libc():
    """ucrtbase on Windows, the process image elsewhere (libc memcpy)."""
    global _LIBC
    if _LIBC is None:
        lib = ctypes.CDLL("ucrtbase" if sys.platform.startswith("win") else None)
        lib.memcpy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        lib.memcpy.restype = ctypes.c_void_p
        _LIBC = lib
    return _LIBC


def memcpy(dst, src, size: int) -> None:
    _libc().memcpy(dst, src, ctypes.c_size_t(size))


def _gbs(size_bytes: int, seconds: float) -> float:
    return size_bytes / (1024**3) / seconds if seconds > 0 else 0.0


class CopyBandwidth(BaseExecutor):
    """memcpy bandwidth (read + write traffic) via libc memcpy."""

    name = "memory.copy"
    tags = ("memory", "native-lib")

    def run(self, ctx: Context) -> Result:
        size = ctx.bytes_for(256)
        src = (ctypes.c_char * size)()
        dst = (ctypes.c_char * size)()
        memcpy(dst, src, size)  # warm-up: fault in destination pages
        samples: list[float] = []
        for _ in range(max(1, ctx.repeat)):
            t0 = time.perf_counter()
            memcpy(dst, src, size)
            samples.append(_gbs(size, time.perf_counter() - t0))
        return Result(
            name=self.name,
            backend="libc memcpy via ctypes (page-warmed)",
            params={"buffer_mb": round(size / (1024 * 1024), 1)},
            metrics=[
                summarize(
                    samples,
                    key="copy_gbs",
                    unit="GB/s",
                    aggregation="best",
                    notes="counts read+write traffic; 30 GB/s here means 15 GB/s read + 15 GB/s write",
                )
            ],
        )


class ScanBandwidth(BaseExecutor):
    """Read-only bandwidth via bytes.count (libc memchr)."""

    name = "memory.scan"
    tags = ("memory", "native-lib")

    def run(self, ctx: Context) -> Result:
        size = ctx.bytes_for(256)
        buf = new_buffer(size, fill=0x11)
        buf.count(0)  # warm-up
        samples: list[float] = []
        for _ in range(max(1, ctx.repeat)):
            t0 = time.perf_counter()
            hits = buf.count(0x00)
            samples.append(_gbs(len(buf), time.perf_counter() - t0))
            assert hits == 0
        return Result(
            name=self.name,
            backend="cpython bytes.count (libc memchr)",
            params={"buffer_mb": round(size / (1024 * 1024), 1)},
            metrics=[
                summarize(
                    samples,
                    key="read_gbs",
                    unit="GB/s",
                    aggregation="best",
                    notes=(
                        "memchr is byte-serial in practice, so this is lower than the "
                        "vectorised native kernels"
                    ),
                )
            ],
        )


class FillBandwidth(BaseExecutor):
    """Write bandwidth into an anonymous mapping (libc memcpy into the mapping)."""

    name = "memory.fill"
    tags = ("memory", "native-lib")

    def run(self, ctx: Context) -> Result:
        size = ctx.bytes_for(256)
        block = new_buffer(size, fill=0x7F)
        mm = aligned_buffer(size)
        samples: list[float] = []
        try:
            mm[:] = block  # warm-up: fault in the mapping
            for _ in range(max(1, ctx.repeat)):
                t0 = time.perf_counter()
                mm[:] = block
                samples.append(_gbs(size, time.perf_counter() - t0))
        finally:
            mm.close()
        return Result(
            name=self.name,
            backend="mmap slice assignment (libc memcpy)",
            params={"buffer_mb": round(size / (1024 * 1024), 1)},
            metrics=[summarize(samples, key="write_gbs", unit="GB/s", aggregation="best")],
        )


class PythonRandomAccess(BaseExecutor):
    """Random access with a real working set (interpreter-bound, for reference)."""

    name = "memory.python_random"
    tags = ("memory", "interpreter")

    def run(self, ctx: Context) -> Result:
        size_mb = max(32, int(128 * ctx.scale))
        n = size_mb * 1024 * 1024 // 8
        data = array.array("q", range(n))  # compact 8-byte slots, real working set
        rnd = random.Random(1234)
        ops = 1_000_000
        idx = [rnd.randrange(n) for _ in range(4096)]
        acc = 0
        t0 = time.perf_counter()
        for i in range(ops):
            acc += data[idx[i & 4095]]
        seconds = time.perf_counter() - t0
        ns_per_op = seconds / ops * 1e9
        return Result(
            name=self.name,
            backend=f"cpython {__import__('platform').python_version()}",
            params={"working_set_mb": size_mb, "ops": ops},
            metrics=[
                summarize(
                    [ns_per_op],
                    key="ns_per_access",
                    unit="ns",
                    aggregation="single",
                    higher_is_better=False,
                    bound="interpreter",
                    notes="interpreter overhead dominates DRAM latency; use native.memory for latency",
                ),
                summarize(
                    [float(acc % 7)], key="checksum", unit="count", aggregation="single", bound="interpreter"
                ),
            ],
        )
