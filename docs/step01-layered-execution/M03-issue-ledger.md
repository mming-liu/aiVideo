# M03：已确认问题的状态台账（2026-11-12）

本文件是统一收口包 **M03** 的交付物。

- 责任文件：`step01_script_gen/issue_ledger.py`（新增，纯函数）、
  `tests/step01_script_gen/test_issue_ledger.py`（9 条）、
  `tools/build_m03_issue_status.py`。
- 产物：[`artifacts/m03/issue_status.json`](artifacts/m03/issue_status.json) /
  [`issue_status.md`](artifacts/m03/issue_status.md)。
- 输入：`tests/step01_script_gen/fixtures/u6_confirmed_issues.json`（S01 人工裁决的
  身份与处置）+ 冻结终局审计 `baselines/…/phase2_20261112T072011/final_audit.json`。

## 1. 现状缺口（已核）

- 全仓**没有**带「候选身份 / 证据 / 状态」的跨轮问题台账；`step01_audit_adjudication.json`
  是**整份覆盖**的影子报告，没有状态字段。
- 提交门的解决判据是 `resolved = before_keys - after_keys`——审计每轮重跑，于是
  「本轮没报出」与「已修好」在代码里**等价**。S06 已实测到同一份**未变**稿件上
  3 条确认缺陷没被再次报出。

## 2. 口径

| 状态 | 含义 | 是否关闭 |
|---|---|---|
| `still_reported` | 本次仍被报出 | 否 |
| `not_detected_this_round` | 本次**没有**报出（≠ 已解决） | 否 |
| `needs_reverify` | 没有观察输入，或报出了但身份/证据对不上 | 否 |
| `fixed_with_evidence` | 有确定性复核证据或重新裁决 | 是（须给 `kind` + `detail`） |
| `withdrawn` | 人工撤销 | 是（须给 `reason` + `adjudicator`） |

三条硬约束：

1. **关闭必须有证据**：`fixed_evidence` 缺 `kind`/`detail` 直接抛 `LedgerError`，
   不静默降级；「本次没报出」本身不足以关闭。
2. **撤销必须留依据**：缺 `reason` 或 `adjudicator` 即拒收。
3. **身份按（镜头, 字段, 事实）归并**：不含 `code` 作为主身份——同一事实换 code
   是实测行为（`PROP_CONTINUITY` → `STATE_CHAIN_CONFLICT`），按 code 比会记成
   「消失 + 新增」。事实指纹取 evidence（缺失时用 code 兜底）；同一 (镜头, 字段)
   存在多个不同事实时才用指纹消歧，唯一事实保留字段级匹配，避免 evidence 措辞
   增删细节把「仍被报出」误判成漏报。

人工判为 `legal_expression` / `improvement` / `undecided` 的条目只记状态，
**不进**「确认缺陷」的分子分母。

## 3. 读数：与 M01 的重算一致

`python tools/build_m03_issue_status.py`：

```json
{"total": 10, "confirmed": 7, "still_reported": 4, "not_detected": 3,
 "needs_reverify": 0, "fixed_with_evidence": 0, "withdrawn": 0,
 "closed": 0, "non_defect": 3, "confirmed_closed_ratio": "0/7"}
```

未检出的三条是 `u6-08`（镜 10 掌掴前披头散发）、`u6-09`（镜 4 凭空提壶）、
`u6-10`（镜 7 刺客握刃）——与 M01 §6 的逐条对照一致。
`u6-01`（合法省略）与 `u6-07`（待裁决）没有被算成漏报。

## 4. 确定性判断的收窄（复核结论）

按计划要求逐项核了现行确定性检查，**收窄已经在位**，本包不改判据：

- **景别不是硬门**：`validation.py` 的 `shot_size_jump` 是 warning 且不在
  `_QUALITY_BLOCKING_CATEGORIES`；`constraints/framing_jump.py` /
  `framing_variety.py` 都是 `Severity.WARN`；`FRAMING_CONFLICT` 只在影子 finding。
- **道具归属不是「名字出现即持有」**：判定要「持有标记同分句」或「身体部位词后
  6 字内出现道具名」（`script_findings.py` 的 `_prop_state` / `_acquire_in`）。
- **时间省略有显式通道**：`_explicit_time_jump` + `context_bridge`；
  `_scene_changed` 只比字符串。
- **枚举/身份/字段路径**走确定性校验（`validate_repair_targets`、`deterministic_checks`）。

仍然存在的**未覆盖**（记为待办，不冒充缺陷）：

1. `scan_script` 的 `ADDED_DIALOGUE` 在两条生产调用点都不传 `source_text` → 生产死码。
2. 三处 blocking 口径不完全一致（`quality_issues.is_blocking` /
   `audit_adjudication._BLOCKING_SEVERITIES` / `quality_gate`）；当前因审计侧已归一
   而不产生可见差异，但没有单一事实源。
3. 「仅 `scene` 变化、无时间跳跃文字」的道具/状态链豁免没有独立对照
   （现有用例同时改了 `scene` 与 `context_bridge`）。
4. 因果类**没有**确定性检查函数；`causal_gap` 属 Step02a。

## 5. 边界

- 台账是**离线可构建**的纯函数，尚未接进提交门（计划要求先影子比较）。
  接线点建议：提交点把 `step01_audit.blocked.json` 与人工裁决集一起喂给
  `build_issue_ledger`，把 `closed` 作为「允许提交」的附加条件——这属于准入语义变更，
  按 M09 的 `[GATE]` 统一裁决，本包不动。
- 夹具是 U6 事故的独立回归数据（含该章事实），不作为跨内容通用性证据；
  通用性由 `audit_narrative_families` 的成对样本承担。
