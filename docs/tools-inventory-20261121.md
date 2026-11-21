# tools/ 清单（2026-11-21 精简包 S02/S03）

范围：`tools/` 根目录下 63 个受版本控制的 .py（子目录 `archive/`、`enhancement_workers/`、
`motion_runtime/`、`samples/`、`studio_ui/` 不在本表，未改动）。

## 分类规则

- **保留**：满足任一条：被代码引用（`import tools.X`、路径或名字整词出现在非测试代码）；
  被测试引用（含磁盘上尚未入库的 `tests/` 文件：`inspect_video`、`verify_candidate_delivery`、
  `diag_prop_anchor_generation` 就是因此保留）；被 AGENTS.md / CLAUDE.md / README.md 引用；属于基础设施
  （`run_tests_grouped`、`compare_junit`、`ruff_gate`、`print_effective_config`、`scan_import_swallow`）；
  或人工复核为当前工作仍在用（`voice_unit_report`：R7 声音审计 11-19；
  `build_storyboard_contact_sheet`：通用联系表工具，现行增强计划列为既有工具）。
- **归档**：只被已跟踪的文档引用 → 移到 `tools/archive/`，对照表见 `tools/archive/README.md`。
  历史文档保持原样，不改写其中的路径。
- **删除**：上述引用一处都没有 → `git rm`，可用 `git show <提交>:tools/<文件名>` 取回。
- 不计入引用：`docs/memory-archive/`（冻结的历史原文）、`baselines/`、`.gitignore`。

引用按整词匹配，宁可多算（注释中提到也算保留），不会误删仍被引用的脚本。

| 脚本 | 结论 | 代码 | 测试 | 规则 | 文档 | 引用样例 |
|---|---|---|---|---|---|---|
| `tools/_scan_anchors_in_script.py` | 删除 | 0 | 0 | 0 | 0 | `—` |
| `tools/build_frame_mockup_svg.py` | 删除 | 0 | 0 | 0 | 0 | `—` |
| `tools/cleanup_anchor_blocks_in_script.py` | 删除 | 0 | 0 | 0 | 0 | `—` |
| `tools/fix_ch1_repair_0912.py` | 删除 | 0 | 0 | 0 | 0 | `—` |
| `tools/p1a_runner_v2.py` | 删除 | 0 | 0 | 0 | 0 | `—` |
| `tools/replay_agent_plan.py` | 删除 | 0 | 0 | 0 | 0 | `—` |
| `tools/video_attempt_stats.py` | 删除 | 0 | 0 | 0 | 0 | `—` |
| `tools/build_m02_u6_candidate.py` | 归档 | 0 | 0 | 0 | 9 | `docs/dsh-handoff/fix_confirmed_problems_20261113.md` |
| `tools/build_m03_issue_status.py` | 归档 | 0 | 0 | 0 | 4 | `docs/dsh-handoff/remaining_problems_result_20261113.md` |
| `tools/build_s03_identity_shadow_diff.py` | 归档 | 0 | 0 | 0 | 2 | `docs/step01-layered-execution/U6-fin-wp1-wp3-2026-11-14.md` |
| `tools/build_s06_decision_samples.py` | 归档 | 0 | 0 | 0 | 3 | `docs/step01-layered-execution/S06-decision-package-2026-11-13.md` |
| `tools/build_s06_g3_isolated_diff.py` | 归档 | 0 | 0 | 0 | 5 | `docs/step01-layered-execution/S01-S07-content-independence-2026-11-13.md` |
| `tools/build_s06_g7_samples.py` | 归档 | 0 | 0 | 0 | 3 | `docs/step01-layered-execution/S06-decision-package-2026-11-13.md` |
| `tools/align_subtitles.py` | 保留 | 1 | 3 | 0 | 0 | `shared/subtitle_contract.py` |
| `tools/audit_frozen_candidate.py` | 保留 | 4 | 7 | 0 | 5 | `docs/step01-layered-execution/artifacts/u6/audit_package_r2.snapshot.json` |
| `tools/bench_reasoning.py` | 保留 | 4 | 1 | 0 | 2 | `_text_gen/config.py` |
| `tools/bench_text_provider_ab.py` | 保留 | 1 | 1 | 0 | 2 | `tools/ruff_baseline.json` |
| `tools/bench_video_prompt_patterns.py` | 保留 | 1 | 1 | 0 | 2 | `step02_video_gen/prompt_builder.py` |
| `tools/build_plan_preview.py` | 保留 | 0 | 1 | 1 | 1 | `tests/step01_script_gen/test_plan_preview.py` |
| `tools/build_s08_report.py` | 保留 | 1 | 0 | 0 | 3 | `tools/ruff_baseline.json` |
| `tools/build_step01_call_index.py` | 保留 | 0 | 1 | 0 | 5 | `tests/tools/test_step01_call_index.py` |
| `tools/build_storyboard_contact_sheet.py` | 保留 | 0 | 1 | 0 | 4 | `tests/tools/test_build_storyboard_contact_sheet.py` |
| `tools/build_timeline_sample.py` | 保留 | 1 | 1 | 0 | 5 | `tools/build_s08_report.py` |
| `tools/cache_bill.py` | 保留 | 2 | 2 | 0 | 3 | `tools/ruff_baseline.json` |
| `tools/cleanup_test_artifacts.py` | 保留 | 2 | 1 | 0 | 2 | `pytest.ini` |
| `tools/compare_junit.py` | 保留 | 2 | 0 | 0 | 1 | `tools/junit_allow_review_20261119.txt` |
| `tools/diag_prop_anchor_generation.py` | 保留 | 0 | 1 | 0 | 4 | `tests/tools/test_diag_prop_anchor_generation.py` |
| `tools/diagnose_video_gaps.py` | 保留 | 0 | 1 | 0 | 3 | `tests/tools/test_diagnose_video_gaps.py` |
| `tools/frame_source_table.py` | 保留 | 1 | 0 | 0 | 1 | `tools/ruff_baseline.json` |
| `tools/freeze_step01_evidence.py` | 保留 | 1 | 0 | 0 | 1 | `tools/ruff_baseline.json` |
| `tools/inspect_video.py` | 保留 | 0 | 3 | 0 | 0 | `tests/enhancements/test_media_inspect.py` |
| `tools/minimal_real_run.py` | 保留 | 1 | 1 | 0 | 0 | `tools/verify_frozen_candidate.py` |
| `tools/motion_lab.py` | 保留 | 2 | 3 | 0 | 1 | `shared/motion_render/__init__.py` |
| `tools/p3_split_real_run.py` | 保留 | 0 | 1 | 0 | 0 | `tests/step01_script_gen/test_p3_pre_e.py` |
| `tools/p4_admission_check.py` | 保留 | 0 | 2 | 0 | 3 | `tests/tools/p4_admission_builder.py` |
| `tools/p4_stability_observe.py` | 保留 | 0 | 1 | 0 | 0 | `tests/step01_script_gen/test_p4_stability_stop_guard.py` |
| `tools/print_effective_config.py` | 保留 | 0 | 1 | 0 | 2 | `tests/tools/test_print_effective_config.py` |
| `tools/probe_isolation.py` | 保留 | 2 | 13 | 0 | 1 | `tools/replay_duration_budget.py` |
| `tools/production_context.py` | 保留 | 4 | 3 | 0 | 7 | `shared/config.py` |
| `tools/reparse_final_audit.py` | 保留 | 0 | 1 | 0 | 3 | `tests/tools/test_reparse_final_audit.py` |
| `tools/replay_duration_budget.py` | 保留 | 0 | 1 | 0 | 0 | `tests/step01_script_gen/test_duration_band.py` |
| `tools/replay_run_h_frame_identity.py` | 保留 | 0 | 2 | 0 | 2 | `tests/step01_script_gen/test_kf_frame_identity.py` |
| `tools/replay_run_j_attribution.py` | 保留 | 1 | 0 | 0 | 2 | `docs/step01-layered-execution/run_j_attribution_replay.json` |
| `tools/replay_step01_repair.py` | 保留 | 0 | 3 | 0 | 4 | `tests/step01_script_gen/test_main_facade_contract.py` |
| `tools/ruff_gate.py` | 保留 | 1 | 1 | 0 | 1 | `ruff.toml` |
| `tools/run_step01_final_audit.py` | 保留 | 1 | 2 | 0 | 3 | `step01_script_gen/orchestrator/runtime.py` |
| `tools/run_step01_from_verified_plan.py` | 保留 | 1 | 1 | 0 | 5 | `tools/ruff_baseline.json` |
| `tools/run_tests_grouped.py` | 保留 | 0 | 1 | 0 | 2 | `tests/tools/test_run_tests_grouped.py` |
| `tools/scan_import_swallow.py` | 保留 | 0 | 0 | 0 | 1 | `docs/pkg1-offline-hygiene-20261119.md` |
| `tools/script_preflight.py` | 保留 | 4 | 7 | 0 | 11 | `shared/script_preflight.py` |
| `tools/shadow_compare_step01_blockers.py` | 保留 | 1 | 1 | 0 | 3 | `tools/build_s08_report.py` |
| `tools/split_step01_main.py` | 保留 | 15 | 2 | 0 | 1 | `step01_script_gen/orchestrator/__init__.py` |
| `tools/studio_server.py` | 保留 | 0 | 1 | 0 | 4 | `tests/tools/test_studio_server.py` |
| `tools/tts_candidate.py` | 保留 | 3 | 2 | 0 | 0 | `shared/candidate_inputs.py` |
| `tools/verify_candidate_delivery.py` | 保留 | 0 | 1 | 0 | 0 | `tests/step01_script_gen/test_candidate_commit_real.py` |
| `tools/verify_enhancement_transaction.py` | 保留 | 1 | 2 | 0 | 0 | `tools/probe_isolation.py` |
| `tools/verify_frozen_candidate.py` | 保留 | 2 | 2 | 0 | 1 | `step01_script_gen/orchestrator/final_audit.py` |
| `tools/verify_merge_timeline.py` | 保留 | 2 | 1 | 0 | 0 | `step04_merge/main.py` |
| `tools/video_ab_experiment.py` | 保留 | 1 | 2 | 0 | 1 | `shared/quality_gate.py` |
| `tools/video_reroll.py` | 保留 | 1 | 5 | 0 | 1 | `tools/video_ab_experiment.py` |
| `tools/vision_check.py` | 保留 | 0 | 1 | 0 | 1 | `tests/tools/test_vision_check.py` |
| `tools/voice_unit_report.py` | 保留 | 1 | 0 | 0 | 4 | `shared/script_preflight.py` |
| `tools/workspace_size_audit.py` | 保留 | 0 | 1 | 0 | 5 | `tests/tools/test_workspace_size_audit.py` |

生成：`.tmp/slim/classify_tools.py`（一次性分析脚本，不入库）。
