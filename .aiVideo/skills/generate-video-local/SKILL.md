---
name: generate-video-local
specializes: generate-video
description: "aiVideo 项目专属的视频生成偏好，覆盖模型策略/速率限制/时长/参考图选择"
---

# Repo-specific 视频生成偏好 for `aiVideo`

此文件是核心 `generate-video` 技能的伴生文件。

## 模型选择策略

- 当前主提供者：Agnes AI（agnes-video-v2.0）
- 回退模型：无（AGNES_VIDEO_FALLBACK_MODELS 为空）
- DashScope 模型已全部 403 耗尽，不再使用

## 速率限制参数

- 提交间隔：65s（VIDEO_CLIP_INTERVAL）
- 每分钟限制：未启用（VIDEO_MAX_REQUESTS_PER_MINUTE 未设）
- 最大重试次数：3（VIDEO_MAX_RETRIES）

## 时长偏好

- 默认 duration：从 shot.duration 读取（回退 5s）
- 视频尺寸：960x1792（9:16 竖屏）

## 首帧生成策略

- 参考图优先级：同摄像机首帧锚定 > 角色肖像（视角匹配 front/side/back） > 风格锚点
- 视角检测规则：
  - 背面/背影/背向 → back
  - 侧身/侧面/侧脸 → side
  - 正面/面对/面向 → front
  - 默认 → front
- 首帧 prompt 用 ff_desc（纯静态快照），不用 motion_desc
- 尾帧 prompt 用 lf_desc
