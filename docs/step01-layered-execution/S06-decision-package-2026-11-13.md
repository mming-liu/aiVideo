# S06 决策包：具体样本与行为影响（2026-11-13）

> 基线：`96f3cc3` + 本包 S02–S05 补修（`53d83fc`、`95d5760`；本轮 S03/S05/S06 更正见
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
| G3 先定稿参考、再构建正文 | **已有**（1 例，隔离样本） | 参考清单不变；正文可新增「参考绑定」段 | 隔离样本；未接线生产 |
| G6 声音两态 | **已有**（1 例） | 声明值被当成实测 → `audio_window_overflow` | 准备/语义真实 |
| G7 `postprocess_reverted` 接门 | **已有**（3 例） | 等价派生 / 目标丢失 / 保护违规 | 判据真实；门禁组合**推演** |

---

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

**隔离样本**（`tools/build_s06_g3_isolated_diff.py`，真实 `build_frozen_for_shot`，
离线、零模型调用；**未接线生产**）：

| 字段 | 当前 | 候选 |
|---|---|---|
| `request.prompt` | 只有画面描述 + 参考图用途清单 | 末尾追加 `【参考绑定】参考图1=甲（角色外观）` |
| `reference_paths` | `portrait_jia.png` | 相同（参考清单不变） |
| `references[*].role` | `reference_image` | 相同 |
| `frozen.media_mode` | `reference` | 相同 |
| 冻结正文 | 无参考绑定 | 含参考绑定（`frozen_prompt_changed=true`） |

- 样本只回答「结构上能不能把参考绑定写进正文」；**不回答模型是否因此改善**。
- 候选正文是工具内追加，尚未实现「参考清单先行」的生产链路；正式默认行为不变。
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

## 7. 证据

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe tools/build_s06_decision_samples.py
# g1: not_detected_this_round/closed=False → 现行 1 / G1 0；fixed_with_evidence/closed=True → 1/1
# g2: [大特写] → [全景]；warning 1 条
# g6: audio_window_overflow；actual_sec=5.0，verified=False

C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe tools/build_s06_g7_samples.py
# R2_plus_protection_matches_expected=true（A accept / B reject / C reject）

C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe tools/build_s06_g3_isolated_diff.py
# prompt_added_block=true；reference_list_unchanged=true；frozen_prompt_changed=true
# 输出：artifacts/s06/g3_isolated_request_diff.{json,md}
```
