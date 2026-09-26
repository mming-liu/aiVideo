# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

aiVideo 是一个 AI 驱动的短视频生成流水线，将中文小说文本转换为约 60 秒的抖音竖屏视频（古风权谋卧底风格）。整个流水线分为 4 个阶段，按顺序执行。

**⚠️ 编码规范：所有项目文件统一使用 UTF-8 编码，禁止使用 GBK 或其他编码。编辑文件时确保保存为 UTF-8。**

## Claude 定位与工作边界

`CLAUDE.md` 规定 Claude 的**实施、测试与运行准则**；`AGENTS.md` 单独规定 Codex 的审核、方案与技术方向建议职责，两份文件不再互为权威。阶段目标内的跨模块同源缺陷由 Claude 自行查证并修复；需要改变已定判据、容量、生产语义，或既有方案反复失效而必须重新定方向时，带证据交 Codex 独立复核，重大取舍仍由用户批准。不要自行堆叠局部补丁。

- 用户只要求审核、诊断或方案时默认只读；明确要求修改或运行时，在已授权范围内实施，并遵守下列工作准则。
- 实施前核对根因、影响面与验收；对同类缺陷检查其他入口和回退路径，优先修复共同契约，不用新增特例、阈值或修复轮数替代根因治理。
- 测试使用冻结输入和判别性反例；Step 01 分开报告新稿、实际选稿、审计/修复/提交及正式产物副作用。保护脏工作树和并行接入中的改动（包括 awesome-seedance）。

## 工作准则（按改动风险适用）

**1. 行为缺陷先查根因，再动手。**
修复 bug 或改变生产行为前，先复现并追踪触发链、影响面与失效的不变量，优先在共同契约处修复，
禁止「看到现象就改一处」的试错式补丁。根因尚未定位时写明已知证据与未排除的假设，
不得把猜测当结论。纯文案、注释和格式调整无需制造一份根因调查。

**2. 验证与风险相称。**
行为修复、契约或判据变化须补/更新能区分修复前后的回归，并跑受影响测试；
纯文档、注释、文案与不改行为的整理做相应检查，不强制新建测试。
报告实际运行的检查与结果；未覆盖或未实跑的部分明确说明，不把测试通过说成生产效果已验证。

**3. 阶段内自行完成，越界才请示。**
已确认的阶段目标、验收标准和红线构成授权边界。边界内必要、可回退的代码、schema、提示词、
测试及同族入口调整自行完成，不把「是否继续下一小步」作为默认收尾；完成后报告依据、改动和验证。
对实现细节有多种合理做法时，选最小、可验证、可回退的一种，并在汇报中说明假设，不因普通技术
选择暂停。只有超出阶段目标、改变已定判据或容量（如修复轮数、联动组上限、功能是否启用）、
引入新的产品/生产语义、超出已授权成本，或需要第 5 条所列的额外权限时，才带证据与选项请示。
命名、报错措辞、注释、测试夹具、明显笔误及等价整理可直接处理；不得借小调整扩大行为范围。

**留档按工作包而非按文件。** 日常改动在测试和最终汇报中记录即可，不要求每次新建 `docs/` 文件。
架构、生产契约或重大判据变化，以及需要跨轮追踪的取证，写一份可复核的专题留档；
同一工作包的补充更新原留档，不为每个小修另建一份。

**4. 费用确认按阶段分级，运行授权仍须明确。**

- **Step 01**：用户已授权该阶段运行后，预算可忽略；范围内必要的生成、审计与小范围复测不逐次报价或请示。
- **Step 02a**：先估算图片数量、模型与影响范围；已授权的小范围诊断可执行，批量生成、明显扩量或升档前须确认。
- **Step 02b**：每次付费视频生成前列明镜头/片段数量、模型、时长与预计消耗，等待用户明确人工确认；此前授权不沿用。
不自行用 `--force` 扩大运行范围。修改代码的授权不等于运行流水线的授权；目标或规模变化时重新核对边界。

**5. 仅额外的高影响动作需再确认。**
授权运行 Step 01 已包含该流程预期的模型调用、诊断快照及正式产物写入，不逐次请示；
授权的文件修改也包含必要的可回退编辑和测试产物。超出授权范围的外部写入或覆盖、
破坏性删除、仓库提交与发布仍须先说明具体影响并取得确认；不触碰无关用户改动。

**6. 只做被要求的范围，改动外科手术式。**
不顺手重构、不扩大范围；以一个可验收的工作包为单位修复同一根因涉及的入口，保持改动可回退；
除任务要求的行为变化外保留既有行为，尤其注意并发编辑中的用户改动。

**7. 宁可显式失败，不可静默伪造或静默降级。**
空值就报空值、失败就报失败：禁止用占位/伪造内容填空（如伪造旁白），
禁止把失败或截断当成功下发，禁止把「没检出」当「没问题」。

**8. 汇报量化且如实。**
给实跑数字（通过/失败数、阻塞数、耗时、消耗），不粉饰、不幸灾乐祸；
未经实跑验证的结论必须标注「尚未实跑验证」。

**9. 通用化与幂等。**
约束/修复逻辑对任何同类镜头都适用，不写死具体小说、角色名、场景名；硬修复必须幂等（反复跑不累积插入）。

## 常用命令

### 安装依赖
```bash
pip install -r requirements.txt
# 系统还需安装 ffmpeg 并加入 PATH
```

### 运行流水线（按顺序）
```bash
python step01_script_gen/main.py  # 阶段1: 小说文本 → 分镜 JSON
python step02a_frame_gen/main.py  # 阶段2a: 分镜 → 首帧/尾帧/关键帧图片
python step02b_video_gen/main.py  # 阶段2b: 图片 → 视频片段
python step03_tts_audio/main.py   # 阶段3: 旁白文本 → 语音音频
python step04_merge/main.py       # 阶段4: 片段 + 音频 + 字幕 → 最终 MP4
```

- 阶段2拆分为2a和2b，方便调试：可以先运行2a检查图片质量，确认OK后再运行2b生成视频
- 阶段2a 调试指定分镜：`--shots 3 --all-frames` 一次生成 shot 3 全部图片（首帧+尾帧+全部关键帧，覆盖 idx>=2 的 Mode B 跳过，会浪费额度）；`--frames ff,lf,kf0` 逐图选择（ff/lf/kf/kfN，默认 Mode A/B 行为）
- 每个阶段脚本都有交互式确认提示。
- 当 `VIDEO_REFERENCE_MODE=text` 时跳过分帧环节，直接文生视频；r2v 模式使用参考图（最多5个）保持角色/道具一致性

每个阶段脚本都有交互式确认提示。

### step01 脚本生成与硬修复层（v6.42 Constraint 框架；2026-09-12 删除旧 _hard_fix_* 路径）
- LLM 生成 → `_coerce_schema_types` → `_fill_missing_new_fields` → narration 兜底 → **`post_fix_shot_prompts`（追加式确定性清洗）** → **`run_hard_fix`（constraints/ Constraint 框架，70+ 约束）** → 注入全局配置 → `_validate_script`
- `post_fix_shot_prompts`：只追加不删除——声音指令/说话人入画/光位量化/抽象词剥离/远景禁五官/ff/lf normalize/kf 过渡态/combat 打击反馈/motion 分段归一/prompt 截断残片
- **硬修复层 = `constraints/` Constraint 框架**（`engine.run_hard_fix` 唯一入口，`ALL_CONSTRAINTS` 统一注册，按 `rules_index.py` 版本索引）
- **设计原则**：约束对任何古风/权谋/刺杀/追逐镜头都适用，不写死"小舟/杨旭"，换小说代码不用动；幂等是硬要求
- **旧 `_hard_fix_*` 家族（2973 行）与 `HARD_FIX_USE_OLD` 开关已于 2026-09-12 删除**：新约束只在 Constraint 框架，旧路径回退即降级；开关残留仅告警不生效。`_split_motion_to_seg_texts` / `_extract_scene_prefix` / 16 个约束共享常量从原区间迁出保留（被 constraints/ 与 post_fix 引用）
- **锚点校验器 3 处 bug**（`_check_grounding`）：①省略号折叠顺序——先折 `§` 再删标点再还原 `…`（旧顺序先删 `…` → 折叠失效）；②原文同步折叠 `…{1,}`（LLM `…` vs 原文 `……`）；③合规自动替换容忍——narration 里 `出人命` 还原为 `杀人` 再匹配原文（不破坏 narration 字段本身）
- **设计原则**：硬修复层**对任何古风/权谋/刺杀/追逐镜头都适用**，不写死"小舟/杨旭"，换小说（主角变林平之、场景变镖局）代码不用动；幂等是硬要求（避免反复跑累积插入「目光紧盯」+「目光紧盯」…）
- **位置**：`step01_script_gen/validation.py` → `hard_fix_shot_prompts()` → 由 `parse_response` 在 `post_fix_shot_prompts` 之后调用

### step01 doubao 路径（v6.60，2026-09-05）
- 目标：把 doubao-seed-2-1-turbo 分镜生成从综合分 78.7 → 92+，5+1 类 hard_fix 全填 + doubao_system.md 重写 + 3 处 prompt 路径一致性修复
- 启用方法：`TEXT_PROVIDER=agent_plan`（与 deepseek 路径二选一，**默认仍为 deepseek**）
- doubao 专用逻辑单开文件：
  - ⚠️ **这 3 个文件已于 54e4bfa（2026-09-06）删除，逻辑迁入 `step01_script_gen/constraints/`**——
    删除是**有意的**（去掉与主 Constraint 框架并行的第二套注册路径），
    别再判成误删去恢复：曾有一次误判为「误删」而重新注册，造成重复注册回归。
  - ~~`step01_script_gen/doubao_pipeline.py`~~ → 修复轮/GAP 重生已并回 `main.py`（`_regen_over_limit` / `_repair_shot_warnings` 统一走一条路径）
  - ~~`step01_script_gen/doubao_hardfix.py`~~ → 7 个 Constraint 迁入 `constraints/`，随 `ALL_CONSTRAINTS` 一起注册，无需 `register_doubao_hardfixes()`
  - ~~`step01_script_gen/doubao_repair_prompts.py`~~ → 修复 prompt 并入 `prompts.py`
  - 现存 doubao 专属文件只剩 `step01_script_gen/prompts/doubao_system.md`（system prompt）
- 5+1 类 hard_fix（**下列编号是历史命名，现对应 `constraints/` 下的同名约束**）：
  - **B.1** → `constraints/narration_subtitle_consistency.py`。
    ⚠️ v6.62.7（09-09）**已删除**「双向空 → 兜底 `某：……` 占位」这一兜底动作，改为纯告警：
    旧实现拿 `ff_desc[-20:]`（画面描述后 20 字）当旁白，会同时写进 narration_segment +
    subtitle + `script["narration"]`，一路烧进 SRT 和视频模型对白 prompt
    （实测落盘「香站在中舱，手持油纸伞，嘴唇微闭，某」——从中间切断，「听香」少了「听」）。
    **空旁白只是「这镜没台词」，伪造旁白是错内容进成片。**
  - **B.2** GAP 关键词强注入：core_keywords 命中率低 → ff_desc 末尾追加 anchor 短句
  - **B.3.1** 角色锚点注入：主角无 `[name: features]` → prompt 头部追加 ≤30 字特征
  - **B.3.2** 开篇空镜插入：前 3 镜无空镜/特写 → index=1 插环境空镜 + 末镜 -2s 联动保 60-90s 总和
  - **B.3.3** 高潮回灌：后半无高潮句 → 中点镜 prompt 追加"冲突到达顶点"短句
  - **B.4** 过肩机位断裂修复：3 镜同景同 cam 同对白 → 中间镜改"过肩镜头"
  - **B.5** frame_layouts 兜底：缺 pose / on_screen / kfs 长度不齐 → 字段补齐（on_screen 矛盾保守不动打 WARN）
- 全部走 Constraint 框架（与 `validation.py` 36 类 hard_fix 同构），register 一次性 append
- 全部幂等：每个 Constraint 自带 `repair_marker`，已修字段自动跳过
- 温度参数：保留 `models.py` dataclass 默认 0.05（保 deepseek 路径零修改），doubao 路径在 `main.py` GAP/修复调用点显式传 `temperature=0.2` 覆盖
- main.py 最小侵入：顶部按 TEXT_PROVIDER 选 prompt builder（`auto_select_prompt_builder`），不再有 doubao 专用 if/else 分支
- 退出 doubao 模式：unset TEXT_PROVIDER 即可回 deepseek（约束层两条路径共用，无需 revert）
- 仍保持 "doubao 2 次不切 deepseek" 决策（v6.59.1 user decision）— GAP fallback 走 doubao 自身修复轮，不升级 deepseek 兜底
- 单测：已随文件迁移并入 `tests/step01_script_gen/`（`tests/step01_script_gen/doubao_hardfix/` 目录已不存在）
- A/B：tools/bench_text_provider_ab.py 跑红果 + 锦衣各 1 章，total_pass_rate ≥92% / narration 非空率 ≥80% / camera 种数 ≥3 / core_keywords 命中率 ≥60%

### step01 kf 物理时刻 = motion_desc 时段铁律（v6.33，2026-08-18）
- **触发问题**：08-18 钩子实测，motion_desc 明确分「前2秒：破水+下刺 / 2-4秒：闷哼+推臂」两段，但 LLM 只生成 2 个 kf **都堆在 post 段**（杨旭已仰卧+血）——**根本没生成"破水+下刺"pre 帧**。08-16 v6.31 校验只检"单 frame pre+post 矛盾"，**不检"完全没有 pre 帧"这一类结构性塌缩**——校验覆盖盲区
- **解决（双层）**：
  1. **校验层新增** `_check_kf_time_window_alignment`（`step01_script_gen/validation.py`，warning-only，3 类检测）：①时段覆盖率（motion_desc 分 N 段 → kfs 个数 < N 报缺段）②时段归属（每 kf 物理时刻 t×duration_sec 不在任一时段内报错窗）③时段单调性（kfs 的 time_ratio 对应时段序号不单调报倒装）。辅助 `_parse_motion_time_segments` 解析「前N秒」「M-N秒」+ 全角 `～` `－`，显式段未覆盖到 duration 末尾时**自动补 implicit 段**（last_end, duration_sec）
  2. **源头规则 16.4**（`step01_script_gen/prompts.py`）：**「关键帧物理时刻 = motion_desc 时间窗铁律」**——kf 物理时刻 `time_ratio × duration_sec` 必须落 motion_desc 显式时段内；kf 个数 ≥ 时段数；写 kf 前先**列 motion_desc 时段表**，按表逐段生成 1 帧定格，`time_ratio = 时段中点 / duration_sec`
- **不做的修复**：**不派生 kf desc 硬修复**——超出确定性兜底范围（需语义理解从 motion_desc 切出 kf desc），应触发 LLM regen 而非代码层硬填
- **设计原则**：**「时序矛盾」（v6.31）和「时序塌缩」（v6.33）是两类缺陷**——前者单 frame 内部矛盾，后者整段缺失；必须分别有检查，不能合并
- **位置**：`step01_script_gen/validation.py` → `_check_kf_time_window_alignment()` → 由 `_validate_script` 注册到 `results["kf_time_window_alignment"]`

### 章节提取调试
```bash
python -m shared.chapter_reader 锦衣夜行_77119小说.txt
```
- 实现在 `shared/chapter_reader.py`（顶层无兼容层，2026-09-06 已删除历史 stub）
- `PROMPT_MAX_CHARS` 控制传递给 LLM 的原文上限（默认 50000，改 .env.content 生效）

### 环境变量配置（.env.content / .env.keys）
- `NOVEL_CHAPTER`: 章节序号（默认1）
- `NOVEL_NAME`: 小说名（小说级目录，全书共享库所在层）
- `BOOK_INDEX`: 部序号（小说内第几部，默认1）
- `BOOK_NAME`: 部名（拼入目录名，如 part_01_杀青州）
- `PROMPT_MAX_CHARS`: 传给 LLM 的原文上限（默认50000，传递完整章节）
- `SHOT_COUNT_MIN/MAX`: 分镜数范围（默认8-20，v4.1 起仅作 validation 软护栏，不进入 prompt）
- `VIDEO_PROVIDER`: 视频生成供应商（默认bailian）
- `IMAGE_PROVIDER`: 图片生成供应商（默认agnes）
- `TEXT_PROVIDER`: 文本生成供应商（默认deepseek）
- `TTS_PROVIDER`: TTS供应商（默认dashscope_sdk；step02 已不调用 TTS，对话由视频模型生成）
- `VIDEO_MODEL_VOICE`: 视频模型自带语音（默认1，对话/背景音交给视频模型；0=旧行为由 step03 TTS 提供）
- `HOOK_ENABLED`: 是否生成钩子分镜（默认1，开启）
- `COMPLIANCE_AUTO_FIX`: 是否自动修复敏感词（默认1，开启）
- `VIDEO_REFERENCE_MODE`: 视频参考图模式（默认text，可选image/r2v）— text=文生视频（跳过帧图片），image=图生视频（需先运行step02a生成帧图片），r2v=参考图生视频（最多5个参考图，角色肖像+场景/道具资产图）
  - ⚠️ **恢复提醒（2026-08-16）**：当前为 `image`（适配火山免费 Seedance 1.0——无 `reference_image` 能力，image 模式只发首尾帧）。**待火山免费 Seedance 1.0 额度消耗完后，必须恢复 `r2v`**（古风权谋角色一致性最关键）；切回 bailian/minimax 生产时同样改回 r2v，否则角色/道具一致性会弱化。
- `SCENE_ASSET_EXTRACTION_ENABLED`: 是否提取场景资产（大场景/近景描述，默认1）
- `PROP_ASSET_EXTRACTION_ENABLED`: 是否提取道具资产（核心道具+外观描述，默认1）
- `ASSET_GEN_ENABLED`: 是否生成场景/道具资产图（step02a 帧生成前，默认1）
- `SCENE_NEAR_SHOT_ENABLED`: 是否生成近景功能场景图（从大场景派生，默认1）

### 参考图上限（随模型走，不写死）
参考图数量上限**按当前供应商主模型从 `shared/ref_compose.py` 注册表解析**（子串匹配），
不硬编码在配置文件：
- `IMAGE_MULTI_REF`：用户偏好（省钱软上限），实际硬上限 = `min(IMAGE_MULTI_REF, 模型能力)`
- `IMAGE_MAX_REFS`：仅作「未注册模型」的回退值（默认 3）
- 注册表示例：qwen-image=3｜wan2.7-image=9｜Seedream 5.0 pro=10 / 4.5/4.0=14｜MiniMax image-01=1｜MiniMax-H3=9｜Seedance 2.5=30 / 2.0=12｜wan2.7-r2v=5
- 视频 r2v 参考图上限在 `step02_video_gen/prompt_builder.py` 按 `VIDEO_PROVIDER` 选实际提交 r2v 的模型解析（bailian→R2V 模型 / volcengine→I2V 模型 / minimax→H3）

> 新增模型遵守规则：
> - 只需在 `MODEL_REF_LIMITS` 加一行 `(模型名子串, 上限)`，子串匹配、大小写不敏感、具体在前泛化在后；模型 ID 是破折号，模式写 `seedance-2-5` 而非 `seedance-2.5`。
> - 上层（bridge 组装 / adapter 兜底 / provider 提交 / r2v prompt）都会经 `get_ref_limit(model)` / `active_image_ref_limit()` 自动跟随，配置文件无需改动。
> - 图片/视频模型的能力（Seedream：档位+像素区间+参考图数；Seedance：时长+分辨率+reference_image+有声）在 `shared/model_caps.py`，与 `MODEL_REF_LIMITS` 保持一致。

### 模型限制约定（新增模型必读）
**模型参数限制属于模型特点，必须按官方文档进注册表跟着模型走，禁止写死到配置文件**
（`.env.content` / `shared/config.py` / provider）：
- **时长上限** → `shared/model_caps.py`：Seedance 用 `SEEDANCE_CAPS`（能力表）；wan2.7/happyhorse=15s、agnes=18s 用 `_VIDEO_DURATION_CAPS`；step01 经 `active_video_duration_cap()` 自动夹取，provider 兜底
- **参考图上限** → `shared/ref_compose.py` `MODEL_REF_LIMITS`
- **prompt 长度上限** → `shared/model_caps.py` `_PROMPT_LENGTH_CAPS`：wan2.7/happyhorse=5000、MiniMax H3=7000、Hailuo=2000；provider 经 `_model_prompt_cap()` 截断（bailian/minimax 已接）
- **运镜格式**（如 Hailuo 方括号指令）→ provider 内映射
- **负面词/seed 支持、有声(generate_audio)、参考图(reference_image) 能力** → `shared/model_caps.py` 能力表
- 配置文件只放：**模型 ID、用户偏好**（省钱/目标分辨率/并发）、**操作参数**（轮询间隔/重试）
- 新增模型时若发现"限制写死在配置里"，应迁入注册表而非继续堆配置

### 火山引擎方舟（Volcengine Ark）配置
- `VOLCENGINE_API_KEY`: 火山方舟 API Key（.env.keys；兼容 `ARK_API_KEY` 写法）
- `VOLCENGINE_BASE_URL`: 方舟端点（默认 `https://ark.cn-beijing.volces.com/api/v3`）
- `VOLCENGINE_T2I_MODEL`: Seedream 文生图模型（默认 `doubao-seedream-4-5-251128`）
- `VOLCENGINE_T2I_FALLBACK_MODELS`: 图片 403 回退列表（默认 `doubao-seedream-4-0-250828`）
- `VOLCENGINE_T2V_MODEL` / `VOLCENGINE_I2V_MODEL`: Seedance 文生/图生视频模型（默认均 `doubao-seedance-1-5-pro-251215`）+ 各自 `_FALLBACK_MODELS` 回退列表
- `VOLCENGINE_IMAGE_SIZE`: 图片 size 强制 override（档位 1K/2K/4K 或像素 WxH，默认空=按分镜推导）
- `VOLCENGINE_VIDEO_RESOLUTION`: 视频分辨率 override（480p/720p/1080p/4k，默认空=按 req.size 推导）
- `VOLCENGINE_VIDEO_RATIO`: 视频比例 override（16:9/9:16/1:1，默认空=按 req.size 推导；2.5 首尾帧强制 adaptive 不可覆盖）
- `VOLCENGINE_VIDEO_DURATION`: 视频时长 override（秒，默认 0=按分镜 duration_sec）
- `VOLCENGINE_VIDEO_POLL_INTERVAL` / `VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS`: 视频任务轮询参数

### 火山方舟 Agent Plan 文本通道（2026-09-03 新增，doubao-seed-2-1-turbo 分解模型）

`TEXT_PROVIDER=agent_plan` 走 `/api/plan/v3/chat/completions`（OpenAI 兼容），作为 step01 分镜分解的备选/对照模型（默认仍 `deepseek`，不切默认）。

- 鉴权复用 `AGENT_PLAN_API_KEY`（与图片/视频同专属 key，**与 `VOLCENGINE_API_KEY` 不通用**——普通 ARK key 报 401）
- 模型默认 `doubao-seed-2-1-turbo`（按 `AGENT_PLAN_TEXT_MODEL` 覆盖）
- 支持 `response_format json_schema`（与 deepseek 路径同款 strict 结构化输出），用于 step01 分镜 JSON 强约束
- 并发限速走 `AGENT_PLAN_TEXT_RPM`（默认 10 RPM，cooldown=6s）
- 思考模式 `AGENT_PLAN_TEXT_REASONING_EFFORT`（none/minimal/low/medium/high/xhigh/max，默认 `none` 关闭）
- 单次输出 token 上限 `AGENT_PLAN_TEXT_MAX_TOKENS`（0/空=不传 → provider fallback 32768）
  - **v6.62.5（09-08）**：16384 太低——全脚本 JSON 实测 ≈66k 字符 ≈**26k tokens**，4 个输出全脚本的站点（初始分镜/GAP 重生/定向修复/超限收敛）必然 `finish_reason=length`。已抬到 **32768**（26k + 25% 余量）
  - 截断行为：provider 判 `success=False` 并整份丢弃（**不做**自动升档重试）；旧实现把截断当 `success=True` 静默下发，是「多次 JSON 解析失败」的根因
  - 长尾若复现（v6.59 的 3-5 分钟问题），降级手段是**给小提取调用显式传小 `max_tokens`**，不要压低这个全局值（会重现截断）
- 错误码映射：401→鉴权失败（提示申请专属 key）、403→套餐/燃料耗尽、5xx→4 次指数退避、429→按 `retry-after` 等待
- Key 缺失时构造直接 `ValueError`（含「AGENT_PLAN_API_KEY」+「专属 key」文案，与 image/video provider 对齐）

**Seedream/Seedance 模型能力矩阵**（值来自官方文档 2026-08 核对，实现见 `shared/model_caps.py`，provider 按模型子串查表收敛参数）：

| 模型 | 时长(秒) | 分辨率 | 参考图(reference_image) | 有声(generate_audio) | 备注 |
|---|---|---|---|---|---|
| Seedream 5.0 pro | — | 档位 1K/1.5K/2K；像素 [921600, 4624220] | ≤10 | — | 图片不支持 negative_prompt/seed |
| Seedream 5.0 lite | — | 档位 2K/3K/4K；像素 [3686400, 16777216] | ≤14 | — | 同上 |
| Seedream 4.5 | — | 档位 2K/4K；像素 [3686400, 16777216] | ≤14 | — | 同上 |
| Seedream 4.0 | — | 档位 1K/2K/4K；像素 [921600, 16777216] | ≤14 | — | 同上 |
| Seedance 2.5 | 4–30 | 480p/720p | ✓ | ✓ | 首帧/首尾帧 ratio 仅 adaptive |
| Seedance 2.0（含 fast/mini） | 4–15 | 480p–4k（fast/mini 仅 480p/720p） | ✓ | ✓ | |
| Seedance 1.5 pro | 4–12 | 480p/720p/1080p | ✗ | ✓ | |
| Seedance 1.0 系列 | 2–12 | 480p/720p/1080p | ✗ | ✗ | |

> 关键约束：`reference_image`（参考生视频/r2v）仅 2.5/2.0 支持；`generate_audio` 仅 2.5/2.0/1.5 pro。首帧/首尾帧、参考生视频、参考视频/音频三种场景互斥，provider 已按模型能力分配 role 并 fail-fast。
> **调试省钱配置**：`VOLCENGINE_T2I_MODEL=doubao-seedream-4-0-250828`（4.0 像素下限低，1024x1792 直接透传）+ `VOLCENGINE_I2V_MODEL=doubao-seedance-2-0-260128`（r2v 且便宜）；纯文生视频省钱可 `doubao-seedance-1-0-lite-t2v-250408`（无声、无参考图）。生产再切回 4.5 / 1.5 pro。

### MiniMax 配置（国际站 api.minimax.io）
- `MINIMAX_API_KEY`: MiniMax API Key（.env.keys）
- `MINIMAX_BASE_URL`: 默认 `https://api.minimax.io`
- 图片：`MINIMAX_T2I_MODEL=image-01`（subject_reference 单张）+ `MINIMAX_T2I_FALLBACK_MODELS=image-01-live`
- 视频 v1（Hailuo 旧模型，省钱）：`MINIMAX_T2V_MODEL=MiniMax-Hailuo-02` / `MINIMAX_I2V_MODEL=MiniMax-Hailuo-2.3-Fast` + 各自回退队列（仅 MiniMax 系列）
- 视频 v2（H3 新模型，贵）：`MINIMAX_H3_MODEL=MiniMax-H3`（尾帧/多关键帧/参考图/参考音频用）
- ⚠️ **首尾帧模式 + MiniMax = 自动升 H3（贵，2026-08-29 确认）**：`VIDEO_REFERENCE_MODE=image` 的请求带尾帧，而 v1 Hailuo I2V 只支持首帧——provider 检测到尾帧/参考图即自动路由到 H3，**不会回落便宜档**。供应商选型约定（2026-08-29 用户决策：只用火山 + MiniMax，不做 A/B）：
  - **日常跑量**：留火山免费 Seedance 1.0 pro（首尾帧模式，无参考图/无声）
  - **关键镜头/验收**：切 MiniMax H3 或火山付费档（Seedance 2.0 起）
  - **恢复 r2v 角色一致性**：火山 Seedance 2.0+ / MiniMax H3 都支持，管线已适配（`_collect_r2v_references` 按 provider 自动收参考图）
  - 以上全部是 `.env` 配置切换，零代码改动——模型能力差异集中在 `shared/model_caps.py`（时长/分辨率/参考图/有声/prompt 上限）+ `shared/ref_compose.py`（参考图数）+ provider 内格式映射，新增/切换模型不改编码
- `MINIMAX_VIDEO_RESOLUTION`（默认 768P）/ 轮询参数（`MINIMAX_VIDEO_DURATION` 死配置已删——时长规则见 shared/model_caps.MINIMAX_V1_*）
- **prompt 适配（2026-08，按官方指南）**：
  - H3（r2v 路径）：provider 自动把 step02 扁平 prompt 重组为官方**时间线三段结构**（`integrated_multimodal_description: [Shot 1] ...` + `overall_soundscape` + `non_diegetic_music`）；「（背景音效：X）」→ 音景；「（用中文普通话说，音色）"台词"」→ `(S1) says: <d>[Chinese] 台词</d>`；有首帧图时前置官方 I2V 对齐指令。见 `_video_gen/minimax_provider.py _h3_render`
  - Hailuo v1（text/image 路径）：中文运镜词自动映射为官方**方括号指令**前缀（`[Push in]`/`[Tracking shot]`/`[Static shot]` 等 15 种，按出现顺序 ≤3 组合）。见 `_hailuo_camera_prefix`
  - 两处均为 provider 内自动转换，其他供应商（bailian/agnes/volcengine）不受影响

## 架构设计

### 四阶段流水线
```
小说文本 (.txt)
    → step01_script_gen/main.py → script_result.json（分镜 + 旁白 + 角色 + 摄像机分组）
    → step02a_frame_gen/main.py  → 首帧/尾帧/关键帧图片（图生视频前导）
    → step02b_video_gen/main.py  → vimax_working/shots/<idx>/video.mp4
    → step03_tts_audio/main.py  → audio/narration.mp3
    → step04_merge/main.py      → final.mp4
```

- 阶段2拆分为2a（帧图片）和2b（视频生成），方便调试：可以先运行2a检查图片质量，确认OK后再运行2b
- 每个阶段脚本都有交互式确认提示
- `VIDEO_REFERENCE_MODE=text` 时跳过分帧环节，直接文生视频；r2v 模式使用参考图（最多5个）保持角色/道具一致性

### 输出目录结构（统一由 shared/paths.py 计算，禁止自行拼接）
层级对应小说自身结构「小说 → 部 → 章」：
```
{OUTPUT_BASE}/                              # 默认 ./output/video
└── 锦衣夜行/                                # {NOVEL_NAME} 小说级
    ├── global/                             # 全书共享（角色形象/风格跨部统一）
    │   ├── characters_bank/                # 角色注册表 + 肖像
    │   ├── style_bank/                     # 风格锚点（Ch1 锁定）
    │   ├── scenes_bank/                    # 场景资产（大场景图 + 近景功能场景图）
    │   ├── props_bank/                     # 道具资产（白底道具资产图）
    │   └── chapter_cache/                  # 章节文本解析缓存
    └── part_01_杀青州/                      # part_{BOOK_INDEX:02d}_{BOOK_NAME} 部级
        └── chapter_002/                    # chapter_{NOVEL_CHAPTER:03d} 章级
            ├── script_result.json / final.mp4 / subtitles.srt
            └── audio/ clips/ images/ vimax_working/ stretched/
```
- 2026-08 架构变更：合并改为唯一 native 模式（直接拼接保留视频原声），只输出 `final.mp4`，删除 final-A/final-B 双版本；对话/背景音由视频模型生成（wan2.7 系列有声视频），视频时长由分镜 `duration_sec` 字段驱动（step01 timecode 权威计算），step02 不再调用 TTS。
- 2026-08 v6.24：分镜生成规则新增 35-40（物体可分性/同场景合并/词序量化/手部锁定/远景省略/画面无字幕），见 `.aiVideo/skills/absorption-log/SKILL.md` §1 源→落地映射；`VIDEO_REFERENCE_MODE=r2v` 为默认（spark-video 70/25/5 逐镜混合未采纳，全局单模）。

### 场景/道具资产（v7 新增）
四步资产生成方法论（`references/ai-scene-prop-asset-guide.md`）：
1. **大场景**：step01 提取场景的 `wide_desc`（大场景建立镜头描述）+ 地点/时间段/天气/色调
2. **近景功能场景**：同一场景的 `near_desc`（从大场景派生，保持季节/光线/年代一致）
3. **道具提取**：step01 提取核心道具的 `appearance`（材质/颜色/结构/尺寸/新旧痕迹）
4. **道具资产图**：step02a 生成白底道具资产图

数据流：
```
step01 asset_extractor.py（LLM 提取，schema: SCENE_ASSETS_SCHEMA / PROPS_SCHEMA）
    → shared/assets_bank.py（SceneAssetsBank / PropAssetsBank，global/scenes_bank/ props_bank/）
    → step02a asset_gen.py（横屏 1600x900 适配器，生成 wide.png/near.png/prop.png）
    → step02b prompt_builder._collect_r2v_references（场景资产图+角色肖像+道具资产图，≤5张）
```
- 资产图是**横屏 16:9 参考图**（不做竖屏约束），专用适配器 `size="1600x900"` + `skip_prefix` + `skip_sanitize`（与角色肖像一致）
- 大场景/近景图**禁止人物**（prompt 硬约束）；资产图跨章复用，bank 中已有图直接跳过
- r2v 参考图收集顺序：场景资产图（环境锚定）→ 出镜角色肖像 → 出镜道具资产图；空镜只收场景资产图

### Provider 抽象模式
`_video_gen/` 和 `tts/` 两个包采用相同架构（`_image_gen/` 同构）：
- **ABC 接口 + 工厂注册表**：通过 `.env.content` 中的 PROVIDER 切换
- **Config 单例**：各包的 `config.py` 从 `.env.content`（内容参数）+ `.env.keys`（API Key）加载
- **Dataclass 模型**：请求和结果用 dataclass 定义

**Provider 清单**（`_auto_register()` 注册）：
- `_image_gen/`：`agnes`（OpenAI 兼容）、`bailian`（百炼 qwen-image）、`volcengine`（Seedream）、`minimax`（image-01）、`agent_plan`（火山方舟 Plan 通道 Seedream）
- `_video_gen/`：`dashscope`（通义万相）、`dashscope_http`（KF2V 首尾帧）、`agnes`、`bailian`（wan2.7/happyhorse）、`volcengine`（Seedance）、`minimax`（Hailuo v1 + H3 v2）、`agent_plan`（火山方舟 Plan 通道 Seedance）
- `_text_gen/`：`deepseek`（默认，OpenAI 兼容 Responses API）、`dashscope`（百炼 Qwen）、`agnes`（OpenAI 兼容）、`agent_plan`（火山方舟 Plan 通道 doubao-seed-2-1-turbo，2026-09-03 接入）
- `_text_gen/`：DashScope Qwen、DeepSeek、Agnes
- `tts/`：DashScope SDK、DashScope HTTP、OpenAI

**Key Conventions**：
1. 视频模式 = 异步轮询：Submit task → poll task_id → download video_url
2. 图片 = 视频首帧：vimax_bridge 生成 first_frame.png per shot
3. 403 回退：provider 用 `_model_queue` 自动切下一个模型
4. 限速：VIDEO_MAX_REQUESTS_PER_MINUTE、IMAGE_GEN_INTERVAL、KeyScheduler
5. 视频生成 provider 统一存 `vimax_working/shots/<idx>/video.mp4`

### DeepSeek 文本生成（`_text_gen/`）
- **Responses API**（默认 `deepseek-flash`）：`POST /responses`，`reasoning.effort=none` 关闭思考 + strict `json_schema` 结构化输出
- **JSON Schema** 定义在 `_text_gen/deepseek_schema.py`（SCRIPT/HOOK/CHARACTERS/SCENE_ANALYSIS 四个）
- 请求结构：`text.format = {type: "json_schema", name, schema}`（漏 `type` 报 400）
- 正文从响应 `output[]` 的 `message.output_text` 提取；`status=incomplete` → `incomplete_details.reason` 诊断截断
- 非 v4-flash 模型回退 `chat.completions`

### 章节提取
- 小说文件：`锦衣夜行_77119小说.txt`（UTF-8编码；`.bak` 为 GBK 原件）
- 标题行格式：`第一部 杀青州 第001章 章节标题`（部名/章名由 `get_chapter_meta()` 解析并写入 script_result.json）
- 使用 `shared.chapter_reader` 提取完整章节

## 关键参考文件
- `skill_spy.txt` — Prompt 工程参考
- `shared/style_prefix.py` — 风格前缀/改写函数（v6.60.1 自根目录迁入；根目录留 deprecated shim）
- `step01_script_gen/prompts.py` — 包含详细的分镜生成 prompt
