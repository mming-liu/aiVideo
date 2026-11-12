# S01：本分支基线与责任表

> 本文件是本分支 `contract/step01-layered-execution` 上重新核对的基线快照，不沿用半完成态工作树的旧结论。
> 只做离线核对；未发起任何付费或媒体调用。S02 的合同接口与单测见
> [S02-execution-contract.md](S02-execution-contract.md)。
>
> **2026-11-12 更新**：master 已合并（HEAD `78ebbb1`）；S01 责任表仍有效。S02 的
> 语义检查器已实现并接入 Step02a 真实入口（见 `S02-execution-contract.md` 与
> `S03-execution-plan.md`）；S07 影子比较/切换清单见
> `S07-formal-gate-switch-checklist.md`。本文件第 1 节的 HEAD/摘要保留为**当时**
> 基线快照，不代表本轮最终 HEAD。

## 1. 基线身份（实算）

| 项 | 值 |
|---|---|
| 工作树 | `C:\myFloders\git\aiVideo-contract` |
| 分支 | `contract/step01-layered-execution` |
| HEAD | `7286516`（`72865169b999aa2387391e640c1709cbbc5340e0`） |
| 与 master 关系 | 本分支当时与 master 同点，`git log master..HEAD` 为空 |
| 工作树 | 基线干净；本包改动为新增文件 + `.gitignore` 放行块 |
| Python | `C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe`（3.14.2，pytest 9.1.1，tenacity 可用） |
| 生效时长带 | `min=60.0, max=90.0, target_min=70.0, target_max=80.0`（`shared.config.duration_band()`） |
| 生效范围 | `NOVEL_CHAPTER=1, BOOK_INDEX=1, BOOK_NAME=杀青州`（`.env.content`，进程环境同值） |

### 1.1 受控输入/配置摘要

| 路径 | 字节 | sha256 |
|---|---:|---|
| `.env.content` | 34878 | `db5a4a0ca4b47f85fba0a38e7fd098da054fe3544414e7f3ac18917baa5bbf49` |
| `.env.keys`（本地凭据，未入库） | 2163 | `79cce235b437842b76e7031e770cca185c8d673a9bf2f1f78dcb6bc5dc7afd40` |

`.env.content` / `.env.keys` 与隔离清单中的 master 摘要一致；本轮测试不读取凭据、不访问网络。
`.pytest_tmp/` 下的 U6/S06 证据是**本机过程数据**，只作历史线索，不是本包基线，也不被新增测试消费。

### 1.2 受保护 Step01 文件摘要（本轮未修改）

| 路径 | 字节 | sha256 |
|---|---:|---|
| `step01_script_gen/audit.py` | 79084 | `6496bfa9fe049c535f0b5649803bc5df92858712627d4db8364f6b2ad203b9ee` |
| `step01_script_gen/main.py` | 729189 | `ede7af79df3aee01df43e6b4eae2390027f2beb0c3249dfc61e20867f89592c6` |
| `step01_script_gen/validation.py` | 449332 | `0d48c3763113eb1ddaa9111b8b2d3375ba76acec4e43d8536580c14de47564cd` |
| `step01_script_gen/repair_fields.py` | 73182 | `a8eaf08f6784bb66bb373ad72eb82112f511e16c281b0d8e74cbb031a4b538bb` |
| `step01_script_gen/resume.py` | 17985 | `f545eea7ca55b771435199174fc6e0d5ab09a466b0494f35671977de23c7b3ea` |

`step01_script_gen/quality_issues.py`、`constraints/`、`revision_route.py`、`plan_preflight.py`、
`restricted_split.py`、`source_revision.py` 与 `shared/quality_gate.py`、`shared/config.py`
用于责任表取证；本轮只读，未修改。S02 新增的 `shared/execution_contract.py` 摘要见交付提交。

### 1.3 R3 离线核验读数

命令（指定 Python，离线，未发模型/媒体调用）：

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest `
  tests/step01_script_gen/test_revision_route.py `
  tests/step01_script_gen/test_core_update_guard.py `
  tests/step01_script_gen/test_source_revision.py `
  tests/step01_script_gen/test_restricted_split.py `
  tests/step01_script_gen/test_plan_preflight.py -q
```

读数：**67 passed**（`test_revision_route` 11、`test_core_update_guard` 3、
`test_source_revision` 41、`test_restricted_split` 10、`test_plan_preflight` 2）。
这只证明这些模块/夹具的离线判据通过，不等于真实运行或正式采纳。

## 2. 责任表：时长与连续性判据

记录格式：`诊断生产者 → 聚合方式 → 现行门禁 → 修订路由 → 目标责任阶段 → 验收案例`。

| # | 判据/诊断 | 诊断生产者 | 聚合方式 | 现行门禁/严重度 | 修订路由 | 目标责任阶段 | 验收案例 |
|---|---|---|---|---|---|---|---|
| D1 | 正片总时长低于 60s | `step01_script_gen/quality_issues.py::_duration_budget_issues`（`Issue("duration_budget","visual_risk")`） | `collect_structured_issues` 追加；`is_blocking` 判 `visual_risk` 为阻塞 | **Step01 提交门阻塞**：`_build_step01_quality_gate` 出 `status=BLOCK`，`_commit_step01_outputs` 的 `_q_blocking` 非空 | `revision_route.route_decision`：`duration_budget` 不在 carrier/source 族，走 `default`，`allowed_fields=None`；`repair_targets=None`、`repairable=False` | Step01 只保留语义与计划；实际声音/成片时长由 S05 声音准备与 S06 最终时间轴验收 | `temporal_ellipsis` 基线可读；变异不得靠下游补造内容；时长不足的差额进入 S05/S06 报告，不静默拉长镜头 |
| D2 | 正片总时长高于 90s | 同上 `_duration_budget_issues` | 同上 | **Step01 提交门阻塞**；`audit._AUDIT_RULES_TEMPLATE` 也明确正片超上限判 blocking | `revision_route` 走 `default`；该 Issue 对超上限给出 `repair_targets`（逐镜 `duration` 可压缩余量） | Step01 决定精简/合并；执行阶段只做已授权的时间安排 | 超上限样本的 `repair_targets` 只指向 `duration`，不得静默截断台词/动作 |
| D3 | 红果 60–90s 标准告警 | `step01_script_gen/validation.py::_check_redguo_standard`（总时长不足/超限写 `warnings`） | `collect_structured_issues` 读取 checklist：`redguo_standard` 不在 `_QUALITY_BLOCKING_CATEGORIES`，降为 `advisory` | **不阻塞**（辅助告警） | 不单独触发修订路由 | 保留为诊断/反馈，不与 D1/D2 双算为阻塞 | 同一时长样本只应有 D1/D2 一条阻塞权威；D3 只作提示 |
| D4 | W6 `runtime_extend` 的“总时长不足”HARD 违例 | `step01_script_gen/constraints/runtime_extend.py::RuntimeExtendConstraint.apply`（`Severity.HARD`） | `RepairReport.hard_unresolved`；`engine.run_hard_fix` 只打日志 | **不是提交门阻塞**（`hard_unresolved` 未被 `_build_step01_quality_gate`/`_commit_step01_outputs` 消费；生产 `regen_trigger` 未启用） | 不直接改路由；保留原值交 audit/regen | 诊断；由 S05/S06 以实测时长裁决 | 用同一短稿验证 D4 只报告、D1 才阻塞 |
| D5 | `duration_budget` 约束（超 90s / 单镜最低需求超 cap） | `step01_script_gen/constraints/duration_budget.py::DurationBudgetConstraint.apply` | `ctx.record_violation(HARD)` / `RepairRecord(action="detect")` | **不是提交门阻塞**（只报告，不写入；未直接映射到 quality 报告） | 约束链后交完整镜头事务/审计 | 诊断；Step01 内容决策 | 超限样本的 `RepairRecord` 只给候选镜，不夹取 |
| D6 | 最终语义审计的时长上限 | `step01_script_gen/audit.py::_AUDIT_RULES_TEMPLATE` | 审计模型返回 blocking 条目 → `_audit_blocking_records` | **审计 blocking**（只明确上限；下限未写成审计 blocking） | 审计修复轮；失败保留 blocking 交提交点 | Step01 语义审计；下限由 D1 负责 | 下限不足不应依赖审计提示；D1 必须独立成立 |
| D7 | 时间轴/派生时长一致性 | `step01_script_gen/timecode.py::_compute_timecodes`（含 `MIN_TOTAL_SHOTS_DURATION=60` 的 WARN） | `finalize_step01_script` → `project_for_commit` | 派生阶段 WARN；最终对象由 D1/D2 再判 | 时间写入后不再由链尾夹取（`runtime_extend` v6.76 只报告） | Step01 派生；S06 用真实媒体复核 | 同一稿的 `timeline` 与 `total_duration` 一致；下游不得把预算时长当成片时长 |
| D8 | 提交门/staged 自验 | `step01_script_gen/main.py::_commit_step01_outputs` + `shared/quality_gate.py::require_semantic_gate` | `quality.status` + `audit.status` + revision + duration_band | **唯一最终提交点**：BLOCK 时三份正式产物不替换 | 提交点只消费上述判定，不另造时长语义 | Step01 提交；下游 `require_step01_upstream` 只接受 COMMITTED + PASS | 构造 `duration_budget` 阻塞时 `step01_commit.json=BLOCK`，不得出现 `script_result.json` 已换而 quality 未换 |

### 2.1 “总时长不足”完整路径（重点）

1. 生效时长带来自 `shared/config.py::duration_band()`；`DURATION_MIN_SEC` 被代码固定为 60（不允许配置成其他下限）。
2. `timecode._compute_timecodes` 派生 `duration_sec`/`timeline`，并在 `shots_total < 60` 时打 WARN；`finalize_step01_script`/`project_for_commit` 负责最终对象。
3. `quality_issues._duration_budget_issues` 用**各镜 `duration_sec` 之和**重新计算：
   - 低于 60：`Issue(code="duration_budget", severity="visual_risk", shot_id=None, repair_targets=None, repairable=False)`；
   - 高于 90：同 code，按可压缩余量给出逐镜 `repair_targets`。
4. `collect_structured_issues` 追加该 Issue；`is_blocking` 把 `visual_risk` 视为阻塞。
5. `_build_step01_quality_gate` 产出 `status="BLOCK"`、`blocking_categories=["duration_budget"]`。
6. `_commit_step01_outputs` 在审计循环里把 `_q_blocking` 与审计 blocking 合并；无修复可做时到轮次上限后进入 BLOCK 分支，正式三份产物不替换，写 `step01_commit.json`。
7. `require_semantic_gate` 在 staged 自验和下游 `require_step01_upstream` 中再次要求 `status=PASS` / `blocking=false`。

**是否触发不相关的内容重写？** 对“总时长不足”这一条：
- `revision_route.route_decision` 走 `default`，不进入 carrier/source 专属修订；
- `repair_targets=None`，`_collect_repair_warnings` 把它归入 `"global"`，`_repair_shot_warnings` 的 `shot_items` 只收 `shot_*`/`hook`，因此**不会**因这一条发起整镜定向重写；
- 它仍会保留为 blocking，最终由提交点 BLOCK。这正是“诊断位置 ≠ 可执行修订位置”的现状：现行系统能阻止提交，但不能自动、合法地补内容。
- 目标责任划分：Step01 不靠拉长镜头/补无依据内容凑 60s；S05 用实际 TTS 时长、S06 用真实媒体时间轴报告差额，内容确需变化时再回 Step01。正式门禁语义变更属于 S07 GATE，本轮不切换。

**本轮实际执行的离线探针范围**：用构造的 9s 短稿调用
`quality_issues._duration_budget_issues`、`is_blocking` 与 `revision_route.route_decision`，
得到 `code=duration_budget`、`severity=visual_risk`、`blocking=True`、`repair_targets=None`、
`route=default`。**没有**构造完整 `_commit_step01_outputs` 提交（那需要完整稿件/审计输入）；
D8 的 commit manifest 读数是验收案例，不是本轮实测。

### 2.2 跨阶段责任/直接消费者映射

| 阶段 | 当前入口/产物 | 现行门禁/判据 | 本包目标责任 | 下一步消费者 | 验收案例 |
|---|---|---|---|---|---|
| Step01 语义 | `main._commit_step01_outputs`、`quality_issues`、`audit`、`chapter_outline` | 事实/承载/声音/时长/审计；唯一提交点写 `script_result.json`+`step01_quality.json`+`step01_audit.json` | 保留语义权威与唯一提交点；不把执行细化回流成整章改写 | S03–S06 读取语义合同；S07 候选链路 | 合法时间省略/画外声音保留；真正因果缺失回 Step01 |
| Step02a 画面 | `_plan_only_report`、帧描述、参考图与内容哈希 | 现有计划预检与图片 QC | 生成可保存、可消费的 `visual_plan.json`；真实请求消费同一份计划 | S04 视频请求、S06 合并 | `offscreen_voice` 基线通过；变异能定位“画外声音错误出镜” |
| Step02b 视频 | `FrozenVideoRequest`、`request_plan.py`、provider prepare | 请求身份/参考绑定/预检 | 扩展冻结请求的 `source`/`requirements`；片段到语义镜头的映射 | S06 最终时间轴 | 参考模式、声音模式、动作顺序在请求中可核 |
| Step03 声音 | `shared/audio_policy.py`、`shared/audio_identity.py`、`step03_tts_audio/main.py` | 声音单元/文本身份/候选 attempt | 增加实际时长反馈与前置准备；超窗口返回受影响单元/镜头 | S04/S06 执行预算 | 正文/说话人完整；TTS 超时长可定位 |
| Step04 合并 | `step04_merge/timeline.py`、`main.py` | 真实素材时长、转场、音轨、覆盖层 | 用真实媒体构建最终时间轴并验收；失败可定位 | S08 可播放样片 | 转场扣时、音频越界、字幕边界、缺轨分别有读数 |

以上是**责任映射**，不是“已接通”声明；S03–S07 的真实入口证据仍待取得。

### 2.3 非目标保护清单（S02 消费）

下游执行候选不得改写以下语义/声音字段；`validate_execution_changes` 以路径段命中即拒收，
`allowed_changes` 不能为它们授权：

- 事实与证据：`facts`、`fact_uids`、`required_facts`、`fact_bindings`、`hook_bindings`；
- 事件/因果/人物：`main_events`、`climax`、`causal_chain`、`end_hook`、
  `character_motives`、`shot_beats`、`events`、`event_order`、`order`、`characters`、
  `story`、`story_text`；
- 声音/播出文本：`speaker`、`speakers`、`voice`、`voice_mode`、`audio_mode`、
  `dialogue`、`dialogue_lines`、`narration`、`narration_lines`、`narration_segment`、
  `subtitle`、`spoken_text`，以及 `voice_units[*].text`。

允许的执行细化（仍须在 `allowed_changes` 与 `binding.changes` 留痕）：构图/画面提示
（`prompt`）、运镜实现（`camera_movement`）、参考绑定、模型参数、动作表达细节、保持映射的
片段拆分、声音/字幕/素材的合法时间安排（`actual_sec`、`window_sec`、`start/end` 等）。
需要改变事实、事件顺序、必要人物/事件、说话人、播出文本或声音模式时，反馈 Step01。

## 3. R3 证据映射

> 说明：仓库内没有单独命名为“R3 承诺清单”的文档；本表依据提交 `8850de0` 的改动、
> 模块 docstring 与测试文件名建立。**未找到原始 R3 逐条承诺文本**，因此“承诺项”是
> 按实现/测试反推的，不把反推结果冒充实测验收。

| R3 项（反推） | 实现位置 | 生产消费者/接线 | 离线证据 | 状态与未知项 |
|---|---|---|---|---|
| R3-1 修订路由与字段白名单 | `step01_script_gen/revision_route.py::route_decision` / `delta_field_violations` / `core_update_problems` | `step01_script_gen/outline.py::revise_plan_once` 使用 `route_decision`；`source_revision.apply_response` 使用 `core_update_problems` | `tests/step01_script_gen/test_revision_route.py` 11 passed；`test_core_update_guard.py` 3 passed | **生产接线存在**。未在本轮重跑真实修订调用；完整验收仍需真实入口证据 |
| R3-2 事实拆分/原子性 | `step01_script_gen/restricted_split.py`、`step01_script_gen/fact_atomicity.py` | `fact_atomicity.atomicity_problems` 被 `plan_preflight` 使用；`plan_preflight` 按 docstring **不接生产主门** | `test_restricted_split.py` 10 passed；`test_plan_preflight.py` 2 passed | **影子/离线**。不能据此说生产提交已强制原子性；事实拆分的生产授权由 `source_revision.v2` 承担，需另核真实入口 |
| R3-3 核验前规划预检 | `step01_script_gen/plan_preflight.py::preflight_problems` | 只被离线回放/实验运行器使用；无 `main.py` 生产调用 | `test_plan_preflight.py` 2 passed | **未接生产主门**（实现自述）。这是明确的未完成项，不是已验收能力 |
| R3-4 core 冻结与依赖原因放行 | `step01_script_gen/source_revision.py`（`source_revision.v2`）、`revision_route.core_update_problems` | `source_revision.apply_response` → `outline.revise_plan_once` 的来源修订路径 | `test_source_revision.py` 41 passed；`test_core_update_guard.py` 3 passed | **生产接线存在**；真实模型响应是否遵从仍需真实入口证据 |
| R3-6 测试环境修正 | `conftest.py`、`tests/conftest.py` | 测试基础设施 | 本轮 67 passed 在指定 Python 下运行 | 只证明本机/该环境；干净检出需按既有 `tests/` 放行清单复核 |

**R3 未知/缺口**：
- 原始 R3 承诺文本、验收金标准与逐项责任人未在仓库内找到；本表不能替代原承诺核对。
- `plan_preflight` / `fact_atomicity` 仍是影子路径；若 S03–S06 依赖它们作为生产门，必须先补接线与真实入口证据。
- 本轮没有真实模型/媒体调用，不能证明模型对 `source_revision.v2` 或审计规则的遵从。

## 4. 三个样本族与实算摘要

夹具目录：`tests/shared/fixtures/execution_contract/`。
每个文件含 `baseline`（合法）与 `mutation`（有因果关系变异）两份完整稿件、派生执行候选及 `expected`。
内容、实体数量、镜头编号与声音分布均不同（人物数 3/2/2；镜头号 1–3、2/5/8、3–4；
声音单元数 0/2/3）；不依赖 `.tmp`、生产 `output`、现场日志或网络。

| 样本族 | 文件 | 字节 | sha256 | scope | 合法基线 | 变异与预期 |
|---|---|---:|---|---|---|---|
| 时间跳跃/过程省略/因果缺失 | `temporal_ellipsis.json` | 12774 | `d67557b76398cb0b6bf9a576ca941021bad2e58b6ad99c029da4a0439f32c9bc` | ch2 / book1 / 匿名样本A | 三日后取船；打磨、填缝、上漆的常规工序以蒙太奇省略；结果有动作与时间依据 | 删除修补动作与“三日后”标记，只保留“船已修好”；预期 `causal_gap`、`time_ellipsis_lost` |
| 画外声音/听者反应/可见性 | `offscreen_voice.json` | 16369 | `e6caaa9d7fee8007f095524080e7c18f9cfb0630a45a8415113fcc8b90d06d7e` | ch5 / book1 / 匿名样本B | 门外来客只在画外发声；阿澄转头/凝视回应；铜灯、信封有布局条目 | 把来客画进 ff/lf 可见布局并移除阿澄转头反应；预期 `offscreen_speaker_visible`、`listener_reaction_missing` |
| 独立 TTS/原生对白/旁白时长 | `voice_timing.json` | 12943 | `4d19b0a8558a9c52a0cf1542c4e8f722c821090cdb97c7457b6bc7850f74bee3` | ch7 / book1 / 匿名样本C | 原生对白已核实；独立 TTS 1.8s + 旁白 1.2s 落在 4.0s 窗口；说话人/正文绑定 | TTS 3.4s + 旁白 1.2s 超 4.0s 窗口；原生对白 `verified=false`；预期 `audio_window_overflow`、`native_dialogue_unverified` |

`manifest.json` 记录上述摘要、字节数与预期读数；`test_fixture_manifest_digests_match_file_bytes`
会重新计算文件字节 sha256，防止摘要漂移。夹具的 `script_revision` 是为绑定测试准备的固定身份，
不是生产 `compute_script_revision` 的产物；真实稿件身份仍以生产入口计算为准。

### 4.1 样本自足性

- 只读夹具文件 + 纯函数模块；无网络、无模型、无媒体、无凭据。
- 不读取 `.pytest_tmp`、`output/`、日志或“最新目录”；摘要固定。
- 每个测试在 pytest 提供的临时根内运行；夹具本身只读。
- 运行命令与读数见 [S02-execution-contract.md](S02-execution-contract.md#3-离线单测命令与读数)。

## 5. 未验证项与下一步

**未验证（本轮明确不做）**：
- 任何真实 Step01/02a/02b/03/04 模型或媒体调用；本轮全部离线。
- `plan_preflight` / `fact_atomicity` 的生产接线与真实入口行为。
- 正式门禁语义切换、正式产物采纳、S07 GATE 裁决（只产出切换清单）。
- ~~三个样本族的语义检查器尚未实现~~：**2026-11-12 已补齐**，见
  `shared/execution_semantics.py` 与 `tests/shared/test_execution_semantics.py`
  （11 passed；基线过、变异六个 code 自动检出）。
- 真实媒体夹具与可播放样片：S06 离线样片已产出（本机 ffmpeg），真实模型成品未运行。

**下一步**：
1. S03 消费 S02 的绑定/变化接口，把 `visual_plan.json` 作为候选产物；用 `offscreen_voice` 族做“画外声音错误出镜”入口反例。
2. S05/S06 用 `voice_timing` 族把实际 TTS 时长与窗口冲突、原生对轨核验接入执行预算/时间轴。
3. S07 在 master 合并后接通候选链路，逐项记录原 Step01 阻断在下游的接收与终局；正式门禁切换仍需逐项 GATE。
4. 若要证明 R3 的生产行为，补真实入口或冻结回放证据；不要用本轮 67 passed 代替。
