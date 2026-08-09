---
name: video-shotcraft
description: "video-shotcraft（Vincentwei1021）吸收评估：静态镜头停留≥2s（规则44 + _check_dwell_time）；104镜头配方卡/Remotion/音效库与现有运镜库重叠、不适用，记录排除项"
license: MIT
user-invocable: false
tags: [aiVideo, prompt, storyboard, video]
metadata:
  version: "1.0"
  updated: "2026-08-09"
  source: "Vincentwei1021/video-shotcraft（Codex/Claude-Code 分镜编排 Skill）"
---

# video-shotcraft 吸收评估（v6.26，1 落地 + 4 排除）

> 该项目是**分镜编排 Skill**（输出结构化分镜 + Seedance/Wan2.7 prompt 或 Remotion TSX），
> 不直接渲染视频。与 aiVideo 现有能力对照后仅 1 点新落地，其余由既有机制覆盖或不适配。

## 吸收点 → 落地映射

| # | 资产 | 评估 | 落地 |
|---|------|------|------|
| 1 | 关键画面停留时长（审美校验：关键画面停留过短） | **落地** | 规则44 + validation `_check_dwell_time`（静态/对白镜头 ≥2s，对白建议 3-5s） |

## 排除项（记录不重复吸收）

| # | 资产 | 排除原因 |
|---|------|----------|
| 2 | 104 张镜头配方卡（推近/环绕/悬浮/飞入/聚焦/转场） | **已被现有运镜库覆盖**：`seedance-shot-angles`（40组视角）+ `advanced-camera-movement`（6组高级运镜）+ `situation-camera`（18种情景运镜）+ `video-transition`（转场）；已有 `_check_camera_movement`/`_check_camera_upgrade`/`_check_transition_keywords` |
| 3 | Remotion React TSX 代码渲染 | **不适配**：aiVideo 用 AI 视频模型逐镜生成真实视频，无本地 Node/Remotion 渲染需求 |
| 4 | 149 套分类音效库（whoosh/riser/impact/sparkle 卡点） | **已被覆盖**：`audio_cue` 三层结构化（v6.22 百炼声音公式：人声/音效/背景音乐）+ `seedance-audio` 增量3（音效具体命名）；shotcraft 按时间轴卡点对齐 → 已在规则12.5 时长分配覆盖，不新增 |
| 5 | 字幕最小尺寸/灯光约束（审美校验） | **不适用**：字幕由 step04 后期烧录（字幕最小尺寸归 merge 字体控制，不进画面 prompt）；灯光已由规则27（8种人像光位）+ `LIGHTING_LAYERS` + `_check_lighting_position` 覆盖 |

## 避坑

- shotcraft 的镜头配方卡名要求大小写/横杠严格匹配——本项目**未采纳**卡片名机制，
  运镜用现有 SKILL 词库自然语言描述，避免多一套命名体系造成 LLM 记忆负担。
- shotcraft 不做内容审核敏感词过滤——aiVideo 由 `scene_compliance`/规则6/规则15 +
  `content_policy_fix` 自行处理，与 shotcraft 无关。
