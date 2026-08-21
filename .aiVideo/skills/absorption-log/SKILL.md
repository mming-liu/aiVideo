---
name: absorption-log
description: "aiVideo 外部技能吸收评估总日志：按源记录吸收点、排除项、落地映射与校验边界"
license: MIT
user-invocable: false
tags: [aiVideo, prompt, storyboard, absorption-log]
metadata:
  version: "1.0"
  updated: "2026-08-19"
  sources:
    - "shyman159/seedance-prompts-skill (12 铁律 + 2026.05 新版增量)"
    - "JuneYaooo/capsule-cinema references/storyboard-craft.md"
    - "JohnKeating1997/spark-video lib/lore.py mood_anchor"
    - "抠姐短剧剧本检查提示词（7 大维度）"
    - "Vincentwei1021/video-shotcraft（Codex/Claude-Code 分镜编排 Skill）"
---

# aiVideo 外部技能吸收评估总日志

> 外部技能吸收惯例：规则进 `prompts.py`、检查进 `validation.py`、运行时进
> `prompt_builder.py`、映射记录进本文件。
> 全部新 validation 检查均为 warning-only（不进 hard_fail_checks，重生成循环昂贵）。

## 总览

| # | 源 | 版本 | 落地数 | 排除/已有覆盖数 | 日期 |
|---|----|------|--------|-----------------|------|
| 1 | seedance-prompts-skill + capsule-cinema + spark-video | v6.24 | 10 | 1 未采纳 | 2026-08-08 |
| 2 | 抠姐短剧剧本检查提示词 | v6.26 | 5 | 2 已有覆盖 + 1 软约束 | 2026-08-09 |
| 3 | Vincentwei1021/video-shotcraft | v6.26 | 1 | 4 | 2026-08-09 |
| 4 | seedance-prompts-skill 2026.05 新版增量 | v6.38 | 4 | 0 | 2026-08-21 |

## 4. seedance 2026.05 新版铁律增量（v6.38，4 落地）

> 来源：shyman159/seedance-prompts-skill 2026-05-15+ 升级版
> （新增「沉浸式短片八维黄金公式」「防崩神词」「排版规则」「情绪具象化表」等）
>
> 本次只吸收低成本高价值 4 点；其他（八维公式/触发关键词大全/构图7种/光位8种）
> 已在 SKILL v6.24 + 项目 camera-mastery/portrait-mastery 中覆盖。

### 4 点 → 落地映射

| # | 吸收点 | 来源 | 落地 |
|---|--------|------|------|
| 1 | 主观视角必须放句首 | seedance 铁律 11 | 规则 55：ff_desc/lf_desc/prompt 用 POV 时必须「第一人称视角/POV/主观视角」放句首 + 角色呼吸/眨眼/伸手 |
| 2 | 防崩神词 + 排版规则 | seedance 铁律 9 | 规则 56：①完整输出 ②末尾追加「画面稳定·不闪烁·不重影·不模糊·动作流畅自然·不卡顿·不断裂·光影细腻·高清画质·电影质感」选 3-5 个 ③禁「————」长破折号 |
| 3 | 情绪具象化表 | seedance 铁律 12 增量 | 规则 57：5 类情绪（悲伤/喜悦/紧张/愤怒/释然）的具体身体外化动作（与 28/52 链式表演规则互补） |
| 4 | 长破折号校验 | seedance 铁律 9 排版 | validation `_check_long_dash_usage` v6.38（warning-only，≥3 个 —— 触发） |

### 合规收紧（2026.05 审核更新）

- 规则 6 补充：「15-17岁少女」改「年轻女孩/年轻女子」；「裸妆」改「清新妆」；「撒娇/嘟嘴」改中性表情
- 禁止负面词前缀（"严禁/禁止 XXX"）一律改正向描述

### 未采纳（记录不重复吸收）

| # | 资产 | 排除原因 |
|---|------|----------|
| 1 | 八维黄金公式 | 已被现有 PROMPT_TEMPLATE 7 步结构覆盖（规则 5.2） |
| 2 | 16 种运镜 + 情绪映射 | 已在 camera-mastery 6 组合 + situation-camera 18 种覆盖 |
| 3 | 8 种光型 + 情绪映射 | 已在 portrait-lighting 8 种 + 规则 27 覆盖 |
| 4 | 7 种构图 + 触发关键词 | 已有部分（前景/中心/三分/对角/对称/引导线/框架），无需重复 |
| 5 | 时长估算公式 `对白句数 × 1.3s + 画面节拍数 × 0.8-1.5s` | 已有 prompt_length_budget 校验；公式较新，暂不嵌入校验层 |
| 6 | 官方修正「镜头N」替代「0-X秒」 | 我们的项目 16.4 规则仍用「前2秒/2-4秒」格式（沉浸式短片对口型同步场景，官方允许保留秒数标注——见 seedance-prompt-guide.md §0.4） |

---

## 1. seedance 生产铁律吸收（v6.24，10 点）

> 来源：shyman159/seedance-prompts-skill（12 铁律）+ JuneYaooo/capsule-cinema + JohnKeating1997/spark-video
>
> 本文件记录源→落地映射，供未来改动追溯，不再重复展开内容。

### 10 点 → 落地映射

| # | 吸收点 | 来源 | 落地 |
|---|--------|------|------|
| 1 | 画面无字幕声明 | seedance 铁律3 | 规则40 + prompt_builder `_build_prompt` parts 首元素 + validation `_check_subtitle_leak` |
| 2 | 物体可分性（掰断→摔/取/递） | seedance 铁律7 | 规则35 + validation `_check_object_separability`（`_DEFORMATION_VERBS`） |
| 3 | r2v 图N 编号=媒体索引 | spark-video 适配核对 | prompt_builder `_build_r2v_purpose_list` 图N 改 `idx+=1`（与 happyhorse `[Image N]` 及 provider media 顺序一致） |
| 4 | Mood anchor（每镜色彩锚点） | spark-video `lore.mood_anchor` | prompt_builder step 8b 复用 `color_tone` 加【色彩氛围】（零 schema 改动） |
| 5 | 同场景镜头合并 + 时长公式 | capsule storyboard-craft | 规则36 真跳切计数 + main.py `_regen_over_limit` 反馈强化；**时长公式已由 timecode `_estimate_duration` 覆盖（字数/语速+标点缓冲），未重复实现** |
| 6 | 多角色短锚点（每人 20-30 字） | capsule | prompt_builder `_shorten_feature(25)`（≥3 人非主角截断） |
| 7 | 远景人物省略（禁五官） | capsule | 规则39 + prompt_builder `_is_wide_shot`→15字剪影 + validation `_check_wide_shot_facial`（`_WIDE_SHOT_WORDS`/`_FINE_FACE_WORDS`） |
| 8 | 词序优先级 + 抽象词量化 | seedance 铁律12 | 规则37（主体→光影→抽象词 + 量化模板） |
| 9 | 双人递物手部锁定 | seedance 铁律8 | 规则38 + validation `_check_hand_lock`（`_HAND_EXCHANGE_WORDS`/`_HAND_LOCK_WORDS`） |
| 10 | Shot kind 比例（70/25/5） | spark-video | **未采纳逐镜混合**（架构改动过大），`.env.content` 文档指引 r2v 为默认（≈70%+） |

### 新 validation 常量（`validation.py` `_COMMON_NEGATIVE_PROMPTS` 后）

- `_DEFORMATION_VERBS`：掰断/撕开/劈开/折断/剥开/捏碎/砸碎/摔碎…
- `_WIDE_SHOT_WORDS`：远景/大远景/全景/俯拍/航拍/俯瞰/鸟瞰/广角
- `_FINE_FACE_WORDS`：剑眉/星目/丹凤眼/柳叶眉/朱唇/五官/睫毛/发簪…
- `_HAND_EXCHANGE_WORDS`：递/接/交/换/奉上/呈上/交出…
- `_HAND_LOCK_WORDS`：手部/左手/右手/手中/指间/双手…

（prompt_builder 自持一份 `_WIDE_SHOT_WORDS`，避免跨模块耦合。）

### 新 prompt_builder helper

- `_shorten_feature(feat, max_len)`：特征压缩保留开头（服装主色/剪影）
- `_is_wide_shot(shot)`：远景/俯拍/航拍镜头探测

### 校验边界

- 全部 10 点**零 schema 改动**（复用 `color_tone`/`static_features`）
- 全部 4 个新 validation check **warning-only**（不进 `hard_fail_checks`，重生成循环昂贵）
- 点 3（图N 索引）是唯一 MED-HIGH 风险改动：修改了「图N」语义，务必核对生成日志

---

## 2. 抠姐短剧剧本检查吸收（v6.26，5 点落地）

> 来源：抠姐短剧剧本检查提示词（7 大维度）
>
> 面向「AI 分镜生成前约束 + 生成后校验」两层；纯剧本评审类软约束（动机/代入/主线）
> 落 SYSTEM_PROMPT 作为生成导向，不做 keyword 校验（避免误伤）。

### 7 维度 → 落地映射

| 维度 | 吸收点 | 落地 |
|------|--------|------|
| 节奏 | 开篇3秒抓注意力 / 前10秒建立核心冲突 | 规则42 + validation `_check_opening_hook`（钩子含冲突词 + 前2镜禁纯环境开场 + 前1/3段建冲突） |
| 节奏 | 爆点密度 1个/30秒（短视频）、前段禁拖沓铺垫 | 规则42.5 + validation `_check_punch_density`（60s全片≥2爆点 + 前1/3段首个爆点） |
| 制作细节 | 台词口语化、单句≤15字 | 规则43 + validation `_check_dialogue_length`（分句标点拆句，说话人前缀不计入） |
| 制作细节 | 音画同步、字幕规范 | **已有覆盖**：规则0/1（subtitle=narration）+ `_check_grounding` + merge 字幕烧录，不重复 |
| 人物动机 / 代入感 / 故事主线 / 冲突张力 | 行为自洽、无上帝视角、主线因果、冲突触痛点 | **软约束**：SYSTEM_PROMPT 规则23（动作因果可视化）/ 3.5（先事件后镜头）/ 规则8（分镜连续性）已覆盖，不新增检查 |
| 合规性 | 色情/暴力/封建迷信/擦边/价值观 | **已有覆盖**：规则6（语义级合规）/ 规则15（替换词表）+ `scene_compliance` + `_check_platform_compliance` + `content_policy_fix`，不重复 |

### 新 validation 常量（`validation.py`，v6.26 块）

- `_PUNCH_WORDS`：爆点/冲突词（刺杀/对峙/反转/揭露/打脸/背叛/尖叫/质问/遇袭/惊觉…），
  供开篇检查与密度检查共用（与 `_check_narrative_arc_distribution` 的 `climax_keywords` 互补）
- `_DIALOGUE_SENTENCE_MAX` = 15（单句上限，规则43）
- `_DIALOGUE_SEP_RE`：对白分句标点（，。！？；…—、\n）
- `_DWELL_STATIC_HINTS`：静态/特写/空镜判定词（静坐/凝视/对视/特写/空镜/独坐…）
- `_DWELL_MIN_SEC` = 2.0（静态停留下限）；`_DWELL_DIALOG_RECOMMEND` = 3.0（对白建议值）

### 校验边界

- 全部检查 **warning-only**（不进 `hard_fail_checks`，重生成循环昂贵）
- 钩子镜跳过 `_check_dwell_time`（钩子为「前一刻」定格，时长由钩子 prompt 独立约束）
- 对白长度计数**忽略引号/空格/书名号**，只数汉字+字母数字；「说话人道：」前缀不计入
- 对白仍须逐字引用原文（规则0）——本检查只按原文标点断句，**不改写文字**

---

## 3. video-shotcraft 吸收评估（v6.26，1 落地 + 4 排除）

> 来源：Vincentwei1021/video-shotcraft（Codex/Claude-Code 分镜编排 Skill）
>
> 该项目是**分镜编排 Skill**（输出结构化分镜 + Seedance/Wan2.7 prompt 或 Remotion TSX），
> 不直接渲染视频。与 aiVideo 现有能力对照后仅 1 点新落地，其余由既有机制覆盖或不适配。

### 吸收点 → 落地映射

| # | 资产 | 评估 | 落地 |
|---|------|------|------|
| 1 | 关键画面停留时长（审美校验：关键画面停留过短） | **落地** | 规则44 + validation `_check_dwell_time`（静态/对白镜头 ≥2s，对白建议 3-5s） |

### 排除项（记录不重复吸收）

| # | 资产 | 排除原因 |
|---|------|----------|
| 2 | 104 张镜头配方卡（推近/环绕/悬浮/飞入/聚焦/转场） | **已被现有运镜库覆盖**：`seedance-shot-angles`（40组视角）+ `advanced-camera-movement`（6组高级运镜）+ `situation-camera`（18种情景运镜）+ `video-transition`（转场）；已有 `_check_camera_movement`/`_check_camera_upgrade`/`_check_transition_keywords` |
| 3 | Remotion React TSX 代码渲染 | **不适配**：aiVideo 用 AI 视频模型逐镜生成真实视频，无本地 Node/Remotion 渲染需求 |
| 4 | 149 套分类音效库（whoosh/riser/impact/sparkle 卡点） | **已被覆盖**：`audio_cue` 三层结构化（v6.22 百炼声音公式：人声/音效/背景音乐）+ `seedance-audio` 增量3（音效具体命名）；shotcraft 按时间轴卡点对齐 → 已在规则12.5 时长分配覆盖，不新增 |
| 5 | 字幕最小尺寸/灯光约束（审美校验） | **不适用**：字幕由 step04 后期烧录（字幕最小尺寸归 merge 字体控制，不进画面 prompt）；灯光已由规则27（8种人像光位）+ `LIGHTING_LAYERS` + `_check_lighting_position` 覆盖 |

### 避坑

- shotcraft 的镜头配方卡名要求大小写/横杠严格匹配——本项目**未采纳**卡片名机制，
  运镜用现有 SKILL 词库自然语言描述，避免多一套命名体系造成 LLM 记忆负担。
- shotcraft 不做内容审核敏感词过滤——aiVideo 由 `scene_compliance`/规则6/规则15 +
  `content_policy_fix` 自行处理，与 shotcraft 无关。

---

## 附录：全部新增常量索引

### validation.py

| 常量 | 来源章节 | 用途 |
|------|----------|------|
| `_DEFORMATION_VERBS` | §1 铁律7 | 物体可分性检查 |
| `_WIDE_SHOT_WORDS` | §1 capsule | 远景探测 |
| `_FINE_FACE_WORDS` | §1 capsule | 远景五官精细词检查 |
| `_HAND_EXCHANGE_WORDS` | §1 铁律8 | 递物场景判定 |
| `_HAND_LOCK_WORDS` | §1 铁律8 | 手部锁定检查 |
| `_PUNCH_WORDS` | §2 抠姐短剧 | 爆点/冲突词（开篇 + 密度共用） |
| `_DIALOGUE_SENTENCE_MAX` = 15 | §2 抠姐短剧 规则43 | 单句对白长度上限 |
| `_DIALOGUE_SEP_RE` | §2 抠姐短剧 | 对白分句标点 |
| `_DWELL_STATIC_HINTS` | §2/§3 | 静态镜头判定词 |
| `_DWELL_MIN_SEC` = 2.0 | §3 shotcraft 规则44 | 静态停留下限 |
| `_DWELL_DIALOG_RECOMMEND` = 3.0 | §3 shotcraft 规则44 | 对白停留建议值 |

### prompt_builder.py

| 函数/常量 | 来源章节 | 用途 |
|-----------|----------|------|
| `_shorten_feature(feat, max_len)` | §1 capsule | 多角色特征压缩 |
| `_is_wide_shot(shot)` | §1 capsule | 远景镜头探测 |
