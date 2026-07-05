---
name: generate-video
description: "将分镜 JSON 转化为视频片段的核心合同，含首帧锚定/尾帧生成/速率限制"
---

# 视频生成

## 输入

- `script_result.json`（含 shots[] 及摄像机连贯性字段）
- `output/vimax_working/` 缓存（首帧/尾帧/角色肖像/风格锚点）

## 输出

- `output/vimax_working/shots/<idx>/video.mp4` — 每个镜头的静音视频片段

## 不可变流程

1. 桥接层翻译 script → ViMax working_dir 缓存
2. 按摄像机分组生成首帧（同机锚定）和尾帧（medium/large）
3. 两阶段提交+轮询：批量提交 → 并发轮询 → 失败重试
4. 速率限制（VIDEO_MAX_REQUESTS_PER_MINUTE）

## 安全规则

- 已有有效缓存时跳过生成（mtime 过期检测）
- 首帧/尾帧生成后校验文件大小（≥5KB，不达标自动重试一次）
- 单个镜头失败不阻断其他镜头（try/except 包裹）
- 最多重试 MAX_RETRIES 次

## 可重写类别

1. **模型选择策略** — 主模型/回退模型优先级
2. **速率限制参数** — 每分钟最大请求数
3. **时长偏好** — 默认 duration、是否从 script 读取
4. **首帧生成策略** — 参考图选择优先级、视角匹配规则
