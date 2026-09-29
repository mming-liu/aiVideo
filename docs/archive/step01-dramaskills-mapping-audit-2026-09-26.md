# step01 × drama-skills 分镜方法论映射审计（2026-09-26）

> 性质：**映射审计文档**（Phase 1 产出）。代码与产物只读；本文档是本批次唯一新增文件。
> 审核修订记录：按 2026-09-26 审核意见修正——①批 A「craft 永不 blocking」撤回为未论证提议（§1）；②外部来源固定版本与指纹（§0）；③缺口判定以可复核反例为准，两处改判（§3）；④原批 G 移出实施范围（§6）；⑤批 F 的授权边界补齐 02b 语义（§6）。
> 复核二修订（2026-09-26）：SHT-08 覆盖范围精确化——`split_boundary_replay` 为**条件触发**（前镜 lf_desc 非空、后镜 motion 段含可解析时间标签、相似度 ≥0.62、状态回退守卫未豁免），issue severity=**visual_risk**（QI:1118-1133 实读），「所有相邻镜对 blocking」表述作废；§6 的 B′/D′/E′ 证据改标**待验证假设**（缺口证据≠因果案例，修订前不作为批准依据）；SHT-15 由「确认为缺失」降为**未确认（显式约束未发现）**。

## 0. 证据基线

**外部来源（固定版本）**：`zenstory-ai/drama-skills`，commit `b71cb3ca9343eaf6c0375725ccc9261a4e79021e`（main，2026-09-24T01:48:40Z，MIT）。审计读取的文件与 sha256（前 16 位）：

| 文件 | sha256*16 | 尺寸 |
|---|---|---|
| skills/short-drama-storyboard/SKILL.md | f893fb81cb45f02a | 17622 |
| references/shot-craft.md | 19f33418eb236fc9 | 16549 |
| references/production-shot-grammar.md | c8723d9f628ea7b5 | 21127 |
| references/keyframe-craft.md | 02c095439f732118 | 10255 |
| references/stage-contract.md | afc5fc4dc60c49e3 | 10181 |
| references/blocking-playbooks.md | 3d52f1c8820be2ea | 12621 |
| references/coverage-audition.md | d2ccefd58df2c3ba | 2128 |
| references/lighting-craft.md | 62b7fe6bfb3a87fc | 2118 |
| references/scene-visual-plan.md | 404b7634d0947335 | 1826 |
| references/shot-revision-identity.md | 960ce518a96854f8 | 1732 |
| references/review-and-fixtures.md | 230026c548e9cb8a | 3326 |

规则总表以 `stage-contract.md` 的 SHT-01~27 + CON-01~07（含分级）为准。

**仓库侧基线**：当前**暂存中的工作树**（`main.py` / `rules_index.py` / `plan_feasibility.py` / `outline.py` / `validation.py` / `shared/content_safety.py` / 3 个新增测试），即候选层台词完整性门（v6.135，未提交、未编号）。本审计以工作树现状为准，**不假设其已合入**；后续批次的实施排期把这批暂存改动作为边界，而不是「等合入」。

**证据分级约定**：结论只用四档——已覆盖 / 部分覆盖 / 未确认 / 确认为缺失。引用格式 `文件:行号` 为本次实际核对；仅出自 `rules_index.py` 版本注释、未逐函数核对的，注明「（rules_index 级证据）」。

## 1. 门禁体系盘点（回应审核修正点 1）

仓库现有**三套互不同维度的分级**，逐一定性：

| 体系 | 取值 | 语义 | 证据 |
|---|---|---|---|
| Constraint.severity | `Severity.WARN / HARD` | **修复语义**：HARD 未修触发 regen（`constraints/base.py:59`，注释见 `base.py:52`） | 修复轮白名单、GAP 重生触发都消费它 |
| 质量检查 issue severity | `hard / visual_risk / blocking`（+advisory） | **验收分类**：哪些进提交门（`shared/quality_gate.py:412`） | `_candidate_quality_summary`、最终门 |
| 审计 issue 严重度 | 独立于上两者（audit.py 体系） | 审计/修复轮的 issue 分级 | v6.134 severity 门（rules_index 级证据） |

**结论**：drama-skills 的四级（structural_invariant / reviewed_invariant / craft_default / taste_option）是**规则的认识论类型**，与上面任何一套都不同维度。把 tier 塞进 Constraint 基类并立「craft 永不 blocking」为注册表不变量，会错误约束确定性结构门（如 `duration_budget`、`_split_boundary_issues` 这些 hard 门本身就有「craft 外观」的表述），且**该不变量未经论证**。原批 A 撤回直接立项；重排为 A′（§6）：只做盘点与提案文档，不改任何门语义。

## 2. 映射总表（SHT / CON 逐条）

判定依据缩写：QI=`quality_issues.py`，VAL=`validation.py`，DSM=`constraints/duplicate_shot_merge.py`，RI=`rules_index.py`，SYS=`prompts/system.md`。

| 规则 | 外部分级 | 仓库实现证据 | 结论 |
|---|---|---|---|
| SHT-01 原文落实/未拍带理由 | structural | core_keyword_coverage（v6.61，RI）；grounding source_quote 可定位（v6.81/82，RI）；gap_analysis.py | **部分覆盖**：词面覆盖率+引文定位有；动作级双向责任账本、显式省略理由记录无 |
| SHT-02 镜头目的 | reviewed | SYS 起止链/唯一动作（v6.94 拆分 prompt，RI）；无检查器 | **部分覆盖**（prompt 层） |
| SHT-03 单镜动作量 | craft | `_check_action_load`（v6.74，RI）；duration_budget；B2 容量自检 | **已覆盖** |
| SHT-04 景别/摄影动机 | craft | `_check_shot_size_jump` VAL:4268、`_check_focal_switch` VAL:3323、adjacent_duplicate_visuals QI:1203+DSM:99 | **部分覆盖**：跳跃/重复有；「工作景别」「情绪≠景别收紧」等语义无 |
| SHT-05 关键帧投影边界+绑定 | structural | frame_spec 单源 + frame_layouts 6 必填 + on_screen 门控（v6.57+，RI） | **已覆盖** |
| SHT-06 冻结瞬间静帧 | reviewed | ff/lf 结构性差异化（v6.51，RI）；状态词≠姿态词（后处理教训） | **部分覆盖**；「有序动词进 ff_desc」无专项检查（grep 零命中） |
| SHT-07 视觉方向 taste | taste | style_prefix 体系；无风格强制门 | **已覆盖/不适用** |
| SHT-08 动作落实唯一 | reviewed | `_split_boundary_issues`（QI:1069，code=split_boundary_replay，**severity=visual_risk**，QI:1124）——仅覆盖**满足触发条件**的相邻镜对：前镜 lf_desc 非空、后镜 motion 分段含可解析时间标签、相似度 ≥0.62（QI:1122）、状态回退守卫未豁免；`adjacent_duplicate_visuals` 亦为 visual_risk（QI:1204），判据同 DSM:99 | **部分覆盖**：相邻重演的「条件命中形态」有检测；条件外形态（motion 无时间分段、相似度低于阈值、前镜 lf 缺失）与**非相邻**重演均无检测 |
| SHT-09 观察视角匹配 | reviewed | prop_spatial_anchor、location 族（DSM:_same_location_family）；scenes_bank wide/near 派生 | **部分覆盖** |
| SHT-10 边界事实不借用 | reviewed | ff-lf 自抄差异化（v6.51）、lf 互斥 QC 根治（v6.56→v6.57，RI） | **部分覆盖**：相似度+互斥有；「ff 含终点事实」语义漂移无专项 |
| SHT-11 承受者反应 | craft | 仅拆分 prompt 局部（「不能切听者反应镜」v6.94，RI） | **部分覆盖**（prompt 局部，无通用规则） |
| SHT-12 观众可见性/揭示账本 | reviewed | grep 零命中（后半段/首次出现仅 climax 反馈 prompts.py:566，非揭示账本） | **确认为缺失** |
| SHT-13 多人关系投影 | reviewed | multi_person_staging（v6.74/86）、group_actor_continuity（v6.61）、redguo_scene_char_limit | **部分覆盖** |
| SHT-14 动态对象跨镜 | reviewed | weapon_state_consistency、kf_blood_monotonic（v6.82）、致命伤跨镜持续（v6.31，VAL） | **部分覆盖** |
| SHT-15 交付面遮挡区 | reviewed | grep「安全区/字幕区/遮挡区/避让」零命中——**未发现交付面安全区的显式约束**；渲染布局与成片样例未检查 | **未确认（显式约束未发现）** |
| SHT-16 单集时长账目 | structural | 60-90s 硬门（v6.74）+ write_timeline_from_durations + kf_time_out_of_shot/duration_two_calibers（v6.83） | **已覆盖**（严格于外部方案） |
| SHT-17 尾帧边界角色+插值意识 | structural | mid-KF 体系（kf_event_window、motion_segment_kf_align v6.102）；ff/lf 字段分工隐式存在 | **部分覆盖**：中段动作结构化承载有；「路径是戏的镜头不靠两端」选型规则、边界投影显式契约无 |
| SHT-18 场次视觉计划 | craft | — | 不适用（可选思考层，非检查对象） |
| SHT-19 coverage audition | reviewed | — | 不适用（人工导演比较层） |
| SHT-20 镜头修订身份 | reviewed | 另解：修复轮内禁重编号 + 快照按列表位置（v6.94，RI） | **已覆盖**（同问题不同解法） |
| SHT-21 关键帧正文纯净 | reviewed | prompt_sanitize、v6_43_prompt_enforcement（v6.61/84）；on_screen 门控 | **部分覆盖**；「资产名泄漏进正文」专项未确认 |
| SHT-22 视觉依据反查成稿 | structural | frame_layouts 必填 + derive_char_layout 回填 + grounding | **部分覆盖**（形态不同：无逐镜「一致性对象清单」字段） |
| SHT-23 REF/PLAN 参考图槽位 | structural | `_collect_r2v_references` + MODEL_REF_LIMITS + provider role fail-fast（AGENTS.md） | **已覆盖**（代码实现更强） |
| SHT-24 依据范围不过载 | structural | on_screen 门控 + 注入锚块单源 | **部分覆盖** |
| SHT-25 参考图用途词表+首尾配对 | structural | kf 参考图 per-frame 化（v6.70，tests/test_kf_refs_per_frame）+ provider 分配 | **已覆盖** |
| SHT-26 来源点名↔在列核对 | structural | grounding 引文可定位（v6.81） | **部分覆盖**；「帧内点名实体必须可在帧中辨认」未确认 |
| SHT-27 承受者反应 | reviewed | 同 SHT-11 | 同 SHT-11 |
| CON-01 相连边界匹配 | structural | char_side_continuity（v6.69 越轴根治）；_split_boundary_issues（相邻状态回退，**条件命中形态**，visual_risk） | **部分覆盖**：无「起止边界逐字段可比较」完整链检查 |
| CON-02 状态不瞬移 | reviewed | blood monotonic、weapon_state、state_updates 体系 | **已覆盖** |
| CON-03 增量记账 | structural | frame_spec 注入锚块增量 | **已覆盖** |
| CON-04 连续性变化五要素 | structural | — | **未确认** |
| CON-05 taste 蒙太奇 | taste | — | 不适用 |
| CON-06 下游文档影响 | structural | — | 不适用（无多文档形态） |
| CON-07 连续性锁逐字表面 | structural | surface_backfill evidence-gated（v6.75） | **部分覆盖**（09-26 run 实录该通道 WARN 面宽） |

**参考文件级条目**：

| 条目 | 仓库证据 | 结论 |
|---|---|---|
| 四级分级（stage-contract） | §1 盘点 | 确认缺失（且维度不同，另议） |
| 对白估时（shot-craft） | B2 自检 + duration_budget + KF 覆盖时长下限（v6.96） | 已覆盖（更刚性） |
| 竖屏纵深/景别收窄（blocking-playbooks §1） | SYS:5 竖屏定位；35-40 规则落地面未逐条核对 | 未确认 |
| 角度三栏分离（production-shot-grammar §5） | shot_size_jump/focal_switch 是**跳跃检测**非字段分离；camera_height 字段无 | 确认缺失（字段层；等 schema 演化需求） |
| 可渲染 12 项清单（keyframe-craft） | frame_spec QC 覆盖站位/朝向/视线/持物/位置；「与上一镜相同」承载句 grep 零命中；光受光结果写法无 | 部分覆盖；承载句+写紧经济缺失 |
| 写紧经济（keyframe-craft） | 无长度指引；1600 截断史（RI v6.68） | 确认缺失 |
| 轴线三行记法/裸左右语义（blocking-playbooks §1b） | axis_jump + char_side_continuity 存在；「裸左右=画面左右」全仓约定未确认 | 部分覆盖 |
| 「！」。标点叠加（production-shot-grammar 反模式5） | `_fix_trailing_dash_punct` VAL:7750 只补破折号裸尾、不剥叠加标点 | 确认缺失（**无触发案例**，按审核意见不立项，留观察） |
| SHT-15 交付面遮挡 | 零命中 | **未确认（显式约束未发现）**：渲染布局与成片样例未查，见 §2/§3 |

## 3. 五项候选缺口复核结论（两处改判）

1. **规则分级**：不是「缺失一条规则」，而是「认识论维度缺位」。且现有三套分级各自有明确消费方（§1）。→ **改判**：不立「craft 永不 blocking」不变量；先出盘点提案文档（A′）。
2. **SHT-12 揭示账本**：维持**确认为缺失**。climax 分布（prompts.py:566「后半段高潮覆盖」）是节奏问题，不是「观众何时知道什么」的信息账。prompt 层起步成立（B′）。
3. **SHT-08 动作落实唯一**：**改判为部分覆盖**（复核二修正覆盖范围）。检测面是**条件命中的相邻重演**：`split_boundary_replay` 仅在前镜 lf_desc 非空、后镜 motion 段含可解析时间标签、相似度 ≥0.62、状态回退守卫未豁免时触发（severity=visual_risk，阻塞类，QI:1118-1133）。**条件外的相邻形态**（motion 无时间分段、相似度不足、前镜 lf 缺失）与**非相邻重演**同样无检测——缺口不止非相邻一种；且回忆/插叙使部分形态的合法性不可静态判定。→ C 批维持撤出实施序列，转运行观察项（观察两类缺口是否出现真实案例）。
4. **SHT-17 首尾帧适用性**：维持**部分覆盖**。真缺口 = 「路径本身是戏的镜头不依赖两端插值」的生成选型规则 + ff/lf=边界投影的显式契约表述（D′，prompt 层）。
5. **可渲染 12 项**：**改判为部分覆盖**。真缺口收窄为两项 prompt 层习惯：「与上一镜相同」连续性承载句 + 写紧经济（E′）；其余 12 项由 frame_spec QC 覆盖或属未确认（随运行证据补）。

**审计副产品（未确认项登记）**：SHT-15 交付面遮挡——grep 证实**未发现交付面安全区的显式约束**，但不能据此断定所有相关几何检查都不存在（渲染布局与成片样例尚未检查），故记**未确认**。风险假设对本项目交付形态（抖音竖屏 + step04 烧录字幕）结构性成立：若关键证据文字/脸部落入字幕条区域，单帧 QC 可全绿而成片不可读。维持观察项——待成片 QC 或人工审片出现真实案例后按批次立项（`shared` 层或 step01 prompt 层均可承载，届时再定）。

## 4. 待对照清单的剩余项

竖屏纵深规则落地面（35-40 规则逐条比对 prompts/*.md）、CON-04 五要素、「裸左右」语义约定、SHT-21 资产名泄漏专项、SHT-26 点名实体可辨认性。这五项共同特点：**静态 grep 无法定性，需要运行证据或逐函数精读**；已登记，随下一轮重跑审计一并复核，不单独开工。

## 5. 缓存指纹的精确语义（回应审核边界点）

「revert 即回退且缓存自动失效」原表述**不精确**，实测证据如下：

- `RULE_SOURCE_FINGERPRINT`（QI:20-72）= 判定实现文件集的 sha256：quality_issues / state_updates / validation / timecode / split_visuals / audit / cross_field_checks / kf_structural_guard / rules_index / deepseek_schema + `constraints/`、`prompt_semantic/` 整目录 + shared 四件。它进缓存键（`hash_cache.py:49` `h.update(str(rule_version...)`）→ **改判定实现自动失效旧缓存与旧审计** ✓。
- `PROMPT_SOURCE_FINGERPRINT`（QI:75-101）= prompts.py / outline.py / main.py / prompts/ 目录，**是诊断字段，不参与门禁、不参与复用判定**（QI:76 docstring 明示）——prompt 改动的复用把关由 `script_revision` 承担。
- 指纹的保证是「旧报告不冒充新规则版本的有效证据」；**回退本身是手工 git 操作**，规则索引条目不是回滚机制。

后续批次的回退条件按此改写：判定层改动（constraints/validation）revert 后缓存自动失效可依赖；prompt 层改动（B′/D′/E′）回退后须确认 script_revision 语义（正常重跑即新稿，无旧报告复用问题）。

## 6. 重排后的批次提案（全部待用户批准，未获批准不动工）

| 批 | 内容 | 证据现状（缺口证据 ≠ 因果案例） | 判别性验收 | 回退条件 | 状态 |
|---|---|---|---|---|---|
| A′ | 分级盘点**提案文档**：逐条列现有 hard 门的判定可机械性，标注哪些属「craft 当 hard」候选降级、哪些属「W 该升」候选升级，附反例 | **真实误报史实**（已发生案例）：multi_person_staging 朝向豁免（v6.86）、weapon_holder 比喻/动宾（v6.85）；「分级机制能系统性预防」是提案主张，由文档逐条验证 | 文档内每条候选附可复现反例；不改任何代码 | 文档本身，无回退需求 | **未批准**（原批 A 的语义变更撤回） |
| B′ | SHT-12 揭示账本 prompt 层：观众可见性四问 + 相对时间词须换算绝对秒 | **待验证假设**：揭示账本缺位由映射确认（§2）；质量问题存在（47 分事故=结构性零高潮）属实，但「该问题源于揭示账本缺失、账本可改善它」**无因果证据**。进入试跑前需：运行观察/成片评审取得「揭示时机失控致可读性失败」的直接样例，**或**由用户明示接受假设风险 | 同章固定规则版本跑 M≥3 候选（预算可忽略），盲评维度：揭示时机可读性 / 扣住手段存在性 / 相对时间词残留数；且相对时间词在审计日志可观测。单章单候选不作为收益证据（v6.134 采样噪声教训） | revert prompts/**.md；无缓存依赖（§5） | 提案，**修订前不作为批准依据** |
| C′ | SHT-08 条件外相邻形态 + 非相邻重演 | — | — | — | **撤出实施序列**，转运行观察项（§3.3） |
| D′ | SHT-17 prompt 层：路径是戏的镜头不靠两端 + ff/lf=边界投影契约写入 frame_spec 文档 | **待验证假设**：v6.90 拆分重演案例证明**相邻重演缺陷**存在，不证明「中段路径动作依赖两端插值」曾造成失败；后者需成片证据。进入试跑前需取得直接样例或用户明示接受 | 判别案例=「动作在镜头中段、ff/lf 均拍不到」的镜头：改后 prompt 产出拆镜或动作落端新稿；step02a 帧图层 QC 无新增失败 | revert 对应 prompt/文档 | 提案，**修订前不作为批准依据** |
| E′ | 可渲染清单收窄后两项：「与上一镜相同」承载句 + 写紧经济（ff_desc ~800 字软指引） | **待验证假设**：1600 截断史证明**长度风险**存在；「承载句缺失已造成实际连续性失败」无样例。进入试跑前需取得直接样例或用户明示接受 | 对照跑一章：承载句出现率 >0；ff_desc 长度分布左移；相似度门（find_adjacent_duplicate_pairs）无非预期恶化 | revert prompt | 提案，**修订前不作为批准依据** |
| F′ | lumenx 三视图肖像试点 | — | — | — | **未批准**。授权边界修正：单角色试点若含 step02a→**step02b 出片对比，须按仓库规则在调用前单独列明镜头数/模型/时长/预估消耗并等待明确人工确认**，02a 图片费用确认不覆盖 02b |
| G′ | （原批 G） | 标点/省略理由/反模式附录 | — | — | **移除**，转入 §2 映射记录；任一项出现真实触发案例时按标准单独立项 |

**运行观察项清单**（不改代码，只进审计视野）：①SHT-15 交付面遮挡成片案例；②SHT-08 两类缺口案例——条件外相邻形态（motion 无时间分段、相似度不足阈值、前镜 lf 缺失的相邻重演）**及**非相邻重演；③待对照清单五项（§4）；④「！。」标点实例。

## 8. 决策更新（2026-09-26，用户批准，本节优先于 §6 状态列）

- **A′ + 待对照五项**：批准一次交付 → 已交付 `docs/step01-grading-inventory-2026-09-26.md`（约束全量 91 类/质量门 35 code 盘点、两跳映射实证、降级候选 3 项均保持现级+观察、升级候选无、五项结论：②已覆盖/①④⑤部分覆盖/③缺失观察）。
- **B′/D′/E′**：批准**合并一轮对照试跑**（用户选择即明示接受假设风险）；前置 = v6.135 提交（rules_index 条目需触暂存文件）；试跑产物落**诊断目录 run_id 隔离**；执行方案见 grading-inventory §3。
- **F′**：批准**连 02b 对比一起规划**；02a 执行前报用量确认、02b 执行前按仓库规则逐项确认；规划见 grading-inventory §4。

## 7. 结论

五项候选缺口经证据复核：1 项改判为部分覆盖并撤出序列（SHT-08；复核二精确化：仅**条件命中形态**有检测——前镜 lf_desc 非空、motion 段含可解析时间标签、相似度 ≥0.62、守卫未豁免，severity=visual_risk——缺口含条件外相邻形态与非相邻两类）、1 项撤回直接立项（分级）、1 项维持缺失（SHT-12）、1 项收窄（可渲染清单）、1 项维持（SHT-17）。SHT-15 记**未确认**（显式约束未发现，待渲染布局与成片样例）。待批准实施面为 B′/D′/E′ 三个 prompt 层批次 + A′ 只读提案文档；**其中 B′/D′/E′ 的证据现状为待验证假设——缺口证据成立、因果案例未建立，修订前不作为批准依据**：进入试跑前需在运行观察中取得直接样例，或由用户明示接受假设风险。F′ 维持未批准且授权边界已补齐 02b 语义。
