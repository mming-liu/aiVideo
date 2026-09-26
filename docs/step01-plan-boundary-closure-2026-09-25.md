# 第二步收口：计划边界（只验算一次 / 缺计划即停 / 原文核验 / 选中内容进提示）2026-09-25

用户 2026-09-25 审核结论（原文摘要）：

> **第二步还不能算闭环。**方向是对的，但"计划约束生成"的关键路径有三处断点：
> 1. `PlanInfeasibleError` 继承 `RuntimeError`，而候选层把它当作生成失败重试；多候选路径也会
>    重复处理。结果不是报告所说的"立即停在规划层"。应在**候选扇出前只验算一次**，并让该异常
>    直接终止，不进入生成重试。
> 2. 生成入口只有 `outline` 非空才过门；大纲提取失败或关闭时仍可生成。若生成前计划是必需契约，
>    **缺计划必须明确停止**。报告中的"任何模型调用之前"也应改为"完整分镜生成之前"。
> 3. 新增的 `dialogue_lines` 只参与算时，没有进入 `outline_to_anchor`，因此生成模型仍可选择别的
>    台词；校验器也未核实它是否逐字来自原文；可行性判定允许"空台词＋非空节拍"通过。应在**同一个
>    计划边界**完成原文核验、预算核算，并将选中内容传入**所有完整稿生成入口**。
> 另有一处准则冲突：第 4 条允许已授权目标下的复跑不再确认，但 Step 02b 原则是每次付费视频运行
> 都要人工确认；实施者实际读的是 CLAUDE.md，需要同步写明这个例外。
> 建议把以上作为第二步的收口验收，**加主流程级判别测试**（缺计划、不可行时零次候选重试、选中台词
> 确实进入提示），**不要另加修复轮或新判据**。当前回归与夹具仍未被 Git 跟踪；"可跟踪"不等于"已交付"。

## 改了什么

| 文件 | 改动 |
|---|---|
| `step01_script_gen/plan_feasibility.py` | ①`PlanInfeasibleError` 改为继承 `Exception`（**刻意不是 `RuntimeError`**）+ 类注释说明理由；②新增原文核验 `_source_norm` / `source_contains` / `verify_dialogue_lines`（整句包含；容忍"说话人前缀"，与 `_check_grounding` 同源宽容）；③`evaluate_plan(plan, *, novel_text=…)`：缺计划/缺字段/**缺原文**/核验后无台词 → 一律 `unevaluable`（fail-closed）；预算只按核验通过的那一组算；④`check_plan_before_generation()` 是**唯一改计划**的地方（把 `dialogue_lines` 收窄为核验通过的集合，仅当可评估时；不可行时一个字都不动）；⑤诊断 `step01_plan_infeasible.json` 增 `dialogue_lines`/`dialogue_sec`/`dialogue_rejected`。 |
| `step01_script_gen/main.py` | ①**门从 `gen_spy_script()` 上移到候选扇出入口 `_generate_script_candidates()`**（函数据此在扇出前验算一次）；②`_regen_over_limit()` 新增 `outline_anchor` 形参并传进 prompt——它此前**完全不带大纲锚点**（超限收敛等于绕开计划另写一版）；③四处锚点渲染改传 `novel_text`（`outline_to_anchor(outline, novel_text)`）；④注释口径更正："任何模型调用之前"→"完整分镜生成之前"。 |
| `step01_script_gen/outline.py` | ①新增 `_check_outline_dialogue_grounding()` 并在 `_validate_outline(outline, novel_text="")` 末尾调用——**复用** `plan_feasibility.verify_dialogue_lines`，不重写判据；②`outline_to_anchor(outline, novel_text="")` 透传；③锚点新增 `【选用台词·逐字，本集只拍这些】` 段（含"不得替换或另编；确需增减回到规划层"）；④`dialogue_lines`/`omittable` 只净化不判空（判空留给门，避免两个判据）。 |
| `CLAUDE.md` | 准则 4 增**例外**：step02a/step02b 的**每次**付费生成运行都要人工确认，即使同一已授权目标下也不复用上一次的许可（"已授权"针对 step01 这类按整轮计的运行）。 |
| `.gitignore` | 第二步的判别测试 `tests/step01_script_gen/test_plan_feasibility.py` 加入既有定向例外（随仓库交付）。 |
| `tests/step01_script_gen/test_plan_feasibility.py` | 7 条 → **20 条**：新增主流程级判别（不可行零候选零重试、缺计划即停、老格式即停、选中台词进初始生成提示、选中台词进超限收敛提示、四个完整稿入口接线）、原文核验（逐字/前缀宽容/全灭/空台词不再放行/省略号折叠与 `_check_grounding` 一致/字段非 list）、边界只收窄不改计划。 |
| `tests/step01_script_gen/test_candidate_parallel.py` / `test_relay_repair_model.py` / `test_structural_convergence.py` | 驱动候选扇出机制的 10 条用例补一份**真实可验算的计划**（门要求"计划可验算"，缺计划即停）。 |
| `tests/step01_script_gen/test_outline_budget_and_repair_prompt.py` | 锚点复用 pin 更新为带 `novel_text` 的形态（同一处核验）。 |
| `docs/step01-plan-feasibility-gate-2026-09-25.md` | 更正"在任何模型调用之前"的过度表述（门在**完整分镜生成之前**）。 |

## 为什么（三处断点是实测复现的，不是推测）

收口前逐条复现（`python - <<...`，读数如下）：

1. `issubclass(PlanInfeasibleError, RuntimeError) = True`，`main.py` 里 `except RuntimeError as e:`
   共 **2** 处（TOPK=1 串行重试 / TOPK>1 并行重试）——异常确实会被"生成失败→重试"分支接住。
2. 生成入口在 `if outline:` 内，`OUTLINE_ENABLED=0` 或大纲提取失败时**照生成**。
3. `evaluate_plan({"dialogue_lines": [], "shot_beats": ["b"]})` → 收口前返回
   `verdict=feasible / evaluable=True / 下界 3.0s`；且 `outline_to_anchor(带 dialogue_lines 的大纲)`
   **不含**选用台词（`'【选用台词' in anchor == False`）。

修法按用户指定边界（不加修复轮、不加新判据）：

- **异常基类**：`Exception` 而非 `RuntimeError`——候选循环的 `except RuntimeError` 是"生成失败→
  换候选/再试"的语义，计划不可行不属于该语义。这一条同时让"零候选重试"变成**结构性保证**，
  不依赖调用顺序。
- **门的落点**：候选扇出入口是本轮**唯一**验算点。放 `gen_spy_script()` 会每个候选重算一遍
  （且被重试分支接住），放 `main()` 调用点则会被任何未来直接调用扇出的代码绕过。
- **原文核验的宽容边界**：实测第 1 章原文 + 失败稿的 12 句 `narration_segment`——
  **不容忍说话人前缀 → 12 句全灭**（`张十三道：` 这类前缀是生成层按规则0补的，不是原文文字），
  容忍前缀 → **命中 11/12**，剩下 1 句正是被改写/拼接过的台词。所以宽容前缀、不放宽容台词体；
  两种情形都进了用例。
- **"只剔除、不为预算删内容"**：核验剔除的是"原文里根本没有的台词"；预算不够走 `infeasible`
  分支且**不动计划**（用例 `test_boundary_narrows_the_plan_to_verified_lines_only` 钉住）。

## 影响面（生产行为变化，均在本阶段授权边界内）

- **计划缺失/不可评估/不可行 ⇒ 整章运行显式停止**：`[FATAL]` + `step01_plan_infeasible.json`
  + 退出码 **1**；不生成完整稿、不写正式三件套、不产生候选。
- `OUTLINE_ENABLED=0` 从此**不是可用配置**（计划是必需契约）：会停在"计划缺失"。
- 大纲缓存因 schema 变更失效 → 下一次运行**重新规划**（一次大纲调用，本就在流程里）。
- 四个完整稿生成入口（初始候选 / GAP 重生 / 结构收敛 / 超限收敛）现在都带**同一份**核验过的计划。
- 无门禁容量改动、无修复轮改动、无新增判据；`prop_anchor_block` 开关状态不变（默认关闭）。

## 实跑测试数字与回退

- `pytest -q` → **2310 passed, 1 skipped**（本步 7→20 条；另有 4 个既有测试文件按新契约补计划/更新 pin）。
- **真实运行一次（2026-09-25 20:06，`python step01_script_gen/main.py`，退出码 1）**：
  - 大纲缓存失效并重出计划 → 新大纲含 `dialogue_lines` 13 句 / `omittable` 4 条 / `shot_beats` 8 个。
  - 门读数：`[PLAN] 对白 12 句/估时 100.44s（逐字核验剔除 1 句）；节拍 8 个/下限 24.00s；
    下界 100.44s；预算 60–90s` → `infeasible`。
  - 剔除的那 1 句：`没什么事……公子爷吃醉了酒，险些跌落水中，所以惊得听香姑娘尖叫起来。`
    （原文中未逐字找到）。
  - **门之后 0 次候选/生成调用**；`script_result.json` 仍为 09-24 08:11（**未动**）；
    诊断 `step01_plan_infeasible.json` 已写；退出码 1。
  - ⇒ **本集（第 1 章）按当前预算不可行，需要拆集或按 `omittable` 重选**。这是本轮
    最重要的读数：此前表现为"生成稿 120–127s 超时"的问题，**在规划层就能看到**——模型自己
    逐字选出的必拍对白已占 100.44s，超出 90s 上限 10.44s（约 3 句）。
- 回退：`plan_feasibility.py` 恢复为上一版（含 `RuntimeError` 基类、无核验）、`main.py` 把门放回
  `gen_spy_script()` 并去掉 `_regen_over_limit` 的 `outline_anchor`、`outline.py` 去掉
  `_check_outline_dialogue_grounding` 与锚点新段、`.gitignore` 去掉一行、测试文件回退。
  另需还原 `CLAUDE.md` 准则 4 的例外段（若用户另有决定）。

## 待用户决定（不在本阶段边界内）

> **2026-09-25 当日后续**：用户选了 **①重新规划一次内容取舍**（不判拆集）。已按该决定实现
> 「一次有数值反馈的规划重选」+ 规划契约补预算 + 跨模型锚点收口，见
> `step01-plan-reselection-and-cross-model-anchor-2026-09-25.md`（真实规划阶段读数：新计划
> 7 句 / 54.74s → `feasible`）。本文件下面第 1 条由此关闭，第 2、3 条仍有效。

1. **本集规划不可行怎么办**：①按 `omittable`（4 条）重选后重新规划；②报告本集需拆集；
   ③其他。**不自动做**——加"按预算重选"的规划轮属于新增修复轮，用户明确要求不要加。
2. **交付**：夹具与回归仍**未被 Git 跟踪**（已可跟踪，未 commit）。提交需用户确认（准则 5）。
3. **尚未实跑验证**：模型是否稳定按 `【选用台词·逐字，本集只拍这些】` 只用选中台词（本轮因计划
   不可行，未进入生成阶段，故无从观察）。
