"""Platform inventory parsing, using fixture text instead of the live machine."""

from __future__ import annotations

from open_benchmark.sysinfo import linux

CPUINFO = """processor\t: 0
vendor_id\t: GenuineIntel
cpu family\t: 6
model name\t: Intel(R) Xeon(R) Gold 6248R CPU @ 3.00GHz
cpu MHz\t\t: 2999.998
physical id\t: 0
core id\t\t: 0
flags\t\t: fpu vme de pse msr avx2 avx512f

processor\t: 1
vendor_id\t: GenuineIntel
cpu family\t: 6
model name\t: Intel(R) Xeon(R) Gold 6248R CPU @ 3.00GHz
cpu MHz\t\t: 2999.998
physical id\t: 0
core id\t\t: 1
flags\t\t: fpu vme de pse msr avx2 avx512f
"""

MEMINFO = """MemTotal:       65855136 kB
MemFree:         1000000 kB
SwapTotal:       8388604 kB
"""


def test_parse_cpuinfo_first_block():
    block = linux.parse_cpuinfo(CPUINFO)
    assert block["model name"] == "Intel(R) Xeon(R) Gold 6248R CPU @ 3.00GHz"
    assert block["vendor_id"] == "GenuineIntel"
    assert "avx512f" in block["flags"]


def test_cpuinfo_cores_counts_sockets_and_core_ids():
    physical, logical = linux._cpuinfo_cores(CPUINFO)
    assert physical == 2  # two distinct core ids on socket 0
    assert logical == 2


def test_meminfo_parsing():
    assert linux._meminfo_kb(MEMINFO, "MemTotal") == 65855136
    assert linux._meminfo_total(MEMINFO) == 65855136 * 1024
    assert linux._meminfo_kb(MEMINFO, "SwapTotal") == 8388604


def test_mhz_parsing():
    assert linux._mhz("2999.998") == 2999
    assert linux._mhz(None) is None
    assert linux._mhz("not-a-number") is None
