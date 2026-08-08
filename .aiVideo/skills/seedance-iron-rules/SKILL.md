---
name: seedance-iron-rules
description: "v6.24 分镜生产铁律吸收（seedance-prompts-skill 铁律 3/7/8/12 + capsule-cinema + spark-video 的 10 点落地映射表）"
license: MIT
user-invocable: false
tags: [aiVideo, prompt, storyboard]
metadata:
  version: "1.0"
  updated: "2026-08-08"
  sources:
    - "shyman159/seedance-prompts-skill (12 铁律)"
    - "JuneYaooo/capsule-cinema references/storyboard-craft.md"
    - "JohnKeating1997/spark-video lib/lore.py mood_anchor"
---

# seedance 生产铁律吸收（v6.24，10 点）

> 外部技能吸收遵循惯例：规则进 `prompts.py`、检查进 `validation.py`、运行时进
> `prompt_builder.py`。本文件记录源→落地映射，供未来改动追溯，不再重复展开内容。

## 10 点 → 落地映射

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

## 新 validation 常量（`validation.py` `_COMMON_NEGATIVE_PROMPTS` 后）

- `_DEFORMATION_VERBS`：掰断/撕开/劈开/折断/剥开/捏碎/砸碎/摔碎…
- `_WIDE_SHOT_WORDS`：远景/大远景/全景/俯拍/航拍/俯瞰/鸟瞰/广角
- `_FINE_FACE_WORDS`：剑眉/星目/丹凤眼/柳叶眉/朱唇/五官/睫毛/发簪…
- `_HAND_EXCHANGE_WORDS`：递/接/交/换/奉上/呈上/交出…
- `_HAND_LOCK_WORDS`：手部/左手/右手/手中/指间/双手…

（prompt_builder 自持一份 `_WIDE_SHOT_WORDS`，避免跨模块耦合。）

## 新 prompt_builder helper

- `_shorten_feature(feat, max_len)`：特征压缩保留开头（服装主色/剪影）
- `_is_wide_shot(shot)`：远景/俯拍/航拍镜头探测

## 校验边界

- 全部 10 点**零 schema 改动**（复用 `color_tone`/`static_features`）
- 全部 4 个新 validation check **warning-only**（不进 `hard_fail_checks`，重生成循环昂贵）
- 点 3（图N 索引）是唯一 MED-HIGH 风险改动：修改了「图N」语义，务必核对生成日志
