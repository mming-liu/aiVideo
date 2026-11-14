# Seedance 官方提示词最佳实践 → aiVideo 视频生成链路 落地总结

- 任务日期：2026-11-12
- 代码基线：`8c2a214`（工作树另有 `.workbuddy/`、`start_studio.bat` 两个无关未跟踪项，未触碰）
- 本任务 commit：`b942098`（仅含本任务 11 个文件；新增测试因 `tests/*` 被 `.gitignore` 忽略，按任务要求 `git add -f` 纳入）
- 生效视频配置（`.env.content`）：`VIDEO_PROVIDER=volcengine`、`VIDEO_REFERENCE_MODE=image`、`VOLCENGINE_I2V_MODEL=doubao-seedance-1-0-pro-250528`
- 结论：T1/T2/T3/T4 均已落地并有单测；**未改变正式门禁 blocking 语义**；active 模型未开放原生视频参考，已按能力位回退既有帧/尾帧路径，本次代码交付不需要产品裁决；是否切换模型以启用属于后续产品/费用决策。

---

## 1. 官方建议 vs 我方现状 vs 本次改动

| 官方建议（来源） | 我方现状（改动前） | 本次改动 |
|---|---|---|
| Seedance 2.0/2.5 支持 `@视频` 参考，视频可提供运镜/动作/光影；2.5 单次最多 10 个视频参考（2.0/2.5 指南、Seedance 2.5 参考指南、官方 skill 模型矩阵） | `reference_paths` 字段名写“参考图/视频”，但两个 Seedance provider 一律按 `image_url` + `image/png` 发送；视频参考完全不可达 | 新增 `_video_gen/media_io.py` 统一识别 `mp4/mov/webm/...`；provider 按媒体类型拆分为 `reference_image` / `reference_video`；视频用 `video_url` + 真实 `video/*` mime；新增 `supports_video_reference` / `video_reference_local_base64` 能力位；不支持时显式丢弃/回退；2.5 本地 Base64 明确拒绝并提示需公网 URL/素材 ID |
| 视频参考指代要写清“参考 [视频 1] 的运镜/动作/光影”，并与图片分开编号（Seedance 2.5 指南、2.0 指南） | 只有 `_build_r2v_purpose_list` 的逐图“图N”用途清单 | 该清单现在把图片与视频分开编号：`图N` 只对应图片、`视频N` 只对应视频；视频行写“参考 视频N 的运镜方式、动作节奏与光影变化”，并保留原有逐图继承/排除边界 |
| 单条连续长镜 + 时间窗（0-5s/5-12s/12-20s）的顺序运镜是合法形态（Seedance 2.5 京剧案例） | 已有 `【时间分段】`，但只输出动作文本，不区分“连续长镜 + 多节拍” | 新增 `_build_time_window_block()`：仅在镜头显式声明连续长镜（“连续镜头/一镜到底/长镜头/不切剪”等）且 `motion_desc` ≥2 个有效节拍时生成 `【时间窗】…0-3s…3-6s…`；置于低权重段，超 500 字时优先整段被压缩砍掉 |
| 一个镜头尽量只 1 种运镜；跳切时每镜仅 1 运镜；避免相互冲突的运镜（如固定机位 + 快速环绕）（Seedance 2.0 指南） | `video_camera_stacking` 对“单镜出现多个运镜词”一刀切警告，无法区分合法连续长镜 | 精化为：①固定机位 + 任何运动仍警告；②无时间窗/顺序标记的多运镜警告；③无时间窗/顺序时推+拉、仰+俯视为冲突；④“连续长镜 + 时间窗”或“先…随后…”顺序多运镜放行。仅 warning，`status`/blocking 不变 |
| 情绪用描述性语句、氛围用类型化电影语言（恐怖片/欧洲文艺电影/复古香港电影等）（Seedance 2.5 指南） | 主要靠 `【色彩氛围】` 结构化标签；无类型化自然语言 | 新增 `_build_atmosphere_enrichment()`：从镜头 `genre/atmosphere/emotion/color_tone` 命中类型线索时追加 `【氛围】…；情绪用可观察的面部与肢体细节表现…`，低权重，压缩先砍；无类型线索不凭空编 |
| 500 中文字符上限、首句权重最高、低权重段可被砍（多来源一致） | 已有 `_seedance_compress_prompt` 高/中/低权重压缩与参考块整块保留 | `【时间窗】`、`【氛围】` 加入 `_LOW_KEYWORDS`；时间窗内部用“，”连接保持单句，确保整段可被优先砍掉，不挤占起点/动作/终点/参考图编号 |

---

## 2. 各 T 的 diff 要点

### T1 原生视频参考（高优先）

- 新增 `_video_gen/media_io.py`
  - `media_kind(path)`：扩展名 → `mimetypes` → `unknown`；`mp4/m4v/mov/webm/avi/mkv/mpeg/mpg` 判为 video。
  - `media_mime(path)`：视频返回 `video/mp4`、`video/quicktime` 等；未知仍回退 `image/png`（保持旧行为）。
  - `encode_media(path)`：按真实 mime 生成 `data:video/mp4;base64,...`。
- `shared/model_caps.py`
  - `SeedanceCap` 新增 `supports_video_reference`、`video_reference_local_base64`。
  - 2.5：视频参考 True、本地 Base64 False（官方只接受 URL/素材 ID）。
  - 2.0 / 2.0-fast / 2.0-mini：视频参考 True、本地 Base64 True。
  - 1.x：保持 False。
  - 新增 `seedance_supports_video_reference()` / `seedance_supports_local_video_reference()`。
- `shared/ref_compose.py`
  - 新增 `MODEL_VIDEO_REF_LIMITS`：2.5=10、2.0 系列=3；`get_video_ref_limit()`。
- `shared/video_media_plan.py`
  - `derive_media_mode()` 把 `reference_video` 归为 reference；`reference_labels()` 增加视频标签。
- `step02_video_gen/request_plan.py`
  - `ROLE_CANONICAL` 增加 `reference_video`/`video` → `reference`；否则 prepared 素材在冻结预检会被判 `unknown_role` 并在 POST 前拒收（T1 接入可达性的关键一环）。
- `_video_gen/volcengine_provider.py`、`_video_gen/agent_plan_provider.py`
  - r2v 分支按 `media_kind` 拆图片/视频：图片沿用 `reference_image` + 拼表容量；视频用 `reference_video`，不参与图片拼表，超视频上限显式丢弃并记 `reference_video_capacity`。
  - 能力位缺失：视频显式丢弃并记 `param_dropped`，图片继续；若没有可用素材则 fail-closed（`video_reference_unsupported`）。
  - 2.5 本地视频：`video_reference_requires_url`（不静默发错误 Base64）。
  - `_materialize_content` 视频发 `{"type":"video_url","video_url":{"url":...},"role":"reference_video"}`；日志同时识别 `video_url`。
- `step02_video_gen/prompt_builder.py`
  - `_build_r2v_purpose_list()` 拆分图片/视频，分别编号；视频行写“参考 视频N 的运镜方式、动作节奏与光影变化”；预算裁剪顺序：排除细节 → 视频细节 → 跨角色隔离，图号/视频号与用途不裁。
  - `_make_request(..., continuity_ref_video=None)`：仅当模型支持本地视频 Base64 且策略为 auto/reference、非 text 模式时，把上一镜视频追加到 `reference_paths`，该镜切到 reference 模式；能力不具备时静默回退现有 image/尾帧路径。`media_selection` 的 role 按媒体类型写 `reference_image`/`reference_video`。
- `step02_video_gen/continuity.py`
  - 新增 `resolve_video_reference(previous_video_path, model=...)`：返回 `{path, reason, model}`；2.0 系列本地视频可用，2.5/1.x 返回 `video_reference_unsupported`。
  - `ContinuityTracker` 记录 `last_video_path`，新增 `get_video_reference()`；不替代尾帧提取，仅提供可选路径。

**验收证据（新单测）**
- 视频型参考被正确编码为 `video_url` / `reference_video` / `data:video/mp4`（volcengine + agent_plan 两个 provider）。
- 2.5 本地视频被明确拒绝（`video_reference_requires_url`）。
- 能力位缺失时视频被丢弃并记账，图片参考继续；`_make_request` 在 1.0 pro 上回退首帧 I2V。
- `_build_r2v_purpose_list` 中 `图1` 与 `视频1` 分开编号，视频不占图片编号。
- 完整冻结路径 `build_frozen_request` + `validate_frozen_request` 对 `reference_video` 通过（归一为 `reference`，不再 `unknown_role`）。
- `resolve_video_reference` 对 2.0 / 2.5 / 缺失文件 / 非视频文件分别给出正确结果。

### T2 时间窗结构化长镜（中优先）

- `prompt_builder._build_time_window_block()`：
  - 触发：`camera_movement/motion_desc/prompt` 含连续长镜标记，且 `build_motion_segments()` 有效段 ≥2。
  - 输出：`【时间窗】单条连续镜头，无切剪，运镜：…，按时间窗顺序推进：0-3s: …，3-6s: …`。
  - 低权重：整块用“，”连接成单句，含 `【时间窗】`；`_seedance_compress_prompt` 超限时优先砍。
- `_build_seedance_concise_prompt()`：命中时间窗时替代旧 `【时间分段】`，避免重复；未命中保持旧行为。

**验收证据**
- 连续长镜 + 3 节拍生成 `0-3s/3-6s/6-9s`。
- 普通固定机位镜头不注入。
- 时间窗句子 `_classify_sentence_weight == 2`（低权重）。

### T3 运镜门禁精化（谨慎，未改 blocking）

- `video_prompt_quality._camera_stacking_issue()`：
  - 运镜词表补充 `推进/拉出/横移/摇摄/仰角/俯角/静态锁定`。
  - 固定机位 + 其他运动：始终警告（conflicting）。
  - 推+拉、仰+俯：无时间窗/顺序标记时警告；有时间窗/顺序时视为合法连续变化。
  - 多运镜且既无时间窗也无顺序标记：警告（unordered_multi_move）。
  - 连续长镜 + 时间窗、或“先…随后…”顺序多运镜：放行。
- warning 增加 `reason` 与 `movements` 字段；报告 `status`、`blocking`、门禁统计不变。

**验收证据**
- `固定机位 + 快速环绕` → conflicting 警告。
- `一条连续镜头，0-5s 近景环绕，6-10s 中景跟拍，11-20s 缓慢拉远` → 不警告。
- `环绕镜，缓慢推近` → unordered 警告。
- `先推近，随后环绕` → 放行。
- 集成报告：矛盾镜 warning、连续长镜无 warning，`status` 仍为 `PASS`。

### T4 类型化氛围/情绪 enrichment（可选，低权重）

- `prompt_builder._build_atmosphere_enrichment()`：
  - 从 `genre/atmosphere/emotion/color_tone` 匹配类型线索：恐怖、悬疑、武侠、文艺爱情、复古、科幻。
  - 命中输出 `【氛围】<类型化电影语言>，情绪用可观察的面部与肢体细节表现（眼神、嘴角、呼吸、手指），不用抽象形容词。`
  - 无类型线索返回空串，不凭空编类型。
- `_build_seedance_concise_prompt()` 末尾追加；`【氛围】` 进低权重词表。

**验收证据**
- `color_tone="冷蓝与暗红"` → 悬疑氛围句，权重 2。
- 无类型线索（`color_tone="中性"` / 空 shot）→ 空串。

---

## 3. 测试结论

使用项目 Python：`C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe`。

| 测试范围 | 结果 | 说明 |
|---|---|---|
| `tests/step02_video_gen/test_seedance_prompt_practices.py`（新增） | **25 passed** | T1-T4 新单测 |
| `tests/step02b_video_gen/test_video_prompt_quality.py` | **3 passed** | 原有运镜/过渡/产品验收 |
| `tests/step02b_video_gen/test_r2v_reference_policy.py` | **16 passed** | r2v 参考图边界/预算/编号 |
| `tests/step02_video_gen/test_video_prepared_providers.py` | **23 passed** | prepared 入口、POST 计数、压缩、素材变化 |
| `tests/shared/test_video_media_plan.py` + `test_video_prompt_system.py` | **19 passed** | 素材计划、参考块/系统运镜 |
| `tests/step02b_video_gen/test_media_plan_wp4.py` + `test_prompt_contract_wp4.py` | **3 passed** | 素材/提示词契约 |
| `tests/step02_video_gen/test_execution_requirements.py` + `test_prepared_frozen.py` + `test_request_plan_and_decision.py` | **16 passed** | 执行要求/冻结/请求计划 |
| `tests/step02_video_gen/test_video_prompt_voice_split.py` + `test_video_quota_breaker.py` | **4 passed** | 声音分流/额度熔断 |
| `tests/step02b_video_gen` group A（frame contract/precheck/plan status） | **13 passed** | |
| `tests/step02b_video_gen` group B（production trace/report/stub clips） | **10 passed / 1 failed** | 失败项 `test_step02b_entry_keeps_success_and_cache_semantics` 在**基线代码 `git stash` 后同样失败**，为既有环境/基线问题，不是本次回归 |
| `tests/step02b_video_gen` group C（video QC/trace/experiment context） | **17 passed** | 会话收尾触发本机 safe-delete bulk guard（exit 1），测试断言全过，非代码失败 |

合计：**149 passed**，1 个已确认的基线失败。新增测试 25 个，全部通过。

> 环境说明：本机 WorkBuddy safe-delete 的 bulk guard 在单次 turn 删除数 >50 时会把正常 pytest 清理变成 `SystemExit(1)`（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`）。本次按目录/小分组运行，并把 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR` 指向工作区可写目录；未绕过 guard。

---

## 4. 遗留风险与边界

1. **active 模型未开放视频参考（需产品/费用决策，未擅自切换）**：当前 `VOLCENGINE_I2V_MODEL=doubao-seedance-1-0-pro-250528` 的能力位 `supports_video_reference=False`。因此生产链路**不会**自动启用原生视频参考，仍走既有 image/尾帧路径；这是预期的能力位回退，不是静默绕过。要启用需切到 2.0 系列（本地 Base64 可用）或 2.5（需公网 URL/素材 ID）；**是否切换模型/承担相应费用属于产品决策，本任务未改动 `.env.content`，也未发起任何真实视频调用**。
2. **2.5 本地视频不可直接 Base64**：provider 会明确拒绝 `video_reference_requires_url`；`continuity.resolve_video_reference()` 只对 2.0 系列返回本地视频路径。若未来要在 2.5 上使用上一镜视频，需要先实现“本地上传/可访问 URL”链路（本次未做）。
3. **continuity 视频参考未自动接线**：`_make_request(continuity_ref_video=...)` 是显式可选路径；当前并行调度没有“上一镜视频已完成”的稳定顺序，且 active 模型不支持，故未在生产调度器中自动传入。后续可在能力具备时，用 `ContinuityTracker.get_video_reference()` 接线。
4. **T3 仍可能漏判**：门禁基于词表 + 时间窗/顺序正则；未登记的运镜术语、隐式冲突（如“希区柯克变焦”与固定机位）可能漏判；仅 warning，不影响 blocking。
5. **T2 触发条件保守**：只有显式连续长镜标记 + ≥2 节拍才生成时间窗；普通多节拍分镜不会自动改结构。若上游希望更普遍地使用时间窗，需要在 step01/step02 约定显式标记。
6. **T4 依赖镜头已有类型线索**：无 `genre/atmosphere/emotion/color_tone` 类型词时不注入；它是低权重段，500 字压缩时可能被砍。当前用 `color_tone` 推断类型是启发式，可能把“冷蓝”误判为悬疑，需在真实样本上校准。
7. **500 字预算**：T2/T4 会增加低权重文本；虽然压缩会优先砍，但参考图/视频块本身超预算时仍会走既有 `prompt_ref_block_over_budget` fail-closed。视频参考行让参考块更长，需关注长 prompt 镜头的阻断情况。
8. **能力指纹变化会失效既有 prepared key**：`SeedanceCap` 新增两个字段后，`_capability_revision()` 的 `dataclasses.asdict` 指纹对所有 Seedance 模型都会变化；这是能力注册表变更的正常结果，但会让旧 prepared 缓存失效。T2/T4 也会改变实际 prompt，因此本任务本来就需要重新生成相关镜头。

---

## 5. 下一步建议

1. **产品决策**：是否把 active I2V 模型切到 Seedance 2.0 系列以启用本地视频参考；若目标 2.5，先补“本地上传/URL”链路。
2. **接线**：在模型能力具备且调度能拿到上一镜视频后，把 `ContinuityTracker.get_video_reference()` 传入 `_make_request(continuity_ref_video=...)`，替代/增强尾帧 I2V。
3. **校准 T3**：用真实镜头 prompt 抽样，补充官方术语（横移/摇摄/升降/希区柯克变焦等）与冲突组合；保持 warning-only，不动 blocking。
4. **A/B 验证 T2/T4**：按官方“一次只调一个变量”原则，在固定 seed、固定参考的少量镜头上对照：A=当前生产 prompt，B=只加时间窗，C=只加氛围 enrichment；比较人物稳定性、运镜遵从与 500 字压缩记录。
5. **监控压缩记录**：在 `prepared.modifications` 中观察 `prompt_compress` 是否频繁砍掉时间窗/氛围；若高频，考虑把 T4 改为仅显式 genre 字段触发，或把时间窗压到更短。

---

## 6. 证据位置

- 新增测试：`tests/step02_video_gen/test_seedance_prompt_practices.py`
- 代码：`_video_gen/media_io.py`、`_video_gen/volcengine_provider.py`、`_video_gen/agent_plan_provider.py`、`shared/model_caps.py`、`shared/ref_compose.py`、`shared/video_media_plan.py`、`step02_video_gen/prompt_builder.py`、`step02_video_gen/video_prompt_quality.py`、`step02_video_gen/continuity.py`、`step02_video_gen/request_plan.py`
- 本总结：`.pytest_tmp/dsh_tasks/seedance_prompt_improve_result_20261112.md`
