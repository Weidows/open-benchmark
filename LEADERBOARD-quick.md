| metric | Apple M1 (Virtual) / 7 GiB<br>`53e9d2ab348c5a04` | AMD EPYC 9V45 96-Core Processor / 16 GiB<br>`288f74dffd18e7fb` | AMD EPYC 7763 64-Core Processor / 16 GiB<br>`db629f3fa20da1bb` |
|---|---|---|---|
| `cpu.compress.compress_mbs` | 258.94 | 518.56 | 150.36 |
| `cpu.compress.decompress_mbs` | 3,290.05 | 499.47 | 303.67 |
| `cpu.hash.blake2b_mbs` | 465.18 | 1,298.13 | 564.90 |
| `cpu.hash.sha256_mbs` | 1,680.02 | 2,120.30 | 1,512.77 |
| `cpu.thread_scale.all_threads_mbs` | 3,629.41 | 6,095.49 | 5,240.58 |
| `cpu.thread_scale.scale_efficiency_pct` | 66.34 | 71.95 | 86.58 |
| `cpu.thread_scale.single_thread_mbs` | 1,823.60 | 2,118.07 | 1,513.25 |
| `disk.rand.rand_read_iops` | 219,681.50 | 5,564.00 | 5,142.00 |
| `disk.rand.rand_read_p50_us` | 2.33 | 150.11 | 158.80 |
| `disk.rand.rand_read_p99_us` | 29.88 | 158.05 | 383.30 |
| `disk.rand.rand_write_iops` | 134,093.00 | 10,005.50 | 482.50 |
| `disk.rand.rand_write_p50_us` | 4.00 | 80.84 | 1,642.30 |
| `disk.rand.rand_write_p99_us` | 41.75 | 102.11 | 9,979.55 |
| `disk.seq.seq_read_mbs` | 7,847.63 | 474.37 | 692.10 |
| `disk.seq.seq_write_mbs` | 2,825.75 | 242.62 | 165.65 |
| `memory.copy.copy_gbs` | 12.11 | 24.90 | 21.41 |
| `memory.scan.read_gbs` | 1.76 | 4.19 | 1.48 |
| `native.cpu.fp64_gflops` | 12.55 | - | 4.92 |
| `native.cpu.int64_gops` | 1.45 | - | 1.60 |
| `native.memory.copy_gbs` | 22.44 | - | 42.50 |
| `native.memory.latency_dram_ns` | 174.21 | - | 88.30 |
| `native.memory.latency_l1_ns` | 1.89 | - | 1.60 |
| `native.memory.latency_l2_ns` | 6.78 | - | 6.84 |
| `native.memory.latency_l3_ns` | 105.36 | - | 18.09 |
| `native.memory.triad_gbs` | 42.73 | - | 29.35 |

_3 run(s) over 3 device(s); values are per-device medians; bound=hardware; suites: quick; units: %, GB/s, GFLOPS, GOPS, IOPS, MB/s, ns, us_
