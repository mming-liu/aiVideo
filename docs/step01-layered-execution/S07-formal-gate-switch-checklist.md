# S07：责任与门禁现状（合并后统一生效）

> **用户裁决 2026-11-12**：合并后的代码统一覆盖原有 Step01 结果；系统只有**一套行为**，
> 不区分「生产 / 离线」「正式 / 候选」两套准入语义，也不为旧路径保留开关或回退分支。
> 因此本文件不再是「待裁决清单」，而是合并后**实际生效的责任表与依据索引**。
>
> 上一版（`bc5c50e`）的判定结果、源码与证据已保留为只读快照
> [`baselines/2026-11-12-pre-layered/`](../../baselines/2026-11-12-pre-layered/README.md)，
> 用于比对修复效果变好还是变差；该快照不参与运行，不是第二套实现。
>
> 逐项读数来自同一冻结夹具输入：`artifacts/s08/shadow/shadow_compare.json`
> （历史文件名保留 `shadow` 字样，指当时的对照实验，不代表现在存在影子准入路径）。
> 现行严重度来自 [S01 责任表](S01-baseline-and-responsibility.md)。

## 1. 逐项责任表（合并后实际状态）

| # | 判据/阻断 | 承担方与严重度 | 合法正例 | 真实缺陷反例（自动检出） | 接收证据 | 状态 |
|---|---|---|---|---|---|---|
| D1 | 正片总时长 < 60s（`duration_budget`） | Step01 提交门 **BLOCK**（沿用 `shared/config.py::duration_band()` 配置事实源，未改写）；S06 时间轴验收同时报出差额与受影响 clip | `temporal_ellipsis` 基线不靠拉长镜头/补造内容通过语义检查 | S06 `timeline_duration_short` 报 `delta_sec=-2.0` 与受影响 clip（`tests/step04_merge/test_timeline_acceptance_s06.py`） | `step04_merge.main` 合成前 `build_timeline_acceptance`；`tests/step04_merge` 20 passed | 已生效 |
| D2 | 正片总时长 > 90s | 同 D1；超长只报差额与受影响位置，不静默截断 | 合法长稿由 Step01 决定精简/合并 | S06 `timeline_duration_long` 报差额与受影响 clip | 同上 | 已生效 |
| D3 | 红果 60–90s 标准告警 | advisory，保留为 S06 诊断，不与 D1/D2 双算 | 同一时长只一条阻塞权威 | 现行测试仍只把 `redguo_standard` 当 advisory | `step01_script_gen/validation.py` 现状未改 | 已生效 |
| S-01 | 结果缺因果/过程（`causal_gap`） | Step01 语义审计 + S03 画面计划预检拒收，附 `route=step01` 反馈 | `temporal_ellipsis` 基线 `findings=[]` | 变异自动检出 `causal_gap` + `time_ellipsis_lost`；`visual_plan.status=blocked` | `step02a_frame_gen/execution_plan.py::build_visual_plan` → `attach_semantic_checks` → `unresolved/feedback` | 已生效 |
| S-02 | 时间省略丢失（`time_ellipsis_lost`） | 同 S-01 | 基线有「三日后」与过程载体 | 变异同上；`tests/shared/test_execution_semantics.py` 逐 code 隔离检测 | 同上 | 已生效 |
| S-03 | 画外声音错误出镜（`offscreen_speaker_visible`） | S03 执行前检查 + 受验证字段保护 | `offscreen_voice` 基线 `findings=[]`，画外说话人不在可见布局 | 变异 `on_screen=true` + 布局出镜被检出；`prompt`/`visual_plan.visible_characters` 无通过记录不放行 | `GUARDED_SEGMENTS` + `semantic_change_unverified`（`tests/shared/test_execution_contract.py`） | 已生效 |
| S-04 | 听者反应缺失（`listener_reaction_missing`） | S03 执行前检查 | 基线 ff→lf 布局变化/动作反应 | 变异移除反应后自动检出 | `shared/execution_semantics.py::_check_listener_reactions` | 已生效 |
| S-05 | 独立声音超窗口（`audio_window_overflow`） | S05 实测 + S06 验收；返回 unit/shot/差额 | `voice_timing` 基线 1.8+1.2 ≤ 4.0 | 变异 3.4+1.2=4.6 > 4.0，报 `unit_id=d02`、`shot_index=4`、`overflow_sec` | `shared/voice_preparation.py`；`tests/step03_tts_audio/test_voice_preparation.py` | 已生效 |
| S-06 | 原生对白未核实（`native_dialogue_unverified`） | S05 请求准备绑定文本/说话人，视频返回后核验；未核实只记未验证 | 基线 `verified=true` + 实测时长 | 变异 `verified=false` 自动检出 | `build_voice_preparation` / `native_dialogue_unverified` | 已生效 |

## 2. 边界（单一行为）

- **一套代码、一套准入**：Step01、Step02a、Step02b、Step03、Step04 都按同一份执行合同判断，
  不存在「隔离执行链才强制 / 正式路径另算」的分叉，也没有需要用户显式开启的开关。
- **时长目标仍从配置事实源读取**：`shared/config.py::duration_band()`；未预设「所有不足
  一律改为 warning」。D1/D2 的 Step01 严重度沿用现行值，S06 负责报出实测差额。
- **合法正例与真实缺陷分开**：基线只证明语义/执行检查不误拒；变异只证明检查器
  可自动区分。两者都不证明真实模型产出质量。
- **旧产物**：没有 `execution_binding.v1` / 新 requirements 字段的旧产物按旧入口规则处理，
  不得据此认定已通过新验收（见 `S03-execution-plan.md` 与 `S05-voice-preparation.md`）。
  这是数据兼容，不是第二套准入语义。

## 3. 同一冻结输入的逐项接收与终局

| 样本族 | 基线语义 codes | 变异语义 codes | 变异终局 |
|---|---|---|---|
| `temporal_ellipsis` | `[]` | `causal_gap`, `time_ellipsis_lost` | S03 `visual_plan=blocked`，feedback `route=step01` |
| `offscreen_voice` | `[]` | `offscreen_speaker_visible`, `listener_reaction_missing` | S03 `visual_plan=blocked`，`semantic_change_unverified` |
| `voice_timing` | `[]` | `audio_window_overflow`, `native_dialogue_unverified` | S05 `voice_preparation=blocked`，unit/shot 定位 |

## 4. 本次合并未改变的事项（如实列出）

- 未做真实 Step01/02a/02b/03/04 模型或媒体调用；真实内容质量收益仍未验证。
- **Step01 提交门的严重度未改写**：时长 BLOCK 沿用配置事实源；`quality_issues` /
  `audit` / `_commit_step01_outputs` 的判定逻辑没有随本包改动。改变它是另一类决策
  （准入语义变更），不是「去掉生产/离线概念」，需单独提出才做。
- **遗留标记**：`step01_script_gen/audit_adjudication.py` 仍写 `gate_effect=shadow_only`
  （确定性发现只进报告、不改 severity/blocking）。这是上一版 `bc5c50e` 的设计，
  本包未改。若也要统一为生效判定，需要单独评估对 Step01 回归的影响，另行处理。
- 未采纳任何正式产物；本包产物为候选计划、报告与可播放离线样片。

## 5. 执行入口拒收（已随合并生效，不做开关化）

三处拒收是本包为满足「发送前验证 / 完成校验后才合成」而建立的**统一行为**，
对所有调用一视同仁，不区分候选或正式、不设环境变量开关：

| 位置 | 生效行为 | 触发条件 |
|---|---|---|
| `step02a_frame_gen/main.py`（`generate` 真实入口、`main()`、plan-only 报告） | `validate_plan_identity` 失败即 `raise SystemExit(2)`，拒绝发送生成 | `scope` 存在且画面计划/输入身份重验有问题 |
| `step02_video_gen/submit_poll.py::_submit_video_candidate` | 合并 `execution_requirements` 后 `validate_requirements` 有问题即记录 `execution_requirements_blocked` 并返回失败，不发 POST | 执行要求（首尾状态/事件/声音/参考）未满足，所有提交路径一致 |
| `step04_merge/main.py::_run_merge` | `build_timeline_acceptance` 非 pass → `RenderError(timeline_acceptance_blocked)`；合成后 `verify_rendered_output` 非 pass → `RenderError(rendered_timeline_mismatch)` | 时间轴验收或成片复核未通过，无条件生效 |

上一版结果的比对入口见 `baselines/2026-11-12-pre-layered/compare.py`：
`--verify` 校验快照完整性、`--old/--new` 比对判定差异、`--fixtures` 重跑三族夹具、
`--regression` 对比回归读数。判读规则见该目录 README，工具不自动下「变好/变差」结论。
