---
name: code-video
description: "用 React (Remotion) 以编程方式创建视频，支持动画、3D、字幕、数据可视化。触发：编程视频、React视频、Remotion"
---

# Code Video — Remotion 编程视频

用 React 以编程方式创建 MP4 视频的框架。适用于：批量生成、数据驱动视频、教程讲解、3D 产品展示、Logo 动画。

## 规则文件索引

所有详细 API 文档在 `rules/` 目录下，按需加载：

### Remotion API
- [rules/animations.md](rules/animations.md) — 动画基础：`useCurrentFrame()` 驱动
- [rules/timing.md](rules/timing.md) — 时间插值：interpolate、spring、easing
- [rules/sequencing.md](rules/sequencing.md) — 时间编排：延迟、裁剪、限制
- [rules/transitions.md](rules/transitions.md) — 场景过渡

### 媒体资源
- [rules/assets.md](rules/assets.md) — 导入图片/视频/音频/字体
- [rules/images.md](rules/images.md) — `<Img>` 嵌入图片
- [rules/videos.md](rules/videos.md) — 嵌入视频：裁剪/音量/速度/循环
- [rules/audio.md](rules/audio.md) — 音频处理
- [rules/fonts.md](rules/fonts.md) — Google Fonts + 本地字体

### 字幕与文字
- [rules/subtitles.md](rules/subtitles.md) — 字幕概览
- [rules/transcribe-captions.md](rules/transcribe-captions.md) — 转录生成字幕
- [rules/display-captions.md](rules/display-captions.md) — 显示字幕
- [rules/text-animations.md](rules/text-animations.md) — 文字排版和动画

### 视频架构
- [rules/tutorial-architecture.md](rules/tutorial-architecture.md) — 教程视频：场景驱动、Hook、相机
- [rules/dialogue-video.md](rules/dialogue-video.md) — 对话视频：多层架构、ChatOverlay
- [rules/3b1b-style.md](rules/3b1b-style.md) — 3Blue1Brown 风格
- [rules/process-animation.md](rules/process-animation.md) — 过程动画模式

### 3D
- [rules/3d.md](rules/3d.md) — Three.js + React Three Fiber

### TTS
- [rules/tts-providers.md](rules/tts-providers.md) — TTS 方案选择（火山/MiniMax/Qwen/Edge）

## 核心概念

| 概念 | 作用 |
|------|------|
| `Composition` | 视频定义：尺寸、帧率、时长 |
| `useCurrentFrame()` | 获取当前帧号，驱动一切动画 |
| `interpolate()` | 帧号 → 任意值（位置、透明度等） |
| `spring()` | 物理弹性动画 |
| `<Sequence>` | 时间轴上排列组件 |
| `<AbsoluteFill>` | 全屏定位容器 |

## 创建项目

```bash
npx create-video@latest
npm run dev
```

## 渲染

```bash
npx remotion render src/index.ts CompositionId --codec=h264 --concurrency=2
```

| 参数 | 说明 |
|------|------|
| `--codec` | h264, h265, vp8, vp9, gif, mp3, wav |
| `--crf` | 质量 (0-51，默认18) |
| `--scale` | 缩放因子（测试用 0.5） |
| `--concurrency` | 并行数（有视频素材时用 2，纯动画 4） |
| `--gl` | 3D 场景用 `angle` |

## 踩坑经验

| 问题 | 原因 | 解决 |
|------|------|------|
| 画面抖动 | `React.lazy()` 在组件函数内 | 放模块级别 |
| 视频丢帧 | 用了 `<Video>` | 改用 `<OffthreadVideo>` |
| 渲染内存溢出 | 大图片 | ffmpeg 缩放到 1920 |
| 渲染崩溃 | `--concurrency` 太高 | 含视频时用 2 |
| WebGL 溢出 | 多 3D 场景共存 | sceneIndex 条件渲染 |
| 相机持续抖动 | 增量更新不收敛 | 直接设置或用 spring |

## 节奏控制

### TTS 语速

| 场景 | 推荐倍速 |
|------|---------|
| 日常对话 | 1.2x |
| 技术讲解 | 1.0-1.1x |
| 情绪表达/旁白 | 1.0-1.15x |

### 叙事弧线

- 片头 0-15s：品牌 5-8s → 钩子前置
- 中段：信息密度波动，短句穿插喘息
- 收尾最后 20s：总结 → 金句 → CTA

### 片头音乐

5s 满音量 + 3s 渐弱淡出，在配音前淡出完成。

## 参考资源

- 官方文档：https://remotion.dev/docs
- 模板库：https://remotion.dev/templates
