"""macOS inventory via sysctl + system_profiler (both ship with the OS)."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from typing import Any

from hwbench.schema import Device

_VM_VENDORS = ("vmware", "virtualbox", "parallels", "qemu", "utm", "apple virtualization")


def collect() -> Device:
    hw = _profiler("SPHardwareDataType")
    storage = _profiler("SPStorageDataType")
    displays = _profiler("SPDisplaysDataType")

    hw0 = (hw.get("SPHardwareDataType") or [{}])[0]
    cpu = {
        "model": hw0.get("chip_type") or _sysctl("machdep.cpu.brand_string") or hw0.get("machine_name"),
        "vendor": hw0.get("platform_Vendor") or ("Apple" if hw0.get("chip_type") else None),
        "physical_cores": _int(_sysctl("hw.physicalcpu")) or hw0.get("number_processors"),
        "logical_cores": _int(_sysctl("hw.logicalcpu")) or os.cpu_count(),
        "base_mhz": _int(_sysctl("hw.cpufrequency_max")) // 1000 if _sysctl("hw.cpufrequency_max") else None,
        "l2_kb": (_int(_sysctl("hw.l2cachesize")) or 0) // 1024 or None,
        "l3_kb": (_int(_sysctl("hw.l3cachesize")) or 0) // 1024 or None,
        "sockets": _int(_sysctl("hw.packages")),
    }
    disks = []
    for item in storage.get("SPStorageDataType", []):
        disks.append(
            {
                "name": item.get("bsd_name"),
                "model": item.get("_name") or item.get("device_model"),
                "size_bytes": _size_bytes(item.get("size_in_bytes")),
                "bus": item.get("physical_drive", {}).get("protocol"),
                "media": "ssd" if item.get("physical_drive", {}).get("is_solid_state") else "unknown",
                "firmware": item.get("physical_drive", {}).get("revision"),
            }
        )
    gpus = [
        {
            "name": g.get("sppci_model") or g.get("_name"),
            "vram_bytes": _size_bytes(g.get("spdisplays_vram")),
            "driver": g.get("spdisplays_metal"),
        }
        for g in displays.get("SPDisplaysDataType", [])
    ]
    device = Device(
        os="macos",
        os_version=f"{platform.mac_ver()[0]} ({platform.release()})",
        arch=platform.machine(),
        cpu=cpu,
        memory={
            "total_bytes": _int(_sysctl("hw.memsize")),
            "modules": [
                {
                    "locator": entry.get("_name"),
                    "size_bytes": _size_bytes(entry.get("SPMemoryDataType")),
                    "speed_mtps": None,
                }
                for entry in []
            ],
        },
        disks=disks,
        gpus=gpus,
        system={
            "vendor": "Apple" if _is_apple_silicon() else hw0.get("platform_Vendor"),
            "model": hw0.get("machine_model") or hw0.get("machine_name"),
            "bios_version": hw0.get("boot_rom_version"),
        },
        raw={"hardware": hw0},
    )
    device.machine_class = _machine_class(device)
    return device


def _sysctl(key: str) -> str | None:
    try:
        proc = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value or None


def _profiler(data_type: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["system_profiler", data_type, "-json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _size_bytes(value: Any) -> int | None:
    """system_profiler reports sizes as '512 GB' / '8 GB' style strings."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    import re

    m = re.match(r"([\d.]+)\s*(KB|MB|GB|TB|bytes)", value.strip(), re.I)
    if not m:
        return None
    mult = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "BYTES": 1}[m.group(2).upper()]
    return int(float(m.group(1)) * mult)


def _is_apple_silicon() -> bool:
    return platform.machine() in ("arm64", "aarch64")


def _machine_class(device: Device) -> str:
    model = (device.system.get("model") or "").lower()
    if any(v in model for v in _VM_VENDORS):
        return "vm"
    return "physical"
