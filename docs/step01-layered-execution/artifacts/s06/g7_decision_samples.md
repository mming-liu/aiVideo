# S06（G7）决策样本：等价派生 / 目标丢失 / 保护违规

> 生成：`python tools/build_s06_g7_samples.py`（离线、零模型调用）
> 记账=`main._authorized_changes_lost`；目标=`main._audit_issue_resolved`；
> 保护=`main._locked_repair_signature`。三个样本均为匿名受控快照。
> **证据层级**：三个判据是真实函数；候选门的组合结果按规则推演，
> **未走完整提交入口**（G7 的现状列 = 记账规则 + 既有保护门，不是提交点实测）。

| 样本 | postprocess_reverted | 目标丢失 | 保护字段变化 | 记账规则 | 现行保护门 | R1 任何残余 | R2 目标丢失 | R2+保护 | 应然 |
|---|---|---|---|---|---|---|---|---|---|
| A_equivalent_derivation | 1:prompt | 过 | 过 | 过 | 过 | 拒 | 过 | 过 | accept |
| B_target_loss | 1:ff_desc | 拒 | 过 | 过 | 过 | 拒 | 拒 | 拒 | reject |
| C_protection_violation | 空 | 过 | 拒 | 过 | 拒 | 过 | 过 | 拒 | reject |

## 逐样本结果

### A_equivalent_derivation

- 说明：等价派生：授权路径被后处理改写，但修复目标仍达成
- 生产者：step01_script_gen.constraints.v6_43_prompt_enforcement._fix_vague_anchor
- 合并后：`prompt='刺客潜伏水下，仅露双眼。'`，`ff_desc='甲立在渡口。'`，`narration_segment='甲走过石阶。'`
- 后处理后：`prompt='刺客潜伏水下，仅露双眼（不是上半身出水）。'`，`ff_desc='甲立在渡口。'`，`narration_segment='甲走过石阶。'`
- 记账：`postprocess_reverted={"1": ["prompt"]}`
- 目标：合并后达成=True，后处理后达成=True → 丢失=False
- 保护字段变化=False
- 应然：accept

### B_target_loss

- 说明：目标丢失：授权删除被后处理改回修前值
- 生产者：synthetic revert（修前快照回填；用于验证目标丢失判据）
- 合并后：`prompt=''`，`ff_desc='机甲立在废墟。'`，`narration_segment='甲走过石阶。'`
- 后处理后：`prompt=''`，`ff_desc='窄身机甲立在废墟。'`，`narration_segment='甲走过石阶。'`
- 记账：`postprocess_reverted={"1": ["ff_desc"]}`
- 目标：合并后达成=True，后处理后达成=False → 丢失=True
- 保护字段变化=False
- 应然：reject

### C_protection_violation

- 说明：保护违规：后处理改了锁定字段（旁白），授权路径未变
- 生产者：synthetic locked-field change（用于验证保护门；正式链路由 locked_changed 拦）
- 合并后：`prompt='甲快步走过石阶。'`，`ff_desc='甲立在渡口。'`，`narration_segment='甲走过石阶。'`
- 后处理后：`prompt='甲快步走过石阶。'`，`ff_desc='甲立在渡口。'`，`narration_segment='甲走过长阶。'`
- 记账：`postprocess_reverted={}`
- 目标：合并后达成=True，后处理后达成=True → 丢失=False
- 保护字段变化=True
- 应然：reject

## 结论（供裁决）

- **记账规则本身**：三例都不拒收——`postprocess_reverted` 只是记录。
- **现行保护门**：C 的保护违规会被既有 `locked_changed` 拦下；A/B 不触发保护门。所以「现状」是：A 过、B 过（目标丢失未被拦）、C 拒。
- **R1（任何残余都拒）**：A 的等价派生被误拒——授权路径被后处理改写不等于目标丢失。
- **R2（仅目标丢失拒）**：A 过、B 拒；但 C 的保护违规不在授权路径上，`postprocess_reverted` 为空，需要既有 `locked_changed` 保护门单独拦截。
- **R2 + 既有保护门**：三个样本均符合应然结果（**仅证明这组三例的判据组合**，不是真实提交入口的最终读数）。

因此 G7 的裁决对象应写成「**按目标丢失接门，并保留既有保护门**」，而不是「按任何残余接门」。
