| metric | Apple M1 (Virtual) / 7 GiB<br>`53e9d2ab348c5a04` | AMD EPYC 9V45 96-Core Processor / 16 GiB<br>`2c0074b3a7d7537a` | INTEL(R) XEON(R) PLATINUM 8573C / 16 GiB<br>`27bd0aec48d77fa2` |
|---|---|---|---|
| `cpu.compress.compress_mbs` | 382.96 | 465.48 | 199.03 |
| `cpu.compress.decompress_mbs` | 3,165.27 | 442.70 | 749.29 |
| `cpu.hash.blake2b_mbs` | 634.37 | 1,109.32 | 637.25 |
| `cpu.hash.sha256_mbs` | 2,097.18 | 1,897.65 | 1,409.13 |
| `cpu.thread_scale.all_threads_mbs` | 4,821.86 | 5,397.28 | 3,507.60 |
| `cpu.thread_scale.scale_efficiency_pct` | 75.33 | 70.95 | 62.07 |
| `cpu.thread_scale.single_thread_mbs` | 2,133.68 | 1,901.81 | 1,412.74 |
| `disk.rand.rand_read_iops` | 335,239.00 | 6,148.00 | 4,561.00 |
| `disk.rand.rand_read_p50_us` | 1.96 | 134.13 | 211.80 |
| `disk.rand.rand_read_p99_us` | 9.92 | 379.30 | 289.21 |
| `disk.rand.rand_write_iops` | 214,262.00 | 8,530.00 | 715.00 |
| `disk.rand.rand_write_p50_us` | 3.29 | 86.42 | 1,254.00 |
| `disk.rand.rand_write_p99_us` | 21.60 | 135.29 | 2,888.26 |
| `disk.seq.seq_read_mbs` | 10,305.99 | 473.67 | 243.28 |
| `disk.seq.seq_write_mbs` | 3,904.19 | 239.23 | 116.30 |
| `memory.copy.copy_gbs` | 25.33 | 20.83 | 10.33 |
| `memory.scan.read_gbs` | 2.58 | 3.73 | 1.34 |
| `native.cpu.fp64_gflops` | 16.21 | - | 6.05 |
| `native.cpu.int64_gops` | 1.68 | - | 1.53 |
| `native.memory.copy_gbs` | 50.46 | - | 24.39 |
| `native.memory.latency_dram_ns` | 183.93 | - | 119.58 |
| `native.memory.latency_l1_ns` | 1.99 | - | 1.64 |
| `native.memory.latency_l2_ns` | 8.13 | - | 5.54 |
| `native.memory.latency_l3_ns` | 82.23 | - | 30.29 |
| `native.memory.triad_gbs` | 49.56 | - | 15.10 |

_3 run(s) over 3 device(s); values are per-device medians; bound=hardware; suites: quick; units: %, GB/s, GFLOPS, GOPS, IOPS, MB/s, ns, us_
