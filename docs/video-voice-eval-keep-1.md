---
name: video-voice-eval-keep-1
description: 评估结果：保持 VIDEO_MODEL_VOICE=1，记录切换条件与备用方案（项目文档，不记 memory）
metadata:
  type: project
  created: 2026-09-07
  location: docs/
---

# 视频模型声音评估 + 备用决策（2026-09-07）

**状态**：保持现状（`VIDEO_MODEL_VOICE=1`），不切 TTS，文档留在项目 `docs/` 供后续直接查，不依赖 memory。

## 当前决策
- `VIDEO_MODEL_VOICE=1` 不变；暂不降级到 `VIDEO_MODEL_VOICE=0`。
- `tts/voice_registry.py` 仍可用，但当前不作主控，仅作为 `step02_video_gen/prompt_builder.py` 的 `style` 描述注入源（`line 69-121`）。
- 原因：评估已完成（模型能力 / 一致性 / 系列长度三维）；避免中途切换引入 `step03_tts_audio` + `step04_merge` 同步风险。

## 评估依据（项目文件路径）
- `tts/voice_registry.py`：角色→音色映射表，跨章持久化（`global_dir()/voice_registry.json`）；仅在 0 时作为主控发挥。
- `shared/model_caps.py`：`VIDEO_MODEL_VOICE` 默认 1；部分模型（Seedance 1.0 系列）无 `generate_audio`，1.5 pro/2.0 有声；无声必须降级 0。
- `step02_video_gen/prompt_builder.py`：`audio_cue` + 台词/说话人/音色描述注入；声音一致性为提示描述级，非声纹指纹级。
- `CLAUDE.md`：2026-08 后默认 1，step03 保留但流水线已剥离 TTS；恢复 r2v 提醒同理适用声音（1.0 消耗完应升 2.0）。
- `step01_script_gen/main.py`：无声视频模型告警（`line 106-119`）。

## 一致性真实水平
- `1`：提示描述级一致（同角色名→同 `style` 字符串注入），无声纹锁定，无 `used_voice_ids` 约束，易随画面/参考图漂移。
- `0`：注册表级唯一（`mapping` + `used_voice_ids`），固定 `voice_id`，可审计、可复现、跨部持久。

## 备用切换条件（未来直接看此处，触发即执行）
1. **模型无声或套餐下线**：当前 `VIDEO_PROVIDER` 对应模型不支持 `generate_audio`（查 `model_caps.py` / 提供商文档）；示例：火山 1.5 pro 降级 / Seedance 1.0 免费额度耗尽 → **立即 0**。
2. **长系列 / 多角色（>4）/ 需求声音指纹审计**：若要“同角色声音不可混用、可追溯” → **必须 0**；视频模型无审计踪迹。
3. **跨镜头声音漂移实测确认**：A/B 测试（2-3 shot）同角色声线明显不同，且 `audio_cue` + `style` 已优化无效 → **降级 0**。
4. **复杂混音/多角色对同声**：`audio_cue` 简洁（10-30 字）；若需精细混音 → TTS + 手动混音更可控。

## 备份内容（项目内可查，不依赖外部记忆）
- 本文件：`docs/video-voice-eval-keep-1.md`
- 相关代码：`tts/voice_registry.py`、`step02_video_gen/prompt_builder.py`、`shared/model_caps.py`、`.env.content`（`VIDEO_MODEL_VOICE` / `VIDEO_PROVIDER` / `VOLCENGINE_I2V_MODEL` / `MINIMAX_H3_MODEL`）
- 测试命令：
  - `ffprobe -show_streams output/video/.../video.mp4` 检查音频轨
  - `python -c "from tts.voice_registry import get_registry; r=get_registry(); print(r.get_voice('角色名'))"` 查当前映射

## 后续动作（待触发，不立即执行）
- [ ] 监控 `.env.content` 模型版本；若切无声模型，立即改 `VIDEO_MODEL_VOICE=0`。
- [ ] 若决定切 0：修改 `.env.content`，运行 `step03_tts_audio/main.py`，验证 `audio/narration.mp3` + `step04_merge` 字幕同步。
- [ ] 若保持 1：每波生成后抽查 2 个分镜，听同角色声线；记录漂移案例。
