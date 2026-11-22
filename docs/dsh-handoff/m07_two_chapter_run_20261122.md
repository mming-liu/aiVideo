# M07 交接：Step01 两章各一轮隔离真实运行（2026-11-22）

执行端：**dsh**，沿用 AGENTS.md 的 Step 01 订阅授权（`agent_plan`，OpenCode Go 订阅端点）。
Kiro 已完成离线修复与准备，**未发送任何模型调用**。

## 1. 固定代码

| 项 | 值 |
|---|---|
| 提交 | `e5958386898d6fbb61c2620de93e9daafeb1a108`（含 M06 分派修复 `409c11c`、发送额度 `ce49901` 及其 Windows 锁修复 `e595838`）。runner 从快照 import `init_file`，快照必须含 `ce49901` 之后的提交 |
| 规则指纹 | 运行前读 `step01_script_gen.quality_issues.RULE_SOURCE_FINGERPRINT` 并记录 |
| 主工作区未提交改动 | `step02a_frame_gen/verify_acceptance.py`、`tests/step02a_frame_gen/test_verify_only.py`（用户的，与本次无关；运行从快照启动，不受影响） |

## 2. 运行

```powershell
python .tmp/s14/m07_run.py --commit <SHA> --preflight     # 只读核对，0 次调用
python .tmp/s14/m07_run.py --commit <SHA> --chapters 1 2  # 两章各 1 轮
```

`.tmp/s14/m07_run.py` 做的事：

- `git archive <SHA>` 物化到 `.tmp/s14/m07/code_<sha12>/`，复制 `.env.content`、`.env.keys`、小说原文；从快照启动。
- 每轮独立 `OUTPUT_BASE` / `TEXT_CALL_LEDGER` / `STEP01_RUN_ID`，输出在 `.tmp/s14/m07/runs/m07_ch{1,2}_r01/`。
  目录已存在就停止，不覆盖。
- 只在子进程环境设 `AUDIT_TIMEOUT_SEC=300`（用户裁决）；`STEP01_TOTAL_BUDGET_SEC=1200` 沿用 `.env.content`。
- **预检（发调用前）**：在快照代码子进程、与正式运行同一环境中导入生产解析器
  （`_text_gen.config`、`agent_plan_provider.chat_completions_url / is_subscription_endpoint`、
  `audit.resolve_audit_channel`、`repair_targets.resolve_repair_channel`、
  `runtime._final_audit_reserve_config()`、`runtime._total_budget_sec()`），断言：三处 provider 均为 `agent_plan`、
  端点为订阅端点、模型均为 `deepseek-v4.1-flash`、审计上限 300、总预算 1200。任一不符即非零退出，Step01 不启动。
- **发送额度**：每轮 `init_file(<run_root>/send_quota.json, 40)` 并设 `TEXT_SEND_QUOTA_FILE`。
  `_text_gen/agent_plan_provider.py::_post`（流式/非流式唯一发送出口）在每次 `requests.post` 之前原子占用一次额度，
  provider 内部重试也各占一次；用满抛 `SendQuotaExhausted`、额度文件缺失/不可读/形状非法或锁超时抛
  `SendQuotaUnavailable`，POST 都不发出，且不被重试策略当作瞬态错误。收尾写本地记录不经过额度。
  两轮合计上限 80 次，不自动追加到每章 3 轮。外层另有 1500 秒硬超时兜底。
- `summary.json` 分开列 `actual_sends`（额度文件 `used`，即实际 POST 次数，含重试）、
  `chapter_ledger_calls`（章节目录 `step01_calls.jsonl` 行数，即逻辑调用数）与 `quota_exhausted`。
- 运行前后核对正式章 `script_result.json` 的 sha256，结果写进 `.tmp/s14/m07/summary.json` 的 `formal_unchanged`。

不要：覆盖正式章、采纳或重签 R7.2、推送、改 `.env.content`。

## 3. 收口要报告的内容（每章）

1. 是否产出可提交稿（`step01_commit.json` 的 `status`）。
2. 首个阻塞点：计划层（拒收码与轮次）/ 候选层 / 提交门（blocking 码）。
3. 提交修复是否获准进入：`step01_metrics.json` 的 `budget.frozen_candidate`（有就是没进入）与 `repair` 计数。
4. 审计是否超时：`step01_timing.json` 的 `commit_audit` / `final_audit` 时长与状态。
5. 实际发送次数与章节台账调用数分开报（`summary.json` 的 `actual_sends`、`chapter_ledger_calls`），以及通道。
6. **若触发了来源修正通道的核心摘要修改**（`step01_replan_rounds.jsonl` 中 `route=source_basis_revision` 且
   `kind=applied`，候选 `source_revision_context.core_update_trace` 非空）：对照原文列出每个被改字段的前后文本，
   以及独立来源保留核验（`plan_verification.source_preservation`）的结论与理由。**没触发就记为本轮未验证**。
   该授权是隔离验证项，不是正式采纳标准（用户 2026-11-22）。

## 4. 离线准备的证据（Kiro）

- M06 分派修复 `409c11c`：17 份 S11 冻结输入经 `revise_plan_once` 回放，不再返回 `invalid_target`，
  全部进入 `plan_revision.delta.v1` 备用修订（每份 1 次请求、0 次收窄槽位请求）。用「恢复 n02 正文与来源为
  原文逐字句」的最小增量作答：ch1_r08 的 5 份 `applied`（n02 逐字在原文、事实与声音身份不变、到达统一重验）；
  ch1_r05 / ch1_r18 的 12 份 `illegal view_voice_unit_unplaced`——这两轮基线里 n02、d02 本来就没有承载节拍，
  最小增量没补节拍，属合法拒收，需要模型给出含节拍的完整修法。
- 回放脚本：`.tmp/s14/m06_dispatch_replay.py`（`count` / `restore` 两种模式），结果 `.tmp/s14/m06_dispatch_{count,restore}.json`。

## 5. 放行条件离线验收（Kiro，2026-11-22，0 次模型调用）

- 发送额度（`tests/_text_gen/test_send_quota.py`，假传输 patch `requests.post`）：额度 40 时第 40 次发出、第 41 次
  抛 `SendQuotaExhausted` 且 `post.call_count` 仍为 40；5xx 重试两次 POST 各占一次额度，剩 1 次时重试被挡在传输层前；
  额度文件缺失/非法 JSON/类型错/负数均 0 次 POST；5 线程并发 50 次占用恰得 1..25、拒 25；用满后本地收尾记录可写。
  `tests/_text_gen` 整目录连续两次 123 passed。
- 整目录跑时曾偶发并发用例失败：Windows 上刚删除的锁文件处于 delete-pending，`O_EXCL` 创建报 `PermissionError`
  而非 `FileExistsError`，被当成“额度不可读”。`e595838` 改为按锁忙等待（超时仍 fail-closed），`os.replace` 遇
  `PermissionError` 有限重试；独立压测 400 轮并发从 3 轮失败降为 0。
- 预检（`.tmp/s14/m07_preflight_check.py e595838…`）：正常环境通过（三处 `deepseek-v4.1-flash`、300、1200）；
  `AUDIT_TEXT_MODEL` / `REPAIR_TEXT_MODEL` / `SCRIPT_CANDIDATE_MODELS` 设标记值、`STEP01_TOTAL_BUDGET_SEC=900`、
  `AUDIT_TIMEOUT_SEC=600` 均非零退出；CLI 在总预算 900 时 returncode=1，`.tmp/s14/m07/runs` 未创建。
  `m07_run.py --commit e595838… --preflight` 通过。
