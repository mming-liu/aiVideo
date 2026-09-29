# Step01 候选层台词完整性硬门 + 死锁收口计划 — `sunny-kindling-wilkinson`

> **Context — 为什么要做这个改动**
>
> 2026-09-26 02:30:36 → 02:37:07 重跑（`TEXT_PROVIDER=agent_plan` / `SCRIPT_CANDIDATE_MODELS=deepseek-v4-flash` 单模型 / 审计仍走 `AUDIT_TEXT_PROVIDER=deepseek` 原生通道）是**里程碑也是死锁首现**：
>
> - 新稿**首次通过 BEST-GUARD 进入提交门**：新终稿 100 分/风险 13 > 快照 17，历史快照未过当前质量初筛；BEST-GUARD 三连选历史的链条首次断掉。
> - 审计 **v6.134/134.1 首考通过**：`status=ok / 13 issue / 5 blocking / 单次成功无重试 / schema_deviations=[] / attempts 字段缺席`，证明有界重试与根对象 `issues` 收口在产线生效。
> - 但提交 **BLOCK**：`accepted=False resolved=4 reason=locked_changed`；直接原因是 shot12 `narration_segment` 截断（`…有些生意上的事情急需` 戛然而止，审计 code=`truncated_text`），修复补丁把 shot12 补全 + 删 shot13 重复前缀，**动了锁定字段 `narration_segment`** → 绑定闸门按设计整组回滚 → `repair_outcome=no_progress` 的前身被 09-25 的 `call_failed/patch_unusable` 分流后，这次以 `locked_changed` 暴露 → 零改善即 BLOCK。
> - 选材服从逐字核对：**6/7 命中、1 句破损**（张十三长台词跨 shot12/13 拆截断 + 重复开头），与审计 `truncated_text` 互证。`plan 0/13` 是 `temporal_plan` 事件表迁移期只读统计（观察项，不参与判定）。`char_layout[N].surface` 全稿缺失是 WARN 面偏宽（FrameSpec 回填未兜住），不阻塞。
>
> 根因定位：**缺陷的 curing 点放错了层**。`narration_segment` 是必保留内容（选材契约的锚），提交前修复**按设计禁止触碰**——闸门正确；但截断恰恰就在旁白本身，`truncated_text` 的唯一正确修复就是改它 ⇒ **凡是选材台词在生成层被拆镜截断的稿子，当前提交门必然 BLOCK 且无法修复**。上一轮 5/5 是运气，不是门。
>
> 本计划收口这一死锁：**把台词完整性从「提交门审计报告」挪到「候选层确定性硬门」**，破损稿在被选中进入提交门之前就被判不合格。闸门不放宽、不新增修复轮、不改判据容量；只把检验时机提前。

---

## 1. 目标与非目标

**做（本工作包内）：**
- 候选层新增**确定性台词完整性硬门**（不再依赖审计的 `truncated_text` 文案），破损候选直接判不合格，不进入 BEST-GUARD 排名；单候选破损时提交层可据同一判据给出明确 BLOCK 原因（`dialogue_truncated`，与 `locked_changed` 区分）。
- 保留并加固现有两道锁：`narration_segment` 仍为锁定字段（`_FINAL_LOCKED_FIELDS` / `_locked_repair_signature`），审计 `truncated_text` 仍为观测信号；两者的语义与 09-25 分流（`call_failed/patch_unusable/no_progress`）保持不变。
- 补齐判别性单测与最小探针（真实章节 + 真实计划 + 冻结含破损稿），证明破损被拦截、完整稿放行、闸门不误伤合规替换。

**不做（需另行决策）：**
- 不放宽 `narration_segment` 的锁定语义、不允许修复侧"部分应用截断补丁"。
- 不抬高 `REPAIR_MAX_OUTPUT_TOKENS`、不增加修复轮数/联动组容量、不改 BEST-GUARD 评分。
- 本次不处理 `char_layout.surface` 全稿缺失、`temporal_plan 0/13` 迁移、`13.7 服装色` 等 WARN 面偏宽问题——仅记录为观察项（见 §5）。
- 不靠提示词工程"让模型别截断"替代硬门。

---

## 2. 方案设计

### 2.1 判据定义（唯一事实源）

- **输入**：`plan.dialogue_lines`（核验通过的那一组，`plan_feasibility.check_plan_before_generation` 已收窄）、`script.shots[].narration_segment` 的**拼接**、原文 `novel_text`。
- **归一化**：复用 `plan_feasibility._source_norm`（省略号折叠 `…{1,}/.{2,}→§→删标点→还原…→删空白`，含 `_PUNCT_CHARS` 直角引号/书名号），**顺序不可换**（与 `validation._check_grounding` 08-17 修掉的 bug 同源）。
- **宽容项（与既有两处同源）**：
  - 说话人前缀：`张十三道：` 按 `re.split(r"[:：]", s, 1)[-1]` 再匹配一次（生成层规则0补的，不一定逐字来自原文）。
  - 合规替换还原：审计侧 `…急需` 截断与 `杀人→出人命` 同源映射——`_check_grounding` 对 `出人命` 还原为 `杀人` 再匹配，**此门同样还原后再匹配**，否则 `6/7` 会被误判为破损。
- **判定**：逐句检查 `normalized(line) in normalized(joined_narration)`；**任一句不命中 ⇒ 破损**。`joined_narration = "".join(narration_segment for shot in shots)`（含 hook？hook 按设计不含台词，不计）。
- **破损认定**：破损即整份候选不合格；不尝试"部分截断长度"等阈值（准则 7：宁可显式失败，不可静默伪造/降级）。

### 2.2 落点（唯一改动面）

```
plan_feasibility.check_plan_before_generation  (已在候选扇出前)
        │
        ▼
_generate_script_candidates()  ── fan-out: gen_spy_script → hard_fix → timecode → (新增) dialogue_integrity_check
        │                              │              │
        │                              │              └─ 破损 ⇒ candidate invalid, 不计分/不进 BEST-GUARD 池
        │                              │
        │                         BEST-GUARD 选稿（仅在池中选）
        │
        ▼
_commit_step01_outputs  ── 选中稿的二次校验（同判据，fail-closed），破损时 BLOCK 原因记 dialogue_truncated
```

- **候选层（主门）**：`step01_script_gen/main.py::_generate_script_candidates` 内，每个候选 `script` 在 `run_hard_fix` + `_compute_timecodes` 之后、进入 `_candidate_quality_summary` 之前，调用新函数 `check_script_dialogue_integrity(script, plan, novel_text)`。返回 `(ok, broken_list)`；`broken_list` 每项 `{line, reason}`。
  - `ok == False` ⇒ 该候选标记 `dialogue_complete=False`，`_candidate_sort_key` 将其排在最后（或直接从 BEST-GUARD 输入池剔除）；日志打 ` [CANDIDATE] dialogue integrity failed: 1/7 broken: 张十三…`（前 40 字 + reason）。
  - 若池中**全部**候选破损：不静默回退旧快照——BEST-GUARD 收到空池时直接返回"无可用候选"并让 `main()` 抛带 `broken_list` 的 `PlanInfeasibleError` 变体或显式 `RuntimeError("all candidates dialogue_truncated")`，写 `step01_plan_infeasible.json` 诊断（`conclusion=replan_incomplete` 同口径）。
- **提交层（辅门，防单候选绕过）**：`_commit_step01_outputs` 入口对选中稿复核同一判据；破损时 `summary["repair_outcome"]="dialogue_truncated"`，`detail` 写"台词完整性硬门：N/M 句破损（…），需在候选层重取"（不进修复轮）。

> 关键约束：**不继承 `RuntimeError` 的异常不用于此门**——候选层破损不是"生成失败→重试"语义，直接判池无效；提交层破损也不是修复可解的，不进 `except RuntimeError` 分支。

### 2.3 为什么放在候选层而不是"让修复能改 narration_segment"

- `narration_segment` 锁定是保护选材服从的闸门（v6.94，唯一绑定闸门）。放宽它会让修复按模型意愿重写必保留内容，语义再合理也不能放行（本次 patch 的"补全…急需公子爷赶回去处理"在孤立看是合理的，但**选材契约要求逐字服从**，必须由规划层/候选层保证完整性）。
- 审计的 `truncated_text` 是 LLM 产出的**观测**，不能作为硬门（采样噪声、文案不稳定）；确定性门必须基于代码归一化与原文证据。

### 2.4 与既有四份边界留档的衔接

- `step01-plan-feasibility-gate`（缺计划/不可行/空台词 fail-closed）与 `step01-plan-boundary-closure`（门在扇出前一次、共享 `verify_dialogue_lines`）**保持**。
- `step01-plan-reselection-and-cross-model-anchor`（同一计划版本只重选一次、压缩锚点只保高潮）**保持**——本门不触及规划重选。
- `step01-contract-positioning-and-replan-semantics`（契约块在最终组装处末尾追加、构造失败抛 `PlanInfeasibleError`）**保持**——本门读的是契约块所承载的那一组 `dialogue_lines`。
- 本工作包新增留档记为对上述四份的**增量**，不另起一份描述同一边界。

---

## 3. 关键文件与改动清单（外科手术式，≤ 5 文件）

| 文件 | 改动 | 依据 |
|---|---|---|
| `step01_script_gen/plan_feasibility.py` | 新增 `check_script_dialogue_integrity(script, plan, novel_text) -> (ok, broken)`：拼接 `narration_segment`、复用 `_source_norm` + 前缀宽容 + 合规还原，做逐句 `in` 判定；`dialogue_lines` 空时按 `evaluable=False` 同口径处理（破损视为不合格）。导出供候选层与提交层共用。 | 复用 `_source_norm`/`verify_dialogue_lines` 的已验证逻辑，不另抄一份标点表 |
| `step01_script_gen/main.py` | ① `_generate_script_candidates`：候选生成后调上式并过滤/标记；② `_commit_step01_outputs`：选中稿二次校验，破损时短路 BLOCK（`repair_outcome=dialogue_truncated`，不进修复事务）。两处均写结构化台账。 | 扇出前门已在 `plan_feasibility`，此门是**候选完整性**（不同维度） |
| `step01_script_gen/quality_issues.py` | **不改**（暂不新增 code），审计 `truncated_text` 保持 WARN/blocking 现状；若后续要把此门纳入 `RULE_SOURCE_FINGERPRINT`，指纹会自动覆盖（`quality_issues.py` 已在指纹输入内）。 | 审计是观测，硬门是代码判据，二者分离 |
| `tests/step01_script_gen/test_plan_feasibility.py` | 新增判别用例：合规替换还原后命中、前缀宽容、跨镜拼接命中、截断破损（`…急需`）、空 `dialogue_lines` 行为。 | 覆盖本次 6/7 与 1 破损的真实形态 |
| `tests/step01_script_gen/test_candidate_dialogue_integrity.py`（新）或并入既有 `test_candidate_parallel.py` | 候选层破损过滤：全破损池→空选失败（带 broken 明细）；1/2 破损时选中完整稿；提交层二次校验 BLOCK 原因与 `locked_changed` 区分。 | 证明"破损在候选层被拦截"而非"在提交门被锁" |
| `docs/step01-*` | 更新既有边界留档的"仍未解决"段，记录本次 1/7 破损形态与硬门落点；不新增独立文档（按 `CLAUDE.md` 留档按工作包）。 | 避免第 5 份计划边界文档 |

> `shared/config.py` / `.env.content` / `AGENTS.md` **不动**（按 `step01-contract-positioning` 的交付分组）。

---

## 4. 验证计划（与风险相称）

**单元（pytest -q）：**
- 上述 `test_plan_feasibility.py` 新增 5–7 条 + 新文件 3–5 条；全量 `pytest -q` 预期 **2365 → ~2375 passed**（基线为 09-26 02:08 的 2365）。
- 关键断言：`check_script_dialogue_integrity` 对 `draft_failed_20260925_184302` 的 1 句破损返回 `broken=[张十三长台词]`，对 09-25 22:10 那轮 5/5 完整稿返回 `ok=True`；候选层空池时异常文案含 broken 行。

**探针（只读，不落盘，不发模型）：**
- `.tmp/dialogue_integrity_probe.py`：读 `outline_20260925.json` 的 7 句 + `chapter001_16th/script_result.json` + 02:30 BLOCK 产生的新终稿快照（`candidate_pre_bestguard_*.json`），对三份稿跑新门，打印 `ok/broken` 与归一化前后对比，证明跨镜拼接与合规还原是否生效。

**实跑（需用户授权，不在本计划内执行）：**
- 下一次 `python step01_script_gen/main.py`（`TEXT_PROVIDER=agent_plan` / `deepseek-v4-flash` 单模型）观测：候选池 `plan 0/13` 仍为观察项，但 `dialogue_complete` 关；若某候选再次截断，日志应出现 `dialogue integrity failed` 而非 `locked_changed`，欠账台账 `detail` 含 `dialogue_truncated`。

---

## 5. 观察项（本次不改，仅记录）

- `char_layout[N].surface` 全稿缺失：FrameSpec 必填字段，`split_visuals.py` 的派生回填未兜住（待查 `frame_layouts → char_layout` 同步）。
- `temporal_plan 0/13`：事件表迁移期统计，只读、不参与判定与选稿（见 `step01-plan-reselection`）。
- 启动横幅`[INFO] 使用 doubao 专属 prompt (provider=agent_plan)` 陈旧文案（实际走 `resolve_model_prompt_profile` = deepseek 剖面，已核实 `main.py:272`）——与"生成分镜脚本"同类日志混淆问题，未改。
- 13.7 服装色未重述 / 镜头 1/4 kf 时间窗缺帧 → WARN 面偏宽，需后续按 v6.134/134.1 的"先拿字段名再定修法"处置。

---

## 6. 风险与回退

- **误拦风险**：合规还原或前缀宽容漏配会导致"完整台词被判破损"。缓解：归一化与 `_check_grounding` 同族 + `杀人→出人命` 还原已在 5/5 采样上验证；用例覆盖跨镜拼接与省略号折叠。
- **单候选全破损时整轮失败**：符合预期（不成片优于错片），诊断写 `dialogue_truncated` + broken 行，前端据此决定"重取候选 / 调 TOPK / 拆长台词"。
- **回退**：删 `check_script_dialogue_integrity` 及其两处调用、还原测试；`narration_segment` 锁定与 BEST-GUARD 行为回到 02:30 前。

---

## 7. 待你确认（2 个）

1. 候选池**全破损时是否直接失败**（推荐：是，宁可显式失败也不可让破损稿进提交门；替代：回退到"最少破损"的候选并标 `advisory`）。
2. 提交层二次校验的 BLOCK 是否同样写 `step01_plan_infeasible.json` 的 `conclusion`（推荐：`dialogue_incomplete`，与 `replan_incomplete/teardown` 分开，便于与"计划不可行"区分）。
---

## Codex 方案审核评论（2026-09-26）

### 审核结论

总体方向成立：把计划选用台词的完整性检查前移为确定性门，可以避免已知破损稿走到提交修复时触发 `locked_changed`；保持 `narration_segment` 锁定语义、不增加修复轮是合适边界。但原方案在以下事项收口前，不建议直接实施。

### 实施前需补齐

1. **历史快照也必须受门控。** 目前方案只在 `_generate_script_candidates` 检查新生成候选；`_cross_run_best_guard` 仍会把历史快照送入排名。提交层复核虽能阻止破损稿落盘，却不满足“破损稿在进入 BEST-GUARD 前排除”的目标。应明确历史快照也按本轮计划执行同一判据，再参与排名。
2. **合规替换还原尚无共享落点。** `plan_feasibility._source_norm` 当前只规范省略号、标点和空白；`杀人→出人命` 的还原在 `validation._check_grounding` 中单独处理。方案需指定可复用的规范化实现，并验证映射方向、冲突及替换后原词仍存在的情形，不能把“同源”当成已具备的实现。
3. **两次校验的时序需明确。** 候选检查要覆盖实际送入 BEST-GUARD 的最终新稿版本；提交复核应位于所有可能改动 `narration_segment` 的钩子处理、finalize 等步骤之后，并在最终提交判定前执行。需按当前真实调用链确认落点。
4. **定义跨镜匹配的边界。** 全稿拼接支持台词连续跨镜，但无分隔拼接可能让不同对白或镜头边界偶然拼成命中。明确允许的跨镜条件（顺序连续，不能跨越另一条对白），并为截断后重复前缀、相邻镜头偶然拼接分别增加反例。
5. **写清候选预算语义。** 说明破损候选是否占 TOPK 名额、是否补取候选、补取上限，以及并行早停时未完成候选的处理。否则“硬门”能否真正阻止破损稿进入候选池仍不确定。

### 两个决策点建议

1. **候选池全破损：直接失败。** 不回退到“最少破损”候选；失败保留 broken 明细，并按已有候选预算结束，不增加修复轮。
2. **诊断结论：独立标记 `dialogue_incomplete`。** 这是生成稿完整性问题，不是计划不可行。优先写入现有运行/提交诊断；若因兼容需要写入 `step01_plan_infeasible.json`，也必须用独立 `conclusion=dialogue_incomplete`，且消费者不得将其解释成 `replan_incomplete` 或触发计划重选。

上述决定建议作为方案约束采纳；历史快照门控、共享规范化、时序和预算语义补齐后，再进入实施。


---

## 落码后二审处置记录（2026-09-26，只读审核 → 已收口）

统一版计划见 `~/.claude/plans/sunny-kindling-wilkinson.md`（§2.2 预算语义已按本节定稿口径更新）。

- **C1 预算语义**：计划原文"不占 TOPK 名额补取至 TOPK"与实现注释"不补取"**都不准确**——实现实为
  「破损候选不占合格名额；差额在既有 `_max_attempts = SCRIPT_PARSE_RETRIES × TOPK` 预算内继续补取
  （与解析失败同一预算池）；预算用尽按实际合格数收池；全部破损才显式失败」。已按此统一：代码注释
  （`main._gen_one_candidate`）与统一版计划 §2.2 同步更正，**无行为变化**。
- **C2 历史快照门控**：按**本轮计划**同判据是既定口径（审核约束①）；计划版本切换后旧快照被批量
  排除属**必然行为**（不成片优于错片），运维指引：旧快照由 BEST-GUARD 排除日志
  `[BEST-GUARD] 台词完整性门排除 N 份历史快照` 可见，需要保留的旧稿应在其生成时的计划下重验。
- **H1 提交层无计划例外**：写入对外契约（见 feasibility-gate 留档增量）——`dialogue_lines` 为空的
  fail-closed 属**门函数**语义；提交层调用点对「无台词计划」（历史模式/旧缓存）不启用门，
  无计划生成在候选层上游已被计划门拦死。不得当成 bug"修复"回退。
- **H2 补测**：① BEST-GUARD 快照池 1 破损+1 完整 → 仅完整参与排名（`test_best_guard_excludes_broken_snapshot_and_keeps_complete_one`）；
  ② 拆分碎片终稿门拦截（`test_terminal_gate_intercepts_full_line_split_fragment`，09-26 三镜全句复刻）；
  ③ 还原双形态边界 3 例 + 逗号结尾跨镜判破损 1 例（M1/M2）。
- **M1 深挖出的真缺陷**：`NARRATION_SUBSTITUTIONS` 单字 safe（"春药"→"药"）会把"抓药去了"还原成
  "抓春药去了"——`_check_grounding` 08-17 起的潜伏缺陷（5 字滑窗掩盖）。门侧已用**双形态匹配**
  （未还原+已还原都试，`plan_feasibility._match_forms`）根治误拦；`_check_grounding` 的等价转调
  保持不动（5 字滑窗容忍该缺陷，修它属另一工作包）。
- **M2 边界固化**：逗号/顿号结尾的分句跨镜 = 破损（有意从严，用例固化）。
- **M3 监控侧约束**：`conclusion=dialogue_incomplete` 是生成稿完整性结论——监控/消费者不得把它
  解释成 `replan_incomplete` 或触发计划重选（与 `step01_plan_infeasible.json` 的其他 conclusion 互斥）。

### 三审处置记录（2026-09-26 落码审核第二轮 4 点，同日完成）

- **P1-1 门异常 fail-closed**：提交层门自身异常改为 BLOCK（`repair_outcome=dialogue_truncated`，
  detail 带 "fail-closed" 与异常原文，零审计调用）——采纳审核补充意见"硬门语义下应显式
  BLOCK+diagnosis 而非 warn 放行"。用例：`test_commit_gate_exception_fails_closed`。
- **P1-2 匹配方向重定（第三条路，非改词表、非词边界还原）**：审核补充"需改词表或改还原为
  词边界/最长匹配"——实取**净化方向**：计划句与稿件**同过 `sanitize_narration`**（orig→safe，
  产出稿件文本的那个确定性函数，幂等）再比对。还原方向对"原词与安全词同句"本质歧义
  （"拿春药，再去抓药。"），净化方向替换永不错位——审核原例回归钉住
  （`test_dialogue_integrity_chunyao_and_zhuayao_coexist`）。词表单字 safe 的设计缺陷仍在
  （影响 `_check_grounding` 的 5 字滑窗），修它属另一工作包，已留档。
- **P2-1 缓存入口**：代码注释与留档更正——"三入口计划均非空"不实；旧缓存无 `chapter_outline`
  时门不启用（兼容取舍），此类稿件不受台词门保护，已显式记录。
- **P2-2 混合失败**：按尝试构成分开计数（`N 次尝试中 X 次台词破损、Y 次生成/解析失败`），
  诊断带 `attempt_stats`；用例：`test_mixed_failures_counted_separately`。

`pytest -q` → **2387 passed, 1 skipped**。