# waoowaoo 项目搬运参考文档

> 搬运自 `C:\myFloders\git\waoowaoo` — 一个完整的故事板/视频生成 Next.js 应用
> 搬运日期: 2026-07-10

## 搬运内容清单

| 文件 | 来源 | 用途 |
|---|---|---|
| `extract_chinese.py` | `waoowaoo/extract_chinese.py` | 从代码中提取中文字符串的工具 |
| `references/from_waoowaoo/prompts/*.json` | `waoowaoo/standards/prompt-canary/` | LLM 输出 JSON 格式参考（金丝雀标准） |
| `references/from_waoowaoo/types/character_profile.ts` | `waoowaoo/src/types/character-profile.ts` | 角色档案数据模型参考 |
| 本 README | — | DashScope API 模式总结、架构参考 |

---

## 一、DashScope（通义万相 / 百炼）API 模式总结

从 `waoowaoo/src/lib/providers/bailian/` 中提炼的完整 API 交互模式。

### 1.1 视频生成（Image-to-Video）

**端点：**
- 常规 i2v: `POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis`
- 首尾帧 KF2V: `POST https://dashscope.aliyuncs.com/api/v1/services/aigc/image2video/video-synthesis`

**请求头：**
```http
Authorization: Bearer {apiKey}
Content-Type: application/json
X-DashScope-Async: enable
```

**请求体（i2v）：**
```json
{
  "model": "wan2.1-i2v-plus",
  "input": {
    "img_url": "https://...",
    "prompt": "A cinematic shot of..."
  },
  "parameters": {
    "resolution": "1280x720",
    "size": "1280*720",
    "watermark": false,
    "prompt_extend": true,
    "duration": 5
  }
}
```

**请求体（KF2V 首尾帧）：**
```json
{
  "model": "wan2.2-kf2v-flash",
  "input": {
    "first_frame_url": "https://...",
    "last_frame_url": "https://...",
    "prompt": "..."
  },
  "parameters": { ... }
}
```

**支持首尾帧的模型**（来自 waoowaoo 源码）：
- `wan2.2-kf2v-flash`（仅首尾帧模式）
- `wanx2.1-kf2v-plus`（仅首尾帧模式）
- `wan2.7-i2v`（支持 i2v + KF2V）

**响应（提交）：**
```json
{
  "request_id": "...",
  "output": {
    "task_id": "...",
    "task_status": "PENDING"
  }
}
```

### 1.2 异步任务轮询

**端点：**
```http
GET https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}
Authorization: Bearer {apiKey}
```

**响应状态映射：**
| `task_status` | 含义 |
|---|---|
| `PENDING` / `RUNNING` | 处理中 |
| `SUCCEEDED` / `SUCCESS` | 完成 |
| `FAILED` | 失败 |
| `CANCELED` / `CANCELLED` | 已取消 |

**完成时响应结构（多种可能）**：
```json
{
  "output": {
    "task_status": "SUCCEEDED",
    "video_url": "https://...",
    "image_url": "https://...",
    "results": [
      { "url": "...", "video_url": "...", "image_url": "..." }
    ]
  }
}
```
优先级顺序：`output.video_url` → `output.image_url` → `results[0].video_url` → `results[0].image_url` → `results[0].url`

### 1.3 TTS 语音合成

**端点：**
```http
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
Authorization: Bearer {apiKey}
Content-Type: application/json
```

**请求体：**
```json
{
  "model": "qwen3-tts-vd-2026-01-26",
  "input": {
    "text": "要合成的文本内容...",
    "voice": "longxiaxiaoxuan|longwan|longchen|longyue|longyuan",
    "language_type": "Chinese"
  }
}
```

**已知语音 ID（来自项目）**：`longxiaxiaoxuan`, `longwan`, `longchen`, `longyue`, `longyuan`

**响应：**
```json
{
  "request_id": "...",
  "output": {
    "audio": {
      "data": "base64encodedwavdata...",
      "url": "https://...",
      "expires_at": 1700000000
    }
  },
  "usage": {
    "characters": 42
  }
}
```
`audio.data` 是 base64 编码的 WAV 数据，`audio.url` 是临时下载链接。

**最大字符限制：** 600 字符/段

**长文本分段策略**（来自 waoowaoo 源码）：
1. 取 `min(cursor + 600, len)` 作为硬边界
2. 从硬边界向前查找标点符号作为切分点（优先级：句号/问号/感叹号 → 逗号/分号/冒号 → 换行符）
3. 跳过后面的空白字符

### 1.4 语音设计（TTS 自定义音色）

**端点：**
```http
POST https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization
Authorization: Bearer {apiKey}
Content-Type: application/json
```

**请求体：**
```json
{
  "model": "qwen-voice-design",
  "input": {
    "action": "create",
    "target_model": "qwen3-tts-vd-2026-01-26",
    "voice_prompt": "温柔知性的年轻女性声音，略带书卷气",
    "preview_text": "用于预览的一段文本...",
    "preferred_name": "custom_voice",
    "language": "zh"
  },
  "parameters": {
    "sample_rate": 24000,
    "response_format": "wav"
  }
}
```

**限制：**
- voice_prompt 最多 500 字符
- preview_text 最少 5 字符、最多 200 字符

---

## 二、数据模型参考

### 2.1 角色档案 CharacterProfileData

从 `character_profile.ts` 提炼：

| 字段 | 类型 | 说明 |
|---|---|---|
| `role_level` | `'S' | 'A' | 'B' | 'C' | 'D'` | 角色重要层级 |
| `archetype` | `string` | 角色原型（如"霸道总裁"、"心机婢"） |
| `personality_tags` | `string[]` | 性格标签 |
| `era_period` | `string` | 时代背景 |
| `social_class` | `string` | 社会阶层 |
| `occupation` | `string?` | 职业（可选） |
| `costume_tier` | `1 | 2 | 3 | 4 | 5` | 服装华丽度 |
| `suggested_colors` | `string[]` | 建议色彩 |
| `primary_identifier` | `string?` | 主要辨识标志（S/A 级角色必填） |
| `visual_keywords` | `string[]` | 视觉关键词 |
| `gender` | `string` | 性别 |
| `age_range` | `string` | 年龄段描述 |

### 2.2 金丝雀 Prompt 格式参考

见 `references/from_waoowaoo/prompts/` 下的 JSON 文件：

| 文件 | 用途 |
|---|---|
| `storyboard_panels.canary.json` | 分镜面板 JSON 格式示例 |
| `screenplay_conversion.canary.json` | 剧本转换 JSON 格式示例 |
| `story_to_script_clips.canary.json` | 小说→脚本分段 JSON 格式示例 |
| `voice_analysis.canary.json` | 语音分析 JSON 格式示例 |

---

## 三、架构模式参考

### 3.1 视频生成错误码

| 错误码 | 说明 |
|---|---|
| `BAILIAN_VIDEO_IMAGE_URL_REQUIRED` | 缺少图片 URL |
| `BAILIAN_VIDEO_MODEL_ID_REQUIRED` | 缺少模型 ID |
| `BAILIAN_VIDEO_LAST_FRAME_IMAGE_URL_REQUIRED` | 首尾帧模型缺少尾帧 URL |
| `BAILIAN_VIDEO_LAST_FRAME_UNSUPPORTED_FOR_MODEL` | 当前模型不支持首尾帧模式 |
| `BAILIAN_VIDEO_OPTION_UNSUPPORTED` | 不支持的选项参数 |
| `BAILIAN_VIDEO_SUBMIT_FAILED` | 提交失败（含 HTTP 状态码和错误信息） |
| `BAILIAN_VIDEO_TASK_ID_MISSING` | 响应中缺少 task_id |

### 3.2 JSON 安全解析模式

waoowaoo 使用 `jsonrepair` npm 包处理 LLM 输出，核心逻辑（`src/lib/json-repair.ts`）：

1. **Strip markdown 围栏**：移除 ` ```json ... ``` `
2. **JSON 子串提取**：找到第一个 `{` 或 `[`，跟踪括号深度提取纯 JSON
3. **自动数组拆包**：若 LLM 返回 `{ "clips": [...] }`，自动取出数组内容
4. **异常修复**：使用 jsonrepair 修复不合法 JSON

### 3.3 async 轮询 externalId 格式

```
PROVIDER:TYPE:REQUEST_ID
```
示例：`BAILIAN:VIDEO:task_abc123`, `BAILIAN:IMAGE:task_def456`

---

## 四、建议后续参考方向

1. **`waoowaoo/src/lib/providers/bailian/`** → 完整的百炼 API 实现（视频、TTS、LLM、图片）
2. **`waoowaoo/src/lib/async-poll.ts`** → 多 provider 统一轮询框架
3. **`waoowaoo/src/lib/json-repair.ts`** → LLM 输出 JSON 修复
4. **`waoowaoo/src/lib/srt.ts`** → SRT 字幕解析/操作
5. **`waoowaoo/src/lib/storyboard-phases.ts`** → 分镜生成的多阶段流水线模式（Phase 1 规划 → Phase 2 摄影/表演 → Phase 3 细节）
6. **`waoowaoo/src/lib/workers/handlers/script-to-storyboard.ts`** → 剧本到分镜的完整 worker 逻辑
