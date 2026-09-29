# aiVideo 全项目代码审查报告

- 审查日期：2026-08-06
- 审查范围：核心 Python 流水线、provider 层、共享工具、配置与密钥管理
- 仓库：`C:\myFloders\git\aiVideo`
- 工作区状态：审查时存在 5 个未提交改动，均已纳入审查
  - `_image_gen/agnes_provider.py`
  - `_image_gen/bailian_provider.py`
  - `shared/vimax_adapters.py`
  - `shared/vimax_bridge.py`
  - `step01_script_gen/validation.py`

## 验证情况

- `python -m compileall -q` 通过。
- `git diff --check` 通过。
- 项目核心流水线未发现可运行的单元测试。
- `.env.keys` 未被 git 跟踪；`.env.content` 虽被跟踪，但未包含 API key。

## 严重度摘要

| 级别 | 数量 | 核心主题 |
|------|------|----------|
| 高 | 4 | TTS 变量错误、Bailian 图片异常路径、step02b 失败计数、openai 依赖缺失 |
| 中 | 6 | 合规清洗被覆盖、API key 日志、全局 logger、缓存失效、R2V 超限、合并路径 |
| 低 | 3 | endpoint 配置无效、DashScope 图片模式、校验豁免过宽 |

## 高优先级问题

### 1. TTS 两条生成路径的 `voice_config` 都会失败

**文件**：`step03_tts_audio/shot_tts.py`

- 异步路径第 290 行给 `voice_Config` 赋值，但后续第 309 行读取 `voice_config`。检测到说话人时必然触发 `NameError`，被外层 `except` 吞掉后该镜头跳过。
- 同步路径第 242 行只在 `speaker_name` 存在时给 `voice_config` 赋值，没有 narrator 兜底；无说话人时第 247/257 行会引用未绑定变量。

**影响**：step03 主入口默认走异步路径，对话镜头 TTS 会大量失败。

**建议**：统一变量名，并在函数入口初始化 `voice_config = NARRATOR_VOICE.copy()`。

### 2. Bailian 图片 provider 在异常时会抛 `UnboundLocalError`

**文件**：`_image_gen/bailian_provider.py`

- `rsp` 只在 `try` 内的第 197 行赋值。
- 第 234 行捕获异常后跳出内层循环。
- 第 243 行仍然访问 `rsp.status_code`。

**影响**：首次请求发生网络错误或 SDK 异常时，不是返回失败结果，而是直接崩溃。

**建议**：保存最后一次状态码，或在访问 `rsp` 前判断其是否已赋值。

### 3. step02b 的失败计数不可信

**文件**：`step02b_video_gen/main.py`

- 第 84 行只把 `asyncio.gather` 返回的异常计入 `failed_count`。
- `_submit_video` 失败返回 `None`，不会进入 `polls`。
- `_poll_video` 失败返回 `False`，但主流程不检查返回值。
- 真实失败记录在 `step02_video_gen/submit_poll.py` 的 `_failed_videos` 中，step02b 未读取。

**影响**：所有镜头失败时仍可能报告 `Failed: 0`，影响人工判断和 CI 门禁。

**建议**：让 `_poll_video` 返回 `bool`，并在 `gen_all_videos` 中统计 `_failed_videos`。

### 4. 默认 DeepSeek 文本链路缺少依赖声明

**文件**：`requirements.txt`、`_text_gen/deepseek_provider.py`

- `deepseek_provider.py` 第 35 行 `from openai import OpenAI`。
- `requirements.txt` 第 10 行把 `openai` 注释掉了。

**影响**：按 README 安装依赖后，默认 `TEXT_PROVIDER=deepseek` 的新环境无法生成文本。

**建议**：将 `openai` 加入正式依赖，或在使用 DeepSeek 前给出明确安装提示。

## 中优先级问题

### 5. 合规清洗结果会被原始 prompt 覆盖

**文件**：`scene_compliance.py`

- 第 290 行保存原始 `prompt`。
- 第 292-295 行清洗后写回 `shot["prompt"]`。
- 第 309 行又用原始 `prompt` 覆盖 `shot["prompt"]`。

**影响**：敏感词清洗不可靠；`_post_process` 中的第二次校验可能暂时兜住，但该函数本身仍存在逻辑缺陷。

**建议**：只用清洗后的局部变量参与场景矛盾修复，并最终写回清洗结果。

### 6. 视频轮询日志写入 API key 片段

**文件**：`_video_gen/agnes_provider.py`

- 第 464 行记录 `key={bound_key[:30]}...`。
- 第 465 行记录 `Authorization: Bearer {bound_key[:40]}...`。

**影响**：日志泄露 key 的前 40 字符，若日志被提交或分享，安全风险很高。

**建议**：只记录 key 编号，或统一输出完全脱敏后的摘要。

### 7. 全局 logger 会合并所有日志文件

**文件**：`shared/tee_logger.py`

- 第 122-127 行中，只要 `_global_logger` 已存在，就忽略新的 `log_path`。

**影响**：`step02.log`、`step02a.log`、`step02b.log` 等会按首次调用顺序写入同一个文件，日志隔离失效。

**建议**：改为按 `log_path` 缓存 logger，或至少按调用方传入路径切换。

### 8. step01 缓存 hash 覆盖不足

**文件**：`step01_script_gen/hash_cache.py`

- 第 14-28 行只包含 `prompts.py`、novel 前 1000 字、章节号和 objects bank。
- 未包含 `validation.py`、`style_prefix.py`、`scene_compliance.py`、character bank 和 `.env` 配置。

**影响**：这些规则或配置变化后，旧 `script_result.json` 可能被错误命中缓存。

**建议**：将相关规则文件和影响生成结果的配置纳入 hash。

### 9. Bailian R2V 参考图数量可能超过模型上限

**文件**：`_video_gen/bailian_provider.py`

- 第 393 行把 `reference_paths` 截到模型上限。
- 第 401-406 行又无条件追加首帧和尾帧。

**影响**：Wan R2V 上限 5 张时，实际可能传 7 张，触发 API 400。

**建议**：首帧/尾帧加入前先检查总数量，或统一收集后再截断。

### 10. 非 vimax 的 CLIP_SOURCE 合并路径不可用

**文件**：`step04_merge/main.py`

- 第 44-47 行：非 vimax 模式指向扁平目录中的 `clip_N.mp4`。
- 第 236 行：仍统一查找 `CLIP_DIR/<idx>/video.mp4`。

**影响**：配置非 vimax 时，已有 `clip_N.mp4` 无法被找到。

**建议**：按 `CLIP_SOURCE` 分支处理文件命名规则。

## 低优先级问题

### 11. endpoint 环境变量未生效

`AGNES_VIDEO_ENDPOINT`、`AGNES_IMAGE_ENDPOINT`、`AGNES_TEXT_ENDPOINT` 已在配置中定义，但 provider 仍硬编码 `/v1/videos`、`/v1/images/generations`、`/v1/chat/completions`。配置项会造成“改了没效果”的误导。

### 12. DashScope 视频 provider 不支持图片模式

`_video_gen/dashscope_provider.py` 的 `create_task` 不传 `image_path` 和 `last_frame_image_path`。配 `VIDEO_PROVIDER=dashscope` 且使用 image 模式时，会静默退化成文生视频。

### 13. 新增“进行中定格豁免”过宽

`step01_script_gen/validation.py` 第 1545-1551 行中，只要 ff 含“即将/欲/尚未”就整体豁免，可能掩盖同句内同时存在的完成态动词和 motion 冲突。

## 审查假设

- 本次审查聚焦项目自身 Python 实现，未逐行审查 `skills/`、`docs/`、`references/` 中的第三方或参考资料。
- 未运行真实 API 调用、未生成视频，因此 provider 的实际响应兼容性无法完全验证。
- 当前未提交改动属于用户工作区内容，审查过程中未修改任何文件。

## 建议下一步

1. 先修复高优先级问题：TTS 变量、Bailian 异常路径、step02b 失败统计、依赖声明。
2. 为中优先级问题补充针对性的回归测试，尤其是 TTS、合规清洗和失败计数。
3. 检查并清理历史日志，确认是否已有 API key 前缀被写入日志文件或 git 历史。
4. 对当前 5 个未提交改动补测参考图裁剪与动作主体排序的边界场景。
