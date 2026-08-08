---
name: seedance-prompt
description: "导演公式参考：Subject+Action+Scene+Camera+Lighting+Audio+Constraints（已被规则5.2覆盖，仅留2个增量文档化）"
license: MIT
user-invocable: false
tags: [aiVideo, prompt]
metadata:
  version: "1.0"
  synced-from: "seedance-2.0/skills/seedance-prompt"
  source-version: "6.6.0"
  updated: "2026-08-08"
---

# 导演公式（seedance-prompt）

> 已由 PROMPT_TEMPLATE 规则 5.2「7 步模板」实现。本文件仅记录 2 个增量，供参考。

## 导演公式 → 规则 5.2 映射
| 公式槽位 | 规则 5.2 步骤 |
|---|---|
| Subject | 第 4 步 人物分点 |
| Action | motion_desc |
| Scene | 第 2 步 环境 |
| Camera | 第 3 步 镜头参数 |
| Lighting/Style | 第 6 步 光影 |
| Audio | 规则 12.5 音效轨 |
| Constraints | 第 7 步 约束收尾 |

## 增量 1：压缩优先级
删 重复风格形容词 → 画质泛词 → 参考图已有背景 → 次要运镜 → 次要动作；
保留 主体/动作/相机/光/约束。支撑规则 13.6 的 ff_desc≤80 字。

## 增量 2：I2V 模式门
- **ff/lf 是给图片模型的**：只写静态画面、不写图片外信息（动作/运镜留给视频）
- **prompt 是给视频模型的**：只写动态/运镜/光变/音效
