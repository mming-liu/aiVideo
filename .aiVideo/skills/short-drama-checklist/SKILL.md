---
name: short-drama-checklist
description: "抠姐短剧剧本检查吸收：开篇3秒冲突/爆点密度≥1个/30秒/对白单句≤15字（规则42-43 + 3个 validation 检查）；动机/代入/主线/合规等软约束进 SYSTEM_PROMPT"
license: MIT
user-invocable: false
tags: [aiVideo, prompt, storyboard, script-review]
metadata:
  version: "1.0"
  updated: "2026-08-09"
  source: "抠姐短剧剧本检查提示词（7大维度）"
---

# 抠姐短剧剧本检查吸收（v6.26，5 点落地）

> 外部技能吸收惯例：规则进 `prompts.py`、检查进 `validation.py`、映射记录进本文件。
> 面向「AI 分镜生成前约束 + 生成后校验」两层；纯剧本评审类软约束（动机/代入/主线）
> 落 SYSTEM_PROMPT 作为生成导向，不做 keyword 校验（避免误伤）。

## 7 维度 → 落地映射

| 维度 | 吸收点 | 落地 |
|------|--------|------|
| 节奏 | 开篇3秒抓注意力 / 前10秒建立核心冲突 | 规则42 + validation `_check_opening_hook`（钩子含冲突词 + 前2镜禁纯环境开场 + 前1/3段建冲突） |
| 节奏 | 爆点密度 1个/30秒（短视频）、前段禁拖沓铺垫 | 规则42.5 + validation `_check_punch_density`（60s全片≥2爆点 + 前1/3段首个爆点） |
| 制作细节 | 台词口语化、单句≤15字 | 规则43 + validation `_check_dialogue_length`（分句标点拆句，说话人前缀不计入） |
| 制作细节 | 音画同步、字幕规范 | **已有覆盖**：规则0/1（subtitle=narration）+ `_check_grounding` + merge 字幕烧录，不重复 |
| 人物动机 / 代入感 / 故事主线 / 冲突张力 | 行为自洽、无上帝视角、主线因果、冲突触痛点 | **软约束**：SYSTEM_PROMPT 规则23（动作因果可视化）/ 3.5（先事件后镜头）/ 规则8（分镜连续性）已覆盖，不新增检查 |
| 合规性 | 色情/暴力/封建迷信/擦边/价值观 | **已有覆盖**：规则6（语义级合规）/ 规则15（替换词表）+ `scene_compliance` + `_check_platform_compliance` + `content_policy_fix`，不重复 |

## 新 validation 常量（`validation.py`，v6.26 块）

- `_PUNCH_WORDS`：爆点/冲突词（刺杀/对峙/反转/揭露/打脸/背叛/尖叫/质问/遇袭/惊觉…），
  供开篇检查与密度检查共用（与 `_check_narrative_arc_distribution` 的 `climax_keywords` 互补）
- `_DIALOGUE_SENTENCE_MAX` = 15（单句上限，规则43）
- `_DIALOGUE_SEP_RE`：对白分句标点（，。！？；…—、\n）
- `_DWELL_STATIC_HINTS`：静态/特写/空镜判定词（静坐/凝视/对视/特写/空镜/独坐…）
- `_DWELL_MIN_SEC` = 2.0（静态停留下限）；`_DWELL_DIALOG_RECOMMEND` = 3.0（对白建议值）

## 校验边界

- 全部检查 **warning-only**（不进 `hard_fail_checks`，重生成循环昂贵）
- 钩子镜跳过 `_check_dwell_time`（钩子为「前一刻」定格，时长由钩子 prompt 独立约束）
- 对白长度计数**忽略引号/空格/书名号**，只数汉字+字母数字；「说话人道：」前缀不计入
- 对白仍须逐字引用原文（规则0）——本检查只按原文标点断句，**不改写文字**
