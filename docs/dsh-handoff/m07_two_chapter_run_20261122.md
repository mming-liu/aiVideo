# M07 交接：Step01 两章各一轮隔离真实运行（2026-11-22）

执行端：**dsh**，沿用 AGENTS.md 的 Step 01 订阅授权（`agent_plan`，OpenCode Go 订阅端点）。
Kiro 已完成离线修复与准备，**未发送任何模型调用**。

## 1. 固定代码

| 项 | 值 |
|---|---|
| 提交 | `master` 上包含 `409c11c` 的提交（见 `docs/s14-slim-joint-20261122.md` 末尾提交表；运行前用 `git log -1` 取实际 SHA 并写进记录） |
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
- **发调用前**用快照代码 + 子进程同一环境读出生成 / 审计 / 修复三处最终生效的 provider、模型与端点，
  任一不是 `agent_plan` + `https://opencode.ai/inference/go/openai/v1/chat/completions` 即停止、不发调用。
- **调用上限**：项目里没有按次数封顶的配置项。脚本每 5 秒读调用台账，单轮到 40 次即终止该轮（终局 `CALL_CAP`）；
  两轮合计上限 80 次，不自动追加到每章 3 轮。外层另有 1500 秒硬超时兜底。
- 运行前后核对正式章 `script_result.json` 的 sha256，结果写进 `.tmp/s14/m07/summary.json` 的 `formal_unchanged`。

不要：覆盖正式章、采纳或重签 R7.2、推送、改 `.env.content`。

## 3. 收口要报告的内容（每章）

1. 是否产出可提交稿（`step01_commit.json` 的 `status`）。
2. 首个阻塞点：计划层（拒收码与轮次）/ 候选层 / 提交门（blocking 码）。
3. 提交修复是否获准进入：`step01_metrics.json` 的 `budget.frozen_candidate`（有就是没进入）与 `repair` 计数。
4. 审计是否超时：`step01_timing.json` 的 `commit_audit` / `final_audit` 时长与状态。
5. 实际调用次数与通道（`summary.json` 的 `calls`、`channel`）。
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
