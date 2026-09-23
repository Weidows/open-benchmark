"""Disk executors with an in-repo raw I/O layer.

CrystalDiskMark-class numbers require bypassing the OS page cache, so this
module implements unbuffered I/O itself:

* Windows - ``CreateFileW`` with ``FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH``
  plus ``OVERLAPPED`` offsets for positional access.
* POSIX   - ``open(..., O_DIRECT)`` with page-aligned buffers via ``mmap`` and
  libc ``pread``/``pwrite``.

If unbuffered I/O is unavailable (tmpfs, some network filesystems) it degrades
to buffered ``pread``/``pwrite`` and records that in ``Result.backend``, so a
leaderboard can tell the two apart.
"""

from __future__ import annotations

import contextlib
import ctypes
import mmap
import os
import random
import sys
import time
from pathlib import Path

from open_benchmark.executors.base import BaseExecutor, Context
from open_benchmark.schema import Result
from open_benchmark.stats import percentile, summarize

BLOCK_SEQ = 4 * 1024 * 1024
BLOCK_RAND = 4096


# --------------------------------------------------------------------------
# raw file layer
# --------------------------------------------------------------------------


class RawFile:
    """Positional, optionally unbuffered file access."""

    backend = "unknown"

    def __init__(self, path: Path, *, unbuffered: bool = True, block: int = BLOCK_RAND) -> None:
        self.path = Path(path)
        self.unbuffered = unbuffered
        self.block = block
        # Two page-aligned scratch mappings: small for timed I/O, big for
        # preallocation. Both must be page aligned for unbuffered access.
        self._buf = mmap.mmap(-1, block)
        self._ptr = ctypes.addressof(ctypes.c_char.from_buffer(self._buf))
        big = max(block, BLOCK_SEQ)
        self._big = mmap.mmap(-1, big)
        self._bigptr = ctypes.addressof(ctypes.c_char.from_buffer(self._big))
        self._open()

    # -- to implement ---------------------------------------------------
    def _open(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def write_at(self, offset: int, size: int) -> int:  # pragma: no cover - abstract
        raise NotImplementedError

    def read_at(self, offset: int, size: int) -> int:  # pragma: no cover - abstract
        raise NotImplementedError

    def flush(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- shared ---------------------------------------------------------
    def preallocate(self, size: int) -> None:
        """Grow the file to ``size`` so random I/O never extends it mid-test."""
        chunk = len(self._big)
        for offset in range(0, size, chunk):
            self.write_big(offset, min(chunk, size - offset))
        self.flush()

    def write_big(self, offset: int, size: int) -> int:
        """Write from the large scratch buffer (preallocation / setup writes)."""
        return self._io_impl(offset, size, self._bigptr, write=True)

    def close(self, *, delete: bool = True) -> None:
        try:
            self._close()
        finally:
            self._buf.close()
            self._big.close()
            if delete:
                with contextlib.suppress(OSError):
                    os.unlink(self.path)

    def _close(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class _WindowsRawFile(RawFile):
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    CREATE_ALWAYS = 2
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_NORMAL = 0x80
    FILE_FLAG_NO_BUFFERING = 0x20000000
    FILE_FLAG_WRITE_THROUGH = 0x80000000
    FILE_FLAG_DELETE_ON_CLOSE = 0x04000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    FILE_BEGIN = 0

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", ctypes.c_uint32),
            ("OffsetHigh", ctypes.c_uint32),
            ("hEvent", ctypes.c_void_p),
        ]

    def _open(self) -> None:
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._k32.CreateFileW.restype = ctypes.c_void_p
        flags = self.FILE_ATTRIBUTE_NORMAL
        if self.unbuffered:
            flags |= self.FILE_FLAG_NO_BUFFERING | self.FILE_FLAG_WRITE_THROUGH
        handle = self._k32.CreateFileW(
            str(self.path),
            self.GENERIC_READ | self.GENERIC_WRITE,
            0,
            None,
            self.CREATE_ALWAYS,
            flags,
            None,
        )
        if handle in (None, self.INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        self.backend = "win32-unbuffered" if self.unbuffered else "win32-buffered"

    def _io(self, fn_name: str, offset: int, size: int, ptr: int | None = None) -> int:
        ov = self.OVERLAPPED(Offset=offset & 0xFFFFFFFF, OffsetHigh=offset >> 32)
        done = ctypes.c_uint32(0)
        ok = getattr(self._k32, fn_name)(
            ctypes.c_void_p(self._handle),
            ctypes.c_void_p(self._ptr if ptr is None else ptr),
            ctypes.c_uint32(size),
            ctypes.byref(done),
            ctypes.byref(ov),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(done.value)

    def write_at(self, offset: int, size: int) -> int:
        return self._io("WriteFile", offset, size)

    def read_at(self, offset: int, size: int) -> int:
        return self._io("ReadFile", offset, size)

    def _io_impl(self, offset: int, size: int, ptr: int, *, write: bool) -> int:
        return self._io("WriteFile" if write else "ReadFile", offset, size, ptr)

    def flush(self) -> None:
        self._k32.FlushFileBuffers(ctypes.c_void_p(self._handle))

    def _close(self) -> None:
        self._k32.CloseHandle(ctypes.c_void_p(self._handle))


class _PosixRawFile(RawFile):
    def _open(self) -> None:
        self._libc = ctypes.CDLL(None, use_errno=True)
        self._libc.pread.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_long]
        self._libc.pread.restype = ctypes.c_ssize_t
        self._libc.pwrite.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_long]
        self._libc.pwrite.restype = ctypes.c_ssize_t
        flags = os.O_RDWR | os.O_CREAT | os.O_TRUNC
        direct = getattr(os, "O_DIRECT", 0)
        if self.unbuffered and direct:
            flags |= direct
        self._fd = os.open(str(self.path), flags, 0o600)
        self.backend = "posix-o_direct" if (self.unbuffered and direct) else "posix-buffered"

    def _call(self, fn, offset: int, size: int, ptr: int | None = None) -> int:
        n = fn(self._fd, ctypes.c_void_p(self._ptr if ptr is None else ptr), size, offset)
        if n < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        return int(n)

    def write_at(self, offset: int, size: int) -> int:
        return self._call(self._libc.pwrite, offset, size)

    def read_at(self, offset: int, size: int) -> int:
        return self._call(self._libc.pread, offset, size)

    def _io_impl(self, offset: int, size: int, ptr: int, *, write: bool) -> int:
        fn = self._libc.pwrite if write else self._libc.pread
        return self._call(fn, offset, size, ptr)

    def flush(self) -> None:
        os.fsync(self._fd)

    def _close(self) -> None:
        os.close(self._fd)


def open_raw(path: Path, *, unbuffered: bool = True, block: int = BLOCK_RAND) -> RawFile:
    """Open a raw file, falling back to buffered I/O when unbuffered is refused."""
    cls = _WindowsRawFile if sys.platform.startswith("win") else _PosixRawFile
    try:
        handle = cls(path, unbuffered=unbuffered, block=block)
        if unbuffered:
            handle.write_at(0, block)  # probe: O_DIRECT rejects some filesystems
            handle.flush()
        return handle
    except (OSError, ValueError):
        if not unbuffered:
            raise
        handle = cls(path, unbuffered=False, block=block)
        handle.backend += "+fallback"
        return handle


# --------------------------------------------------------------------------
# executors
# --------------------------------------------------------------------------


class DiskSequential(BaseExecutor):
    """Sequential write then read with large blocks."""

    name = "disk.seq"
    tags = ("disk", "sequential")

    @property
    def size_mb(self) -> int:
        return 256

    def available(self, ctx: Context) -> tuple[bool, str]:
        need = ctx.bytes_for(self.size_mb) / (1024 * 1024) + 256
        if ctx.free_space_mb(ctx.disk_path) < need:
            return False, f"needs {need:.0f} MB free on {ctx.disk_path}"
        return True, ""

    def run(self, ctx: Context) -> Result:
        size = ctx.bytes_for(self.size_mb)
        size -= size % BLOCK_SEQ
        path = ctx.temp_file(f"open-benchmark-seq-{os.getpid()}.bin")
        raw = open_raw(path, unbuffered=True, block=BLOCK_SEQ)
        write_samples: list[float] = []
        read_samples: list[float] = []
        try:
            for _ in range(max(1, ctx.repeat)):
                t0 = time.perf_counter()
                for offset in range(0, size, BLOCK_SEQ):
                    raw.write_at(offset, min(BLOCK_SEQ, size - offset))
                raw.flush()
                write_samples.append(size / (1024 * 1024) / (time.perf_counter() - t0))

                t0 = time.perf_counter()
                for offset in range(0, size, BLOCK_SEQ):
                    raw.read_at(offset, min(BLOCK_SEQ, size - offset))
                read_samples.append(size / (1024 * 1024) / (time.perf_counter() - t0))
        finally:
            raw.close(delete=True)
        return Result(
            name=self.name,
            backend=raw.backend,
            params={
                "file": str(path),
                "size_mb": round(size / (1024 * 1024), 1),
                "block_kb": BLOCK_SEQ // 1024,
                "repeat": ctx.repeat,
            },
            metrics=[
                summarize(write_samples, key="seq_write_mbs", unit="MB/s"),
                summarize(read_samples, key="seq_read_mbs", unit="MB/s"),
            ],
        )


class DiskRandom(BaseExecutor):
    """4K random read/write at queue depth 1, with latency percentiles."""

    name = "disk.rand"
    tags = ("disk", "random")

    region_mb = 512

    def available(self, ctx: Context) -> tuple[bool, str]:
        need = ctx.bytes_for(self.region_mb) / (1024 * 1024) + 256
        if ctx.free_space_mb(ctx.disk_path) < need:
            return False, f"needs {need:.0f} MB free on {ctx.disk_path}"
        return True, ""

    def run(self, ctx: Context) -> Result:
        region = ctx.bytes_for(self.region_mb)
        region -= region % BLOCK_RAND
        duration = 5.0 if ctx.suite == "full" else 2.0
        path = ctx.temp_file(f"open-benchmark-rand-{os.getpid()}.bin")
        raw = open_raw(path, unbuffered=True, block=BLOCK_RAND)
        read_ops: list[float] = []
        read_lat: list[float] = []
        write_ops: list[float] = []
        write_lat: list[float] = []
        try:
            raw.preallocate(region)
            for _ in range(max(1, ctx.repeat)):
                iops, lat = self._random_pass(raw, region, duration, write=False)
                read_ops.append(iops)
                read_lat.extend(lat)
                iops, lat = self._random_pass(raw, region, duration, write=True)
                write_ops.append(iops)
                write_lat.extend(lat)
        finally:
            raw.close(delete=True)

        return Result(
            name=self.name,
            backend=raw.backend,
            params={
                "file": str(path),
                "region_mb": round(region / (1024 * 1024), 1),
                "block_kb": BLOCK_RAND // 1024,
                "queue_depth": 1,
                "duration_s": duration,
                "repeat": ctx.repeat,
            },
            metrics=[
                summarize(read_ops, key="rand_read_iops", unit="IOPS"),
                summarize(write_ops, key="rand_write_iops", unit="IOPS"),
                summarize(
                    [percentile(read_lat, 50)],
                    key="rand_read_p50_us",
                    unit="us",
                    aggregation="single",
                    higher_is_better=False,
                ),
                summarize(
                    [percentile(read_lat, 99)],
                    key="rand_read_p99_us",
                    unit="us",
                    aggregation="single",
                    higher_is_better=False,
                ),
                summarize(
                    [percentile(write_lat, 50)],
                    key="rand_write_p50_us",
                    unit="us",
                    aggregation="single",
                    higher_is_better=False,
                ),
                summarize(
                    [percentile(write_lat, 99)],
                    key="rand_write_p99_us",
                    unit="us",
                    aggregation="single",
                    higher_is_better=False,
                ),
            ],
        )

    @staticmethod
    def _random_pass(raw: RawFile, region: int, duration: float, *, write: bool) -> tuple[float, list[float]]:
        rnd = random.Random(20240922)
        offsets = [rnd.randrange(0, region - BLOCK_RAND, BLOCK_RAND) for _ in range(8192)]
        latencies: list[float] = []
        ops = 0
        i = 0
        t_end = time.perf_counter() + duration
        clock = time.perf_counter_ns
        while time.perf_counter() < t_end:
            offset = offsets[i & 8191]
            i += 1
            t0 = clock()
            if write:
                raw.write_at(offset, BLOCK_RAND)
            else:
                raw.read_at(offset, BLOCK_RAND)
            latencies.append((clock() - t0) / 1000.0)
            ops += 1
        return ops / duration, latencies
