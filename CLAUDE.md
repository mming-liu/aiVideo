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
- `SHOT_COUNT_MIN/MAX`: 分镜数范围（默认8-20）
- `VIDEO_PROVIDER`: 视频生成供应商（默认agnes）
- `IMAGE_PROVIDER`: 图片生成供应商（默认agnes）
- `TEXT_PROVIDER`: 文本生成供应商（默认deepseek）
- `TTS_PROVIDER`: TTS供应商（默认dashscope_sdk）
- `HOOK_ENABLED`: 是否生成钩子分镜（默认1，开启）
- `COMPLIANCE_AUTO_FIX`: 是否自动修复敏感词（默认1，开启）
- `VIDEO_REFERENCE_MODE`: 视频参考图模式（默认text，可选image/r2v）— text=文生视频（跳过帧图片），image=图生视频（需先运行step02a生成帧图片），r2v=参考图生视频（最多5个参考图，角色/道具一致性）

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
    │   └── chapter_cache/                  # 章节文本解析缓存
    └── part_01_杀青州/                      # part_{BOOK_INDEX:02d}_{BOOK_NAME} 部级
        └── chapter_002/                    # chapter_{NOVEL_CHAPTER:03d} 章级
            ├── script_result.json / final-A.mp4 / final-B.mp4 / subtitles.srt
            └── audio/ clips/ images/ vimax_working/ stretched/
```

### Provider 抽象模式
`_video_gen/` 和 `tts/` 两个包采用相同架构：
- **ABC 接口 + 工厂注册表**：通过 `.env` 中的 PROVIDER 切换
- **Config 单例**：各包的 `config.py` 从 `.env` 加载
- **Dataclass 模型**：请求和结果用 dataclass 定义

### 章节提取
- 小说文件：`锦衣夜行_77119小说.txt`（UTF-8编码；`.bak` 为 GBK 原件）
- 标题行格式：`第一部 杀青州 第001章 章节标题`（部名/章名由 `get_chapter_meta()` 解析并写入 script_result.json）
- 使用 `chapter_reader.py` 提取完整章节

## 关键参考文件
- `skill_spy.txt` — Prompt 工程参考
- `step01_script_gen/prompts.py` — 包含详细的分镜生成 prompt