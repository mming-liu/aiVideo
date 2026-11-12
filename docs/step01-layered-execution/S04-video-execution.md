# S04：视频执行请求与反馈路由

> 离线可完成项：冻结请求扩展、plan-only 与实际 prepare 共用要求、片段映射、
> 四类反馈路由、桩响应贯通。未发起视频生成；每次付费调用仍需人工确认。

## 1. 真实入口与数据

| 项 | 位置 |
|---|---|
| 执行要求构建 | `step02_video_gen/execution_requirements.py::build_requirements` |
| 冻结请求合并 | `merge_into_frozen`（写 `requirements.execution` + `source.semantic_shot/visual_plan_digest/audio_digest`，重算 `request_key`） |
| 实际 prepare | `step02_video_gen/submit_poll.py::_submit_video_candidate`（merge + `validate_requirements`，失败零 POST） |
| plan-only | `step02b_video_gen/main.py::build_plan` 使用同一 `build_requirements`，entry 记录 `execution_requirements` |
| 缓存/发送前 | `_process_one_shot_skip_frames` 读取 `visual_plan.json` 后构建同一份要求，缓存键包含语义事实 |
| 反馈路由 | `classify_failure` 四类：`video_execution_prepare` / `candidate_failed` / `step01` / `task_resume` |

`requirements.execution` 含：`semantic_shot`（shot_id、起止状态、必要事件、动作顺序、
计划时长）、`production_fragments`（片段→语义镜头映射、事件覆盖）、`references`
（模式、实际使用素材 role/sha256、能力未声明）、`audio`（策略、需保留声音身份、
原生对白核验要求）、`duration`（计划/模型区间/未验证）、`visual_plan`（摘要/帧 id/
可见人物/画外声音）、`audio_artifacts`（由需保留声音身份计算 summary digest 与
unit 列表，不再留空槽位）。

## 2. 反馈路由

| 失败 code 类 | 路由 | 动作 |
|---|---|---|
| `role_unsupported` / `reference_capacity` / `bad_duration` / 能力未准备 | `video_execution_prepare` | 调参数/换 provider，不静默降级 |
| `action_missing` / `event_order_mismatch` / `fragment_event_loss` / `reference_identity_mismatch` | `candidate_failed` | 候选失败并附证据，重试候选 |
| `causal_gap` / `time_ellipsis_lost` / `offscreen_speaker_visible` / `structural_infeasible` | `step01` | 回 Step01 修订语义，不删内容凑 |
| `submission_unknown` / `timeout` / `download_failed` / `poll_failed` | `task_resume` | 沿用同 task_id 恢复，不重新生成 |

未知 code 默认 `video_execution_prepare` 且要求先分类，不静默重试。

## 3. 自动反例与桩贯通

`tests/step02_video_gen/test_execution_requirements.py`：

- `test_fragment_mapping_detects_event_loss_and_order`：片段丢失必要事件 →
  `fragment_event_loss`；顺序反了 → `event_order_mismatch`；
- `test_failure_routing_is_four_way`：四类 code 路由与 `resume_same_task`；
- `test_stub_response_round_trip_preserves_execution_facts`：真实
  `_submit_video_candidate` + 桩 provider（本机 ffmpeg 造可解码 mp4）→ 1 次 POST →
  保存冻结执行事实 → accepted → 重启恢复（0 次新 POST）→ `resolve_candidate_inputs`
  按 accepted/unit_id 消费。

## 4. 命令与读数

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest `
  tests/step02_video_gen/test_execution_requirements.py -q `
  --basetemp=.pytest_tmp/s04_module -p no:cacheprovider
```

读数：**5 passed**（本模块新增文件）。

模块级回归（仅作旁证）：`tests/step02b_video_gen tests/step02_video_gen` =
44 passed / 4 skipped / 2 failed；2 failed 均为本机 safe-delete shim 的 `EPERM`
（`post_budget.reset` / attempt 锁清理），不是本包行为。

## 5. 未验证项

- 未发起真实视频生成；未验证真实 provider 对 requirements/参考角色/声音策略的能力。
- 真实模型是否满足动作/起止状态，仍需产物 QC；离线只核对请求事实与显式观测。
- 桩 provider 只在测试/离线工具中，未注册生产 provider。
