---
name: update-script
description: "从人类对 script_result.json 的编辑中学习，更新分镜生成伴生技能"
---

# 分镜自进化

## Write surface

仅允许写入 `.aiVideo/skills/generate-script-local/SKILL.md`

## 工作流

1. 获取 `script_result.json` 的 git diff（自上次 commit 以来的修改）
2. 解析 diff，提取人类修改的字段和模式
3. 聚合信号：同一类型的修改在 diff 中出现 ≥2 次 → 视为学习信号
4. 生成对 `generate-script-local/SKILL.md` 的最小增量更新
5. 运行 write surface 校验（确保只写伴生文件）
6. 将更新写入伴生文件，输出变更摘要

## 学习字段

仅从以下字段中学习（这些是有视觉偏好的描述性字段）：
- `shots[].ff_desc` — 首帧静态快照
- `shots[].lf_desc` — 尾帧静态快照
- `shots[].motion_desc` — 帧间运动描述
- `shots[].context_bridge` — 视觉衔接描述
- `character_profile` — 人物外貌总纲
- `shots[].cam_idx` — 摄像机分配
- `shots[].variation_type` — 变幅类型

## 不学习的字段

以下字段是原文摘句或拼装产物，无视觉偏好信息：
- `narration_segment` / `subtitle` / `narration` — 纯文学摘句
- `prompt` — 拼装产物，源信息在其他字段
- `negative_prompt` — 固定模板
- `duration` — 功能性字段

## 证据规则

- 至少同一模式出现 ≥2 次才写入（过滤一次性偏好/错误修复）
- 同一类别的信号才聚合（比如全是"ff_desc 长度增加"）
- 新写入的规则不能与核心合同冲突

## 学习示例

| 人类编辑模式 | 出现次数 | → 写入伴生文件 |
|---|---|---|
| 把 ff_desc 从 40 字扩到 70+ 字 | ≥3 | 叙事节奏：ff_desc 建议 60-80 字 |
| 把"远景"改成"全景" | ≥2 | 构图启发式：偏好全景开场 |
| 把 cam_idx 从 1,1,2,2,3,3 改为 1,2,2,3,3,3 | 1 | 不学习（仅 1 次） |
| 在 context_bridge 中频繁出现"光影从 X 转为 Y" | ≥2 | 衔接描述偏好：包含光影过渡 |
