# 2026-11-12 合并前基线快照（上一版 Step01 结果）

本目录是**只读留档**，不参与任何运行、不被任何阶段读取、不是「离线/生产」两套实现中的一套。
它保存的是 `contract/step01-layered-execution` 合并进 `master` **之前**那一版
（`bc5c50e`）的判定结果与证据，用来回答：新合同覆盖 Step01 原判定之后，
修复效果是变好了还是变差了。

用户裁决（2026-11-12）：合并后的代码统一覆盖原有 Step01 结果，不区分生产与离线，
整个系统只有一套行为。因此**不保留旧路径的开关，也不做双轨回退**；旧结果以本快照形式保留，
仅用于比对，不用于执行。

## 目录

| 路径 | 内容 |
|---|---|
| `manifest.json` | 本目录所有文件的 sha256、来源路径与 commit；`--verify` 可自检完整性 |
| `regression_baseline.json` | 上一版回归读数、S06 真实模型 A/B、U6 冻结标识、S01 十条裁决、三族夹具预期码、当时门禁状态 |
| `compare.py` | 比对工具（只读，无模型/媒体/网络调用） |
| `source_bc5c50e/` | `git show bc5c50e:` 导出的 8 个 Step01 关键源码，便于干净 checkout 下复现旧判定 |
| `step01_outputs/chapter_001/` | chapter_001 的 15 个 Step01 正式 json（稿件、审计、修复轮次、提交、预算收据等） |
| `step01_outputs/run_review_20261013/` | 历史复盘的稿件与 advisory / blocked / initial 审计判定 |
| `s06_ab/runs_20261112T070909/` | S06 真实模型 A/B 对照结果、发送记录与 meta |
| `s06_ab/phase2_20261112T072011/` | 受限修订、终局审计、修复诊断与未变稿件 |
| `s06_ab/prompt_pairs_20261112T070806/` | 新旧契约的成对提示词文本 |
| `s06_ab/tools/` | 当时跑 A/B 的脚本，可查口径 |
| `u6_frozen/` | U6 冻结基线的 111 个 json / 脚本 / 日志（4.3MB 参考大图未复制） |
| `misc/` | 修复区反向重建的未跟踪基线 zip |

这些文件原本大多在 `output/` 与 `.pytest_tmp/` 下、被 `.gitignore` 忽略，
**干净 checkout 回放不了**；这里已实体化为版本化受控快照。磁盘上的原始 `output/` 产物
未被删除、未被改写，本目录只是额外副本。

## 上一版结论（比对时的参照）

- **S06 判定**：新审计契约在「真实状态矛盾」样本检出旧契约漏掉的 `ACTION_OMISSION`；
  耗时/tokens 203s / 33,945 对 331s / 46,472。但「合法省略减少误拒」未被真正检验。
  8 次真实发送全部成功，通道 `agent_plan` / `deepseek-v4.1-flash`，每格 n=1。
- **受限修订整轮回退**：7 条确认缺陷入轮、点名 4 镜，shot7/11 因未授权 `keyframes`
  整镜替换被拒，shot1/6 `resolved=0`，稿件 revision 未变，**7 条缺陷一条也没消掉**。
- **终局审计**：11 条全 advisory / 0 blocking（冻结审计 10 条 / 1 blocking），
  漏掉 3 条确认缺陷；另发现镜1「笛声」vs 原文箫音、镜12 `on_screen` 矛盾两处疑似真问题。
- **当时结论**：证据不足以放宽正式门禁；模型给严重度不稳定而门禁依赖 `blocking`；
  瓶颈是修订授权面太窄（道具/状态修复需动同帧 `keyframes`/布局），不是模型输出面太大。
- **当时门禁状态**：Step01 时长 `duration_budget` 仍为 BLOCK；
  `audit_adjudication.gate_effect = shadow_only`（确定性发现只进报告、不改 severity/blocking）。
- **回归**：master 全量 4212 passed / 278 skipped / 4 xfailed / 0 failed；
  Step01 全量 4189 passed / 278 skipped / 4 xfailed；受影响子集 950 passed / 27 skipped / 0 failed。

## 怎么比对

全部用项目解释器，且带上环境变量避免本机删除确认门打断：

```powershell
$py = "C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe"
cd baselines/2026-11-12-pre-layered

# 1) 快照完整性自检
& $py compare.py --verify

# 2) 判定差异：旧审计 vs 当前代码跑出的审计（findings 逐项列出新增/消失/严重度变化）
& $py compare.py --old step01_outputs/chapter_001/step01_audit.json --new <当前产出.json>

# 3) 用当前代码的语义检查器重跑三族夹具，与上一版声明的六码对照
& $py compare.py --fixtures

# 4) 上一版回归读数（可加 --current <json> 做数值对比）
& $py compare.py --regression
```

回归复现命令（按需分组，避免单进程触发确认门）：

```powershell
$env:CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR = (Resolve-Path ../../.pytest_tmp).Path + '\safe_accept'
& $py -m pytest -q <目录或测试文件> --basetemp=../../.pytest_tmp/<唯一名> -p no:cacheprovider
```

### 判读规则（工具不自动下结论）

| 差异 | 可能是变好 | 可能是变差 |
|---|---|---|
| 新版新增检出 | 漏检减少，真缺陷被接住 | 误报增加，合法表达被误拒 |
| 新版不再检出 | 误拒减少，合法表达放行 | 漏检增加，真缺陷被放走 |
| 严重度升级 | 责任接住了 | 准入变严导致返工增加 |
| 修订轮数 / 作废素材下降 | 返工减少 | —— |
| Step01 调用数下降但下游重生成上升 | —— | 成本只是转移，不是下降 |

必须结合 `regression_baseline.json` 的 S01 十条裁决表（7 确认缺陷 / 1 合法表达 /
1 待裁决 / 1 改进）逐项裁定。三族夹具里的时间跳跃、画外声音、听者反应是**合法正例**，
不应出现在任何一版的检出中；六个缺陷码只在变异样本中应被检出。

## 未包含

- `output/` 其余约 1.7GB 素材、`u3_sheet` 的 4.3MB 参考大图（体积原因）。
- 旧 dsh 会话的对话上下文（仍在 `~/.dsh/sessions/`，未改动）。
- 任何真实模型 / 媒体调用的重跑结果——本快照只读，不含新调用。

## 与合并的关系

本快照随 `contract/step01-layered-execution` 一起进入 `master`。
合并后系统是**一套代码**：Step01 的原有判定被新执行合同覆盖，
没有「正式 / 离线」或「候选 / 生产」两套准入语义。需要回看旧行为时，
用 `source_bc5c50e/` 里的源码配合本目录产物复现，而不是把旧路径重新接回运行时。
