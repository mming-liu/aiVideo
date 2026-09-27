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


## 增量（2026-09-26，工作包 sunny-kindling-wilkinson）：候选层台词完整性硬门

**背景**：09-26 02:30 重跑（agent_plan / deepseek-v4-flash 单模型）首次出现"新稿进提交门
后死锁"——选材台词被拆镜截断（shot12 "…有些生意上的事情急需" 句中碎片），审计报
`truncated_text`，唯一正确修复字段是 `narration_segment`（锁定字段）→ `locked_changed`
拒收 → 零改善 BLOCK。缺陷的 curing 点放错了层：完整性必须在候选层把关，而不是靠提交门
的审计"发现"。

**落点**（对本文档边界的增量，不另起文档）：

- `plan_feasibility.check_script_dialogue_integrity(script, plan)`：确定性、零模型调用。
  命中 = 台词逐字在**单个**镜头旁白里（说话人前缀宽容 + 合规替换还原，均与既有实现同源，
  还原共享落点 `shared.content_safety.restore_narration_substitutions`）；跨镜拼接仅在
  「起点对齐 + 参审镜头全部句末完整 + 末镜可只消费前缀」时命中——**句末完整性约束是命门**：
  纯整稿拼接判据会把 09-26 死锁稿放行（干跑实测 0/7 破损），实装判据拦下 2/7（连人工核对
  漏掉的听香句碎片一并抓住）。
- 三个接入点：候选层（`_gen_one_candidate`，破损候选不进池、消耗当轮名额、不补取）；
  终稿层（拆分/finalize 之后、BEST-GUARD 之前——碎片可能由定向拆分产生，只查候选会漏）；
  提交层辅门（`_commit_step01_outputs` finalize 后，破损 → BLOCK `dialogue_truncated`，
  零审计/修复调用；**无台词计划时门不启用**——历史模式/旧缓存无契约可核，生产三入口
  计划均非空）。BEST-GUARD 历史快照按**本轮计划**同判据过滤（快照生成本轮计划之外，
  实测 09-24 100 分快照 6/7 句缺失 → 排除）。
- 候选池全破损 → `DialogueIncompleteError`（不继承 RuntimeError）+ 诊断
  `step01_plan_infeasible.json` 的 `conclusion=dialogue_incomplete`（独立结论，不得触发
  计划重选语义——审核决策点 2）。用户决策点 1 采纳：全破损直接失败，不回退"最少破损"。

**验收**：`pytest -q` → **2380 passed, 1 skipped**（基线 2367；本包 +13：门单测 9 条含
09-26/09-25 实损反例、贯通测试 4 条；受影响机制测试夹具按 v6.131 先例改为与计划自洽）。
只读探针 `.tmp/dialogue_integrity_probe.py`：A·09-26 死锁稿 2/7 破损被拦、B·09-25 失败稿
5/7 破损被拦、C·09-24 100 分快照 6/7 缺失被拦（历史快照门控的实际后果）。
**未实跑验证**：下一次真实运行应出现 `dialogue integrity failed` 而非 `locked_changed`。

### 二审收口（2026-09-26 落码审核，同日完成）

- **预算语义定稿**：破损候选不占合格名额，差额在既有 `_max_attempts` 预算内继续补取（与解析失败
  同一预算池）；预算用尽按实际合格数收池；全部破损才显式失败。统一版计划 §2.2 与代码注释已对齐。
- **对外契约（勿当 bug 回退）**：`check_script_dialogue_integrity` 对空 `dialogue_lines` 返回
  fail-closed（门函数语义）；**提交层调用点对「无台词计划」不启用门**——历史模式/旧缓存无契约可核，
  无计划生成已在候选层上游被计划门拦死。
- **历史快照门控口径**：按**本轮计划**同判据，计划版本切换后旧快照被批量排除属必然行为
  （09-24 100 分快照实测 6/7 缺失）；排除由
  `[BEST-GUARD] 台词完整性门排除 N 份历史快照` 日志留痕。
- **匹配方向重定（三审 P1-2 定稿）**：合规还原（safe→orig）对"原词与安全词同句"本质歧义
  （计划"拿春药，再去抓药。"整段还原成"拿春药，再去抓春药。"，两侧都对不上），二审的
  "未还原+已还原双形态"同样救不了。门匹配改为**净化方向**：计划句与稿件**同过
  `sanitize_narration`**（orig→safe，正是产出稿件文本的确定性函数，幂等）再比对，替换
  永不错位；原样形态保留给两侧都未净化的路径。`restore_narration_substitutions` 仍归
  `_check_grounding` 使用（其 5 字滑窗容忍单字 safe 歧义——"春药/媚药→药"的词表设计缺陷
  修它属另一工作包）。
- **边界固化**：逗号/顿号结尾的分句跨镜 = 破损（有意从严）。
- 新增回归 5 条（快照门贯通、终稿门全句复刻、双形态边界 3 例+逗号边界），`pytest -q` → **2384 passed, 1 skipped**。

### 三审收口（2026-09-26 落码审核第二轮，同日完成）

- **P1-1 门异常 fail-closed**：提交层门自身异常不再"warn 后放行"——无法证明台词完整即
  **BLOCK**（`repair_outcome=dialogue_truncated`，detail 带 "fail-closed" 与异常原文，
  零审计/修复调用）。缓存路径只靠本门复核，放行 = 门失效时裸奔。
- **P1-2 匹配方向重定**：见上（sanitize 方向替代还原双形态）；审核原例
  "计划「拿春药，再去抓药。」/ 稿件「拿药，再去抓药。」"回归钉住。
- **P2-1 缓存入口口径更正**：原"生产三入口计划均非空"**不实**——缓存入口的旧缓存可能没有
  `chapter_outline`，此时门不启用（兼容取舍，显式记录），这类稿件**不受台词门保护**；
  要补保护需先给缓存路径接计划来源（另一工作包）。
- **P2-2 混合失败分开计数**：无合格候选时按尝试构成报告（`N 次尝试中 X 次台词破损、
  Y 次生成/解析失败`），诊断带 `attempt_stats`；不再把混合失败写成"全部破损"。
- 新增回归 3 条（审核原例同句共存、门异常 fail-closed、混合失败计数），`pytest -q` →
  **2387 passed, 1 skipped**。

### 四审收口（2026-09-26 落码审核第三轮 + 实跑后统一排查，同日完成）

**审核两点（已修）**：

1. **P1 净化异常静默降级**：`_match_forms` 原实现 `except` 吞掉 `sanitize_narration`
   异常、退回原样匹配——"两侧原样恰好相同"的稿子在净化器故障时照样过门，提交层
   fail-closed 分支收不到任何错误（硬门在自身故障时静默放行 = 门失效）。修复：
   门内异常**向外传播**；四个调用方各自 fail-closed 接住且严格分账——
   候选层新增 `_CandidateGateError`（判无效不进池、名额消耗，attempt_stats 记
   `gate_errors` 桶，不混入 `dialogue_broken`）；终稿层转显式
   `DialogueIncompleteError`（不裸崩）；BEST-GUARD 快照过滤排除该快照并留痕；
   提交层走既有 fail-closed BLOCK。回归 4 条：门单元级 raise（两侧原样相同
   反例）、净化函数抛错→提交 BLOCK 端到端、候选层分账、快照排除。
2. **P2 归因口径**：门自身异常原与稿件截断共用 `repair_outcome=dialogue_truncated`
   ——结构化消费者会把检查故障统计进"截断率"。修复：独立代码
   `dialogue_gate_error`（`repair_outcome` 与 `blocking[0].code` 同步），
   `detail` 继续携带异常原文与 fail-closed 标记。

**判据边界（四审记录，有意取舍，非缺陷）**：门契约 =「净化后文本一致」。
净化后同形为同一安全词的原词（春药/媚药 → 药）有意不可区分——正式产物只含
净化后文本，门保证的是"成片将呈现的台词与计划一致"，不是原词逐字一致。
（写入 `_match_forms` docstring。）

**实跑验证（同日 05:54 run）**：门系统按设计工作——候选层门放行完整候选 →
定向拆分（逗号切点）→ 终稿门拦 2/7（cross_shot_split）→ 6 分钟显式失败
（verdict=dialogue_incomplete 落盘，零审计、无 locked_changed）。暴露上游
结构性矛盾（**未修，待决策**）：计划选中超长**单句**台词（64字/84字）在
"单镜 12s × 句末完整性"下不可表示——整装装不下、拆开必判破损。统一排查：
第 1 章台词面 23% 超限（12% 不可表示）、全书 18% 不可表示；拆分器不偏好句末
切点使本可合法拆的台词（省略号切点存在）也被判死。修法建议：计划层
「单句台词可表示性」检查（落点 `evaluate_plan`，根治）+ 拆分器句末优先
（救回可拆类）；剔除/改写超长台词属内容取舍，交用户决定。

测试：**2391 passed, 1 skipped**（基线 2387 + 4）。

### 五审收口（2026-09-26 第四轮复核：顶层归因漏口，同日完成）

**审核点（已修）**：四审后纯门故障路径仍把检查故障归为稿件不完整——候选层池末
与终稿门的诊断固定写 `conclusion=dialogue_incomplete`、异常类型也是
`DialogueIncompleteError`。修复（顶层结论随实际失败类型区分）：

1. `dump_dialogue_incomplete` 按批次**自动归类**：全部条目 `gate_error` →
   `conclusion/verdict=dialogue_gate_error`（note：稿件完整性**未被判定**，
   检查故障）；含真实破损（含**混合失败**）→ 仍 `dialogue_incomplete`，
   混合失败各类计数留在 `attempt_stats`（破损/门异常/生成解析失败分账）。
   新增 `layer` 参数修掉「终稿路径复用候选层 note 文案」的错标（前次 P3）。
2. 纯门故障抛新子类 `DialogueGateError(DialogueIncompleteError)`（候选层池末
   + 终稿门）——traceback/日志类名如实归因；父类可捕获性不变，
   `is_dialogue_verdict` 识别不受影响。

测试：新增 `test_dump_conclusion_follows_failure_type`（纯门故障/真实破损/
混合/layer 四态）+ `test_candidate_gate_error_counted_separately` 升级断言
（类型钉住 + conclusion）+ 混合测试补 conclusion 断言。
**2392 passed, 1 skipped**。

### A+B 实施（2026-09-26 用户决策：判据统一，暂不选 C）

**决策原文要点**：A 检查"能否按合法句末切成若干 ≤12s 镜头"，不是简单要求整条
≤12s（64 字听香台词按省略号切开 ≈5.85/5.85/3.75s，保留；84 字张十三台词句内
只有逗号、无合法切点，才在规划层重选）。B 让拆分器**只用同一套合法切点**
（逗号切点与等字长兜底废除）；无合格切法在**调用拆分模型之前**停下。
规划重选必须保留必保留事实与事件顺序，不得静默删掉肖管事传话；现有大纲缓存
同样重过可表示性检查；原文无合适替代时明确报告"规划无法满足当前单镜规则"，
内容取舍交用户。

**统一判据（单源：`plan_feasibility.find_legal_split` / `check_dialogue_representable`）**：
合法切点 = 句末边界（。！？…；，与台词完整性门 `_SENT_END_CHARS` 同集）；
台词可表示 ⇔ 整条（含说话人前缀，规划层按 5 字余量保守估计）装进单镜
`active_shot_cap()`，或存在 ≤4 段、每段 ≤ 上限的句末切分（同段数下最长段最短）。

- **A（规划层）**：`evaluate_plan` 逐句检查 `dialogue_verified`，不可表示句进
  `dialogue_unrepresentable`（结构化明细）⇒ verdict=infeasible → 既有重选轮
  （`enforce_budget_by_replanning`，缓存命中路径同样过闸）→ 反馈块
  `_PLAN_REPRESENTABILITY_FEEDBACK` 点名台词+估时+reason，硬边界：替换台词仍须
  逐字来自原文、必保留事实/事件顺序不得静默删除、无合适替代须明确写明（代码据此
  显式停止交用户取舍）；重选后仍不可行 → `PlanInfeasibleError`（teardown）。
  预算反馈块改为**只在真超预算时**给出（不撒"装不下"的谎）。
- **B（拆分器）**：`_deterministic_narration_cuts` 改为委托 `find_legal_split`；
  删除逗号切点、等字长兜底与 `_CUT_DELIMS`/`_cut_bounds`/`_segments_at`/
  `_narration_parts` 死代码；无句末合法切法 → None → `_scoped_split_one` 在
  调用拆分模型前停下（日志说明），交整脚本 regen／规划层。
- **缓存**：`generate_outline` 的重选闸本就覆盖缓存命中路径，缓存计划每轮重过
  新判据；实缓存离线验证 = infeasible（点名 84 字句，64 字句保留），反馈块
  渲染正确。

**已知残留（明示，不在本步范围）**：拆后正片时长曾达 96.5s 超 90s 上限、约束
HARD 未解决等提交面问题仍在下游门处理——A+B 只解决台词死锁，不保证下次提交通过。

测试：判据单测（64 字保留/84 字拒绝/整条装得下返 None）+ evaluate_plan 接入
（infeasible+结构化明细/多句长台词不误伤）+ 重选反馈（点名/硬边界/预算块不撒谎）
+ 重选贯通（换合法台词后采纳）；拆分器夹具改句号分句、等字长兜底测试反转为拒绝。
**2399 passed, 1 skipped**。

### 六审收口（2026-09-26 第五轮复核：A+B 实施后的四处修正，同日完成）

审核确认 A+B 接入位置正确，但指出三处 P1 一处 P2 会影响真实缓存计划的重选，
**修复前不建议重跑**（已照办——本轮未发起任何模型调用）：

1. **P1 反馈缺旧计划**：可表示性单独触发时（预算内）预算块不渲染，旧计划 JSON
   只在预算块里——模型看不到旧字段却被要求"其余字段原样返回"，重选必被
   `_plan_core_preserved` 拒收。修复：`_budget_feedback_block` 拼装后**统一在
   尾部附完整旧计划**（两类反馈都带）；预算模板里的 `{plan_json}` 移除。
2. **P1 "无合适替代"无代码出口**：提示要求模型在原文无更短替代时在
   `time_budget` 明写标记，但代码不读——模型删长台词+写标记后 rep2 会被判
   feasible 放行（必保留事实被静默丢弃）。修复：`evaluate_plan` 识别标记 ⇒
   infeasible（reason 点名"须内容取舍，代码不代决"）——即使其余台词可表示、
   预算可行；标记随缓存持久化，后续每轮都在本门显式停止。
3. **P1 切分搜索非完整**：>14 切点截窗会漏掉用被裁切点的合法解 → 误报
   "不可表示"，直接驱动重选/停止。修复：**完整动态规划**精确求解
   （`_est_ij` 缓存，≤4 段），与穷举参考在可行性/最少段数/最长段上一致
   （测试用 21 切点种子化台词钉住）。
4. **P2 切点与择优**：连续句末标点串（`……`）只在**串尾**切，不在串中间切；
   择优改**最少段数优先**（两段合法不选三段）、同段数内最长段最短（DP 内建）。

实施中自测另抓到并修掉两个 DP 真 bug（写入时序）：pts 漏正文末尾 L（末段被
截短、估时偏小、可能误判可行）；选解行索引错位（rows[k-1] 实为 D(k,·)，返回
切分与 DP 评估解不一致）。修后 DP 与穷举参考在 64 字台词三个 cap 下全部一致
（cap12/10 → 两段 max 8.15s；cap8 → 三段 max 5.85s），拼接逐字还原。

测试：+3（最少段数+标点串边界 / 21 切点 DP 精确性 / 标记出口）+1 更新
（反馈块断言旧计划在提示内）。**2402 passed, 1 skipped**。

### 七审收口 + 修复轮重试两次实跑（2026-09-26，同日）

**第六轮实跑（18:10）暴露重试接线缺口**：内层 continue 在提交路径 `max_rounds=1`
下装不下第二轮——「带反馈重试」打了日志就返回，提交层仍零改善 BLOCK。修复：
`_repair_shot_warnings` 增加 `retry_state` 进/出参（入口预载剔除+反馈并消费；
new_blocking 终拒时导出），提交层零改善分支改为「有待处理重试且
`_attempt+1 < STEP01_AUDIT_REPAIR_MAX_ROUNDS` → continue 到下一次 attempt」
（审计经 `_reusable_final_audit` 同 revision 复用，零额外审计调用；总修复调用
数仍 ≤ §1.1 的 2 轮）。配套：audit 问题在重试轮继续合并（否则重试轮 picked
为空空转）；`_dec_try` 回退重试失败原因入日志（此前无从归因）。
测试：内层重试（max_rounds=2）+ 跨调用契约（max_rounds=1 两次调用）各一条，
含「证据必须在基线稿里」的夹具校正（v6.108 兜底口径会吃掉区分度）。

**第七次实跑（18:24，机制层全绿）**：缓存计划直接复用（重选零成本）→ 候选
100 分 → shot8（64 字台词）句末切 2 段 → 台词门全过 → BEST-GUARD 台词门排除
22 份历史快照（计划重选的预期后果）→ **修复轮 1：accepted resolved=4 触达=3
new=0；修复轮 2：accepted resolved=6 触达=1 new=0**（连续两轮真实净改善、
零新阻塞——零改善死路消除）→ 第 3 轮复审计仍有 13 条 blocking（审计 3 +
确定性 10，从 24 收敛而来）→ **§1.1 两轮预算上限显式 BLOCK**（三件套保持
09-24；欠账台账同时登记原稿与修后稿 revision）。机制层无新问题；剩余是
收敛预算/速度的用户决策项（2 轮不够从 24 收敛到 0）。

### 八审实跑（2026-09-26 18:39，预算 2→4 后首轮）：跨 run 收敛棘轮开始工作

`STEP01_AUDIT_REPAIR_MAX_ROUNDS` 2→4（v6.137 后每轮有验收闸且实测连续净改善，
2 轮实测 24→21→13 不够收敛；4 轮成本 ≈ +5 分钟/次）。本轮实跑：
- **BEST-GUARD 选中上一 run 的修后稿**（同为 100 分、风险 10 < 新稿 28，
  `script_result_20260926_183146.json`）——上一轮修复成果经快照进入本轮起点，
  **跨 run 收敛棘轮成立**；台词门正确放行该新世代快照（基于重选后计划）、
  继续排除 20 份旧世代破损稿。
- 修复轮 1 accepted（resolved=2，13→11）；轮 2 accepted（触达=2，11→10）；
  轮 3 的 5 镜联动组（climax 段 shot 8-12）补丁零可验证解决 → `no_target_resolved`
  回退 → 零改善止损（预算有余额，但同目标重发无意义）。
- 终态：10 条 blocking（确定性 7 + 审计 3），三件套保持 09-24。剩余是 climax 段
  语义/时序硬尾巴（DIALOGUE_ORDER_REVERSED / ONSCREEN_CONTRADICTION /
  TIMELINE_CONFLICT 等），修复 LLM 跨 run 未能量产可验证解决——属修复模型能力/
  审计判据校准问题，非管线缺陷。选项：relay 互补模型修复（v6.67 待办）/
  内容层裁决 / 继续 run 让棘轮收敛。

### 九审（2026-09-26 晚）：三根因定案 + 事件履约契约（v6.138）落地

八审后「剩余 10 条 = 修复模型能力天花板」的结论被九审驳回，三条根因链经代码+产物
独立复核**全部实证**：

1. **上游缺事件履约约束**：入选快照 shot8 在供述台词进行中插入「再度尖叫」（修复
   补丁引入，183146 motion 6-8.12 秒段）；replan 压缩预算 54.74s→35.15s（7 句→6 句）
   删掉「恐吓听香」「赶车命令」的承载台词 → 节拍无承载（MISSING_PLOT/MISSING_ACTION
   结构性必然），而修复轮不许改台词 → 死锁。
2. **修复验收把「动过审计字段」当进展**：`_audit_issue_newly_resolved` 的 v6.108
   触达兜底 + 接受条件 `resolved=0 or audit_field_touched>0`（main.py 4738）——
   18:46 run 台账第 21 条 `accepted=True, resolved=0` 实锤；触达证明尝试、不证明
   语义解决。
3. **局部失败升级全局停机**：零改善分支（main.py 2887 `break`）在 5 镜联动组
   `no_target_resolved` 后立即停止，shot 2/6/7/13 的独立 blocking 还有预算没用。

#### v6.138 实施（九审修复 1 + 九审四缺口，2026-09-26）

- **事件契约**：`shot_beats` 条目末尾标「｜载体：台词k/画面」；`render_execution_contract`
  新增第四节（B1..Bn 编号，不压缩）+ 第五节（`plan_refs` 履约声明规则）；shot schema
  （deepseek `_SHOT` required / doubao RULE-31 / main.md 骨架）每镜输出
  `plan_refs={beats,lines}`；`plan_feasibility.check_event_contract` 在候选入池前核验
  **覆盖/顺序（B、D 序列按镜序非降）/越界**，旧稿整稿无声明容忍（`declared=False`，
  快照线不灭）；拆分子镜继承父镜 refs；候选显式失败 `EventContractBrokenError` +
  `attempt_stats.contract_broken` 独立分账。
- **载体悬空**（九审修复 1 规划侧）：`evaluate_plan` 判「载体：台词k」指向已删台词
  → infeasible → 重选反馈（`_PLAN_CARRIER_FEEDBACK`，只许改载体段）；`_plan_core_preserved`
  放行纯载体差异；「载体缺失：无合适承载」= 显式停止交用户（代码不代决）。
- **九审 P1-2 排除前置**：`_pick_repair_targets(excluded=)` 先过滤再填名额；对镜补选、
  联动组扩选同一排除约束（组含肇事目标 → 整组跳过）；重试排除集只用**真实肇事目标**
  （`_offend_raw`），整组回退不再吃掉非肇事成员的重试资格。
- **九审 P1-3 冒号前缀**：`_is_speaker_prefix_head` 单源判据（≤12 字 + 无子句/句末
  标点）；`verify_dialogue_lines` / `_split_speaker_prefix` / `_speaker_body` /
  main.py `_narration_body` 统一走它——「且慢，我告诉你：你先走。」不再凭冒号后半句
  通过原文核验。
- **九审 P2-4 排他性**：台词门新增 `extra_dialogue`——带说话人前缀的旁白若与全部
  计划句无包含关系（整句在镜内或跨镜碎片）⇒ 稿件另编了计划外对白，判破损；
  无前缀旁白（描述/画外音）不拦。现役三份稿（184215 新候选 / 183146 入选快照 /
  182620 候选）离线核验全部通过，棘轮快照线不受影响。
- **引号切点边界**：`find_legal_split` 句末串后紧随的收尾符（闭引号/括号/空白）由
  前段一并消费——切点不再落在 `！` 与 `”` 之间。
- 测试：`test_event_contract.py`（22 项）+ rollback 组跳过 harness 用例；全量
  **2412 passed**（基线 2404）。

### v6.138 首跑（20:25）与二次事故：大纲缓存重生成坏抽样（v6.138.1 防护）

- **20:25 run**：outline prompt 变更 → 缓存键（含 prompt）失效 → 大纲重生成出
  **坏抽样**（6 句台词→2 句、7 节拍）→ 成片 38s 撞 duration_budget 硬门；且计划一换，
  全部历史快照因选材门失格（新计划下 6 句稿的 4 句成 extra_dialogue）→ 棘轮归零。
  提交点另暴露既有审计契约缺口（审计声明 `kfs[0][1]` 目标不存在 → 「缺少可执行
  修复目标」直接 BLOCK）。
- **处置**：从 183146 快照内嵌的 `chapter_outline` 恢复良计划（6 句/35.15s，写回
  新缓存键）；坏计划留 `.bak_sparse_20260926`。新增
  `_rotate_outline_cache_on_regen`：**重生成覆盖缓存前旧计划留 .bak（单文件轮换）
  + 选材退化告警**（6→2 句实测形态），退化可见、可恢复，是否采用新计划仍由
  运行者裁决（内容取舍不代决）。
- **恢复后首跑（20:49）**：候选 100 分/63s；修复轮 **21→10→6→3 全部真实解决**
  （resolved=14/10/2/2/1，无一轮靠触达兜底——排除前置+名额补齐生效）；
  第 5 轮审计回归 7 条（纯审计，确定性清零）→ 预算上限 BLOCK。同一下层问题
  四轮换名（FUTURE_STATE→FUTURE_STATE_IN_FF→result_shown_in_ff→
  FUTURE_STATE_IN_PRE_FRAME）+ 触达补丁引入新语义问题（步摇/船桨）——正是
  修复 2 的靶子。

### 修复 2（v6.139）+ 修复 3（v6.140）实施（2026-09-26 晚）

- **v6.139 触达兜底单次记账**：新增 `_audit_issue_identity(issue)` = `(shot, family)`
  跨轮稳定身份（`_AUDIT_FAMILY_PATTERNS` 归并措辞漂移）；`_audit_issue_newly_resolved`
  的 v6.108 兜底对**已尝试过的身份不再给分**——要么真解决（证据判据不受影响），
  要么如实 `no_target_resolved`。已尝试集合跨 commit attempt 持久
  （`_audit_attempted`），只记**落定生效**的触达（回退轮不消耗名额）。
  台账新增 `audit_field_touched`/`audit_touched_ids`；提交循环每轮输出
  **审计身份比对**（仍在/消失/新现，措辞漂移按族归并）。
- **v6.140 止损局部化**：提交层零解决且失败者是**真联动组**（≥2 成员）→ 组整组
  **暂缓**（`_deferred_targets`，本 run 后续轮次不选），组外目标继续用剩余预算；
  组的 blocking 保留、照常 BLOCK（不做半条修复）。`_repair_shot_warnings` 新增
  `defer_targets`，与肇事剔除集合同一过滤口径（取名额/对镜补选/组扩选三处）。
  ⚠️ 单测实锤：合并集必须**每轮重算**，循环外快照会让重试剔除静默失效。
- 测试：触达兜底单次记账（harness）、组失败暂缓+组外继续（提交层端到端）、
  缓存轮换防护；全量 **2416 passed**。
