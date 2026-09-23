"""Executor registry and suite definitions.

Suites are just ordered lists of executor names, so ``--suite full`` is
reproducible from the hwbench version alone.
"""

from __future__ import annotations

from hwbench.executors import cpu, disk, memory, native
from hwbench.executors.base import BaseExecutor, Context, Executor

ALL: list[Executor] = [
    cpu.HashThroughput(),
    cpu.CompressionThroughput(),
    cpu.ThreadScaling(),
    cpu.PrimeSieve(),
    memory.CopyBandwidth(),
    memory.ScanBandwidth(),
    memory.FillBandwidth(),
    memory.PythonRandomAccess(),
    disk.DiskSequential(),
    disk.DiskRandom(),
    native.NativeCpu(),
    native.NativeMemory(),
]

BY_NAME: dict[str, Executor] = {ex.name: ex for ex in ALL}

SUITES: dict[str, list[str]] = {
    "quick": [
        "cpu.hash",
        "cpu.compress",
        "cpu.thread_scale",
        "memory.copy",
        "memory.scan",
        "disk.seq",
        "disk.rand",
        "native.cpu",
        "native.memory",
    ],
    "full": [ex.name for ex in ALL],
}


def names() -> list[str]:
    return [ex.name for ex in ALL]


def resolve(only: list[str] | None = None, exclude: list[str] | None = None, suite: str = "quick"):
    """Resolve a runnable list of executors from a suite plus include/exclude."""
    if suite not in SUITES:
        raise KeyError(f"unknown suite {suite!r}; known: {', '.join(SUITES)}")
    selected = list(SUITES[suite])
    if only:
        selected = []
        for item in only:
            matches = [n for n in BY_NAME if n == item or n.startswith(item + ".")]
            if not matches:
                matches = [n for n in BY_NAME if item in n]
            if not matches:
                raise KeyError(f"no executor matches {item!r}")
            selected.extend(m for m in matches if m not in selected)
    if exclude:
        for item in exclude:
            selected = [n for n in selected if n != item and not n.startswith(item + ".")]
    return [BY_NAME[n] for n in selected]


__all__ = ["ALL", "BY_NAME", "SUITES", "BaseExecutor", "Context", "Executor", "names", "resolve"]
