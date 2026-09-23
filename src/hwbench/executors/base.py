"""Executor protocol shared by every in-repo benchmark.

An executor owns exactly one benchmark: it probes for availability, runs the
workload, and returns a :class:`hwbench.schema.Result`. No shelling out to
third-party benchmark binaries - the workload is implemented in this repo, so
results are reproducible from the pinned hwbench version.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from hwbench.schema import Result
from hwbench.sysinfo import cores


@dataclass
class Context:
    """Everything an executor needs to run: paths, knobs, logging."""

    suite: str = "quick"
    repeat: int = 3
    workdir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "hwbench")
    disk_path: Path | None = None
    scale: float = 1.0  # multiplies default buffer sizes (quick < full)
    use_native: bool = True
    log: Any = print
    native: Any = None  # hwbench.executors.native.Toolchain | None

    def __post_init__(self) -> None:
        self.workdir = Path(self.workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        if self.disk_path is None:
            self.disk_path = self.workdir

    @property
    def cpu_count(self) -> int:
        return cores()

    def bytes_for(self, base_mb: int) -> int:
        """Scale a default workload size, clamped to something sane."""
        return max(4, int(base_mb * self.scale)) * 1024 * 1024

    def temp_file(self, name: str) -> Path:
        return self.workdir / name

    def free_space_mb(self, path: Path) -> float:
        try:
            return shutil.disk_usage(str(path)).free / (1024 * 1024)
        except OSError:
            return float("inf")


@runtime_checkable
class Executor(Protocol):
    name: str
    tags: tuple[str, ...]

    def available(self, ctx: Context) -> tuple[bool, str]: ...

    def run(self, ctx: Context) -> Result: ...


class BaseExecutor:
    """Convenience base: timing, error capture and skip handling."""

    name = "base"
    tags: tuple[str, ...] = ()

    #: minimum free space the executor needs on ``ctx.disk_path``
    needs_free_mb = 0.0

    def available(self, ctx: Context) -> tuple[bool, str]:
        if self.needs_free_mb and ctx.free_space_mb(ctx.disk_path) < self.needs_free_mb:
            return False, f"needs {self.needs_free_mb:.0f} MB free on {ctx.disk_path}"
        return True, ""

    def run(self, ctx: Context) -> Result:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------
    def skipped(self, reason: str) -> Result:
        return Result(name=self.name, status="skipped", error=reason)

    def failed(self, exc: BaseException) -> Result:
        return Result(name=self.name, status="error", error=f"{type(exc).__name__}: {exc}")

    def with_params(self, **params: Any) -> dict[str, Any]:
        return params


def new_buffer(size: int, *, fill: int = 0xA5) -> bytes:
    """Allocate a large buffer without the cost of os.urandom."""
    return bytes([fill]) * size


def aligned_buffer(size: int):
    """Page-aligned writable buffer, required by O_DIRECT / FILE_FLAG_NO_BUFFERING."""
    import mmap

    return mmap.mmap(-1, size)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
