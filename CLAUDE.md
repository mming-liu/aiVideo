# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

aiVideo 是一个 AI 驱动的短视频生成流水线，将中文小说文本转换为约 60 秒的抖音竖屏视频（古风权谋卧底风格）。整个流水线分为 4 个阶段，按顺序执行。

**⚠️ 编码规范：所有项目文件统一使用 UTF-8 编码，禁止使用 GBK 或其他编码。编辑文件时确保保存为 UTF-8。**

## 常用命令

### 安装依赖
```bash
pip install -r requirements.txt
# 系统还需安装 ffmpeg 并加入 PATH
```

### 运行流水线（按顺序）
```bash
python step01_script_gen/main.py  # 阶段1: 小说文本 → 分镜 JSON
python step02a_frame_gen/main.py  # 阶段2a: 分镜 → 首帧/尾帧/关键帧图片
python step02b_video_gen/main.py  # 阶段2b: 图片 → 视频片段
python step03_tts_audio/main.py   # 阶段3: 旁白文本 → 语音音频
python step04_merge/main.py       # 阶段4: 片段 + 音频 + 字幕 → 最终 MP4
```

- 阶段2拆分为2a和2b，方便调试：可以先运行2a检查图片质量，确认OK后再运行2b生成视频
- 每个阶段脚本都有交互式确认提示。
- 当 `VIDEO_REFERENCE_MODE=text` 时跳过分帧环节，直接文生视频；r2v 模式使用参考图（最多5个）保持角色/道具一致性

每个阶段脚本都有交互式确认提示。

### 章节提取调试
```bash
python chapter_reader.py 锦衣夜行_77119小说.txt
```
- 此脚本为 thin re-export 层，实际实现在 `shared/chapter_reader.py`
- `PROMPT_MAX_CHARS` 控制传递给 LLM 的原文上限（默认 50000，改 .env 生效）

### 环境变量配置（.env）
- `NOVEL_CHAPTER`: 章节序号（默认1）
- `NOVEL_NAME`: 小说名（小说级目录，全书共享库所在层）
- `BOOK_INDEX`: 部序号（小说内第几部，默认1）
- `BOOK_NAME`: 部名（拼入目录名，如 part_01_杀青州）
- `PROMPT_MAX_CHARS`: 传给 LLM 的原文上限（默认50000，传递完整章节）
- `SHOT_COUNT_MIN/MAX`: 分镜数范围（默认8-20，v4.1 起仅作 validation 软护栏，不进入 prompt）
- `VIDEO_PROVIDER`: 视频生成供应商（默认bailian）
- `IMAGE_PROVIDER`: 图片生成供应商（默认agnes）
- `TEXT_PROVIDER`: 文本生成供应商（默认deepseek）
- `TTS_PROVIDER`: TTS供应商（默认dashscope_sdk；step02 已不调用 TTS，对话由视频模型生成）
- `VIDEO_MODEL_VOICE`: 视频模型自带语音（默认1，对话/背景音交给视频模型；0=旧行为由 step03 TTS 提供）
- `HOOK_ENABLED`: 是否生成钩子分镜（默认1，开启）
- `COMPLIANCE_AUTO_FIX`: 是否自动修复敏感词（默认1，开启）
- `VIDEO_REFERENCE_MODE`: 视频参考图模式（默认text，可选image/r2v）— text=文生视频（跳过帧图片），image=图生视频（需先运行step02a生成帧图片），r2v=参考图生视频（最多5个参考图，角色肖像+场景/道具资产图）
- `SCENE_ASSET_EXTRACTION_ENABLED`: 是否提取场景资产（大场景/近景描述，默认1）
- `PROP_ASSET_EXTRACTION_ENABLED`: 是否提取道具资产（核心道具+外观描述，默认1）
- `ASSET_GEN_ENABLED`: 是否生成场景/道具资产图（step02a 帧生成前，默认1）
- `SCENE_NEAR_SHOT_ENABLED`: 是否生成近景功能场景图（从大场景派生，默认1）

## 架构设计

### 四阶段流水线
```
小说文本 (.txt)
    → step01_script_gen/main.py → script_result.json（分镜 + 旁白 + 角色 + 摄像机分组）
    → step02a_frame_gen/main.py  → 首帧/尾帧/关键帧图片（图生视频前导）
    → step02b_video_gen/main.py  → vimax_working/shots/<idx>/video.mp4
    → step03_tts_audio/main.py  → audio/narration.mp3
    → step04_merge/main.py      → final.mp4
```

- 阶段2拆分为2a（帧图片）和2b（视频生成），方便调试：可以先运行2a检查图片质量，确认OK后再运行2b
- 每个阶段脚本都有交互式确认提示
- `VIDEO_REFERENCE_MODE=text` 时跳过分帧环节，直接文生视频；r2v 模式使用参考图（最多5个）保持角色/道具一致性

### 输出目录结构（统一由 shared/paths.py 计算，禁止自行拼接）
层级对应小说自身结构「小说 → 部 → 章」：
```
{OUTPUT_BASE}/                              # 默认 ./output/video
└── 锦衣夜行/                                # {NOVEL_NAME} 小说级
    ├── global/                             # 全书共享（角色形象/风格跨部统一）
    │   ├── characters_bank/                # 角色注册表 + 肖像
    │   ├── style_bank/                     # 风格锚点（Ch1 锁定）
    │   ├── scenes_bank/                    # 场景资产（大场景图 + 近景功能场景图）
    │   ├── props_bank/                     # 道具资产（白底道具资产图）
    │   └── chapter_cache/                  # 章节文本解析缓存
    └── part_01_杀青州/                      # part_{BOOK_INDEX:02d}_{BOOK_NAME} 部级
        └── chapter_002/                    # chapter_{NOVEL_CHAPTER:03d} 章级
            ├── script_result.json / final.mp4 / subtitles.srt
            └── audio/ clips/ images/ vimax_working/ stretched/
```
- 2026-08 架构变更：合并改为唯一 native 模式（直接拼接保留视频原声），只输出 `final.mp4`，删除 final-A/final-B 双版本；对话/背景音由视频模型生成（wan2.7 系列有声视频），视频时长由分镜 `duration_sec` 字段驱动（step01 timecode 权威计算），step02 不再调用 TTS。

### 场景/道具资产（v7 新增）
四步资产生成方法论（`references/ai-scene-prop-asset-guide.md`）：
1. **大场景**：step01 提取场景的 `wide_desc`（大场景建立镜头描述）+ 地点/时间段/天气/色调
2. **近景功能场景**：同一场景的 `near_desc`（从大场景派生，保持季节/光线/年代一致）
3. **道具提取**：step01 提取核心道具的 `appearance`（材质/颜色/结构/尺寸/新旧痕迹）
4. **道具资产图**：step02a 生成白底道具资产图

数据流：
```
step01 asset_extractor.py（LLM 提取，schema: SCENE_ASSETS_SCHEMA / PROPS_SCHEMA）
    → shared/assets_bank.py（SceneAssetsBank / PropAssetsBank，global/scenes_bank/ props_bank/）
    → step02a asset_gen.py（横屏 1600x900 适配器，生成 wide.png/near.png/prop.png）
    → step02b prompt_builder._collect_r2v_references（场景资产图+角色肖像+道具资产图，≤5张）
```
- 资产图是**横屏 16:9 参考图**（不做竖屏约束），专用适配器 `size="1600x900"` + `skip_prefix` + `skip_sanitize`（与角色肖像一致）
- 大场景/近景图**禁止人物**（prompt 硬约束）；资产图跨章复用，bank 中已有图直接跳过
- r2v 参考图收集顺序：场景资产图（环境锚定）→ 出镜角色肖像 → 出镜道具资产图；空镜只收场景资产图

### Provider 抽象模式
`_video_gen/` 和 `tts/` 两个包采用相同架构：
- **ABC 接口 + 工厂注册表**：通过 `.env` 中的 PROVIDER 切换
- **Config 单例**：各包的 `config.py` 从 `.env` 加载
- **Dataclass 模型**：请求和结果用 dataclass 定义

### DeepSeek 文本生成（`_text_gen/`）
- **Responses API**（`deepseek-v4-flash`）：`POST /responses`，`reasoning.effort=none` 关闭思考 + strict `json_schema` 结构化输出
- **JSON Schema** 定义在 `_text_gen/deepseek_schema.py`（SCRIPT/HOOK/CHARACTERS/SCENE_ANALYSIS 四个）
- 请求结构：`text.format = {type: "json_schema", name, schema}`（漏 `type` 报 400）
- 正文从响应 `output[]` 的 `message.output_text` 提取；`status=incomplete` → `incomplete_details.reason` 诊断截断
- 非 v4-flash 模型回退 `chat.completions`

### 章节提取
- 小说文件：`锦衣夜行_77119小说.txt`（UTF-8编码；`.bak` 为 GBK 原件）
- 标题行格式：`第一部 杀青州 第001章 章节标题`（部名/章名由 `get_chapter_meta()` 解析并写入 script_result.json）
- 使用 `chapter_reader.py` 提取完整章节

## 关键参考文件
- `skill_spy.txt` — Prompt 工程参考
- `step01_script_gen/prompts.py` — 包含详细的分镜生成 prompt