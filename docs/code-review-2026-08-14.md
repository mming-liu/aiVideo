# aiVideo 全项目代码审查报告

- 审查日期：2026-08-14
- 审查范围：核心 Python 流水线、provider 层、共享工具、vimax 桥接（不含 `skills/`、`reference_skills/`、`.claude/`、`.agents/`、`.aiVideo/` 等第三方/参考资源）
- 审查对象：`shared/`、`step01_script_gen/`、`step02_video_gen/`、`step02a_frame_gen/`、`step02b_video_gen/`、`step03_tts_audio/`、`step04_merge/`、`tts/`、`_text_gen/`、`_image_gen/`、`_video_gen/`、`vimax/`、`tools/`、根目录 `.py`（约 115 个文件）
- 仓库：`C:\myFloders\git\aiVideo`
- 审查方式：4 个子系统并行人工走查 + 关键发现二次人工复核（复核项在报告中以「✅已复核」标注）
- 上一份报告：`docs/code-review-2026-08-06.md`（其中多项问题本次复查仍存在，见 §5）

## 严重度摘要

| 级别 | 数量 | 核心主题 |
|------|------|----------|
| 高 | 7 | TTS 变量笔误（复现）、submit_poll logger 未定义、无限递归、img2img URL 覆盖、step02b zip 错位、跨事件循环信号量、QC 返回已删路径 |
| 中 | 15 | 换 key 预算共用、响应未校验、字幕引号判定、资产库共享状态、logger/信号量、硬编码章节名、配置双数据源等 |
| 低 | 22 | 死代码、重复代码、裸 except、句柄不关、硬编码路径/中文、编码细节等 |

## 高优先级问题

### 1. `step03_tts_audio/shot_tts.py` TTS 变量名笔误仍存在（08-06 报告问题 1 未修复） ✅已复核

- 异步路径 `_gen_shot_tts_async` 第 290 行 `voice_Config = voice_registry.get_voice(...)`（大写 `C`），后续第 300/303/308/309 行引用小写 `voice_config`。
- 一旦检测到说话人（有角色库的小说必然触发），第 300 行抛 `NameError`；该异常位于 `try` 块（305 行）之前，直接传播到 `asyncio.gather`，使 step03 整个 TTS 生成退出（main 仅捕获后 `sys.exit(1)`）。
- 同步版 `gen_shot_tts` 第 244 行引用从未赋值的 `output_path`（异步版 294 行才有赋值），同步路径同样 `NameError`。
- **影响**：step03 主入口默认 `use_async=True`，多角色小说必然崩溃。
- **建议**：统一变量名为 `voice_config`，函数入口初始化 `voice_config = NARRATOR_VOICE.copy()`，并补 `output_path` 赋值。

### 2. `step02_video_gen/submit_poll.py:394` `_submit_video` 引用未定义的 `logger` ✅已复核

- `logger` 只在 `_qc_video_product`/`_poll_video`/`_process_one_shot`/`generate_alls` 内局部定义（模块级无定义）。
- `_submit_video` 第 394 行 429 限流分支 `logger.verbose(...)` 抛 `NameError`，且发生在 except 处理器内部，不被自身捕获 → 直接炸掉整个 `asyncio.run`。
- **影响**：提交遇 429 这一高频场景让 step02 崩溃而非按设计重试。
- **建议**：在 `_submit_video` 内定义 `logger`，或提升为模块级。

### 3. `shared/chapter_reader.py` `_recursion_depth` 守卫永不触发 → 无限递归 ✅已复核

- `filter_section_by_chapter`（385 行）的 `_recursion_depth` 参数从不递增，唯一调用点 `extract_section`（221 行）也不传值。
- 调用链 `extract_section → filter_section_by_chapter → _get_chapter_scenes → extract_chapter → extract_section` 形成闭环，每次递归到 `RecursionError`（406 行）兜底返回原文。
- **影响**：LLM 场景分析功能静默失效（永远不执行），且每层对 12MB 小说文件重复 `find_sections` 全量解码，性能灾难。
- 另：`filter_section_by_chapter` 本身是文档化的空操作（恒返回原文），只烧 LLM 额度。
- **建议**：调用点传入 `_recursion_depth+1` 并在函数内自增；同时明确该过滤功能是否已废弃。

### 4. `_image_gen/agnes_provider.py` 端点 URL 被参考图数据 URL 覆盖 → 图生图不可用 ✅已复核

- 第 109 行 `url` 赋值为 API 端点；第 136/143 行编码参考图时又用同名变量 `url = self._encode_image(ref_path)` 覆盖为 `data:image/...;base64,...`。
- 第 226 行 `requests.post(url, ...)` 实际 POST 到 data URL，抛 `InvalidSchema`；重试循环还会把它当连接错误再烧 3 次重试。
- **影响**：img2img（参考图模式）在此 provider 上整体不可用。
- **建议**：编码循环改用独立变量名（如 `ref_data_url`）。

### 5. `step02b_video_gen/main.py:96-103` zip 错位导致失败计数与日志张冠李戴

- `polls` 只收集返回非 None 的镜头（缓存命中/3 次提交失败返回 None 的不在其中），但 `zip((t["index"] for t in tasks), results)` 用**全部** `tasks` 配对。
- 任一镜头返回 None 后，后续结果全部错配到错误镜头；且 `_poll_video` 内部异常不进入 `results`，step02b 的 `Failed: N` 基本恒为 0。
- **建议**：按 index 建字典映射而非 zip；失败计数统一读取 `submit_poll._failed_videos`。

### 6. `shared/vimax_adapters.py:41,167-181` 类级 Semaphore 跨事件循环崩溃

- `ImageGeneratorAiVideoAdapter._global_semaphore` 类级懒加载，首次 `generate_single_image` 创建时绑定当前事件循环。
- `step02a_frame_gen/main.py` 用 3 个独立 `asyncio.run()`（build_working_dir / gen_all_assets / gen_all_frames），只要前两个 run 触发过图片生成，run3 `async with semaphore` 抛 `RuntimeError: bound to a different event loop`。
- `VideoGeneratorAiVideoAdapter`（197 行）同模式，step02/step02b 之间同样跨 loop。
- **建议**：每次 `asyncio.run` 重新创建信号量，或改为运行时按 loop 缓存。

### 7. `shared/vimax_bridge.py:2166/2344/2547` QC critical 末次尝试返回已删除的文件路径

- 首帧/尾帧/关键帧生成后：写哈希 → QC 检出 critical → unlink 帧+哈希，若 `attempt == max_retries` 不 continue，直接 `return str(ff_path)`，返回不存在的路径。
- 默认 `max_retries=1` 时两次 QC 全 critical 必触发；下游 provider 读不存在的参考图。
- **建议**：末次尝试返回 None 并在上层按失败处理。

## 中优先级问题

### 正确性 / 业务

8. `step04_merge/srt_gen.py:96-105` — `has_dialogue`（含「」『』全角引号）算而不用；`is_dialogue` 只认 ASCII 引号与 `«»` → 纯全角/直角引号且无说话动词的台词被丢弃，与 `shared/dialogue.py` 标准不一致，部分对白镜头无声字幕。

9. `shared/assets_bank.py:33-52` — `_next_asset_id` 给无 id 旧条目分配 P01/P02 后不更新 `max_n`，新条目返回 P01 撞车。
   - `:101,280` `dict(_EMPTY_SCENES)` 只拷贝顶层，嵌套 `scenes`/`meta` 全实例共享，一个实例写入会污染全局常量及其它实例。

10. `shared/content_policy_fix.py:117` — `discovered_at` 写入的是 `str(Path.cwd())` 而非时间戳，字段名误导，无法追溯发现时间。

11. `step03_tts_audio/main.py:67-70` — 全部 `shot_N.mp3` 存在即 return，绕过 `.voice` 音色变更再生逻辑；改音色配置后旧音频被原样使用。

12. `step04_merge/stretch.py:51` / `main.py:329` — 时长探测失败静默返回 `0.0`，直接拼 `trim=duration=0.00` 生成零帧输入，无任何提示。
    - `stretch.py` 已退化为纯时长工具，不再做音频拉伸，音画同步仅靠 step01 `duration_sec` 粗控制。

13. `shared/style_cinematic.py:130-165` — `_normalize_style` 把空格/连字符替换为 `_`，但 `_ALIASES` 键含空格（"david fincher" 等）永不命中，这些风格别名静默返回空前缀。

14. `step02_video_gen/submit_poll.py:49` — 模块级 `_failed_videos` 在 `generate_alls` 开头不清空，同进程多次运行（多章/重试）累计上一章失败记录，汇总计数失真。

15. `step02_video_gen/continuity.py:96` — `extract_tail_frame` 的 `subprocess.run` 无 `timeout`，ffmpeg 卡死时整个 Mode B 串行链永久挂起（对照 video_qc.py:55 有 timeout=30）。

### Provider / 错误处理

16. `_text_gen/agnes_provider.py:127-139` — 401/402 换 key 与连接错误/5xx/429 共用 `_submit_retry` 计数器，默认 3 次预算下第 4 个及以后备用 key 永远轮不到；实例级 `_current_key_index` 跨调用、跨 `asyncio.to_thread` 线程无锁持久化，坏 key 持续拖累后续请求。

17. `_image_gen/bailian_provider.py:240`、`_text_gen/deepseek_provider.py:157,181` — `choices[0]` 无越界/None 校验，空响应抛 `IndexError`/`AttributeError` 且被外层宽 except 吞成无意义错误。
    - `bailian_provider.py:135,215` `ratio_str`（9:16 等）只进日志不进请求，长宽比配置静默失效；`:102` `_current_model`/`_advance_model` 死代码。

18. `tts/voice_design.py:116` — `res.json()` 无保护，网关返回 HTML 错误页抛 `JSONDecodeError`（非 `RequestException`）未被捕获 → 真实崩溃路径。

19. `tts/voice_registry.py:63,157` — `load()` 失败只 WARN 导致音色全部重分配（跨章节声音漂移）；`save()` 非原子覆写，写一半崩溃损坏注册表；`get_registry()` 单例无锁，`get_voice` 的 mapping 写入与落盘竞态。

20. `tts/dashscope_qwen3.py` — `TTSRequest` 无 `voice_id` 字段，`getattr(req, "voice_id", None)` 恒为 None，voice_registry 分配的 sambert 音色到不了 qwen3 provider（恒用硬编码默认）；`_split_text_by_limit`（45-53）空段抛 `ValueError` 无重试。
    - 另 `dashscope_qwen3.py:3` 模块导入即 `sys.stdout.reconfigure`，进程级副作用。

21. `_video_gen/` 三 provider — 轮询中单次瞬时查询错误直接杀镜头：`agnes.wait_for_completion` 非 429 直接 raise；`dashscope.query_task` 无 429/5xx 重试且 `rsp.output["task_id"]` KeyError 无保护；`http_dashscope` 的 `res.json()` `JSONDecodeError` 未捕获。此类失败只进 `_failed_videos` 无自动重跑。

22. `shared/tee_logger.py:136-141` — 全局 logger 一旦创建永久绑定第一个 `log_path`，同进程混用 step02a/02b 时日志全写进第一个文件。

23. `shared/key_scheduler.py:30-49` — `acquire()` 无超时，所有 key 被长冷却（429 的 600s）时工作线程无限阻塞，`asyncio.to_thread` 线程池被占满。

### 配置 / 一致性

24. `shared/config.py` vs 各模块 `config.py` — 同一批默认值双份定义已漂移：`DEEPSEEK_MODEL`（`deepseek-v4-flash` vs `deepseek-chat`）、agnes 图片模型版本不一致。改一处漏一处。

25. `_text_gen/config.py:75` — `validate()` 只查 `AGNES_API_KEY`，只配备用 key 被判缺配置（与 `_image_gen` 循环查 1~20 口径不一致）。

26. `step01_script_gen/main.py:848` — 角色提取兜底路径 `extract_characters` 无任何 try/except，是唯一「失败即硬崩溃」的提取步骤（其余均为失败不阻断）。

27. `step01_script_gen/validation.py:4721` — `_check_prop_spatial_anchor` 逐镜循环内反复 `json.load(open(script_result.json))`，句柄不关、裸 except 吞错、且校验磁盘上可能过期的文件而非内存 script。

28. `style_prefix.py:1238+` — 硬编码第一章角色名（杨旭/听香/张十三/老汉/肖管事），换章/换书即注入错误约束；`:1422` 局部维护敏感词表，与「收敛到 `shared/content_safety.py`」注释承诺相悖。

29. `step01_script_gen/timecode.py:150-151` — 先帧对齐再 `max(duration, MIN_SHOT_DURATION)` 钳制，短镜头取最小值时帧对齐被破坏，与「防多/少一帧卡顿」目标自相矛盾。

## 低优先级问题

30. **重复代码**：provider 复制粘贴（`_encode_image`/`_download` 在 agnes/bailian 逐行相同）、`_find_exe` 在 stretch/merge_core/continuity/video_qc 各一份、`service.py` 注册工厂四处同构、`_detect_speaker_from_narration`（shot_tts 68-113）与 `shared/dialogue.py` 双实现、`audio_pregen.py:51-65` 两个逐行相同函数。

31. **死代码**：`AGNES_TEXT_ENDPOINT`/`AGNES_IMAGE_ENDPOINT` 定义未用（provider 硬编码 `/v1/...`，08-06 报告问题 11 未修复）；`bailian_provider.py` `_current_*_model_index`、`http_dashscope_provider.py:88-92` 紧跟 raise 的死代码、`merge_core._run_cmd` 未调用、`step04_merge/main.py:26` `TEMP_DIR` 未用、`agnesis_provider` `_api_key`/`_switch_to_backup_key` 未用、`vimax/utils/` timer/image/video 大量未调用函数、`prompt_builder.py:885-934` 空分支。

32. **安全/规范**：`_video_gen/agnes_provider.py` 日志打印 API key 末 8 位；根目录小说文件名「锦衣夜行_77119小说.txt」多处硬编码（main.py:117/496/641/876、bench_reasoning.py:81）；`_image_gen`/`_video_gen` 文件名前缀由中文字符串驱动（`fname_prefix`），违反 AGENTS.md「避免硬编码中文」约定；`shared/character_bank.py`/`assets_bank.py` 用 LLM 提供的 identifier 直接拼路径未净化（低风险路径穿越）。

33. **裸 except 集群**：validation.py 6 处（928/2158/3525/4064/4468/4723）、prompts.py 4 处（589/635/741/870，动态 import skill 全吞）、`json_repair.py:226` 裸 `except:`、`hash_cache.py:46`、`main.py:133`。

34. **句柄泄漏**：`validation.py:4027`、`vimax_bridge.py:1144,1365`、`step01 main.py:718` 等多处 `json.load(open(...))` 不关句柄。

35. **编码细节**：`srt_gen.py:158` SRT 无 BOM，Windows 播放器按 ANSI/GBK 误读中文；`srt_gen.py:14` 毫秒 `int((s-int(s))*1000)` 浮点敏感可能出现 `,1000` 非法值；`chapter_reader.py` 两函数独立探测编码且优先级不同，GBK/UTF-8 双解时切片错位。

36. **杂项**：`step03 main.py:75` 交互式 `input()` 阻断自动化；`validation.py:5354` 字符类重复字符、`:5340` 空 `for…pass` 死代码；`bench_reasoning.py` `--apply` 直接改写 `.env.content` 可能破坏格式；`vimax/utils/timer.py` `self.format` 遮蔽内建；`image_qc.py:30` 默认值与注释不符（注释写 `=1（默认）` 实际 `=0`）。

## 与上次审查（08-06）的延续性

| 上次问题 | 本次状态 |
|----------|----------|
| TTS `voice_config` 变量错误（问题 1） | **仍存在**（高 #1），且本次确认同步/异步两条路径都有变量问题 |
| step02b 失败计数不可信（问题 3） | **仍存在**（高 #5），根因细化为 zip 配对错位 |
| AGNES endpoint 配置无效（问题 11） | **仍存在**（低 #31） |
| Bailian 图片异常路径 UnboundLocalError（问题 2） | 本次未复现同一代码点，但同类"响应字段无校验"问题仍存在（中 #17） |
| 全局 logger 合并日志（问题 7） | **仍存在**（中 #22） |

## 审查假设

- 本次审查聚焦项目自身 Python 实现，未逐行审查 `skills/`、`reference_skills/`、`references/` 中的第三方或参考资源。
- 未运行真实 API 调用、未生成视频，provider 实际响应兼容性无法完全验证。
- 审查过程中未修改任何文件；工作区存在 2 个未提交改动（`_text_gen/deepseek_schema.py`、`step01_script_gen/prompts.py`，为 char_layout 规则扩展，未发现与其相关的明显问题）。

## 建议下一步

1. 优先修复 7 个高优先级问题，其中 #1/#2/#3 为主路径崩溃，应先处理：
   - TTS 变量笔误 + `output_path` 缺失
   - submit_poll `_submit_video` 的 `logger`
   - chapter_reader 无限递归
2. 为修复项补充最小回归测试（TTS 多角色、submit 429、章节提取）。
3. 修复后跑 `python -m py_compile`（AGENTS.md 提供的验证命令）并执行一次完整 step03/step02 冒烟。
4. 处理中等级换 key 预算、响应校验、SRT 引号判定、资产库共享状态，作为第二批。
5. 长期：收敛 provider 重复代码、统一 config 单数据源、清理死代码与裸 except。
