# Step01 规划层稳定性包 · 交付记录（2026-11-21）

> 工作包：S01–S09、S12 离线实现/验收/交付记录；S10 在 S07/S09 触发时停用户裁决；
> S11 与正式根运行需用户另行确认。全程保护正式五件套、R7.2 冻结包与工作树既有脏文件。
> 分支：`refactor/step01-planning-stability-20261121`（由 `21cc7a7` 切出，之后并行 slim
> 提交 `d3fb033` / `ee6cb0f` 也落在本分支，非本包改动）。

## 一、结论摘要

| 模块 | 结论 | 主要证据 |
|---|---|---|
| S01 | 冻结 22 条真实终局语料（COMMITTED 5 / commit BLOCK 3 / 计划层 14）与 D1–D10 归因 | [docs/step01-planning-failure-corpus-2026-11-21.md](../step01-planning-failure-corpus-2026-11-21.md)（`dce6eb5`） |
| S02 | 离线回放跑道：钉定基线回放 11-21 第 2/4 轮逐字一致（applied `b1653b43…` / no_change `ecc6b6e4…`），零模型调用 | `tools/replay_plan_revision.py`、`tests/tools/test_replay_plan_revision.py`（`a409755`） |
| S03 | 拒收只回灌“最后一次成功应用之后”的轮次，并显式列出已生效修订；完整历史仍全量留档 | `step01_script_gen/plan_revision.py`、`outline.py`（`a0ba16f`） |
| S04 | 槽位按能力类别部分适用：view/ops/核验/声音轴表达不了的项 deferred；content/plan_field 与“适配器覆盖但证据不足”仍 fail-closed；`route_decision.deferred_codes` 记录来源分流 | `outline.py`、`revision_route.py`（`a0ba16f`） |
| S05 | 增量协议 11 条自检规则收成唯一来源并按问题类别裁剪；清掉测试小说特征（`例如 d09`、`88 字/20s`、写死情节链、实测轮次/编号） | `outline.py`、新增 `test_revision_request_hygiene.py`（`5874d01`） |
| S06 | 核验员载体建议降级为结构化 `hint_carriers`（标注“未核实”）；修订请求附 `carrier_capability_table()`（由 `CARRIER_KINDS` 单源生成） | `shared/content_verification.py`、`shared/presentation_contract.py`、`plan_content_repair.py`（`5874d01`） |
| S07 | **契约可满足**：手写合法增量走真实 `apply_delta_view` 应用成功、统一验收无非核验类阻塞 ⇒ 11-21 失败不是契约无解，问题在请求/分流/判据 | `.../acceptance/s07_contract_satisfiability.json` |
| S08 | 首版登记问题可唯一推导 **0/14**（13 台词 + 旁白 N2 均无引用/编号关系）；显式 `载体：旁白k` 已确定性投影为 `voice_ref`，其余保留为登记残余交修订 | `.../acceptance/s08_registration_projection.json`（`fdc5a3c`） |
| S09 | 9 次真实调用（3 样本 ×3）：A 的 f03 三判 `partial/partial/supported`、A/B 的 coherence 三判不一致 ⇒ **判据不稳定**，触发 S10 | `.tmp/s09_calibration/`、`.../acceptance/s09_judge_calibration.json` |
| S10 | 用户裁决**方案1：稳定性兜底**。生产入口对单次语义负面复核一次，两次一致负面才阻断；单次负面/coherence 轴降为 advisory；复核独立额度 ≤2 次/运行，不可用则 fail-closed；协议/证据问题不降级 | `.../acceptance/s10_decision.json`、`fd7338c` |
| S11 | 隔离真实运行验证（第 1 章 ×3 + 第 2 章 ×2）——结果见 §五（运行完成后回填） | `.tmp/s11_isolated/`、`.../acceptance/s11_isolated_real_runs.json` |
| S12 | 媒体清理移到提交 `COMMITTED` 之后：BLOCK 时音频/vimax/stretched/成片原样保留，逐镜保留/占用告警语义不变 | `step01_script_gen/main.py`、`test_media_cleanup_after_commit.py`（`18bb86d`） |

## 二、基线与证据身份

- 起始 HEAD：`21cc7a7`（原计划写 `0b53f67`；实际差异仅 `.gitignore`、测试语法守卫与
  `skills/.agents` 去跟踪，**无 Step01 业务代码 delta**）。
- 冻结现场：11-21 计划层失败 run `20261121T181234-6228`（9 次 agent_plan 调用，
  179.3s，`PlanContractInvalidError`，计划 final `ecc6b6e4…`）。
  语料清单与 sha256：`.tmp/step01_plan_corpus_20261121/`（`corpus_manifest.json`、
  `sha256_manifest.txt`、`inventory.json`）。
- 回放代码基线：S02 验收以 `--code-source head` 钉住提交 `ee6cb0f`（S02 合入后 HEAD），
  S03/S04 验收证据记录在 `a0ba16f` 时点；S05 起提示文本有意改变，旧语料逐字回放不再
  适用当前工作树（属于预期，S02 工具对漂移 fail-closed）。合并后该提交仍可达，回放改为
  显式钉 `corpus_manifest.json["code_baseline"]`（`ee6cb0f28c7e…`），工具新增
  `--code-source <任意 git ref/commit>`；外部回归项缺该声明时 skip 并注明“未运行”。
- 正式五件套（只读核对）：`output/锦衣夜行/part_01_杀青州/chapter_001/script_result.json`
  sha1 `17719bd87fe93c4dbe1bfed525358da2177dda14`（与基线一致）；`step01_commit.json` /
  `step01_quality.json` / `step01_audit.json` / `step01_adoption.json` 时间戳保持 15:01。
- R7.2 冻结包未写：`binding_digest=afc80619b4201fb273ff295d380b46b6` 等文件未触碰；
  单次终审仍待用户放行。

## 三、关键实现与设计边界

### S03（D1 拒收回灌）
- `plan_revision.RevisionRejections(list)` 携带 `applied_rounds`；控制器只把
  “最近一次成功应用之后”的拒收传给下一步请求，完整 `_rej` 仍留给结果/诊断；
  连续拒收计数只算应用后的轮次。
- 渲染端（`outline._rejection_feedback_block`）在有已应用轮次时不再写
  “上一轮……一个字节没改”，改为列出已生效修订（如“第 2 轮（view_applied）”）。

### S04（D2/D3 槽位部分适用）
- `_content_slots_applicable` 按能力类别区分：`view/ops` 与核验/声音轴表达不了的项
  deferred（下一轮完整验收再判）；`content`/`plan_field` 与“槽位适配器存在但当前证据
  不足”的 code 仍 fail-closed 回退非槽位路。
- 验收读数：round4 当前代码分流 `content_slots`，槽位 `slot:fact:f03`/`slot:fact:f06`，
  `deferred=[verify_plan_coherence]`，槽位请求 20,310 字符（归档增量请求 36,249）且
  不含任何过时拒收。

### S05（D6 内容无关性）
- `_DELTA_RULES` 为唯一规则来源；`delta_instruction_for(problems)` 按类别裁剪
  （无预算问题不发预算规则；无删除/增删内容可能不发删改互斥规则；always 规则恒在）。
- 单测扫描协议常量与真实请求，确认不出现测试小说人名/地名/情节/编号；真实请求只含
  本次输入的原文（内容来自输入，不是提示硬编码）。

### S06（D4 核验员建议降级）
- `verify_fact_unsupported` 的问题记录保留核验员 `missing`，载体的
  `suggested_carriers` 改为结构化 `hint_carriers` + `hint_note`（“核验员提示，未核实
  （不等于可执行的修法）”），视图反馈单独标注。
- `carrier_capability_table()` 由 `presentation_contract.CARRIER_KINDS` 单源生成，
  delta/view/槽位三类修订请求都附能力表；只描述能力，不做语义判定。

### S10（判据稳定性兜底，用户裁决）
- `VerificationController(stability_confirmation=True)` 仅生产入口启用（库默认关闭，
  p4/回放等“每轮一次发送”工具不受影响）。
- 单次语义负面（fact/voice）触发一次独立复核（独立小额度 ≤2 次/运行，不占既有
  `max_verifications` 主额度，共用同一截止时刻）：两次一致负面才继续阻断；单次负面
  记入 `record.stability.advisory_facts/advisory_voices` 并降为 advisory。
- coherence 轴本轮整体 advisory（`advisory_coherence=True`）。
- 复核额度/截止不足、复核调用失败或响应不合格 ⇒ **fail-closed**，保留负面阻断。
- 协议/证据/身份类问题（引文缺失、摘要不符等）不降级。

### S12（D9 媒体清理时序）
- 新增 `_cleanup_stale_media_after_commit(status, *, preserve_shots, changed_shots,
  removed_shots)`：先只计算逐镜保留范围；提交 `COMMITTED` 后才删除
  changed/removed 镜头与（无保留范围时）全量媒体、成片和字幕；BLOCK 时不动媒体；
  占用导致的清理失败仍只告警。

## 四、S07/S08/S09/S10 验收读数

- S07：`apply_delta_view` `kind=applied`、应用期 0 问题；`plan_acceptance_problems` 残余
  仅 `verify_record_missing`（S07 不判语义），非核验类问题 0。手写增量不进入正式产物
  或生产测试夹具，仅作实验证据。
- S08：`carrier_relation_records` 对首版计划复算得 13 台词 + 旁白 N2 = 14 条
  `orphan_uncarried`，其中**可唯一推导 0 条**；`view_from_plan` 对显式
  `载体：旁白k` 的 b01/b02/b05 已输出 `voice_ref={n01/n03/n04}`。
- S09：9/9 调用 success、记录形状 0 问题，累计 usage 177,912 tokens（隔离台账/归档）。
  稳定性表见 `.../acceptance/s09_judge_calibration.json`；结论 `unstable_judge`。
- S10：新增 6 项稳定性用例；相干性/legacy 用例按新策略更新（记录保留真实负面，
  仅硬门口径变化）；`tests/step01_script_gen` 全量 4670 passed（4 项
  `test_run_provenance` 为文档标注的基线失败）；`ruff gate` 无新增。

## 四·五、与 slim-offline 的合并与回归（2026-11-21）

- 合并提交 `39e0a15`：把 `refactor/slim-offline-20261121` 的续作（约束删除 `29a4c81`、
  旧 QC 链删除 `0addf0d`、交付记录 `ffbabe5`）合入本分支；自动合并无冲突；按 slim
  文档删除合并后不再适用的两个**未入库**旧测试
  `tests/step01_script_gen/constraints/test_boat_crop.py`、
  `test_self_review.py`（它们 import 已删除模块）。
- 合并后全量 `pytest tests`：**7637 passed / 303 skipped / 4 xfailed / 13 failed**；
  其中 12 项为 `docs/pkg1-offline-hygiene-20261119.md` 已列的基线失败
  （`test_run_provenance`×4、`test_disk_governance`×3、`test_tts_providers`×2、
  `test_video_reroll`×2、`test_content_binding_ratchet`×1），另 1 项
  `tests/tools/test_replay_plan_revision.py` 外部语料项是因为合并后 HEAD 已含 S05
  提示改动而按 HEAD 回放漂移。已把外部回放改为钉 `corpus_manifest["code_baseline"]`
  （`ee6cb0f…`），工具支持 `--code-source <git ref/commit>`；重跑
  `tests/tools/test_replay_plan_revision.py` 10/10 通过。
- `ruff gate`：F401 240 / F811 16（与 slim 分支基线下调一致）。
- 主工作区既有未提交改动（`step02a_frame_gen/verify_acceptance.py`、
  `tests/step02a_frame_gen/test_verify_only.py`）在合并中未被触碰。

## 五、S11 隔离真实运行（第 1 章 ×3 + 第 2 章 ×2）

> 运行方式：`.tmp/s11_isolated_runs.py`，每次独立 `OUTPUT_BASE` / 调用台账 /
> `STEP01_RUN_ID`，`TEXT_PROVIDER=agent_plan`（OpenCode Go 订阅通道），不写正式
> `output/`。每条 run 走完整 Step01（计划→生成→审计→修复→提交）；5 条 run 均在
> 同一提交 `fd7338c` 的代码上以独立进程运行；聚合读数见
> `.tmp/s11_isolated/s11_summary_final.json` 与
> `.tmp/step01_plan_corpus_20261121/acceptance/s11_isolated_real_runs.json`。

| # | run_id | 章 | 计划层（replan 轮次） | 终局 | 调用数 | 说明 |
|---|---|---|---|---|---|---|
| 1 | `ch1_run1-20261121T220746` | 1 | 3 轮：ok→applied→applied，无非法终局 | 提交 **BLOCK** | 16 | 生成/审计完成，最终审计修复轮被**预算守卫**拦（剩余 400.2s < 修复 180s + 最终审计 600s + 收尾 60s）；冻结候选已保存，正式产物未动 |
| 2 | `ch1_run2-20261121T222107` | 1 | 3 轮：ok→applied→applied | 提交 **BLOCK** | 15 | 同上，预算守卫（剩余 596.9s） |
| 3 | `ch1_run3-20261121T223111` | 1 | 3 轮：ok→applied→applied | 提交 **BLOCK** | 16 | 同上，预算守卫（剩余 610.0s） |
| 4 | `ch2_run1-20261121T224415` | 2 | 5 轮：ok→illegal→illegal→applied→applied | 提交 **BLOCK** | 15 | 两次非法响应后恢复并产出可执行计划；最终审计修复轮被预算守卫拦（剩余 627.3s） |
| 5 | `ch2_run2-20261121T225349` | 2 | 7 轮：ok→illegal→applied→applied→illegal→applied→illegal | **计划层终局** | 15 | `PlanInfeasibleError(illegal_response)`：槽位修复答案被判 `slot:fact:f11 repair_answer_target_not_allowed`（所选片段 `b09` 不在该片段合法落点集合，且该片段可用落点为 **[]**），连续非法响应耗尽修订额度 |

**结论（计划层稳定性）**：
- 5/5 **没有** `PlanContractInvalidError`；4/5 产出可执行计划（其中 2 条经历多次非法响应后恢复，
  验证了 S03 拒收回灌 + S04 槽位 deferred 的恢复能力）；
- 提交门 4/5 为 `BLOCK`，原因均为**最终审计修复轮预算守卫**（非计划层、非契约层）；
  冻结候选均已保存，正式产物未提交；
- 1/5（第 2 章第 2 次）在**槽位修复目标校验**上停在计划层：模型答案选择已有落点 `b09`，
  而该片段合法落点为 `[]`，被 `repair_answer_target_not_allowed` 拒收直至额度耗尽。
  这是 S11 暴露的**新机制缺口**（槽位选择应在片段无合法落点时 defer / 改走完整视图修复，
  而不是让模型反复给出必被拒的答案），已登记为后续工作包候选（见 §六.8），不改变
  S01–S09/S12 的离线交付结论。

## 六、已知限制与未决

1. **R7.2 冻结包**：仍停在 `binding_digest=afc80619b4201fb273ff295d380b46b6`，单次
   终审需用户放行；本包未触碰。
2. **S02 工具与代码漂移**：冻结语料对应提交 `ee6cb0f`；S05 起提示文本有意改变，
   旧语料逐字回放须用 `--code-source head`（或重冻语料），工作树模式的“请求不一致”
   是预期 fail-closed。
3. **S09 判据不稳定是真实结论**：已由 S10 用户裁决兜底；coherence 轴暂为 advisory，
   后续若要恢复硬门需重新校准（另需真实调用）。
4. **基线已知失败**：`test_run_provenance` 4 项在整目录顺序下失败，属
   `docs/pkg1-offline-hygiene-20261119.md` 已列基线问题，非本包引入。
5. **本地未入库测试**：`tests/step01_script_gen/test_plan_coherence.py` 等为被忽略的
   本地历史测试，已按 S10 新策略更新（不入 git）；入库等价保护在
   `test_legacy_semantic.py` 与 `test_verify_stability_advisory.py`。
6. **分支污染说明**：本分支含并行 slim 提交 `d3fb033`、`ee6cb0f`，非本包改动；
   合并/评审时不要误归因。S11 之后已合入 `refactor/slim-offline-20261121`
   （`39e0a15`：约束精简 + 旧 QC 链删除），合并后按 slim 文档删除两个未入库旧测试
   `test_boat_crop.py` / `test_self_review.py`。
7. **一次性日志副作用（已核实）**：某次未设 `OUTPUT_BASE` 的探针 import 给正式
   `step01.log` 追加 1 行通道信息（21:04:58）；正式五件套内容/时间戳未变，
   `script_result.json` sha1 与基线一致。
8. **S11 后续候选（新发现，未纳入本包门禁）**：槽位修复在“所选片段合法落点为 []”时
   仍向模型下发该事实槽位，模型只能给必被拒的答案（`repair_answer_target_not_allowed`），
   连续非法响应后以 `PlanInfeasibleError` 停在计划层。建议下一工作包：槽位选择阶段
   过滤“合法落点为空”的片段（defer 或改走完整视图/内容修复路），并补一条“落点为空”
   的离线反例用例；是否开工由用户决定。

## 七、证据索引与复现

| 内容 | 路径 / 命令 |
|---|---|
| 失败语料与基线 | `docs/step01-planning-failure-corpus-2026-11-21.md`、`.tmp/step01_plan_corpus_20261121/` |
| 回放（pinned 基线） | `python tools/replay_plan_revision.py --manifest .tmp/step01_plan_corpus_20261121/corpus_manifest.json --round 2 --code-source <corpus_manifest.code_baseline>`（round 4 同；当前基线 `ee6cb0f28c7e…`） |
| S07/S08/S09/S10 证据 | `.tmp/step01_plan_corpus_20261121/acceptance/{s07_contract_satisfiability,s08_registration_projection,s09_judge_calibration,s10_decision}.json` |
| S09 原始响应 | `.tmp/s09_calibration/archives/`、`.tmp/s09_calibration/s09_calibration.json` |
| S11 运行证据 | `.tmp/s11_isolated/`（每 run 的 `run.log`、chapter 产物、台账）、`.../acceptance/s11_isolated_real_runs.json` |
| 全量回归 | `python -m pytest tests/step01_script_gen -q`；`python tools/ruff_gate.py` |

## 八、提交清单（本包）

```
dce6eb5 docs(step01): S01 冻结规划层失败语料与基线
a409755 test(step01): S02 离线修订回放跑道
a0ba16f fix(step01): S03+S04 拒收只回灌有效轮次 + 槽位按能力类别部分适用
5874d01 fix(step01): S05+S06 修订请求去样本化/按类裁剪 + 核验员建议降级与能力表
18bb86d fix(step01): S12 媒体清理移到提交 COMMITTED 之后
fdc5a3c test(step01): S08 首版登记问题的确定性派生与唯一映射边界
fd7338c feat(step01): S10 判据稳定性兜底（用户裁决·方案1）
```
