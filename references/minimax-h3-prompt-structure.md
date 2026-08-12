# MiniMax H3 提示词六结构 参考（评估记录，不接入）

> 来源：抖音「MiniMax H3提示词创作指北」教程（2026-08 用户提供）。
> 结论：**只存参考，不进流水线**（用户决策 08-12）。本流水线为逐镜分解架构，6 大组件
> 均已有更细粒度覆盖；H3 六结构是 MiniMax 专属 API 的**单视频**提示词格式，fl2va/ref2va
> 是其 API 规范，与我们的 provider 抽象（bailian/agnes/seedance）不兼容。

## H3 官方六结构

1. **subject_definitions（主体定义）**：明确视频中出现的主体/角色/物体及其特征。
2. **summary（摘要）**：剧情梗概/故事摘要。
3. **retention_analysis（保留分析）**：分析观众留存——开头钩子/爆点/节奏安排。
4. **integrated_multimodal_description（整合多模态描述）**：画面 + 声音 + 运镜整合的
   连贯视觉描述（非单一模态堆砌）。
5. **overall_soundscape（整体声景）**：空间音景设计（环境声/音源方向/层次）。
6. **non_diegetic_music（非叙事音乐）**：背景配乐风格（不随画面内声源）。

## 与本流水线逐项对照

| H3 结构 | 本流水线覆盖 | 覆盖点 |
|---|---|---|
| subject_definitions | ✅ 更强 | `script.characters[]` static/dynamic features + CharacterBank 跨章持久化 + `[角色名:特征]` 锚点（prompt_builder.py:621-633 重建）+ 肖像参考图（r2v 注册表）|
| summary | ✅ | narration + outline_anchor（大纲两阶段，prompts.py build_prompt 注入）|
| retention_analysis | ✅ | hook 生成（gen_hook_script）+ 开篇冲突/爆点密度 warning 检查（validation._check_opening_hook / _check_punch_density）+ gap_analysis 反馈循环 |
| integrated_multimodal_description | ✅ 更细 | 逐镜 prompt：景别/机位/运镜/光位/材质/色彩/音效，比 H3 单视频结构更细粒度 |
| overall_soundscape | ✅ | `audio_cue` 三层结构化（人声/音效/背景音乐，v6.22 百炼声音公式，prompts.py:88/294-297），唯一消费点=prompt_builder.py:732 注入 `（背景音效：…）` |
| non_diegetic_music | ✅ | `audio_cue`「背景音乐=配乐+风格」层（prompts.py:297）|

## 不接入理由

1. **架构不同**：H3 六结构是给 MiniMax 自家 API 的**一整条视频**提示词；本流水线 step01
   生成 18-20 镜、每镜独立 prompt + ff/lf/keyframes 三帧 + r2v 参考图，控制粒度细得多。
2. **fl2va/ref2va 是 API 规范**：与我们的 provider 抽象（视频负向词/num_frames/参考图
   各自适配）不兼容，无落地价值。
3. **唯一潜在增量（声景/配乐显式化）已存在**：audio_cue 三层结构已含背景音乐层；且视频
   provider（wan2.7 系列有声视频）已在 prompt 文本层消费 audio_cue。若未来要显式分离
   「环境音 vs 配乐」两条 prompt 线，可参考 H3 的 soundscape/music 二分，但那只是措辞
   整理，不需要新字段。

## 教程附带的三条生成路径（不适用，记录备查）

- 工具直接调用（ClaudeCode/Codex 装 h3-prompt-writing 技能）→ 本流水线不用 MiniMax。
- 大模型对话生成（把规范投喂给豆包等）→ 与本流水线自动生成不冲突，人工外挂场景可选。
- ComfyUI 工作流（TextGenerate 节点）→ 与本流水线无关。
