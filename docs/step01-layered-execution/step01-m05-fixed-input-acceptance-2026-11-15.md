# M05：固定输入验收与工作包交付（2026-11-15）

> 对应施工包《Step01 修复闭环收敛方案》M01–M05。
> 本文件只记录**可复核读数**：离线入口测试、冻结现场回放、同条件基线对照、剩余问题。
> 边界：真实模型调用 **0**、正式产物写入 **0**、历史现场只读、未 push。

---

## 1. 边界与结论口径

| 项 | 本包状态 |
| --- | --- |
| 真实模型调用 | 0（GATE 未解锁；沿用「run j 收口、不自动加跑」） |
| 正式产物（`./output`） | 未写入；回放输出只落 `docs/` 与本目录 |
| 历史现场（`.dsh/**`） | 只读，未改写任何历史台账 / 日志 |
| 远端 | 未 push；本地提交见 §7 |
| 成功判据①离线能力 | **已证明**（§3、§4） |
| 成功判据②模型遵从 | **未证明**（GATE 未解锁，无新真实答卷） |
| 成功判据③产品结果 | **未成立**（a→j 仍无 PASS） |

> 本包**不**声称「Step01 已修好」。可声称的是：补帧身份、请求契约、拒收反馈、预算准入四类缺陷已在离线入口上可复核地收口。

---

## 2. 固定输入（run j 冻结现场）

回放输入指纹（`run_j_attribution_replay.json` 登记，逐字节复核）：

| 文件 | SHA-256（前 16 位） |
| --- | --- |
| `snapshots/script_result_20261115_175552.json` | `2259c5aaed5263ff` |
| `step01_call_bodies/c1abb36fe6f7042f5.txt` | `4881372d596d0ef8` |
| `step01_repair_rounds.json` | `e288649a275a8e20` |
| `step01_audit.blocked.json` | `24c878638758afa9` |
| `_resume/novel_aba1adc22826.txt` | `aba1adc22826c283` |

- 冻结稿 revision：`d8781a99ab9e9befa1663ffb5393488b0d3f88954c7ea603dac631e389d6809f`
- 回放校验 `revision_identity_ok = true`（历史 `revision_before` 与现场一致）
- 回放产物 SHA-256：`48f233b53423b0195f42c621938565c3842d9144c21dda04b718ff8798f60bb4`（41,853 bytes）
- **两次覆盖重跑逐字节一致**（`deterministic=YES`）：回放文件不含生成时间戳，只登记输入 SHA-256 与 mtime

### 两组匿名自足样本

| 样本 | 载体 | 自足性 |
| --- | --- | --- |
| 单元级 | `tests/step01_script_gen/test_kf_frame_identity.py`（匿名夹具 `entry/layout/kf`） | 零模型 / 零网络 / 零 `.dsh` 依赖，干净检出可跑 |
| 事务级 | `tests/step01_script_gen/test_kf_frame_identity_transaction.py::_TxHarness`（假 `_Svc`） | 同上；走**真实** `_repair_shot_warnings` 入口 |

真实 run j 只用于**归因回放**（`tools/replay_run_j_attribution.py`），不作为可移植样本。

---

## 3. 七类场景覆盖

| # | 场景 | 入口 | 代表用例 | 读数 |
| --- | --- | --- | --- | --- |
| 1 | 精确叶子修复 | `apply_scoped_field_updates` / 请求示例 | `test_m02_precise_leaf_paths_are_not_promoted_to_parent_field`、`test_m02_compact_example_updates_pass_real_application_entry` | 通过：`kfs[0][0].pose` 不再被提升为 `frame_layouts`，示例本身可被真实入口应用 |
| 2 | 合法中间补帧 | 事务入口 | `test_legal_middle_insert_is_accepted_by_transaction`、`test_layout_addition_binds_to_shifted_frame_through_four_nodes` | 通过：`source=assembly` 映射，效果落在新帧位，四节点账本齐全 |
| 3 | 联动修改 + 保护字段保留 | 事务入口 | `test_layout_violation_rolls_back_only_offending_target`、`test_target_rollback_restores_identity_and_drops_effects` | 通过：只回退肇事目标，其余目标映射与效果保留 |
| 4 | 首轮非法 → 次轮合法 | 事务入口（两轮真实调用） | `test_m03_two_call_feedback_closed_loop` | 通过：首轮拒收保留基线；结构化反馈进入第二次实际请求文本；次轮合法响应被应用、后处理、重验、最终返回 |
| 5 | 多目标部分拒收 | 事务入口 | `test_rejected_target_has_no_mapping_while_others_keep_theirs`、`test_layout_violation_rolls_back_only_offending_target` | 通过：拒收目标不导出映射，合法目标映射独立有效 |
| 6 | writer 真改写 vs 事务回滚 | 归因层（单元 + 事务 + 现场回放） | `test_writer_revert_is_attributed_to_first_changing_writer`、`test_transaction_rollback_is_not_reported_as_postprocess_revert`、`test_m01_rollback_is_not_attributed_to_postprocess` | 通过：writer 改写定位到首个改动 writer；整轮回退不再误记 `postprocess_reverted` |
| 7 | 预算不足 / 恢复 / 身份不符 | `repair_budget` + 模拟时钟 | `test_simulated_run_exhaustion_and_recovery`、`test_admission_rejects_one_second_short_of_full_audit_reserve`、`test_verify_rejects_revision_mismatch_even_with_same_digest`、`test_verify_rejects_changed_script_identity` | 通过：不足即拒并冻结候选；恢复后可续验；revision / 配置 / 规则身份任一不符即拒 |

覆盖缺口（如实记录）：**「合法中间补帧」只在匿名样本上被触发**。run j 的真实答卷仍未提供合法中间补帧，因此该分支在真实模型上尚无正例。

---

## 4. 读数

### 4.1 全目录回归（`tests/step01_script_gen/`，junit 口径）

| 指标 | 值 |
| --- | --- |
| 执行用例 | 4791 |
| 通过 | 4479 |
| 失败 | **14（全部为既有失败，见 §4.3）** |
| 报错 | 0 |
| 跳过 | 298（含 4 xfail） |
| 墙钟 | 306.5 s |

### 4.2 本包新增验收（145 项）

| 文件 | 用例数 | 对应 |
| --- | --- | --- |
| `test_kf_frame_identity.py` | 44 | S01–S03 帧身份契约 |
| `test_kf_frame_identity_transaction.py` | 14 | 事务入口（含 M01×2、M03×4） |
| `test_repair_attribution.py` | 10 | M01 阶段归因 |
| `test_repair_answer_example.py` | 14 | M02 请求契约（新增 11） |
| `test_repair_feedback.py` | 41 | M03 结构化反馈 |
| `test_repair_budget.py` | 22 | M04 预算准入与续验 |

### 4.3 14 个既有失败的**同条件基线对照**

方法：在 `.dsh/m03_baseline` 用 `git worktree add --detach efa5f3f` 建 HEAD 独立检出，**把当前 `tests/` 目录整体同步进去**，跑同一组 5 个文件——即「同样的测试 + HEAD 生产代码」。

| 侧 | 结果 |
| --- | --- |
| HEAD 基线（efa5f3f 生产代码） | 14 failed / 105 passed，失败 ID 逐条一致 |
| 当前工作树（M01–M04） | 14 failed / 105 passed，同一组 ID |

结论：这 14 个 `test_plan_phase_*` 失败**与 M01–M04 无关**；它们全在 `outline.py` 计划阶段，不在修复轮链路。基线检出已清理（`git worktree remove --force`）。

### 4.4 全量收集数（单列，不替代执行结果）

`pytest --collect-only -q tests/`：**7628 collected**（13.3 s）。
此数字只说明测试发现量，**不代表通过率**；执行结果以 §4.1 的 `tests/step01_script_gen/` 读数为准，全仓其余目录本包未执行。

---

## 5. 逐包交付

### M01 诊断时点修正 + 现场冻结

- **代码**：`repair_attribution.py`（新增）；`main.py` 新增四阶段快照 A/B/C/D 与 `field_attribution`；`repair_effects.py` 节点冻结 `authorized_paths`。
- **入口测试**：`test_repair_attribution.py` 10 项 + 事务入口 2 项。
- **输入摘要**：run j 冻结四件输入（§2），离线回放。
- **实际读数**：历史 36 字段 → `transaction_rollback` 36 / 模型写入 36 / writer 改动 2 / **纯 writer 回退 0** / 后处理阶段逐字保留 34。
- **修正的旧结论**：撤回「36 字段被既有后处理 writer 全数回退」。三份文档旧归因已同步改写。
- **剩余**：writer 改动的 2 字段（`shot_4:char_layout.0.facing` ← `identity_unification`；`shot_4:motion_desc` ← `v6_43_prompt_enforcement`）随整轮回退消失，是否需要独立修复取决于目标是否先被解决。

### M02 修复请求契约

- **代码**：`repair_authorization.py`（新增）；`_repair_answer_example(..., compact=True)` 重写；两处请求构造注入同一 `_auth_guidance`。
- **入口测试**：`test_repair_answer_example.py` 14 项（新增 11）。
- **实际读数**：通配授权展开为具体下标路径；`frame_layouts` 占位值为 dict；补帧许可复用 `_whole_kfs_authorized`；示例 `updates` 全部可通过真实 `apply_scoped_field_updates`。
- **剩余**：示例只能降低误表达概率，不能强制模型遵从；遵从未验证（GATE）。

### M03 有界反馈闭环

- **代码**：`repair_feedback.py`（新增，七字段 `target/phase/code/field_path/allowed_paths/retryable/details` + 仅展示用 `text`）；替换拒收与事务拒收统一写入 `retry_state`；`no_target_resolved` / `no_gain` 也产出结构化残余。
- **入口测试**：`test_repair_feedback.py` 41 项 + 事务入口 4 项。
- **实际读数**：每个冻结目标集合**恰好一次**纠正机会；相同拒因 + 同一补丁 → 停止该目标；工程故障优先阻断依赖动作；跨 revision 反馈不串稿。
- **回归修复**：首轮引入 11 个回归（`excluded` 语义误置、`continue` 绑定错误、效果抵消类 reason 被 fail-closed 成工程故障），已全部收口。
- **剩余**：两轮链路只在匿名事务样本上证明，未在真实模型上验证。

### M04 预算准入与冻结稿续验

- **代码**：`repair_budget.py`（新增）；`main.py` 动作前准入 + 候选冻结 + 续验核对；`audit.py` / `_text_gen` 补四项计时观测字段。
- **入口测试**：`test_repair_budget.py` 22 项（模拟时钟）。
- **实际读数**：准入要求「动作 + 完整审计 + 收尾」全部容纳；差 1 秒即拒；审计预留取**最终生效**配置上限而非截短额度；预算不足落 `budget_exhausted` 并冻结候选到隔离根；续验核对 revision / 配置 / 规则身份，且拒收夹带上轮审计结果的记录。
- **剩余**：服务端等待与流式耗时在真实通道上仍为 `None`（provider 未埋点时为「不可用」而非估算），需一次真实调用才有读数——属 GATE 范围。

### M05 固定输入验收与交付

- 本文档 + `run_j_attribution_draft_diff.md`（可读修订前后稿）+ 全目录读数 + 同条件基线对照 + 收集数单列。

---

## 6. 可读的修订前后稿

`docs/step01-layered-execution/run_j_attribution_draft_diff.md`（21,365 bytes，由回放工具 `--markdown-out` 生成，不含时间戳）。

要点（shot_4，本轮回退的典型）：

| 项 | 基线(A) | 合并后(B) | 最终(D) |
| --- | --- | --- | --- |
| `motion_desc` | 原描述（含「平底敞口采莲小舟…」） | 模型改写（「素白色轻袍袒腹仰卧船头…」） | **回到基线原文** |
| `char_layout.0.facing` | `袒腹仰卧` | `袒腹仰卧船头`（先经 `identity_unification` 再改写） | **回到基线** |
| `frame_layouts.kfs` 长度 | 3 | 3 | 3 |

即：模型改动确实进入了合并稿（B 与 A 不同），随后**整轮事务回退**把最终稿退回基线（D == A），revision `d878…09f` 前后相等。这正是旧诊断把它误读成「writer 全数回退」的现场。

---

## 7. 提交与状态

- 本包改动与 M01–M04 一并本地提交（见 `git log -1`），**未 push**。
- `.gitignore` 逐条放行 5 个验收文件；`.gitattributes` 为按字节登记 SHA-256 的证据文件加 `-text`。
- 工作树在提交后应干净；历史现场 `.dsh/**` 忽略，不入库。

---

## 8. 未解决（不在本包范围或未成立）

1. **端到端仍无 PASS**：a→j 十个 run 全部 BLOCK；本包只修机制，不改产品结果。
2. **模型遵从未验证**：合法中间补帧在真实答卷上仍是 0 次正例。
3. **B 类协议/prompt 张力**（授权粒度、整表覆盖倾向、约 20 条硬约束）**未收敛**——M02/M03 降低误表达并提供纠正，但未证明模型会遵从。
4. **通道超时**：g 186 s / i 126 s；M04 只做准入与预留，未解决通道本身时长。
5. **14 个 `test_plan_phase_*`** 仍是既有失败，属单独排期。
6. **正式重签 / 正式采纳 / push** 未执行，等决策。
7. **真实调用 GATE 未解锁**：`[GATE] 用户确认固定输入验证后解锁真实调用`；建议上限 2 次修复 + 2 次完整审计，不自动加跑。
