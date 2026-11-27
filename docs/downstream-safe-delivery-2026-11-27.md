# aiVideo 下游链路安全出片基础包（S01–S07）交付与基线账本

- **分支**：`codex/downstream-safe-delivery`
- **隔离 worktree**：`C:/myFloders/git/aiVideo/.tmp/worktrees/downstream-safe-delivery`
- **冻结基线 SHA**：`3aa86304525903c3a8bb3a1d52cdbb93c2864431`
- **总体目标（零模型调用）**：在隔离环境中，通过真实 `Step03 → Step02b → Step04` 入口，证明声音完整、请求身份一致、恢复不重复提交、失败不覆盖基线，并产出可播放的合成测试片与完整报告。

## 一、S01 基线与生效配置快照（非敏感）

- **代码基线**：`git rev-parse HEAD = 3aa86304525903c3a8bb3a1d52cdbb93c2864431`（未改动主工作区、未复写或清理既有 `.tmp/s14a` 与 `aiVideo-s14base`）。
- **有效配置来源**（由 `tools/print_effective_config.py --json` 于隔离 worktree 实测，无 `.env.keys`）：
  - `TEXT_PROVIDER=agent_plan`（`.env.content`）、`AGENT_PLAN_TEXT_MODEL=deepseek-v4.1-flash`（`.env.content`）
  - `VIDEO_PROVIDER=volcengine`（`.env.content`）、`VOLCENGINE_T2V_MODEL=doubao-seedance-1-0-pro-250528`（`.env.content`）、`VOLCENGINE_I2V_MODEL=doubao-seedance-1-0-pro-250528`（`.env.content`）
  - `VIDEO_REFERENCE_MODE=image`（`.env.content`）
  - `TTS_PROVIDER=dashscope_sdk`（`.env.content`）、`DASHSCOPE_TTS_FORMAT=mp3`（code default）、`VOICE_STRICT_UNIQUE=1`（code default）
  - `MERGE_AUDIO_MODE=native`（`.env.content`；经 `shared.audio_policy.resolve_audio_policy()` 与 `doubao-seedance-1-0-pro-250528` 能力联合解析后，实际生效 `effective_merge_mode=tts_mix`）
  - `MERGE_AUDIO_LOUDNORM=1`（`.env.content`）
- **基线门禁对照记录**：
  - `python tools/ruff_gate.py`：基线 `3aa8630` 上存在 1 条既有未定义错误 `_text_gen/service.py` `F821 _cid x1`（非本包改动范围）；本包所有新增/修改文件保持 `F821/F401/F811` 为 `0`。
  - 干净检出（无 `.env.keys`、无 `output/`）下既有 `tests/step02_video_gen`、`tests/step02b_video_gen`、`tests/step03_tts_audio`、`tests/step04_merge`、`tests/tts` 基线跑测：`206 passed / 6 skipped / 15 failed / 7 errors`（失败均来自未设测试虚拟 key 时直接实例化 `VolcengineVideoService()` / `AgentPlanVideoService()` 触发 `config.validate()`；skipped 来自缺少正式 `output/` 产物）。本包新增测试全部自足、不依赖 `.env.keys` 或现场 `output/`。

## 二、S01–S07 施工单追踪

| 模块 | 责任文件（规划/实际） | 状态 | 验证证据 |
|---|---|---|---|
| S01 基线与隔离 | `docs/downstream-safe-delivery-2026-11-27.md`、`tests/integration/downstream_fixtures.py`、`tests/integration/test_s01_isolation_baseline.py` | 已完成 | `pytest tests/integration/test_s01_isolation_baseline.py tests/test_py311_source_grammar.py` `15 passed`；`ruff check` 0 错误 |
| S02 任务恢复与计量 | `shared/request_events.py`、`step02_video_gen/submit_poll.py`、`step02b_video_gen/main.py`、`_video_gen/*provider.py`、`tools/step02b_metrics_report.py`、`tests/step02b_video_gen/test_s02_recovery_and_metrics.py` | 已完成 | `pytest test_s02_recovery_and_metrics.py` `6 passed`、`test_production_trace.py` 全过、`ruff check` 0 错误 |
| S03 逐镜策略贯通 | `shared/video_media_plan.py`（新增 `VIDEO_MEDIA_POLICY_SOURCE` / `resolve_shot_media_policy`）、`step02b_video_gen/cli.py`（`--media-policy-source`）、`step02b_video_gen/main.py`、`step02_video_gen/execution_requirements.py`、`step02_video_gen/prompt_builder.py`、`step02_video_gen/submit_poll.py`、`_video_gen/models.py`、`_video_gen/*provider.py`、`tests/step02b_video_gen/test_s03_mixed_shot_policies.py` | 已完成 | `pytest tests/step02b_video_gen/test_s03_mixed_shot_policies.py` `6 passed`；`tests/step02b_video_gen tests/step02_video_gen tests/step03_tts_audio tests/step04_merge tests/tts tests/integration` `258 passed / 14 skipped / 3 failed`（3 条均为既有 `tests.enhancements.test_transaction_cli` 缺失导入，与本包无关）；`ruff check` 无新增（既有 11 条 F401/F811 保持原样） |
| S04 音色容量与身份 | `tts/voice_registry.py`（分级/共享桶/扩容通道）、`tts/models.py`（`SpeechUnit.voice_tier`）、`step03_tts_audio/shot_tts.py`（`derive_voice_tiers` / `plan_role_voices` / 分级版 `check_voice_uniqueness`）、`tts/synthesis_job.py`（speaker 校验与分配同源）、`shared/voice_fit.py`（共享等级不误报复用冲突）、`tools/voice_unit_report.py`、`tools/voice_tier_audit.py`（新增，多章审计）、`tests/tts/test_s04_voice_tiers.py` | 已完成 | `pytest tests/tts/test_s04_voice_tiers.py` `9 passed`；下游回归 `254 passed / 6 skipped`；`ruff check` 0 新增（`tts/synthesis_job.py` 的 `KIND_DIALOGUE` F401 为基线既有）；真实第 1+2 章只读副本离线核对：两章 `voice_uniqueness.ok=true`、跨章合并 `ok=true`、`provider_calls=0`，见「S04 证据」 |
| S05 实测时长与防截音 | `shared/voice_preparation.py`（`build_duration_audit` 投影）、`step03_tts_audio/main.py`（`_write_duration_audit`：正式 + 候选都落 `step03_duration_audit.json`）、`step02b_video_gen/main.py`（`build_plan` 消费审计、`_assert_audio_fit` 生视频前拒收）、`step04_merge/audio_fit.py`（新增：策略纯算术）、`step04_merge/timeline.py`（`ClipEntry.pad_sec` / `TimelineAudioTrack.atempo`）、`step04_merge/backends/ffmpeg.py`（`tpad` / `atempo`）、`step04_merge/main.py`（策略接线 + `audio_fit` 入质量报告）、`tests/step04_merge/test_s05_audio_fit.py`、`tests/integration/test_s05_duration_audit.py` | 已完成 | `pytest tests/step04_merge/test_s05_audio_fit.py` `11 passed`（含真实 FFmpeg 渲染：pad 后音频尾音保留、音画等长）；`pytest tests/integration/test_s05_duration_audit.py` `6 passed`；下游回归 `270 passed / 6 skipped`（1 条 `step03.log` 断言失败来自 `.pytest_tmp` 上次跑测遗留的现场目录，清掉后该套件 `57 passed`）；`ruff check` 0 新增 |
| S06 媒体检查与混音 | `step02_video_gen/video_qc.py`、`step02_video_gen/submit_poll.py`、`step04_merge/backends/ffmpeg.py`、`step04_merge/quality_gate.py`、`step04_merge/main.py` | 待开工 | — |
| S07 完整离线交付 | `tests/integration/test_downstream_safe_delivery_s07.py`、干净副本复跑 | 待开工 | — |

## 三、S04 证据：角色分级、龙套共享池与音色容量

**分级来源（确定性，代码不写死任何作品角色）**：输入声明
（`characters[].voice_tier|voice_pool|role_tier`，值 `primary/recurring/minor/extra` 及常见别名）
> 注册表历史记录（跨章稳定）> 本次结构派生（发言单元数 ≥ `VOICE_PRIMARY_MIN_UNITS`，默认 2，
或出镜 ≥ 2 镜 ⇒ primary；只有**输入带帧布局**且仅发言 1 单元、仅出镜 ≤ 1 镜才判 extra——
无帧布局不猜龙套）。旁白恒为 primary 且独占旁白音色。

**共享与容量**：
- `minor/extra` 走龙套桶：同一 `(gender, age_range)` 桶可共用一条声线，但**不撞**独占角色与旁白；
- 同镜/同场（按帧布局与发言镜并集算）会被同时听见的龙套，分配时互相 `avoid`，池够时给不同声线，
  池不够则 `voice_pool_exhausted` 显式失败（不静默撞音）；
- 独占角色满池仍**显式失败**（`voice_pool_exhausted`），符合「主配角满池抛错」验收；
- 扩容通道（零调用、纯配置）：`TTS_EXTRA_VOICES=音色id|male|young,...` 扩充旧池，
  `TTS_EXTRA_SPEAKERS=a,b` 扩充有独立 speaker 的 provider 白名单（与 `voice_design` 产出的
  自定义 voice id 同口径）；非法写法显式报错。分配侧与合成侧共用 `legal_speakers_for()`，
  `tts/synthesis_job._check_speaker` 不会把扩容 speaker 判成非法。

**证据（2026-11-27，本 worktree）**：
- 匿名单测：`pytest tests/tts/test_s04_voice_tiers.py -q` → `9 passed`
  （分级派生/声明覆盖历史/满池抛错/龙套共享不撞独占与旁白/同场 avoid/跨章稳定/7+ 角色报告 ok/扩容通道）。
- 下游回归：`pytest tests/tts tests/step03_tts_audio tests/step02_video_gen tests/step02b_video_gen tests/step04_merge` → `254 passed / 6 skipped`（skip 均为缺正式 `output/` 产物的既有用例）。
- **真实章节只读副本核对**（外部数据集，非基础测试）：
  - 数据：生产 `output/video/锦衣夜行/part_01_杀青州/chapter_001|002` 的 `script_result.json`
    + `chapter_outline.cache.json`，以及 `global/voice_registry.json` **复制件**；副本落在
    `.tmp/s04-real/`，生产目录只读未改。
  - 命令：`python tools/voice_tier_audit.py --chapters 1,2 --out-json .tmp/s04-real/voice_tier_audit.json`
    与 `python tools/voice_unit_report.py --out-json ...`（每章各一次）。
  - 读数：两章 `voice_uniqueness.ok=true`、跨章合并 `ok=true`、`provider_calls=0`；两章剧本合计
    **9 个角色**（第 1 章 6、第 2 章 5，跨章 2 个重合），报告零报错。
  - 限制：这两章的旧格式计划缓存未带身份绑定，工具已显式告警「按旧格式读取、身份未核」；
    两章实际发声角色 4 个（其余角色只出镜不发声），因此**共享池路径由匿名单测覆盖**，不是靠这两章。

## 四、S05 证据：实测时长审计与"不截字"策略

**A2 实测时长对账（Step03 → Step02b）**
- Step03 收尾（正式与候选两种落点都写）产出 `step03_duration_audit.json`：
  `schema=step03_duration_audit.v1`，逐单元 `planned_est_sec`（文本估时）/
  `actual_wav_sec`（ffprobe 实测）/`window_sec`/`overflow_sec`/`recommended_shot_dur`，
  逐镜 `audio_need_sec`（只取 confirmed 实测）与 `overflow_sec`，summary 给
  `measured_units / overflow_shot_count / max_overflow_sec`。**未测到音频不冒充超窗证据**
  （`state=prepared`、`audio_need_sec=null`）。
- Step02b `build_plan()` 读取该审计（显式参数 > 章目录 > `shots/` 父目录，绝不按
  mtime/"最新目录"找）：实测 `audio_need_sec > 镜头时长 + 容差（默认 0.15s）` ⇒
  `verdict=blocked, code=audio_overflow`，并给 `recommended_shot_dur`；生成入口在
  任何提交前 `_assert_audio_fit()` 直接退出码 2，未找到审计则显式告警"本次无实测下限"。

**M1 逐镜音轨超窗策略（Step04）**——`MERGE_AUDIO_OVERFLOW_POLICY`：
| 值 | 行为 | 上限/记录 |
|---|---|---|
| `fail`（默认） | 超窗 > 容差即拒收 `audio_overflow_truncated` | 不改时长、不截音 |
| `pad_video_tail` | 末帧克隆补齐（ffmpeg `tpad=stop_mode=clone`） | ≤ 1.0s，超限拒收 `audio_overflow_exceeds_pad` |
| `gentle_atempo` | 轻度加速（ffmpeg `atempo`） | ≤ 1.12x，超限拒收 `audio_overflow_exceeds_atempo` |
| `trim` | 旧行为，仅 `TTS_MIX_ALLOW_TRIM=1` 显式开启 | 被截时长如实写入报告（`audio_overflow_trimmed`） |

策略判定在 `step04_merge/audio_fit.plan_audio_fit()`（纯算术，可单测）；补齐/加速在
时间线层落实（`ClipEntry.pad_sec` / `TimelineAudioTrack.atempo`，`validate_timeline`
再核 atempo 上限），后端按同一字段下发 `tpad` / `atempo`；逐条调整进
`step04_quality.json.audio_fit` 与日志，**不静默**。

**证据（2026-11-27，本 worktree）**
- `pytest tests/step04_merge/test_s05_audio_fit.py -q` → `11 passed`：策略解析/容差/
  满限拒收/`trim` 留痕/无对白轨不受影响；`compile_graph` 出现 `tpad=stop_mode=clone:stop_duration=0.8`
  与 `atempo=1.12`；**真实渲染** 2.0s 视频 + 2.8s 人声按 `pad` 补齐后
  输出 ≈2.8s，音轨 ≥2.7s 且音画差 ≤0.15s（人声尾音未被 `atrim` 截掉）。
- `pytest tests/integration/test_s05_duration_audit.py -q` → `6 passed`：投影超窗读数
  6.2−5.0=**+1.2s**（`overflow_sec=1.2`、`recommended_shot_dur=6.2`）；未实测不算超窗；
  真实 `_write_duration_audit` 用 6.2s WAV 实测写入；`build_plan` 阻断 `audio_overflow`；
  容差内放行；**真实入口** `main()` 在候选生视频前退出码 2 且无任何 mp4 产物。
- Step03 不依赖 Step02b 产物：现有 `test_candidate_run_writes_only_under_the_candidate_root`
  在同一次 `57 passed` 内以桩 provider 完成真实合成（无任何 clip 输入）。
- 限制：以上全部为离线/桩+本地合成媒体，**未运行真实 TTS/视频模型**；`atempo` 的听感
  与 `pad` 末帧观感需真实成片复核。

## 五、结论边界声明

本工作包全链路验收均为 **0 次外部模型调用**，本地音频/视频素材由 FFmpeg 合成，provider 网络交互由可控传输桩替代：
- **源码完成 / 离线通过**：仅证明下游协议、隔离事务、状态机恢复、逐镜策略透传、音色容量预检、实测超窗拒收与 FFmpeg 混音/媒体检测工程通路成立；
- **真实模型未运行**：不代表真实语音音质、人物唇形/句内同步或视频动作生成质量已验证；
- **正式未采纳**：未修改主仓库或正式 `output/` 下的任何章节产物。
