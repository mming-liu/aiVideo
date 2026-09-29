# Step01 十次连跑问题报告（chapter_001）

> 复测（P1–P6 实施之后，2026-09-28）见 [step01-ten-run-report-2026-09-28-p1p6-retest.md](step01-ten-run-report-2026-09-28-p1p6-retest.md)：本文 §4 的六项建议逐项裁决与四项验收的前后对比都在那一份。

日期：2026-09-27。范围：同一份代码版本下连续跑 **10 次 step01 第一章**，汇总结论与逐条证据。
本文可独立阅读；每条问题都给出**可复核的文件/日志出处**与**判定依据**。

---

## 0. 方法与可复核性

| 项 | 值 |
|---|---|
| 跑次 | 10 次连续（串行，章节锁独占；无并发写） |
| run_id | `20260927T165916-98960` … `20260927T182017-97332`（见 §1 表） |
| 总耗时 | **5447s ≈ 91 分钟**（单次 244–796s） |
| 终局 | **10/10 BLOCK** |
| 正式产物 | **未替换**——`script_result.json` / `step01_quality.json` / `step01_audit.json` 仍是 09-24 08:11 那版（mtime 复核） |
| 稿件身份 | 每次都是**本轮新稿**进提交门：10/10 命中 BEST-GUARD 的「不选历史稿」路径（§3.4 是这条改动的验证） |
| 数据归档 | `.tmp/run10/run_NN/{step01_commit.json,step01.log,step01_repair_rounds.json,…}`、`.tmp/run10/audit_ledger/`（审计台账滚动 40 条，用守护进程逐轮另存）、`.tmp/run10/{summary,report_data,final_rows}.json` |
| 分析脚本 | `.tmp/run10_step01.py`（跑批）、`.tmp/agg_run10.py`（聚合）、`.tmp/run_one.py`（单次速览）、`.tmp/final_rows.py`（停止原因/丢弃数） |

**两份读数的口径差异**（重要）：`step01_commit.json` 的 `blocking` 数组被**截断到 20 条**
（`shared/quality_gate.py:468`：`"blocking": list(blocking or [])[:20]`，而 `blocking_count` 是真实值）。
所以：

- **总计与「审计/确定性」拆分**取自每轮日志行 `[COMMIT] 第 N 轮发现 X 条 blocking（审计 A + 确定性 B）`——**准确**；
- **逐 code 分布**只能按清单统计 → 是**下限**（36 条的 run 只数得到 20 条）。

---

## 1. 逐次读数

| run | run_id | 耗时 | 终局 blocking | 确定性 / 审计 | 新稿镜数 | 总帧 | 缺段镜 | 修复轮 | 停止原因 |
|---|---|---|---|---|---|---|---|---|---|
| 01 | `…T165916-98960` | 520s | 26 | 17 / 9 | 15 | 31 | 14 | 2 | `locked_changed` |
| 02 | `…T170641-11736` | 434s | 26 | 23 / 3 | 14 | 30 | 12 | 1 | 超限联动组（7 镜） |
| 03 | `…T171344-94908` | 244s | 27 | 20 / 7 | 16 | 48 | 2 | 1 | `locked_changed` |
| 04 | `…T171840-9432` | 422s | 21 | 16 / 5 | 12 | 25 | 7 | 1 | 超限联动组（12 镜） |
| 05 | `…T172649-75548` | 659s | **36** | 25 / 11 | 15 | 30 | 15 | 3 | `kf_structure_invalid` |
| 06 | `…T173730-3232` | 448s | **17** | 14 / 3 | 15 | 31 | 7 | 1 | 超限联动组（14 镜） |
| 07 | `…T174719-82888` | 796s | 17 | 15 / 2 | 11 | 23 | 10 | 3 | `locked_changed` |
| 08 | `…T180151-53656` | 712s | 28 | 21 / 7 | 15 | 30 | 15 | 2 | `locked_changed` |
| 09 | `…T181003-69508` | 675s | 24 | 21 / 3 | 14 | 33 | 9 | 4 | `kf_structure_invalid` |
| 10 | `…T182017-97332` | 537s | 23 | 21 / 2 | 16 | 32 | 16 | 2 | 超限联动组（8 镜） |
| **合计** | | **5447s** | **245** | **193 / 52** | 143 | 313 | **107** | 20 | |

**三条一眼可见的结论**

1. **确定性判据占终局 blocking 的 79%**（193/245），审计只占 21%。「内容审计挡提交」不是本批的主因。
2. **停止原因没有一次是「修到没得修」**：`locked_changed` 4 次、超限联动组 4 次、`kf_structure_invalid` 2 次——全部是**闸门把整轮修复判死**。
3. **新稿欠帧是常态**：143 镜里 107 镜（75%）有动作时段没有对应关键帧。

---

## 2. 问题清单（按影响排序）

### P1 ★ 合规替换被审计当成「改词」，修复照办后撞锁定闸门 → 整轮修复作废（4 次跑 / 丢 35 条修复）

**现象**：审计报 `DIALOGUE_WRONG_WORDS`，要求把台词改回原文的「杀人」；修复模型照办；
`narration_segment` 是锁定字段 → 整轮被回退 → 判「零改善」→ **停止修复**。

**证据（逐字，run 01）**

```
run 01 model_audit_DIALOGUE_WRONG_WORDS @shot 6
  msg: 关键尖叫台词被改词，与原文及后续"杀人啦"的惊恐语气不符。
       建议：改为原文"公……公子他……杀……杀人啦……"
  ev : 台词写作"公…公子他…杀…出人命啦…"，原文为"公……公子他……杀……杀人啦……"

[REPAIR] 锁定字段变化: ["6.narration_segment: '听香道：公…公子他…杀…出人命啦…' → '听香道：公……公子他……杀……杀人啦……'"]
[REPAIR] 修复未满足接受条件（reason=locked_changed, resolved=1, 字段触达=1, new=0, ...），回退本轮修复
[COMMIT] 提交前修复轮零改善（稿件未变；上一轮事务 accepted=False resolved=1 reason=locked_changed），停止修复
```

**复现**：10 次里 **6 次**出现锁定违规事件（共 7 次；run 07 两次），字段**全部是 `narration_segment`**，
镜号 5/6/7/13；其中 4 次（01/03/07/08）直接成为**停止原因**。run 01 与 run 05 的 diff **逐字相同**。

**机制（两侧都有出处，缺口在审计侧）**

- 确定性侧**知道**这个替换：[`validation.py:696-700`](../../step01_script_gen/validation.py) —「合规自动替换层（`sanitize_narration`）会把"杀人"→"出人命"等……双向归一化：narration 里的"出人命"还原为"杀人"以跟原文对齐」。所以 `_check_grounding` 不误报。
- 审计侧**完全不知道**：审计的 system prompt 与规则模板都在 [`audit.py`](../../step01_script_gen/audit.py)（`_AUDIT_SYSTEM` + 规则模板 + `build_audit_prompt`），全文只有一处「原文」（标题行），**没有任何一句说明 narration 可能因合规替换而与原文不同**。

**影响**：这是「一个正确的闸门 + 一个错误的输入」的组合。闸门拦得对（CLAUDE.md：任何修复不得静默改动已锁定台词），
但它拦下的是**审计指使的**改动；结果是每轮修复预算被清空并提前终止。run 03 一次丢掉 **13 条**已完成的修复。

**建议方向（需裁决）**：在审计契约里注明存在合规替换（列出替换对，或给出「台词与原文的差异若属于既定合规替换 → 不算缺陷」的判据）；
`audit.py` 的 `audit_prompt_version()` 会随提示变化自动失效旧审计，改这一处不影响复用语义。

---

### P2 ★ 审计单条问题声明超上限联动组 → 构造上不可修，直接 BLOCK（4 次跑）

**现象**：审计把一次 **systemic** 发现写成**一条** issue + 覆盖很多镜的 `repair_targets`，
而联动组上限是 6（`_REPAIR_MAX_SHOTS_PER_GROUP`，由来见 `main.py:5018`：6 镜约 8–10k token / 输出上限 16k），
且代码**拒绝拆组**（拆组=半条修复、删目标=漏声明）→ 该 run 不调 LLM、不再发审计，直接 BLOCK。

**证据（run 06，主控源：审计台账里的那条 issue 本体）**

```
run 06 审计第 2 轮: model_audit_INVALID_STAGE_VALUE
  repair_targets 覆盖 14 镜 ['1','3','4','5','6','7','8','9','10','11','12','13','14','15']

[REPAIR] 联动组 ['shot_1','shot_10',…,'shot_9'] 共 14 镜，超过联动组上限 6 → **无法整组修复**
         （拆组=半条修复、删目标=漏声明，两者都禁止；该镜数是单条问题的声明或多条共享镜头合并后的并集）
         ——请检查审计契约或稿件本身
```

**分布**：run 02（7 镜）、run 04（12 镜）、run 06（14 镜）、run 10（8 镜）——**4/10 次**。

**机制**：审计是模型，systemic 缺陷（如「stage 枚举取值非法」遍布全篇）会被表达成一条问题 + N 个成对目标；
N > 6 时该问题在构造上不可修。当前提示里没有「单条问题的目标镜数上限」。

**建议方向（需裁决）**：审计契约加约束（单条 issue 的 `repair_targets` 覆盖镜头数 ≤ 6；systemic 缺陷按镜拆成多条），
或在提交层把超限组**按镜拆成独立目标**（这与既有「拆组=半条修复」的裁决冲突，故不建议单方面改）。

---

### P3 ★ 生成侧欠帧：模型稳定写「3 段 2 帧」（10/10 次，确定性第一大类）

**现象**：`motion_desc` 显式分 3 段，`keyframes` 只写 2 帧，且第 2 帧的**内容**常属于第 3 段
（按 `stage` = pre + climax 组帧，而非按段组帧），于是末段既无时间归属、又带出 `kf_event_window_mismatch`。

**证据（10 次新稿池化，143 镜）**

| (段, 帧) | 镜数 | 占比 |
|---|---|---|
| **(3, 2)** | **100** | **70%** |
| (3, 3) | 26 | 18% |
| (2, 2) | 15 | 10% |
| (2, 3) / (4, 2) | 2 | 2% |

逐次缺段镜：14/15、12/14、**2/16**、7/12、15/15、7/15、10/11、15/15、9/14、16/16。
唯一接近合规的是 run 03（新稿 16×3=48 帧，缺段镜 2），该轮 `kf_segment_missing` 终局为 **0** —— 说明
**规则生效时它确实能消掉这一整类**。

**已排除的两件事**

- **不是提示缺失**：进程内验证 `build_prompt()` 与 `SYSTEM_PROMPT` 都含新写回的那条规则
  （判别短语 `每个正文非空的时段至少有一帧`，三份生成面 `main.md` / `system.md` / `doubao_system.md` 同措辞，
  守卫见 `tests/step01_script_gen/test_generation_kf_contract.py`）。
- **不是代码删帧**：`motion_segment_kf_align` v6.103 起只报告、`kf_frame_redundancy` 是 WARN 且不删有时段承载的帧。

**结论**：这是**生成侧遵从度**问题（09-21 复盘记录过同一现象）。当前唯一杠杆是 4 轮定向修复，
而每轮只动 ≤4 镜、且随时可能被 P1/P2/P4 判死。

**建议方向（需裁决）**：把「缺段」接进**结构收敛重生成**（带 `_structural_feedback` 的一次重生成），
而不是只靠定向修复轮；或者在生成契约里给**逐段帧数的范例**（现在的 JSON 范例里 `keyframes` 只有 1 个元素）。

---

### P4 ★ 接受闸门丢弃已完成的修复，且每次丢弃都触发「零改善 → 停止修复」（丢 60/109 = 55%）

**证据（10 次跑、20 个修复轮，按 `[REPAIR] 修复未满足接受条件` 行统计）**

| 拒绝原因 | 轮数 | 被丢弃的 `resolved` |
|---|---|---|
| `locked_changed` | 5 | 35 |
| `new_blocking` | 2 | 12 |
| `kf_structure_invalid` | 3 | 13 |
| **合计** | **10 轮 / 20 轮** | **60 条（占全部 109 条的 55%）** |

**机制**：三个闸门（锁定字段、新增阻塞即回退、KF 结构验收）都是**轮级**判定——一处越界 → 整轮回退；
回退后该轮 `resolved` 被清零 → 外层判「零改善」→ 按既有策略**停止修复**（不再消耗后续轮次）。

各闸门**单独看都是对的**（都在防「让告警消失而不是修好」）。问题在粒度：**一条目标上的违规，
代价是同一轮里其它目标的全部真修复**。

**建议方向（需裁决）**：把回退粒度从「整轮」收窄到「肇事目标」（`_new_blocking_targets` 已有定位能力），
保留「该目标整组回退」的既有语义；或至少在判「零改善」时把「因闸门回退而放弃的 resolved」单列，
不要与「模型没改对」混为一谈。

---

### P5 帧内容与时段错位：`kf_event_window_mismatch`（下限 22 条 / 10 次跑）

形态：帧落在某段的时间窗内，但 `desc` 写的是**另一段**的事件。与 P3 同源（按 stage 组帧）：

```
~ shot 2  motion: 前2秒 钓鱼 / 2-4秒 生火 / 4-5秒 望星空饮酒
  kf t=1.0 pre      → 钓鱼     （段 1）
  kf t=3.5 climax   → 望星空饮酒（段 3 的内容，落在段 2 的窗内）
```

后果：末段（4-5s）无帧 + 一条内容错位。**该类的修法提示里已写「改写文案 + 为本段补帧，不必搬 t_sec」**，
但受 P3/P4 影响，10 次里从未被清完。

---

### P6 纯 lint 类缺陷也有一票否决权：`multi_person_staging`（下限 20 条 / 7 次跑）

形态：`frame_layouts.kfs[i][j].pose` 缺朝向词（如 `怒极，右手垂于身侧` 缺 面向/背对/侧身/正对）。
这是**一个词就能修**的 lint，但它和结构性缺陷同权 blocking，并且进的是同一张 ≤4 目标的修复名额表；
run 09 一轮里就有 6 条。建议（需裁决）：这类 lint 是否应降为 WARN 或单独一个「可批量确定性修复」通道
（它的修法是纯文本追加，可确定性完成，不需要 LLM）。

### P7 `shot_duration_below_min`：**恰好 3.00s** 的镜头（12 条 / 4 次跑）

```
镜头5 时长 3.00s 低于 3.5s 下限——需合并到相邻镜或补充 motion_desc/台词，不能靠事后加秒数
```

提示侧 [`system.md` B1](../../step01_script_gen/prompts/system.md) **明写**：「非末镜 ≥ 3.5s（末镜 ≥ 4s）。
**写 3.0-3.4s 的镜头会被质量门判 blocking**——不要写这个区间」。模型照写 3.00s。
同 P3，属生成侧遵从度；但它每次出现都稳定贡献 blocking。

### P8 其余确定性类（下限计数，均为可确定性判定）

| code | 条数 | 形态 |
|---|---|---|
| `prop_possession_consistency` | 6 | 描述「杨旭放下玉箫/酒壶」而同帧布局仍列为持有 → 道具时间线倒流 |
| `split_boundary_replay` | 5 | 后镜复演前镜 LF 已完成动作（相似度 ≥0.62） |
| `frame_presence_consistency` | 3 | `老农身影消失在田埂尽头` / `老农已走远` / `庄仆们已走远` 而布局仍 `on_screen=true`——**全部是完成态真缺陷**（见 §3.2） |
| `kf_person_missing_in_layout` | 3 | 帧描述里明确可见的人物缺布局条目 |
| `kf_time_out_of_shot` | 2 | KF 时刻超出镜长 |
| `result_action_support` | 2 | LF 结果动作在 `motion_desc` 里无过程段 |
| `shot_content_over_cap` | 2 | 内容最低需求 12.5s > 单镜上限 12.0s |
| `kf_temporal_consistency` / `duration_budget` | 各 1 | — |

### P9 审计 code 不是受控词表（影响所有按 code 的统计与回归）

**40 个字面 code**，归族后约 **10 族**。同族之间只有大小写与措辞差异：

```
FUTURE_STATE_EARLY / future_state_early / FUTURE_STATE_IN_FF / FF_STATE_CONTRADICTION / future_state_in_ff
ON_SCREEN_CONFLICT / on_screen_contradiction / ONSCREEN_CONTRADICTION / OFFSCREEN_ACTIVE_STATE
STATE_CONTRADICTION / state_contradiction / INTERNAL_CONTRADICTION / CROSS_FIELD_CONFLICT
```

**影响**：`step01_commit.json` / 审计台账里的 code 按字面聚合会把同族打散（本报告因此同时给字面与归族两份读数）；
任何「按 code 白名单」的判据（如修复优先级表）都会漏。

**归族后的分布（终局 52 条，此侧未被清单截断——各条合计正好 52）**：

| 族 | 条数 | 出现镜 |
|---|---|---|
| `state_contradiction` | 9 | 2,4,5,6,9 |
| `dialogue` | 9 | 5,6,7,12,13,14,16 |
| `prop_state` | 7 | 2,3,5,6,10 |
| `state_chain_mismatch` | 4 | 2,3,10,13 |
| `missing_beat` | 3 | 6,14,15 |
| `frame_role` | 3 | 1,4,7 |
| `future_state` / `timing` / `trailing_text` | 各 2 | — |
| `action_duplicate` / `on_screen` / `state_prop_no_motion` / `missing_climax_beat` / `text_garble` / `missing_character` / `missing_action` / `text_corruption` / `frame_stage_violation` / `missing_key_beat` / `duplicated_text` | 各 1 | — |

**台账全部 64 轮（含中途轮，未截断）**：`state_contradiction` **65**、`prop_state` 38、`future_state` 31、
`missing_beat` 23、`dialogue` 21、`on_screen` 14、`frame_role` 7、`state_chain_break` 7、`timing` 4、
`state_chain_mismatch` 4、`text_corruption` 4、`action_duplicate` / `state_inconsistent` 各 3 …

**读法**：中途轮的总量（≥300 条）远大于终局（52 条），说明审计每轮都在产出新条目而不是复述旧条目
——与 P10 的「零留存」互为印证：**收敛靠的是修复改对了，不是审计口径稳定**。

### P10 审计逐轮「零留存」：4/10 次出现「仍在 无」

`[COMMIT] 审计身份比对（(镜头,族) 归并措辞漂移）` 行里，上一轮的 `(镜头,族)` 身份**全部消失**、
同时冒出新的一批：run 06/07 各 1 次、run 09/10 各 2 次。

**影响**：`消失` ≠ `已核实修好`。因此「`resolved=N`」不能单独当作修复有效的证据，
`new=0` 也不能当作「没引入新问题」——审计面本身每轮在换。建议（需裁决）：
报告口径把「审计身份消失」与「确定性 blocking 消失」分开陈述。

### P11 复核清单被截断，外部无法完整复核（`shared/quality_gate.py:468`）

```
"blocking_count": len(blocking or []),      # 真实值（本批最大 36）
"blocking": list(blocking or [])[:20],      # 清单只留前 20 条
```

本报告只能按清单统计逐 code 分布 → 只能给**下限**。建议把截断显式化（写明 `blocking_truncated: true` +
`blocking_count`），或按「每 code 至少留一条」截断。

### P12 修复模型输出里的空条目：`shot_id 为空`（7 次 / 10 次跑）

`[REPAIR] 替换被拒 1 条: ['shot_id 为空']`——模型返回的 `shot_replacements` 里有条目缺 `shot_id`，
每次丢一个修复目标（09-23 起就有，非本轮引入）。量小但稳定，属可确定性剔除/告警的形态。

### P13 既有死判据（只记录，未改）

`validation._check_kf_time_window_alignment` 的**检测 3（时段顺序倒装）不可能触发**：
`mid_kfs` 在检测前已按 `t` 升序排序，段序号必然非递减。同一缺陷已由
`quality_issues._keyframe_timeline_issues` 的**列表序**严格递增（`kf_time_not_strict` /
`kf_time_too_close` / `kf_event_duplicate`）覆盖，故本轮未复活它（复活 = 新增阻塞面，属改判据）。

---

## 3. 本轮四项裁决的验证结果

### 3.1 缺段只计一次（裁决 3）——✅ 生效

- 同一稿（08:11 快照）实测：blocking **24 → 16**，`kf_time_window_alignment` 不再重复上报缺段，
  诊断类别 `kf_segment_coverage_diag` 保留 8 条 advisory（不进风险数、不进修复 prompt）。
- 本批 10 次终局里 `kf_time_window_alignment` 只在其自身语义（越窗/顺序）上出现；
  缺段全部走 `kf_segment_missing`。
- 授权面同步收窄：`kf_time_window_alignment` 移出 `_KF_STRUCTURAL_CODES`（不再凭旧类别拿「可增删整张 KF 列表」）。

### 3.2 「退下」时态判据（裁决 2）——✅ 生效，且未被削弱

- 本批 10 次里 `frame_presence_consistency` 只报 **3 条**，逐条 evidence 是
  `老农身影消失在田埂尽头` / `老农已走远` / `庄仆们已走远`——**全部完成态**。
- 裸「退下/散去」这类进行中离场**零误报**（裁决前正是它制造了 shot 12 的新造阻塞）。
- v6.115 的反例（`已倒下`、`仍在远处可见`）未复活。

### 3.3 生成契约写回生成面（裁决 1）——✅ 提示已到位，⚠️ 模型遵从度波动

- 三份生成面同措辞，进程内验证进入实际输入（§P3）。
- 效果是真实的但**不稳定**：run 03 整篇按段给帧（缺段镜 2/16），其余 9 次 70% 的镜仍是 3 段 2 帧。
- 成本读数按你的更正：**新稿欠 107 帧**（143 镜 313 帧，逐次缺段镜见表 §1），不是「约 45 帧」。

### 3.4 BEST-GUARD 空转条件（裁决 4）——✅ 生效

- **10/10 次**命中 `[BEST-GUARD] 全部历史快照都在欠账台账内（20 份，判据指纹未变）——本轮**不选历史稿**，
  让新稿直接进提交门检验；欠账最重的几份：ff06662b×2、eedf752287×2、6752f4ab2ec1×2、…`。
- 每次都由**本轮新稿**进提交门；正式产物 10 次都没被替换（保护有效）。
- 副作用（预期内）：本批因此是对**新稿**的 10 次独立检验——这正是本报告的数据基础；
  同时暴露出「新稿质量本身离门还差多远」（245 条 blocking）这一此前被「选历史稿空转」掩盖的事实。

---

## 4. 建议的下一步（按优先级；标注是否需要裁决）

**状态（2026-09-27）：用户裁决「全做」→ P1–P6 已实施，实施记录见
[`docs/archive/step01-temporal-contract-and-quality-gate-plan-2026-09-27.md` §15](archive/step01-temporal-contract-and-quality-gate-plan-2026-09-27.md)。
本地判别性测量：`multi_person_staging` 34 → 7（10 份新稿走完整质量门）；
全量回归 2674 passed / 1 skipped。10 次复测结果见 §6。**

| # | 动作 | 性质 | 状态 |
|---|---|---|---|
| 1 | **审计契约补合规替换说明**（P1）：让 `audit.py` 的规则知道 narration 可能存在 `杀人→出人命` 这类既定替换 | 提示改动 | ✅ 已实施（§15.1） |
| 2 | **回退粒度收窄到肇事目标**（P4）：`locked_changed` / `new_blocking` / `kf_structure_invalid` 只回退肇事目标，保留同轮其它目标的修复 | 改事务语义 | ✅ 已实施（§15.2） |
| 3 | **审计目标数上限**（P2）：单条 issue 的 `repair_targets` ≤ 6 镜，systemic 缺陷按镜拆条 | 改审计契约 | ✅ 已实施（§15.3） |
| 4 | **缺段接入结构收敛重生成**（P3）：生成侧给一次带反馈的重生成，而不是只靠 ≤4 镜/轮的定向修复 | 改收敛契约 | ✅ 已实施（§15.4） |
| 5 | **lint 类确定性修复/读权威字段**（P5）：`multi_person_staging` 的朝向判定改读 `frame_layouts.gaze_target` | 改阻塞判据 | ✅ 已实施（§15.5，实测 34→7） |
| 6 | **报告口径**（P10/P11）：`blocking` 截断显式化；「审计身份消失」与「确定性阻塞消失」分开陈述 | 可自行实施 | ✅ 已实施（§15.6） |

**本报告未提出的动作**（后续同样不做）：放宽任何已确认的阻塞判据、增加修复轮数、
恢复「全欠账放开选稿」。前者会让缺陷静默通过，后者正是裁决 4 要治的空转。

### 4.1 本轮新暴露、尚未处理的两类（下一轮取证）

P5 的测量第一次拿到**未截断**的新稿确定性分布，露出两个此前被清单截断掩盖的大类：

| code | 10 份新稿条数 | 说明 |
|---|---|---|
| `kf_person_missing_in_layout` | **83** | 帧描述里明确可见的人物缺 `frame_layouts` 条目 |
| `timeline_consistency` | **40** | 时间轴类不一致（尚未逐条归类） |

两者都不在 P1–P6 范围内，按「不以数量认定性质」的既有口径，应先逐条分类再决定
是否接入门/修复路径（方法与 P4 的取证同构：冻结样本 + 逐条归类 + 判别性反例）。


---

## 附录：本批数据文件

```
.tmp/run10/summary.json            跑批逐次 rc/status/blocking/duration
.tmp/run10/final_rows.json         逐次停止原因/锁定违规/超限组/resolved 明细
.tmp/run10/report_data.json        逐次读数 + 跨次聚合（确定性/审计字面/审计归族）
.tmp/run10/agg_out.txt             聚合脚本的可读输出
.tmp/run10/run_NN/                 逐次产物副本（commit/quality/audit/repair_rounds/timing/metrics/script_result/log）
.tmp/run10/audit_ledger/           审计台账逐轮留档（滚动 40 条会被挤掉，用守护进程另存）
output/video/锦衣夜行/part_01_杀青州/chapter_001/snapshots/   每次 run 的新稿候选 + 终稿 + 完整日志
```
