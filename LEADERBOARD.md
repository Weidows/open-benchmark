| metric | Apple M1 (Virtual) / 7 GiB<br>`53e9d2ab348c5a04` | AMD EPYC 9V45 96-Core Processor / 16 GiB<br>`288f74dffd18e7fb` | AMD EPYC 7763 64-Core Processor / 16 GiB<br>`db629f3fa20da1bb` |
|---|---|---|---|
| `cpu.compress.compress_mbs` | 391.84 | 496.19 | 150.49 |
| `cpu.compress.decompress_mbs` | 3,396.67 | 477.21 | 295.80 |
| `cpu.hash.blake2b_mbs` | 666.78 | 1,244.07 | 552.51 |
| `cpu.hash.sha256_mbs` | 2,317.57 | 2,028.89 | 1,511.56 |
| `cpu.thread_scale.all_threads_mbs` | 6,271.57 | 5,403.06 | 5,166.58 |
| `cpu.thread_scale.scale_efficiency_pct` | 98.54 | 66.85 | 85.63 |
| `cpu.thread_scale.single_thread_mbs` | 2,121.55 | 2,020.55 | 1,508.47 |
| `disk.rand.rand_read_iops` | 377,815.00 | 6,218.50 | 4,547.50 |
| `disk.rand.rand_read_p50_us` | 1.92 | 150.17 | 168.30 |
| `disk.rand.rand_read_p99_us` | 8.08 | 385.83 | 485.10 |
| `disk.rand.rand_write_iops` | 264,500.00 | 10,809.00 | 542.50 |
| `disk.rand.rand_write_p50_us` | 3.25 | 84.89 | 1,546.90 |
| `disk.rand.rand_write_p99_us` | 10.50 | 241.54 | 6,231.27 |
| `disk.seq.seq_read_mbs` | 12,904.15 | 532.26 | 676.24 |
| `disk.seq.seq_write_mbs` | 2,613.14 | 266.10 | 145.35 |
| `memory.copy.copy_gbs` | 20.55 | 23.45 | 20.36 |
| `memory.scan.read_gbs` | 2.44 | 4.00 | 1.48 |
| `native.cpu.fp64_gflops` | 20.58 | - | 4.92 |
| `native.cpu.int64_gops` | 1.91 | - | 1.60 |
| `native.memory.copy_gbs` | 45.59 | - | 39.79 |
| `native.memory.latency_dram_ns` | 109.97 | - | 125.06 |
| `native.memory.latency_l1_ns` | 1.36 | - | 1.61 |
| `native.memory.latency_l2_ns` | 5.79 | - | 7.39 |
| `native.memory.latency_l3_ns` | 55.51 | - | 52.15 |
| `native.memory.triad_gbs` | 44.21 | - | 27.03 |

_3 run(s) over 3 device(s); values are per-device medians; bound=hardware; suites: quick; units: %, GB/s, GFLOPS, GOPS, IOPS, MB/s, ns, us_
