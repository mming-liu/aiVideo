# Agnes API 快速参考

> 基于 `docs/agnes-*.md` 和 `docs/faq.md`

## 免费用户 RPM 限制 (default)

| 类型 | 分辨率 | RPM | 建议间隔 |
|-----|-------|-----|---------|
| 图片 | 2K | 10 | 6秒 |
| 图片 | 1K | 20 | 3秒 |
| 图片 | 3K/4K | 1 | 60秒 |
| 视频 | - | 1 | 60秒 |

> Token Plan: 图片 1K=100, 2K=80, 视频=5 RPM

## 编程调用

```python
from _image_gen.config import config as img_cfg
from _video_gen.config import config as vid_cfg

# 获取免费用户限制
img_info = img_cfg.get_rate_limit_info()  # {'image_2k_rpm': 10, ...}
vid_info = vid_cfg.get_rate_limit_info()  # {'video_rpm': 1, 'video_interval': 60}
```

## .env 配置

```bash
# 图片 (默认适配免费用户 2K)
IMAGE_MAX_REQUESTS_PER_MINUTE=10
IMAGE_GEN_INTERVAL=6

# 视频 (默认适配免费用户)
VIDEO_CLIP_INTERVAL=60
```

## API 参数

### 图片 (2K + 9:16)
```python
ImageGenerationRequest(
    prompt="...",
    shot_index=0,
    size="2K",
    ratio="9:16",
    negative_prompt="...",
    seed=12345,
)
```

### 视频 (关键帧模式)
```python
VideoGenerationRequest(
    prompt="...",
    clip_index=0,
    duration=5,
    negative_prompt="...",
    image_path="first.png",        # 首帧
    last_frame_image_path="last.png",  # 尾帧 → keyframes模式
    seed=12345,
)
```

## 帧数约束 (视频)
- `num_frames <= 441`
- `num_frames = 8n + 1` (81, 121, 241...)

## 错误码
| 码 | 说明 |
|----|------|
| 400 | 请求无效 |
| 401 | API Key 错误 |
| 429 | 速率限制 |
| 500/503 | 服务端错误 |

## 已实现
- [x] 403 自动回退
- [x] 5xx 重试
- [x] negative_prompt / seed
- [x] 图生视频 / 关键帧动画
- [x] 帧数 8n+1 约束
- [x] 免费用户默认间隔 (图6s/视频60s)