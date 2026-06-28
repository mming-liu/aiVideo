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
python 01_script_gen.py      # 阶段1: 小说文本 → 分镜 JSON（调用 Qwen LLM）
python 02_video_gen.py       # 阶段2: 分镜 → 视频片段（调用 DashScope 文生视频，耗时较长）
python 03_tts_audio.py       # 阶段3: 旁白文本 → 语音音频
python 04_merge_ffmpeg.py    # 阶段4: 片段 + 音频 + 字幕 → 最终 MP4
```

每个阶段脚本都有交互式确认提示。

### 无测试框架和 Lint 配置

项目目前未配置 pytest、tox、flake8 等测试/lint 工具，也没有 CI/CD。

## 架构设计

### 四阶段流水线

```
小说文本 (.txt)
    → 01_script_gen.py  → script_result.json（分镜 + 旁白）
    → 02_video_gen.py   → output/clips/*.mp4（各镜头视频片段）
    → 03_tts_audio.py   → output/audio/narration.mp3（旁白语音）
    → 04_merge_ffmpeg.py → output/final.mp4（最终合成视频）
```

### Provider 抽象模式

`_video_gen/` 和 `tts/` 两个包采用相同的架构：

- **ABC 接口 + 工厂注册表**：通过 `.env` 中的 `VIDEO_PROVIDER` / `TTS_PROVIDER` 切换实现，无需改代码
- **Config 单例**：各包的 `config.py` 从 `.env` 加载配置，暴露模块级 `config` 对象
- **Dataclass 模型**：请求和结果用 dataclass 定义（`models.py`）

视频生成当前只有 `DashScopeVideoService` 实现（异步任务 + 轮询）。TTS 有 `DashScopeSDKProvider`、`DashScopeHttpProvider` 和 `OpenAIProvider` 三个实现。

### 403 自动回退机制

视频生成和 TTS 合成均实现了 HTTP 403 自动回退：当模型额度耗尽时，自动切换到下一个可用模型。

- **视频生成**：回退状态在实例生命周期内保持（同一批次中切换后，后续镜头也使用新模型）
- **TTS 合成**：回退在单次调用级别（每次合成独立遍历模型队列）
- 详细文档见 `docs/video_model_fallback.md`

> ⚠ 当前所有模型（视频 + TTS）均为调试用临时模型，后续会替换为正式模型。

## 配置说明

所有配置集中在 `.env` 文件中（使用 python-dotenv 加载），包括：

- API 密钥（`DASHSCOPE_API_KEY`）
- Provider 选择（`VIDEO_PROVIDER`、`TTS_PROVIDER`）
- 视频主模型 + 回退列表（`DASHSCOPE_VIDEO_MODEL`、`DASHSCOPE_VIDEO_FALLBACK_MODELS`）
- TTS 主模型 + 回退列表（`DASHSCOPE_TTS_MODEL`、`DASHSCOPE_TTS_FALLBACK_MODELS`）
- FFmpeg 参数、TTS 音色/语速/音量等

**注意：`.env` 包含 API 密钥，在初始化 git 前务必添加 `.gitignore`。**

## 关键参考文件

- `skill_spy.txt` — Prompt 工程参考：视觉风格前缀、负面 prompt、镜头词汇、FFmpeg 调色参数
- `novel_sample.txt` — 小说样本片段（用于测试）
- `锦衣夜行_77119小说.txt` — 完整小说源文本（~8MB）

## 开发注意事项

- 所有代码和注释均为中文
- FFmpeg 合成阶段使用了复杂的滤镜链（色彩分级、暗角、字幕烧录、音频混流），修改时需小心滤镜参数顺序
- 视频生成为异步轮询模式（提交任务 → 轮询状态 → 下载），单次生成可能需要数分钟
- 阶段 2 的视频生成对每个镜头有 try/except 包裹，单个镜头失败不会中断整个批次
