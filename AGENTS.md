# Repository Guidelines

## Project Overview

aiVideo is an automated video production pipeline that transforms novel chapters into cinematic videos. It generates storyboards, video clips, TTS narration, and merges everything into a final MP4.

## Pipeline

```
01_script_gen.py → 02b_vimax_gen.py → 03_tts_audio.py → 04_merge_ffmpeg.py
   (storyboard)      (video + frames)      (narration)        (merge)
```

- **01_script_gen.py**: Novel chapter → LLM generates storyboard JSON
- **02b_vimax_gen.py**: Storyboard JSON → ViMax translation → first/last frame generation → video clip submission + polling
- **03_tts_audio.py**: Narration text → TTS audio generation
- **04_merge_ffmpeg.py**: Video clips + audio + subtitles → final MP4

## Project Structure

| Path | Description |
|------|-------------|
| `01_*.py` - `04_*.py` | Main pipeline entry points |
| `tts/` | TTS providers (DashScope SDK/HTTP, OpenAI) |
| `_text_gen/` | Text generation (Qwen, DeepSeek, Agnes) |
| `_video_gen/` | Video generation (DashScope, Agnes) |
| `_image_gen/` | Image generation (Agnes) |
| `vimax/` | ViMax framework (agents, interfaces, pipelines, tools) |
| `vimax_bridge.py` | Bridge layer between script and ViMax |
| `.env` | Configuration (API keys, model settings) |

## Build & Run

```bash
# Install dependencies
pip install -r requirements.txt

# Run pipeline steps
python 01_script_gen.py    # Generate storyboard
python 02b_vimax_gen.py    # Generate video clips
python 03_tts_audio.py     # Generate narration audio
python 04_merge_ffmpeg.py  # Merge into final video

# Verify syntax
python -m py_compile 01_script_gen.py 02b_vimax_gen.py 03_tts_audio.py 04_merge_ffmpeg.py
```

## Coding Style

- **Language**: Python 3.10+
- **Indentation**: 4 spaces
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes
- **Encoding**: UTF-8 (avoid hardcoded Chinese characters in logic)
- All providers use `.env` — never hardcode API keys

## Provider Architecture

All providers follow ABC interface + factory registry + Config singleton pattern:

| Module | Protocol | Implementations |
|--------|----------|-----------------|
| `_text_gen/` | OpenAI chat completions | DashScope Qwen, DeepSeek, Agnes |
| `_image_gen/` | OpenAI images API | Agnes |
| `_video_gen/` | Custom polling | DashScope, Agnes |
| `tts/` | TTS | DashScope SDK, DashScope HTTP, OpenAI |

## Configuration

Edit `.env` to switch providers:

```ini
# Text generation
TEXT_PROVIDER=deepseek

# Video generation
VIDEO_PROVIDER=agnes

# TTS
TTS_PROVIDER=dashscope_sdk

# Chapter selection
NOVEL_CHAPTER=1
```

## Key Conventions

1. **Video mode = async polling**: Submit task → poll task_id → download video_url
2. **Images = video first frames**: vimax_bridge generates first_frame.png per shot
3. **403 fallback**: Providers use `_model_queue` with automatic fallback on 403 errors
4. **Rate limiting**: VIDEO_MAX_REQUESTS_PER_MINUTE, IMAGE_GEN_INTERVAL
