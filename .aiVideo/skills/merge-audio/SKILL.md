---
name: merge-audio
description: "将视频片段+音频+字幕合成为最终 MP4 的核心合同"
---

# 成片合成

## 输入

- 视频片段：`output/vimax_working/shots/<idx>/video.mp4`
- 旁白音频：`output/audio/narration.mp3`
- 字幕数据：`script_result.json` shots[]

## 输出

- `output/final.mp4` — 最终合成视频（含色彩分级、暗角、字幕、音频）

## 不可变流程

1. 测量所有视频片段时长
2. 计算目标时长（以视频为基准，不拉伸不压缩）
3. 生成 SRT 字幕（按字幕字数比例分配时间）
4. 拉伸片段（仅当视频与音频不对齐时，最大拉伸系数 2.5x）
5. FFmpeg 合成：concat → subtitles → eq → vignette + 音频混流

## FFmpeg 滤镜链（不可变顺序）

```
concat → subtitles（SRT 烧录）→ eq（对比度+饱和度）→ vignette（暗角）
音频：volume 增益 → 混入最终流
```

## 编码参数

- 视频：libx264, CRF 20
- 音频：AAC 192k

## 可重写类别

1. **色彩分级参数** — contrast/saturation 值
2. **暗角强度** — vignette angle
3. **音频增益** — 音量 dB 值
4. **字幕样式** — 字体/大小/颜色/描边/位置
5. **拉伸策略** — 最大拉伸系数
