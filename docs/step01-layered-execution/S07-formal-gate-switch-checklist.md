# S07：正式门禁切换清单（只产出清单，不执行切换）

> 本文件是**评审清单**，不是切换动作。Step01 的提交门禁语义（`quality_issues` /
> `audit` / `_commit_step01_outputs`）未改，未采纳任何正式产物，未执行 GATE 裁决。
> 正式切换必须由用户另行裁决；本分支只提供逐项依据。
>
> **诚实边界**：为满足 S03/S04/S06 的「发送前验证 / 完成校验后才合成」，本分支在
> Step02a、Step02b、Step04 的**生产入口**新增了执行级拒收（见第 5 节）。这些是
> 尚未经 GATE 裁决的行为变化，合并到 master 前必须逐项确认，不能按「未改门禁」略过。
>
> 影子比较读数：`.pytest_tmp/s07c/shadow_compare.json`（同一冻结夹具输入；基线与
> 变异对照见同目录 `shadow_compare.md`）。现行严重度来自
> [S01 责任表](S01-baseline-and-responsibility.md)，不是本包重新定义的。

## 1. 逐项清单

| # | 判据/阻断 | 现行严重度与责任 | 建议责任变更 | 合法正例 | 真实缺陷反例（自动检出） | 下游接收证据 | 切换前必须人工确认 |
|---|---|---|---|---|---|---|---|
| D1 | 正片总时长 < 60s（`duration_budget`） | **BLOCK**，Step01 提交门 | Step01 不再以“补足 60s”阻塞语义稿；差额交 S06 时间轴验收，内容确需变化才回 Step01 | `temporal_ellipsis` 基线不靠拉长镜头/补造内容通过语义检查 | S06 `timeline_duration_short` 报 `delta_sec=-2.0` 与受影响 clip（`tests/step04_merge/test_timeline_acceptance_s06.py`） | `step04_merge.main` 合成前 `build_timeline_acceptance`；`tests/step04_merge` 20 passed | 是：会改变 Step01 提交门严重度 |
| D2 | 正片总时长 > 90s | **BLOCK**，Step01 提交门 | 同 D1；超长只报差额与受影响位置，不静默截断 | 合法长稿由 Step01 决定精简/合并 | S06 `timeline_duration_long` 报差额与受影响 clip | 同上 | 是 |
| D3 | 红果 60–90s 标准告警 | advisory | 保留为 S06 诊断，不与 D1/D2 双算 | 同一时长只一条阻塞权威 | 现行测试仍只把 `redguo_standard` 当 advisory | `step01_script_gen/validation.py` 现状未改 | 否（只保留） |
| S-01 | 结果缺因果/过程（`causal_gap`） | audit blocking（Step01 语义审计） | S03 画面计划预检拒收，附 `route=step01` 反馈 | `temporal_ellipsis` 基线 `findings=[]` | 变异自动检出 `causal_gap` + `time_ellipsis_lost`；`visual_plan.status=blocked` | `step02a_frame_gen/execution_plan.py::build_visual_plan` → `attach_semantic_checks` → `unresolved/feedback` | 是：Step01 审计仍是语义权威 |
| S-02 | 时间省略丢失（`time_ellipsis_lost`） | audit blocking | 同 S-01 | 基线有“三日后”与过程载体 | 变异同上；`S02` 测试逐 code 隔离检测 | `tests/shared/test_execution_semantics.py` | 是 |
| S-03 | 画外声音错误出镜（`offscreen_speaker_visible`） | **现行 Step01 无执行级检查**（not_detected） | S03 执行前检查 + 受验证字段保护 | `offscreen_voice` 基线 `findings=[]`，画外说话人不在可见布局 | 变异 `on_screen=true` + 布局出镜被检出；`prompt`/`visual_plan.visible_characters` 无通过记录不放行 | `GUARDED_SEGMENTS` + `semantic_change_unverified`（`tests/shared/test_execution_contract.py`） | 是：新增执行级门禁 |
| S-04 | 听者反应缺失（`listener_reaction_missing`） | **现行 Step01 无执行级检查** | S03 执行前检查 | 基线 ff→lf 布局变化/动作反应 | 变异移除反应后自动检出 | `shared/execution_semantics.py::_check_listener_reactions` | 是 |
| S-05 | 独立声音超窗口（`audio_window_overflow`） | **现行 Step01 无实测** | S05 实测 + S06 验收；返回 unit/shot/差额 | `voice_timing` 基线 1.8+1.2 ≤ 4.0 | 变异 3.4+1.2=4.6 > 4.0，报 `unit_id=d02`、`shot_index=4`、`overflow_sec` | `shared/voice_preparation.py`；`tests/step03_tts_audio/test_voice_preparation.py` | 是 |
| S-06 | 原生对白未核实（`native_dialogue_unverified`） | **现行 Step01 无实测** | S05 请求准备绑定文本/说话人，视频返回后核验；未核实只记未验证 | 基线 `verified=true` + 实测时长 | 变异 `verified=false` 自动检出 | `build_voice_preparation` / `native_dialogue_unverified` | 是 |

## 2. 责任变更的边界

- **不执行切换**：以上“建议责任变更”只在隔离执行链/影子路径生效；Step01 的
  `quality_issues` / `audit` / `_commit_step01_outputs` 未改。
- **现行时长目标仍从配置事实源读取**：`shared/config.py::duration_band()`；
  本包没有把所有不足改成 warning，也没有预设新的时长阈值。
- **合法正例与真实缺陷分开**：基线只证明语义/执行检查不误拒；变异只证明检查器
  可自动区分。两者都不证明真实模型产出质量。
- **旧产物**：没有 `execution_binding.v1` / 新 requirements 字段的旧产物按旧入口
  规则处理，不得据此认定已通过新验收（见 `S03-execution-plan.md` 与
  `S05-voice-preparation.md`）。

## 3. 下游接收与终局（影子比较）

同一冻结夹具输入的三族结果（`shadow_compare.json`）：

| 样本族 | 基线语义 codes | 变异语义 codes | 变异终局 |
|---|---|---|---|
| `temporal_ellipsis` | `[]` | `causal_gap`, `time_ellipsis_lost` | S03 `visual_plan=blocked`，feedback `route=step01` |
| `offscreen_voice` | `[]` | `offscreen_speaker_visible`, `listener_reaction_missing` | S03 `visual_plan=blocked`，`semantic_change_unverified` |
| `voice_timing` | `[]` | `audio_window_overflow`, `native_dialogue_unverified` | S05 `voice_preparation=blocked`，unit/shot 定位 |

## 4. 未执行项

- 未做真实 Step01/02a/02b/03/04 模型或媒体调用；未采集真实收益。
- Step01 正式门禁未改、未执行 GATE 裁决、未采纳正式产物。
- D1/D2 的“Step01 不再阻塞”只是清单建议；切换需要用户裁决和正式回归。

## 5. 合并前必须由 GATE 裁决的执行入口拒收（本分支新增）

以下三处**不是** Step01 门禁变更，但会改变生产入口的实际行为：合并到 master 后，
原本能继续的调用可能因新的执行检查被拒收。按 KICKOFF 的边界，它们只应在隔离
执行链/候选路径强制，或由用户显式开启；**本轮未做开关化，因此列为待裁决项**。

| 位置 | 新增行为 | 触发条件 | 影响 | 建议裁决 |
|---|---|---|---|---|
| `step02a_frame_gen/main.py`（`generate` 真实入口、`main()`、plan-only 报告） | `validate_plan_identity` 失败即 `raise SystemExit(2)`，拒绝发送生成 | `scope` 存在且计划/输入身份重验有问题 | 缺少或过期 `visual_plan.json` 的既有生产流程会被拒 | 限定为候选路径，或加显式开关；确认旧入口兼容规则 |
| `step02_video_gen/submit_poll.py::_submit_video_candidate` | 合并 `execution_requirements` 后 `validate_requirements` 有问题即记录 `execution_requirements_blocked` 并返回失败，不发 POST | `production_output=True` 时也生效 | 正式视频提交可能因新要求（首尾状态/事件/声音）被拒 | 同上；需逐项确认哪些要求允许在正式路径阻塞 |
| `step04_merge/main.py::_run_merge` | `build_timeline_acceptance` 非 pass → `RenderError(timeline_acceptance_blocked)`；合成后 `verify_rendered_output` 非 pass → `RenderError(rendered_timeline_mismatch)` | **无条件**，正式与候选路径都生效 | 正式合成会被新的时长/音轨/字幕验收阻断；与 D1/D2 的建议严重度尚未对齐 | 合并前必须裁决：默认只告警+记录，或候选路径才阻塞；不得随代码合并静默生效 |

会话实现时的判断是「S06 合成前验收属新执行合同，不是 Step01 门禁」，故保留了阻塞。
该解释与 KICKOFF「不擅自变更正式门禁语义」存在解释空间，**交由用户裁决**，不在本包内
擅自开关化。本节列出事实与位置，避免合并评审时误认为生产行为未变。
