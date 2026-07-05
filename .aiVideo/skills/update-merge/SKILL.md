---
name: update-merge
description: "从合成质量反馈中学习，更新合成伴生技能"
---

# 合成自进化

## Write surface

仅允许写入 `.aiVideo/skills/merge-audio-local/SKILL.md`

## 工作流

1. 分析 `04_merge_ffmpeg.py` 的运行日志和最终视频
2. 识别模式：
   - 字幕被裁切 → 调整字幕位置/大小
   - 音频音量偏低 → 调整音量增益
   - 暗角过强 → 调整 vignette angle
   - 颜色过于暗淡 → 调整 contrast/saturation
3. 聚合信号（≥2 次相同调整）
4. 生成最小增量更新
5. Write surface 校验

## 证据规则

- 同类参数调整出现 ≥2 次才写入
- 极端值（如音量 > 10dB、contrast > 2.0）自动忽略
