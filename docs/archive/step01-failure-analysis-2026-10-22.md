# Step01 修订反复失败：受限入口的离线收口（2026-10-22）

> 本包不发模型请求、不改生产代码、不新增运行器。所有结论来自四份冻结证据的静态核对、
> 两个现有入口的离线实测，以及一份独立标注的机械闭合对照。
> 离线实验只证明"可表达 / 可应用 / 确定性结果"，**不证明**模型下一轮会更好，
> 也**不替代**真实内容核验。

## 0. 结论（修正后）

1. **可确认的只有一件事**：`real_rev4` 那份响应里，`fact_bindings` 的 `f08 → {visual, b09}`
   是一条**合法的声明编辑**；把同一意图**只写成这一条声明 + 一处节拍正文**（不动声音），
   当前 delta 入口可以 `applied`、`76.50 s` 不变、`n07` 仍由 `b09` 播放。
2. **不能确认**"模型已经完成合法表达"：`real_rev4` 是**最终响应**，它同时提交了
   `f08 → visual/b09`、`b09.voice_ref → visual/b09`、以及 `n07` 的 upsert；
   没有中间执行状态支持"先做对、后多做一步"的过程描述。它整体是一个**关系不闭合的响应**。
3. **不能确认**"内容本身可用"：把 `n07` 删掉后只剩 `verify_record_missing`，
   恰好说明**内容核验还没做**；f08 的主张含"听香答应"，而 `b09` 冻结正文只有
   "张十三威胁听香不得声张"，**完整表达仍未成立**。
4. **入口选择（实测）**：同一完整目标下，受限 delta 需 2 个实体（1 条绑定 + 1 条节拍）；
   `fact_carriers + beat_ops` 需 **18 个实体**（1 条 beat_op + 9 条全量 `fact_carriers`
   + 8 条整表 `narration_lines`），且会把旧 `plan_verification` 带到候选上
   （`verify_digest_mismatch`）。**结论：保留 delta，不换 ops。**
5. **范围保护已接线并有对照**：结构合法但改声音正文/归属的响应会被 `scope_*` 拒收并指出字段
   （§5）；旧"删被引用单元"对照仍由引用守卫拒收——两者证明的是不同的东西。
6. **本包的成功条件不是"六条清零"**：声音在本轮被冻结，d03/n07 两条声音问题**必然仍在**。
   分项判据见 §6；整体状态仍须标为未通过。六条清零属于另一个目标（需放开声音修复并补样本）。

## 1. 证据（只读，摘要见 `failure_analysis/evidence_manifest.json`）

| 编号 | 用途 | 路径 | 备注 |
|---|---|---|---|
| E1 | 最新修订：请求、响应、拒收 | `d11_run/real_rev4/calls/send_01.json`、`run_log.json` | 响应同时提交声明、节拍改动与单元 upsert |
| E2 | 可应用候选 + 6 条真实核验问题 | `d11_run/real_rounds3/*`、`verify_rounds3_verify/verification_record.json` | 候选 `plan_digest 841bec5d…` |
| E3 | 拆分/重复后留孤儿 | `d10_runs/d10v2_revision_01/minimal_fix/original_response.json` | 两个孤儿 |
| E4 | 人工合法修法（协议可达性对照） | `.../minimal_fix/fixed_delta.json` | **从未真实核验**，不能当内容通过 |

基线：E1 用 `frozen_rounds3/candidate_plan.json`；E2/E3/E4 用 `d9_feasibility/input/plan.json`。
`novel_sha256 = aba1adc2…`。

## 2. 编辑意图表

| 响应 | 实际改了什么 | 与目标的差距 | 首个拒收点 | 说明 |
|---|---|---|---|---|
| **E1 rev4** | 声明：f08→`{visual,b09}`；节拍：`b09` 改画面、正文补"掌掴"；单元：upsert `n07`、`d03` | `b09` 正文仍缺"听香答应"；`n07` 变孤儿 | `narration_units[n07] view_voice_unit_unplaced` | 整体不闭合；**不能**拆成"先对后错" |
| **E2 rounds3** | 7 节拍 + 7 单元（含删 `n08`） | f02/f08/f09 仍是窄载体 | 无（applied） | 真实核验 6 条未过 |
| **E3 d10v2** | 9 节拍 + 8 单元 | 两个孤儿 | `view_voice_unit_unplaced` ×2 | R1' 归并后 105.65 s、预算超限 |
| **E4 人工修法** | 9 节拍 + 6 单元 + 3 条绑定 | f02/f08 未处理 | 无（applied） | 102.50 s、预算超限；未核验 |

四类归因（修正）：**内容选择本身错误——不能判定**（"选到相关原文"不等于"完整满足主张"）；
**内容有价值但关系未闭合——E1/E3 已确认**；**稿件中有内容但声明载体未覆盖——E2/E3 已确认**；
**请求/转换/验收使合法编辑不可完成——未发现**（合法声明路径可走通，见 §4）。

## 3. 三项假设

| 假设 | 判定 | 依据 |
|---|---|---|
| 复合事实 vs 单载体契约 | **不支持"契约限制表达"** | 声画共镜（事实挂画面、节拍照旧出声）往返保真、过验收；画面正文由模型撰写（只有 `evidence` 须逐字）；多载体是模式能力（旧模式 1 条），本目标不需要 |
| 模型承担过多机械维护 | **支持** | 同一目标：delta 需 1 条绑定 + 1 条节拍（7 字段）；ops 需 18 个实体；人工修法动了 3 条绑定，模型响应里 E2/E3 一条都没动 |
| 反馈是否可执行 | **部分支持** | 完整主张、缺失项、当前承载、预算都在请求里；声画共镜写在 `fact_bindings` 字段说明里，问题清单的修正指引只说"改挂到已有逐字正文的载体上" |

## 4. 双入口实测（同一完整目标）

**目标**（人工内容预期，未经真实核验）：`b09` 正文写成
"张十三掌掴听香并威胁她不得声张，听香答应听命"；`f08` 承载改 `B9/画面`；
**`b09` 的 `voice_ref` 保持 `narration/n07`**。

| 项 | 受限 delta | `fact_carriers + beat_ops` |
|---|---|---|
| 提交实体 | **2**（1 条绑定 + 1 条节拍，7 字段） | **18**（1 beat_op + 9 全量 fact_carriers + 8 整表 narration_lines） |
| 必须全量声明的触发 | 无 | 用了 `beat_ops` ⇒ `fact_carriers` 必须逐项给全（`outline.py:4469`，应用器 `:5235` 执行） |
| 镜像约束 | 未触发 | 触发：`N7.fact_refs` 仍登记 F8 ⇒ 还要改整张 `narration_lines` |
| 结果 | `applied`，`76.50 s` 不变，`n07` 仍在，`b09` 仍出声 | `applied`，但带 `verify_digest_mismatch`（旧核验记录随计划带入） |
| 确定性验收 | `['verify_record_missing']` | `['verify_digest_mismatch', 'verify_bad_response']` |
| 样本 | `failure_analysis/legal_sample_f08_delta.json` | 未保存（被否决的入口） |

**越权对照**（`failure_analysis/negative_control_voice_tamper.json`）：删掉仍被 `b09` 引用的 `n07`
⇒ `view_voice_ref_dangling` + `view_binding_dangling`，整份拒收。保护在**关系层**，不在入口层；
两个入口共用这条判据。

## 5. 范围保护（已接线，附三组对照）

### 5.1 权限定义（请求侧与执行侧应同一份）

```json
{"fact_bindings": ["f08"], "beat_text": ["b09"]}
```

受保护面固定为：`narration_units` / `dialogue_units` 的**集合与正文**、
`beats[].voice_ref`（声音归属）、节拍的集合与顺序、未列出的事实绑定。
实现：`shared.presentation_contract.scope_violations(base_view, candidate_view, scope)`；
`apply_delta_view(..., scope=)` 在**采纳边界**执行（`stage="scope"`），
运行器侧由 `D11_SCOPE` 在同一位置执行。默认 `scope=None` 不改变任何既有行为。

### 5.2 对照（全部离线、可复跑）

| 对照 | 构造 | 不带范围 | 带范围 |
|---|---|---|---|
| 合法 | 只改 f08 绑定 + `b09` 正文，`b09` 仍由 `n07` 出声 | `applied` | **`applied`**（76.50 s 不变） |
| 越权 A：改声音正文 | `n07`/`n08` 正文互换（两句都逐字来自原文，结构闭合） | `applied` | **拒收** `scope_voice_text_changed`：`narration_units[n07].text`、`narration_units[n08].text` |
| 越权 B：改声音归属 | `b09`↔`b12` 的 `voice_ref` 互换（两单元仍各播一次，结构闭合） | `applied` | **拒收** `scope_voice_ownership`：`beats[b09].voice_ref`、`beats[b12].voice_ref` |
| 引用对照 | 删仍被引用的 `n07` | 拒收 | 拒收（`view_voice_ref_dangling` + `view_binding_dangling`，**在范围检查之前**） |

运行器端到端：`D11_STAGE=offline` + `D11_SCOPE` + 上述两份响应 ⇒ 合法样本写候选、越权样本
`candidate_origin=scope_rejected`、`scope.ok=false`、**不写候选**；两次运行自检均 PASS。
样本：`failure_analysis/scope_legal_request.json`、`scope_tamper_voice_ownership.json`、
`scope_definition.json`。

> 与之前那份"越权对照"的区别：旧对照删了被引用单元，被拒是**引用守卫**；
> 这两组新的对照**结构完全合法**，只因改了受保护面被拒——这才证明"声音不在授权内"。

### 5.3 还没接线的一项（进入真实验证前必须补）

**请求侧尚未声明这份权限**：模型现在拿不到"本轮只允许改 f08 的绑定与 b09 的正文"这句话。
不补这一句，付费运行里的范围拒收对模型就是意外的，实验也不成立。
接线点：`run_plan_revision → revise_plan_once` 的请求构造（传入同一份 `scope` 并渲染成
"本轮编辑范围"小节），**不是**再改判据。这一步做完才值得发那一次修订。

## 6. 验收分层（替代"六条清零"）

**本包只验证一个问题**：限制编辑范围后，模型能否补齐目标事实的画面表达，同时保持声音、
其他事实与顺序不变。

| 层 | 判据 | 本包的期望 |
|---|---|---|
| 目标事实 | f08 的 `verify_fact_unsupported` 消失（真实核验判定） | 通过 |
| 保护 | 声音集合/正文/归属、节拍顺序、其他事实绑定均未变（`scope_violations` 为空） | 通过 |
| 非目标 | 现有 d03（内心独白占对白槽）、n07（发言占旁白槽）**仍然存在** | 记录为既有问题，**不要求清零** |
| 新增 | 不出现新的 `verify_*` / 结构问题 | 通过 |

因此本包**成功时整体状态仍是"未通过"**（`final_acceptance.codes` 至少还含两条声音问题）。
"下一轮六条全清零"是另一个目标：必须把 d03/n07 的声音修复纳入授权修改面，并先补
"类型迁移"的合法入口样本——**不能沿用"声音冻结"这套规格**。

## 7. 边界与不确定性

- **保留 delta、不换 ops**：本样本实测 ops 需 18 个实体（全量声明 + 整表镜像），delta 需 2 个。
  这条只支持"本样本不切换"，不外推到其他问题类型。
- **节拍部分 upsert 不作为下一步**：孤儿可能来自显式改错 `voice_ref` 或删除，而不是漏字段；
  只有失败证据指向"完整对象回写导致误改"时才实施。
- 限制修改面后模型是否守范围：未验证，需要请求侧声明 + 1 次修订 + 条件满足时 1 次核验。
- 画面承载的完整性核验尺度弱于旁白侧（`test_a_visual_carrier_expressing_only_part_of_the_fact_is_not_yet_caught`
  仍是 XFAIL），影响"画面是否算完整"的判定，但不影响范围保护本身。
- `legal_sample_f08_delta.json` 是人工内容预期，只证明可表达、可应用，**未经真实核验**。
