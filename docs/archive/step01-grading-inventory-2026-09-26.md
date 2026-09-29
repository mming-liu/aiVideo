# step01 A′ 分级盘点 + 待对照五项精读 + 已批批次执行规划（2026-09-26）

> 性质：**只读交付文档**（本日唯一新增文件）。前置：`docs/step01-dramaskills-mapping-audit-2026-09-26.md`（下称「审计文档」）。
> 证据格式同审计文档：`文件:行号` 为实读；仅出自 rules_index 注释的标明「（RI 级证据）」。

## 0. 决策记录（2026-09-26，用户批准）

| 项 | 决策 |
|---|---|
| A′ + 待对照五项 | 批准，一次交付（=本文档 §1、§2） |
| B′/D′/E′ | **合并一轮对照试跑**（选择即明示接受假设风险）；实施排在 v6.135 提交之后 |
| 试跑产物 | 诊断目录，run_id 隔离；正式 chapter 产物不动 |
| F′ | 连 02b 对比一起规划（§4）；02a 执行前报用量确认，02b 执行前按仓库规则逐项确认 |

## 1. A′ 分级盘点提案

### 1.1 判定面清单（全量）

**约束层**（regex 提取 91 类，含 3 基类；提取口径注记：`constraints/framing_jump.py` 存在但类签名未匹配本提取式，计入口径外）：

- **HARD 12**：duplicate_shot_merge*、duration_budget*、emotion_visualization_repair、frame_spec_consistency、identity_unification、kf_timeline_normalize、motion_time_integrity、offscreen_action_consistency、redguo_scene_char_limit、runtime_extend*、short_shot_merge、weapon_state_consistency（\*=skip_hook，report-only）
- **WARN 76** / **INFO 3**（climax_completeness、opening_empty_shot_remove、self_review）/ inherit 1（v6_43_prompt_enforcement，基类默认 WARN）
- skip_hook=True（report-only，不写入）共 **23** 个

**质量门 issue code**（quality_issues.py 提取，35 个）：hard **11**（CHECK_FAILED、duration_two_calibers、kf_at_shot_start、kf_person_missing_in_layout、kf_time_out_of_shot、semantic_audit_write、shot_content_over_cap、shot_duration_over_cap、time_field_audit_write、timecode_span_mismatch、timeline_consistency）、visual_risk **19**、advisory 2（opening_empty_shot_overload、structural_write_sanctioned）、unverified 1（eye_state_conflict_unverified）。

### 1.2 结构事实：severity 是两跳映射（复核一结论的实证补强）

Constraint.severity（修复语义，`base.py:59`）→ violation → gate code severity（验收语义）是**两跳**：HARD 约束可产出 visual_risk（如 short_shot_merge=HARD 报告，`short_shot_merge.py:42` docstring 明示「HARD 报告（不改写时长）」），gate code 才决定阻塞。⇒ 四级 tier 若引入，只能作为**独立元数据（rule_tier）**挂在注册表旁：不参与 severity 计算，仅供 ①分级盘点检索、②注册期 lint（新增约束声明 craft/taste 时禁止配套 blocking code）。**这是 A′ 的核心提案，待批**；在 lint 落地前，分级靠本清单人工维持。

### 1.3 降级候选（有证据）

| 约束 | 判定机制（实读） | 提案 |
|---|---|---|
| short_shot_merge | 时长阈值机械判定，apply 只报告不改写（`short_shot_merge.py:48-79`；v6.79 起停写入） | **保留 HARD**。曾疑「等级与行为不同步」，实读证伪——报告型 HARD 语义正确（修法=合并/补内容走完整 regen） |
| emotion_visualization_repair | 抽象情绪词→可视化**改写**，语义决策最强；v6.75 起 evidence-gated（不确定→保留原文报告） | 保持现级 + 登记观察：出现误报/争议即触发降级评估 |
| weapon_state_consistency | 词表 `_HOLD` + 时间窗矛盾消解（`weapon_state_consistency.py:11-13`），机械度较高；同族 `_check_weapon_holder` 有比喻/动宾误报史（v6.85） | 保持现级 + 登记观察（同上） |

### 1.4 升级候选

**本轮无**。v6.102 起审计判据为收紧方向（audit「新解决」判据、零改善回退），无「WARN 该升 blocking」的证据案例；宁缺勿滥。

### 1.5 未评估余量

76 个 WARN 的逐条判定机制精读未做（本轮精读覆盖 HARD 12 + 报告型抽验）。后续按「出现误报或阻塞争议的先精读」滚动推进，不为盘点而盘点。

## 2. 待对照五项精读结论

| # | 项 | 证据 | 结论 |
|---|---|---|---|
| ① | 竖屏纵深落地面 | 景别词表+时长引导在 `prompts.py:252/292/406`（「特写 2-4s/中景 3-6s/远景 5-8s」）+ framing_jump / framing_prefix_fill / framing_variety 约束；**纵深调度指引（前/中/后景分层、遮挡构图）在 prompts.py、main.md、system.md 全部零命中** | **部分覆盖**：景别已覆盖；纵深指引缺失（craft 级 prompt 缺口，登记观察；不并入本轮试跑，避免归因混淆） |
| ② | 裸左右语义 | `char_side_continuity.py:30` 用**显式词表** `画左|画右`（_SIDE_RE）+ HARD 交换守卫（:44 _swap_sides） | **已覆盖（另一解法）**：词表强制优于外部方案的语义约定（更机械、可判）；裸写「左/右」歧义残留但生成端被引导用词表，降级为观察 |
| ③ | 资产名/路径泄漏进正文 | prompt_sanitize 与 v6_43_prompt_enforcement 无文件名/扩展名模式；identity_unification 管「别名/群演占位」是相邻但不同的缺陷 | **缺失**（无触发案例 → 观察项） |
| ④ | 点名实体可辨认（SHT-26） | `shared/frame_spec.py:29` is_on_screen 单一事实源（缺省 True 保守）+ 章级开关 `shared/image_qc.py:1645` QC_DESC_CLAIMS（desc claims 核查分发） | **部分覆盖**：机制在，覆盖度随运行证据 |
| ⑤ | CON-04 连续性变化五要素 | `state_updates.py:140-160` extract_states/diff_states/merge_states 承载 **before/after + actor + 镜内文本区间**（3/5）；cause/source 无状态层联动（grounding source_quote 是引文定位、不接状态机）✗；跨镜 effective range 无显式字段 ✗ | **部分覆盖（3/5）**；缺口登记，等触发案例再立项 |

## 3. B′/D′/E′ 合并对照试跑执行方案（已批；前置 = v6.135 提交）

- **设计**：同一试跑章两臂——baseline（当前 prompt）/ treated（三批合并改动），每臂 M=3 候选。试跑章定 chapter_002 同章重跑（step01 预算可忽略）。
- **边界**：产物落诊断目录（run_id 隔离），不写 chapter 正式 `script_result.json`；报告按 AGENTS.md 分层注明 run_id/revision，与正式产物分开陈述。
- **改动面**：`prompts/system.md`（B′ 两节 + D′ 一条 + E′ 写紧指引）、`shared/frame_spec.py` 文档段（D′ ff/lf=边界投影契约）、`rules_index.py` 版本条目（需触该文件 → 前置 v6.135 提交的原因）。
- **每批独立判据**：
  - B′：相对时间词残留计数（机器）+ 揭示时机可读性盲评（我先评、用户抽验终评）
  - D′：「动作在镜头中段且 ff/lf 均拍不到」镜头的动作落端/拆镜率（机器可数）
  - E′：「与上一镜相同」承载句出现率 + ff_desc 长度分布左移（机器）
- **守卫**：adjacent_duplicate_visuals、shot_content_over_cap 等现有门无非预期恶化；PROMPT_SOURCE_FINGERPRINT 为诊断字段、不阻塞复用（审计文档 §5）。
- **回退**：revert prompts 与文档段；rules_index 条目随批次 revert；无缓存依赖。

## 4. F′ 三视图 + 02b 对比规划（已批规划；执行前另确认）

- **对象**：主角级 1 名（按试跑稿出镜角色定）；`characters_bank` 肖像 → 三视图（正/侧/背 + 体态锚），走 `asset_gen` 适配器。
- **A/B 设计**：同一冻结分镜稿，step02a 帧生成两臂——对照=现单肖像、实验=三视图；变量仅参考图集。帧图层验收：QC 通过率 + 跨镜一致性人工评分。
- **02b 对比臂（规划）**：镜头数=章分镜数（约 16）、模型=届时 VIDEO_REFERENCE_MODE 对应模型、时长=Σduration_sec（约 75s）——**执行前按仓库规则单独列明以上参数与预计消耗，等待明确人工确认**；02a 图片用量（三视图 3 张/角色 + 两臂帧生成差量）同样先报后跑。
- **时序**：排在 §3 试跑之后（一次只动一层）；r2v 模式恢复后补 step02b r2v 消费臂。

## 5. 下一步

1. 等 v6.135 提交 → 解锁 §3 实施（唯一阻塞）。
2. §3 试跑报告出来后 → 决定 B′/D′/E′ 是否转正、纵深指引（§2①）是否并入下一批。
3. §4 报价确认 → F′ 执行。
