# Step01 真实运行报告与发送预算修正（2026-11-16）

状态：**零模型调用**的代码/报告/文档修正。基线 `4f3b056`；本轮不含真实调用。

## 1. 原报告的错误（按 `run_log.json` 核对）

已提交的收尾文档 §8 写“发送 2 次、无审计”，与运行报告冲突。实际：

| 项 | 读数 |
|---|---|
| `requests.post` 尝试 | **6 次** |
| 其中抛出 SSL 异常 | **4 次**（均为钩子：1 次首次 + 3 次重试） |
| 收到 HTTP 响应 | **2 次**（正文生成 200、审计 200） |
| 逻辑调用（台账） | **3 条**：`storyboard_script` ok、`hook_shot` **failed**、`script_audit` ok |
| 审计 | **已执行**，432.2s，13 条 blocking（`step01_commit.json`） |
| `ledger_rows` | 报告为 0 是**读取路径错误**；台账实际在 `chapter_dir/step01_calls.jsonl`，共 3 行 |
| `shots` | 报告为 0 是因为 BLOCK 不写 `script_result.json`；冻结候选有 **13 镜** |
| `plan 0/14` | 是 `temporal_plan` 迁移统计（`main.py:11873`），**不是** plan_refs；候选 13/13 声明、顺序 D1→D2→D3 |

结论：顺序修复仍被证据支持；修复轮确实未进入；BLOCK 是**内容残余 + 预算准入**，
不是事件契约失败。

## 2. 代码修正

- 新增 `shared/send_cap.py`（发送边界唯一计数实现；**不复用/不覆盖**已有的
  `shared/send_budget.py`——后者是跨恢复的 `allow(schema)` 预算，语义不同）：
  - `attempts` / `responses_received`：总请求尝试 / 收到 HTTP 响应；
  - `hook_attempts` / `hook_responses` / `hook_blocked`：钩子分类尝试 / 响应 / 超限阻断；
  - 总上限与钩子分类上限都在**发请求之前**执行；异常只记“未收到响应”，
    不再用 `transmitted` 这个名字暗示“请求已发出/未发出”。
- `tools/run_step01_from_verified_plan.py`：
  - 台账改读 `chapter_dir/step01_calls.jsonl`（`main.py:12959` 的真实落点）；
  - `transmitted` → `responses_received`；新增 `hook_attempts/hook_responses/hook_blocked`；
  - BLOCK 时从 `.dsh/step01_budget_frozen/<run_id>/frozen_script.json` 读冻结候选
    （`shots=13`），并记录 `frozen_candidate` 路径；
  - `commit.detail` 读 `detail`（旧代码读不存在的 `status_detail`）；
  - 审计优先读 `step01_audit[.blocked].json`，缺失时回退 commit manifest 的
    `audit_status/blocking_count`；
  - manifest 新增 `hook_max_sends`（逻辑生成上限，默认 1）与
    `hook_max_attempts`（请求尝试上限，默认 4）。
- `shared/config.py` 新增 `HOOK_MAX_ATTEMPTS`（0..6，默认 4）；**`main.py` 保持字节不变**
  （原因见 §4.1：`PROMPT_SOURCE_FINGERPRINT` 哈希整个 `main.py`，改它会作废已有审计的
  `source_identity`）。钩子请求尝试上限在发送边界由 `CallCounter` 执行；逻辑生成上限
  仍由 `HOOK_MAX_SENDS` 控制。
- `step01_script_gen/resume.py`：冻结续验新增 `--max-sends`、`--hook-max-attempts`、
  `--audit-repair-rounds`（默认 1）、`--no-hook`；在发送边界执行总尝试与钩子分类上限，
  预算耗尽输出 `send_budget_exhausted` 并保留现场（不重抛）。
- 夹具 `fixtures/step01-run-k-fixed-plan-manifest.json` 显式写入
  `hook_max_sends=1`、`hook_max_attempts=4`。

## 3. 离线回归

新增 `tests/step01_script_gen/test_send_counter.py`（5 条）：

1. 6 次尝试（1 成功 + 4 SSL + 1 成功）→ `attempts=6, responses_received=2`；
2. 钩子分类按系统/用户提示词标记（`data=` 与 `json=` 两种形态）；
3. 钩子尝试上限：超限请求**不再发出**，`hook_blocked=1`；
4. 总上限：超限请求不再发出；
5. 钩子尝试/响应分别计数。

全量 `tests/step01_script_gen` 与 `tests/shared` 读数见 §6（本轮运行后回填）。

### 3.1 冻结续验预检发现（真实原因）

- 恢复入口 dry-run 首次复核报 `source_identity 不匹配（原文/计划/契约已变）`。
- 逐项对账：`RULE_SOURCE_FINGERPRINT`、`audit_prompt_version`、`audit_focus_version`、
  通道、时长带、原文、计划、`script_revision` 全部一致；唯一变化是
  `PROMPT_SOURCE_FINGERPRINT`（`e57bdcf27c4b80e4` → `e299709b953fd5f2`）。
- 根因：`_compute_prompt_source_fingerprint`（`quality_issues.py:169-195`）哈希
  `prompts.py` / `outline.py` / **整个 `main.py`** / `prompts/`；本轮在 `main.py` 里加
  `_hook_max_attempts()` 这个**非提示词**辅助函数，改变了文件字节，进而改变了
  `PROMPT_SOURCE_FINGERPRINT`，而 `_final_audit_source_identity`（`main.py:2862-2876`）
  又把它并入 `source_identity`，导致审计被正确但**无必要**地判为不可复用。
- 这与既有合同冲突：`test_audit_reuse_ignores_the_prompt_source_fingerprint`
  （`test_step01_commit.py:1047`）与 `_compute_prompt_source_fingerprint` 的注释都声明
  该指纹**不参与**复用判定；但 `source_identity` 路径实际包含了它。
- 本轮处置：`HOOK_MAX_ATTEMPTS` 只放 `shared/config.py`（不在提示词指纹内），
  `main.py` 回退到审计产物的字节状态；恢复入口 dry-run 重新得到
  `audit_reusable=true`、`audit_issues=11`。
- 遗留缺陷（建议后续单独修）：`source_identity` 应剔除 `PROMPT_SOURCE_FINGERPRINT`，
  或把提示词指纹收窄到真正的提示词内容；否则任何 `main.py` 非提示词改动都会作废
  历史审计。本次不扩大改动，以免影响已冻结身份。

## 4. 冻结续验准备

- 恢复清单：`snapshots/step01_resume_20261116_003848.json`，`loadable=true`。
- `python -m step01_script_gen.resume --manifest … --dry-run` 读数：
  `ok=true`、`audit_reusable=true`、`audit_issues=11`；计划/原文/时长带/通道身份一致
  （`plan_sha256=5c1bc5ef…`、`novel_sha256=aba1adc2…`、`agent_plan/deepseek-v4.1-flash`）。
- 续验入口（非 dry-run）会走 `_commit_step01_outputs`：**复用审计**、进入定向修复与
  最终审计，**不重新生成正文/钩子**；`--max-sends` 覆盖内部重试。

## 5. BLOCK 归因（分开记账）

- **内容残余**：审计 11 条 issues、commit `blocking_count=13`（含确定性质量门条目）；
- **预算准入**：剩余 582.7s < 本轮修复 180s + 最终审计 600s + 收尾 60s = 840s；
  总预算 `STEP01_TOTAL_BUDGET_SEC=1200`（`main.py:11323`）；修复轮未进入。
- 617 + 180 + 600 + 60 ≈ **1457s**；1800s 只是下一次实验的候选，不保证提交通过；
  动态放宽必须带显式上限。

## 6. 回归读数

- 全量 `tests/step01_script_gen`：**4529 passed, 294 skipped, 4 xfailed, 0 failed, 0 error**
  （含新增 `test_send_counter.py` 6 条、`test_hook_budget.py` 3 条）。
- 计数修正前后对比：旧报告 `sends=2 / ledger=0 / shots=0`；新逻辑读数为
  `attempts=6 / responses_received=2 / hook_attempts=4 / hook_responses=0 /
  ledger_rows=3 / shots=13 / audit.status=ok / audit.issues=11 / commit.detail=预算不足…`。
- `shared/send_budget.py` 保持原样（`git status` 无改动）；新计数模块独立为
  `shared/send_cap.py`。
