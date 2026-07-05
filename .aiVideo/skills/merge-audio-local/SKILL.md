---
name: merge-audio-local
specializes: merge-audio
description: "aiVideo 项目专属的合成偏好，覆盖色彩/暗角/音量/字幕/拉伸"
---

# Repo-specific 合成偏好 for `aiVideo`

此文件是核心 `merge-audio` 技能的伴生文件。

## 色彩分级参数

- 对比度（contrast）：1.15
- 饱和度（saturation）：0.85

## 暗角强度

- 暗角角度（vignette angle）：PI/6 ≈ 0.5236

## 音频增益

- 音量增益：5dB（TTS 输出偏低，需正向增益）

## 字幕样式

- 字体：Sans
- 大小：20
- 颜色：&H00FFFFFF（白色）
- 描边颜色：&H00000000（黑色）
- 描边宽度：1

## 拉伸策略

- 最大拉伸系数：2.5x（FFMPEG_MAX_STRETCH）
- 时间轴铁律：视频绝对不改（不拉伸不压缩），audio 与 video 不对齐时音频被截断/无声
