# 契约块位置 + 构造失败不降级 + 重选语义分开 2026-09-25

用户 2026-09-25 审核（原文摘要）：

> 暂存内容还不建议提交。契约块完整进入了生成提示，但我在当前代码中确认了两处尚未闭合的执行边界：
> 1. `main.py:257` 会吞掉大纲锚点或契约块构造异常，随后继续生成。这让"已通过计划门"的稿件仍可能
>    在**没有执行契约**的情况下生成。应改为生成失败并明确报错，补一条异常时模型服务不被调用的回归。
> 2. 契约块并非四个入口都位于**最终请求末尾**：超限收敛 / 结构收敛 / GAP 重生又追加了反馈。
>    现有测试证明"块在提示里"，没有证明"块在末尾"。建议统一由最终组装处追加契约，并对三个重生
>    请求断言末尾位置。
> 另有一处诊断语义要收口：`outline.py:201` 把网络/解析失败与"有效重选后仍超预算"一起持久化为
> 已用完重选机会；前者不能据此报告"需拆集"。可以保持不自动重试，但须分别记录原因并提供明确的
> 重新规划入口。
> 交付范围我建议这样定：`AGENTS.md` 保持独立，不替你暂存；`.env.content` 与 `shared/config.py` 的
> **DeepSeek-only 切换另成一组变更**；文档若要随仓交付，只精确放行这份留档，不放开整个 `docs/`。

## 改了什么

| 位置 | 改动 |
|---|---|
| `step01_script_gen/main.py`（`gen_spy_script`） | **构造失败不再降级**：原来的 `except Exception: _logger.warn("…继续无大纲生成")` 改为抛 `PlanInfeasibleError`（**非 `RuntimeError`**——候选循环的 `except RuntimeError` 是"生成失败→重试"语义，确定性失败不该重试）。异常在 `create_service()` **之前**抛出 ⇒ 不调用模型。 |
| `step01_script_gen/prompts.py` | 契约追加点从 builder **移到统一助手** `append_execution_contract(prompt, contract)`（两个 builder 撤回 `contract_block` 形参）；四个入口各自在**最终组装处**调用它。 |
| `step01_script_gen/main.py`（三个重生入口 + GAP） | 超限收敛：`builder → 时长反馈 → 契约`；结构收敛：`builder → 结构反馈 → checklist → 契约`；GAP：新增 `_build_gap_regen_prompt()`（`锚点 → gap_context → 契约`）并把该处组装从 `main()` 里抽出来，**可直接单测**（此前只能源码钉住）。 |
| `step01_script_gen/outline.py` | **重选语义分开**：`replan_dialogue_within_budget()` 失败时返回 `(None, 原因)`（`call_failed: …` / `invalid_result` / `core_field_changed: …`），`enforce_budget_by_replanning()` 记进 `info["detail"]`；新增显式入口 `STEP01_FORCE_REPLAN=1`（默认空 = 沿用已记录结论，不自动重试）。 |
| `step01_script_gen/plan_feasibility.py` | 新增 `_replan_note()`：`result="replan_failed"` → 报错写"**重选未完成…这不是「本集不可行」的判定，不能据此报告需拆集**"并**撤下**"拆集"建议、改给 `STEP01_FORCE_REPLAN=1` 入口；`result="infeasible"` → "已重选 N 轮后仍超预算（对白 x→y）⇒ 需拆集"。两者都进 `step01_plan_infeasible.json`。 |
| `tests/step01_script_gen/test_plan_feasibility.py` | 39 条（+6）：构造失败**不调模型**且异常非 RuntimeError；三个可截获入口的**末尾位置**断言（`endswith`）+ GAP 组装函数单测（反馈必须在契约之前）；四个入口接线源码钉（含"契约之后不得再拼内容"）；`replan_failed` 与 `infeasible` 的报错/诊断分开；`STEP01_FORCE_REPLAN=1` 真重跑一轮 vs 默认 0 次调用。 |
| `tests/step01_script_gen/test_outline_budget_and_repair_prompt.py` | 契约 pin 改为按新接口断言（形参 + 最终组装点）。 |

## 为什么

1. **吞异常 = "已过计划门"与"实际受约束"可以不一致**：门判过之后构造失败还继续生成，产出的稿子
   没有任何选材约束，而报告会显示计划已生效——这是最坏的一种静默降级（准则 7）。现在显式失败，
   且失败发生在模型调用之前（可断言）。
2. **"块在提示里"≠"块在最后一眼"**：三个重生入口在 builder 之后还要拼反馈（实测提示 3.4 万字符，
   长 prompt 后段易失声），而**契约是本集选材的唯一强制通道**，被埋在中间等于半失效。统一由
   最终组装处追加，并把 GAP 的组装抽成函数，让"末尾"这件事可以被**断言**而不是靠读代码相信。
3. **没跑成 ≠ 不可行**：网络/解析失败得到的是"没有结论"，与"重选过一轮仍装不下"是两种事实。
   前者若被持久化成"重选机会已用完"，下一轮既不会重试、还会被报成"需拆集"——把工具故障
   变成了内容判决。

## 影响面

- **行为变化（fail-closed）**：锚点/契约构造异常时整轮显式失败（退出码 1），不再降级生成。
- **提示变化**：三个重生入口的契约块从"反馈之前"移到"反馈之后"（内容不变，位置变）；
  其余不变。
- **不自动重试的语义保持**：同一计划版本仍只重选一次；`STEP01_FORCE_REPLAN=1` 是唯一显式重跑入口。
- 无判据/容量/修复轮改动。

## 实跑测试数字与回退

- `pytest -q` → **2329 passed, 1 skipped**。
- **只读核对（`.tmp/contract_probe.py`，桩服务、无网络调用，真实章节 + 真实计划 + 冻结失败稿）**：
  契约块 890 字符 / 7 句台词 / 5 条事实 / 5 条事件；**初始候选、超限收敛、结构收敛**三处
  「块内缺 0 句 + **末尾是契约块 = True**」；两个剖面（当前生效 `build_prompt` 与 doubao）
  同样「完整 + 在末尾」。GAP 那处由 `test_gap_regen_prompt_puts_the_contract_last` 单测覆盖。
- 回退：还原 `gen_spy_script` 的 except 分支、把契约追加挪回 builder、还原 `_build_gap_regen_prompt`
  的抽离、还原 `replan_dialogue_within_budget` 的返回形态与 `_replan_note`、测试回退。

## 收口补正（同日复核）
用户复核：**重选语义还漏了一处**——`replan_failed` 时正文解释了"重选未完成"、建议也撤下了拆集，
但**异常开头仍写"本集规划不可行／需拆集"**，把调用失败误报成内容结论；原用例只排除了建议中的
一段措辞，没检查这个开头。已改：

- `check_plan_before_generation()`：`replan_failed` 时异常**开头**改为
  `规划重选未完成——尚无「本集不可行」的结论`；`infeasible` 时仍是 `本集规划不可行／需拆集`。
  **首版计划超预算的数字事实照旧保留**（在 `reasons` 里）。
- 诊断产物新增 `conclusion` 字段区分**可据此下的结论**：`replan_incomplete`（重选没跑成，
  尚无结论）/ `teardown`（确认装不下）；`verdict` 仍是计划本身的读数（事实）。
- 用例改为断言**异常开头**（不是整段措辞）+ 诊断的 `conclusion`/`verdict`/建议集合：
  `test_replan_call_failure_is_not_reported_as_teardown`（开头无"需拆集/本集规划不可行"、
  保留 90.62s 与"超预算上限"、`conclusion=replan_incomplete`、建议里无拆集）与
  `test_replan_still_over_budget_is_reported_as_teardown`（开头恰为"本集规划不可行／需拆集"、
  `conclusion=teardown`）。
- 实测两条分支的报错（同一份超预算计划）：`开头 = 规划重选未完成——尚无「本集不可行」的结论`
  ／`含拆集建议 = False`／`conclusion = replan_incomplete`；另一条 `开头 = 本集规划不可行／需拆集`
  ／`含拆集建议 = True`／`conclusion = teardown`。
- `pytest -q` → **2329 passed, 1 skipped**。

## 重跑读数（同日 22:10，Step01 一次，退出码 0 = BLOCK 正常收尾）

用户授权重跑一次以决定是否手动提交。结果 **BLOCK**，但**新稿不是被拦的那一份**：

- **计划门通过**：`[PLAN] 对白 5 句/估时 27.55s（逐字核验剔除 0 句）；节拍 6 个/下限 18.00s；
  下界 27.55s；预算 60–90s`；`[INFO] 生成执行契约 1061 字符（选用台词 5 句）`
  （本轮 provider/模型/提示都变 ⇒ 大纲缓存失效，由 `deepseek-flash` 重新规划：5 句 / 27.55s）。
- **新稿服从契约（首次实测）**：新稿 17 镜 / 67.14s / 5 个台词镜，
  **5/5 命中计划选的那 5 句，0 句自选，0 句遗漏**（唯一的"未命中"是合规替换
  `杀人`→`出人命`，按 `_check_grounding` 同源映射还原后命中）。
  ⚠️ **单次采样**，不能据此宣称后续生成稳定遵守选材。
- **新稿输给历史快照**：新终稿 90.0 分/风险 34（首尾帧完整性异常）< 快照最优 100.0 分/风险 17
  ⇒ BEST-GUARD 选中历史快照（`script_result_20260924_220539.json`），新稿留档
  `snapshots/candidate_pre_bestguard_20260925_221341.json`。
- **被拦的是那份历史快照**：提交管线第 1 轮 22 条 blocking（审计 5 + 质量门 17）→ 第 2 轮 8 条
  （审计 2 + 质量门 6）→ **"提交前修复轮零改善，停止修复"** ⇒ BLOCK。
  本轮触发原因**不是** 6 镜联动组上限（那是更早几轮的形态），而是修复轮零改善。
- **正式产物副作用**：三份正式产物仍是 09-24 08:11（**未替换**）；`step01_commit.json` 记 BLOCK；
  欠账台账登记本轮三个身份（被选中快照自身 `04c9d556…` / 提交入口 `8b783bb2…` / 修后 `b02adb8e…`）
  —— 早前那条身份边界修复在生产里**再次生效**。
- **口径得到验证**：计划下界 27.55s 低于下限 60s，而成片实际 67.14s（正片）——**下界确实不能推出欠时**。

### 顺带修的可观测性缺口

上面这条"下界低于下限"的读数此前**只在停下时才出现在报错里**，可行路径不打日志（本轮就没人看得见）。
已补：`check_plan_before_generation()` 在 `verdict=feasible` 且 `reasons` 非空时打
`[PLAN] 注意：…`；用例 `test_below_min_is_logged_on_the_pass_path` 挂 handler 断言这条日志真的出来。
**未改判据、未改容量**，只把已定的报告口径真正暴露出来。

## 交付状态（本轮只暂存，未提交）

- 暂存组 1（本次实现的代码 + 测试）：见 `git diff --cached`——已按本轮修复重新暂存并复核自包含。
- 另成一组（用户建议）：`.env.content` + `shared/config.py` 的 **DeepSeek-only 切换**，**未暂存**，
  由用户决定何时作为第二组提交。
- `AGENTS.md`：保持独立，**未暂存**。
- 留档：仍在 `/docs/` 忽略之下（要随仓交付需**只精确放行相关留档**，不放开整个 `docs/`）。
