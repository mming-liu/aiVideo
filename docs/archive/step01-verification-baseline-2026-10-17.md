# Step 01 验收基线（2026-10-17）

> 本文件是该工作包的**交付记录**：固定基线、列清被 `.gitignore` 隐藏的测试改动、
> 复核 4 条 xfail、登记已知缺口。读数只在标注的轮次有效；后续改动须重新取值。
>
> **为什么放在 `docs/archive/`**：`.gitignore` 对 `docs/` 是**精确逐份放行**
> （`/docs/*` + 少量 `!docs/xxx.md`，注释记录了"只放行计划边界那四份"的既有决定）。
> 本记录不改那条规则，改放在已开放的 `docs/archive/` 目录，从而**可以入库**。
> 若希望它出现在 `docs/` 顶层，需要额外加一行 `!docs/<文件名>` 的放行白名单。

## 1. 基线

| 项 | 值 |
|---|---|
| 仓库 HEAD | `a49fe20`（"update"），父 `de480fe` |
| 工作树改动 | 仅 `tests/step01_script_gen/test_plan_revision_v2_wiring.py`（**非本工作包所改**：把 `delta` 断言更正为 `"delta" not in _props`，理由见该文件注释） |
| 生产改动是否已入库 | **是**。`outline.py`（含 `planning_acceptance_version_invalid` 能力登记）、`plan_feasibility.py`（v6 开关统一、legacy 分支消费开关）已在 HEAD 内 |
| 测试命令 | `python -m pytest tests/step01_script_gen/ -q --no-header -p no:cacheprovider --tb=no` |
| 读数 | **0 failed / 3482 passed / 277 skipped / 4 xfailed**（收集 3763） |
| 277 skipped 的构成 | 113 归档回放 + 约 164 `.tmp/run_lifecycle.py`，**长期惰性**，不计入修复收益 |

### 受测文件摘要（SHA256 前 12 位）

| 文件 | 摘要 | git |
|---|---|---|
| `step01_script_gen/outline.py` | `2A9056695C3E` | tracked（已在 HEAD） |
| `step01_script_gen/plan_feasibility.py` | `783E2BF1F491` | tracked（已在 HEAD） |
| `tests/verification_helpers.py` | `5907FE824040` | **IGNORED** |
| `tests/step01_script_gen/test_plan_carrier_contract.py` | `20EC65ED437A` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_h_call_evidence.py` | `F90EE0608F5E` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_j_capability.py` | `2993E4500D02` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_j_combinations.py` | `4054B860A454` | **IGNORED** |
| `tests/step01_script_gen/test_ch1_first_version_positive_sample.py` | `75A15D32CC7A` | **IGNORED** |
| `tests/step01_script_gen/test_ch1_frozen_hybrid_delta.py` | `22B3164042E4` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_c_declaration_ops.py` | `A67A75894A4C` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_d_wiring.py` | `778103E779B7` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_e_commit_chain.py` | `31F56B03853F` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_g_rejection_feedback.py` | `02CB79F53977` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_j_properties.py` | `FE0FE42A3E06` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_k_baseline_and_capability.py` | `58026F6349ED` | **IGNORED** |
| `tests/step01_script_gen/test_plan_phase_l_interface_decision.py` | `49C94F715F6F` | **IGNORED** |

> ⚠️ **15 个受测文件里 14 个被 `.gitignore` 隐藏**（仅 `outline.py`/`plan_feasibility.py` 与
> 他人改的 `test_plan_revision_v2_wiring.py` 在版本里）。⇒ 当前"全绿"**不能由 `git clone`
> 复现**；提交时必须显式放行这些路径，否则只交付生产代码、测试证据全部丢失。

## 2. 本轮使读数转绿的改动与归属

| # | 改动 | 责任入口 | 归属 |
|---|---|---|---|
| 1 | 核验覆盖缝接线（脚本服务不再被核验消耗） | `test_plan_carrier_contract._patch_service`、`test_plan_phase_h_call_evidence._patch_ledgered` | 本工作包 |
| 2 | 计划级版本码登记 | `outline.py` `PLAN_FIX_CAPABILITY["planning_acceptance_version_invalid"]` | 本工作包 |
| 3 | 核验记录原文取**本次调用实参** | 10 个测试文件的 `_gor_with_verify` | 本工作包 |
| 4 | `legal_record_for`：定位不到出处时判 `unknown`（不再谎报 `supported`） | `tests/verification_helpers.py` | 本工作包 |
| 5 | ch1 记录补 `coherence` 轴 / `novel_digest` | ch1 两个测试文件 | 本工作包 |
| 6 | v6 开关统一 + legacy 分支消费 `require_verification` | `plan_feasibility.py` | 本工作包（已在 HEAD） |
| 7 | `delta` 断言更正 | `test_plan_revision_v2_wiring.py` | **他人**（工作树未提交） |
| 8 | ch1_first 夹具修订（`_DISPLAY`/`_SOURCE` 改 6 键、f04 测试重写，+10 行） | `test_ch1_first_version_positive_sample.py`（mtime 22:45:55） | **他人/未确认** |

## 3. 已知覆盖缺口（必须随交付记账）

| 缺口 | 证据 | 状态 |
|---|---|---|
| `test_ch1_first_version_positive_sample.py` 可能存在约 **12 个测试函数**缺失 | 同会话同一命令的**自洽读数**：事故前两次 **3775**（21+3473+277+4、15+3479+277+4）、现在两次 **3763**；两次均含 277 skipped / 4 xfailed。当前该文件 `def test_` 23 个、收集 33 | **未恢复**，也**未最终证实**（见下方"取证边界"） |
| 该文件的常量块为**重建**（块内标 `[恢复]`/`[派生]`） | 文件内注释；`_BEAT_EVENTS`/`_CARDS`/`_DISPLAY` 取自损坏前 `.pyc` 的 `co_consts` 转储 | 已重建，**非逐字复原** |

### 3.1 恢复取证（已穷尽，未找到测试体）

| 来源 | 结果 |
|---|---|
| Git 提交历史 | **无**：`git log --all -- <路径>` 为空，`git rev-list --all --objects` 里无该路径 ⇒ 从未入库 |
| `git stash@{0}` | 只含 12 个生产/配置文件，**不含**该测试文件 |
| 全盘内容检索（仓库 + `%TEMP%` + `C:\myFloders`） | 除当前文件与被引用的生产文件外，**无第二份副本** |
| PyCharm LocalHistory | `changes.storageData`（105 MB）非明文 zlib 流，按 zlib 扫描 0 命中；**未**继续逆向其私有格式 |
| `.pytest_cache/v/cache/nodeids` | 存在，但时间戳 **22:46**（事故之后），只能作为当前状态佐证（3763 / ch1_first 33） |

**处置**：不从 `.pyc` 常量**推造测试体**；不把当前通过数当作覆盖完整；
上表"12 条"在**取到独立基线之前**只能记为**未最终证实**的差异，不得表述为已确认丢失。

### 3.2 归属未确认的改动（不进本包提交）

| 文件 | 改动 | 归属 |
|---|---|---|
| `tests/step01_script_gen/test_ch1_first_version_positive_sample.py` | mtime **22:45:55**，+10 行；`_DISPLAY`/`_SOURCE` 由 7 键变 6 键、f04 用例重写 | **他人/未确认**（与本包的重建块混在同一文件，须先分离） |
| `tests/step01_script_gen/test_plan_revision_v2_wiring.py` | `delta` 断言改为 `"delta" not in _props`（工作树未提交） | **他人** |

## 4. 4 条 xfail 复核

判据：是否仍是**当前契约**下的已知缺口；是否属于本工作包的验收承诺。

| 测试 | 缺口内容 | 判定 |
|---|---|---|
| `test_cross_field_checks.py::test_evidence_guard_does_not_yet_block_participant_substitution` | 跨字段证据守卫按**词面包含**判；受事角色替换（杨旭→听香）不转红 | 已知缺口，**词面判据边界**；非本包承诺 |
| `test_plan_contract_r2.py::test_agent_patient_swap_is_not_caught_by_the_lexical_screen` | 词法筛分不出施受事 | 同上；完整承载交内容核验 |
| `test_plan_contract_r2.py::test_single_relation_fact_is_not_caught_by_the_lexical_screen` | 单一关系事实与"两人同框"共享要素 | 同上 |
| `test_plan_narration_fact_support.py::test_a_visual_carrier_expressing_only_part_of_the_fact_is_not_yet_caught` | 画面是**转述**，未逐分量判 | 同上（该文件注释明写：审计侧补上内容闭环后应变正例） |

**共同结论**：4 条都是**确定性词面/跨字段判据的已知边界**，其补偿控制是"完整承载交内容核验"。
本工作包已把该补偿控制变成**强制**（无记录不得判通过；应用阶段与完整验收的两模式对照见第 5 节），
但**没有**加强词面判据本身。

⇒ 不属本包验收承诺，**登记为后续维护项**（"词面判据边界 + 内容核验补偿"）。

## 5. 本包已验的契约对照（离线）

| 对照 | 结果 |
|---|---|
| v6：无记录 / 旧摘要记录 × `require_verification` True/False | True+无记录 ⇒ `verify_record_missing`；True+旧记录 ⇒ 记录问题；False ⇒ 均 `[]`（只跑确定性上下文准入） |
| legacy-marked：`require_verification` True/False | True ⇒ 要求记录；False ⇒ `[]` |
| 应用阶段（`apply_replan_ops`） | 只在候选定稿前做确定性契约检查，记录由统一出口按最终摘要绑定 |
| c5 判别性 | 基线两条同类问题；第一轮消一条、留一条；剩余项逐字进下一轮 |

## 6. 四状态

- **代码落盘**：是（第 2 节 1–6 项；其中 1、2、3、4 项的受测文件被 gitignore）。
- **离线验收**：是（读数见第 1 节；契约对照见第 5 节）。
- **真实运行**：**未验证**。当前证据全部是离线桩/夹具，**不能证明真实模型收益**。
- **独立复核**：未做。

## 7. 下一步

1. 决定是否提交：需显式放行第 1 节列出的 14 个被忽略的受测文件，否则基线不可复现。
2. 是否做 Step 01 小范围实跑：需**单独确认运行范围与授权**（AGENTS.md 的费用与产物边界），
   并先明确"要验证的问题、目标路径、预期观测"。
