# Step01 交接卡（2026-10-21，供**新会话**直接开工）

> 目的：让下一个会话（上下文干净）不必重新推导状态。**读这一页即可开工**。

## 1. 当前代码状态（已落盘，脏工作树，勿回滚）

| 文件 | 状态 |
|---|---|
| `shared/delta_reconcile.py` | **R0 + R1'**：R0 只修 `{kind,uid}` 形状引用漏写的 `new:` 前缀（六条件）；**R1'**（2026-10-21 新增）做"基线权威的重复归并"①逐字相等 ⇒ 改指基线并删重复（跨集合时类型随基线，记 `R1p_type_restored`）②零引用孤儿正文被同类型已承载单元完整包含 ⇒ 删孤儿。均要求唯一解，不满足就 `R1p_skipped` 交统一验收。R1/R1b/R2 仍停用；`diagnose_splits()` 只诊断 |
| `shared/presentation_contract.py` | `apply_delta_view` 在 merge 后调用 `reconcile(view, base_view=, delta=)`，结果带 `reconciled` |
| `step01_script_gen/outline.py` | 提示两处：①引用新增声音须带 `new:` 前缀（三处一致）；②预算 90 s / 单镜 12 s 为硬约束，超了靠移出声音或换短句，不靠新增 |
| `tests/shared/test_delta_reconcile_r0.py` | **28 例全通过**（17 + R1' 11 例） |
| `tools/d11_run2.py`（在 run 目录下） | `STAGE=replay`（冻结响应回放）/ `STAGE=verify`（直接核验 `D11_BASE`）/ `D11_BASE` `D11_ROUNDS`；**退出码即判据**；每轮写 `candidate_origin` 与 `self_check` |
| `tools/guard_core.py` | 每次已传输调用归档 `calls/send_NN.json`（请求体 + 原始响应） |
| `step01_script_gen/plan_feasibility.py` | 旁白可表示性**不加说话人前缀余量**（与预算下界同一把尺） |
| `step01_script_gen/outline.py` | `_budget_limits()`：预算记录带逐条成本/净减量/替代句菜单；旁白估时同源 |

本包报告：[step01-r1prime-2026-10-21.md](step01-r1prime-2026-10-21.md)。

已知宿主失败：`test_plan_revision_delta` / `_end_to_end` / `test_presentation_transaction` / `test_relation_admission_matrix` 共 **8 failed**（与 R0 收紧后一致，新增 0）；`test_plan_revision_delta_equivalence` 另有 1 failed，经运行期屏蔽 R1' 验证为**既有**。

## 2. 冻结输入（不要再改）

- 计划/原文/问题集：`output/step01_ab/20261018T135622/d9_feasibility/input/{plan.json,novel.txt,problems.json}`
- 基线核验记录（可 seed）：`.../d10_runs/offline_recovery/accepted_record.json`
- 历史真实响应（4 份固定样本）：见 `.../d11_run/reconcile_r0only/replay.json`
- 运行器：`.../tools/d11_run2.py`（`D11_STAGE=real|offline`、`D11_CAP`、`D11_CASE`）

## 3. 事实状态

- 真实发送累计：**14 次**（旧 9 + 本会话 5：budget_ctx 1、rounds2 2、rounds3 1、核验 1）。
- 本会话首次取得**可应用且在预算内**的候选：`d11_run/real_rounds3/candidate_plan.json`
  → `kind=applied`、语音下界 **76.50 s**、13 节拍、无不可表示单元。
- 内容核验（1 次真实调用）判 **6 条实质问题**：
  `verify_fact_unsupported ×3` / `verify_voice_not_speech ×2` / `verify_voice_speaker_missing ×1`
  （记录：`d11_run/verify_rounds3_verify/verification_record.json`）。
- **每次发送的请求/原始响应都已归档**（`d11_run/*/calls/send_NN.json`）；运行器自检
  保证"归档数 = 传输数、基线不冒充候选、候选来源写进 run_log"。
- **冻结候选**：`d11_run/frozen_rounds3/`（候选 + 核验记录 + 正式 seed 过的 `base_with_record.json`
  + `freeze.json` + `reading.md` 阅读稿）。`plan_digest` 与核验记录一致，`seed=True`。
- **2026-10-22 收窄包**：同一冻结基线上只发 1 次修订（`real_rev4`），被拒
  `narration_units[n07] view_voice_unit_unplaced`（模型把 b09 改成画面却没删 n07），
  按规则**停止、未追加调用、未人工改稿**；候选项未变。
- 双通道对照第一包已完成：`.../hist_compare/report.md`（结论：保持默认通道，可比性不足）。

## 4. 下一步唯一有意义的工作

**先读失败归因包（已按复核修正）**：[step01-failure-analysis-2026-10-22.md](step01-failure-analysis-2026-10-22.md)。

已确认的只有：`real_rev4` 里 `f08→{visual,b09}` 是**合法声明**；把同一意图**只写成一条绑定 +
一处节拍正文**（不动声音），delta 入口 `applied`、76.50 s 不变、`n07` 仍由 `b09` 播放。
**未确认**：模型"已完成合法表达"（`b09` 冻结正文缺"听香答应"）、"只剩机械收尾"（删 `n07` 后
只剩 `verify_record_missing`，说明内容核验还没做）。样本：`failure_analysis/legal_sample_f08_delta.json`；
越权对照：`failure_analysis/negative_control_voice_tamper.json`（删被引用单元 ⇒ `view_voice_ref_dangling`）。

**入口实测（同一完整目标）**：受限 delta = **2 个实体**；`fact_carriers + beat_ops` = **18 个实体**
（beat_ops 触发全量 `fact_carriers` 9 条 + 整表 `narration_lines` 8 条），且带入旧核验记录
⇒ `verify_digest_mismatch`。**结论：保留 delta，不换 ops；暂不拆两个请求。**

下一步（1 次修订 + 条件满足时 1 次核验）：在 delta 请求里只允许"声明 + 指定节拍正文"，
节拍 `voice_ref` 必须原样保留；越权改声音直接拒收并给原因。判据：
`final_acceptance.codes == []`、下界 ≤90 s、无不可表示、其余事实/既有声音/事件顺序不变。
若限制修改面后仍出现孤儿/悬空，才考虑编译层最小改动（节拍部分 upsert，未给字段=保留），当前不实施。

## 5. 开工即用的执行纪律（本会话未做到的部分）

1. 开工先 `todo_write` 列出包内全部步骤；未全部 completed/blocked 不得结束回合。
2. 每完成一步立刻更新 todo，不写"下一步是…"。
3. 每包只用**一个**运行器，就地扩展，不再新建 `dN_*.py`。
4. 结束前跑机器可检的出口（pytest 指定文件或 `python tools/<check>.py`），以退出码为准。
5. 只在四类硬阻塞时停：外部复核关口 / 改产品标准 / 需扩授权 / 规格冲突；其余继续做。
