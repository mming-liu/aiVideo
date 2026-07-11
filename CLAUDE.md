# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

aiVideo 是一个 AI 驱动的短视频生成流水线，将中文小说文本转换为约 60 秒的抖音竖屏视频（古风权谋卧底风格）。整个流水线分为 4 个阶段，按顺序执行。

## 常用命令

### 安装依赖
```bash
pip install -r requirements.txt
# 系统还需安装 ffmpeg 并加入 PATH
```

### 运行流水线（按顺序）
```bash
python step01_script_gen/main.py  # 阶段1（新版入口）：小说文本 → 分镜 JSON
# python 01_script_gen.py         # 旧版入口（已弃用，仅作向后兼容包装）
python 02b_vimax_gen.py      # 阶段2: 分镜 → 视频片段（首帧锚定、尾帧生成）
python 03_tts_audio.py       # 阶段3: 旁白文本 → 语音音频
python 04_merge_ffmpeg.py    # 阶段4: 片段 + 音频 + 字幕 → 最终 MP4
```

每个阶段脚本都有交互式确认提示。

### 章节提取调试
```bash
python chapter_reader.py 锦衣夜行_77119小说.txt
```

### 环境变量配置（.env）
- `NOVEL_CHAPTER`: 章节序号（默认1）
- `PROMPT_MAX_CHARS`: 传给 LLM 的原文上限（默认50000，传递完整章节）
- `SHOT_COUNT_MIN/MAX`: 分镜数范围（默认8-20）
- `VIDEO_PROVIDER`: 视频生成供应商（默认agnes）
- `IMAGE_PROVIDER`: 图片生成供应商（默认agnes）
- `TEXT_PROVIDER`: 文本生成供应商（默认deepseek）
- `TTS_PROVIDER`: TTS供应商（默认dashscope_sdk）

## 架构设计

### 四阶段流水线
```
小说文本 (.txt)
    → step01_script_gen/main.py → script_result.json（分镜 + 旁白 + 角色 + 摄像机分组）
    → 02b_vimax_gen.py  → output/vimax_working/shots/<idx>/video.mp4
    → 03_tts_audio.py   → output/audio/narration.mp3
    → 04_merge_ffmpeg.py → output/final.mp4
```

### Provider 抽象模式
`_video_gen/` 和 `tts/` 两个包采用相同架构：
- **ABC 接口 + 工厂注册表**：通过 `.env` 中的 PROVIDER 切换
- **Config 单例**：各包的 `config.py` 从 `.env` 加载
- **Dataclass 模型**：请求和结果用 dataclass 定义

### 章节提取
- 小说文件：`锦衣夜行_77119小说.txt`（GBK编码）
- 章节格式：`第001章 章节标题`
- 使用 `chapter_reader.py` 提取完整章节

## 关键参考文件
- `skill_spy.txt` — Prompt 工程参考
- `01_script_gen.py` — 包含详细的分镜生成 prompt