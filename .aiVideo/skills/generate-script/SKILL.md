---
name: generate-script
description: "将小说章节转化为视听同步的动漫电影级分镜脚本的核心合同"
---

# 分镜生成

## 输入

- 小说章节文本（≤8000 字符）
- 已知角色库（跨章节一致性）

## 输出 Schema

### 不可变字段
- `narration` — 所有 subtitle 的拼接体
- `negative_prompt` — 负面提示词
- `shots[].index` — 镜头序号
- `shots[].duration` — 镜头时长（秒）
- `shots[].narration_segment` — 该镜旁白原文摘句
- `shots[].subtitle` — 与 narration_segment 逐字相同
- `shots[].prompt` — 完整视觉 prompt（含前缀 + 景别 + 机位 + 运镜 + 主体 + 环境 + 色彩）

### 可变字段（摄像机连贯性体系，v3+）
- `character_profile` — 全局人物外貌总纲（50-80 字）
- `shots[].cam_idx` — 摄像机分组编号（1-3）
- `shots[].variation_type` — 变幅类型（small/medium/large）
- `shots[].ff_desc` — 首帧静态快照（80-120 字，纯静态）
- `shots[].lf_desc` — 尾帧静态快照（small 时留空）
- `shots[].motion_desc` — 帧间运动描述（50-100 字，纯过程）
- `shots[].context_bridge` — 视觉衔接描述（30-60 字，镜头 1 留空）

## 安全规则

1. 字幕-旁白精确一致：subtitle 必须与 narration_segment 逐字相同
2. 画面-旁白逐词绑定：prompt 的每个视觉元素必须来自对应 subtitle
3. 平台合规：禁止性描写/血腥细节，敏感词必须替换
4. 全弧线覆盖：6 镜均匀覆盖情节开头/中段/结尾
5. 固定前缀必须一致（见 style_prefix.py）

## 可重写类别

以下类别可由伴生技能（`generate-script-local/SKILL.md`）覆盖或增强：

1. **视觉风格偏好** — 前缀短语、色调倾向、光影氛围约定
2. **角色外貌约定** — 描述粒度、五官/服饰/体型的描述风格
3. **镜头构图启发式** — 景别分布偏好、摄像机数量偏好、相邻镜头约束
4. **叙事节奏偏好** — 每镜字数范围、总旁白字数范围、duration 默认值
5. **合规词替换表** — 敏感词到替代词的映射（增量追加，不覆盖核心表）
