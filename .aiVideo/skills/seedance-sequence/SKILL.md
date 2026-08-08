---
name: seedance-sequence
description: "事件密度参考：三分桶（已发生/仅本镜/留待后续）+ 一镜一事件（已被规则3/3.5/4覆盖）"
license: MIT
user-invocable: false
tags: [aiVideo, sequence]
metadata:
  version: "1.0"
  synced-from: "seedance-2.0/skills/seedance-sequence + references/event-density"
  source-version: "6.6.0"
  updated: "2026-08-08"
---

# 事件密度（seedance-sequence / event-density）

> 已由规则 3/3.5/4（内容主次/单镜一动作/全弧线覆盖）实现。仅文档化事件密度三分桶。

## 事件三分桶
- **already_happened（已发生）**：上一镜 lf 承接，不重放（规则24）
- **this_clip_only（仅本镜）**：本镜执行的动作（规则 3.5 一镜一动作）
- **reserved_for_later（留待后续）**：保留给打斗反转/大招（规则 30 四段式）

## 一镜一事件
一次生成通常只承载一个可见节拍 + 一个变化终点。长背景/多地点/多动作/密集对话属于序列规划，不属于当前 prompt。
