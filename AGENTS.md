# AGENTS.md

本文件记录 agnes ai 接入项目的实现计划与约定，供后续会话复用。

## 总目标

接入 **agnes ai** 作为新供应商，覆盖三个模态：**文本模型、图片模型、视频模型**。
agnes 兼容 OpenAI 协议。所有 base_url / 端点 / 模型名走 `.env` 配置，运行时填值，不硬编码。

## 关键决策（已锁定）

1. **视频模式 = async 轮询**：`AGNES_VIDEO_MODE=async`，提交任务→轮询 task_id→下载 video_url
2. **图片 = 视频首帧**：`01b_image_gen.py` 为每个镜头生成首帧图 → `02_video_gen.py` 以图为首帧做 image-to-video
3. **agnes = OpenAI 兼容协议**：
   - 文本：`POST {base}/v1/chat/completions`
   - 图片：`POST {base}/v1/images/generations`
   - 视频：`POST {base}/v1/video/generations`（提交）、`GET {base}/v1/video/generations/{task_id}`（轮询）
4. **鉴权**：`Authorization: Bearer {AGNES_API_KEY}`
5. **统一 403 回退**：三个 agnes provider 均沿用现有 `_model_queue` + 403 自动切换模式
6. **文本阶段重构**：把 `01_script_gen.py` 的过程式 if/else 重构为 `_text_gen/` 包，对齐 `_video_gen`/`tts` 的 ABC + 工厂架构

## 新流水线顺序

```
01_script_gen → 01b_image_gen(首帧) → 02_video_gen(图生视频) → 03_tts → 04_merge
```

## 文件结构

### A. 文本阶段：重构 `_text_gen/` 包

| 文件 | 内容 |
|---|---|
| `_text_gen/__init__.py` | 导出 `TextGenCfg`/`config`/`TextGenerationRequest`/`TextGenerationResult`/`TextGenerationService`/`create_service` |
| `_text_gen/models.py` | `TextGenerationRequest(system_prompt, user_prompt, max_tokens=4000, temperature=0.7)` / `TextGenerationResult(success, text, error)` |
| `_text_gen/config.py` | `TextGenCfg`：`PROVIDER=os.getenv("TEXT_PROVIDER","dashscope")`；迁移 `DASHSCOPE_*`/`DEEPSEEK_*`；新增 `AGNES_API_KEY/BASE_URL/TEXT_MODEL/TEXT_FALLBACK_MODELS/TEXT_ENDPOINT`(默认`/v1/chat/completions`)；`validate()` 三分支 |
| `_text_gen/service.py` | `TextGenerationService` ABC(`generate`)+`register/create_service/_auto_register`(注册 dashscope/deepseek/agnes) |
| `_text_gen/dashscope_provider.py` | 迁移 `01:_call_dashscope` |
| `_text_gen/deepseek_provider.py` | 迁移 `01:_call_deepseek` |
| `_text_gen/agnes_provider.py` | OpenAI 兼容 chat，403 回退遍历 `AGNES_TEXT_FALLBACK_MODELS` |

`01_script_gen.py`：删 `_call_dashscope`/`_call_deepseek`/`gen_spy_script` 分支；`gen_spy_script` 改 `create_service().generate(req)`；`SCRIPT_PROVIDER`→`TEXT_PROVIDER`；保留 `read_chapter`/`build_prompt`/`parse_response`/`SYSTEM_PROMPT`/`PROMPT_TEMPLATE`/`_validate_script`。

### B. 视频阶段：`_video_gen/agnes_provider.py`（image-to-video + 轮询）

- `create_task`：POST `{AGNES_BASE_URL}{AGNES_VIDEO_ENDPOINT}`，body=`{model, prompt, size, duration, negative_prompt, [image]}`。**若 `req.image_path` 存在**：读文件→base64 data URL→放入 `AGNES_VIDEO_IMAGE_FIELD`(默认`image`)字段。200→返回 `task_id`；403→`_advance_model` 回退
- `query_task`：GET `{endpoint}/{task_id}`
- `wait_for_completion`：按 `AGNES_VIDEO_POLL_INTERVAL/MAX_POLL_ATTEMPTS` 轮询；`status==SUCCEEDED`→取 `video_url`/`video_urls[0]`→`_download`；`FAILED`→失败；其余继续
- 若 `image_path` 为 None 或文件缺失：降级为纯 text-to-video（不中断）

`_video_gen/models.py`：`VideoGenerationRequest` 增 `image_path: Optional[str] = None`（默认 None，向后兼容 dashscope）。
`_video_gen/config.py`：增 `AGNES_API_KEY/BASE_URL/VIDEO_MODEL/VIDEO_FALLBACK_MODELS/VIDEO_ENDPOINT`(默认`/v1/video/generations`)/`VIDEO_IMAGE_FIELD`(默认`image`)/`VIDEO_POLL_INTERVAL/MAX_POLL_ATTEMPTS`；`validate()` 增 agnes 分支。
`_video_gen/service.py:_auto_register()`：`register("agnes", _ag.AgnesVideoService)`。

### C. 图片阶段：`_image_gen/` 包 + `01b_image_gen.py`

| 文件 | 内容 |
|---|---|
| `_image_gen/__init__.py` | 导出公共 API |
| `_image_gen/models.py` | `ImageGenerationRequest(prompt, shot_index, negative_prompt, size="1024x1792", reference_image_path=None)` / `ImageGenerationResult(success, save_path, error)` |
| `_image_gen/config.py` | `ImageGenCfg`：`PROVIDER=os.getenv("IMAGE_PROVIDER","agnes")`；`OUTPUT_DIR=./output/images`；`IMAGE_SIZE=1024x1792`；`AGNES_API_KEY/BASE_URL/IMAGE_MODEL/IMAGE_FALLBACK_MODELS/IMAGE_ENDPOINT`(默认`/v1/images/generations`) |
| `_image_gen/service.py` | ABC(`generate`)+工厂 |
| `_image_gen/agnes_provider.py` | POST `/v1/images/generations`，解析 `data[0].url`(下载) 或 `b64_json`(解码)，存 `output/images/shot_{idx}.png`，403 回退 |

`01b_image_gen.py`：读 `script_result.json`→遍历 `shots`→用 `shot["prompt"]`+`negative_prompt` 生成首帧→存 `shot_{index}.png`；跳过已存在有效文件；单镜头 try/except。

### D. 改造 `02_video_gen.py`（接首帧）

- `build_prompt` 增：查找 `output/images/shot_{index}.png`，存在则传入 `VideoGenerationRequest.image_path`
- 其余逻辑不变

### E. 配置与文档

- `.env.example`：增 `TEXT_PROVIDER`/`IMAGE_PROVIDER`/全部 `AGNES_*`（模型名占位 `agnes-text-1` 等）
- `CLAUDE.md`：流水线加 `01b_image_gen.py` 阶段、provider 列表加 agnes
- `04_merge_ffmpeg.py`：**不改**（图片仅作首帧，不进合成）

## 执行顺序

1. `_text_gen/` 包 + 迁移 + `01` 改造 → dashscope 回归
2. `_text_gen/agnes_provider.py`
3. `_image_gen/` 包 + `01b_image_gen.py`
4. `VideoGenerationRequest.image_path` + `_video_gen/config.py` AGNES_*
5. `_video_gen/agnes_provider.py` + 注册
6. `02_video_gen.py` 接首帧
7. `.env.example` / `.env` / `CLAUDE.md`

## 运行时需用户填入 `.env` 的值

```
AGNES_API_KEY=xxx
AGNES_BASE_URL=https://你的agnes域名/v1
AGNES_TEXT_MODEL=文本模型id
AGNES_IMAGE_MODEL=图片模型id
AGNES_VIDEO_MODEL=视频模型id
# 可选 fallback
AGNES_TEXT_FALLBACK_MODELS=...
AGNES_IMAGE_FALLBACK_MODELS=...
AGNES_VIDEO_FALLBACK_MODELS=...
```

## 架构约定（与现有代码一致）

- 所有代码和注释均为中文
- Provider 抽象模式：ABC 接口 + 工厂注册表 + Config 单例 + Dataclass 模型
- 403 自动回退：视频生成回退在实例生命周期内保持；TTS/文本/图片回退在单次调用级别
- 禁止硬编码 base_url / 模型名，全部走 `.env`
- 不添加任何注释（除非用户要求）
