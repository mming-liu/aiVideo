---
name: video-edit
description: "视频编辑/后处理技能：剪辑、调色、字幕烧录、遮罩动画、合成，基于 video-use/src/subtitles 体系"
---

# 视频编辑

## 位置

本技能搭载于 aiVideo 的 04 合成阶段之后，在视频编辑/后处理阶段使用。

## 输入

- 已渲染的视频片段（MP4/MOV 等）
- 旁白音频（可选）
- 字幕数据（可自动转录或手动提供）

## 输出

- 剪辑后的最终 MP4（含调色/字幕/动画覆盖层）

## 不可变规则（Hard Rules）

1. **字幕最后烧录**：subtitle filter 必须是滤镜链最后一步，否则覆盖层会遮住字幕
2. **逐段提取+无损拼接**：不要单次 filtergraph，必须 `-c copy` concat 避免重编码
3. **每段边界 30ms 音频淡入淡出**：否则每次切点都有爆破音
4. **覆盖层用 setpts 移位**：`setpts=PTS-STARTPTS+T/TB` 对齐覆盖层帧0到目标时间窗口
5. **主 SRT 用输出时间线偏移**：`output_time = word.start - segment_start + segment_offset`
6. **永远不在字中间切断**：每个切点必须在文字边界上
7. **切点加 Padding**：30-200ms，快节奏偏紧、电影感偏松
8. **先确认再执行**：用户确认后方可改动
9. **所有输出写入 `<videos_dir>/edit/`**

## 工作流

1. **扫描**：`ffprobe` 每个源文件，转录，打包 transcript
2. **预检**：扫描 verbal slips / mis-speaks
3. **对话**：按素材特点提问，确定风格/时长/画幅
4. **提案**：4-8 句策略说明，等待确认
5. **执行**：生成 EDL.json → 逐段提取+调色 → 动画并行构建 → 字幕 → 合成
6. **预览**：渲染 720p 预览
7. **自检**：每个切点 ±1.5s 的 timeline_view，字画同步、覆盖层对齐、音频无爆音
8. **迭代 + 持久化**：自然语言反馈 → 重新规划 → 渲染，追加 project.md

## 调色

ASC CDL 模型：`out = (in * slope + offset) ^ power`，然后全局饱和度。

- `slope` → 高光
- `offset` → 阴影
- `power` → 中间调

预设：`warm_cinematic`（青橙复古）、`neutral_punch`（微对比+S曲线）、`none`（直通）。

通用约束：
- 逐段提取时调色（非拼接后，避免重编码两次）
- 有皮肤时先测试再加大强度

## 字幕

三个维度：块大小（1-3词/4-7词），大小写（全大写/首字母/自然），位置（底部MarginV）。

风格示例：
- **bold-overlay**：2词块、全大写、Helvetica Bold 18、白字黑描边、`MarginV=35`
- **natural-sentence**：4-7词、句子大小写、自然停顿、`MarginV=60-80`

## 动画

工具选择：HyperFrames（HTML/CSS/GSAP 浏览器合成）、Remotion（React 组件）、Manim（数学动画）、PIL+PNG序列（简易覆盖卡片）。

通用约束：
- 叙述同步：至少 3s，简单卡 5-7s，复杂图 8-14s
- 最后帧保持 ≥1s
- 永不线性缓动（`ease_out_cubic` 单揭示，`ease_in_out_cubic` 连续绘制）
- 打字效果居中锚定在全字符串宽度上

覆盖层渲染模板见 `video-use/SKILL.md`。

## EDL 格式

```json
{
  "version": 1,
  "sources": {"C0103": "/abs/path/C0103.MP4"},
  "ranges": [
    {"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK", "reason": "..."}
  ],
  "grade": "warm_cinematic",
  "overlays": [
    {"file": "edit/animations/slot_1/render.mp4", "start_in_output": 0.0, "duration": 5.0}
  ],
  "subtitles": "edit/master.srt",
  "total_duration_s": 87.4
}
```

## 反模式

- 覆盖层在字幕之后（字幕被遮挡）
- 字幕嵌入基础视频后再加覆盖层
- 单次 filtergraph 含覆盖层（造成重编码）
- 动画用线性缓动 → 每次用 cubic
- 硬切无音频淡出 → 必有爆音
- 打字效果逐字居中 → 文本左右漂移
- 动画串行构建 → 永远并行
- 先改再确认策略
- 重复转录不变源文件

## 调色脚本

`helpers/grade.py` 提供 `--list-presets` 和 `--filter '<raw>'`。
见 SKILL.md 所在目录下的 `grade.py`。

## 参考资源

- 完整 video-use SKILL.md 见 `C:\myFloders\git\video-use\SKILL.md`
- 动画子系统见 `skills/manim-video/`
- Remotion 视频方案见 `../code-video/SKILL.md`
