# Step01 M01–M05 修复后真实重跑验证记录（run k，2026-11-15）

> 性质：M01–M05 提交（`d816a09`）**推送后**的第一次真实模型运行，如实记账。
> 结论：run k 在**候选生成层**就 FATAL（`EventContractBrokenError`），3 次生成全部因
> 台词事件顺序回退（`lines_order_violation`）被判无效，**未进入选稿/修复/提交环节**。
> 因此本轮**无法验证 M01–M05 修复的效果**——那套链路（阶段归因、请求授权、有界反馈、
> 预算准入、固定输入验收）这次一次都没被触发。不以离线测试或历史回放冒充真实收益。

## 1. 运行前核对

- 代码：`d816a09`（M01–M05）已推送到 `github/master`（`61d7433..d816a09`）。
- 通道 / 配置 / 输入：与 run i / run j 完全一致——`agent_plan` provider、
  `deepseek-v4.1-flash`、`opencode.ai` 订阅端点、`锦衣夜行 / 1 / 杀青州 / 1`。
- 解释器：Python 3.14.2（`pythoncore-3.14-64`）。
- 命令：`OUTPUT_BASE=<隔离根> PYTHONIOENCODING=utf-8 python step01_script_gen/main.py`，
  单章、单次运行，不重试、不加 `--force`。
- 隔离根：`.dsh/step01_fix_verify_20261115k/output`，三份正式产物零写入。
- GATE：用户在本轮明确要求「跑下 step01，看修复效果」，构成对真实调用的确认。

## 2. 真实读数（来自 run k 产物）

| 项 | run j（M01–M05 前） | **run k（M01–M05 后）** |
|---|---|---|
| run_id | `20261115T174547-19332` | `20261115T202018-23956` |
| 时长 / 退出 | 10m24s / `exit=0` | **9m19s / `exit=1`（FATAL）** |
| 真实调用（发送数） | 14（按 run j 记录） | **9（正文生成 3；计划修订 1、内容核验 1、提取 4）** |
| `outcome_code` | `commit_blocked` | **`EventContractBrokenError`** |
| `stop_layer` | commit | **candidate（候选层硬门）** |
| 修复轮 | 进入，`resolved=0 / no_target_resolved` | **未进入（0 修复轮）** |
| 终局归因 | 模型答卷零有效改善 | **3/3 顺序门拒收（计划内部顺序冲突）** |
| `step01_commit.json` | 有（BLOCK） | **无** |
| `step01_repair_rounds.json` | 有 | **无** |

`step01_dialogue_incomplete.json`：
`verdict=event_contract_broken`、`total_lines=3`、
`attempt_stats={total:3, dialogue_broken:0, gate_errors:0, contract_broken:3, other_failed:0}`、
`broken_batches=[[{line:"", reason:"event_contract: lines_order_violation"}]]`。
控制台示例：事件履约契约违规「D1 在 D2 之后首次出现（事件/台词顺序不得回退）」。

## 3. 失败定位与归因（只读核查）

- 报错码 `lines_order_violation` 的检查逻辑在 `step01_script_gen/plan_feasibility.py`
  （跨镜「首次声明序」检查：后编号台词不得先于前编号台词首次出现）。
- **直接失败机制已定位为计划内部顺序冲突**（2026-11-15 复核，证据见
  [P01 证据包](step01-p01-voice-order-evidence-20261115.md)）：最终计划
  `dialogue_lines` 数组序是 D1 谎称、D2 威胁，而 `shot_beats` 承载序是
  B9 威胁（台词2）→ B10 谎称（台词1）；生成契约同时携带这两份顺序，
  **忠实执行节拍的候选必然**触发 `D1 在 D2 之后首次出现`。
- 三份候选逐条对应：均按 B9 `lines:[2]` → B10 `lines:[1]` → B11 `lines:[3]`
  发声（首次出现序 D2→D1→D3）；**不是随机波动**。
- 出站请求已**完整重建**并比对摘要一致（`prompt_chars=46089`、
  `prompt_sha256=1e8638…`，三次生成相同），请求确实携带了这份计划与契约。
- `M01–M05` 提交 `d816a09` 未触碰 `plan_feasibility.py`、`prompts.py`；
  `_text_gen/agent_plan_provider.py` 的改动只是加观测字段 `timing`（`_with_timing`）。
  这只说明**判据与提示文本未改**，不等于已排除改动对计划生产或其他调用路径的影响；
  本记录只主张"直接失败机制是计划冲突"，不主张"排除 M01–M05 影响"。

## 4. 对 M01–M05 的效果判断

- **未能证明修复收益，也未被证伪**：run k 与 run j 失败在不同层（candidate vs commit），
  输入虽同、答卷与路径完全不同，**不构成可比对照**，不推算通过率。
- run j 已验证到的接线证据（帧来源映射、四节点账本、越权拒收）依旧成立，但那是
  **M01–M05 之前**的 run j 现场；M01–M05 新增的有界反馈闭环与预算准入在真实模型上
  **仍无正例**。
- 产品结果层面：a→k 全部未通过，端到端 PASS 仍为 0。

## 5. 边界复核

- `./output` 正式产物未替换；历史 run a–j 现场只读未改。
- `.dsh/` 被忽略，run k 全部产物落隔离根。
- 本轮发送 9 次（正文生成 3、计划修订 1、内容核验 1、提取 4）；`git status` 除本文件外无源码改动；无正式重签、无采纳。

## 6. 后续（未执行，待用户决策）

1. 若要看 M01–M05 的真实效果，需在**不触发候选层事件契约破损**的前提下重跑，
   或直接构造能通过候选层、进入修复轮的固定输入（对应 M05 固定输入验证）。
2. 候选层 `lines_order_violation` 连续 3/3 触发是否属异常敏感度，可单独评估检查器
   判据（非本次 M01–M05 范围，需独立决策）。

## 7. 结构化证据

- `.dsh/step01_fix_verify_20261115k/console.log`、`exit_code.txt`（`exit=1`）。
- 章节目录 `step01_metrics.json`（`outcome_code=EventContractBrokenError`，
  `calls={script_generation:3}`，`phases=[{_total, failed, 558.0s}]`）、
  `step01_dialogue_incomplete.json`、`step01_replan_rounds.jsonl`、`step01_timing.json`、
  `step01_calls.jsonl`、`step01_call_bodies/`。
- **无** `script_result.json`、`step01_commit.json`、`step01_audit.json`、
  `step01_repair_rounds.json`（候选层即 FATAL）。
