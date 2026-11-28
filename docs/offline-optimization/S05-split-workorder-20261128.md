# S05 四文件拆分施工单（2026-11-28）

> 对应计划 S05：在搬代码前把四个目标文件的边界、依赖、兼容符号与验收次序写全。
> 本文件只做静态分析（AST + 依赖/打桩扫描），零模型调用、零 `output/` 写入。
> 基线提交 `66a2feb`（S04）。

## 1. 共同原则（S06–S09 逐文件执行）

1. **不迁 `parts/state.py`。** 父模块的配置、日志、运行状态初始化留在原位；只搬职责明确的函数。
2. **子模块导入期不得回导父模块。** 需要父模块当前值时，在**调用点**用访问器读取；
   可替换的 callable 与可变布尔/容器状态分别处理：
   - 值（int/str/bool/list）→ `_pv(name)`：`getattr(importlib.import_module(_PARENT), name)`；
   - 可替换函数 → `_pc(name, *a, **k)`：取父模块属性后调用（打桩立即生效）；
   - 可变运行状态（如 `_failed_videos`）→ 只经 `_pv` 取同一个对象，原地更新，不复制。
3. **原签名/默认值/异常/日志关键字/下划线名字不变。** 父模块用
   `from .parts.x import (...)` 或 `from <pkg>.parts.x import (...)` 全量 re-export；
   文件没有 `__all__`，不新增导出面。
4. **插入点用 AST 定位**：父模块最后一个模块级赋值之后、第一个使用迁移符号的函数之前；
   禁止字符串查找。保留 docstring、`__future__` 与作用域初始化次序。
5. **每个文件独立提交、独立验收。** 失败只回退该文件的 `parts/` 目录与父模块改动。
6. **净行数目标 ≥30%**，按父模块最终行数计（含 re-export 导入块），不得删行为或复制实现。
7. 每个文件完成后跑：`python -c "import <parent>"`、受影响分组、`ruff_gate`、
   `RULE_SOURCE_FINGERPRINT`（S09 另记 `PROMPT_SOURCE_FINGERPRINT` 变化）。
8. 子模块导入顺序为 DAG：`state/常量`（留在父）→ `结构` → `操作` → `反馈/恢复`；
   同包内只允许导入排在**前面**的模块。

## 2. S06：`step04_merge/main.py`（1,891 行）

**保留**：`main()`、`_run_merge()`、`_encode_settings`/`_subtitle_style`（媒体编码配置）、
`_commit_candidate`/`_dump_card_manifest`/`_validate_candidate_path`（发布与恢复）、
路径/日志初始化、`_options`。

**迁出**（`step04_merge/parts/`）：

| 模块 | 函数 | 行数 | 累计占比 |
|---|---|---:|---:|
| `track_collector.py` | `_collect_shot_tts_paths`(100)、`_collect_narration_tracks`(210)、`_narration_subset_decision`(17)、`_resolve_dialogue_path`(6)、`_resolve_narration_path`(6) | 339 | 17.9% |
| `timeline.py` | `_parse_transition`(55)、`_compute_clip_windows`(18)、`_build_timeline`(55)、`_declared_black_windows`(22)、`_color_filter`(7)、`_assert_step02b_scope`(25) | 182 | 27.5% |
| `declarations.py` | `_source_declarations`(54)、`_detector_parameters`(16)、`_load_validated_timings`(16)、`_shot_audio_hashes`(10)、`_assert_voice_gate`(23) | 119 | 33.8% |

**依赖与路由**：

| 名字 | 类型 | 处理 |
|---|---|---|
| `_AUDIO_MODE`（track_collector） | 运行配置 | `_pv` 每次调用读取 |
| `_TTS_BASE_GAIN`、`_NARRATION_GAIN`（timeline） | 导入期常量 | `_pv` 读取（保持测试可改父值） |
| `_EXPLICIT_KIND_RE`、`_TRANSITION_MAP` | 正则/表 | 随 `timeline.py` 迁移，父模块 re-export |
| `_logger` | 日志对象 | 父模块导入后作为参数传入每个入口（不在子模块复制 logger） |
| `_resolve_dialogue_path`、`_resolve_narration_path`、`_narration_subset_decision` | 同组函数 | 同模块内直接调用 |

**打桩扫描**：本文件无 `setattr(step04_merge.main, ...)`；测试只赋路径常量
（`CHAPTER_DIR/CLIP_DIR/FINAL_OUT/SCRIPT_FILE/SRT_OUT/TEMP_DIR/VIMAX_WD/CHAPTER_INDEX`），
迁出函数只读 `CHAPTER_INDEX`/`CLIP_SOURCE` 的两个（`_run_identity`/`_validate_candidate_path`/
`_resolve_hook_clip`）本批不迁，因此**不需要 callable 代理层**。

**插入点**：父模块第 194 行后（`_resolve_dialogue_path` 之前）插入 re-export 块。

**验收**：`pytest tests/step04_merge tests/enhancements -k merge`；正常合并、镜头子集、
音频超窗拒收、发布故障/恢复用例；`python -c "import step04_merge.main"`；
目标净行数 ≤1,350。

## 3. S07：`step02_video_gen/submit_poll.py`（2,668 行）

**保留**：`_submit_video_candidate`（提交主流程）、`_poll_video`（轮询主循环）、
`_create_prepared_task_once`、`_process_one_shot`、`_submit_video`、`gen_shot_frames`、
轮询配置常量与 `_failed_videos`。

**迁出**（`step02_video_gen/parts/`）：

| 模块 | 函数 | 行数 | 累计占比 |
|---|---|---:|---:|
| `frozen.py` | `frozen_snapshot_path`/`read_frozen_snapshot`/`write_frozen_snapshot`(19)、`generation_requirements`(27)、`_chapter_index_safe`/`_script_revision`/`_scope_identity_safe`(19)、`build_frozen_for_shot`(45)、`_prepare_error`/`_service_supports_prepared`(19)、`build_prepared_facts`(68)、`record_submitted_duration`(30)、`cache_decision`(47)、`PreparedFacts`(13)、`_read_qc_retry_state`(14)、`_record_qc_failure`(8)、`_is_recoverable`(15)、`_get_shot_hash`(145) | 471 | 17.7% |
| `qc.py` | `_VideoProductQc`(20)、`_qc_video_product`(11)、`_qc_video_product_result`(94)、`_check_keyframe_hallucination`(28)、`_qc_coverage`(28)、`_record_candidate_qc`(84)、`accept_candidate_video`(85)、`_report_to_dict`(12) | 342 | 30.5% |
| `recovery.py` | `_ResumeContext`(19)、`find_recoverable_attempt`(119)、`resume_candidate_attempt`(141)、`_request_from_frozen`(30)、`_scope_matches`(10)、`_chapter_scope_str`(7)、`_work_id_str`(3)、`_emit_video_ledger`(49)、`attempt_task_id`(25)、`_record_content_policy_revision`(11) | 395 | 45.3% |
| `failures.py` | `_classify_poll_failure`(15)、`repair_missing_clip`(16)、`submit_failure_code`(11)、`_poll_video_production`(18) | 60 | 47.5% |

**依赖与路由**（关键：测试大量直接 patch 父模块）：

| 名字 | 类型 | 处理 |
|---|---|---|
| `INITIAL_POLL_DELAY`、`POLL_*`、`VIDEO_SUBMIT_RETRIES` | 轮询配置 | 主循环在父模块，保持原样 |
| `VIDEO_QC_ENABLED`、`VIDEO_DURATION_DEV_RATIO`、`VIDEO_DURATION_DEV_SEC` | 布尔/浮点 | `_pv` 在 `_qc_video_product_result` 入口读取（测试 patch 父值生效） |
| `_probe_video_duration` | 可替换 callable | 父模块保留定义；子模块 `_pc("_probe_video_duration", path)` |
| `_get_shot_hash` | 可替换 callable | 父模块保留定义；`cache_decision` 内 `_pc("_get_shot_hash", ...)` |
| `cache_decision` | 被 `step02b main` patch | 父模块 re-export；`main` 直接调用父名字，补丁生效 |
| `_video_service_for`、`_trace_video_event`、`_candidate_attempt` | 父模块构造/记账 | 保留父模块，子模块 `_pc` |
| `_failed_videos` | 可变容器 | `_pv("_failed_videos")` 取同一对象原地更新 |
| `_poll_video`、`_failed_videos`（`failures.py`） | 父模块轮询 | `_pc` 调用，避免复制循环 |
| `_qc_video_product_result`、`accept_candidate_video` | 迁出函数 | 同包内按 `frozen → qc → recovery → failures` 顺序导入 |

**打桩清单**（测试里已存在，必须全部保持生效）：
`_submit_timeout`、`_probe_video_duration`、`_get_shot_hash`、`cache_decision`、
`_submit_video`、轮询常量；`submit_poll` 的 `_process_one_shot` 保持父模块定义。

**插入点**：第 75 行后（`_probe_video_duration` 之前）插入 re-export；`_failed_videos`
与轮询常量必须已初始化。

**验收**：`pytest tests/step02b_video_gen tests/step02_video_gen tests/enhancements/test_video_reroll.py tests/integration/test_chain1_video_recovery.py`；
覆盖 `_process_one_shot`、候选提交、恢复已有 task、缓存命中/拒收；POST 次数与请求身份
由现有用例断言；`python -c "import step02_video_gen.submit_poll"`；目标净行数 ≤1,850。

## 4. S08：`step03_tts_audio/shot_tts.py`（1,502 行）

**保留**：同步/异步合成循环（`gen_single_tts`/`_gen_single_tts_async`/`_prepare_shot_unit`/
`gen_shot_tts`/`_gen_shot_tts_async`/`gen_all_shot_tts`/`gen_narration_tts`）、缓存与产物
（`_cache_hit`/`_drop_cached`/`_unit_artifact`）、`load_script`/`current_scope`/
`script_revision_of`/`_caps_of`/`MAX_RETRIES`/`TTS_SEMAPHORE`。

**迁出**（`step03_tts_audio/parts/`）：

| 模块 | 函数 | 行数 | 累计占比 |
|---|---|---:|---:|
| `speakers.py` | `_detect_speaker_attributes`(21)、`resolve_shot_speaker`(73)、`_dialogue_speech`(7)、`resolve_voice`(79)、`_primary_min_units`(8)、`role_visibility`(26)、`role_shot_sets`(25)、`_has_frame_layout_data`(12)、`derive_voice_tiers`(99)、`plan_role_voices`(50)、`_voice_key`(6)、`_tier_for_standalone`(6)、`canonical_character_role`(18)、`dialogue_speech_unit`(26)、`narration_speech_unit`(25)、`_window_of`(3)、`_compute_voice_windows`(55)、`_transition_hint`(14)、`narration_subset_entries`(26)、`_role_counts_free`(20) | 599 | 39.9% |
| `units.py` | `build_speech_units`(93)、`check_voice_uniqueness`(63) | 156 | 50.3% |

**依赖与路由**：

| 名字 | 类型 | 处理 |
|---|---|---|
| `NARRATOR_ROLE`、`MAX_RETRIES`、`TTS_SEMAPHORE` | 父模块常量/状态 | 合成循环留在父；迁出函数不直接引用 |
| `current_scope`、`script_revision_of` | 父模块函数 | `_pc` 调用（保持替换生效） |
| `_caps_of`、`provider_capabilities` | 可替换 callable | `_caps_of` 留父；speakers 内 `_pc("_caps_of", ...)` |
| `resolve_voice`、`resolve_shot_speaker`、`_dialogue_speech` | 测试 patch 目标 | 父模块 re-export；迁出函数内部一律 `_pc(...)` |
| `build_speech_units` | 测试 patch 目标 | 父模块 re-export；父合成循环直接调用父名字 |
| `synthesize_unit`、`create_provider` | 父模块导入/测试 patch | 合成循环留父，不受拆分影响 |

**打桩清单**：`build_speech_units`、`create_provider`、`provider_capabilities`、
`resolve_voice`、`_dialogue_speech`、`resolve_applied`、`_unit_artifact`、`_cache_hit`、
`run_split_transaction`、`synthesize_unit`。

**插入点**：第 57 行后（`_tts_cache_sig` 之前）插入 re-export；`MAX_RETRIES`/
`TTS_SEMAPHORE` 等初始化必须已执行。

**验收**：`pytest tests/step03_tts_audio tests/tts tests/shared/test_audible_speech.py tests/step01_script_gen/test_narration_voice_delivery.py tests/step01_script_gen/test_voice_adaptation.py tests/step01_script_gen/test_p3_split_entry.py`；
覆盖 `build_speech_units`、同步/异步合成桩、旁白子集、缓存拒收；`python -c "import step03_tts_audio.shot_tts"`；
目标净行数 ≤1,000。

## 5. S09：`step01_script_gen/outline.py`（7,483 行）

**保留**：生成/修订编排（`generate_outline`/`generate_outline_result`/`revise_plan_full`/
`revise_plan_once`/`run_plan_revision`/`replan_dialogue_within_budget`/
`enforce_budget_by_replanning`）、提示与 schema 常量、缓存提交
（`_rotate_outline_cache_on_regen`/`_cache_backup`/`_backup_*`）、路由
（`_replan_route`/`_legacy_route`/`default_revision_transport`）、
`_replan_by_split`/`_replan_by_voice_split`（第二里程碑，先不搬）。

**迁出到 `step01_script_gen/orchestrator/`（不导入 `runtime`）**：

| 模块 | 函数 | 行数 | 累计占比 |
|---|---|---:|---:|
| `outline_structure.py` | `_outline_max_tokens`(7)、`_parse_outline_json`(16)、`_with_protocol_fields`(43)、`outline_structure_problems`(178)、`plan_acceptance_problems`(130)、`_split_acceptance_records`(12)、`_sentence_alternatives`(42)、`_budget_limits`(67)、`_uid_tables_of`(18)、`_uid_for`(10)、`_calibrate_time_budget`(27)、`_validate_outline`(116)、`_check_outline_dialogue_grounding`(35)、`_malformed_numbered_item`(32)、`_reject_malformed_numbered_items`(11)、`normalize_hybrid_plan`(229)、`normalize_plan_for_mode`(6)、`outline_to_anchor`(45)、`presentation_mode`(13)、`outline_prompt_for_mode`(15)、`outline_schema_for_mode`(5) | 1,057 | 14.1% |
| `outline_ops.py` | `apply_replan_ops`(266)、`_norm_beat_carrier`(47)、`_coerce_op_index`(19)、`_norm_narration_entries`(39)、`_brief_ids`(6)、`_join_replan_problems`(18)、`_plan_invariants_intact`(26)、`_plan_evidence_intact`(61)、`_strip_fact_carrier`(4)、`_plan_carrier_declarations_intact`(161)、`_hook_carrier_decl_text`(13)、`_brief_value`(4) | 664 | 23.0% |
| `outline_feedback.py` | `_problem_identity`(5)、`_unexpanded_block`(25)、`_revision_feedback_block`(42)、`_rejection_feedback_block`(49)、`_view_feedback_block`(46)、`_structure_feedback_block`(23)、`_allowed_fields_block`(14)、`render_protected_fields_rule`(18)、`render_protected_fields_inline`(11)、`render_plan_baseline`(11)、`render_full_plan_rule`(13)、`_render_plan_for_revision`(17)、`_full_plan_response_problem`(25)、`_view_identity`(21)、`_view_path`(27)、`_view_response_shape`(25)、`_response_summary`(29)、`_canonical_view_map`(32)、`_replan_by_ops`(69)、`_replan_by_view`(184)、`_replan_by_slots`(210)、`_content_slots_applicable`(149)、`_slots_revision_outcome`(100)、`_slot_deferral`(46)、`_voice_targets_from_slots`(36)、`_split_terminal`(6)、`_verification_record_for`(7)、`_detail_vp`(9)、`complete_candidate`(54)、`_accept_view_candidate`(3) | 1,306 | 40.5% |

合计迁出 **3,027 行（40.5%）**；父模块加 re-export 导入块（约 90–200 行）后仍 ≥35%。

**依赖与路由**：

| 名字 | 类型 | 处理 |
|---|---|---|
| 提示/schema 常量（`_OUTLINE_SYSTEM_PROMPT`、`_REVISION_NOVEL_PROMPT`、`_PLAN_*`、`ROUTE_*`、`BEAT_OP_*`、`PLAN_*_RULE_VERSION`） | 导入期常量 | 迁出函数 `_pv` 每次读取；常量定义仍留父模块 |
| `_parse_outline_json` | 迁出到 structure | 同包内 `from step01_script_gen.orchestrator import outline_structure as _os`，按 order 先于 feedback/ops |
| `outline_structure_problems`、`plan_acceptance_problems`、`_validate_outline`、`_content_slots_applicable` | 测试 patch 目标 | 父模块 re-export；迁出函数内部 `_pc(...)`，保证 `o.X = ...` 生效 |
| `delta_request`、`revision_request` | 父模块导入的 callable，测试 patch | `_pc` 调用 |
| `create_service`、`generate_outline_result`、`run_plan_revision`、`enforce_budget_by_replanning`、`_rotate_outline_cache_on_regen`、`_cache_backup`、`round_archive_writer` | 父模块编排/缓存 | 不迁，保持父定义 |
| `_replan_by_split`/`_replan_by_voice_split` | 第二里程碑 | 先留父；它们调用迁出函数时经父模块名字（re-export） |

**S09 额外约束**：

- 三个模块放入 `orchestrator/`，因此 `PROMPT_SOURCE_FINGERPRINT` 自动覆盖其源码；
  **不得**修改 `quality_issues.py` 的指纹计算器。拆分后 `PROMPT_SOURCE_FINGERPRINT`
  会变化（诊断字段，不参与门禁），需在交付文档记录旧/新值。
- `orchestrator/__init__.py` 增加 `OUTLINE_HELPERS`（固定顺序
  `outline_structure → outline_ops → outline_feedback`），不并入 `MODULE_ORDER`
  （`tools/split_step01_main.py` 的 ORDER/ALIASES 保持原样）。
- 扩展 `tests/step01_script_gen/test_orchestrator_guards.py`：
  1. `_ALLOWED_IMPORTERS` 加入 `step01_script_gen/outline.py`；
  2. 三个 helper 不得导入 `step01_script_gen.orchestrator` 任何模块（含 runtime）；
  3. helper 之间只允许按 `OUTLINE_HELPERS` 顺序导入；
  4. 新增注入对照：其他 step01 模块导入 helper 仍被拒。
- `outline.py` 内使用绝对别名导入，例如
  `from step01_script_gen.orchestrator import outline_structure as _os`，不用相对导入。

**插入点**：第 305 行后（`replan_protected_fields` 相关常量之后、`_replan_editable_keys`
之前）插入三段 re-export；`orchestrator` 包不导入 `runtime`，因此不触发导入期副作用。

**验收**：`pytest tests/step01_script_gen tests/shared/test_cache_and_card_handoff.py tests/shared/test_consumption_admission_matrix.py tests/shared/test_plan_revision_delta_end_to_end.py tests/tools/test_replay_plan_revision.py`；
正常运行链 `main → concurrent_extract → unified_plan → lightweight_plan`、`revise_plan_once`、
合法 ops、缓存/拒收；`python -c "import step01_script_gen.outline"`；
`RULE_SOURCE_FINGERPRINT` 不变；记录 `PROMPT_SOURCE_FINGERPRINT` 前后值；目标净行数 ≤4,800。

## 6. 总表与初始待办

| 模块 | 文件 | 迁出 | 目标净行 | 关键门禁 |
|---|---|---|---:|---|
| S06 | `step04_merge/main.py` | 640（33.8%） | ≤1,350 | 合并/拒收/发布恢复；无 patch 目标 |
| S07 | `submit_poll.py` | 1,268（47.5%） | ≤1,850 | 提交/恢复/POST 身份；补丁语义 |
| S08 | `shot_tts.py` | 755（50.3%） | ≤1,000 | 声音归属/窗口/缓存/同步异步 |
| S09 | `outline.py` | 3,027（40.5%） | ≤4,800 | 正常规划链/修订/缓存；提示指纹记变化 |

- [ ] S06：按 §2 拆 `step04_merge/main.py` 并独立提交。
- [ ] S07：按 §3 拆 `submit_poll.py` 并独立提交。
- [ ] S08：按 §4 拆 `shot_tts.py` 并独立提交。
- [ ] S09：按 §5 拆 `outline.py` 并独立提交。
- [ ] S10：整包复核与两章只读刷新预演。
