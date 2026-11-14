# S06 决策包：具体样本与行为影响（2026-11-13）

> 基线：`96f3cc3` + 本包 S02–S05 补修（`53d83fc`、`95d5760`、`a2b6621`；本轮 S03/S05/S06 更正见
> `S01-S07-content-independence-2026-11-13.md`）。
> 范围：只提供裁决材料，**不改变任何门禁默认行为**；全部离线、零模型调用。
> 样本生成器：
> - `tools/build_s06_decision_samples.py` → `artifacts/s06/g1_g2_g6_decision_samples.{json,md}`
> - `tools/build_s06_g7_samples.py` → `artifacts/s06/g7_decision_samples.{json,md}`

## 0. 覆盖状态

| 决策 | 样本状态 | 行为读数 | 证据层级 |
|---|---|---|---|
| G1 `issue_ledger.closed` 接提交门 | **已有**（2 例） | 沉默 vs 有证据关闭的 resolved 差异 | 台账真实；门禁**推演** |
| G2 `framing_jump` 自动改写 | **已有**（1 例） | 大特写→全景的改写 vs 只报 warning | 约束/校验真实 |
| G3 先定稿参考、再构建正文 | **已有**（1 例 × 3 分支，隔离样本） | 参考清单不变；绑定逐项对应；两条真实 prepare 通过，full_prompt 记 pending | 隔离样本；未接线生产 |
| G6 声音两态 | **已有**（1 例） | 声明值被当成实测 → `audio_window_overflow` | 准备/语义真实 |
| G7 `postprocess_reverted` 接门 | **已有**（3 例） | 等价派生 / 目标丢失 / 保护违规 | 判据真实；门禁组合**推演** |

---

## 0.5 裁决阶段总表（2026-11-14）

> 状态：离线修复与决策材料已交付，进入**裁决阶段**。本表只给建议与影响，
> **不改变任何默认门禁**；采纳与否由用户逐项确认。G4 按具体候选单独审批；
> 视频模型选择继续后置。

| 决策 | 现状/证据 | 建议 | 采纳后的改动点与影响 | 需确认 |
|---|---|---|---|---|
| G1 `issue_ledger.closed` 接提交门 | 台账真实；门禁推演：沉默样本现行门接受、G1 门拒绝 | 只对**阻塞级**确认问题要求 `closed=True`；非阻塞继续只记账 | 提交门新增 closed 判据；沉默样本从 accept 变 reject | 是否接门、是否仅阻塞级 |
| G7 `postprocess_reverted` 接门 | 三判据真实；组合推演：A 记账过/保护过/R1 误拒；B R2 拒；C 保护门拒 | **按目标丢失接门 + 保留既有保护门**（不按任何残余） | 提交门新增 target-loss 判据；R1 类等价派生不得误拒 | 是否接门、是否保留保护门组合 |
| G2 `framing_jump` 自动改写 | 1 例：大特写→远景被自动改写成「全景」；validation 已报 6 级 warning | **改为只报不改**，先做影子对照 | 提示不再自动改景别；输出提示变化，需回归受影响镜头 | 是否停止自动改写 |
| G3 参考清单先行 | 1 例 × 3 分支：2 条真实 prepare 通过且绑定逐项对应；`full_prompt` 记 `not_ready`（provider 无 prepare） | **先在隔离分支实现，再裁决是否接生产** | 改 `build_frozen_for_shot` / `prepare_request` 顺序；全部视频请求受影响，需全量回归 | 是否进入生产接线、full_prompt 分支如何处理 |
| G6 声音两态 | 1 例：声明 5s 写入 `actual_sec`、`verified=False`，仍产出 `audio_window_overflow` | **拆准备态/确认态**：准备态只写声明值与窗口预算，确认态才写实测 | 改 `audio_policy` / `voice_preparation` / `execution_semantics` 消费方 | 是否拆两态、超窗报告口径 |
| 旧稿准入口径 | 影子差异：旧稿 `unresolved` 只出 `causal_gap` advisory + `needs_semantic_review`，不新增拒收 | **保持 advisory，不在本包收紧正式准入**；若正式采纳要求身份，另走影子评估与裁决 | 不改门禁；新稿身份生产已接线（`（Bk）` + 重映射） | 正式采纳是否要求身份、何时切换 |

- G3 报告字段语义：`block_survives_prepare` 为三态（`true`/`false`/`null`），
  `null` 表示 provider 无 `prepare_request`、**未验证**；另有
  `block_survives_prepare_status` 与 `block_in_frozen_prompt` 供机器读取。
- 本表所有"推演"结论只到判据组合层；真实提交入口/真实模型效果仍需后续验证。

## 0.6 P1 接线完成记录（2026-11-14）

六项裁决已按获批口径接线（G3 仍按裁决留在隔离分支，不切生产主干）：

| 决策 | 落地位置 | 行为变化 |
|---|---|---|
| G1 `closed` 接提交门 | `main.py` `_commit_step01_outputs` 审计轮（`_prev_audit_issues` / `_prev_audit_script`） | 上一轮 blocking 本轮不再报出、判据证据可判且授权目标字段未改动 → BLOCK（`blocking_unclosed_without_evidence`）；非阻塞不拦 |
| G7 `postprocess_reverted` 接门 | `main.py` `_repair_acceptance_decision(merged_script=...)` | 合并后目标已达成、后处理后丢失 → 拒绝（`postprocess_target_lost`）；保留既有保护门，不按残余一刀切 |
| G2 自动改写 | `constraints/framing_jump.py` | 改为 `action="warn"` + violation 记录，剧本景别/机位一字不改 |
| G6 声音两态 | `shared/voice_preparation.py`、`shared/execution_semantics.py` | 单元新增 `state`（prepared/confirmed）；准备态计划超窗 → `audio_window_overflow_planned`（advisory，不阻断），确认态实测超窗才 `audio_window_overflow`（reject） |
| 旧稿准入口径 | 无改动 | 保持 advisory，不新增追溯性拒收 |

- 最终规则指纹：**`RULE_SOURCE_FINGERPRINT = db08353ea107b486`**（接线前 `593deea03c7cddc6`；`RULE_VERSION=2026-09-23.4` 未变）。
- 回归：`tests/step01_script_gen` **4285 passed / 278 skipped / 4 xfailed / 0 failed**；
  `tests/shared + tests/integration + tests/step02a/02b/02_video` **1748 passed / 8 skipped / 0 failed**。
- 说明：G1 只对「判据证据可判」的阻塞条目生效；证据缺失/过短或证据文本从未字面出现在稿件的条目保持既有语义（不凭沉默定罪，也不放行有证据却仍在稿中的情况）。G7 的 target-loss 判定复用 `_audit_issue_resolved`（合并后 vs 后处理后）。
- 后续（未完成）：G3 在隔离分支调整 `build_frozen_for_shot` / `prepare_request` 顺序并跑全量视频请求回归，产出前后 diff 后再裁决是否合入 master。

## 1. G1：`issue_ledger.closed` 接提交门

样本（`build_issue_ledger`，真实函数）：

> **证据层级**：台账状态是真实函数产出；下表两列门禁结果是**按规则推演**
> （`resolved = before_keys - after_keys` + `closed` 规则），**没有走真实提交入口**。
> 真实提交点的最终结果仍需在提交链上验证。

| 样本 | 台账状态 | closed | 现行门 resolved（推演） | G1 门 resolved（推演） | 现行门（推演） | G1 门（推演） |
|---|---|---|---|---|---|---|
| 本轮审计没有再次报出（模型沉默） | `not_detected_this_round` | False | 1 | 0 | accept（把沉默读成已解决） | reject（无关闭证据） |
| 有确定性复核证据关闭 | `fixed_with_evidence` | True | 1 | 1 | accept | accept |

- 现行门：`step01_script_gen/main.py:6672` 的 `resolved = before_keys - after_keys`；
  问题没再被报出 ⇒ key 从 after 消失 ⇒ 计 1 条 resolved。
- G1 门：只对**阻塞级**确认问题要求 `closed=True`；沉默条目 resolved=0 → 拒收。
- **唯一行为差异**是沉默样本；有证据关闭的样本两种门一致。
- 建议：按 G1 接门，但只覆盖阻塞级；非阻塞条目继续只记账。

## 2. G2：景别跨度 > 2 级的自动改写

样本（`FramingJumpConstraint` + `_check_shot_size_jump`，真实函数）：

- 镜 1 `[大特写] 甲的手指按在门闩上` → 后处理后 `[全景] 甲的手指按在门闩上`
- 镜 2 `[远景] 山谷与长河`
- 既有 warning（改写前）：`镜头1→2: 景别「大特写」→「远景」跳跃幅度 6 级（>2 级）…`

| 选项 | 行为 | 风险 |
|---|---|---|
| 保留自动改写（现状） | 前镜「大特写」被改成「全景」；audit 不再收到这条跨度提示 | 刻意选择的近景被后处理改掉；与修复轮已授权镜头冲突 |
| 只报 warning，不改写 | 前镜保持「大特写」；validation 仍报 6 级跨度 | 跨度可能留在成片里，需要审计/人工接住 |

- 建议：G2 只报不改。`_check_shot_size_jump` 已产出同一条 warning；先做影子对照，
  再决定是否保留自动改写。

## 3. G3：先定稿参考、再构建正文（隔离样本已出）

当前链路（已核对）：

1. `step02b_video_gen/main.py:464` `build_frozen_for_shot(...)` 先构建
   `prompt` 与参考清单；
2. `:479` `_svc.prepare_request(_req)` 才冻结实际生效文本/参数/参考；
3. `:484` 再用 `prepared` 重建冻结请求。

于是正文构建时读不到 `prepare_request` 才定稿的参考清单，正文里无法声明
「参考图 k 是谁/什么」。

**隔离样本 v2**（`tools/build_s06_g3_isolated_diff.py`，真实
`build_frozen_for_shot` + 真实 provider `prepare_request`，零提交、零模型调用；
**未接线生产**）。同一镜头走三条正文构建分支，各自先定稿参考清单再追加绑定：

| 分支 | 正文路径 | 参考清单 | prepare（零提交） | 绑定逐项对应 |
|---|---|---|---|---|
| `seedance_concise` | Seedance 精简正文（agent_plan，r2v） | 首帧 + 角色肖像（2 项） | **prepared** | 是 |
| `full_prompt` | 完整提示（bailian，image） | 首帧 + 尾帧（2 项） | `not_ready`（bailian 未实现 prepare_request） | 是（以冻结 references 为准） |
| `first_last_image` | `first_last+image` 运动正文（volcengine） | 首帧 + 尾帧（2 项） | **prepared** | 是 |

每条分支核对：

- 候选正文按**最终参考清单**逐项写 `参考图k=身份/用途`，编号与清单第 k 项一致；
- 参考清单在候选前后**不变**（角色/文件名逐项相同）；
- 两条真实 prepare 分支：绑定段经 provider `prepare_request` 后仍在最终正文里；
- `full_prompt` 分支的 provider 未实现 `prepare_request`，按生产同一路径记
  `pending_verification`，不冒充已准备。
- **报告字段语义**：`block_survives_prepare` 为三态——`true`=经 prepare 后仍在
  最终正文、`false`=prepare 后丢失、`null`=provider 无 `prepare_request`、**未验证**；
  `block_survives_prepare_status` 给出 `verified` / `unverified_provider_no_prepare`，
  `block_in_frozen_prompt` 记录冻结正文是否含绑定段。机器读取不得把 `null` 当 `true`。

- 样本只回答「结构上参考绑定能否进正文并通过真实 prepare」；**不回答模型是否
  因此改善**。候选正文是工具内追加，正式默认行为不变。
- 限制：样本使用隔离角色肖像/首帧，不读真实资产库；真实收益需实跑对照。
- 产物：`artifacts/s06/g3_isolated_request_diff.{json,md}`。

## 4. G6：声音「准备态 / 视频后确认态」

样本（`build_voice_preparation` + `check_script_semantics`，真实函数）：

- 一条独立对白声明 `5.0s`，镜头窗口 `4.0s`，**没有音轨**（未实测）。
- `build_voice_preparation`：`actual_sec=5.0`、`verified=False`、
  `measurement='not_measured'`（声明值被写进实测字段）。
- `check_script_semantics`：`['audio_window_overflow']`（reject）——只看 `actual_sec`，
  不区分「声明」与「实测」。

| 选项 | 行为 | 风险 |
|---|---|---|
| 维持单态（现状） | 声明值写进 `actual_sec`；不看 `verified` 的消费方会把声明当实测 | 未实测内容按实测拒收；准备期超窗与成片超窗混在一起 |
| 拆准备态/确认态 | 准备态只写 `declared_sec` 与窗口预算；确认态只在 `verified=True` 时写 `actual_sec` | 需改 `audio_policy` / `voice_preparation` / `execution_semantics` 消费方 |

- 建议：拆两态；准备态允许「计划超窗」作为规划问题报告，确认态才按实测拒收。

## 5. G7：`postprocess_reverted` 接门（更正原表述）

原表述「按残余接门会拒收等价派生」**过宽**：它把三类不同事实混成一句。
三个样本（`main._authorized_changes_lost` / `_audit_issue_resolved` /
`_locked_repair_signature`，真实函数）的实际结果：

> **证据层级**：三个判据是真实函数；候选门的组合结果按规则推演，
> **未走完整提交入口**。下表「记账规则」只代表 `postprocess_reverted` 本身是否
> 拒收；「现行保护门」是既有 `locked_changed` 对保护字段变化的判定。

| 样本 | `postprocess_reverted` | 目标丢失 | 保护字段变化 | 记账规则 | 现行保护门 | R1 任何残余 | R2 目标丢失 | R2+保护 | 应然 |
|---|---|---|---|---|---|---|---|---|---|
| A 等价派生 | `1:prompt` | 否 | 否 | 过 | 过 | **拒（误）** | 过 | 过 | accept |
| B 目标丢失 | `1:ff_desc` | **是** | 否 | 过 | 过 | 拒 | 拒 | 拒 | reject |
| C 保护违规 | 空 | 否 | **是** | 过 | **拒** | 过 | 过（漏） | 拒 | reject |

- A：模型删掉无来源的「约 2cm」，后处理只补「（不是上半身出水）」——授权路径值变了，
  但修复目标仍达成。**R1 会误拒这一类。**
- B：模型删掉「窄身」，后处理把修前值写回——目标丢失，R2 能拒；记账规则和既有
  保护门都不拦，**这是现状的真实缺口**。
- C：授权路径没变，后处理改了锁定的 `narration_segment`——`postprocess_reverted`
  为空；R1/R2 都看不见，**既有 `locked_changed` 保护门会拒**。

**更正后的裁决对象**：不是「按任何残余接门」，而是
**「按目标丢失接门，并保留既有保护门」**。若只做 R1，会误拒等价派生；
若只做 R2，会漏掉保护违规（需既有保护门补位）。
`R2_plus_protection_matches_expected=true` 只证明这组三例的判据组合，
不代表真实提交入口的最终读数。

## 6. 建议裁决顺序

1. G1：按「阻塞级必须有证据关闭」接门（行为差异仅沉默样本）。
2. G7：按「目标丢失 + 既有保护门」接门，不按任何残余。
3. G2：改为只报不改，先做影子对照。
4. G6：拆两态；准备态与确认态分别消费。
5. G3：隔离差异样本已出（§3）；正式接线前先裁决「参考绑定进正文」的产品语义，
   再在隔离分支实现「参考清单先行」并做真实模型对照。
6. 旧稿准入口径：保持 advisory（不新增拒收）；正式采纳是否要求身份另走影子评估。
7. G4：按具体候选单独审批；视频模型选择继续后置。

## 7. 证据

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe tools/build_s06_decision_samples.py
# g1: not_detected_this_round/closed=False → 现行 1 / G1 0；fixed_with_evidence/closed=True → 1/1
# g2: [大特写] → [全景]；warning 1 条
# g6: audio_window_overflow；actual_sec=5.0，verified=False

C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe tools/build_s06_g7_samples.py
# R2_plus_protection_matches_expected=true（A accept / B reject / C reject）

C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe tools/build_s06_g3_isolated_diff.py
# 三分支：seedance_concise / full_prompt / first_last_image
# all_reference_lists_unchanged=true；all_bindings_one_to_one=true
# prepared：seedance_concise + first_last_image；full_prompt（bailian）记 not_ready
# 输出：artifacts/s06/g3_isolated_request_diff.{json,md}
```
