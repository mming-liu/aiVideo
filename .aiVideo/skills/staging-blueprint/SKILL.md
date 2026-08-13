---
name: staging-blueprint
description: "v6.35 多人站位蓝图吸收（抖音 AIGC视觉笔记「导演站位台」→ 结构化 char_layout 字段 + 规则51 + validation 检查）"
license: MIT
user-invocable: false
tags: [aiVideo, prompt, storyboard, layout]
metadata:
  version: "1.0"
  updated: "2026-08-13"
  sources:
    - "抖音 @AIGC视觉笔记「站位功能/导演站位台」（自定义人物布局/机位视角/色块图导出）"
---

# 多人站位蓝图吸收（v6.35，站位台 → 结构化站位）

> 外部技能吸收遵循惯例：规则进 `prompts.py`、schema 进 `deepseek_schema.py`、
> 检查进 `validation.py`、运行时进 `prompt_builder.py` / `vimax_bridge.py`。
> 本文件记录源→落地映射，供未来改动追溯。
>
> 吸收取舍：**不接 GUI 拖拽/色块图导出**（与全自动文字→视频流水线不兼容，逐镜人工
> 摆位不可行）；只吸收其核心思想——把抽象空间关系变成**确定性站位蓝图**，以
> 结构化 `char_layout` 字段承载，治「站位混乱 / 比例失衡」两个已知痛点。

## 源→落地映射

| # | 吸收点 | 来源（站位台） | 落地 |
|---|--------|----------------|------|
| 1 | 多人物位置关系确定性控制 | 站位台可拖拽摆位 | `shot.char_layout` 结构化字段（name/position/facing/role），schema 定义见 `deepseek_schema.py` `_SHOT` |
| 2 | 大场面/多人布局模板 | 站位台预设布局 | 规则51「多人站位蓝图」：对峙型/群像型/比例层级锚定，见 `prompts.py` SYSTEM_PROMPT 末 |
| 3 | 参数化精准控制 | 站位台参数面板 | prompt_builder `_build_character_layout_block` 结构化优先分支（直读 position/facing，文本探测兜底） |
| 4 | 站位落地到帧 | 站位台机位画面 | vimax_bridge `_extract_char_layout` 结构化优先分支（char_layout 提供位置/朝向，文本扫描提供姿态） |
| 5 | 布局合规校验 | 站位台导出可核对 | validation `_check_multi_person_staging`（缺布局/编造角色/同侧拥挤，warning-only） |

## 三张站位蓝图（规则51 正文）

1. **对峙型（双人对立/过肩/正反打）**：A 固定画左、B 固定画右（或反之），隔物对坐/对站、朝向相对；威胁源在其目标方向一侧（配合规则41 视线耦合，多人视线汇成闭合三角）。
2. **群像型（3人及以上同框）**：主位居画中靠前（role=主），其余按画左/画右/画中扇形展开，禁止全挤同侧；前中后景拉开纵深（前景半身、中景全身、后景剪影），配 role=次/背景。
3. **比例/层级锚定（治比例失衡）**：前景主体画大、后景陪体画小，相对尺寸符合规则25 物体容量（船上几人就几人、不超额）；禁止「两人近景同大」的透视扁平。

## char_layout 字段契约

```json
"char_layout": [
  {"name": "杨旭", "position": "画左前景", "facing": "面向画右的听香", "role": "主"},
  {"name": "听香", "position": "画右中景", "facing": "面向画左的杨旭", "role": "次"}
]
```

- `name` 须与顶层 `characters.identifier` 一致（校验 #2 拦截编造路人）
- `position` 值域：画左/画右/画中/前景/中景/后景（可组合）；`facing` 值域：面向画左/画右/镜头/背对/面向[角色]
- `role` 值域：主/次/背景
- 空镜填 `[]`；每个出镜角色必须写入（规则51 要求 + 校验 #1 兜底）
- 可选字段（不在 `_SHOT.required`），旧缓存脚本缺字段时消费端一律 `.get("char_layout") or []` 兜底

## 校验边界

- `char_layout` 为可选字段：schema 不强制（strict json_schema 允许可选属性输出），靠规则51 强提示 + `_check_multi_person_staging` 缺布局 WARN 双保险
- 新 check **warning-only**（不进 `hard_fail_checks`，与 v6.24/v6.25 惯例一致）
- 触发门控：多人同框（文本探测 ≥2 角色）才查缺布局，空镜/单角色不触发，防误报刷屏
- 消费端改动均「结构化优先 + 文本扫描兜底」，旧脚本无 char_layout 时行为与改动前一致
- 同步改动 `prompts.py` 使 `_source_hash` 失效 → 所有章节缓存需重跑 step01（预期，旧脚本本就无蓝图约束）
