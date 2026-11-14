# Step A + Step B 执行记录（2026-11-14）

> 依据：《Post-R6 终版》§3.1/§3.2 与 §4 的优先执行指令。
> **Step A 零模型调用**；**Step B 仅 1 次逻辑视觉 QC（0 次图片 POST）**。
> 未触碰正式采纳身份 `0afa8074…` / `e19acd18…`，未改 `script_result.json`。

## 一、Step A：全盘无死角对账与分层清理

### A1 方法（先对平，再动手）

用 `os.scandir` 递归统计**仓库根下每一个一级项**（含 `.git`、`.pytest_tmp`、`.dsh`、
`.p1a_exp`、`output`、`reference_skills`、隐藏目录与根文件），以**文件字节数之和**
为口径；整棵树与各一级项用同一算法。

```
TOTAL 5.324 GB  16,940 files  6,197 dirs
SUM-OF-PARTS 5.324 GB  delta=0 bytes
```

**口径修正（如实记录）**：计划预估「7 GB+」，实测为 **5.324 GB**。差异原因已逐项核实：

- 仓库内**没有** `.venv` / `venv` / `node_modules` / `tools/motion_runtime/.browsers/`
  （`tools/` 合计仅 5.23 MB），不存在 2.5–4.5 GB 的依赖环境；
- `.git` 仅 62.34 MB；
- 7 GB+ 更可能是**当时 `.pytest_tmp` 的峰值**（本轮清理前它仍占 3.27 GB）叠加
  `output/` 1.66 GB 后的近似值，或使用了「磁盘占用」而非文件字节口径。

另：`.tmp/` 下 51 个 Codex 沙箱遗留目录带 `Deny DeleteSubdirectoriesAndFiles` ACL，
扫描不可读；`dir /s` 实测 **0 文件 / 0 字节**，不构成体积盲区（详见 A3）。

### A2 清理动作与释放量

| 目标 | 清理前 | 动作 | 释放 |
|---|---|---|---|
| `.pytest_tmp`（含 `r02b` 3.26 GB） | 3,273.8 MB | `cleanup_test_artifacts.py --keep-hours 0 --keep-latest 0 --apply --permanent`（49 个根） | **3,273.7 MB** |
| `%TEMP%\pytest-of-ee54` | 45.7 MB | 同上（工具覆盖该根）+ 余量删除 | **45.7 MB** |
| `output/step01_run_review/20261013_073508` | 555.8 MB | 整目录删除 | **555.8 MB** |
| `output/video/motion_fixtures` | 40.9 MB | 整目录删除 | **40.9 MB** |
| `output/video/_enhancement`（实际路径） | 156.4 MB | 删除 `_baseline`/`_commit_archive`/`_delivery`，**保留空目录壳**（`shared/paths.py` 的 `ENHANCEMENT_DIRNAME` 需要它） | **156.4 MB** |
| `.dsh/u6_closeout_20261110` 淘汰轮次 | 8.98 MB | 删除 `r2–r5`、`candidate_d1a`、`iso`、`s01a`、`u6_kfs0fix`、`u6_probe`、`u6_package`、`__pycache__`；**保留 `r6`**、脚本与证据 JSON | ~4.9 MB |
| `.p1a_exp` 旧探针 | 0.94 MB | 删除 `probe_runtime`；**保留 `audit_authorizations`** | <0.1 MB |
| `.tmp`、`.n03_*` 空目录 | 0 字节 | `.n03_*` 已删；`.tmp` 因子目录 ACL 拒绝删除，**保留（0 字节）** | 0 |
| `.venv` / `.browsers` / `node_modules` | 不存在 | 无需处置 | 0 |

**仓库内合计释放 3.938 GB**（5.324 → 1.386 GB）；另有 `%TEMP%` 45.7 MB 不在仓库口径内。

### A3 清理前后一级项对账

| 一级项 | 清理前 | 清理后 |
|---|---|---|
| `output/` | 1,663.1 MB | **910.0 MB** |
| `.pytest_tmp/` | 3,273.8 MB | **0.04 MB** |
| `reference_skills/` | 339.8 MB | 339.8 MB（参考仓库，未在清理清单内，保留） |
| `.git/` | 62.3 MB | 62.3 MB |
| `tests/` | 25.9 MB | 25.9 MB |
| `skills/` | 22.6 MB | 22.6 MB |
| `.dsh/` | 9.0 MB | **4.1 MB** |
| 其余（代码/文档/小说/基线等） | ~58 MB | ~58 MB |
| **合计** | **5.324 GB / 16,940 files** | **1.386 GB / 8,154 files** |

`output/` 剩余 910 MB 的构成（**均为未授权删除项**）：

- `output/锦衣夜行/part_01_杀青州/chapter_001/` **349.9 MB**：正式六件套 + 预算账本 +
  `vimax_working/shots/1–2/` + 历史 `snapshots/`、`step01_call_bodies/`、备份目录；
- `output/video/锦衣夜行/` **505.7 MB**：Step 02b 视频工作区与 `global/` 资产库
  （`global` 261.3 MB 已核验未动）；
- `output/step01_ab/` 54.3 MB：A/B 实验产物。

「非依赖工作区 < 500 MB」的口径核对：**代码/测试/文档/基线合计约 165 MB**（已达标）；
若把 `reference_skills` 参考 clone（339.8 MB）也算进「工作区」，则为约 505 MB；
若再算 `output/` 生产与视频资产，则必然超过 500 MB——这部分不是可清理的膨胀，
而是当前章节的交付资产。

### A4 保护清单（字节级核验）

清理前/后对 30 个受保护文件 + 2 棵资产树做 SHA-256 比对：

| 对象 | 结果 |
|---|---|
| `script_result.json` / `step01_quality.json` / `step01_audit.json` / `step01_commit.json` / `step01_adoption.json` | **30/30 未变** |
| `step01_send_budget.json`、`step02a_image_budget.json`（33/172）、`step01_audit_rounds.json` | 未变 |
| `vimax_working/shots/1/` 全部 19 个文件（FF/LF/KF/哈希/sidecar） | 未变 |
| `vimax_working/shots/2/first_frame.png`（`4089f234…`）、`frame_hash.txt`（内容 `06ab04ab252884f8`） | 未变 |
| `output/锦衣夜行/global/`（26 文件 / 28,359,731 B） | 未变 |
| `output/video/锦衣夜行/global/`（138 文件 / 261,296,539 B） | 未变 |

`step02_events.jsonl` 在 Step A 后仍为 2,855 B；Step B 的 QC 成功后**追加** 1 条
`qc_passed` 事件（2,855 → 3,228 B，旧行原样保留），属设计内的事件证据，不是清理改写。

## 二、Step B：视觉 QC 429 退避 + shot 2 FF 补验

### B1 代码加固（`shared/image_qc.py`）

`_opencode_vision_chat` 新增受控退避：

- 仅 `429 / 502 / 503 / 504` 与瞬态 `SSLError / ConnectionError` 重试；
- `QC_VISION_HTTP_RETRIES` 控制次数（默认 **2** = 最多 3 次尝试）；
- 优先 `Retry-After`，否则 `2s → 5s`；`400/401/403/404` 立即失败、绝不重试。

离线单测 `tests/shared/test_image_qc_retry.py`：**6 passed**（429→200、Retry-After、
401 不重试、SSL 重试、重试耗尽、`RETRIES=0`）；与既有 image_qc/U4/retry-cap 合跑
**34 passed**。

### B2 shot 2 FF 只读补验（U6 收口）

脚本：`.dsh/u6_closeout_20261110/r7_shot2_qc_maybe_sync.py`（走生成链同一入口
`maybe_qc_sync` + `write_sidecar`；不调用 `step02a` / `_gen_first_frame_async`）。

| 项 | 值 |
|---|---|
| 逻辑 QC | **1 次**（第 3/1/2 层子请求；本次无 429，未触发退避） |
| 结论 | **`qc_passed`**；`has_hallucination=false`；1 条 warning（`body_deformity`：持箫左手手指轻微模糊，非 critical） |
| 图片 sha256 | 检前/检后 `4089f234ff475f7a7053975cd26e0a9e347e17eec99feb8aab153708f070389e`，**未变** |
| 内容哈希 | `06ab04ab252884f8` |
| 图片 POST | **0**；账本 `33 → 33 / 172`（remaining 139） |
| sidecar | `first_frame_hallucination.json` → `status=qc_passed`（`at=2026-11-14 23:43:29`） |
| 事件流 | 追加 1 条 `qc_passed` |

**U6 S07/S08 的最后一项验收至此关闭**；shot 2 可进入 Step 02b。

## 三、证据位置

- 全盘对账：`.dsh/step_a/audit_before.json`、`audit_after.json`
- 保护清单：`.dsh/step_a/protected_before.json`、`protected_after.json`
- 清理报告：`.dsh/step_a/cleanup_dryrun.json`、`cleanup_applied.json`
- QC 运行日志：`.dsh/step_b/shot2_qc_run.log`
- 代码：`shared/image_qc.py`、`tests/shared/test_image_qc_retry.py`
