# Result schema — `open-benchmark.run/v1`

一个 run 就是一行 JSON（JSONL）。设计目标是「agent 直接读，不用猜」：单位固定、来源明确、参数齐全、原始样本保留。

## 顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | string | `open-benchmark.run/v1`；破坏性变更才改这一版号 |
| `run_id` | string | 本次运行的短 id（12 hex） |
| `suite` | string | `quick` / `full` |
| `started_at` / `ended_at` | string | UTC，`YYYY-MM-DDTHH:MM:SSZ` |
| `device` | object | 设备指纹，见下 |
| `env` | object | Python 版本、平台、`tool`（工具名+版本）、原生工具链、`scale`、`repeat`、`wall_seconds` |
| `results` | array | 每个执行器一条，见下 |
| `notes` | array[string] | 例如「native kernels unavailable: ...」 |

## `device`

| 字段 | 说明 |
|---|---|
| `fingerprint` | 16 hex，榜单行的主键。由 CPU 型号、物理/逻辑核、内存总量与每条容量/频率、磁盘型号+容量+介质、机型、OS/arch 派生；**不含**主机名/序列号/时间 |
| `os` / `os_version` / `arch` | 平台标识（Windows 用 CIM，Linux 用 `/etc/os-release` + kernel，macOS 用 `platform.mac_ver`） |
| `machine_class` | `physical` / `vm` / `container` / `unknown` |
| `cpu` | `model`、`vendor`、`physical_cores`、`logical_cores`、`base_mhz`、`max_mhz`、`l2_kb`、`l3_kb`、`sockets`（Linux 还有 `flags`） |
| `memory` | `total_bytes`、`modules[]`（`locator`、`size_bytes`、`speed_mtps`、`configured_speed_mtps`、`manufacturer`、`part_number`） |
| `disks[]` | `model`、`size_bytes`、`media`(`ssd`/`hdd`/`unknown`)、`bus`、`firmware` |
| `gpus[]` | `name`、`vram_bytes`、`driver` |
| `system` | `vendor`、`model`、`bios_version`、`bios_date` |
| `raw` | 平台原始载荷（CIM JSON / `/proc/cpuinfo` 首块 / `system_profiler` 片段），便于日后重新解析 |

## `results[]`

| 字段 | 说明 |
|---|---|
| `name` | 执行器名，如 `disk.seq` |
| `status` | `ok` / `skipped` / `error`（`skipped` 带原因，例如没有 C 编译器；不计入失败退出码） |
| `error` | 失败或跳过的原因 |
| `backend` | 这个数字是怎么来的：`openssl ...`、`win32-unbuffered`、`posix-o_direct+fallback`、`C kernel via gcc` 等 |
| `params` | 本次工作负载参数（缓冲区大小、块大小、QD、时长、repeat） |
| `duration_s` | 该执行器耗时 |
| `tool_versions` | 相关库/内核版本 |
| `metrics[]` | 见下 |

## `metrics[]`

| 字段 | 说明 |
|---|---|
| `key` | 指标名，全小写下划线，如 `seq_read_mbs` |
| `value` | 聚合后的数值（默认中位数） |
| `unit` | `MB/s`、`GB/s`、`IOPS`、`us`、`ns`、`GFLOPS`、`GOPS`、`MHz`、`%`、`count`、`passes/s` |
| `bound` | **`hardware`** 测到的是硬件；**`mixed`** 原生库+解释器胶水；**`interpreter`** 测到的主要是解释器 |
| `higher_is_better` | 延迟类指标为 `false` |
| `aggregation` | `median`（默认）/ `best` / `mean` / `single` |
| `samples` | 原始样本（保留，便于重算分位数/复核噪声） |
| `n` | 样本数 |
| `spread_pct` | `(max-min)/best`，百分比；>10% 说明这次测量噪声偏大 |
| `notes` | 口径说明（例如「copy 统计读写双向流量」） |

## 消费建议

1. **榜单只取 `bound == "hardware"`**，`interpreter` 类只在同解释器版本间比较（`env.python`）。
2. 同一 `device.fingerprint` 的多份 run，**先按指标取中位数再排名**；不要先排名再平均。
3. `spread_pct` 大于 10% 的指标建议重跑或排除。
4. 判断可比性时同时看 `params`（块大小/时长不同则不同口径）与 `backend`（`+fallback` 表示走了缓冲 I/O，不能和 `o_direct` 直接比）。
5. 字段是**增量扩展**的：新指标/新字段直接加，旧字段不改名。解析方应忽略未知字段（`Run.from_dict` 就是这么做的）。
6. 提交前先跑 `open-benchmark validate`（重算指纹 + 字段自洽），要对比两次运行/两台机器用 `open-benchmark compare`（按指纹取中位数再逐指标给判定）。
