# M03 有界反馈闭环 —— 交付记录（2026-11-15）

状态：**离线完成**。真实模型调用 0，正式产物写入 0，未 push，未重签、未采纳。
基线提交 `efa5f3f`；本文件与 M01/M02/M03 的生产改动在**同一份未提交工作树**里。
`[GATE] 用户确认固定输入验证后解锁真实调用` 仍未解锁。

## 1. 本模块回答的问题

施工包 M03 要求：复用 `retry_state`，在**替换拒收**与**事务拒收**两处统一提供结构化
结果，字段为 `target / phase / code / field_path / allowed_paths / retryable / details`；
按生产者分类处理，文本只用于展示；每个冻结目标集合最多一次答卷纠正机会；并用真实
`_repair_shot_warnings` 入口证明两轮链路。

对应三层成功判据：本模块只交付**离线能力**（合法补丁经真实事务入口保留、越权补丁
拒收、反馈准确进入下一次请求）。模型遵从与产品结果留待 GATE 之后的有限真实验证，
本文件不作任何推断。

## 2. 代码改动

| 文件 | 改动 |
| --- | --- |
| `step01_script_gen/repair_feedback.py`（新增） | `feedback_record` 规范化七字段；`POLICY` 策略表；`render_feedback` 只做展示；`frozen_signature` / `answer_signature` 稳定指纹；`plan_feedback_state` 发放一次纠正机会；`transaction_feedback_records` 把裁决生产者结果转成结构化记录 |
| `step01_script_gen/main.py` | ① `_apply_shot_replacements` 新增 `rejections_out`，所有拒收改走 `_reject(code, text, ...)`，code 由调用点与结构化预检给出；② `_repair_shot_warnings` 入口读结构化状态、校验 `source_revision`、工程故障在发请求之前终局；③ 替换拒收与事务拒收两处统一写 `retry_state`；④ 反馈写回**同一条事务诊断记录**（不新增带 `repair_round` 的记录）；⑤ `_no_gain` 记真实残余；⑥ 提交层按 `correction_granted` 续跑，另留一条 v6.137 遗留格式的窄兼容通道 |
| `tests/step01_script_gen/test_repair_feedback.py`（新增） | 41 项单元验收：七字段契约、未知 code 的 fail-closed、文本不参与分类、策略表逐条、稳定签名、一次机会、重复拒因、混合拒因、事务 reason 映射、真实应用器生产者分类 |
| `tests/step01_script_gen/test_kf_frame_identity_transaction.py` | 夹具增加请求文本捕获与跨调用答卷序列；追加 4 个 M03 真实入口用例 |
| `tests/step01_script_gen/test_repair_call_outcome.py` | 混合拒因用例按 M03 新契约更新断言（见 §5） |
| `.gitignore` | 放行 `test_repair_feedback.py` |

### 分类不读文本

`_apply_scoped_field_updates` / `_validate_replacement_shot` 返回的原因串只进 `text`。
`field_path` 取自答卷的 `updates` / `keyframe_additions` 结构化数据，`allowed_paths` 取自
本轮 `_allowed_by_target`。`_validate_replacement_shot` 的 code 由三项结构化事实判定：
帧数与布局数不等 → `pair_shape_invalid`；授权 diff 有越权项 → `protected_field`；其余 →
`shape_invalid`。未知 code 一律 `engineering_failure`（fail-closed）。

### 策略表落点

| 生产者结果 | code | retryable | terminal |
| --- | --- | --- | --- |
| 未授权父路径 / 通配路径 | `unauthorized_path` | 是 | 否 |
| 配对形状 / 装配缺半 | `pair_shape_invalid` | 是 | 否 |
| 整表替换来源无法证明 | `frame_identity_unproven` | 是 | 否 |
| 新增阻塞 | `new_blocking` | 是 | 否 |
| 保护字段越权 | `protected_field` | 否 | 否（重验继续完整执行） |
| 合法应用但目标未解决 | `no_target_resolved` | 否 | 否（返回真实残余） |
| checker / writer 观测失败 / 映射内部失配 | `engineering_failure` | 否 | **是**（优先阻断） |
| 相同拒因 + 同一补丁 | `duplicate_rejection` | 否 | **是** |
| 一次机会已用完 | — | 否 | **是** |

§14 P3 的四类效果抵消归因（`postprocess_effect_reverted` / `actor_presence_conflict` /
`actor_binding_ambiguous` / `repair_capability_missing`）映射到「真实残余」，**不**按工程
故障终局；冲突本身仍由 `layout_conflict` 通道进入下一次请求。

## 3. 真实入口两轮链路（零模型、零网络）

`test_m03_two_call_feedback_closed_loop` 用同一份冻结稿连跑两次真实
`_repair_shot_warnings`，只打桩 LLM 响应 / 检查器 / writer / 日志：

1. 首次答卷越权改 `subtitle` → 应用器拒收 → `rejected_by_validation` →
   `compute_script_revision(outputs[0]) == compute_script_revision(BASE)`，基线逐字保留；
   `retry_state.feedback_records[0]` = `unauthorized_path` / `shot_5` / `subtitle`，
   `allowed_paths` 非空，`source_revision` 等于冻结稿 revision。
2. 第二次**实际请求文本**含「上一轮修复反馈（结构化」、`unauthorized_path` 与
   `subtitle`；首次请求不含反馈。反馈绑定的 revision 与第二次进入的冻结稿一致。
3. 第二次答卷 `keyframe_additions` 合法中间补帧 → 应用 → 后处理四节点 → 重验 →
   `accepted=true`、`resolved>=1`、`frame_locator_integrity=[]`、
   `frame_mappings.shot_5.source=assembly`；最终返回稿 3 帧 / 3 布局且 revision 不同于基线。

其余三个真实入口用例：

* 相同补丁 + 相同拒因 → `stop_reason=duplicate_rejection`，第三次调用**在发请求之前**
  返回，`calls["n"]` 不增加，`feedback_terminal/sealed_before_send=true`。
* writer 观测异常 → `engineering_failure` 终局，记录落在同一条事务诊断里，第二次不发请求。
* `source_revision` 与冻结稿不符 → 丢弃跨稿反馈，请求文本不含上一轮反馈，不借用旧裁决。

## 4. 回归读数与同条件基线对照

| 项目 | 读数 |
| --- | --- |
| M03 单元（`test_repair_feedback.py`） | **41 passed** |
| M03 真实入口 4 项 | **4 passed** |
| M01+M02+M03+帧身份组合 | **123 passed** |
| 修复轮相关 6 文件聚焦回归 | **175 passed** |
| `tests/step01_script_gen/` 全目录执行 | **14 failed / 4457 passed / 294 skipped / 4 xfailed** |
| 全量 `pytest --collect-only -q tests/` | **7628 tests collected**（只收集，不替代执行结果） |
| 真实模型调用 / 正式产物写入 | **0 / 0** |

### 14 个 `test_plan_phase_*` 失败：同条件基线对照

用户要求「缺少同条件基线对照时，不仅凭 diff 无依赖判断为既有失败」。本次在
`.dsh/m03_baseline` 建立 `git worktree add --detach ... efa5f3f`（HEAD 基线，无
M01/M02/M03 生产改动），把当前 `tests/` 同步进去，用**同一条 pytest 命令**跑同 5 个
`test_plan_phase_*` 文件：

| 环境 | 结果 |
| --- | --- |
| HEAD 基线检出 | `14 failed, 105 passed` |
| 当前 M03 工作树 | `14 failed, 105 passed` |

失败 ID 逐条一致（`d02`×2、`d05`、`g07/g08/g09/g13/g14`、`h05/h06/h07`、
`j04/j05`、`k05`），集中在 `outline.py` 计划阶段的能力登记与首版接线，与修复轮无关。
**结论：这 14 个是既有失败，不是本包引入。** 临时 worktree 已 `git worktree remove`
清理；`.dsh/` 历史现场、历史日志均未改动。

### 过程中发现并收口的 11 个回归

第一次全目录执行曾出现 25 failed（14 既有 + 11 新增）。11 个全部定位为 M03 引入并修好，
未归为既有：

1. `excluded` 误把「剩余待修目标」当肇事目标（应为 `_offend_raw`）→ 加 `excluded_override`。
2. `continue` 被绑在 `retry_state is not None` 上，掐掉 GAP 路径的内层重试 → 状态裁决与跨调用写入解耦。
3. 效果抵消类 reason 被 fail-closed 成工程故障，挡住 §14 P3 的第二次请求 → 补映射表。
4. 追加带 `repair_round` 的诊断记录把 `rounds[-1]` 事务记录顶掉 → 改为写回同一条。
5. 提交层只认 `correction_granted`，v6.137 遗留桩不再续跑 → 窄兼容通道（仅在完全没有
   `feedback_records` 时生效，生产路径不经过）。
6. 旧文本契约「上一轮补丁被拒 / 已从本轮移除 / 带反馈重试」在展示层补齐。
7. 混合拒因的旧「一律停止」语义按 M03 施工包改为逐项处理（见 §5）。

修好后全目录从 `25 failed / 4446 passed` 变为 `14 failed / 4457 passed`，总用例数
4769 前后完全一致（11 个从 failed 转为 passed）。

## 5. 契约变更（需要评阅者确认）

`test_repair_call_outcome.py::test_mixed_rejection_classes_keep_stop_semantics` 的旧契约是
「混合拒因一律停止、不导出反馈」。M03 施工包明确要求「混合拒因逐项处理，工程故障优先
阻断依赖动作」，因此该用例已更新为：两条生产者记录都保留、两者均非工程故障、
`terminal=false`、只发放一次纠正机会。**这是唯一一处主动改写的既有测试契约**，
未删除用例、未放宽采纳判据（基线仍保留、BLOCK 仍照常）。

## 6. 剩余问题（不在本模块范围内）

* **M04**：预算准入、审计预留、模拟时钟、冻结稿续验、服务端等待 / 流式耗时记录。
* **M05**：run j 冻结现场 + 两组匿名自足样本的七类场景离线验收、按 M01–M05 汇总工作包、
  修订前后可阅读稿件。
* **GATE 未解锁**：有限真实模型验证（最多 2 次修复请求 + 2 次完整审计请求）需用户确认
  固定输入验证后才可开始；本包不自动加跑，沿用「run j 收口」。
* 端到端仍无 PASS：a→j 及 run i/j 均 BLOCK；补丁版真实模型未触发合法中间补帧。
  离线链路已证明机制可用，**不等于**真实模型遵从或产品结果更好，本文件不作该推断。
* 14 个 `test_plan_phase_*` 既有失败仍未解决（与本包无关，已留基线对照）。
* 工作树在 `efa5f3f` 之后有 M01/M02/M03 未提交改动；未 push、未重签、未正式采纳。
