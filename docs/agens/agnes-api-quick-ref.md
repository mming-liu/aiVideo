# Agnes API 快速参考

> 基于 `docs/agnes-video-api.md`、`docs/agens-picture-api.md` 和 `docs/faq.md` 的实用参数汇总。

> **重要**: 图片 API 推荐使用档位式 `size` + `ratio`，避免精确像素被标准化到意外尺寸。

## 免费用户 RPM 限制 (default user)

| 模型类型 | 分辨率 | 实际 RPM | 建议间隔 |
|---------|-------|---------|---------|
| 文本 | - | 20 | 3s |
| 图片 | 1K | 20 | 3s |
| 图片 | 2K | 10 | 6s |
| 图片 | 3K/4K | 1 | 60s |
| 视频 | - | 1 | 60s |

> Token Plan 用户更高（图片 1K: 100, 2K: 80, 视频: 5 RPM）

## .env 配置示例

```bash
# 图片 (免费用户默认 2K=10RPM)
IMAGE_ MAX_REQUESTS_PER_MINUTE=10   # 2K: 10 RPM
IMAGE_GEN_INTERVAL=6                # 间隔6秒

# 视频 (免费用户默认 1RPM)
VIDEO_ CLIP_INTERVAL=60             # 间隔60秒
```

## 图片生成 (v2.1 Flash)

### 端点
```
POST https://apihub.agnes-ai.com/v1/images/generations
```

### 推荐参数（档位式）
```python
ImageGenerationRequest(
    prompt="...",
    shot_index=0,
    size="2K",      # 档位: 1K/2K/3K/4K（推荐，10 RPM）
    ratio="9:16",   # 竖屏短视频
    negative_prompt="...",
    seed=12345,     # 可选：用于可复现结果
)
```

### 尺寸参考
| Ratio | 2K |
|-------|-----|
| 9:16 | 1472x26 |
| 16:9 | 26x14 |
| 1:1 | 20 |

### 图生图
```python
ImageGenerationRequest(
    prompt="保持构图，...",
    size="2K",
    ratio="9:16",
    reference_image_path="/path/...",
)
```

## 视频生成 (V2.0)

### 端点
```
POST https://apihub.agnes-ai.com/v1/videos
GET  https://apihub.agnes-ai.com/agnesapi?video_id=<ID>
```

### 推荐参数
```python
VideoGenerationRequest(
    prompt="[主体] + [动作] + [场景] + ...",
    clip_index=0,
    duration=5,
    negative_prompt="...",
    size="9x17xx92",
    image_path="first_frame.png",
    last_frame_image_path="last_frame.png",
    seed=12345,
)
```

### 帧数约束
- **必须** `num_frames <= 441`
- **必须** `num_frames = 8n + 1`

| 目标时长 | num_frames | frame_rate |
|---------|------------|------------|
| ~3s | 81 | 24 |
| ~5s | 121 | 24 |
| ~10s | 241 | 24 |

### 关键帧动画模式
当同时提供首帧+尾帧时，使用 `mode="keyframes"`

## 错误码
| 状态码 | 说明 |
|-------|------|
| 400 | 请求无效 |
| 401 | API Key 错误 |
| 429 | 速率限制 |
| 500/503 | 服务端错误 |

## 已实现特性
- [x] 403 自动回退
- [x] 5xx 自动重试
- [x] negative_prompt
- [x] seed
- [x] 图生视频
- [x] 关键帧动画
- [x] 帧数 8n+1 约束
- [x] 免费用户默认间隔 (图片6s, 视频60s)