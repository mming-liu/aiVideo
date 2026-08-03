# aiVideo - AI 短视频生成流水线

将中文小说文本转换为约 60 秒的抖音竖屏视频（古风权谋卧底风格）。

## 目录

- [快速开始](#快速开始)
- [环境配置](#环境配置)
- [流水线](#流水线)
- [环境变量详解](#环境变量详解)
- [Claude Code 命令](#claude-code-命令)
- [输出目录结构](#输出目录结构)
- [常见问题](#常见问题)

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
# 系统还需安装 ffmpeg 并加入 PATH
```

### 2. 配置环境变量

复制并配置 `.env.content` 和 `.env.keys`：

```bash
# .env.content - 内容参数
NOVEL_NAME=锦衣夜行
NOVEL_CHAPTER=1
BOOK_INDEX=1
BOOK_NAME=杀青州
OUTPUT_BASE=./output/video

# .env.keys - API Keys
DEEPSEEK_API_KEY=sk-xxx
AGNES_API_KEY=xxx
DASHSCOPE_API_KEY=xxx
```

### 3. 运行流水线

```bash
# 阶段1: 小说 → 分镜 JSON
python step01_script_gen/main.py

# 阶段2a: 分镜 → 首帧/尾帧/关键帧图片
python step02a_frame_gen/main.py

# 阶段2b: 图片 → 视频片段
python step02b_video_gen/main.py

# 阶段3: 旁白 → 语音音频
python step03_tts_audio/main.py

# 阶段4: 片段 + 音频 → 最终 MP4
python step04_merge/main.py
```

---

## 流水线

```
小说文本 (.txt)
    → step01__script_gen/main.py → script_result.json（分镜 + 旁白 + 角色）
    → step02a_frame_gen/main.py  → 首帧/尾帧/关键帧图片
    → step02b_video_gen/main.py  → vimax_working/shots/<idx>/video.mp4
    → step03_tts_audio/main.py   → audio/narration.mp3
    → step04_merge/main.py       → final.mp4
```

**调试技巧：**
- 阶段2拆分为2a和2b，可先运行2a检查图片质量，确认OK后再运行2b
- 每个阶段脚本都有交互式确认提示
- `VIDEO_REFERENCE_MODE=text` 时跳过分帧环节，直接文生视频

---

## 环境变量详解

### 核心配置（必需）

| 变量 | 说明 | 示例 |
|-----|------|------|
| `NOVEL_NAME` | 小说名（用于输出目录） | `锦衣夜行` |
| `NOVEL_CHAPTER` | 章节序号 | `1` |
| `BOOK_INDEX` | 部序号（小说内第几部） | `1` |
| `BOOK_NAME` | 部名（拼入目录名） | `杀青州` |
| `OUTPUT_BASE` | 输出根目录 | `./output/video` |

### 供应商选择

| 变量 | 说明 | 可选值 |
|-----|------|-------|
| `TEXT_PROVIDER` | 文本生成 | `deepseek`, `dashscope`, `agnes` |
| `VIDEO_PROVIDER` | 视频生成 | `agnes`, `dashscope`, `bailian` |
| `IMAGE_PROVIDER` | 图片生成 | `agnes`, `bailian` |
| `TTS_PROVIDER` | 语音合成 | `dashscope_sdk`, `dashscope_http`, `openai` |

### 分镜生成（step01）

| 变量 | 说明 | 默认值 |
|-----|------|-------|
| `SHOT_COUNT_MIN` | 分镜数最小值 | `6` |
| `SHOT_COUNT_MAX` | 分镜数最大值 | `15` |
| `CHARACTER_EXTRACTION_ENABLED` | 角色提取 | `1` 开启 |
| `HOOK_ENABLED` | 钩子分镜 | `1` 开启 |
| `GAP_ANALYSIS_ENABLED` | 间隔分析 | `0` 关闭 |

### 视频生成（step02）

| 变量 | 说明 | 默认值 |
|-----|------|-------|
| `VIDEO_SIZE` | 画面尺寸（9:16 竖屏） | `720*1280` |
| `VIDEO_DURATION` | 单镜头默认时长(秒) | `6` |
| `VIDEO_CONCURRENCY` | 视频并发数 | `6` |
| `VIDEO_MAX_RETRIES` | 单镜头最大重试次数 | `3` |
| `VIDEO_REFERENCE_MODE` | 参考图模式 | `text` |

**VIDEO_REFERENCE_MODE 选项：**
- `text` - 文生视频（跳过帧图片，纯 prompt 生成，推荐）
- `image` - 图生视频（需先运行 step02a 生成帧图片）
- `r2v` - 参考图生视频（使用 reference_paths 保持角色/道具一致性，最多5个参考图）

**VIDEO_STYLE 选项（13种）：**
- `gufeng` - 古风真人（默认）
- `ghibli` - 吉卜力动画
- `healing` - 治愈系插画风
- `fairy_tale` - 童话绘本风
- `oil_painting` - 油画电影风
- `pixar` - 皮克斯3D卡通
- `clay` - 粘土定格风
- `absurd` - 荒诞卡通风
- `sketch` - 孩童草稿风
- `disney` - 迪士尼童话风
- `flat` - 扁平矢量风
- `ink_painting` - 国风水墨动画
- `anime` - 二次元漫剧

### TTS 语音（step03）

| 变量 | 说明 | 默认值 |
|-----|------|-------|
| `DASHSCOPE_TTS_RATE` | 语速 | `0.8` (0.5~2.0) |
| `DASHSCOPE_TTS_VOLUME` | 音量 | `50` (0~100) |

### 片段合并（step04）

| 变量 | 说明 | 默认值 |
|-----|------|-------|
| `FFMPEG_BIN_FFMPEG_` | FFmpeg 路径 | 系统 PATH |
| `FFMPEG_AUDIO_VOLUME` | 旁白音量增益 | `5dB` |
| `OPENING_` | 开场标题 | `0` 关闭 |
| `CLIP_SOURCE` | 视频来源 | `vimax` |
| `MERGE_MODE` | 合并模式 | `native` |

**MERGE_MODE 选项：**
- `stretch` - 音画同步（视频拉伸匹配音频）
- `concat` - 直接拼接
- `native` - 纯视频拼接（语音由视频模型生成）

### Bailian (百炼) 模型配置

| 变量 | 说明 | 默认值 |
|-----|------|-------|
| `BAILIAN_T2I_MODEL` | 文生图模型 | `qwen-image-2.0-pro-2026-06-22` |
| `BAILIAN_T2V_MODEL` | 文生视频模型 | `wan2.7-t2v-2026-06-12` |
| `BAILIAN_I2V_MODEL` | 图生视频模型 | `happyhorse-1.0-i2v` |
| `BAILIAN_R2V_MODEL` | 参考生视频模型 | `happyhorse-1.1-r2v` |
| `BAILIAN_VIDEO_RESOLUTION` | 视频分辨率 | `720P` |
| `BAILIAN_VIDEO_RATIO` | 视频比例 | `9:16` |
| `BAILIAN_VIDEO_DURATION` | 视频时长 | `5` (2-15秒) |

---

## Claude Code 命令

在 Claude Code 中直接调用百炼 API 生成图片/视频：

| 命令 | 用途 | 示例 |
|-----|------|------|
| `/text-to-image` | 文生图 | `/text-to-image shot 3` |
| `/image-to-image` | 图生图 | `/image-to-image shot 3` |
| `/ref-to-image` | 参考生图 | `/ref-to-image shot 1,3-5` |
| `/text-to-video` | 文生视频 | `/text-to-video shot 3,5,7` |
| `/image-to-video` | 图生视频 | `/image-to-video shot 0` |
| `/ref-to-video` | 参考生视频 | `/ref-to-video shot 2` |

**分镜指定格式：**
- 单个：`shot 3`
- 多个：`shot 3,5,7`
- 区间：`shot 3-7`
- 省略：默认 shot 0（hook）

---

## 输出目录结构

```
{OUTPUT_BASE}/                    # ./output/video
└── 锦衣夜行/                      # {NOVEL_NAME} 小说级
    ├── global/                    # 全书共享（角色形象跨部统一）
    │   ├── characters_bank/       # 角色注册表 + 肖像
    │   ├── style_bank/            # 风格锚点（Ch1 锁定）
    │   └── chapter_cache/         # 章节文本解析缓存
    └── part_01_杀青州/             # part_{BOOK_INDEX:02d}_{BOOK_NAME} 部级
        └── chapter_001/           # chapter_{NOVEL_CHAPTER:03d} 章级
            ├── script_result.json # 分镜脚本
            ├── final-A.mp4        # 最终视频 A
            ├── final-B.mp4        # 最终视频 B
            ├── subtitles.srt      # 字幕
            ├── audio/
            │   ├── narration.mp3  # 完整旁白
            │   └── shot_*.mp3     # 分镜旁白
            ├── clips/             # 视频片段
            │   └── clip_*.mp4
            ├── vimax_working/
            │   └── shots/         # 分镜工作目录
            │       └── {idx}/
            │           ├── first_frame.png
            │           ├── last_frame.png
            │           ├── video.mp4
            │           └── keyframes/
            └── stretched/         # 拉伸后视频片段
```

---

## 常见问题

### Q: 如何指定生成特定分镜？

```bash
# 只生成第 3、5、7 分镜
python step02b_video_gen/main.py --shots 3,5,7

# 强制重生成（忽略缓存）
python step02b_video_gen/main.py --shots 3 --force
```

### Q: 如何跳过帧图片直接文生视频？

在 `.env.content` 中设置：
```
VIDEO_REFERENCE_MODE=text
```

### Q: 视频时长如何自动匹配音频？

系统会自动：
1. 在 step02b 生成视频前，先用 TTS 生成音频
2. 根据音频实际时长设置视频 duration
3. 修复已应用到：`_video_gen/bailian_provider.py`（使用 `req.duration or self._duration`）

### Q: 如何切换导演风格？

在 `.env.content` 中设置：
```
CINEMATIC_STYLE=王家卫  # 或 kubrick, villeneuve, fincher 等
```

### Q: 视频生成失败怎么办？

- 检查 `VIDEO_MAX_RETRIES` 设置（默认3次重试）
- 查看日志 `step02b.log` 或 `step02.log`
- 确认 API Key 余额充足

### Q: 如何调试章节提取？

```bash
python chapter_reader.py 锦衣夜行_77119小说.txt
```

---

## API Keys 获取

| 服务 | 获取地址 |
|-----|---------|
| DeepSeek | https://platform.deepseek.com/ |
| Agnes AI | https://www.agnes-ai.cn/ |
| DashScope (百炼) | https://bailian.console.aliyun.com/ |

---

*最后更新：2026-08-02*