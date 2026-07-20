---
name: generate-script
description: "将小说章节转化为视听同步的动漫电影级分镜脚本的核心合同，含分镜师知识库"
---

# 分镜生成

## 输入

- 小说章节文本（≤12000 字符）
- 已知角色库（跨章节一致性）

## 知识库（分镜决策参考）

以下 reference 文件提供分镜拆解的专业知识支持，生成前按需加载：
- [references/shot-language.md](references/shot-language.md) — 景别/运镜/构图词汇库
- [references/cinematography.md](references/cinematography.md) — 摄影技法库
- [references/director-styles.md](references/director-styles.md) — 导演风格库
- [references/art-styles.md](references/art-styles.md) — 美术风格库
- [references/montage-theory.md](references/montage-theory.md) — 蒙太奇理论
- [references/color-psychology.md](references/color-psychology.md) — 色彩心理学
- [references/film-genres.md](references/film-genres.md) — 影片类型库
- [references/veo-jimeng-guide.md](references/veo-jimeng-guide.md) — Veo/即梦双平台提示词最佳实践

分镜模板见 [template.md](template.md)。

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
- `shots[].ff_desc` — 首帧静态快照（80-120 字，纯静态，必须包含本镜头所有角色）
- `shots[].lf_desc` — 尾帧静态快照（small 时留空，必须包含本镜头所有角色）
- `shots[].motion_desc` — 帧间运动描述（50-100 字，纯过程）
- `shots[].context_bridge` — 视觉衔接描述（30-60 字，镜头 1 留空）

## 安全规则

1. 字幕-旁白精确一致：subtitle 必须与 narration_segment 逐字相同
2. **画面-旁白逐词绑定（最高优先级）**：prompt 的每个视觉元素（人物、动作、场景、物体、天气、光线）必须严格来自对应 narration_segment
   - narration 说"烈日烘烤"，prompt 必须有"烈日"高悬在画面中
   - narration 说"土地冒烟"，prompt 必须是"土地"在冒烟，不能是"树叶"
   - narration 说"赤膊娃娃奔跑"，只能出现娃娃，不能出现成人
   - **人物出现规则**：只有 narration 明确提到角色名或明确描述人物动作时，prompt 才能出现该人物
     - narration 只说"箫音飘荡"但没提是谁 → prompt 是空镜，禁止脑补人物
     - narration 说"听到脚步声"但没看到人 → 远景对向声源方向， 画面不应有人
3. 平台合规：禁止性描写/血腥细节，敏感词必须替换
4. 全弧线覆盖：6 镜均匀覆盖情节开头/中段/结尾
5. 固定前缀必须一致（见 style_prefix.py）
6. **首尾帧描述完整规则**：ff_Desc 和 lf_Desc 必须包含本镜头 prompt 中出现的所有角色
   - 镜头有 N 个角色，ff_Desc 和 lf_Desc 也必须出现 N 个角色
   - 示例：prompt 说"杨旭吹箫，听香撑伞"，ff_Desc 必须包含听香

## 可重写类别

以下类别可由伴生技能（`generate-script-local/SKILL.md`）覆盖或增强：

1. **视觉风格偏好** — 前缀短语、色调倾向、光影氛围约定
2. **角色外貌约定** — 描述粒度、五官/服饰/体型的描述风格
3. **镜头构图启发式** — 景别分布偏好、摄像机数量偏好、相邻镜头约束
4. **叙事节奏偏好** — 每镜字数范围、总旁白字数范围、duration 默认值
5. **合规词替换表** — 敏感词到替代词的映射（增量追加，不覆盖核心表）
