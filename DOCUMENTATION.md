# aiVideo Pipeline — 完整文档

> 生成日期: 2026-07-15
> 项目路径: C:\myFloders\git\aiVideo

---

## 一、项目概览

aiVideo 是一个自动化视频生产流水线，将小说章节转换为电影级短视频（9:16 竖屏，动漫风格）。流水线涵盖：分镜脚本生成 → 视频片段生成 → TTS 语音合成 → 最终合成。

核心设计哲学：
- 每个流水线步骤独立（step01~step04），产物为文件级中间状态
- 多供应商抽象层（_text_gen / _image_gen / _video_gen / tts），通过 .env.content（内容参数）+ .env.keys（API Key）一键切换
- ViMax 框架作为底层智能体编排层
- 缓存机制避免重复的 LLM/API 调用

---

## 二、文件与目录结构

核心模块：
- step01_script_gen/：分镜脚本生成
- step02_video_gen/：视频生成
- step03_tts_audio/：TTS 语音生成
- step04_merge/：视频合成
- shared/：共享工具（vimax_bridge, vimax_adapters, content_policy_fix, chapter_reader, character_bank）
- _text_gen/, _image_gen/, _video_gen/, tts/：供应商抽象层
- vimax/：ViMax 框架

---

## 三、流水线详解

### 步骤一：分镜脚本生成（step01_script_gen/）

输入：小说 TXT 文件 + 章节号
输出：{OUTPUT_BASE}/book_N/chapter_N/script_result.json

流程：
read_chapter() → gen_spy_script() → parse_response() → 差距分析 → _post_process() → 生成钩子分镜 → 提取角色 → 计算时间轴 → 保存

核心逻辑：
- read_chapter()：通过 shared/chapter_reader.py 从小说 TXT 中提取指定章节
- gen_spy_script()：调用 LLM 生成结构化分镜脚本，prompt 包含镜头语言库、风格前缀、合规规则、物理特效/光影分层/材质约束、画面-字幕逐词绑定规则
- parse_response()：解析 JSON 带损坏恢复链（safe_parse_json_object 优先，兜底修复包括去代码块标记、提取 {} 区域、修复尾部逗号）。校验镜头数、字段完整性、景别多样性、字幕-画面一致性、合规性
- 差距分析：检查后 1/3 镜头是否覆盖高潮情节，可自动带反馈重新生成（最多 2 次）
- _post_process()：注入全局渲染参数 + 自动合规过滤 + 敏感词校验
- 钩子分镜：生成 1 个独立开场悬念镜头
- 角色提取：跨章节一致性，通过 CharacterBank 持久化

### 步骤二：视频生成（step02_video_gen/）

输入：script_result.json
输出：{VIMAX_WORKING_DIR}/shots/{idx}/video.mp4 + 首帧/尾帧

流程（2026-08 起不再预生成 TTS）：
build_working_dir() → 逐镜并发：生成帧 → 提交任务 → 后台轮询

核心逻辑：
- build_working_dir()（shared/vimax_bridge.py）：将 script_result.json 转换为 ViMax 工作目录（角色、故事板、摄像机树、镜头描述、角色肖像）
- 逐镜处理：生成首帧 first_frame.png（及尾帧 last_frame.png 若为 medium/large 变幅），提交异步视频任务，后台轮询结果（最多重试 3 次）
- **时长**：视频时长由 `shot["duration_sec"]`（step01 timecode 权威计算）驱动，不再由 TTS 音频决定
- **语音**：对话/背景音交给视频模型（wan2.7 系列有声视频，`VIDEO_MODEL_VOICE=1`），step02 不调用 TTS；台词+说话人+音色描述（voice_registry style）注入 prompt
- 速率控制：提交间隔 + RPM 限流 + 并发数控制
- 422 违规自动修复：通过 content_policy_fix.py 替换敏感词后重试
- **智能轮询**：指数退避 + 抖动策略，显著减少 API 查询频率
  - 初始等待 60s 后开始轮询
  - 间隔序列：10s → 15s → 22s → 33s → 50s → 60s（上限）
  - 429 限流触发更激进退避：10s → 20s → 40s → 60s
  - 每次查询 ±3s 随机抖动防请求风暴

### 步骤三：TTS 语音（step03_tts_audio/）

可选独立步骤（step02 已不调用 TTS）。逐镜 TTS 生成，检测到已有音频文件则跳过。包含：
- 多音色支持：自动说话人检测 + 动态音色分配
- 异步并发优化：Semaphore(2) 限流 + 指数退避重试

### 步骤四：视频合成（step04_merge/）

输入：script_result.json + 视频片段
输出：final.mp4 + subtitles.srt

核心逻辑（2026-08 native 精简版）：
- SRT 字幕生成：基于逐镜字幕文本 + 视频实际时长计算时间戳，单段最长 8s
- **native 拼接（唯一模式）**：xfade/acrossfade 转场链直接拼接，保留每段视频原声
  （语音/背景音由视频模型生成）；不再按音频时长拉伸视频
- 转场效果：硬切 / 淡入 / 淡出 / 模糊叠化 / 简单叠化 / 黑场闪白
- 最终合成：调色（对比度/饱和度/暗角）+ 字幕叠加
- **单输出 final.mp4**：删除 final-A（纯 TTS）/ final-B（混音）双版本
- **视频完整性校验**：检测异常小文件（<20KB）并自动重生成

---

## 四、供应商架构

所有供应商遵循 ABC 接口 + 工厂注册 + Config 单例模式。

文本生成（_text_gen/）：
- dashscope_provider.py：DashScope HTTP API（qwen-plus）
- deepseek_provider.py：OpenAI 兼容接口（deepseek-chat）
- agnes_provider.py：OpenAI 兼容接口（agnes-2.0-flash）

图片生成（_image_gen/）：
- agnes_provider.py：Agnes AI（agnes-image-2.1-flash）

视频生成（_video_gen/）：
- dashscope_provider.py / http_dashscope_provider.py：DashScope（happyhorse-1.1-t2v）
- agnes_provider.py：Agnes AI（agnes-video-v2.0），异步轮询模式

语音合成（tts/）：
- dashscope_provider.py：DashScope SDK WebSocket（sambert-zhida-v1）
- dashscope_qwen3.py：DashScope HTTP（qwen3-tts）
- openai_provider.py：OpenAI 兼容（tts-1）

403 回退机制：每个供应商有 _model_queue，遇到 403（配额超限）自动切换到下一个回退模型。多个 API Key 轮换（Agnes 支持 8 个 Key）。

---

## 五、ViMax 集成

vimax_bridge.py 将 step01 产出的 script_result.json 翻译为 ViMax 工作目录格式。

核心功能：
- 摄像机树（_build_camera_tree）：基于 cam_idx 算法构建多摄像机父子链
- 首帧/尾帧生成：同摄像机内非首个镜头引用该摄像机首帧作为锚定，组间并行（不同摄像机同时生成）、组内串行
- 参考图选择（_select_best_reference）：从 ff_desc/prompt/context_bridge 检测视角偏好（正面/侧面/背面），回退到角色肖像 → 风格锚点
- 角色肖像生成：新角色生成 front/side/back 三视图（横屏白底，跳过风格前缀）
- Prompt 哈希缓存：每镜记录 prompt hash，只有 prompt 变化时才重新生成帧

适配器：
- ImageGeneratorAiVideoAdapter：强制竖屏 9:16，复用风格前缀 + 合规化清洗
- VideoGeneratorAiVideoAdapter：拼前缀 + 合规化 + 首帧/尾帧参考图

---

## 六、内容合规与敏感词

style_prefix.py：
- 定义固定 STYLE_PREFIX
- sanitize_prompt()：替换敏感词（暴力/色情/恐怖）
- validate_sensitive_words()：残留敏感词校验
- inject_global_config_into_script()：注入全局渲染参数

content_policy_fix.py：
- 解析 422 错误信息提取疑似敏感词
- auto_fix_prompt()：自动替换并重试
- 本地敏感词库持久化（.cache/sensitive_words.json）
- 内置词库：暴力约 30 词、色情约 16 词、恐怖约 20 词

scene_compliance.py：
- 场景矛盾检测
- 逐镜合规过滤

---

## 七、缓存机制

| 级别 | 条件 |
|------|------|
| Prompt 哈希 | prompts.py + novel_preview + 章节号组合哈希不变则跳过 LLM |
| 首帧/尾帧 | 每镜 prompt_hash.txt，只有 prompt 变化才重新生成 |
| 视频片段 | 文件 > 20KB 且 mtime >= script mtime |
| TTS 音频 | 文件 > 10KB 则复用 |
| 拉伸视频 | 时长匹配且未设 DEBUG_SKIP_CACHE |

---

## 八、关键配置

文本/图像/视频/TTS 供应商：
- TEXT_PROVIDER：LLM 供应商（deepseek/dashscope/agnes）
- VIDEO_PROVIDER：视频供应商（agnes/dashscope）
- IMAGE_PROVIDER：图片供应商（agnes）
- TTS_PROVIDER：TTS 供应商（dashscope_sdk/dashscope_http/openai）

章节与分镜：
- NOVEL_CHAPTER：处理的小说章节
- BOOK_INDEX：书号索引
- SHOT_COUNT_MIN/MAX：分镜数量范围
- HOOK_ENABLED：钩子分镜开关（默认 1）

视频生成（step02）：
- VIDEO_CLIP_INTERVAL：视频提交间隔（同 key 61s/不同 key 30s）
- VIDEO_CONCURRENCY：视频提交并发（默认 4，单源 shared/config.py；.env.content 默认未设）
- VIDEO_MAX_REQUESTS_PER_MINUTE：RPM 限流
- VIDEO_MAX_RETRIES：单镜最大重试次数（默认 3）
- **轮询参数**（POLL_xxx，定义在 step02_video_gen/submit_poll.py）：
  - POLL_BASE_INTERVAL：初始轮询间隔（默认 180s）
  - POLL_MAX_INTERVAL：最大轮询间隔（默认 180s——与 BASE 相等时指数退避不生效，恒定 180s）
  - POLL_EXPONENTIAL_FACTOR：退避系数（默认 1.5x）
  - POLL_JITTER：随机抖动范围（默认 ±10s）
  - INITIAL_POLL_DELAY：提交后首次等待（默认 180s）

图片生成：
- IMAGE_GEN_INTERVAL：图片生成间隔（默认 21s）
- AGNES_IMAGE_GENERATE_TIMEOUT：图片生成超时（默认 180s）

视频生成：
- AGNES_VIDEO_SUBMIT_TIMEOUT：提交超时（默认 180s）
- AGNES_VIDEO_FETCH_TIMEOUT：轮询超时（默认 1200s）

Step04 合成：
- STRETCH_RATIO_MAX：最大拉伸比率（默认 1.333）
- COMPLIANCE_AUTO_FIX：自动合规修复（默认 1）

---

## 九、已修复缺陷（共 6 个）

1. shared/vimax_adapters.py:106 — 点号后多余空格（_time_module. monotonic()），执行到此分支时会 SyntaxError
2. shared/vimax_adapters.py:115 — 函数名拼写错误（auto_fix_rompt → auto_fix_prompt），遇到 422 错误时 NameError
3. step04_merge/merge_core.py:529 — MB 计算错误（10*10240*10240 应改为 1024*1024），显示的 MB 值约为实际值的千分之一
4. step04_merge/merge_core.py — 淡出转场缺少 _run_cmd(cmd) 和 return，实际效果降级为硬切
5. step01_script_gen/main.py:229 — generate_gap_report 调用时传入不存在的文件路径（script_result.partial.json 尚未保存）
6. step01_script_gen/prompts.py:548 — 函数名 parse_hooK_response 中字母 K 大写不一致

---

## 十、其他注意事项

- content_policy_fix.py 的 _infer_substitute() 中存在死代码（不可达的 else 分支），不影响运行
- gap_analysis.py 的高潮关键词是英文但旁白文本是中文，关键词匹配不会生效（已知限制）
- merge_core.py 的 apply_transition() 使用独立的 if 语句而非 elif 链，但因各分支有 early return，当前安全
- Windows GBK 控制台下 print() 必须使用 ASCII 安全字符（无中文/表情符号）
- Agnes 视频 API 要求 num_frames = 8n + 1（自动 clamp）
- Agnes 视频端点 base64 编码需要去掉 = 填充符（图片端点不需要）