"""Optional native toolchain: compile the bundled C kernels, run them, parse JSON.

Kept in-repo (no fio / sysbench / cpufp binaries) and strictly optional: if no C
compiler is found the executors report ``skipped`` with the reason, and every
other executor still produces numbers. The compiled binary is cached by source
hash + compiler identity, so results can be traced back to a kernel revision.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from open_benchmark.executors.base import BaseExecutor, Context
from open_benchmark.schema import Result
from open_benchmark.stats import summarize

KERNEL_SRC = Path(__file__).with_name("native") / "kernels.c"


def cache_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "open-benchmark" / "native"


class Toolchain:
    """Detects a C compiler and builds the kernels on first use."""

    def __init__(self, cmd: list[str] | None, name: str) -> None:
        self.cmd = cmd
        self.name = name
        self._binary: Path | None = None
        self.error = ""

    # -- detection ------------------------------------------------------
    @classmethod
    def detect(cls) -> Toolchain:
        override = os.environ.get("OPEN_BENCHMARK_CC")
        candidates: list[tuple[str, list[str]]] = []
        if override:
            candidates.append((override, override.split()))
        for exe in ("gcc", "cc", "clang", "zig"):
            path = shutil.which(exe)
            if path:
                candidates.append((exe, [path]))
        cl = shutil.which("cl")
        if cl:
            candidates.append(("cl", [cl]))
        if importlib.util.find_spec("ziglang"):
            candidates.append(("ziglang", [sys.executable, "-m", "ziglang", "cc"]))

        for name, cmd in candidates:
            tc = cls(cmd, name)
            if tc._try_build():
                return tc
        return cls(None, "none")

    def _try_build(self) -> bool:
        try:
            self._binary = self.build()
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {str(exc)[:200]}"
            self._binary = None
            return False

    # -- build ----------------------------------------------------------
    def build(self) -> Path:
        assert self.cmd is not None
        source = KERNEL_SRC.read_bytes()
        digest = hashlib.sha256(source + "|".join(self.cmd).encode()).hexdigest()[:12]
        out = cache_dir() / f"kernels-{digest}{'.exe' if sys.platform.startswith('win') else ''}"
        if out.exists():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp" + (".exe" if sys.platform.startswith("win") else ""))
        if self.cmd[0].endswith("cl") or self.cmd[0].endswith("cl.exe"):
            cmd = [*self.cmd, "/nologo", "/O2", str(KERNEL_SRC), f"/Fe:{tmp}"]
        else:
            cmd = [*self.cmd, "-O2", "-std=c11", "-o", str(tmp), str(KERNEL_SRC), "-lm"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if proc.returncode != 0 or not tmp.exists():
            raise RuntimeError((proc.stderr or proc.stdout or "compile failed").strip()[:400])
        tmp.replace(out)
        # zig/cl leave sibling artifacts behind (e.g. a .pdb next to the temp exe)
        for leftover in out.parent.glob(f"kernels-{digest}.tmp*"):
            with contextlib.suppress(OSError):
                leftover.unlink()
        return out

    # -- run ------------------------------------------------------------
    def run(self, mode: str, *args: str, timeout: float = 120.0) -> dict:
        if self._binary is None:
            self._binary = self.build()
        proc = subprocess.run(
            [str(self._binary), mode, *[str(a) for a in args]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or f"kernel {mode} exited {proc.returncode}").strip()[:300])
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not line:
            raise RuntimeError(f"kernel {mode} produced no output")
        return json.loads(line)

    @property
    def version(self) -> str:
        return f"{self.name}:{' '.join(self.cmd or [])}"


class _NativeBase(BaseExecutor):
    tags = ("native",)

    def available(self, ctx: Context) -> tuple[bool, str]:
        if not ctx.use_native:
            return False, "disabled (--no-native)"
        tc = ctx.native
        if tc is None or tc.cmd is None:
            return False, f"no C compiler found ({getattr(tc, 'error', '') or 'gcc/clang/cl/zig missing'})"
        return True, ""


class NativeCpu(_NativeBase):
    """FP64 FMA throughput and 64-bit integer throughput (single thread)."""

    name = "native.cpu"
    tags = ("cpu", "native")

    def run(self, ctx: Context) -> Result:
        tc: Toolchain = ctx.native
        seconds = "0.6" if ctx.suite == "full" else "0.3"
        flops = [tc.run("flops", seconds)["value"] for _ in range(max(1, ctx.repeat))]
        ints = [tc.run("intops", seconds)["value"] for _ in range(max(1, ctx.repeat))]
        return Result(
            name=self.name,
            backend=f"C kernel via {tc.name}",
            params={"sample_seconds": float(seconds), "repeat": ctx.repeat},
            metrics=[
                summarize(
                    flops,
                    key="fp64_gflops",
                    unit="GFLOPS",
                    aggregation="best",
                    notes="scalar FMA chain with 16 independent accumulators; not an AVX-512/SIMD peak",
                ),
                summarize(
                    ints,
                    key="int64_gops",
                    unit="GOPS",
                    aggregation="best",
                    notes="dependent 64-bit LCG chain, 2 ops per step",
                ),
            ],
            tool_versions={"kernel": tc.version, "kernel_source": str(KERNEL_SRC.name)},
        )


class NativeMemory(_NativeBase):
    """Real memory bandwidth (triad/copy) and latency across the cache hierarchy."""

    name = "native.memory"
    tags = ("memory", "native")

    #: label -> working-set size in KB (L1/L2/L3 sizes vary by CPU; these are probes)
    LATENCY_SIZES = (("l1", 32), ("l2", 512), ("l3", 8192), ("dram", 65536))

    def run(self, ctx: Context) -> Result:
        tc: Toolchain = ctx.native
        region_mb = int(64 * ctx.scale) if ctx.suite == "full" else 32
        steps = "4000000" if ctx.suite == "full" else "1500000"

        triad = [tc.run("triad", str(region_mb), timeout=300)["value"] for _ in range(max(1, ctx.repeat))]
        copy = [tc.run("copy", str(region_mb), timeout=300)["value"] for _ in range(max(1, ctx.repeat))]
        metrics = [
            summarize(
                triad,
                key="triad_gbs",
                unit="GB/s",
                aggregation="best",
                notes="a[i]=b[i]+s*c[i]; 24 B moved per element",
            ),
            summarize(copy, key="copy_gbs", unit="GB/s", aggregation="best"),
        ]
        latencies: dict[str, list[float]] = {}
        for label, kb in self.LATENCY_SIZES:
            latencies[label] = [tc.run("latency", str(kb), steps)["value"] for _ in range(max(1, ctx.repeat))]
        for label, _ in self.LATENCY_SIZES:
            metrics.append(
                summarize(
                    latencies[label],
                    key=f"latency_{label}_ns",
                    unit="ns",
                    aggregation="best",
                    higher_is_better=False,
                    notes="dependent random pointer chase (pointer-chase latency)",
                )
            )
        return Result(
            name=self.name,
            backend=f"C kernel via {tc.name}",
            params={"region_mb": region_mb, "steps": int(steps), "repeat": ctx.repeat},
            metrics=metrics,
            tool_versions={"kernel": tc.version},
        )
