# 问题记录（Problems Log）

> 记录流水线开发中遇到过的真实问题（含已解决与开放中），避免重复踩坑、重复调查。
> 每个问题记录：现象 → 根因 → 处理/状态。与 `todolist.md`（仅当前待办）互补。
> 最近更新：2026-08-20（v6.35 高潮优先原则 + v6.37 修复轮结构化）

---

## 🔴 开放中（未解决/未收敛）

### P26. 镜头预算未优先分配给高潮（08-20 决策 → 当天 v6.35 落地）
- **现象**：60s 短剧应该「事件密度 × 视觉张力」高，但 step01 现有规则 3「内容主次判断」只说「主要内容必拍 2-4 镜/段，背景内容合并为 1 个空镜」——**留了「快铺垫 1 镜」的口子**，LLM 顺势给「身世伏笔/环境暗示/田埂老汉」这些低视觉张力内容分配镜头。实测 chapter_001（08-19 C 模式产物）：有 2 镜纯风景（夕阳荷塘/田埂牵牛）浪费镜头预算，挤压强冲突/关键情感戏
- **根因**：①「合并为 1 个空镜」是个**软口子**——LLM 把"环境铺垫"理解为可拍内容；②规则 4「全弧线覆盖」要求覆盖原文前/中/后三段，**让背景镜头有合法位置**（中段/后段允许有「过渡镜」）；③没有针对「身世/履历/环境/日常」等具体类别的硬约束
- **解决（08-20，v6.35 源头 + v6.37 校验双层）**：
  1. **源头收紧 prompt 规则 3 + 4 + 新增 3.7**（`step01_script_gen/prompts.py`）：
     - 规则 3.7 新增「**高潮优先原则**」：60s 短剧 = 事件密度 × 视觉张力，所有镜头预算优先分配给高潮内容。**强冲突（必拍 ≥2 镜/段）** / **关键情感戏（可拍 1-3 镜/段）** / **快速铺垫（限 1-2 镜，仅开篇 ≤3s）** / **背景/过渡/日常（0 镜）**；镜头预算公式 = 铺垫 ≤2 镜 + 强冲突/关键情感戏 N 镜 + 收尾 0-1 镜
     - 规则 3 收紧：背景内容（身世/履历/环境/季节/日常）**禁止分配任何镜头**，信息由旁白（narration_segment）一句带过
     - 规则 4 收紧：核心镜头必须取高潮段，1-2 镜快铺垫只允许在开篇，**背景/过渡/日常一律 0 镜**
  2. **校验层新增** `_check_climax_priority`（`step01_script_gen/validation.py`，warning-only）：
     - 背景关键词（24 词：身世/履历/家境/殷实/媒人/田埂/老汉/牵牛/季节/晨光/暮色/炊烟/闲坐/品茶/寒暄/沏茶…）+ 强冲突（30+ 词：刺杀/追杀/追击/拔剑/中刀/格挡/跃出…）+ 关键情感戏（25+ 词：诀别/告别/拥抱/哭泣/释怀/立誓/思念…）
     - 命中背景词 + 未命中冲突/情感词 → 警告「非高潮镜头，应改 0 镜」
     - 开篇 idx 1-2 且 duration ≤3s 豁免（合法快铺垫）
     - 全片非高潮 > 2 镜 → 加总览警告
     - 注册到 `results["climax_priority"]` + `_REGEN_FEEDBACK_LABELS` 进修复轮反馈链
- **用户决策（08-20）**：保留 1-2 镜快铺垫 + 强冲突/关键情感戏；背景/过渡/日常 = 0 镜；仅改 prompt 措辞（不强制 LLM 删镜头）
- **预期效果**：镜头数会减少（去掉背景镜），但总时长不一定降低（每个镜头可拉长）。重点是镜头预算从「全章平均」转向「高潮密集」
- **待验证**：下一轮 step01 regen 观察镜头分布是否集中到高潮段；旧 script_result.json（08-20 之前生成）需重跑才能体现新规则

### P24. 硬修复层 + 锚点校验器 3 处 bug + 通用化重构（08-17 解决）
- **现象**：step01.log 跑完后 script_result.json 带病保存——致命伤锚点在 9 镜消失（shot12/14/17/18/20/21/23 心口洇痕全无），视线方向违规 5+ 处（"望着她"/"看杨旭"等无空间方位），道具空间方位缺失 5+ 处，锚点校验器 3 个误报（shot11/14/19 喊词台词被判"未找到原文"）。
- **根因**：①LLM 写"致命伤持续/视线方向/道具方位"是硬伤，规则 8/41/50 写到 prompts.py 已 30+ 条 LLM 仍不遵守——**规则数量与 LLM 实际遵守率成反比**；②校验器 `_check_grounding` 把"…"从 narration 删了再做 5 字子串匹配，删后 narration 与原文失配（"啊…啊…救命啊…" 折成 "啊啊救命啊"，原文是 "啊……啊……救命啊……" 折成 "啊啊……救命啊"），shot11/18 喊词 LLM 实际引用了原文却被报"未找到"；③`_check_grounding` 不做合规词对还原（"杀人→出人命"），shot14 narration 改写后查不到原文；④致命伤硬修复写死"小舟/杨旭"，换小说就失效。
- **解决（08-17，v6.32，新增 `hard_fix_shot_prompts` 函数）——代码层确定性兜底**：
  1. **致命伤持续**（`_hard_fix_injury_continuity`）：扫所有 shot 找第一个含 anchor 词的镜头作为 build_idx，自动取 `build_loc` 和 `main_subject` → 之后同 location 且仍含"被打者"角色名的镜头缺锚则追加"伤处白袍暗红洇痕持续可见"；anchor 词扩到 16 个（心口/洇痕/暗红/中刀/白袍/胸口/心窝/腹部/中剑/中枪/血痕/刀伤/溅血…），location/角色不写死
  2. **视线方向**（`_hard_fix_gaze_direction`）：扫所有 shot+hook 的 4 字段（prompt/ff_desc/lf_desc/motion_desc），"看/盯/望+人名"无空间方位则改写为"目光紧盯人名"；幂等——字段已含"目光/凝望/仰望/俯视"则跳过（08-17 调试：Python `re` 的 `(?<!目光)` 多字符负向预查是定长回溯"目光紧盯"中"盯"前 2 字符是"紧光"≠"目光"，会反复累积"目光紧盯"+"目光紧盯"…）
  3. **道具空间方位**（`_hard_fix_prop_position`）：14 类常见道具（炭炉/酒壶/酒杯/钓杆/油纸伞/步摇/灯盏/灯笼/叉子粪铲/农具/竹箫/马车/舴艋舟/刺客匕首）出现但 8 字内无空间方位则追加"（画X/船头/甲板/发间/岸边）"
  4. **kf 时序矛盾**（`_hard_fix_kf_pre_post_conflict`，原 `_hard_fix_hook_kf_timing` 通用化）：扫所有 shot+hook 的 keyframes，pre-state（半身探出/破水/举刀/出剑/跃起/冲撞）+ 角色已躺血 → 矛盾，删 pre 词保留 post，t=0.5；纯 pre 但 t>0.33 压到 0.2；纯 post 但 t<0.67 拉到 0.8；pre/post 关键词扩到 13+8 类（破门/跃起/出剑/扑出/倒地/坠落/被擒…），任何打斗/追逐/位移场景都适用
  5. **锚点校验器 3 处 bug**：
     - 省略号折叠顺序：先折叠为占位符 `§` → 删标点 → 还原 `§` 为 `…`（旧顺序先删标点含 `…` → 折叠失效）
     - 原文同步折叠：novel_text 也做 `…{1,}` 折叠（LLM `…` vs 原文 `……`）
     - 合规自动替换容忍：narration 里的 `出人命` 还原为 `杀人` 再匹配原文（`_check_grounding` 不破坏 narration 字段本身）
- **接入位置**：`validation.parse_response` 在 `post_fix_shot_prompts` 之后、`_validate_script` 之前调用 `hard_fix_shot_prompts(script)`，让写入 script_result.json 的就是干净版
- **效果**（chapter_001 实测）：致命伤 9 镜缺 → 0 镜缺；视线 5+ 违规 → 0 违规；道具 5+ 违规 → 0 违规；锚点误报 3 个 → 0 个；钩子 kf t=0.4 矛盾帧 → t=0.5 stab 帧清理完成；**幂等**：连续 3 次跑 hard_fix = 第 1 次有改动 + 第 2/3 次 0 改动
- **通用性**：所有 4 个硬修复**对任何古风/权谋/刺杀/追逐镜头都适用**——不再依赖"小舟"或"杨旭"，下一部小说主角"林平之"、场景"镖局"代码不用动
- **教训**：**LLM 写规则已 30+ 条仍不遵守 = 移到代码层确定性兜底**。校验器自己也是 bug 来源（"…" 折叠顺序错误让 3 个原本引用原文的喊词被判误报），改完必须对真实 script_result.json 回归；幂等是硬修复层的硬要求（避免反复跑累积插入"目光紧盯"+"目光紧盯"…）
- **记忆**：`memory/hard-fix-shott-prompt-2026-08-17.md`（v6.32 集成位置/4 类硬修复/锚点校验 3 处 bug/通用化前后差异）

### P25. 关键帧结构性塌缩 — kfs 全堆 post 段、缺失 pre 帧（08-18 新发现 → 当天 v6.33 修复）
- **现象**：钩子 6.04s 视频，motion_desc 明确分两段「**前2秒**：画左水面爆开，神秘人破水而出，右手紧握匕首，刀尖朝下垂直下刺，直入杨旭心口 / **2-4秒**：杨旭闷哼一声，身体后仰，神秘人推臂一送，双腿一弹，倒纵入水，水花四溅」——但 script_result.json 只生成了 2 个 kf，**都堆在 post 段**：
  - kf0 (t=0.5→3.02s) 写"杨旭仰卧+心口洇痕+听香跪坐"= post 死亡画面（应该是「破水+下刺」pre）
  - kf1 (t=0.8→4.83s) 写"杨旭后仰+刺客已没入"= 终态（正确）
- **根因**：①LLM 不知道 `time_ratio × duration_sec = 物理时刻`——把 t=0.5 当成"模糊中段"乱写；②post_fix + hard_fix 都**无法"无中生有"**——只能删/调，不能派生新 kf；③08-16 v6.31 的 `_check_kf_temporal_consistency` 只检"单 frame pre+post 矛盾"，**不检"完全没有 pre 帧"这一类结构性塌缩**——校验覆盖盲区
- **解决（08-18，v6.33，源头+校验双层）**：
  1. **校验层新增** `_check_kf_time_window_alignment`（`step01_script_gen/validation.py`，warning-only，3 类检测）：
     - **时段覆盖率**：motion_desc 显式分 N 段 → kfs 个数 < N 时 WARN（缺段）
     - **时段归属**：每 kf 物理时刻 t×duration_sec 不在任一时段内 → WARN（错窗）
     - **时段单调性**：kfs 的 time_ratio 升序对应的时段序号不单调 → WARN（倒装）
     - 辅助 `_parse_motion_time_segments`：解析「前N秒」「M-N秒」（含全角 `～` `－`），显式段未覆盖到 duration 末尾时**自动补 implicit 段**（last_end, duration_sec）——避免漏报"4-duration_sec 末尾段" kf
  2. **源头规则 16.4**（`step01_script_gen/prompts.py`）：**「关键帧物理时刻 = motion_desc 时间窗铁律」**——motion_desc 显式分时段时，kf 物理时刻必须落对应窗内；kf 个数 ≥ 时段数；写 kf 前先**列 motion_desc 时段表**（时段序号+起止秒+关键词），按表逐段生成 1 帧定格，`time_ratio = 时段中点 / duration_sec`
- **效果**（chapter_001 钩子实测）：`_check_kf_time_window_alignment` 报"motion_desc 分 3 段（0-2秒、2-4秒、4-6.04秒）但只有 2 个 kf，缺段"——精确指出 0-2秒 和 2-4秒 段无 kf
- **不做派生 kf 硬修复**：超出确定性兜底范围（需要语义理解从 motion_desc 切出 kf desc），应触发 LLM regen 而非代码层硬填
- **教训**：**08-16 v6.31 校验只覆盖"矛盾"，不覆盖"塌缩"**——两种缺陷看起来像一类（都"kfs 不对"），但根因不同，必须分别有检查。`time_ratio × duration_sec = 物理时刻` 这一基本物理事实，**对 LLM 是隐藏的**——必须显式写规则
- **记忆**：`memory/kf-time-window-alignment-2026-08.md`（v6.33 集成位置/3 类检测/源头规则 16.4/与 08-16 姐妹规则的关系）

---

## 🔴 开放中（未解决/未收敛）—— 旧条目保留

### P23. 关键帧时序一致性 — 单 frame pre+post 状态混搭（08-16 新发现 → 当天 v6.31 三层修复）
- **现象**：钩子 kf0.4 prompt 同时写「刺客半身探出（pre-state，刺客刚出水面）」+「杨旭仰卧+心口暗红洇痕（post-state，已躺下+已流血）」。图片模型渲染后：刺客方向错乱+刀位置错+红血时机错+与首帧不一致（FF 写盘坐吹箫+无血，kf 跳到躺下+有血）。
- **根因**：LLM 在 step01 一次性堆叠所有动作结果到单 kf，**没有时间线分镜概念**。单 frame 同时表达 pre-state（刺客潜伏/刚出）+ post-state（杨旭躺下+血）= 时序荒谬。图片模型对矛盾状态无解。
- **同类问题**：「刺客刚破水（无血）+ 听香跪坐捂嘴+杨旭瞪眼+刺客已刺入+血」这种「一帧打完 4 个时序状态」常见于 step01 重生成。
- **解决（08-16，v6.31/v6.32）三层防御**：
  1. **step01 prompt 新增 5 条规则**（`prompts.py`）：
     - 规则 5.4 人物形象禁止动物特征修饰（鹿系/猫系/兽耳/犄角 → 具体面部特征）
     - 规则 5.5 禁止精确空间约束（指甲盖/竖直/正对 → 模糊词或 doodle 兜底）
     - 规则 13.8 威胁源非实体提及不注入完整角色（水下黑影/刀尖微露 → 不在 char_layout 标位置）
     - 规则 13.9 武器归属逻辑不矛盾（禁止双重否定「X 不持 Y」+「武器只能 X 持」）
     - 规则 16.1 关键帧时序一致性铁律（3 段式 kf 模板：pre-stab / stab / post-stab）
     - 规则 16.2 关键帧是单帧静态瞬间不是动作链（同瞬间判定：所有动作必须物理上能同时发生）
  2. **validation 检查 `_check_kf_temporal_consistency`**（`validation.py`）：warning-only，三项检查：单 frame pre/post 冲突（刺客 pre-state + 杨旭 post-state=真矛盾）/ kf[0..N] 状态单调性（血不能回退、刺客不能「没入→刚出」倒退）/ FF→kf1 连续性（盘坐→仰卧缺过渡）
  3. **doodle 区域编辑已扩展到 KF**（v6.30，`vimax_bridge.py` `_gen_keyframes_async`）：QC 通过后检测空间精确词 → 调 `_doodle_refine_first_frame` 兜底，失败保留原图
- **本次修复**（`script_result.json` shot 0 重写为刺客 story）：
  - ff_desc: 杨旭盘坐吹箫+水下黑影伏笔（pre-state）
  - kf0 (t=0.3): 刺客只露刀尖+头肩水下，杨旭仍盘坐未察觉
  - kf1 (t=0.6): 刺客半身破水刀尖刺入心口，此刻杨旭胸口初现暗红洇痕（stab 瞬间）
  - kf2 (t=0.85): 刺客踞蹲船舷匕首已刺入，杨旭仰倒洇痕扩大
  - lf_desc: 刺客已没入水中，杨旭仰倒心口洇痕+听香惊恐（post-state）
  - char_layout：杨旭在画右船头（用户建议，刺客画左下手方便）
- **教训**：**单 frame 表达多时序状态 = 必错的视觉**。图片模型只能画单一瞬间，连续 3 帧递进才能表达完整动作。源头（prompt）必须按时序分镜；doodle 兜底修不了时序问题。
- **记忆**：`memory/kf-temporal-consistency-2026-08.md`（v6.31 集成位置/3 段式模板/校验规则/修法）

### P22. Seedream 精确空间约束无解（08-16 新发现 → 当天 doodle fallback 集成）
- **现象**：hook 首帧。prompt 写「一截刀尖微露水面，刀身/护手/刀把必须完全没入水下」「刀身与水面垂直」「刀尖正对杨旭心口方向」等**精确空间约束**——模型反复画出完整匕首（漂浮/横躺/与水面平行/刀把露出水面）。本轮调 7+ 次仍非确定。
- **根因**：Seedream 全档（`shared/model_caps.py` SEEDREAM_CAPS）不支持 `negative_prompt` / `seed` → 纯文本 prompt 无法可靠约束「指甲盖大小/竖直/正对某人」类像素级空间——模型非确定性是底层属性。P3「锚点系统是上限放大器，不是模型能力替代品」同一根因的具体表现。
- **尝试方案**：
  - ❌ 改 prompt 措辞（指甲盖/竖直/正对方向/禁止平行悬浮横躺）— 7+ 轮迭代，模型反复回到「完整匕首漂浮」状态
  - ❌ 试 seed=42 — HTTP 200 但**非确定性**（同 seed 不同 MD5 哈希）
  - ❌ 改 IMAGE_PROVIDER 切 bailian/qwen-image-3.0 — 留 Plan C，待 r2v 模式恢复后
  - ✅ **官方 doodle/bbox 区域编辑**（Seedream 4.0-4.5 i2i + 视觉标注）—— 验证有效：base 上画红框 → i2i 改写该区域 → 红框外严格保持原图。模型**优先**处理红框区域。
- **解决（08-16，v6.30）**：`shared/vimax_bridge.py` 集成 doodle 自动 fallback——
  - 新增 `_SPATIAL_FALLBACK_DEFAULTS` (per-phrase: bbox_ratio + refined_prompt)
  - `_detect_spatial_fallback(ff_desc_full, shot)` 检测是否需 doodle
  - `_doodle_refine_first_frame()` 画红框 + i2i 调用
  - `_gen_first_frame_async` 在 QC critical 之后、return 之前插入 doodle 调用
  - 失败兜底：doodle 异常/i2i 失败 → 保留原图（graceful degradation）
  - Per-shot 覆盖：`shot["doodle_refine"] = {bbox_ratio, refined_prompt}`
- **当前接受方案**：doodle 红框 + prompt 写狠了「完全没入不可见」→ 模型把**整把刀抹掉**（红框区域无可见匕首），用户接受「比一开始刀到处乱飞更好」+ 整体水下黑影伏笔保留
- **改进方向**（待办）：①prompt 调软「只留 1cm 顶端，hilt/guard/blade 没入」去掉「禁止…横躺/悬浮/平行」强否定（红框已约束空间，模型过度解读） ②新增短语到 `_SPATIAL_FALLBACK_DEFAULTS`（如「左手持物」「正对某人」） ③LF 集成（hook lf 是「刺客彻底没入水中」类）
- **教训**：精确空间约束 = 模型能力上限的硬边界。锚点系统 + 多次调 prompt 都突破不了时，必须切换通道（doodle 区域编辑 = Seedream 的「负向词替代品」）。下一步给 LLM 加规则：检测到「指甲盖/竖直/正对」类约束自动建议切 doodle，避免手动调半天。
- **记忆**：`memory/doodle-spatial-fallback.md`（v6.30 集成位置/触发条件/失败兜底/Per-shot 覆盖）

### P3. 图片模型不忠实渲染 ff_desc 锚点（08-14）
- **现象**：hook 首帧/尾帧。最终 prompt 明确写了「画右水下，一团玄色黑影轮廓，一截刀尖微露水面，正对杨旭心口方向」「【首帧禁血】刺客尚未出水、未刺中任何人」，但渲染出的图是**刺客上半身出水举匕首**、匕首尾部**发蓝光**（prompt 无任何「蓝光/发光」描述，为模型幻觉）；尾帧 prompt 写「神秘人已彻底没入水中」，图却是**刺客站水面上**（违反物理）；听香未跪坐、杨旭侧躺、钓杆画成细长撑杆。
- **根因**：`_build_final_frame_prompt` 重建证明**锚点已忠实到达图片模型**——是 wan2.7-image 对复杂单帧（3角色+威胁+道具+前一刻定格）渲染保真不足；「一截刀尖微露水面」诱导模型脑补持刀者（刀不浮空→补手→补手臂→刺客出水）；发光武器为模型常见幻觉。
- **影响**：Plan A 锚点系统在**文本层正确**（ff_desc/校验/最终 prompt 全达标），但**超出现行模型能力边界**——文本对、图不对。
- **待办方向**（用户定夺）：①简化 hook 首帧构图降载（纯水下黑影不要刀尖/删一件道具）；②hook 帧换 wan2.7-image-pro（官方标注复杂指令遵循更强）；③修 QC 让它当场拦住（见 P4）。
- **教训**：ff_desc 锚点写得再全，也要在「模型画得出」的复杂度内——锚点系统是上限放大器，不是模型能力替代品。

### P4. 图片 QC 的 prompt_semantics 失效（08-14 → 08-15 修复）
- **现象**：hook 4 帧 sidecar 全部 `has_hallucination:false, issues:[]`，但图明显不符 prompt（刺客出水/站水面/蓝光/姿势错）。QC 宣称做了「画面 vs 最终 prompt 语义核对」却全放过。
- **根因**：①传递链终点 `qc_image` 只认 `prompt is None`——空串/纯空白不触发 `build_qc_prompt` 兜底，视觉模型拿到空的「画面描述」无物可比 → 必然 clean（sidecar clean 实为「没查」）；②判定规则只给 warning、无关键锚点升级通道。
- **解决（08-15）**：`shared/image_qc.py` `qc_image` 改 `not (prompt and prompt.strip())` 才走兜底；`build_qc_prompt` 增关键锚点（服装色/人物数量/威胁源状态）严重度规则，`QC_SEMANTICS_CRITICAL=1` 升级 critical（删帧重生成，当场拦住），默认 warning。
- **教训**：QC 说 clean ≠ 图对。sidecar 的 `issues:[]` 在 QC prompt 无效时是「没查」而非「没问题」。

### P5. 关键帧数量 2→1（08-14 → 08-15 修复）
- **现象**：hook 的 script 有 keyframes×2（ratio 0.4/0.8），但 `images/` 只产出 1 张 `shot_0_kf.png`（`vimax_working/shots/0/` 有 keyframe_0/1）。
- **根因**：provider `_download` 的 fname_prefix 对**所有**关键帧统一取 `"kf"`（只有首帧/尾帧区分 ff/lf）→ 多个中间帧写同名 `shot_N_kf.png` 互相覆盖，只留最后一张。
- **解决（08-15）**：bridge 传 `frame_desc="关键帧{i}(t=...)"`，两个 provider（bailian/agnes）解析序号 → `shot_N_kf_0.png` / `shot_N_kf_1.png`。
- **教训**：provider 落盘命名与 bridge 帧类型脱钩时，同名覆盖是最隐蔽的数据丢失——命名必须携带唯一标识（帧类型+序号）。

### P6. 主角后镜省略服装色（08-14 → 08-15 修复）
- **现象**：规则 13.7 提升最高优先级后，主分镜服装色告警 24→10；剩 10 条（镜头 14-23）全为**主角杨旭**后镜省略——「杨旭仰卧船头，心口暗红洇痕」没写「素白轻袍」，而张十三/听香都重述了。
- **根因**：LLM 把主角当「已出场/已建立」，后镜不再重述；13.7 对 shots 的约束力仍弱于 hook（hook 是独立模板突出条目）。
- **影响**：渲染期 `_inject_character_costume` 已注入服装色兜底（实际渲出有）；ff_desc 自足性待收敛。
- **解决（08-15）**：规则 13.7 补「主角/已出场角色后镜也必须重述——禁止以『前镜已述/主角已建立』为由省略，主角与配角一视同仁」；待下一轮 step01 regen 验证收敛。

### P18. 校验无闭环 + 开篇冲突告警被淹没（08-15 确认 → 当天修复）
- **现象**：step01.log 每轮 ~60 条 WARN 纯打印后落盘；「前1/3段未建立核心冲突」「未检测到第一个爆点」这类直接决定前 10 秒留存的告警也只是一行 WARN。
- **根因**：`[GAP] 差距分析通过（尝试 1/2）`每轮都一次通过 → 重生成反馈（top-5 类）**从不附加** → 所有 WARN 无人修；`hard_fail_checks` 不通过也只 print。
- **影响**：高价值告警（开篇冲突/威胁朝向/keyframes 质量/首帧时序）带伤进 step02 烧额度；WARN 噪音导致「狼来了」效应，没人看 WARN。
- **解决（08-15）**：①定向修复轮 `_repair_shot_warnings`（main.py）——GAP 通过后高价值告警触发单镜 LLM 修复，只重写告警镜头，最多 2 轮/轮 6 镜，失败即停不中断；开篇冲突/爆点密度并入 `_REPAIR_PRIORITY_CATEGORIES`（作为修复前言注入）；`REPAIR_ROUNDS_ENABLED` 可关。②step02b 失败计数修真——`gen_all_videos` 统一以 `submit_poll._failed_videos` 为准（覆盖提交/QC/轮询失败 + Exception），坏镜头不再带 `Failed: 0` 假象进 step04。③QC 失败自动重提交（`VIDEO_QC_RESUBMIT=2`）+ 视频 seed 固定（`shot_index|ff|lf|prompt` 哈希派生）。④视频 QC 首/中/尾三帧（原单帧中段，漏检开头/结尾黑场淡出）。
- **验证**：`py_compile` 全过；修复收集器对现有脚本识别 23 个待修镜头；seed 确定性冒烟通过。
- **教训**：校验广度和「告警→修复」闭环是两回事；warning-only 系统会随时间积累成无人阅读的噪音——高价值告警必须有触发修复的通道，且要全脚本重生成之外的「单镜手术」级修复手段。

---

## ✅ 已解决（存档）

### P1. hook 首帧手改+反复重渲死胡同（08-13 → 08-14 解决）
- **现象**：手改生成文件的 ff_desc + 反复重渲，越改越乱——杨旭素白轻袍漂色、听香视线变向、船画小两人挤满、刺客不看目标。
- **根因**：①图像模型每次重渲=全新随机，文字手改对像素细节无控制力；②手改删掉锚点（尺寸/服装色/威胁源朝向），只靠角色肖像软锚必漂。
- **解决**：改走「规则+校验」系统性修（Plan A）：`_HOOK` 加 `char_layout`+keyframes；ff_desc 全锚点校验（威胁朝向/视线方位/尺寸锚/服装色）；规则 13.7 服装色提升最高优先级。LLM 一次生成全锚点 ff_desc。
- **教训**：**手改生成文件 + 反复重渲是死胡同**。要改就让 LLM 按规则一次生成对，校验兜底。

### P2. LLM 原始输出丢失，无法回滚（08-13 记录）
- **现象**：想回滚 hook 6 字段到 08-13 22:50 的 LLM 原始输出，但只有 todolist 抄录的 ff_desc 一句话，其余 5 字段（lf/motion/prompt/keyframes）原文丢失。
- **根因**：`output/` 整个目录被 `.gitignore` 忽略（`.gitignore:14`），`script_result.json` 从未进 git；无 .bak、无 raw 响应缓存。
- **解决**：无法 git 还原，用户决策跳过回滚、全量重跑 step01。
- **教训**：生成产物不进 git，**重要基线需主动备份**（或关键中间产物入库）。

### P7. 校验误报刷屏（08-11 → 08-12 解决）
- **现象**：step01.log 同一类校验误报十几条重复，淹没真实缺陷。
- **根因**：自己打的检查自己误报（P0 1-5：电影级污染/fps对齐/空镜误判/多地点堆叠/渲染参数缺失）+ 规则过严（P1 6-7：keyframes/视线刷屏）。
- **解决**：P0 修根因（remove 词表/按 fps=24 对齐/动作镜排除/去纯转场词/提前注入渲染参数）+ P1 聚合提示；真实缺陷 8/9/11/12 单独修。
- **教训**：校验也要单测防自误报；刷屏用「每类最多 1 条 + 命中数」聚合。

### P8. qwen-image 800 字 prompt 限制遗留（08-08 定 → 08-14 确认并放宽）
- **现象**：ff_desc 80/120 字符限制与锚点栈（服装色/尺寸/威胁朝向）打架，hook ff_desc 塞不下尺寸锚。
- **根因**：80/120 是 08-08 换 `qwen-image-2.0-pro` 时定（官方硬限制：t2i 800 字符/2.0 系列 1300 token）；现行 `wan2.7-image` 官方文档「最大输入长度 = ---（未设定）」。
- **解决**：A+B 放宽 **普通镜头 ≤80→≤120，钩子 ≤120→≤160**；尺寸锚职责交给 bank 渲染期注入（`_inject_object_size_anchor`，bank 未覆盖才要求 ff_desc 自足）。
- **教训**：模型换代的遗留限制要跟着清；先查现行模型官方限制再决定约束值。

### P9. prompt.py 精简 token 预估偏高（08-12 记录）
- **现象**：预估省 1.6万-2.2万 token，两轮做完实测只省 ~5.5k token（14%）。
- **根因**：把 SYSTEM_PROMPT 源码块字符（含引号/换行转义）误当正文字符，高估可删示例量。
- **解决**：无（记录事实，剩余可压缩空间已很小，继续删风险/收益比上升）。
- **教训**：预估 token 省量要用「LLM 实际看到的正文」算，别用源码字符。

### P10. hook 与 shot 校验「两套」（08-14 确认，未重构）
- **现象**：`validate_hook`（手写子集）+ `_run_prompt_checklist`（54 项）两套校验；hook 模板改动、阈值改动要手动同步两处。
- **根因**：hook 在主分镜生成之后单独 LLM 生成（要引用主分镜台词黑名单），主校验跑在 parse_response（hook 未挂载）；hook 挂载后只能 `validate_hook` 补查。
- **解决**：未重构。已把威胁耦合/keyframes/站位/ff锚点 手动补进 `validate_hook` 缓解。
- **待办方向**：hook 挂载后重跑 `_run_prompt_checklist`（此时 script 有 hook）统一，`validate_hook` 退化为薄封装。

### P11. 12 位「未知」问题（占位，待补充）

> 以下为已知但未单列的问题（记忆中有记录，先占位不展开）：
> - hook 武器 t=0 不可见 → 模型凭空变武器/刺向错（已修 `_fix_ff_weapon_visible`）
> - 尾帧武器残留（匕首尖停水面）→ 已修规则 609/validation
> - 空镜出现人物幻觉（拿蜡烛的人）→ 前缀撤烛火+负面撤禁亮光+旁白改画外音
> - 图片 429 限流白烧免费额度 → 读 retry-after + 60s 窗口兜底
> - 首尾帧动漫→真人漂移 → 全镜头首尾双关键帧+prompt 尾部收束

### P12. is_happyhorse NameError 回归（08-15 引入 → 当天修复）
- **现象**：`_video_gen/bailian_provider.py` r2v 提交必崩 NameError（`is_happyhorse` 未定义），而 `.env.content` 默认 `VIDEO_REFERENCE_MODE=r2v` → **所有镜头视频生成失败**。
- **根因**：08-15 改 ref_compose 时把 `_create_r2v_task` 里的 `is_happyhorse = "happyhorse" in model.lower()` 连同旧 `[:max_refs]` 裁剪逻辑一起删了，但 445/459/467 行仍引用。
- **解决**：补回定义；`py_compile` + rg 验证。
- **教训**：**重构删代码时，用 rg 全局确认所有引用点**（子代理审计才发现的回归）；改 provider 模型分支后至少 py_compile + 一次冒烟调用。

### P13. 中间关键帧白生成（08-15 确认 → 修复）
- **现象**：step02a 花额度生成 keyframe_0/1，但 bailian wan2.7-i2v 的 media 组合固定（首帧+尾帧+音频），`extra_keyframe_paths` 全程未读；image 模式下 prompt 还虚假声明「有关键帧参考图」→ 模型被告知存在实际没传的图。
- **根因**：bailian provider 只编码 `image_path`/`last_frame_image_path`；keyframe 落盘逻辑（P5 修过）与视频请求脱钩。
- **解决**：prompt_builder 按 provider 区分——bailian image 模式不收集 kf（不虚假声明），agnes image 模式保留（真传），r2v/text 不收集。
- **教训**：帧图生成（step02a）与视频生成（step02b）是两条链路，**产物是否真被下游消费必须逐 provider 验证**——「生成了」≠「用上了」。

### P14. 双层降采样 720P 瓶颈（08-15 确认 → 配置化）
- **现象**：1080P 首帧图 → wan2.7 按 `BAILIAN_VIDEO_RESOLUTION=720P` 出片（一次降采样）→ step04 硬编码 `scale=720:1280` 再压（第二次）；动作镜头细节发糊。
- **根因**：`prompt_builder.py` 的 `size="720x1280"` 是死参数（bailian 不读 size，读 resolution）；step04 merge 硬编码 scale 双重降采样。
- **解决**：`MERGE_RESOLUTION`/`MERGE_PRESET`/`MERGE_CRF`/`MERGE_AUDIO_MODE`/`MERGE_AUDIO_LOUDNORM` 全部配置化（shared/config.py）；调试期保持 720P 省钱，上线切 1080P（视频 0.6→1 元/秒）。
- **教训**：**「改了没反应」陷阱**——先确认参数是否真到达 provider（size vs resolution 双源）；分辨率要全链路（生成+合成）统一口径。

### P15. step04 合成层缺陷批（08-15 修复）
- **现象**：①字幕 PrimaryColour=OutlineColour=白色 → 亮场景不可读；②调色滤镜在字幕**之后**（vignette 压暗字幕区）；③无响度归一，只有固定 +5dB（放大底噪且不解决逐镜跳变）；④编码 ultrafast+CRF20 画质损失；⑤hook 704 宽 pad 出 8px 黑边烧进成片；⑥回退 happyhorse（无声）→ `[i:a]` 匹配不到 → **整章合并失败**。
- **根因**：合并层从未做过可读性/音质/鲁棒性审计；preset/CRF/分辨率全硬编码。
- **解决**：字幕黑边+微软雅黑+字号随分辨率缩放；filter 顺序改调色在前字幕最后；逐镜 loudnorm（EBU R128）替代固定增益；preset=medium/CRF=18 配置化；increase+crop 消黑边；无声片段 `anullsrc` 补轨。
- **教训**：合成层是成片的「最后一公里」，编码/字幕/音质/鲁棒性四个维度都要配置化+兜底。

### P16. step03 TTS 孤儿步骤（08-15 确认 → 保留为备选）
- **现象**：voice_registry 8 音色精心生成的 mp3 **不进成片**——step04 native 模式只保留视频原声；音色一致性只靠 prompt 文字描述；`reference_audio` 是 TODO。
- **根因**：架构改 native（视频模型自带语音）后删了 TTS 混音路径，但 step03 没跟着删。
- **解决**：`MERGE_AUDIO_MODE=tts_mix` 恢复可选混音（TTS 权重 1.0、原声压 0.6），对视频原声不满意时切换；默认仍 native。TTS 300 字拦腰截断改为按句末标点安全截断。
- **待办方向**：实现 `reference_audio` 通道（step03 产物变音色参考，一石二鸟）。
- **教训**：删掉下游消费者时要同步评估上游是否变孤儿（跑 step03 = 白烧额度）。

### P17. step01 检查器误报 ~50%（08-15 修复）
- **现象**：最新轮 62 条 WARN，逐条对照 script_result.json 核实约一半是检查器自身缺陷（详见 P17 子项）。
- **根因**：检查器正则/判定逻辑与 LLM 实际输出格式脱节 + 规则互相矛盾 + 阈值不符合连续动作物理性。
- **解决**（validation.py + prompts.py，回归全过）：
  - 威胁源朝向句式扩充（刺向/刀尖朝/举过头顶/即将…）+ 规则48 水下黑影豁免（V1/V2）
  - 锚点标点归一化补全（！？…等）——「哎呀！」带感叹号匹配失败真因（V3）
  - 克制情绪触发词移除「压抑/微红」（氛围词/物理红印误报，V4）
  - 色调过渡混合色调兼容——「冷蓝+暖橙对比」≠突变，纯暖↔纯冷才报（V5）
  - shot_count 缺失静默补齐；narration 拼接仅字段存在时比对（V6/V7）
  - 内容密度档位放宽——连贯动作链「推臂→弹腿→倒纵」不是 3 个独立动作（V8）
  - hook 豁免结果状态污染 + motion 首时间段内状态词放行（V9）
  - 全检查器 hook 双编号统一 `_shot_tag`（V10）
  - prompts.py SYSTEM_PROMPT 注入 41.1/24.1/28.1/22.5a 四条规则（让 LLM 一次写对）
- **效果**：62 → 2 条（剩 2 条人工核实为真命中：hook 微表情不足、镜头7 动作密度偏满）。
- **教训**：**校验器自己也会出错**；改完必须对真实 script_result.json 回归；误报率 >30% 的系统没人信（狼来了效应），高价值检查（威胁源朝向）测不准比没有更糟——先修可信度再谈自动化。

### P19. lf_desc 锚点校验缺失（08-15 修复）
- **现象**：锚点检查（威胁朝向/视线方位矛盾/服装色重述）只查 `ff_desc`，尾帧同样喂图片模型却零锚点校验——实测现有脚本 lf_desc 有 **22 条服装色缺口**（尾帧漂色根因之一）。
- **根因**：08-14 的 Plan A 锚点系统只覆盖「首帧」维度（ff_desc 是动作起点），漏掉尾帧；规则 13.7 也只针对 ff。
- **解决**：`_check_ff_desc_anchors` 重构出 `_check_frame_desc_anchors(text, label, ...)`，A/B/D 三类锚点对称套到 lf_desc（ff==lf 时不重复查；尺寸锚 C 仍 ff 专属，bank 注入兜底 lf）。
- **验证**：对现有 script_result.json 重跑，新增 22 条 lf 告警（均为真实缺口，交由定向修复轮处理）。
- **教训**：**锚点校验要覆盖所有喂给图片模型的描述字段**（首帧/尾帧/关键帧）——只修一半等于留一半漂色口子。

### P20. 视频 QC 单帧 + seed 不传 + 失败假象（08-15 修复）
- **现象**：①视频视觉 QC 只抽**中段 1 帧**（`_MID_FRAME_RATIO=0.5`），开头黑场/结尾淡出吞画面漏检；②`req.seed` 恒 None → 每次提交纯抽卡，QC 判坏重提交拿全新随机结果，好坏反复横跳；③step02b `Failed: N` 只统计 gather 抛出的 Exception——`_poll_video` 正常 `return False`（终态错误/QC 失败/超时）与 `_submit_video` 失败（return None）都没计入，坏镜头带 `Failed: 0` 假象流进 step04。
- **根因**：QC 设计时只考虑了中段代表性；seed 从未接线（wan2.7 文档支持 seed，白白没用）；失败计数用 Exception 判定而非 `_failed_videos` 注册表。
- **解决**：①`video_qc.py` 抽首/中/尾三帧（0.12/0.5/0.88），任一帧 critical 即判坏；②`prompt_builder._make_request` 用 `shot_index|ff|lf|prompt` MD5 派生固定 seed；③`gen_all_videos` 失败数以 `submit_poll._failed_videos` 为准，日志输出失败镜头明细；④QC 失败自动重提交（`VIDEO_QC_RESUBMIT=2`，相同 req=相同 seed 可复现）。
- **验证**：seed 确定性冒烟通过（同输入同输出、改 prompt 变 seed）；三帧比例断言通过；`py_compile` 全过。
- **教训**：①视觉 QC 的抽帧策略要覆盖「可能出问题」的时段，单帧中段是最乐观抽样；②生成模型该用 seed 的地方一定要用（可复现性是 A/B 测试和 QC-重试循环的前提）；③失败统计要认「失败注册表」而非「异常类型」——正常返回 False 也是失败。

### P21. 刺客弯刀幻觉重复道具（08-16 确认 → 当天修复）
- **现象**：`props_bank` 同时存在「刺客弯刀」(P06) 与「刺客匕首」(P09)，钩子镜 r2v 参考图把两件道具一起注入（`image_submit: ref[6] 源路径=...刺客弯刀\prop.png`）。但刺客武器从头到尾是匕首（shot 6/7/hook 全为「右手紧握寒光匕首」）。
- **根因**：①第 1 章原文**从未命名刺客武器**——只有「手中一道寒光一闪，恰如天边那轮弦月一般」，道具提取 LLM 顺着「弦月」意象推断出「弯刀」；②后来钩子武器锁定为匕首（P1/Plan A 系列），提取器改名新建「刺客匕首」；③`register_prop` 只按 **identifier 精确匹配**去重（「刺客弯刀」≠「刺客匕首」），两件武器并存、都生成 prop.png；④运行时 `_collect_prop_asset_refs`/r2v 按「道具名+consistent_terms」子串匹配，弯刀别名含「匕首」→ 钩子镜写匕首时两件参考图一起命中。
- **解决（双加固）**：①数据：删 props_bank.json「刺客弯刀」条目 + 删目录（代码里其它「弯刀」都是通用武器词正则，与资产无关）；②`register_prop` 新增 `_find_alias_conflict` 别名去重（identifier∈别名 或 去泛化后别名交集非空 → 并入既有、不新建；吸收时保留 canonical 外观/别名），`_ALIAS_GENERIC_TERMS` 停用表防 牛车/马车 共享「车」误并入；③`_collect_prop_asset_refs` 与 r2v 道具段改为「命中词子串嵌套过滤」——hit 是另一命中的子串视为同一件丢弃（「匕首」⊂「寒光匕首」→ 只留匕首），命中词相同保留先出现者。
- **验证**：S1 寒光匕首镜头只留匕首 / S2 老酒胜酒壶 / S3 酒壶+老酒同镜两件都留（不误杀）/ T1 弯刀并入匕首 canonical 不污染 / T2 牛车马车不误并入 / r2v 路径无 NameError。`py_compile` 全过。
- **残留**：当前 script_result.json 的 prop_assets 仍含「刺客弯刀」（只影响【道具锚定】文字块冗余，不影响参考图——参考图走 bank）。prop_assets 从 bank 回写（main.py:719），下轮 step01 跑一次自动干净；**勿手动改 script_result.json**（mtime 变更触发帧/视频缓存失效，白烧额度）。已生成的钩子帧/视频（shot 6/7）含旧弯刀参考，需 `--force` 重生成该镜。
- **教训**：bank 去重只认 identifier 精确匹配时，LLM 给同一物件换名=必出重复条目，别名重叠才是「同一件」的真实信号；且「一致性问题」要在写入端（bank）拦一次、读取端（匹配）兜一次，单层防线必漏。
