"""CPU executors.

The high-value trick here is that CPython already links native, SIMD-optimised
C code: OpenSSL (``hashlib``) and zlib. Hashing/compressing a large buffer with
a single call measures *real* CPU throughput with no compiler and no external
tool, and those calls release the GIL, so thread scaling is meaningful too.
Pure-Python loops are kept but tagged ``bound="interpreter"`` so leaderboards
can exclude them.
"""

from __future__ import annotations

import hashlib
import platform
import ssl
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

from open_benchmark.executors.base import BaseExecutor, Context, new_buffer
from open_benchmark.schema import Result
from open_benchmark.stats import summarize


def _openssl_version() -> str:
    try:
        return ssl.OPENSSL_VERSION
    except Exception:  # pragma: no cover
        return "unknown"


def _zlib_version() -> str:
    return getattr(zlib, "ZLIB_RUNTIME_VERSION", getattr(zlib, "Z_SOLVER", "unknown"))


def _throughput_mbs(size_bytes: int, seconds: float) -> float:
    return size_bytes / (1024 * 1024) / seconds if seconds > 0 else 0.0


class HashThroughput(BaseExecutor):
    """SHA-256 / BLAKE2b throughput (OpenSSL / libb2, single core)."""

    name = "cpu.hash"
    tags = ("cpu", "native-lib", "single-core")

    def run(self, ctx: Context) -> Result:
        buf = new_buffer(ctx.bytes_for(64))
        mb = len(buf) / (1024 * 1024)
        samples: dict[str, list[float]] = {"sha256_mbs": [], "blake2b_mbs": []}
        for _ in range(max(1, ctx.repeat)):
            t0 = time.perf_counter()
            hashlib.sha256(buf).digest()
            samples["sha256_mbs"].append(_throughput_mbs(len(buf), time.perf_counter() - t0))

            t0 = time.perf_counter()
            hashlib.blake2b(buf).digest()
            samples["blake2b_mbs"].append(_throughput_mbs(len(buf), time.perf_counter() - t0))
        return Result(
            name=self.name,
            backend=f"openssl {_openssl_version()}",
            params={"buffer_mb": round(mb, 2), "repeat": ctx.repeat},
            metrics=[
                summarize(
                    samples["sha256_mbs"],
                    key="sha256_mbs",
                    unit="MB/s",
                    aggregation="best",
                    notes="single-call hashlib over a large buffer: native OpenSSL, not interpreter-bound",
                ),
                summarize(samples["blake2b_mbs"], key="blake2b_mbs", unit="MB/s", aggregation="best"),
            ],
            tool_versions={"openssl": _openssl_version(), "python": platform.python_version()},
        )


class CompressionThroughput(BaseExecutor):
    """zlib compress/decompress throughput (native zlib)."""

    name = "cpu.compress"
    tags = ("cpu", "native-lib", "single-core")

    def run(self, ctx: Context) -> Result:
        buf = new_buffer(ctx.bytes_for(64), fill=0x5A)
        compressed = zlib.compress(buf, 6)
        ratio = len(compressed) / len(buf)
        comp_samples: list[float] = []
        decomp_samples: list[float] = []
        for _ in range(max(1, ctx.repeat)):
            t0 = time.perf_counter()
            zlib.compress(buf, 6)
            comp_samples.append(_throughput_mbs(len(buf), time.perf_counter() - t0))

            t0 = time.perf_counter()
            zlib.decompress(compressed)
            decomp_samples.append(_throughput_mbs(len(buf), time.perf_counter() - t0))
        return Result(
            name=self.name,
            backend=f"zlib {zlib.ZLIB_VERSION}",
            params={"buffer_mb": round(len(buf) / (1024 * 1024), 2), "level": 6, "ratio": round(ratio, 4)},
            metrics=[
                summarize(comp_samples, key="compress_mbs", unit="MB/s", aggregation="best"),
                summarize(decomp_samples, key="decompress_mbs", unit="MB/s", aggregation="best"),
            ],
            tool_versions={"zlib": zlib.ZLIB_VERSION},
        )


class ThreadScaling(BaseExecutor):
    """How well hashing scales from 1 to N threads (real multi-core signal)."""

    name = "cpu.thread_scale"
    tags = ("cpu", "native-lib", "multi-core")

    def run(self, ctx: Context) -> Result:
        per_thread = ctx.bytes_for(16)
        threads = ctx.cpu_count
        blobs = [new_buffer(per_thread, fill=0xA5 + i) for i in range(threads)]

        def worker(blob: bytes) -> float:
            t0 = time.perf_counter()
            hashlib.sha256(blob).digest()
            return _throughput_mbs(len(blob), time.perf_counter() - t0)

        # warm up so the first (cold) run does not distort the scaling curve
        hashlib.sha256(blobs[0]).digest()

        single: list[float] = []
        for _ in range(max(1, ctx.repeat)):
            single.append(worker(blobs[0]))

        parallel: list[float] = []
        for _ in range(max(1, ctx.repeat)):
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=threads) as pool:
                list(pool.map(worker, blobs))
            total_mb = len(blobs) * len(blobs[0]) / (1024 * 1024)
            parallel.append(total_mb / (time.perf_counter() - t0))

        best_single = max(single)
        best_parallel = max(parallel)
        efficiency = best_parallel / (best_single * threads) * 100 if best_single else 0.0
        return Result(
            name=self.name,
            backend=f"openssl {_openssl_version()}",
            params={"threads": threads, "per_thread_mb": round(len(blobs[0]) / (1024 * 1024), 2)},
            metrics=[
                summarize(single, key="single_thread_mbs", unit="MB/s", aggregation="best"),
                summarize(parallel, key="all_threads_mbs", unit="MB/s", aggregation="best"),
                summarize(
                    [efficiency],
                    key="scale_efficiency_pct",
                    unit="%",
                    aggregation="single",
                    notes=(
                        "all_threads / (single_thread * threads) * 100; "
                        "<60% hints at power or thermal limits"
                    ),
                ),
            ],
            tool_versions={"openssl": _openssl_version()},
        )


class PrimeSieve(BaseExecutor):
    """Pure-Python sieve: reported for completeness, interpreter-bound."""

    name = "cpu.python_sieve"
    tags = ("cpu", "interpreter")

    limit = 400_000

    def run(self, ctx: Context) -> Result:
        passes: list[float] = []
        count = 0
        for _ in range(max(1, ctx.repeat)):
            t0 = time.perf_counter()
            count = self._sieve(self.limit)
            passes.append(1.0 / (time.perf_counter() - t0))
        return Result(
            name=self.name,
            backend=f"cpython {platform.python_version()}",
            params={"limit": self.limit},
            metrics=[
                summarize(
                    passes,
                    key="passes_per_s",
                    unit="passes/s",
                    aggregation="best",
                    bound="interpreter",
                    notes="measures CPython loop speed; comparable only against other Python runs",
                ),
                summarize(
                    [float(count)],
                    key="primes_found",
                    unit="count",
                    aggregation="single",
                    bound="interpreter",
                ),
            ],
        )

    @staticmethod
    def _sieve(limit: int) -> int:
        flags = bytearray([1]) * limit
        flags[0] = flags[1] = 0
        for i in range(2, int(limit**0.5) + 1):
            if flags[i]:
                step = i
                start = i * i
                flags[start:limit:step] = bytearray(len(range(start, limit, step)))
        return sum(flags)
