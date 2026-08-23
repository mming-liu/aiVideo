# TODO 待办清单

> 记录**尚未完成**的修复、待验证项、待决策事项。
> 已解决的问题与历史存档见 **[problems.md](problems.md)**（问题记录文档，含现象/根因/处理/教训）。
> 最近更新：2026-08-22

---

## ✅ v6.35（08-20）—— 高潮优先原则落地

**用户决策**：保留 1-2 镜快铺垫 + 强冲突/关键情感戏；背景/过渡/日常 = 0 镜；仅改 prompt 措辞

**`step01_script_gen/prompts.py` 改动**：
- [x] **新增规则 3.7「高潮优先原则」**：60s 短剧 = 事件密度 × 视觉张力，所有镜头预算优先分配给高潮内容
  - 强冲突（必拍，≥2 镜/段）：打斗/刺杀/对峙/追击/反转/揭露/质问/陷害/揭秘
  - 关键情感戏（可拍，1-3 镜/段）：告别/重逢/释怀/隐忍/崩溃/承认/和解/复仇誓
  - 快速铺垫（限 1-2 镜）：开篇场景建立 + 主要人物亮相，每镜 ≤3 秒
  - 背景/过渡/日常（0 镜）：人物履历/身世背景/环境描写/季节更替/日常琐事/过场叙述
  - 镜头预算公式：铺垫 ≤2 镜 + 强冲突/关键情感戏 N 镜 + 收尾 0-1 镜
- [x] **收紧规则 3「内容主次判断」**：从「合并为 1 个空镜」改为「禁止分配任何镜头」，信息由旁白（narration_segment）承担
- [x] **收紧规则 4「全弧线覆盖」**：核心镜头必须取高潮段，1-2 镜快铺垫只允许在开篇，背景/过渡/日常一律 0 镜

**预期效果**：
- 镜头数会减少（去掉背景镜），但总时长不一定降低（每个镜头可拉长）
- 重点是镜头预算从「全章平均」转向「高潮密集」
- 下一轮 step01 regen 验证：观察镜头分布是否集中到高潮段

**未做（后续可补）**：
- [x] validation.py 检查：检测"非高潮段"镜头是否过多（`v6.37` `_check_climax_priority` 落地，规则 3.7 警告 + 全片超 2 镜总览警告）
- [ ] 旧脚本_result.json（08-20 之前生成）需重跑才能体现新规则
- [x] 在 problems.md 添加 v6.35 决策记录（P26 已添加）

---

## 🟡 Mode A 修复 prompt 加强（08-20 现状：精简 + 强手术刀）

**背景**：08-20 mode A/B/C 三套反馈聚合上线，**A 是「精简 + 强手术刀」版本**——无全片错误分布、强 5 条手术刀禁令（`step01_script_gen/main.py:838-853` + fallback `main.py:890-899`）。A 全局视图差但无 C 的 4 个破坏性代价（lighting 暴涨 / 服装色漏 / 致命伤删除 / 总时长 +10s）。

**问题**：A 当前 prompt 只强调「**不要做什么**」（5 条禁令），没说「**必须保留什么**」——
1. 致命伤 / 服装色 / 角色状态 / 锚点连贯 4 类**关键字段**未进白名单——LLM 仍有空间误删 anchor（如 C 实验中删 shot_14/24/25 的"心口暗红洇痕"）
2. 无 top-3 高频 hint——LLM 不知道全片主要矛盾，全局视图弱
3. 无「mask 字段清单」——"逐字保留"过于抽象，LLM 不知哪些字段是神圣不可动
4. 缺"warn 改字段时保留同字段 anchor"约束——LLM 改 prompt 时连带删 anchor 的风险未堵

**Mode A++ 落地（08-21，已实施 + 5 轮微调 + 模式合并）**：
- [x] **加「关键字段保留白名单」**：致命伤 anchor / 服装色 / 角色状态 / 锚点连贯 / color_tone
- [x] **加「mask 字段清单」**：narration_segment / index / duration_sec / subtitle / variation_type / scene / location
- [x] **加 top-3 高频 hint**：`_label_total` 总是计算
- [x] **同镜内 warn 改字段时必须保留同字段 anchor 子句**
- [x] **v2 微调**：视线方位白名单例外 + lf_desc ≥40 字下限 + variation_type 字段名修正
- [x] **v3 扩展**：Mode B/C 也加 4 类约束 + top-3 hint；validation.py 加 lf_desc 字符下限硬校验
- [x] **v4 增强**：修复决策树 + 3 组正反例 + 同角色跨镜一致性
- [x] **v5 模式合并**（08-21 末）：
  - 删除 `FEEDBACK_MODE` 全局常量 + `_parse_mode_flag()` 函数 + `--mode` CLI flag
  - 删除 `if FEEDBACK_MODE == "A": / else:` 的 if/else 分支（_repair_turn + fallback）
  - `_global_block` / `_label_total` / 原文切片 总是注入
  - 单一 unified prompt（v1-v4 所有特性合并）
  - `_feedback_json["mode"]` 改为 `"unified"`
- [ ] **重跑 chapter_001 验证 unified 模式效果**（v1-v4 全部特性 + validation 兜底）
- [ ] **若 unified 稳定 → 作为新默认（已是默认）**

**位置**：`step01_script_gen/main.py:822-`（_repair_turn unified v5）+ `main.py:931-`（fallback 段 unified v5）+ `validation.py:1684-1703`（lf_desc 字符下限校验）

---

### 7. seedance 2026.05+ 新版铁律增量吸收（08-21 v6.38，已实施）

**调研方法**：firecrawl 需付费认证，web search 全部无返回 → 改用 `curl` 抓 GitHub API + raw 文件（成功抓到 shyman159/seedance-prompts-skill SKILL.md + 4 个 references）

**已实施 4 落地 + 合规收紧**：
- [x] **规则 55**：主观视角必须放句首（seedance 铁律 11）—— POV/第一人称视角放句首 + 角色呼吸/眨眼/伸手
- [x] **规则 56**：防崩神词 + 排版规则（seedance 铁律 9）—— 末尾追加「画面稳定·不闪烁·不重影·不模糊·动作流畅自然·不卡顿·不断裂·光影细腻·高清画质·电影质感」+ 禁「————」长破折号
- [x] **规则 57**：情绪具象化表（seedance 铁律 12 增量）—— 5 类情绪（悲伤/喜悦/紧张/愤怒/释然）具体身体外化动作
- [x] **规则 6 补充**：2026.05 审核收紧——「15-17岁少女」→「年轻女孩」/「裸妆」→「清新妆」/禁负面词前缀
- [x] **validation._check_long_dash_usage** v6.38：检测 ≥3 个 ——
- [x] **注册到 `_REGEN_FEEDBACK_LABELS`** + `_feedback_json` 反馈链
- [x] **absorption-log §4**：记录新源 + 落地映射 + 5 条未采纳（含官方修正「镜头N」不适配原因）
- [x] **memory entry**：seedance-2026-05-absorption-2026-08.md

**未采纳（已在项目覆盖）**：
- 八维黄金公式（PROMPT_TEMPLATE 7 步结构覆盖）
- 16 种运镜/8 种光型/7 种构图（已有 camera-mastery/portrait-mastery 覆盖）
- 时长估算公式 `对白句数 × 1.3s + 画面节拍数 × 0.8-1.5s`（已有 prompt_length_budget 校验）
- 官方修正「镜头N」替代「0-X秒」（我们的 16.4 仍用「前2秒/2-4秒」——沉浸式短片对口型同步场景官方允许保留秒数标注）

**位置**：`step01_script_gen/prompts.py` 规则 55/56/57 + 规则 6 补充；`step01_script_gen/validation.py` v6.38 `_check_long_dash_usage`；`.aiVideo/skills/absorption-log/SKILL.md` §4



### 1. hook 首帧渲染验收（⏳ Plan A 最后一步）
- 渲一次 hook 首帧（step02a `--shots 0`）→ 人工验收（不再反复手改）。
- **注意**：当前 hook 首帧/尾帧已渲染（08-14 22:50-22:53），但**图片模型不忠实渲染锚点**（见 #2）——
  图是「刺客出水举蓝光匕首」，prompt 是「潜伏水下刀尖微露」。验收前需先解决 #2。
- **08-15 进展**：qwen-image-3.0-pro 直测已验收（钩子「水下黑影+刀尖微露」忠实渲染、船体比例修复）；
  模型全局切换 + 色调锚（color_tone 注入）+ 空间充裕锚规则已落地，可重渲 step02a `--shots 0` 验收。

### 2. 图片模型不忠实渲染 ff_desc 锚点 🔴（08-15 已修复，待人工重渲验收）
- **问题**（详见 problems.md P3）：最终 prompt 带全部锚点（含「刺客尚未出水/刀尖微露」），
  wan2.7-image 却画成刺客出水举刀 + 蓝光匕首幻觉；尾帧「已没入水中」画成「站水面」。
- **08-15 已修复**：
  - [x] 图片模型切换 `qwen-image-3.0-pro`（全局，`.env.content` BAILIAN_T2I_MODEL + BAILIAN_IMAGE_RPM=1 预限速，403 回退 wan2.7-image → wan2.7-image-pro）——08-15 钩子直测验收通过
  - [x] 色调锚：`shared/vimax_bridge.py` 新增 `_inject_tone_anchor`，首帧/尾帧/关键帧/QC 核对 prompt 统一注入 `color_tone`
  - [x] 空间充裕锚：`step01_script_gen/prompts.py` HOOK_PROMPT_TEMPLATE 增「物体上有人物须写容纳性（船身宽大/空间充裕）」+「ff_desc 色调重述」规则
- **待办方向**（用户定夺）：
  - [ ] 简化 hook 首帧构图降载（纯水下黑影不要「刀尖微露」/删一件道具）——已验证 qwen-3.0-pro 可忠实渲染，暂不需要
  - [ ] 修 QC（见 #3）让「画不对」当场拦住

### 3. 图片 QC 的 prompt_semantics 失效 🔴（08-15 已修复）
- **问题**（详见 problems.md P4）：图明显不符但 sidecar 全 clean。
- **08-15 已修复**：
  - [x] `shared/image_qc.py` `qc_image` 空串/纯空白也触发 `build_qc_prompt` 兜底（`not (prompt and prompt.strip())`）
  - [x] 关键锚点（服装色/人物数量/威胁源状态）不符 → warning；`QC_SEMANTICS_CRITICAL=1` 可升级 critical 删帧重生成

### 4. 关键帧数量 2→1 🟡（08-15 已修复）
- script 有 keyframes×2（0.4/0.8），`images/` 只产出 1 张 `shot_0_kf.png`。
- **08-15 已修复**：根因=provider `_download` 所有关键帧共用 `kf` 前缀同名覆盖；
  bridge 传 `frame_desc="关键帧{i}"` + 两 provider 解析序号 → `shot_N_kf_0.png`/`shot_N_kf_1.png`。

### 5. 主角后镜省略服装色（⏳ 收敛中）
- 规则 13.7 提升后 24→10；剩 10 条（镜头 14-23）全为**杨旭**后镜省略服装色。
- 渲染期 `_inject_character_costume` 已兜底。
- **08-15**：13.7 规则已补「主角/已出场角色后镜也必须重述（禁止『前镜已述』省略）」；
  待下一轮 step01 regen 验证收敛。

### 6. prompts.py 优化（代码审计发现，08-18）
**文件**：`step01_script_gen/prompts.py`

- [x] **🔴 死 import 清理**：第 20 行 `import style_prefix` 和第 1027 行 `import style_prefix` 均无调用
  （全文无 `style_prefix.`），纯死代码。删掉两处。
- [x] **🔴 PROMPT_TEMPLATE 末尾重复注入 `{scene_object_anchors}`**：第 41 行已有
  「场景物体尺寸锚定库」section，第 138 行 `{novel_text}{scene_object_anchors}` 又拼一次，
  LLM 看到两段相同锚定内容。删掉末尾重复注入。
- [ ] **🔴 skill 全量加载 token 浪费**：`_load_knowledge_skills()` 一次性加载 20 个 skill
  （battle-layered/face-shapes 等与本章无关），推高 token 消耗且稀释核心指令注意力。
  方案：按本章场景类型（打斗/对话/情感）做相关性筛选，或改为目录式索引（只列 skill 名+摘要）。
  **08-20 进展**：技能合并完成——已合并 7 组共 30 个技能 → 7 个（seedance-craft / absorption-log / camera-mastery / combat-mastery / portrait-mastery / transition-mastery / character-performance），加载数从 22→9。但仍是全量加载，未做相关性筛选。
  - 合并明细：seedance 13→1 + absorption 3→1 + camera 5→1 + combat 4→1 + portrait 3→1 + transition 3→1 + character 2→1
  - 当前 _load_knowledge_skills() 加载 9 个 skill，总 ~28k chars (~7,000 tokens)
  - 总删除：30 个 skill 目录（46 → 16 → 7 个 skill 类别）
- [x] **🔴 反馈覆盖审计（v6.37，08-20）**：之前 repair 轮只覆盖 9/68 类检查，32 类有 label 但未进 repair，28 类 silent（无 label）
  - 修复：`_REPAIR_PRIORITY_CATEGORIES` 9 → 40 类（覆盖所有字段级可手术修复）
  - 修复：`_REGEN_FEEDBACK_LABELS` 40 → 46 类（补 ff_desc_anchors + kf_time_window_alignment + grounding + subtitle_leak + climax_priority + focal_switch 6 个 label）
  - 新增：`_check_climax_priority` (v6.37) — 检测非高潮镜头（背景/过渡/日常关键词 + 无强冲突/情感戏），对应规则 3.7
  - 优化：`build_regen_feedback` 由「首例截断」改为「每类全量+镜头号列表」——LLM 改 shot_4 时能看到「同类全片共 N 处，命中 镜头2,5,7」
  - 待验证：下一次 step01 regen 观察 WARN 是否减少（特别是之前 silent 的几类）
- [x] **🟡 v6.37 修复轮结构化输入（input 多轮 messages）**：用户提问"prompt 和修复内容是否分开"——查 OpenAI Responses API 文档/SDK 后发现 `input` 字段是 `Union[str, ResponseInputParam]`，实际支持结构化多 item 列表（多轮 user 消息），并非"必须全拼一起"
  - 改动：`_text_gen/models.py` 新增 `user_inputs: Optional[list] = None` 字段
  - 改动：3 个 provider（deepseek_responses / deepseek_chat / agnes / dashscope）全部支持 user_inputs，旧 user_prompt 作为回退
  - 改动：`step01_script_gen/main.py` `_repair_shot_warnings` 拆 2 turn：
    - Turn 0: INPUT（**当前分镜 + 角色锚点 + skills，不含 novel，防 LLM 重生成**）
    - Turn 1: REPAIR FEEDBACK（结构化 JSON 按 shot 分组）+ TASK（手术刀原则+修复要点）
  - LLM 把两条视为两条 user 消息，结构比原 `\n\n` 拼接清晰
  - user_prompt 仍保留（拼接旧格式），作为不支持多轮 provider 的回退
- [x] **🟡 v6.37 镜头数硬约束（PROMPT_TEMPLATE 改造）**：用户问"压缩分镜数时其他修复质量是否会下降"——回答：会，LLM 合并镜头时 surgical 修复（服装色/致命伤/kf 时序/高潮优先）会失守 8/10
  - 根因：`_regen_over_limit` 让 LLM 同时做"合并（结构性）"和"修复（局部性）"两个矛盾任务
  - 解决：在 PROMPT_TEMPLATE 第 1 句硬约束「镜头数 ≤ {shot_count_max}」（默认 25），并明确「本轮一次性合规」+「走兜底合并」
  - 占位符注入：`build_prompt` 改用 `.format(shot_count_min=..., shot_count_max=...)`，从 env 读，避免硬编码 25/12
  - 双重保险：硬约束 + `_regen_over_limit` 兜底（实测 LLM 仍偶发超限）
- [x] **🟡 v6.35 高潮优先原则落地**（见上 v6.35 块）
- [x] **🟡 钩子缺少 `_duration_cap_guidance`**：主分镜 `build_prompt` 有模型时长上限引导，
  `build_hook_prompt` 没有。对低上限模型（如 Hailuo v1 6s）钩子可能生成超长镜头。
  对齐补上。
- [x] **🟡 合并 `_format_list` / `_format_dict` / `_format_movement_params`**：
  三个函数功能重叠，本质都是「格式化为 `- key: value` 列表」。抽一个多态 `_format_kv`。
- [x] **🟡 抽 `_get_skills_loader()`**：`_load_local_skill_context` 和 `_load_knowledge_skills`
  重复了 importlib 加载 `skills_loader.py` 的代码，DRY。
- [x] **🟢 第 226 行示例笔误**：「剑眉星目身穿白袍的杨旭白色长袍]杨旭坐在船头」
  中「白色长袍]」是残留错误文本，删掉。
- [ ] **🟢 规则编号重排**：3 / 3.5 / 3.6 / 3.7 / 4 混乱（3.5/3.6 是后插入的），长期维护堪忧。
  考虑重编号或用命名常量。
  - **08-21 决策**：暂不动——v2.0-3.x 已占用 3.1/3.2/3.3（参考素材/正文/制作说明组），
    重编号会撞名；当前 3.5/3.6/3.7 作为 3 的子规则在结构上合法（5.1/5.2/13.x/16.x/22.x 都同款），
    不影响 LLM 遵守。若未来扩展可考虑：把 3.5/3.6 移入「模型能力/动作密度」专章（编号 60+），
    3.7 单独升为 4（与 4 合并）。

## 🟡 待决策

- **shot ff_desc ≤120 vs hook ≤160 数字**：是否统一（有意差异——hook 全景建场更宽）。
- **hook 与 shot 校验「两套」是否统一**（problems.md P10）：hook 挂载后重跑 `_run_prompt_checklist`。
- **反馈聚合模式 A vs C 效果对比**（08-18 A/B 实验，**08-19 结论：C 胜出，已改默认 C**）：
  - 实验设计：修复轮（`_repair_shot_warnings`）的 WARN 聚合方式
  - **A 模式（旧默认）**：按分镜聚合，每镜头看到自己的 WARN，不知全片同类总数
  - **C 模式（新默认）**：每条 WARN 末尾追加"（同类全片共 N 处）"+ prompt 开头加「全片错误分布」
  - 触发方式：`--mode A` / `--mode C`（CLI flag，**默认 C**）
  - 基线：step01.log 中 2026-08-18 23:21:33 之前 = A；之后 = C
  - 产物：`script_result_A.json`（A 模式结果，已保存）/ `script_result.json`（C 模式结果，现用）
  - **对比结论**（08-19 实测 chapter_001）：
    - 总 WARN：90 → 73，**-17（-18.9%）** ✓
    - top-6 被选镜头：WARN 35 → 24（-31%）；**非 top-6 的 21 个镜头也改善**（55 → 47，-15%），13 个镜头受益 = **全局视图迁移效应** ✓
    - 最大赢家：多人站位 13→0（-100%）、打斗打击 1→0、视线 11→6（-45.5%）、FF 时间线 4→1（-75%）
    - **代价 1**：lighting_position 3→12（+9，建议类 WARN，不是错误）
    - **代价 2**：服装色锚定 4→7（+3，C 模式改 lf_desc 时漏写张十三服装色）
    - **代价 3**：致命伤锚定 16→14（-2 镜）——C 模式 LLM 删掉了 shot 14/24/25 的"心口暗红洇痕"（违反规则 8）
    - **代价 4**：总时长 +10s（173→183s）
  - **防护措施**（08-19 已加）：
    1. 默认模式改为 C
    2. 修复轮返回后再跑一次 `hard_fix_shot_prompts`（幂等兜底）
    3. 追加**轻量致命伤兜底**：build_idx 之后的镜头，杨旭仰卧/倒下 + 无 anchor → prompt 补伤处锚（只补刺杀后的镜头，不误伤夜宴仰卧喝酒）
  - 评估维度：总 WARN 数变化（↓幅度）、关键类别（致命伤/锚点/衔接）收敛率、未被选中镜头是否受益（全局视图迁移效应）、新引入问题数量（是否越修越糟）

## ⏳ 待人工核对

- **镜头 9 narration 原文未找到**（08-12 遗留）：原文锚点校验报台词未命中（「听香道：哎呀！…」），
  需对照小说原文确认 LLM 编造还是节选变形（非代码问题，须人工判断）。

---

## 🟢 v6.41+ C 方案 — 跨章按冲突重组分集（08-22 决策入档，future roadmap）

**用户决策（08-22）**：网文 1 章 ≠ 红果 1 集——必须按冲突密度重组。

| 章节类型 | 现行（A 方案） | 需求（C 方案） |
|---|---|---|
| 冲突密集章（刺杀/揭露/对峙/反转） | 1 章 = 1 集（3-8 镜 60-90s） | **1 章拆 2-3 集**（每集 60-90s，保留高潮完整度） |
| 铺垫/水字数章（场景建立/心理/环境/日常） | 1 章 = 1 集（信息密度低，60-90s 凑不满） | **2-3 章合并成 1 集**（信息密度凑足） |

**核心原则**：「改编是抽核心冲突重新重组，不是简单切割原文」——LLM 不再逐章镜化，而是先做**冲突密度判断**再决定拆/合。

### 现状（08-22）
- 入口：`step01_script_gen/main.py:1184` `read_chapter()` → `extract_section(path, NOVEL_CHAPTER=1)` 单章切片
- 切片粒度：机械按「第NNN章」标记切 1 章
- 输出：1 个 `chapter_XXX/script_result.json`（1 集）
- 「跨章」相关 grep 结果：全部指**资产/角色/音色跨章持久化**，**零** episode 业务代码
- 唯一沾边文字：`validation.py:8164`「合并同场景连续动作，或拆成多集」——**只是 LLM regen 文字建议**，无实际拆镜逻辑
- v6.38 红果 memory（`redguo-60-90s-episode-standard.md`）已标 C 方案为 future roadmap、预计 15+ 文件改动 1+ 月

### 实施分 3 阶段（渐进降低风险）

**阶段 1：LLM 能力验证（1-2 天）**
- [ ] 加 `extract_section_range(novel_path, start_chapter, end_chapter)`（`shared/chapter_reader.py`）—— 把 N 章文本拼成大段
- [ ] `step01_script_gen/main.py` 加 `--chapter-range 3-5` CLI flag → 把大段文本喂给 LLM
- [ ] prompt 加「**冲突密度判断**」规则：先判定本章属「密集/中等/水字数」→ 决定输出 1/2/3 集
- [ ] 输出仍为单 `script_result.json`（先验证 LLM 能不能按冲突重组）
- [ ] **关键验证**：跑 3 个对比 case（密集/中等/水字数各 1 章）→ 看 LLM 自决 N 集是否合理

**阶段 2：多集输出 + 路径改造（1 周）**
- [ ] step01 输出改为 `script_result.json` 含 `episodes: [{ep_idx, shots, hook, ...}]` 数组
- [ ] `shared/paths.py` 加 `episode_dir(chapter, ep_idx)` → `chapter_001/episode_001/` `episode_002/`
- [ ] step02-04 跟随 episode 维度输出（`vimax_working/episodes/<ep>/shots/<idx>/`、`audio/episodes/<ep>/`）
- [ ] 缓存键加入 episode 维度（kf 哈希、prompt 哈希）

**阶段 3：入口主键迁移（1-2 周）**
- [ ] 入口按「集」为主键（而非「章」）—— `NOVEL_EPISODE=2` / `--chapter-range 3-5 --episode 1` CLI
- [ ] 旧 `chapter_XXX/` 数据全量迁移到 `chapter_XXX/episode_001/`
- [ ] 全部缓存失效（中间帧图/视频）+ 重新跑
- [ ] C 方案文档化：v6.41 release notes

### 不在 C 方案范围
- ❌ 不动 v6.38 A 方案（1 章 = 1 集）—— 阶段 1 完成后 A/C 双轨运行，按需切换
- ❌ 不动资产/角色/音色跨章持久化（已稳定）
- ❌ 不破坏 step02-04 已有逻辑（只在路径层加 episode 维度）

### 关键风险

| 风险 | 缓解 |
|---|---|
| LLM 不能可靠判断「冲突密度」 | 阶段 1 先验证 3 个 case；不行则规则化（关键词计数：刺/杀/叫/破/击/血/尸 ≥3 = 密集） |
| 跨章角色名不一致（同一人物 2 章用了不同名） | character_extractor 跨章预提取（已有 v6.27 基础）+ 阶段 2 注入 LLM 提示 |
| 多集总时长不可控（5 集 × 90s = 450s > 红果单集标准） | 阶段 1 先验 LLM 选集数；如失控硬约束「总集数 ≤ 章数 × 1.5」 |
| 缓存全失效重生成成本高 | 阶段 1 不动缓存（仍单集输出），阶段 2 缓存键加 episode 维度避免全失效 |
| 旧 `chapter_XXX/` 数据迁移破坏 step02-04 已有产物 | 阶段 2 双写（旧路径 + 新路径并存），阶段 3 才切主键 |

### 触发条件（C 方案正式启动）
- A 方案跑通 2-3 个 chapter 效果稳定（v6.40 P18 闭环后）
- 用户明确要求「按冲突重组」而非「按章保留」
- 阶段 1 LLM 能力验证通过

---

## 归档说明

已完成项已从本清单移除，历史记录见：
- **[problems.md](problems.md)** — 问题记录（P1-P11：hook churn 教训 / LLM 输出丢失 / 模型不忠实渲染 / QC 失效 / kf 2→1 / 服装色收敛 / 校验误报 / qwen 遗留限制 / token 预估偏差 / 两套校验）
- 08-10 全流水线审计（A1-A7/B1-B10/C1-C5）已完成（commit b062c7b）
- 08-12 校验误报刷屏 P0/P1 修复 + prompt.py 精简（省 5.5k token）+ 第二轮校验反馈（v6.33）已完成
- 08-14 Plan A（hook 全锚点系统性修）代码已实施：schema/prompt/validation/A+B/方案1/Plan B/一致性
