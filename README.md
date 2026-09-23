# open-benchmark

**Agent 友好的硬件性能基准测试工具**：零运行时依赖、纯 stdlib，执行器（executor）全部在本仓库内实现，不 shell out 到 fio / sysbench / CrystalDiskMark 等外部二进制；输出稳定 JSON schema，可直接被 agent 消费、聚合成榜单、以 PR 形式提交。

```bash
uv run open-benchmark run --suite quick --json -            # 一条命令拿到机器全部指标（JSON 到 stdout）
uv run open-benchmark run --suite full --json results/x.jsonl
uv run open-benchmark leaderboard results/ --out LEADERBOARD.md
uv run open-benchmark submit results/x.jsonl --repo-dir ../open-benchmark-results --dry-run
```

## 为什么不是又一个脚本

| 需求 | 现成方案的问题 | open-benchmark 的做法 |
|---|---|---|
| 一条龙、可被程序调用 | VPS 测评脚本输出给人看的报告，字段不稳定 | `--json -` 输出 `open-benchmark.run/v1` 文档，stdout 只放 JSON，人读文本走 stderr |
| 不依赖 GUI/闭源软件 | CrystalDiskMark / PerformanceTest 只有 GUI 或闭源 | 全部执行器在本仓库实现（含 Windows 无缓冲 I/O 的 ctypes 实现） |
| 可复现、可追责 | 脚本调用外部二进制，版本漂移后结果不可比 | 每个结果记录 `backend` + 工具版本 + 参数；原生内核按源码 hash 编译缓存 |
| 榜单可信 | 分不清"测到硬件"还是"测到解释器" | 每个 metric 带 `bound: hardware \| mixed \| interpreter`，榜单默认只取 `hardware` |

## 安装

```bash
uv sync                 # 开发环境（含 pytest / ruff）
uv run open-benchmark --help
# 或直接安装
uv tool install .
```

要求 Python >= 3.11。**运行时零依赖**（不装任何第三方包）。

## 命令

| 命令 | 作用 |
|---|---|
| `open-benchmark run [--suite quick\|full] [--only X] [--exclude Y] [--repeat N] [--json PATH\|-]` | 跑基准并输出 run 文档 |
| `open-benchmark devices [--json PATH]` | 只采集设备指纹（CPU/内存条/盘/GPU/BIOS） |
| `open-benchmark executors [--json -]` | 列出执行器、标签与所属 suite |
| `open-benchmark leaderboard PATH... [--bound hardware] [--metric cpu] [--out FILE]` | 把 JSONL 聚合成 markdown 榜单 |
| `open-benchmark submit PATH... --repo-dir DIR [--dry-run]` | 把结果作为 PR 提交到榜单仓库 |
| `open-benchmark schema` | 打印 schema 版本与示例文档 |

`run` 的退出码：`0` 全部成功（`skipped` 不算失败）、`1` 有执行器报错。

## 执行器与 suite

`quick` ≈ 1–2 分钟（9 个执行器）；`full` 加上纯解释器类与更大的工作量（12 个）。

| 执行器 | 指标 | 覆盖 | bound | 实现方式（全部在仓库内） |
|---|---|---|---|---|
| `cpu.hash` | `sha256_mbs` / `blake2b_mbs` | CPU 单核 | hardware | 单次 `hashlib` 大缓冲（OpenSSL，GIL 释放） |
| `cpu.compress` | `compress_mbs` / `decompress_mbs` | CPU 单核 | hardware | zlib（native C） |
| `cpu.thread_scale` | `single_thread_mbs` / `all_threads_mbs` / `scale_efficiency_pct` | CPU 多核/功耗墙 | hardware | 线程池 × 每线程独立缓冲 |
| `cpu.python_sieve` | `passes_per_s` | 解释器 | interpreter | 纯 Python 筛法 |
| `memory.copy` | `copy_gbs` | 内存带宽 | hardware | `bytes` 切片（libc memcpy） |
| `memory.scan` | `read_gbs` | 内存读带宽 | hardware | `bytes.count`（libc memchr） |
| `memory.fill` | `write_gbs` | 内存写带宽 | hardware | `mmap` 切片赋值 |
| `memory.python_random` | `ns_per_access` | 随机访问 | interpreter | `array` 随机索引 |
| `disk.seq` | `seq_write_mbs` / `seq_read_mbs` | 顺序吞吐 | hardware | 自实现无缓冲 I/O（见下） |
| `disk.rand` | `rand_read_iops` / `rand_write_iops` / `p50` / `p99` | 4K QD1 IOPS+延迟 | hardware | 同上 |
| `native.cpu` | `fp64_gflops` / `int64_gops` | CPU 单线程 ALU/FP | hardware | 仓库内 `kernels.c`，按需编译 |
| `native.memory` | `triad_gbs` / `copy_gbs` / `latency_{l1,l2,l3,dram}_ns` | 带宽+缓存层级延迟 | hardware | 同上（pointer chase） |

### 无缓冲 I/O 是怎么做的

* **Windows**：`ctypes` 调 `CreateFileW` + `FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH`，`OVERLAPPED` 指定偏移做定位读写，缓冲用 `mmap` 保证页对齐（这是 `FILE_FLAG_NO_BUFFERING` 的硬要求）。
* **POSIX**：`open(..., O_DIRECT)` + libc `pread/pwrite`，缓冲同样来自匿名 `mmap`。
* 文件系统拒绝（tmpfs、部分网络盘）时自动回退缓冲 I/O，并在 `Result.backend` 里标记 `+fallback`，避免把两种口径混进同一榜单。

### 可选的原生内核

`native.*` 需要 C 编译器（自动探测 `gcc` / `cc` / `clang` / `zig` / `cl` / `python -m ziglang cc`，可用 `OPEN_BENCHMARK_CC` 覆盖）。没有编译器时这些执行器报 `skipped` 并写明原因，其余指标照常输出——榜单里不会出现半真半假的数字。内核按「源码 hash + 编译器」缓存到 `~/.cache/open-benchmark/native/`。

机器上没有任何 C 编译器时，可以只为了拿到内核而装 zig：`uv sync --extra native`（ziglang wheel 提供 `zig cc`）。

## 输出 schema

一个 run == 一个 JSON 对象；多 run == JSONL（一行一个）。完整说明见 [docs/schema.md](docs/schema.md)。

```json
{
  "schema": "open-benchmark.run/v1",
  "run_id": "9f1c...",
  "suite": "quick",
  "device": {"fingerprint": "ab12cd34ef567890", "cpu": {"model": "..."}, "memory": {...}, "disks": [...]},
  "env": {"tool": "open-benchmark 0.1.0", "native_toolchain": "gcc", "scale": 1.0},
  "results": [
    {"name": "disk.seq", "status": "ok", "backend": "win32-unbuffered",
     "params": {"size_mb": 256, "block_kb": 4096},
     "metrics": [{"key": "seq_read_mbs", "value": 7100.5, "unit": "MB/s",
                  "bound": "hardware", "aggregation": "median", "n": 3,
                  "samples": [7050.1, 7100.5, 7200.9], "spread_pct": 2.1}]}
  ]
}
```

设备指纹（`device.fingerprint`）只由「可比较的硬件字段」算出：CPU 型号、物理/逻辑核数、内存总量与每条的容量/频率、磁盘型号+容量+介质、机型、OS/架构。**主机名、序列号、时间戳不参与**，因此同款机器会落进榜单同一行。

## 榜单与 PR

```bash
uv run open-benchmark run --suite full --json results/local/mine.jsonl
uv run open-benchmark leaderboard results --suite full --bound hardware --out LEADERBOARD.md
uv run open-benchmark submit results/local/mine.jsonl --repo-dir ../open-benchmark-results
```

榜单按 `device.fingerprint` 分组、对每个指标取中位数。`quick` 与 `full` 的工作量不同，混在一起会得到不可比的数字，所以默认会提示；用 `--suite` 明确口径。

`submit` 只做三件事：把 JSONL 复制进 `results/`、切分支提交、`gh pr create`。它依赖 `gh` 已登录与目标仓库是 git checkout；`--dry-run` 只打印计划。

## CI

[`.github/workflows/bench.yml`](.github/workflows/bench.yml)：matrix（ubuntu / windows / macos）跑 `quick` suite，产物上传为 artifact；汇总 job 把新结果放进 `results/ci/`，按 suite 分别重建 `LEADERBOARD.md`（full）与 `LEADERBOARD-quick.md`（quick），再开 PR。CI runner 是噪声很大的虚拟机，只用于同一 runner 上的回归；真机结果请用 `open-benchmark submit` 提交。

## 开发

```bash
uv run pytest -q
uv run ruff check .
```

## Roadmap

* 磁盘 QD32 并发测试、DirectStorage/io_uring 后端
* GPU 计算（OpenCL/Vulkan 查询 + 计算内核）
* 网络（loopback 与 LAN 吞吐）
* 结果直传 HuggingFace dataset（注意 `HF_ENDPOINT=https://hf-mirror.com`）
* 固定 kernel revision 的编译产物指纹，写进 `Result.tool_versions`

## License

MIT
