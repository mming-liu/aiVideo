# 第二步：计划＋生成前可行性门（2026-09-25）

用户 2026-09-25（原文摘要）：

> **在生成前选内容、算预算；装不下就停在规划层。**但"计划是唯一来源"应收窄为
> "**计划是本集内容取舍和预算的权威**"——小说原文仍是事实与台词的依据。
> 1. 大纲标明必保留事实及证据、选用的逐字台词、可省略内容和事件顺序；**时长由代码用现有
>    估算器计算，不采信模型自报秒数**。对白与可同时发生的画面动作**不能简单重复相加**。
> 2. 若必保留内容的**估算下界已超过 90 秒**，报告"本集规划不可行／需拆集"，**不生成完整稿**，
>    也不靠加快语速或删必保留事实过关。
> 3. **第二步只接计划与生成前校验**；拆镜／重生仍以初稿为验收基线的问题留给第三步。

## 改了什么

| 文件 | 改动 |
|---|---|
| `step01_script_gen/plan_feasibility.py`（新增） | `evaluate_plan(plan)`：逐句用 `timecode._estimate_duration` 算对白估时；节拍下限 = `len(shot_beats) × MIN_SHOT_DURATION`；**下界 = max(对白, 节拍下限)，不做加法**；判据「下界 > 预算上限（90s）⇒ 不可行」。`check_plan_before_generation(plan, chapter_dir=…)`：不可行/不可评估 → 写诊断 `step01_plan_infeasible.json` 并抛 `PlanInfeasibleError`；可行 → 返回报告并记一行 `[PLAN]`（对白句数/估时、节拍数/下限、下界、预算）。预算区间读 `validation._REDGUO_DURATION_MIN/MAX`（唯一事实源，不硬编码）。 |
| `step01_script_gen/main.py` | 生成前先过这道门——不可行就停在规划层。**位置口径已更正（同日收口）**：门挂在候选扇出入口 `_generate_script_candidates()`，在**完整分镜生成之前**（不是"任何模型调用之前"——角色/场景物体/大纲三个提取模型此前已并发调用过）。详见 `step01-plan-boundary-closure-2026-09-25.md`。 |
| `step01_script_gen/outline.py` | 输出格式与规则新增 `dialogue_lines`（**逐字**摘出、宁少勿滥）、`omittable`（可省略内容，重选池）；明确 `main_events` = **事件顺序**；`time_budget` 只写分配倾向、**不必报精确秒数**（自报秒数不参与判定），并提示对白与同时发生的动作不要重复计时。 |
| `_text_gen/deepseek_schema.py` | `OUTLINE_SCHEMA` 增 `dialogue_lines` / `omittable`（进 `required`，`additionalProperties` 仍 False）。缓存键含该 schema → 旧大纲缓存自动失效，下次运行按新契约重出计划。 |
| `tests/step01_script_gen/test_plan_feasibility.py`（新增 7 条） | 代码算时长（计划自称"对白约30秒"被忽略，实测判不可行）；max≠和（57s 对白 + 60s 节拍下限 → 可行；若求和 117s 会误判不可行）；低于下限只报不判不可行；**不可行 → 服务根本没被创建**（`create_service` 被换成"一调用就炸"）+ 诊断产物 + 显式异常；只判定不改计划（输入快照比对 + suggestions 只给"重选/拆集"）；缺字段 fail-closed；schema 与门对得上。 |
| `tests/step01_script_gen/test_plan_to_commit_baseline.py` | 基线第 2 条按落地顺序**翻新**为 `test_the_plan_now_carries_what_the_gate_needs`（旧读数作为历史留档保留在同一用例里）。 |

口径决定（阶段边界内自行决定，已按准则 3 执行）：

- 下界取 **max 不取和** —— 对白常发生在动作镜里，两者可同时发生；加法值只作诊断字段
  `additive_sum_sec`，**不作判据**。
- **缺字段 fail-closed**（不可评估即停）——不带着未验算的计划生成完整稿；`dialogue_lines`
  在 schema 里是 `required`，正常路径不会缺。
- 门**只判定、不改计划**：不加速语速、不删必保留事实、不产出"精简稿"（suggestions 只给
  "按 `omittable` 重选"或"报告需拆集"）。
- `time_budget` 字段保留（仍是 schema 要求），但降级为**倾向性文字**，不参与判定。

## 为什么

基线读数（`docs/step01-plan-infeasibility-baseline-2026-09-25.md`）：失败稿的对白镜已占
**91.03s**（> 90s 上限），而计划里 `time_budget` 自称"对白约30秒"——**自报秒数与实测差 3 倍**，
说明"生成前先算"必须由代码做；同时这份稿是**在生成阶段**才被发现超时的，等于把规划层的
取舍问题推给了下游修复与选稿（选稿因此一直偏向 73.02s 的旧快照）。这道门把该决定挪回规划层。

## 影响面（**这是本阶段授权的生产行为变化**）

- 计划不可行/不可评估时：**整章运行显式停止**，不生成完整稿、不写正式三件套，
  只留 `step01_plan_infeasible.json` 诊断（准则 7：失败必须看得见）。
- `outline` 契约变更 → 旧大纲缓存失效，下次运行会重新规划（多一次大纲调用，本就有）。
- 不再产生"注定超时的完整稿"的生成成本；无新增门禁、无容量改动、无修复轮改动。
- 拆镜/重生验收基线**未动**（仍取初稿）——那是第三步。

## 实跑测试数字与回退

- `pytest -q` → **2297 passed, 1 skipped**（本步新增 7 条；基线第 2 条翻新）。
- 真实数据复现：用失败稿 `184302` 的逐字对白过门 → 对白估时 **90.62s**、判 `infeasible`
  （对白镜 `duration_sec` 和 91.03s，两者都超上限）。
- 回退：删除 `plan_feasibility.py` 与对应测试、还原 `main.py` 里那 3 行调用、还原
  `OUTLINE_SCHEMA`/`OUTLINE_PROMPT` 的两个字段与规则、还原基线第 2 条。

## 仍未做（第三步）

生成/拆镜/结构收敛**各记录同一组数**（现在只在生成前记一次 `[PLAN]`）；拆镜与重生的验收
基线从"初稿"改为"已验证计划"；跨镜道具欠账分流为"需重新规划／生成"；选稿兜底替换。
