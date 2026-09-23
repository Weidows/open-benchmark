"""Windows inventory via CIM (PowerShell is part of the OS, so still in-repo).

`wmic` is deprecated and removed on recent builds, so the collector writes a
small PowerShell script to a temp file, runs it with ``-NoProfile`` and parses
the JSON it emits.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from typing import Any

from open_benchmark.schema import Device

_PS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$o = [ordered]@{}
$o.cpu   = Get-CimInstance Win32_Processor        | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,CurrentClockSpeed,L2CacheSize,L3CacheSize,SocketDesignation,ProcessorId,Architecture
$o.mem   = Get-CimInstance Win32_PhysicalMemory   | Select-Object Manufacturer,PartNumber,Capacity,Speed,ConfiguredClockSpeed,DeviceLocator,SMBIOSMemoryType
$o.disk  = Get-CimInstance Win32_DiskDrive        | Select-Object Model,Size,InterfaceType,MediaType,FirmwareRevision,Partitions,SerialNumber
$o.pdisk = Get-PhysicalDisk                       | Select-Object FriendlyName,MediaType,BusType,Size,SerialNumber,FirmwareVersion,SpindleSpeed
$o.gpu   = Get-CimInstance Win32_VideoController  | Select-Object Name,AdapterRAM,DriverVersion,VideoProcessor,PNPDeviceID
$o.cs    = Get-CimInstance Win32_ComputerSystem   | Select-Object Manufacturer,Model,TotalPhysicalMemory,SystemType,NumberOfProcessors
$o.bios  = Get-CimInstance Win32_BIOS             | Select-Object Manufacturer,SMBIOSBIOSVersion,ReleaseDate
$o.os    = Get-CimInstance Win32_OperatingSystem  | Select-Object Caption,Version,BuildNumber,OSArchitecture
$o | ConvertTo-Json -Depth 4 -Compress
"""

_VM_VENDORS = ("vmware", "virtualbox", "innotek", "qemu", "kvm", "hyper-v", "xen", "parallels", "bochs")


def collect() -> Device:
    raw = _query()
    cpu_rows = _rows(raw.get("cpu"))
    cpu0 = cpu_rows[0] if cpu_rows else {}
    mem_rows = _maybe_list(raw.get("mem"))
    disks = _physical_disks(_maybe_list(raw.get("pdisk"))) or _disk_drives(_maybe_list(raw.get("disk")))
    gpus = _maybe_list(raw.get("gpu"))
    cs = raw.get("cs") or {}
    bios = raw.get("bios") or {}
    osinfo = raw.get("os") or {}

    device = Device(
        os="windows",
        os_version=" ".join(
            x for x in (osinfo.get("Caption"), osinfo.get("Version"), osinfo.get("BuildNumber")) if x
        ).strip(),
        arch=os.environ.get("PROCESSOR_ARCHITECTURE") or _english_arch(osinfo.get("OSArchitecture")),
        cpu={
            "model": (cpu0.get("Name") or "").strip(),
            "vendor": (cpu0.get("Manufacturer") or "").strip(),
            "physical_cores": _int(cpu0.get("NumberOfCores")) * max(len(cpu_rows), 1) or None,
            "logical_cores": _int(cpu0.get("NumberOfLogicalProcessors")) * max(len(cpu_rows), 1) or None,
            "base_mhz": _int(cpu0.get("MaxClockSpeed")) or None,
            "current_mhz": _int(cpu0.get("CurrentClockSpeed")) or None,
            "sockets": len(cpu_rows) or None,
            "l2_kb": _int(cpu0.get("L2CacheSize")) or None,
            "l3_kb": _int(cpu0.get("L3CacheSize")) or None,
        },
        memory={
            "total_bytes": _int(cs.get("TotalPhysicalMemory")) or None,
            "modules": [
                {
                    "locator": m.get("DeviceLocator"),
                    "manufacturer": (m.get("Manufacturer") or "").strip(),
                    "part_number": (m.get("PartNumber") or "").strip(),
                    "size_bytes": _int(m.get("Capacity")),
                    "speed_mtps": _int(m.get("Speed")),
                    "configured_speed_mtps": _int(m.get("ConfiguredClockSpeed")),
                    "smbios_type": _int(m.get("SMBIOSMemoryType")),
                }
                for m in mem_rows
            ],
        },
        disks=disks,
        gpus=[
            {
                "name": (g.get("Name") or "").strip(),
                "vram_bytes": _int(g.get("AdapterRAM")),
                "driver": g.get("DriverVersion"),
            }
            for g in gpus
        ],
        system={
            "vendor": (cs.get("Manufacturer") or "").strip(),
            "model": (cs.get("Model") or "").strip(),
            "system_type": cs.get("SystemType"),
            "bios_version": bios.get("SMBIOSBIOSVersion"),
            "bios_date": _bios_date(bios.get("ReleaseDate")),
        },
        raw={"cim": raw},
    )
    device.machine_class = _machine_class(device)
    return device


def _query() -> dict[str, Any]:
    fd, path = tempfile.mkstemp(suffix=".ps1", prefix="openbenchmark-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
            fh.write(_PS)
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            raise RuntimeError((proc.stderr or "empty CIM response").strip()[:400])
        return json.loads(proc.stdout)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _rows(value: Any) -> list[dict[str, Any]]:
    return _maybe_list(value)


def _maybe_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return [v for v in value if isinstance(v, dict)]


def _physical_disks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer MSFT_PhysicalDisk: it reports real MediaType (SSD/HDD) and BusType."""
    disks: list[dict[str, Any]] = []
    for d in rows:
        media = (d.get("MediaType") or "").lower()
        bus = (d.get("BusType") or "").lower()
        if media not in ("ssd", "hdd"):
            media = "ssd" if "nvme" in bus else "unknown"
        name = (d.get("FriendlyName") or "").strip()
        disks.append(
            {
                "name": name,
                "model": name,
                "size_bytes": _int(d.get("Size")),
                "bus": bus or None,
                "media": media,
                "firmware": (d.get("FirmwareVersion") or "").strip(),
                "serial": (d.get("SerialNumber") or "").strip(),
            }
        )
    return disks


def _disk_drives(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback inventory from Win32_DiskDrive (no Get-PhysicalDisk available)."""
    return [
        {
            "name": (d.get("Model") or "").strip(),
            "model": (d.get("Model") or "").strip(),
            "size_bytes": _int(d.get("Size")),
            "bus": (d.get("InterfaceType") or "").lower() or None,
            "media": _media(d),
            "firmware": (d.get("FirmwareRevision") or "").strip(),
            "serial": (d.get("SerialNumber") or "").strip(),
        }
        for d in rows
    ]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _english_arch(value: Any) -> str:
    """OSArchitecture is localised ('64 位'), so map it to a stable token."""
    text = str(value or "")
    if "64" in text:
        return "AMD64"
    if "32" in text:
        return "X86"
    return text.strip()


def _bios_date(value: Any) -> str:
    """Win32_BIOS.ReleaseDate arrives either as 'YYYYMMDD......' or '/Date(ms)/'."""
    text = str(value or "").strip()
    if text.startswith("/Date(") and text.endswith(")/"):
        digits = "".join(ch for ch in text[6:-2] if ch.isdigit() or ch == "-")
        try:
            import datetime as _dt

            ts = float(digits) / 1000.0
            return _dt.datetime.fromtimestamp(ts, _dt.UTC).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            return text
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) >= 8 and text[:8].isdigit() else text


def _media(disk: dict[str, Any]) -> str:
    media = (disk.get("MediaType") or "").lower()
    model = (disk.get("Model") or "").lower()
    bus = (disk.get("InterfaceType") or "").lower()
    if "ssd" in media or "nvme" in model or "ssd" in model:
        return "ssd"
    if "hdd" in media or "fixed" in media:
        return "hdd" if bus in ("ide", "sata", "ata") else "unknown"
    if "nvme" in bus:
        return "ssd"
    return "unknown"


def _machine_class(device: Device) -> str:
    vendor = (device.system.get("vendor") or "").lower()
    model = (device.system.get("model") or "").lower()
    if any(v in f"{vendor} {model}" for v in _VM_VENDORS):
        return "vm"
    return "physical"
