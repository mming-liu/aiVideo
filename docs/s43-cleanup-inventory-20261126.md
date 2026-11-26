# S43 旧流程资料盘点与清理候选（未删除）

**日期：** 2026-11-26
**状态：** A 已按批准执行清理；B 保留待外部 owner 确认。最新实际释放与核对见 [S44 收口](s44-review-closeout-20261126.md)。
**扫描根：** `C:\myFloders\git\aiVideo`；另扫描外部 worktree `C:\myFloders\git\aiVideo-s14base`。

## 一、15GB 主要在哪里

| 位置 | 大小 | 文件数 | 判断 |
|---|---:|---:|---|
| `.dsh/tmp` | **12.68 GB** | 6180 | DSH 测试临时媒体（主要是 Step02b/04 测试生成的 MP4） |
| `C:\myFloders\git\aiVideo-s14base\.pytest_tmp` | **7.645 GB** | 21685 | 外部 worktree 的 pytest 临时媒体 |
| `.dsh` 其他目录 | 0.029 GB | 784 | 早期 Step01 运行记录 |
| `.tmp` 全部 | 1.102 GB | 28782 | 含 S35/S26 证据与早期 s14/s11 运行 |
| `output` | 0.895 GB | 3200 | 正式产物/图片/视频；保留 |
| `baselines` | 0.005 GB | 165 | 基线；保留 |
| `reference_skills` | 0.332 GB | 1302 | 外部技能资料；非本包 |

结论：**15GB 主要不是 `.tmp` 旧目录，而是 `.dsh/tmp` 的测试媒体，以及外部 worktree 的 `.pytest_tmp` 测试媒体。** 不能按“旧目录”或“`.tmp`”一刀切。

## 二、主要候选（需确认后删除）

### A. 当前仓库 `.dsh/tmp` 测试媒体

| 绝对路径 | 大小 | 用途 | 保留依据 | 预计释放 |
|---|---:|---|---|---:|
| `C:\myFloders\git\aiVideo\.dsh\tmp\fix2` | 5.956 GB | Step02b/04 合并测试输出，含 `final.mp4` 与分镜视频 | 测试临时产物；代码/测试不引用；非正式产物 | 5.956 GB |
| `C:\myFloders\git\aiVideo\.dsh\tmp\p0304` | 3.517 GB | Step02b/04 测试输出 | 同上 | 3.517 GB |
| `C:\myFloders\git\aiVideo\.dsh\tmp\p02ab` | 3.185 GB | Step02b 测试输出 | 同上 | 3.185 GB |

小计：**12.658 GB**。最后写入均为 2026-11-15，早于当前 S35–S42 工作；不包含 S35/S26 证据。

### B. 外部 worktree 的 pytest 临时媒体（需 owner 确认）

| 绝对路径 | 大小 | 用途 | 保留依据 | 预计释放 |
|---|---:|---|---|---:|
| `C:\myFloders\git\aiVideo-s14base\.pytest_tmp` | 7.645 GB | 其他 worktree 的 motion/Step02 测试临时输出 | **属于外部 worktree，可能是其他执行者现场**；未确认前不删 | 7.645 GB（待确认） |

小计：**7.645 GB，暂不列入默认清理**。

## 三、明确保留

- `output/` 正式产物、正式图片/视频、`global/` 身份与采纳记录。
- `baselines/`、`tests/fixtures/`、代码、报告、当前 `.tmp/s35/` 和 `.tmp/s26/` 证据。
- `.dsh/` 下除 `tmp/fix2`、`tmp/p0304`、`tmp/p02ab` 外的早期 Step01 记录（体积很小，保留作恢复）。
- `C:\myFloders\git\aiVideo-s14base` 的源码与 `.git`；仅评估其 `.pytest_tmp`，且需 owner 确认。
- `.tmp/s14a` worktree（当前 `git worktree list` 注册，属其他分支）。
- 其他执行者正在使用的目录、未提交修改。

## 四、不默认清理的老 `.tmp` 证据

`.tmp/s14`（0.592 GB）、`.tmp/s11_batch`（0.100 GB）、`.tmp/s14_closeout_codex`（0.083 GB）、`.tmp/s14a`（0.065 GB）虽为早期运行资料，但可能用于对账/恢复；本清单不提议删除，除非另行确认。

## 五、删除前检查与回退

若确认 A：

1. 先确认没有进程占用 `.dsh/tmp/fix2`、`p0304`、`p02ab`；
2. 记录三目录文件数与总字节；
3. 删除后核对 `.tmp/s35/`、`.tmp/s26/`、`output/`、`baselines/` 摘要不变；
4. 报告实际释放量。

若确认 B：先取得外部 worktree owner 确认；只删 `.pytest_tmp`，不动源码/`.git`。

**执行结果：** A 三目录已删除，实际释放 **13,591,182,214 字节（12.658 GiB）**；B 未动。删除后 `.tmp/s35`、`.tmp/s26`、`output`、`baselines` 保留，保护集 391 项一致。
