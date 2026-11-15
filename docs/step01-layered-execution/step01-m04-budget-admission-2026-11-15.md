# M04 审计预算准入与冻结稿续验 —— 交付记录（2026-11-15）

状态：**离线完成**。真实模型调用 0，正式产物写入 0，未 push、未重签、未采纳。
基线提交 `efa5f3f`；M01–M04 的生产改动在**同一份未提交工作树**里。
`[GATE] 用户确认固定输入验证后解锁真实调用` 仍未解锁。

## 1. 本模块回答的问题

用户指出的缺口：`_audit_budget_allowance()` 只在审计**即将开始时**读剩余预算并
截短额度。把总预算从 900s 抬到 1200s 并不能保证前序阶段留下足够时间——run g
最终审计只剩 186s、run i 只剩 126s，两次都被通道超时打断，且 `BLOCK` 是通道
故障而非内容结论。

M04 改成：在**每个额外动作启动之前**先判定剩余时间能否容纳
「本次动作 + 最终生效审计配置上限 + 提交收尾」。不足即保存冻结候选与
`budget_exhausted` 明确终局，不再把「时间不够」写成「内容不可行」。

## 2. 代码改动

| 文件 | 改动 |
| --- | --- |
| `step01_script_gen/repair_budget.py`（新增） | `SimClock` 确定性模拟时钟；`admit_action` 纯函数准入；`audit_reserve_sec` 用配置 cap；`freeze_candidate` / `verify_frozen_candidate` 冻结稿续验；`budget_timing_record` 四项读数 |
| `step01_script_gen/main.py` | 新增 `_final_audit_reserve_config` / `_action_cost_sec` / `_action_budget_admission` / `_budget_freeze_root` / `_freeze_budget_exhausted` / `_verify_budget_resume` / `_audit_budget_timing`；在 4 个动作入口与 2 个审计调用点接线 |
| `_text_gen/models.py` | `TextGenerationResult` 新增可选 `timing`（默认 `None`，不改任何既有构造语义） |
| `_text_gen/agent_plan_provider.py` | `_parse_response` 把已有的阶段轨迹（`headers_at` / `first_event_at` / `done_at` / `events`）挂到 `result.timing`，只作观测 |
| `step01_script_gen/audit.py` | `AuditResult` 新增可选 `timing`；每次调用落 `server_wait_sec`（`generate()` 墙钟）与 `stream_elapsed_sec`（仅真正走 SSE 事件流时有值）；**未发请求 / 调用异常 / 未埋点一律如实记 None** |
| `tests/step01_script_gen/test_repair_budget.py`（新增） | 22 项模拟时钟验收 |
| `.gitignore` | 放行 `test_repair_budget.py` |

### 准入判据

```
required = 本次动作耗时 + 最终生效审计上限(cap) + 提交收尾预留
allowed  = (剩余预算 is None) 或 (剩余预算 >= required)
```

* 「本次动作耗时」取**最终生效的超时上限**（保守最坏情况），不是猜出来的预估：
  regen 用 `AGENT_PLAN_TEXT_TIMEOUT_SEC`，修复轮用 `_repair_timeout_sec()`。
* 「最终生效审计上限」取 `_audit_budget_allowance()` 的 `info["cap"]`（即
  `AUDIT_TIMEOUT_SEC` 的最终生效值），**不能**用已被剩余预算截短过的 `allowance`
  ——否则剩余越少预留越小，判据恒真。默认 `300 + 60 = 360s` 完整审计预留。
* 剩余预算统一读既有 `_budget_left_sec()`，不另造一套时钟。

### 接线点

| 位置 | 动作 | 准入不足时 |
| --- | --- | --- |
| `main()` 镜头超限收敛轮 | `regen_over_limit` | 跳过 + 冻结候选 + `budget_exhausted` |
| `main()` 结构收敛轮 | `regen_structural_over_limit` | 同上 |
| `main()` 定向修复轮 | `repair_rounds` | 同上 |
| 提交层 attempt 循环 | `commit_repair_round` | 停止修复轮 + `summary.repair_outcome=budget_exhausted` |
| `_run_final_semantic_audit` | 最终审计 | 四项计时写 `audit.budget_timing` 与日志，**不改 payload 形状** |
| `main()` 中间轮审计 | mid_pipeline | 四项计时写 `audit.budget_timing_mid` 与日志 |

冻结候选写到 `.dsh/step01_budget_frozen/<run_id>/` 下的
`frozen_candidate.json` 与 `frozen_script.json`，**不碰 `./output` 正式产物**。

### 续验不借用旧审计结果

`freeze_candidate()` 记录只含输入 revision、稿件 digest、审计配置指纹、规则指纹、
准入读数与隔离输出根；明确 `prior_audit_reused=False`、`terminal=budget_exhausted`，
不含任何 `audit_payload` / `issues` / `status`。

`verify_frozen_candidate()` 逐项核对：输入 revision、稿件 digest、审计配置身份、
规则身份、记录里没有夹带旧审计结论、（若给了新准入）预算已恢复。五项全真才
`eligible=True`，允许从**已冻结候选**进入现有完整审计 / 提交链。配置指纹只取
稳定项，运行时读数（`left_sec` / `audit_allowance_sec`）不进指纹，避免续验恒假。

## 3. 四项计时（分别记录，不估算填充）

| 字段 | 来源 |
| --- | --- |
| `remaining_budget_sec` | 调用前 `_budget_left_sec()` |
| `audit_allowance_sec` | 调用前 `_audit_budget_allowance()[0]` |
| `server_wait_sec` | `audit.py` 中 `service.generate()` 的客户端墙钟 |
| `stream_elapsed_sec` | `agent_plan` SSE 阶段的 `trace.done_at`（仅 `trace.events` 存在时）；其它通道 `None` |

`stream_timing_source` 标注 `agent_plan_trace` / `provider_not_instrumented` /
`no_request_sent`。**非流式与其它 provider 目前不埋点，记为 `None` 并标来源，
绝不反推或估算**；这是已固定的观测缺口，不是本包声称已覆盖的能力。

## 4. 模拟时钟验收（22 项，零模型 / 零网络）

`tests/step01_script_gen/test_repair_budget.py`：

* 准入：`540s` 恰好够（180+300+60）→ `budget_ok`；`539s` 差一秒 →
  `budget_exhausted` + `freeze_required`；无总预算约束 → 沿用旧行为。
* 防「预留缩水」：`200s` 剩余即使把截短 allowance 设成 `126s`（run i 形态）也
  必须拒绝；预留恒按 cap `300+60` 计算。
* 耗尽与恢复：1200s 烧到剩 200s → 拒绝并冻结；新一轮时钟重置 → 准入通过 →
  续验五项全真、`prior_audit_reused=False`。
* 冻结候选：同输入两次序列化逐字节一致、不含时间戳、不含旧审计结论。
* 续验反例：改稿 / revision 不符 / 审计配置变更 / 规则身份变更 / 记录夹带
  `audit_payload` / 预算未恢复，六种情形均 `eligible=False`。
* 四项计时：分别记录、缺值为 `None` 且标来源；`_audit_budget_timing` 从
  `AuditResult.timing` 正确桥接。
* provider 埋点：`_parse_response(trace=...)` 把 `done_at/events` 挂上；空 trace
  时 `timing is None`。
* 隔离根：`_freeze_budget_exhausted` 只写 `tmp_path`（monkeypatch 后的隔离根），
  两个文件落盘后 `_verify_budget_resume` 对同一份稿通过。

聚焦回归（M01–M04 + 审计 / 通道相关 19 个文件）：**337 passed / 14 skipped**，
其中 14 个 skipped 是 `test_audit_authorization_guard.py` 的外部冻结数据集缺失，
与本包无关。

## 5. 剩余问题

* **M05**：run j 冻结现场 + 两组匿名自足样本的七类场景离线验收、按 M01–M05
  汇总工作包、可阅读的修订前后稿件。
* **GATE 未解锁**：有限真实模型验证（最多 2 次修复请求 + 2 次完整审计请求）
  需用户确认固定输入验证后才可开始；本包不自动加跑，沿用「run j 收口」。
* 14 个 `test_plan_phase_*` 既有失败仍未解决；M03 已在 HEAD 基线独立检出上做
  同条件对照，确认与本包无关。
* `stream_elapsed_sec` 目前只有 `agent_plan` SSE 通道有真实读数；其它通道为
  `None`。要全覆盖需在其余 provider 补同类阶段轨迹，属后续工作。
* 预算准入是**保守最坏情况**判据（按超时上限算动作耗时）；真实耗时更短时
  可能提前冻结。这是有意的取舍：宁可留明确终局，也不发注定挤占审计的请求。
* 端到端仍无 PASS；离线机制可用不等于真实模型遵从或产品结果更好。
* 工作树在 `efa5f3f` 之后有 M01–M04 未提交改动；未 push、未重签、未正式采纳。
