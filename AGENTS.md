# AGENTS.md

本文件记录项目架构与约定，供后续会话复用。

## 流水线

```
01_script_gen.py → 02b_vimax_gen.py → 03_tts_audio.py → 04_merge_ffmpeg.py
     (分镜)          (视频+首帧+尾帧)       (旁白语音)         (合成)
```

- **01_script_gen.py**：小说章节 → LLM 生成分镜 JSON（含摄像机分组、镜头分解、视觉衔接、人物外貌总纲）
- **02b_vimax_gen.py**：分镜 JSON → ViMax 桥接层翻译 → 首帧/尾帧生成（摄像机锚定）→ 视频片段提交+轮询
- **03_tts_audio.py**：旁白文本 → TTS 语音
- **04_merge_ffmpeg.py**：片段 + 音频 + 字幕 → 最终 MP4

## 新能力（v3 + v4）

### 已同步的 ViMax 连续性机制
- 摄像机分组（cam_idx 2-3 台）
- 镜头分解（ff_desc / lf_desc / motion_desc）
- 变幅类型（small / medium / large）
- 首帧锚定（同摄像机首帧共享）
- 尾帧生成（medium/large 变幅）
- 视觉衔接（context_bridge）
- 人物外貌总纲（character_profile）
- 视角检测（front/side/back 肖像匹配）
- 速率限制器（每分钟 API 上限）
- 首帧/尾帧生成校验 + 自动重试

### 已删除的旧入口
- **02_video_gen.py**：被 02b_vimax_gen.py 完全取代
- **01b_image_gen.py**：首帧生成改由 vimax_bridge 内部处理

## 关键决策

1. **视频模式 = async 轮询**：提交任务 → 轮询 task_id → 下载 video_url
2. **图片 = 视频首帧**：vimax_bridge 内部为每个镜头生成 first_frame.png，视频生成做 image-to-video
3. **统一 403 回退**：各 provider 均沿用 `_model_queue` + 403 自动切换模式
4. **所有 provider 走 .env**，不硬编码

## Provider 架构

| 模块 | 协议 | 当前实现 |
|---|---|---|
| `_text_gen/` | OpenAI chat completions | DashScope Qwen / DeepSeek / Agnes |
| `_image_gen/` | OpenAI images API | Agnes |
| `_video_gen/` | 自定义轮询 | DashScope 通义万相 / Agnes |
| `tts/` | TTS | DashScope SDK / DashScope HTTP / OpenAI |

均采用：ABC 接口 + 工厂注册表 + Config 单例 + Dataclass 模型。
