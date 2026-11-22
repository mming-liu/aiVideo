# tests/ 入库盘点（2026-11-22，M09 只读）

磁盘上 `tests/` 共 633 个 .py，其中受 git 管理 280 个。
分类依据：源码文本特征（见下）+ 干净检出工作树（`815e936`，复制未入库测试）的分组跑测结果。
含作品词（work_terms）只记录，不作为分类依据。

| 分类 | 已入库 | 未入库 |
|---|---|---|
| 辅助模块（非 test_） | 14 | 17 |
| 引用已不存在的模块 | 0 | 0 |
| 依赖本机绝对路径或真实凭据 | 1 | 2 |
| 依赖生产 output / 冻结现场 / 真实小说文件 | 24 | 13 |
| 无外部依赖且本轮全部通过（基础测试候选） | 223 | 296 |
| 无外部依赖但本轮有失败/错误 | 15 | 5 |
| 未被分组跑测收集 | 3 | 20 |

特征：`prod_output` 引用生产 output 目录；`tmp_dir` 引用 .tmp/.pytest_tmp；`abs_path` 本机绝对路径；
`frozen_env` 冻结现场/归档；`real_creds` 真实凭据文件；`real_novel_file` 真实小说文件；`work_terms` 作品词。

## 未入库文件明细

| 文件 | 分类 | 特征 | 本轮结果 |
|---|---|---|---|
| `tests/_image_gen/__init__.py` | 辅助模块（非 test_） | — | — |
| `tests/_image_gen/test_agent_plan_provider.py` | 无外部依赖但本轮有失败/错误 | — | failed=19,passed=10 |
| `tests/_text_gen/__init__.py` | 辅助模块（非 test_） | — | — |
| `tests/_text_gen/test_agent_plan_sent_format.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=2 |
| `tests/_text_gen/test_deepseek_default_model.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=1 |
| `tests/_text_gen/test_deepseek_json_mode.py` | 无外部依赖但本轮有失败/错误 | — | failed=3 |
| `tests/_text_gen/test_deepseek_streaming.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=7 |
| `tests/_text_gen/test_deepseek_timeout.py` | 无外部依赖但本轮有失败/错误 | — | failed=8,passed=1 |
| `tests/_text_gen/test_send_guard.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=8 |
| `tests/_text_gen/test_sse_transport_local.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=14 |
| `tests/_text_gen/test_truncation_contract.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output | failed=5,passed=8 |
| `tests/_video_gen/__init__.py` | 辅助模块（非 test_） | — | — |
| `tests/_video_gen/test_agent_plan_video_provider.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/_video_gen/test_camera_movement_provider.py` | 未被分组跑测收集 | work_terms | — |
| `tests/_video_gen/test_duration_rounding.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=2 |
| `tests/_video_gen/test_seedance_seed_caps.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/enhancements/_candidate_fixtures.py` | 辅助模块（非 test_） | — | — |
| `tests/enhancements/build_fixtures.py` | 辅助模块（非 test_） | tmp_dir | — |
| `tests/enhancements/fixture_media.py` | 辅助模块（非 test_） | — | — |
| `tests/enhancements/test_enhancement_io.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=34 |
| `tests/enhancements/test_media_inspect.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=49 |
| `tests/enhancements/test_merge_backend.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=19 |
| `tests/enhancements/test_merge_cli.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=14 |
| `tests/enhancements/test_merge_timeline.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=38 |
| `tests/enhancements/test_merge_verify_tool.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=6 |
| `tests/enhancements/test_motion_cli.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir,work_terms | passed=14 |
| `tests/enhancements/test_motion_overlay.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=24 |
| `tests/enhancements/test_motion_render.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=14 |
| `tests/enhancements/test_motion_revise.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=18 |
| `tests/enhancements/test_motion_schema.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=26 |
| `tests/enhancements/test_motion_validate.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir,work_terms | passed=44 |
| `tests/enhancements/test_subtitle_alignment.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=32 |
| `tests/enhancements/test_transaction_cli.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=15 |
| `tests/enhancements/test_tts_candidate_cli.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=10 |
| `tests/enhancements/test_tts_media_inputs.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=18 |
| `tests/enhancements/test_tts_providers.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output,work_terms | failed=2,passed=12 |
| `tests/enhancements/test_tts_synthesis_job.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=26 |
| `tests/enhancements/test_video_attempts.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=9 |
| `tests/enhancements/test_windows_process_liveness.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=1 |
| `tests/enhancements/worker_stub.py` | 辅助模块（非 test_） | — | — |
| `tests/integration/__init__.py` | 辅助模块（非 test_） | — | — |
| `tests/integration/conftest.py` | 辅助模块（非 test_） | tmp_dir | — |
| `tests/integration/isolation_guard.py` | 辅助模块（非 test_） | prod_output,tmp_dir | — |
| `tests/integration/test_chain1_video_recovery.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=6 |
| `tests/integration/test_chain2_audio_merge.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=2 |
| `tests/integration/test_chain3_content_revision.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=6 |
| `tests/integration/test_chain3b_video_and_alignment.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/integration/test_chain4_full_enhancement.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=1 |
| `tests/integration/test_chain6_candidate_admission.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=6 |
| `tests/integration/test_chain7_screen_card_composition.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/integration/test_isolation_enforced.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/integration/test_screen_card_sample_film.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=7 |
| `tests/integration/video_chain_stub.py` | 辅助模块（非 test_） | — | — |
| `tests/shared/__init__.py` | 辅助模块（非 test_） | — | — |
| `tests/shared/_stage_head_probe.py` | 辅助模块（非 test_） | — | — |
| `tests/shared/test_adoption_config_inheritance.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=2 |
| `tests/shared/test_audio_policy.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/shared/test_audit_authorization.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=24 |
| `tests/shared/test_consumption_admission_matrix.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=63 |
| `tests/shared/test_content_safety_collapse.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/shared/test_content_safety_sanitize_words.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/shared/test_ff_state_preservation.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=8 |
| `tests/shared/test_frame_roster_refs.py` | 未被分组跑测收集 | work_terms | — |
| `tests/shared/test_frame_spec.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=60 |
| `tests/shared/test_gaze_state_qc.py` | 未被分组跑测收集 | work_terms | — |
| `tests/shared/test_image_qc.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=17 |
| `tests/shared/test_image_qc_v6541.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=13 |
| `tests/shared/test_json_repair_truncation.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=9 |
| `tests/shared/test_kf_refs_per_frame.py` | 未被分组跑测收集 | work_terms | — |
| `tests/shared/test_kf_time_contract.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/shared/test_motion_segments.py` | 未被分组跑测收集 | work_terms | — |
| `tests/shared/test_plan_revision_delta.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=37 |
| `tests/shared/test_production_docs_match_judgements.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=8 |
| `tests/shared/test_quality_gate.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=18 |
| `tests/shared/test_reference_identity_contract.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=35 |
| `tests/shared/test_revision_request_evidence.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/shared/test_run_scope.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=29 |
| `tests/shared/test_scene_analysis_request_limit.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/shared/test_scene_objects_bank_consolidate.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=2 |
| `tests/shared/test_screen_cards.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir,work_terms | passed=35 |
| `tests/shared/test_semantic_barrier.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=28 |
| `tests/shared/test_semantic_write_lockdown.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/shared/test_stage_chapter_identity.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/shared/test_style_follows_video_style.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/shared/test_style_prefix_ff_fixes.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=3 |
| `tests/shared/test_temporal_bounds.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=21 |
| `tests/shared/test_text_integrity.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=3 |
| `tests/shared/test_threat_visible_v650.py` | 依赖本机绝对路径或真实凭据 | prod_output,abs_path,work_terms | passed=18 |
| `tests/shared/test_vimax_bridge_prop_costume.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/shared/test_voice_state.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=7 |
| `tests/step01_script_gen/__init__.py` | 辅助模块（非 test_） | — | — |
| `tests/step01_script_gen/constraints/__init__.py` | 辅助模块（非 test_） | — | — |
| `tests/step01_script_gen/constraints/test_axis_jump.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_boat_width.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=9 |
| `tests/step01_script_gen/constraints/test_bridge_words.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_camera_movement_fill.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_camera_variety.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=6 |
| `tests/step01_script_gen/constraints/test_char_layout_sync.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_climax_completeness.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=9 |
| `tests/step01_script_gen/constraints/test_climax_distribution.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=1 |
| `tests/step01_script_gen/constraints/test_climax_priority.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_combat_impact.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=7 |
| `tests/step01_script_gen/constraints/test_conflict_detector.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=7 |
| `tests/step01_script_gen/constraints/test_constraint_order_dependency.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/constraints/test_core_keyword_coverage.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_core_keyword_pollution_cleanup.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=2 |
| `tests/step01_script_gen/constraints/test_dialogue_continuation.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/step01_script_gen/constraints/test_dialogue_grounding.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=11 |
| `tests/step01_script_gen/constraints/test_duration_budget.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/constraints/test_emotion_visualization_repair.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=1 |
| `tests/step01_script_gen/constraints/test_engine.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=34 |
| `tests/step01_script_gen/constraints/test_engine_warn_logging.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/step01_script_gen/constraints/test_ff_motion_overlap.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/step01_script_gen/constraints/test_final_quality_normalizer.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=11 |
| `tests/step01_script_gen/constraints/test_frame_anchor_rebuild.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=1 |
| `tests/step01_script_gen/constraints/test_frame_layouts_required_kfs_key.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_frame_spec_props_consistency.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/constraints/test_frame_spec_repair.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_frame_spec_roster_coverage.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_frame_text_state_cleanup.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=2 |
| `tests/step01_script_gen/constraints/test_framing_diversity_v2.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=6 |
| `tests/step01_script_gen/constraints/test_framing_prefix_fill.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=9 |
| `tests/step01_script_gen/constraints/test_framing_variety.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/constraints/test_full_hardfix_kf_idempotency.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=2 |
| `tests/step01_script_gen/constraints/test_gaze_direction.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=10 |
| `tests/step01_script_gen/constraints/test_gaze_state_repair.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=2 |
| `tests/step01_script_gen/constraints/test_gaze_typo.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_group_actor_continuity.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=3 |
| `tests/step01_script_gen/constraints/test_group_actor_presence.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/step01_script_gen/constraints/test_handle_ambiguity_prop_state.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_hook_duration.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=7 |
| `tests/step01_script_gen/constraints/test_identity_unification.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_injury_continuity.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=11 |
| `tests/step01_script_gen/constraints/test_kf_blood_monotonic.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=10 |
| `tests/step01_script_gen/constraints/test_kf_event_order.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/step01_script_gen/constraints/test_kf_frame_redundancy.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/constraints/test_kf_monotonic.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=6 |
| `tests/step01_script_gen/constraints/test_kf_pose_anchor.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/constraints/test_kf_pre_post_conflict.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/constraints/test_kf_t_sec_bounds.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=9 |
| `tests/step01_script_gen/constraints/test_kf_time_window_alignment.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/constraints/test_kf_timeline_normalize.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/step01_script_gen/constraints/test_kf_weak_template.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=7 |
| `tests/step01_script_gen/constraints/test_kneel_beside_victim.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=2 |
| `tests/step01_script_gen/constraints/test_lf_desc_length.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/step01_script_gen/constraints/test_lf_midstate.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_mandatory_biz_fields.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=7 |
| `tests/step01_script_gen/constraints/test_motion_segment_kf_align.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=7 |
| `tests/step01_script_gen/constraints/test_motion_time_integrity.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/constraints/test_multi_char_layout_depth.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_narration_subtitle_consistency.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=11 |
| `tests/step01_script_gen/constraints/test_new_quality_guards.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/step01_script_gen/constraints/test_opening_empty_shot_cap.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/constraints/test_opening_empty_shot_insert.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_opening_empty_shot_remove.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_opening_hook.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/constraints/test_overshoulder_consistency.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_platform_compliance.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_post_combat_blank.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_prompt_repetition.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=9 |
| `tests/step01_script_gen/constraints/test_prompt_repetition_dangling.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=2 |
| `tests/step01_script_gen/constraints/test_prop_position.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=10 |
| `tests/step01_script_gen/constraints/test_punch_density.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_redguo_scene_char_limit.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_registry.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/step01_script_gen/constraints/test_restrained_emotion_chain.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_role_anchor.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_role_anchor_onscreen.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/constraints/test_runtime_extend.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/step01_script_gen/constraints/test_short_shot_merge.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/step01_script_gen/constraints/test_shot_index_renumber.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=10 |
| `tests/step01_script_gen/constraints/test_split_content_similarity.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=17 |
| `tests/step01_script_gen/constraints/test_split_continuity.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=7 |
| `tests/step01_script_gen/constraints/test_structure_fixes_0912.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=9 |
| `tests/step01_script_gen/constraints/test_surface_backfill.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=3 |
| `tests/step01_script_gen/constraints/test_threat_facing.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=14 |
| `tests/step01_script_gen/constraints/test_threat_gaze_coupling.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/constraints/test_transition_keywords.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/constraints/test_v6_62_2_generic_fallbacks.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/step01_script_gen/constraints/test_voice_text_fidelity_20261026.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/constraints/test_weapon_state_consistency.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/prompt_semantic/__init__.py` | 辅助模块（非 test_） | — | — |
| `tests/step01_script_gen/prompt_semantic/test_canonical_anchor.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/prompt_semantic/test_extractor.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=7 |
| `tests/step01_script_gen/prompt_semantic/test_normalizer.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/prompt_semantic/test_physics.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=11 |
| `tests/step01_script_gen/prompt_semantic/test_registry.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=13 |
| `tests/step01_script_gen/test_action_load.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=2 |
| `tests/step01_script_gen/test_archived_e_chain.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | tmp_dir,frozen_env,work_terms | skipped=2 |
| `tests/step01_script_gen/test_audit_compliance_and_target_cap.py` | 未被分组跑测收集 | — | — |
| `tests/step01_script_gen/test_audit_console_encoding.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=1 |
| `tests/step01_script_gen/test_audit_literal_evidence.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/step01_script_gen/test_audit_paired_progress.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/step01_script_gen/test_audit_resolution_fallback.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/test_audit_round_diag.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=23 |
| `tests/step01_script_gen/test_audit_rules_duration_and_frames.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_audit_single_send.py` | 无外部依赖但本轮有失败/错误 | — | failed=2,passed=8 |
| `tests/step01_script_gen/test_audit_timeout_budget.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/step01_script_gen/test_authored_time_container.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_axes_admission_and_deadline.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=35 |
| `tests/step01_script_gen/test_axes_durable_failures.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | skipped=20 |
| `tests/step01_script_gen/test_batch_store_matrix_r26.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=40 |
| `tests/step01_script_gen/test_beat_record_compat.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=13 |
| `tests/step01_script_gen/test_candidate_commit_chain.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/step01_script_gen/test_candidate_commit_real.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output,frozen_env | skipped=1 |
| `tests/step01_script_gen/test_candidate_delivery.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=12 |
| `tests/step01_script_gen/test_candidate_parallel.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=17 |
| `tests/step01_script_gen/test_carrier_entailment.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | skipped=29 |
| `tests/step01_script_gen/test_carrier_entailment_boundaries.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=1,skipped=30 |
| `tests/step01_script_gen/test_carrier_replay_tool_r16.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=3 |
| `tests/step01_script_gen/test_carrier_time_semantics_r15.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/step01_script_gen/test_ch1_first_version_positive_sample.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=33 |
| `tests/step01_script_gen/test_ch1_frozen_hybrid_delta.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=23 |
| `tests/step01_script_gen/test_character_consistency_generic.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/test_commit_disk_identity.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_commit_manifest_blocking_coverage.py` | 未被分组跑测收集 | — | — |
| `tests/step01_script_gen/test_delivery_followup.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=35 |
| `tests/step01_script_gen/test_diagnostic_chain_r11.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=8,skipped=3 |
| `tests/step01_script_gen/test_diagnostic_identity.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=2 |
| `tests/step01_script_gen/test_direction_repair_semantics.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/step01_script_gen/test_duration_convergence.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=14 |
| `tests/step01_script_gen/test_emotion_repair_scope.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=115 |
| `tests/step01_script_gen/test_facing_identity.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=7 |
| `tests/step01_script_gen/test_fingerprint_split.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=7 |
| `tests/step01_script_gen/test_fixture_integrity.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=12 |
| `tests/step01_script_gen/test_flash_request_contract.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/step01_script_gen/test_frame_layout_entry_delete.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=23 |
| `tests/step01_script_gen/test_frame_presence_exit_tense.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/test_gap_zero_climax.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=15 |
| `tests/step01_script_gen/test_generation_kf_contract.py` | 未被分组跑测收集 | — | — |
| `tests/step01_script_gen/test_golden_chapter_001.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=20 |
| `tests/step01_script_gen/test_hook_quality_gate.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/step01_script_gen/test_hook_time_invariant.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=11 |
| `tests/step01_script_gen/test_kf_missing_single_report.py` | 未被分组跑测收集 | — | — |
| `tests/step01_script_gen/test_kf_missing_structural_convergence.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/test_kf_pairing_assembly.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/test_kf_person_visibility.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=39 |
| `tests/step01_script_gen/test_kf_segment_missing_authorization.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/test_kf_segment_missing_gate.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/test_kf_structural_repair.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=22 |
| `tests/step01_script_gen/test_kf_temporal_false_positive.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=5 |
| `tests/step01_script_gen/test_kf_temporal_pose_map_identity.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/test_main_regen_guard.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=38 |
| `tests/step01_script_gen/test_metrics.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/step01_script_gen/test_motion_narrow_patch.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=11 |
| `tests/step01_script_gen/test_motion_time_parsing.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=1 |
| `tests/step01_script_gen/test_multi_person_staging_actions.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=10 |
| `tests/step01_script_gen/test_multi_person_staging_authoritative_field.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/test_narration_voice_delivery.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=20 |
| `tests/step01_script_gen/test_object_extractor_truncation.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/step01_script_gen/test_order_gate_r17.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output,work_terms | passed=8 |
| `tests/step01_script_gen/test_outline_budget_and_repair_prompt.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=6 |
| `tests/step01_script_gen/test_outline_planning.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=7 |
| `tests/step01_script_gen/test_parse_shots_structure.py` | 未被分组跑测收集 | — | — |
| `tests/step01_script_gen/test_person_boat_position.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=2 |
| `tests/step01_script_gen/test_plan_carrier_contract.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=13 |
| `tests/step01_script_gen/test_plan_coherence.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=26 |
| `tests/step01_script_gen/test_plan_contract_r2.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=47,skipped=2 |
| `tests/step01_script_gen/test_plan_delta_experiment_rescore.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_plan_narration_fact_support.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8,skipped=1 |
| `tests/step01_script_gen/test_plan_normalize_ranges.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=33 |
| `tests/step01_script_gen/test_plan_ops_representation.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=38 |
| `tests/step01_script_gen/test_plan_parse_boundaries.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=23 |
| `tests/step01_script_gen/test_plan_phase_a_matrix.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=106,skipped=3 |
| `tests/step01_script_gen/test_plan_phase_b_deadline.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output | passed=54 |
| `tests/step01_script_gen/test_plan_phase_c_declaration_ops.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=97 |
| `tests/step01_script_gen/test_plan_phase_i_acceptance_reasons.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/step01_script_gen/test_plan_phase_j_combinations.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_plan_phase_j_properties.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/step01_script_gen/test_plan_phase_l_interface_decision.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=22 |
| `tests/step01_script_gen/test_plan_phase_m_composition.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | real_novel_file,work_terms | passed=3 |
| `tests/step01_script_gen/test_plan_preview.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output | passed=13 |
| `tests/step01_script_gen/test_plan_revision_controller.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=59 |
| `tests/step01_script_gen/test_plan_revision_legal_sample.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_planning_request_contract_delivery.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=13 |
| `tests/step01_script_gen/test_postfix_roots_0912.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=8 |
| `tests/step01_script_gen/test_presence_subject_binding.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=8 |
| `tests/step01_script_gen/test_projected_timecode_score.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=2 |
| `tests/step01_script_gen/test_prompt_provider_selection.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_prop_state_anchor.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output,real_novel_file,work_terms | failed=2,passed=53,skipped=7 |
| `tests/step01_script_gen/test_quality_issues.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=28 |
| `tests/step01_script_gen/test_quantified_verification.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | frozen_env | skipped=31 |
| `tests/step01_script_gen/test_redguo_duration_guard.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/step01_script_gen/test_regen_acceptance_gates.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=16 |
| `tests/step01_script_gen/test_relay_repair_model.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=13 |
| `tests/step01_script_gen/test_repair_effects.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=74 |
| `tests/step01_script_gen/test_repair_fields.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=18 |
| `tests/step01_script_gen/test_repair_mask_baseline.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=7 |
| `tests/step01_script_gen/test_repair_patches.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=9 |
| `tests/step01_script_gen/test_repair_rollback_granularity.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=1 |
| `tests/step01_script_gen/test_review_fixes_0915.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/step01_script_gen/test_revision_identity.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=23 |
| `tests/step01_script_gen/test_rule_version_cache.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_rules_index.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=6 |
| `tests/step01_script_gen/test_run_lifecycle_r12.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=3,skipped=4 |
| `tests/step01_script_gen/test_run_lifecycle_r13.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=8 |
| `tests/step01_script_gen/test_run_lifecycle_r14.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output,tmp_dir | skipped=9 |
| `tests/step01_script_gen/test_run_lifecycle_r15.py` | 依赖本机绝对路径或真实凭据 | tmp_dir,real_creds | skipped=9 |
| `tests/step01_script_gen/test_run_lifecycle_r16.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=6 |
| `tests/step01_script_gen/test_run_lifecycle_r17.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=10 |
| `tests/step01_script_gen/test_run_lifecycle_r18.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=7 |
| `tests/step01_script_gen/test_run_lifecycle_r19.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=7 |
| `tests/step01_script_gen/test_run_lifecycle_r20.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=8 |
| `tests/step01_script_gen/test_run_lifecycle_r21.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=6 |
| `tests/step01_script_gen/test_run_lifecycle_r22.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=12 |
| `tests/step01_script_gen/test_run_lifecycle_r23.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=11 |
| `tests/step01_script_gen/test_run_lifecycle_r24.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=11 |
| `tests/step01_script_gen/test_run_lifecycle_r25.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | skipped=10 |
| `tests/step01_script_gen/test_run_provenance.py` | 无外部依赖但本轮有失败/错误 | work_terms | failed=4,passed=26 |
| `tests/step01_script_gen/test_same_model_audit.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=51 |
| `tests/step01_script_gen/test_scene_anchors.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=29 |
| `tests/step01_script_gen/test_screen_hybrid_components_chain.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=26 |
| `tests/step01_script_gen/test_semantic_lint_boundaries.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=14 |
| `tests/step01_script_gen/test_short_audit_evidence.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_slots_binding_policy.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=21 |
| `tests/step01_script_gen/test_source_references.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=9 |
| `tests/step01_script_gen/test_split_frame_layouts_kfs.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=7 |
| `tests/step01_script_gen/test_split_shot_differentiation.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=16 |
| `tests/step01_script_gen/test_state_updates.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=13 |
| `tests/step01_script_gen/test_step01_lock.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5,skipped=1 |
| `tests/step01_script_gen/test_structural_convergence.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=16 |
| `tests/step01_script_gen/test_temporal_plan_migration_stats.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/step01_script_gen/test_text_retry_policy.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=16 |
| `tests/step01_script_gen/test_timecode_hook_none.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=2 |
| `tests/step01_script_gen/test_timecode_split_gate.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/step01_script_gen/test_verification_record_delivery.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=5 |
| `tests/step01_script_gen/test_voice_entries_r7.py` | 无外部依赖且本轮全部通过（基础测试候选） | tmp_dir | passed=25 |
| `tests/step01_script_gen/test_voice_entries_r8.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=16 |
| `tests/step01_script_gen/test_weapon_holder.py` | 未被分组跑测收集 | work_terms | — |
| `tests/step01_script_gen/test_weapon_rejection_contract.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=23 |
| `tests/step02_video_gen/test_request_plan_and_decision.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=8 |
| `tests/step02_video_gen/test_video_prompt_voice_split.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=3 |
| `tests/step02a_frame_gen/__init__.py` | 辅助模块（非 test_） | — | — |
| `tests/step02a_frame_gen/test_plan_sheet_materialization.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/step02b_video_gen/test_candidate_cli.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=20 |
| `tests/step02b_video_gen/test_experiment_context.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=9 |
| `tests/step02b_video_gen/test_r2v_reference_policy.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=16 |
| `tests/step02b_video_gen/test_video_prompt_quality.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=3 |
| `tests/step02b_video_gen/test_video_qc_context.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=1 |
| `tests/step02b_video_gen/test_video_trace_gaps.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output | passed=7 |
| `tests/step03_tts_audio/test_tts_duration.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=3 |
| `tests/step04_merge/test_clip_timeline.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=10 |
| `tests/step04_merge/test_narration_overlay.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=26 |
| `tests/step04_merge/test_voice_state_gate.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=7 |
| `tests/test_probe_http_target.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=8 |
| `tests/test_probe_isolation.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=17 |
| `tests/tools/test_ab_tools.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/tools/test_bench_text_provider_ab.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=6 |
| `tests/tools/test_build_storyboard_contact_sheet.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=10 |
| `tests/tools/test_diag_prop_anchor_generation.py` | 无外部依赖且本轮全部通过（基础测试候选） | work_terms | passed=4 |
| `tests/tools/test_replay_step01_repair.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=4 |
| `tests/tools/test_run_step01_final_audit.py` | 依赖生产 output / 冻结现场 / 真实小说文件 | prod_output | passed=9 |
| `tests/tools/test_video_ab_experiment.py` | 无外部依赖且本轮全部通过（基础测试候选） | — | passed=21 |
