# S06（G1/G2/G6）决策样本

> 生成：`python tools/build_s06_decision_samples.py`（离线、零模型调用）

## G1：`issue_ledger.closed` 接提交门

裁决问题：issue_ledger.closed 是否接进提交门（阻塞问题必须有证据关闭）

证据层级：{"ledger": "actual_function:build_issue_ledger", "gate_verdicts": "deduced_from_rule:未走真实提交入口"}

| 样本 | 台账状态 | closed | 现行门 resolved（推演） | G1 门 resolved（推演） | 现行门（推演） | G1 门（推演） |
|---|---|---|---|---|---|---|
| 本轮审计没有再次报出（模型沉默） | not_detected_this_round | False | 1 | 0 | 推演 accept（把沉默读成已解决） | 推演 reject（无关闭证据） |
| 有确定性复核证据关闭 | fixed_with_evidence | True | 1 | 1 | 推演 accept | 推演 accept |

建议：只对**阻塞级**确认问题要求 closed=True；非阻塞条目继续只记账。样本 G1_a 是唯一行为差异：按规则推演为现行门接受、G1 门拒绝；真实提交入口的最终结果尚未验证（见 evidence_level）。

## G2：景别跨度 > 2 级的自动改写

裁决问题：景别跨度 > 2 级时，后处理是否继续自动改写前镜景别

- 样本：镜 1 `[大特写] 甲的手指按在门闩上` → `[全景] 甲的手指按在门闩上`；镜 2 `[远景] 山谷与长河`
- 约束改写：[{"shot": 1, "before": "[大特写] 甲的手指按在门闩上", "after": "[全景] 甲的手指按在门闩上"}]
- 既有 warning：["镜头1→2: 景别「大特写」→「远景」跳跃幅度 6 级（>2 级）——特写直接跳大远景观感割裂，建议中间补过渡景别（镜头组接规则33）。"]

| 选项 | 行为 | 风险 |
|---|---|---|
| 保留自动改写（现状） | 前镜「大特写」被改成「全景」；audit 不再收到这条跨度提示 | 用户/审计刻意选择的近景被后处理改掉；与修复轮已授权镜头冲突 |
| 只报 warning，不改写 | 前镜保持「大特写」；validation 仍报 6 级跨度，交审计/人工决定 | 跨度可能留在成片里；需要审计或人工接住这条 warning |

建议：G2_b：`_check_shot_size_jump` 已经产出同一条 warning，后处理不再二次改写；先做影子对照再决定是否保留自动改写。

## G6：声音「准备态 / 视频后确认态」

裁决问题：声音是否拆「准备态（声明）/ 视频后确认态（实测）」两态

- 样本：一条独立对白声明 `5.0s`，镜头窗口 `4.0s`，**没有音轨**（未实测）。
- `build_voice_preparation` 产出：`actual_sec=5.0`，`verified=False`，`measurement='not_measured'`。
- `check_script_semantics` 产出：`['audio_window_overflow']`（独立声音单元 'v1' 实测 5.00s 超过单元窗口 4.00s；镜头 1 的独立声音合计 5.00s 超过可用窗口 4.00s（差 1.00s））。

| 选项 | 行为 | 风险 |
|---|---|---|
| 维持单态（现状） | 声明值写进 actual_sec；消费方若不看 verified，会把声明当实测（本样本直接产出 audio_window_overflow/reject） | 未实测的内容按实测拒收；准备阶段的超窗与成片超窗混在一起 |
| 拆准备态/确认态 | 准备态只写 declared_sec 与窗口预算；确认态只在 verified=True 时写 actual_sec，消费方按状态路由 | 需要改 audio_policy / voice_preparation / execution_semantics 的消费方 |

建议：G6_b：准备态允许「计划超窗」作为规划问题报告，确认态才按实测时长拒收；拆分后 `execution_semantics` 只消费 verified=True 的 actual_sec。
