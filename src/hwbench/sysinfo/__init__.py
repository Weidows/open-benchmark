"""Device inventory, collected in-repo (no fastfetch / dmidecode dependency).

Each platform module returns a :class:`hwbench.schema.Device`. Anything the OS
will not tell us without extra privileges is left blank rather than guessed,
and the untouched platform payload is kept under ``Device.raw`` so results can
be re-parsed later when the collector learns more.
"""

from __future__ import annotations

import os
import sys

from hwbench.schema import Device


def collect() -> Device:
    """Detect the current device. Never raises: worst case returns a stub."""
    try:
        if sys.platform.startswith("win"):
            from hwbench.sysinfo import windows as mod
        elif sys.platform == "darwin":
            from hwbench.sysinfo import darwin as mod
        else:
            from hwbench.sysinfo import linux as mod

        device = mod.collect()
    except Exception as exc:  # pragma: no cover - platform surprises
        device = Device()
        device.raw = {"error": f"{type(exc).__name__}: {exc}"}
    _fill_common(device)
    device.compute_fingerprint()
    return device


def _fill_common(device: Device) -> None:
    """Fields we can always answer, plus the best-effort VM/container hint."""
    import platform

    if not device.arch:
        device.arch = platform.machine()
    if not device.hostname:
        device.hostname = platform.node()
    if not device.machine_class or device.machine_class == "unknown":
        device.machine_class = _machine_class()


def _machine_class() -> str:
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return "container"
    if os.environ.get("container"):  # noqa: SIM112 - runtime-provided var is lowercase
        return "container"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "vm"  # WSL is virtualised hardware from the host's point of view
    return "unknown"


def cores() -> int:
    """Usable logical cores for this process (respects affinity)."""
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except AttributeError:
        return os.cpu_count() or 1
