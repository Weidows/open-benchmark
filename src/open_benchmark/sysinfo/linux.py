"""Linux inventory from /proc, /sys and (when present) lscpu/dmidecode.

No hard dependencies: every source is optional and the collector degrades to
whatever the kernel exposes to an unprivileged user.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from open_benchmark.schema import Device

_VM_VENDORS = (
    "vmware",
    "virtualbox",
    "innotek",
    "qemu",
    "kvm",
    "microsoft corporation",
    "xen",
    "amazon ec2",
    "google",
    "alibaba",
)


def collect() -> Device:
    cpuinfo = _read("/proc/cpuinfo")
    meminfo = _read("/proc/meminfo")
    lscpu = _lscpu()

    cores = _cpuinfo_cores(cpuinfo)
    cpu = {
        "model": (
            _cpuinfo_field(cpuinfo, "model name") or _cpuinfo_field(cpuinfo, "Processor") or ""
        ).strip(),
        "vendor": (_cpuinfo_field(cpuinfo, "vendor_id") or "").strip(),
        "physical_cores": cores[0],
        "logical_cores": cores[1],
        "base_mhz": _mhz(_cpuinfo_field(cpuinfo, "cpu MHz")),
        "max_mhz": _lscpu_max_mhz(lscpu),
        "sockets": _lscpu_sockets(lscpu),
        "l2_kb": _cache_kb(lscpu, "L2") or _cache_kb_from_sys("/sys/devices/system/cpu/cpu0/cache", 2),
        "l3_kb": _cache_kb(lscpu, "L3") or _cache_kb_from_sys("/sys/devices/system/cpu/cpu0/cache", 3),
        "flags": (_cpuinfo_field(cpuinfo, "flags") or "").split()[:40],
    }

    device = Device(
        os="linux",
        os_version=_os_version(),
        arch=os.uname().machine,
        cpu=cpu,
        memory={
            "total_bytes": _meminfo_total(meminfo),
            "modules": _dmi_memory(),
            "swap_bytes": _meminfo_kb(meminfo, "SwapTotal") * 1024,
        },
        disks=_block_devices(),
        gpus=_gpus(),
        system=_dmi_system(),
        raw={"proc_cpuinfo_first": _first_block(cpuinfo), "lscpu": lscpu},
    )
    device.machine_class = _machine_class(device)
    return device


# ---- /proc parsing --------------------------------------------------------


def parse_cpuinfo(text: str) -> dict[str, str]:
    """First processor block of /proc/cpuinfo as a dict (exported for tests)."""
    block: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            if block:
                break
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            block[key.strip()] = value.strip()
    return block


def _first_block(text: str) -> dict[str, str]:
    return parse_cpuinfo(text)


def _cpuinfo_cores(text: str) -> tuple[int | None, int | None]:
    """(physical, logical) core counts from /proc/cpuinfo."""
    logical = len(re.findall(r"^processor\s*:", text, re.M)) or None
    per_socket: dict[str, set[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = re.match(r"^physical id\s*:\s*(\d+)", line)
        if m:
            current = m.group(1)
            per_socket.setdefault(current, set())
            continue
        m = re.match(r"^core id\s*:\s*(\d+)", line)
        if m and current is not None:
            per_socket[current].add(m.group(1))
    if per_socket:
        physical = sum(len(v) for v in per_socket.values()) or None
    else:
        core_ids = set(re.findall(r"^core id\s*:\s*(\d+)", text, re.M))
        physical = len(core_ids) or None
    return physical, logical


def _cpuinfo_field(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}\s*:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else None


def _meminfo_total(text: str) -> int | None:
    kb = _meminfo_kb(text, "MemTotal")
    return kb * 1024 or None


def _meminfo_kb(text: str, key: str) -> int:
    m = re.search(rf"^{key}:\s*(\d+)\s*kB", text, re.M)
    return int(m.group(1)) if m else 0


def _mhz(value: str | None) -> int | None:
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _first_line(path: str) -> str:
    return _read(path).strip().splitlines()[0].strip() if _read(path).strip() else ""


def _os_version() -> str:
    text = _read("/etc/os-release")
    pretty = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.M)
    kernel = os.uname().release
    return f"{pretty.group(1) if pretty else 'linux'} (kernel {kernel})"


# ---- lscpu / dmidecode ----------------------------------------------------


def _lscpu() -> dict[str, Any]:
    exe = shutil.which("lscpu")
    if not exe:
        return {}
    try:
        proc = subprocess.run([exe, "-J"], capture_output=True, text=True, timeout=15, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            return {k: v for k, v in data.items() if isinstance(v, (str, int, float))}
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return {}


def _lscpu_max_mhz(lscpu: dict[str, Any]) -> int | None:
    for key in ("CPU max MHz", "CPU MHz"):
        if lscpu.get(key):
            try:
                return int(float(lscpu[key]))
            except (TypeError, ValueError):
                continue
    return None


def _lscpu_sockets(lscpu: dict[str, Any]) -> int | None:
    try:
        return int(lscpu.get("Socket(s)")) if lscpu.get("Socket(s)") else None
    except (TypeError, ValueError):
        return None


def _cache_kb(lscpu: dict[str, Any], level: str) -> int | None:
    for key, value in lscpu.items():
        if key.startswith(level) and "cache" in key.lower():
            m = re.match(r"([\d.]+)\s*([KMG])i?B", str(value), re.I)
            if m:
                num, unit = float(m.group(1)), m.group(2).upper()
                return int(num * {"K": 1, "M": 1024, "G": 1024 * 1024}[unit])
    return None


def _cache_kb_from_sys(root: str, level: int) -> int | None:
    for entry in sorted(Path(root).glob("index*")) if Path(root).exists() else []:
        try:
            if int((entry / "level").read_text().strip()) != level:
                continue
            size = (entry / "size").read_text().strip()
            m = re.match(r"(\d+)\s*([KMG])", size)
            if m:
                return int(m.group(1)) * {"K": 1, "M": 1024, "G": 1024 * 1024}[m.group(2)]
        except (OSError, ValueError):
            continue
    return None


def _dmi_system() -> dict[str, Any]:
    return {
        "vendor": _first_line("/sys/class/dmi/id/sys_vendor"),
        "model": " ".join(
            x
            for x in (
                _first_line("/sys/class/dmi/id/product_name"),
                _first_line("/sys/class/dmi/id/product_version"),
            )
            if x
        ),
        "board": _first_line("/sys/class/dmi/id/board_name"),
        "bios_version": _first_line("/sys/class/dmi/id/bios_version"),
        "bios_date": _first_line("/sys/class/dmi/id/bios_date"),
    }


def _dmi_memory() -> list[dict[str, Any]]:
    """Memory module details need root; only dmidecode can give them."""
    exe = shutil.which("dmidecode")
    if not exe or os.geteuid() != 0:
        return []
    try:
        proc = subprocess.run(
            [exe, "-t", "memory"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    modules: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in proc.stdout.splitlines():
        if line.strip() == "Memory Device":
            if current.get("size_bytes"):
                modules.append(current)
            current = {}
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "Size" and "No Module" not in value:
            m = re.match(r"(\d+)\s*(MB|GB|TB)", value)
            if m:
                mult = {"MB": 1024**2, "GB": 1024**3, "TB": 1024**4}[m.group(2)]
                current["size_bytes"] = int(m.group(1)) * mult
        elif key == "Speed" and value.endswith("MT/s"):
            current["speed_mtps"] = _int_or_none(value.split()[0])
        elif key == "Configured Memory Speed" and value.endswith("MT/s"):
            current["configured_speed_mtps"] = _int_or_none(value.split()[0])
        elif key == "Locator":
            current["locator"] = value
        elif key == "Manufacturer":
            current["manufacturer"] = value
        elif key == "Part Number":
            current["part_number"] = value
    if current.get("size_bytes"):
        modules.append(current)
    return modules


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


# ---- block devices / gpu --------------------------------------------------


def _block_devices() -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    base = Path("/sys/block")
    if not base.exists():
        return disks
    for dev in sorted(base.iterdir()):
        name = dev.name
        if name.startswith(("loop", "ram", "zram", "dm-", "md")):
            continue
        try:
            size = int((dev / "size").read_text().strip()) * 512
        except (OSError, ValueError):
            continue
        rotational = _first_line(str(dev / "queue" / "rotational"))
        model = _first_line(str(dev / "device" / "model")) or name
        real = dev
        if name.startswith("nvme"):
            real = dev / "device"
            bus = "nvme"
        elif name.startswith("sd"):
            bus = "sata"
        else:
            bus = "unknown"
        firmware = _first_line(str(dev / "device" / "firmware_rev")) or _first_line(
            str(real / "firmware_rev")
        )
        disks.append(
            {
                "name": name,
                "model": model,
                "size_bytes": size,
                "bus": bus,
                "media": "hdd" if rotational == "1" else "ssd",
                "firmware": firmware,
            }
        )
    return disks


def _gpus() -> list[dict[str, Any]]:
    gpus: list[dict[str, Any]] = []
    base = Path("/sys/class/drm")
    if not base.exists():
        return gpus
    for card in sorted(base.glob("card[0-9]")):
        try:
            vendor = (card / "device" / "vendor").read_text().strip()
            device = (card / "device" / "device").read_text().strip()
        except OSError:
            continue
        gpus.append({"name": f"{vendor}:{device}", "driver": None, "vram_bytes": None})
    return gpus


def _machine_class(device: Device) -> str:
    vendor = (device.system.get("vendor") or "").lower()
    model = (device.system.get("model") or "").lower()
    if any(v in f"{vendor} {model}" for v in _VM_VENDORS):
        return "vm"
    return "physical"
