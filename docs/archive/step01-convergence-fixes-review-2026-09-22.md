# step01 收敛修复代码审核（2026-09-22）

> **最新复核状态（2026-09-23 22:13）**：结合 v6.102、第十六次重跑产物与
> step02a 实际消费契约复核，新增 **2 项 P0 + 1 项 P1 + 1 项 P2**；当前
> `step01_quality.json=PASS`，但正式产物不能通过 step02a 的语义门禁，因此不能判定
> 收敛闭环完成，也不能按现状进入 step02。详见第十一节。

- 审核对象：`step01-convergence-fixes-2026-09-22.md` 所述 v6.87～v6.97 实现，以及当前工作树中与其直接相关的收敛、约束链、时间屏障和质量门代码。
- 审核方式：静态代码审查、调用链核对、最小反例、聚焦回归。
- 聚焦回归：`66 passed`；语法检查通过。
- 结论：**当前还不能把这批实现判为“收敛闭环完成”**。v6.96/v6.97 修掉了若干真实问题，但仍有 5 个会改变最终语义或错误采纳候选的高风险缺口。现有测试通过，是因为它们主要验证当前实现约定，没有覆盖下述对抗反例。

## 一、审核结论

| 优先级 | 结论 | 影响 |
|---|---|---|
| P0 | 结构收敛 `N → 0` 走提前返回，绕过完整性和内容不退化门 | 最成功的一次结构重生反而拥有最弱验收，可静默删剧情或引入新的完整性问题 |
| P0 | B2 内容不退化的分母由候选自己提供，并在异常时 fail-open | 候选删掉 `core_keywords`、台词或整段事件后，检查可返回“未退化” |
| P0 | 约束链声明“审计段只读”，实现却只保护时间字段；链前、链后仍有镜头删除器 | 空镜启发式可删除完整镜头，丢失 motion/KF/姿态/道具/结果语义 |
| P0 | `run_hard_fix()` 在跟踪器最后一次回调之后执行自动 LLM regen | `REGEN_AUTO_TRIGGER=1` 时，LLM 写入不进入时间屏障，hook/characters 还能无条件覆盖 |
| P1 | v6.97 的“单镜上限唯一源”未覆盖实际生成提示和异常回退 | 模型仍被高优先级提示要求 3～8s，门禁却按 12s；会制造不必要拆镜与时长膨胀 |
| P1 | 完整性门只比较异常数量，不比较异常身份 | 旧问题消失、同数量的新问题出现时仍会通过 |
| P2 | v6.87 的 mask 检查取错对象，实际比较的是旧稿与旧稿 | 该纵深防御恒为空；当前主要靠另一条 locked signature 兜底 |
| P2 | 汇总文档版本、测试数和“唯一非代码阻塞”结论已过期 | 容易把仍由代码制造的时长问题误判为只能删台词 |

## 二、具体发现

### P0-1：结构收敛 `N → 0` 绕过所有不退化验收

位置：`step01_script_gen/main.py:1392-1400`。

当前流程先计算 `new_issues`，只要新稿结构问题为 0，就直接 `return new_script`。完整性比较只位于后面的 `0 < len(new_issues) < len(issues)` 分支（约 `1415-1436`）。因此：

1. 原稿 5 个结构问题，新稿 1 个：会跑完整性门；
2. 原稿 5 个结构问题，新稿 0 个：直接采纳，不跑完整性门；
3. 新稿即使删掉关键事件、把角色状态改坏、引入新的阻塞身份，也可因“结构干净”被接受。

这不是边界遗漏，而是**验收逻辑被复制成两条分支**造成的控制流漏洞。测试 `test_structural_regen_adopts_clean_new_plan` 只断言结构归零和镜头数，没有构造“结构归零但内容损坏”的反例，所以无法发现此问题。

#### 根因修法

建立一个统一的候选验收函数，所有 `N → M`（包括 `M=0`）都只能从这一个出口采纳：

```text
accept_regen_candidate(before, candidate, objective="structural")
  1. checker 全部成功；未知 = 拒绝
  2. 目标指标严格改善
  3. 没有新增 blocking identity
  4. 固定内容清单不退化
  5. 通过后才提交 candidate
```

不得为“完全修好”保留快速返回；它只是目标指标的最佳值，不代表其他维度安全。

### P0-2：B2 的内容完整性度量可通过删除分母绕过

位置：`step01_script_gen/main.py:450-518`。

`_content_degradation()` 当前有两个自指问题：

- 原文覆盖率从候选自己的 `core_keywords` 计算。候选删除全部 `core_keywords` 后，候选分母为 0、结果为 `None`，比较被跳过。
- grounding 率只统计候选仍保留的非空 `narration_segment`。候选删除全部台词后，候选分母为 0、结果为 `None`，比较同样被跳过。
- 三段检查都用宽 `except: pass`；检查器异常等价于“没有退化”。
- 比率口径允许候选按比例删除内容：例如 10/10 grounded 变成 1/1 grounded，比例仍是 100%，但 9 段内容已经消失。

已用最小反例核实：旧稿包含可定位台词和 `core_keywords=["船头"]`，新稿把台词和关键词清空，`_content_degradation(old, new, novel)` 返回空列表。

#### 根因修法

在重生前从**已接受旧稿 + 小说原文**生成不可由候选修改的 `ContentManifest`，至少包含：

- 去重后的原文依据片段或稳定 span id；
- 实际在旧稿画面中出现、且能在原文定位的核心实体/道具/状态锚；
- 台词原文片段及说话人；
- 致命伤、武器归属、事件结果等不可丢状态；
- 可合并但不可消失的事件 id。

候选只能证明自己覆盖这份固定清单，不能提供自己的分母。合法合并允许多个旧镜映射到一个新镜，但固定的唯一事件/span 集合不能减少。任何检查器异常、`None` 或无法建立映射都应拒绝候选，而不是放行。

### P0-3：三阶段契约只在注释中成立，语义/结构写入未被屏障保护

位置：

- `step01_script_gen/constraints/__init__.py:152-162`
- `shared/semantic_barrier.py:63-77, 131-187`
- `step01_script_gen/constraints/opening_empty_shot_cap.py:105-167`
- `step01_script_gen/constraints/opening_empty_shot_remove.py:64-157`

注册表声明 `POST_TEMPORAL_AUDIT` 是“只读检查；运行时断言语义 payload 零写入”。实际 `TimeFieldWriteTracker` 只快照：

- `duration`
- `duration_sec`
- `motion_desc`
- `keyframes[*].t_sec`

它不保护镜头集合、顺序、prompt、ff/lf、KF 描述、布局、角色、道具和事件结果。

当前存在两条已证实的语义删除路径：

1. `OpeningEmptyShotCapConstraint` 位于审计边界之前。它按文本启发式把多个开场镜判为空镜，`del shots[ri]`，仅把少量 `main_subject` 关键词并入保留镜。被删镜的 prompt、ff/lf、motion、KF、布局、姿态、道具和结果均丢失。
2. `OpeningEmptyShotRemoveConstraint` 位于审计边界之后，直接 `shots.pop(0)`，最多删除两镜。它没有 repair-loop 守卫，也没有事件等价证明。

最小反例中，三个带不同 motion/KF（烈日、柳荫、荷湾）的开场镜被压成一个；两个镜头的动作与 KF 全部消失。现有 `test_opening_empty_shot_cap.py` 把“发生删除”当成正确行为，只验证时长和关键词，没有验证语义保真。

`OpeningEmptyShotRemoveConstraint` 的删除有时会因列表位置平移被时间跟踪器间接报成越权，但这是偶然副作用，不是可靠保护；跟踪器本身没有“禁止增删/重排镜头”的契约。

#### 根因修法

1. 两个空镜删除约束立即改为 **report-only**，输出可定位证据并进入结构 regen。
2. 审计边界后增加独立的 `SemanticPayloadTracker`：至少保护镜头数量、顺序、逐镜 semantic revision、characters/hook，以及全部 `_SEMANTIC_SHOT_FIELDS`。
3. 审计段后任何增删、重排或语义字段变化直接形成 blocking issue；不要靠位置变化碰巧触发时间 diff。
4. 若未来确需确定性合并，必须先证明同一事件/状态/原文 span 的等价映射，再生成一个完整替代镜头；不能通过拼关键词和累加时长代替语义合并。
5. 把“文本归一化”“时间派生”“只读审计”拆成真实的三个执行入口；不要继续用一个 `ALL_CONSTRAINTS` 混合链配注释模拟阶段。

### P0-4：自动 regen 在审计回调结束后修改脚本

位置：

- `step01_script_gen/constraints/engine.py:221-228`
- `step01_script_gen/constraints/regen_trigger.py:218-301, 304-368`

`apply_constraints()` 在每个约束后调用 `on_constraint_done`。约束循环完全结束后，`run_hard_fix()` 才调用 `trigger_regen_and_merge()`。因此自动 regen 的所有改动都发生在跟踪器最后一个快照之后。

风险不只限于时间字段：

- 目标镜头通过 `preserved.update(new_shot)` 可覆盖任意字段；
- hook 只要在输出里存在就整对象替换；
- characters 按 ident 更新或新增；
- script-level HARD 的 `target_indices` 可能为空，但 hook/characters 仍可被覆盖；
- merge 后没有重新跑同一约束链、语义事务验收或 tracker 终态观察。

当前 `.env.content` 没有启用该开关，默认也是关闭，因此本次实际重跑未必触发。但这是代码明确支持的生产路径，不能用“默认关闭”证明门禁闭环。

#### 根因修法

首选：删除 `run_hard_fix()` 内的 LLM 调用。确定性 hard-fix 只产出结构化违例，统一交给 main 中已有的事务式 regen/repair 管线处理。

若必须保留：

1. merge 前后使用与定向修复相同的字段授权、目标集合、blocking identity 和固定内容清单；
2. hook/characters 必须显式成为目标，否则禁止写入；
3. merge 后重新跑完整约束与质量检查；
4. 对最终对象补一次 tracker 终态快照；
5. 任一检查器失败回滚整个事务。

### P1-1：v6.97 仍未形成真正的单镜上限唯一源

位置：

- `step01_script_gen/prompts.py:249, 257, 324-338, 549`
- `step01_script_gen/main.py:604, 773, 776`
- `step01_script_gen/constraints/duration_budget.py:58-61`
- `step01_script_gen/constraints/opening_empty_shot_cap.py:85-91`
- `step01_script_gen/constraints/motion_time_bounds.py:35-40`
- `step01_script_gen/validation.py:7820-7825`

`timecode.active_shot_cap()` 已加入，主门禁的一部分调用点也已迁移；但实际生成提示仍有多条生效的 8 秒规则：

- `_build_total_duration_constraint()` 使用 `min(8.0, cap)`，并写“超 8s 必拆镜”；
- `_duration_cap_guidance()` 仍以模板 8 秒为基线；
- regen iron rules 写“每镜 3-8s”；
- 时长收敛反馈写“单镜 3-8s”；
- 结构重生反馈要求每个子镜 `≤8s`、`3.5-8s`。

这些不是陈旧注释：`build_prompt()` / `build_doubao_prompt()` 会把它们加入真实请求。当前模型有效 cap 为 12s 时，生成层会主动把 8～12s 的合法镜头拆开，而质量门又按 12s 接受。多出来的镜头受最短时长约束后会抬高总时长，所以文档中“106～113s 只剩内容超量、与代码无关”的结论尚不能成立。

另外，多处 `except` 仍回退硬编码 `8.0`。这意味着唯一源导入失败时系统不是 fail-closed，而是悄悄恢复旧规则并产生假 HARD。

#### 根因修法

1. prompt builder 接收一个运行时 `shot_cap`，所有用户可见规则由同一格式化函数生成。
2. 删除业务模块里的 `return 8.0`；无法取得唯一源时应明确失败或报告 checker failure。
3. 允许景别给出推荐区间，但硬上限必须引用 `shot_cap`，不能把“远景建议 5～8s”写成全局硬规则。
4. 增加端到端测试：分别把有效 cap 设为 6/12/15，断言最终 system + user + regen prompt 中不存在更严格的陈旧硬上限。

### P1-2：完整性“数量不增加”不能证明没有新问题

位置：`step01_script_gen/main.py:419-447`。

`_integrity_not_degraded()` 只比较 `integrity_count`。若旧稿有问题 A，新稿修掉 A 但引入问题 B，数量仍为 1，函数会判定“未退化”。对状态、角色、武器、事件结果这类问题，身份变化比数量更重要。

#### 根因修法

让 `_candidate_quality_summary()` 返回稳定 identity 集合，例如：

```text
(code, shot_id, frame_id, entity_id, field_path)
```

采纳条件至少应为 `new_identities - old_identities == ∅`。目标问题允许消失；不得用一个新问题替换一个旧问题。数量只用于日志和排序，不能作为安全门。

### P2-1：v6.87 的 mask 纵深检查当前恒为空

位置：`step01_script_gen/main.py:3774-3815`。

`_apply_shot_replacements()` 返回 `merged` 后，代码先执行：

```python
_mask_violations_after_patch = _final_field_mask_violations(
    _round_before_script, script, set(picked))
script = merged
```

此时 `script` 仍是旧稿，所以比较的是旧稿与旧稿。`_mask_violations_after_patch` 无法发现任何 LLM 写入。随后 `_locked_after_patch` 的取值顺序是正确的，提交前的 `check_replacement_authorized` 也能覆盖一部分风险，因此该问题目前是纵深防御失效，而非单独的主旁路。

#### 修法

先 `script = merged`，再计算 mask；或直接对 `_round_before_script` 与 `merged` 比较。补一个真正修改 locked 字段的反例，断言 `_locked_by_llm` 非空并拒绝事务。不要只写“源码中赋值位置早于后处理”的静态测试。

### P2-2：汇总文档与当前实现已不同步

`step01-convergence-fixes-2026-09-22.md` 当前同时存在：

- 标题写 v6.87→v6.96，但正文已加入 v6.97；
- 开头和规模表仍写规则 `2026-09-22.9`，代码为 `2026-09-22.10`；
- 开头写 `1764 passed`，本轮此前完整测试记录已到 1780，且 v6.97 后尚需重新跑全量；
- v6.93 声称“三个 prompt 里的全部 8s 表述”已同步，`prompts.py` 和 main 的运行时反馈仍有多处；
- 第五节把“内容超量”定为唯一非代码阻塞，证据不足。

建议在代码修完并完成新一次 step01 重跑后再更新轨迹，避免把中间结论写成最终决定。

## 三、建议实施顺序

### 第一阶段：先封住错误采纳和审计旁路

1. 统一结构/时长/定向重生的候选验收入口，消除 `N→0` 快速返回。
2. 建立固定 `ContentManifest`，替换候选自带分母。
3. 把 `opening_empty_shot_cap/remove` 改为 report-only。
4. 从 `run_hard_fix()` 移除自动 LLM merge，统一进入事务式 repair。
5. 完整性比较改为 identity 集合差。

### 第二阶段：把阶段边界做成真实架构

1. `PRE_NORMALIZE`：允许明确列出的确定性文本/结构归一化。
2. `TEMPORAL_DERIVE`：唯一一次生成 duration/timecode/KF 时间坐标。
3. `POST_AUDIT`：纯函数检查器，禁止修改脚本。
4. 每阶段计算 semantic revision；阶段函数若宣称只读，返回后 revision 必须相同。
5. 对镜头增删/重排单独记录 structural revision，不能继续借用时间字段 diff。

### 第三阶段：统一 cap 与提示生成

1. 所有 prompt/regen feedback 从 `active_shot_cap()` 的值渲染。
2. 清除异常回退里的 8.0。
3. 检查文档、日志、错误消息只显示运行时有效值。

### 第四阶段：补对抗回归

至少增加以下测试：

1. `结构问题 1→0，但候选删除剧情/新增完整性 identity` → 拒绝。
2. 候选删光 `core_keywords` → 内容退化。
3. 候选删光 narration → 内容退化。
4. 10/10 grounded 变成 1/1 grounded → 内容退化。
5. 旧完整性 A 被新完整性 B 替换、数量不变 → 拒绝。
6. 空镜约束不得改变镜头数或 semantic payload，只能报告。
7. 审计边界后增删/重排镜头 → blocking。
8. `REGEN_AUTO_TRIGGER=1` 的 merge 必须进入事务门；未点名 hook/characters 不得变化。
9. cap=6/12/15 时，所有生产 prompt 与门禁使用同一个值。
10. LLM 修改 locked 字段时，mask 检查必须实际命中。

## 四、是否需要重跑 step01

**需要，但不是现在立刻重跑。**

当前剩余问题会影响“候选是否被采纳”和“开场镜头是否被删除”，继续重跑只能得到新的不稳定样本，无法证明闭环。建议先完成上述第一阶段和 cap 统一，再执行：

1. 聚焦对抗测试；
2. step01 全量测试；
3. 用同一章节重跑 step01；
4. 核对最终脚本的 semantic revision、镜头事件清单、总时长和 blocking identity；
5. 确认空镜未被确定性删除、结构重生没有丢原文 span，再决定是否进入 step02。

## 五、本次验证记录

执行：

```text
python -m pytest \
  tests/step01_script_gen/test_structural_convergence.py \
  tests/step01_script_gen/test_duration_convergence.py \
  tests/shared/test_semantic_barrier.py \
  tests/step01_script_gen/constraints/test_regen_trigger.py \
  tests/step01_script_gen/constraints/test_opening_empty_shot_cap.py -q
```

结果：`66 passed in 0.69s`。

语法检查：

```text
python -m py_compile \
  step01_script_gen/main.py \
  step01_script_gen/timecode.py \
  step01_script_gen/constraints/engine.py \
  step01_script_gen/constraints/regen_trigger.py \
  shared/semantic_barrier.py
```

结果：通过。

这些结果说明当前实现与现有测试一致，不构成对上述反例的否定；需要第四节列出的对抗测试才能验证修复完成。

---

## 六、复核意见（2026-09-22，Claude 逐条核对 + 确定性反例）

复核方式：读代码 + **最小 fixture 直接调用被测函数**（脚本 `_verify_review.py`，
输出 `_verify_review.out.txt`），不用日志推断——日志只打 top-5，`grep 不到 ≠ 没触发`。

| 编号 | 复核结论 | 依据 |
|---|---|---|
| P0-1 | ✅ **成立** | `main.py:1393` 的 `if not new_issues: return new_script` 位于完整性门之前 |
| P0-2 | ✅ **成立**（已复现 5 例，含 fail-open） | 见下方「反例 A–E」 |
| P0-3 | ⚠️ **拆成两半**：**3b 成立**（审计段只保护时间字段）；**3a 属产品取舍**，需你定（见「待确认 1」） | `TimeFieldWriteTracker` 只快照 `duration`/`duration_sec`/`motion_desc`/`kf_t` |
| P0-4 | ⚠️ **代码路径成立，但当前未启用** | `.env.content` 无 `REGEN_AUTO_TRIGGER`（默认 `0`）→ 本次重跑不可能触发；仍按首选修掉 |
| P1-1 | ✅ **成立** | `prompts.py:249/257/549`、`main.py:609/780-782` 均写死 8s |
| P1-2 | ✅ **成立** | 反例 D |
| P2-1 | ✅ **成立** | `script` 在 `main.py:3806` 之前**未被就地改写**（该区间唯一的 `script = ` 在 3807 行，即 `script = merged`）→ 比较的确是旧稿与旧稿 |
| P2-2 | ✅ **成立** | 文档确已与代码不同步 |

### 确定性反例（`_verify_review.py` 实跑输出）

```text
修复前（= 审核提出的反例）：
A) 候选删光 core_keywords        → content_degradation = []      # 应报退化
B) 候选删光 narration            → content_degradation = []      # 应报退化
C) 10/10 grounded → 1/1 grounded → content_degradation = []      # 应报退化
D) 完整性数量 1 → 1（身份全换）  → integrity_not_degraded = (True, '')   # 应拒绝
E) keeper 抛异常                 → content_degradation = []      # fail-open

修复后（同一个脚本 `python _verify_review.py`，v6.98 实跑）：
A) → ["已覆盖关键词丢失 2 个: ['江面', '船头']"]                    ✅ 命中
B) → ["台词丢失 1 句: ['他站在船头']", "grounding 无法验证…"]        ✅ 命中
C) → ["台词丢失 9 句: ['他站在船头第2回', …]"]                       ✅ 命中
D) → (False, "新完整性身份 1 类: state_order_replay|||听香|…")       ✅ 拒绝
E) → ["关键词清单检查器异常，按退化处理: checker exploded", …]        ✅ fail-closed
```

> **C / D 的用例本身也修过**：初版 C 用「同一句台词 ×10」当样本（子串口径会把它
> 当合法去重，测不到「按比例删内容」），D 的摘要里没有 `integrity_ids`（身份门
> 无从触发）。照原样贴出来会**看起来像没修**——所以这里把用例一并改成真实形态。

### 我**不采纳**的一条修法（P0-2 的「完整 ContentManifest」）

判据缺的是「**候选不能提供自己的分母**」这一条性质，而这条性质用
「**从已接受旧稿导出固定清单**」就已具备，不需要原文 span id / 事件 id 那一层。
且原文 span 在合并（合法压缩的主要手段）下会改变粒度，强行比对会把**合法合并**
判成退化——正是本次要修的那类「永不收敛」缺口。

因此 P0-2 按「旧稿固定清单 + fail-closed」实现（见修毕记录）；
`致命伤/武器归属/事件结果` 那几类由 P1-2 的**身份集合**（而非数量）覆盖，
两条合起来比单一 Manifest 更贴合现有检查器。

### 待确认 1：开场空镜删除（P0-3a）——需要你的一句话

`opening_empty_shot_cap`（审计段**前**，合并 3 个空镜为 1）与
`opening_empty_shot_remove`（审计段**后**，`shots.pop(0)` ≤2 镜）都会**删掉完整镜头对象**
——被删镜的 prompt / ff / lf / motion / KF / 布局 / 姿态 / 道具随 `del` 一起消失。

但这是**你定的产品行为**（`prompts.py` B.3.2 开篇空镜插入 + `constraints/__init__.py` R2b
「doubao 经常开头 2-3 镜全是空镜，节奏太慢」），不是无意写出来的。所以它算不算缺陷，
得你定：

| 选项 | 行为 | 代价 |
|---|---|---|
| **A（现状）** | 保留删除，但把它登记为**显式受豁免的结构写入者**（可审查的白名单 + 必留证据） | 空镜的 KF/环境多样性仍会丢，但**不再是隐式行为** |
| **B（审核建议）** | 改 report-only，删除诉求交结构 regen | 开篇不再自动剪空镜 → 开头变慢、总时长上抬 |
| **C（折中）** | 保留合并，但把被删镜的**环境/道具/设施描述并进保留镜**（不只并 `main_subject` 关键词），KF 仍按 1 镜重算 | 实现量最大；语义损失降到「多环境合为一镜」 |

**我的默认动作**：先按 **A** 落地（= 行为零变化、但把隐式删除变成显式豁免 + 证据），
等你选 B/C 再改——那是一处注册表级的小改动。

### 待确认 2（沿用前文，仍未决）

- **③ 的取舍**：接受 ~95% 相似度的拆分（`adjacent_duplicate_visuals` 改 `repairable=True`），
  用一条 `hard` 换一条 `visual_risk`？还是放宽阈值 / 代码强制区分景别？
- **内容超量**：正片 101.39s ≫ 90s。**注意**：P1-1 一旦修掉，「超 8s 必拆镜」这条
  陈旧提示消失后，模型不再被诱导把 8-12s 合法镜头拆开——**总时长可能自然回落**，
  所以「只能靠删台词」这个结论**在 P1-1 修完重跑之前不成立**（原汇总文档第五节的
  说法据此撤回）。


---

## 七、修复落地记录（2026-09-22，v6.98 / 规则 2026-09-22.11）

按审核建议顺序**第一阶段 + 第三阶段**已全部落地（含 P2 两项），全量测试
**1784 passed, 1 skipped**。逐条对应：

| 编号 | 状态 | 落地位置 | 对抗回归 |
|---|---|---|---|
| P0-1 | ✅ 已修 | `main.py::_accept_regen_candidate()`（**唯一采纳出口**）；`_regen_structural_over_limit` 取消 `N → 0` 快速返回 | `test_structural_convergence.py::test_structural_regen_rejects_clean_plan_that_drops_content`（对旧代码验证必失败） |
| P0-2 | ✅ 已修（修法**不完全照审核**，见第六节） | `_content_inventory()` / `_covered_keywords()` / `_narration_anchors()`；`_content_degradation()` 四条判据 + 异常 fail-closed | `test_regen_acceptance_gates.py`（5 例，含反向保护「合法合并不算退化」） |
| P0-3 | ✅ 3b 已修（红线**收窄到结构**，见下）；⚠️ 3a **待你定** | `shared/quality_gate.py::semantic_payload_digest/diff_semantic_structure`；`shared/semantic_barrier.py` 跟踪结构写入 + `STRUCTURAL_SANCTIONED_WRITERS`；`quality_issues._semantic_write_audit_issues`（hard / advisory 两档） | `tests/shared/test_semantic_barrier.py`（9 例：删除/重排/分界前不误报/豁免留证/落盘成硬 issue + **真实链条全约束跑一遍无未授权结构写入者**） |
| P0-4 | ✅ 已修 | `constraints/engine.py::run_hard_fix` 删除 LLM merge 调用点 | `tests/.../test_regen_trigger.py`（行为桩 + 源码静态锁） |
| P1-1 | ✅ 已修 | `prompts.py`（`active_shot_cap` 渲染 + `_dur_range` 夹取 + 删 `return 8.0`）、`main.py` 结构反馈、`timecode` 日志、三处 `except: return 8.0` | `test_regen_acceptance_gates.py::test_all_production_prompts_render_the_runtime_shot_cap`（cap 6/12/15） |
| P1-2 | ✅ 已修 | `_integrity_identity()`（去序号、数字归一）+ `integrity_ids`；判据 = 计数不增 **且** 无新身份 | 同上文件（同数量换身份 → 拒绝；换镜头号 → 放行） |
| P2-1 | ✅ 已修 | `main.py:_repair_shot_warnings` 比较对象改为 `merged` | `test_repair_shot_replacements.py::test_locked_field_written_by_merge_is_caught_at_call_site`（对旧代码验证必失败） |
| P2-2 | ✅ 已修 | 本文档 + `step01-convergence-fixes-2026-09-22.md`（标题/版本/测试数/撤回结论） | `test_rules_index.py` 锚点校验 |

### 一处按实测**收窄**了审核要求的地方（P0-3）

审核原文要求「审计段后任何增删、重排或**语义字段变化**直接形成 blocking issue」。
照做的结果是**每次运行必然 BLOCK**：`kf_time_window_alignment` 之后还有 71 个约束，
它们**就是靠写 `prompt`/`ff_desc`/`lf_desc` 工作的**（补景别前缀、重建锚点、合规
替换、冗余清洗…）。实测两次收窄：

1. 逐镜语义改写 → 全线误报；
2. hook 的**任何**变化 → `transition_keywords` 追加过渡词即误报。

最终红线取：**镜头数 / 纯重排（`set` 相同、`order` 不同）/ hook 有无 /
角色 identifier 集合**。逐镜文本改写进 `content_writes`，只做诊断。
依据是真实链条回归：16 镜真实 fixture + 全部 71 个审计段约束跑一遍，断言
`unauthorized_semantic_writes() == []`（同时防「红线划宽」与「将来新加结构写入者」）。

这与审核第四阶段第 7 条（「审计边界后增删/重排镜头 → blocking」）一致；
与第 6 条（「空镜约束不得改变镜头数或 semantic payload，只能报告」）方向相反，
原因见「待确认 1」——那是产品取舍。

### 仍未做（以及为什么）

- **第三阶段的第 4 条**（端到端测试「最终脚本中不存在更严格的陈旧硬上限」）：
  已用「四个生产提示块在 cap=6/12/15 下渲染」覆盖。`prompts/*.md` 是静态模板，
  按类型给的推荐区间（如「对话镜 6-15s」）在 cap=6 时会高于生效上限——
  运行时块已显式声明「更大或更小都以本值为准」，**但要彻底消除只能改成运行时插值**
  （模板 → 生成期渲染），那是一次独立的改造，建议单独排期。
- **第二阶段的「真实三阶段执行入口」**（把 `ALL_CONSTRAINTS` 混合链拆成三个执行入口）：
  当前是「一条链 + 屏障断言 + 语义/结构守卫」。审核的第 5 条要求把注释里的架构变成
  真实入口——这是一次结构性重构，**建议在本次重跑确认收敛之后**再做，避免和
  收敛行为的变化混在同一次验证里。
- **`opening_empty_shot_cap/remove` 改 report-only**：见「待确认 1」，等你一句话。

### 下一步

1. 你在「待确认 1 / 待确认 2」上给一句话（或在本文档标注）。
2. **重跑 step01**（同一章节），核对：最终 `hard`/`visual` 计数、正片总时长是否因
   P1-1 回落、`semantic_audit_write` 是否为 0、`structural_write_sanctioned` 是否
   只出现在开篇空镜那一条。
3. 把结果写回 `step01-convergence-fixes-2026-09-22.md` 的轨迹表（第十三次）。

---

## 八、二次代码复核（2026-09-23）

### 复核结论

v6.98 对上一轮结论的处理情况：

| 原编号 | 复核结果 |
|---|---|
| P0-1 `N → 0` 快速返回 | ✅ 已修；结构收敛的所有改善稿都进入 `_accept_regen_candidate()` |
| P0-2 候选自带分母 / fail-open | ✅ 已修主要反例；旧稿固定台词/关键词清单与异常 fail-closed 均生效 |
| P0-3 审计段语义/结构写入 | ⚠️ 结构跟踪已补，但空镜删除白名单方案存在新的时间审计冲突；决定改 report-only |
| P0-4 hard-fix 链尾自动 LLM merge | ✅ 已修；`run_hard_fix()` 已移除该调用点 |
| P1-1 单镜 cap 唯一源 | ⚠️ 运行时追加块已统一；最终 system + user prompt 仍含静态 15s/8s 数字冲突 |
| P1-2 完整性只看数量 | ✅ 对已列入 `integrity_codes` 的问题已补身份；但统一验收仍没有覆盖**全部 blocking** |
| P2-1 mask 比较旧稿与旧稿 | ✅ 已修为比较 `merged` |
| P2-2 文档过期 | ✅ v6.98 已同步；本节记录新复核结果 |

现有聚焦测试 `115 passed`，全量测试 **`1804 passed, 1 skipped`**。下面两个 P0
都有当前工作树的确定性反例，因此测试全绿不能作为闭环完成的证据。

### P0-A：统一验收只比较部分 integrity，仍会采纳新 blocking

位置：

- `step01_script_gen/main.py:629-662`
- `step01_script_gen/main.py:1819-1860`

`_accept_regen_candidate()` 名称和注释都称“统一候选验收入口”，但实际只检查：

1. `_candidate_quality_summary()` 中手工列出的 9 个 `integrity_codes`；
2. `_content_degradation()` 的结构数量、台词、关键词与 grounding。

它没有比较 `structured_issues` 中的**全部 blocking identity**。例如
`kf_time_out_of_shot`、`kf_layout_count_mismatch`、`kf_event_window_mismatch` 等不在
`integrity_codes` 中，新稿可在解决结构超载的同时制造这些阻塞并被采纳。

当前代码的确定性反例：旧稿与候选内容完全相同，只给候选增加一个
`t_sec=9.0`、镜长 `6.0s` 的 KF。结果为：

```text
old_blocking = [duration_budget]
new_blocking = [duration_budget, kf_layout_count_mismatch, kf_time_out_of_shot]
old_integrity_ids == new_integrity_ids
_accept_regen_candidate(old, new, novel) == (True, [])
```

其中 `kf_time_out_of_shot` 是 `hard`。这与第十一次重跑“超载归零、同时新出现 3 条
KF 越界”的真实故障同型。

#### 根因

“完整性问题”与“blocking 问题”被当成两个集合，但候选提交门只消费前者。
把控制流统一到一个函数并不等于验收维度已经统一。

#### 实施决定

在 `_candidate_quality_summary()` 增加全部 blocking 的稳定身份**多重集**，统一验收要求：

```text
candidate_blocking - allowed_resolved_targets - before_blocking == empty
```

不能只比较总数或 `set`：同一问题重复次数从 1 增到 3 也属于退化，应使用 `Counter`。
整脚本重生会改变 shot index，因此身份优先使用：

```text
(code, entity_id, field_path, frame_id, source/evidence anchor, normalized message)
```

若某类问题无法建立跨重排的稳定锚，按无法验证拒绝，不得静默忽略。结构轮允许旧目标
消失，但不允许引入任何新的 hard / visual-risk 身份或增加既有身份的次数。

必须补回归：候选只增加 `kf_time_out_of_shot` 时 `_accept_regen_candidate()` 返回拒绝。

### P0-B：开场空镜删除的 sanctioned 方案与时间屏障冲突

位置：

- `shared/semantic_barrier.py:78-92`
- `shared/semantic_barrier.py:219-249`
- `step01_script_gen/constraints/opening_empty_shot_cap.py:99-172`
- `step01_script_gen/constraints/opening_empty_shot_remove.py:81-157`

v6.98 选择把 `opening_empty_shot_remove` 放进
`STRUCTURAL_SANCTIONED_WRITERS`，希望保留删除行为、只产生 advisory。但它只豁免了
**结构**检查，没有豁免 `TimeFieldWriteTracker`。

删除首镜后，后续镜头按列表位置前移。时间快照也是按列表位置比较，因此真实镜头的
duration / motion / KF 不同时，会被视为大量未授权时间写入。最小实测：

```text
opening_empty_shot_remove 删除 1 镜，shots 10 → 9
sanctioned_semantic_writes = [镜头数 10 → 9]
unauthorized_writes = 20 条时间差异
```

最终结果同时得到 advisory 和 `time_field_audit_write` HARD。现有
`test_sanctioned_structural_writer_is_recorded_but_not_blocking` 只断言语义白名单，没有
断言 `tracker.unauthorized_writes() == []`，所以没有发现这个矛盾。

更根本的问题仍未变化：两个约束都按窄文本启发式删除完整镜头，无法证明被删镜不含
独立环境、道具、姿态、KF 或事件结果。把它加入白名单只改变报告等级，不提供语义安全性。

#### 决定：采用原审核选项 B

`opening_empty_shot_cap` 与 `opening_empty_shot_remove` **全部改为 report-only**：

- 不删除、不合并、不重编号、不改时长；
- 记录命中的镜头、判空证据和建议保留/处理顺序；
- 交结构 regen 生成完整替代镜头，并通过统一候选验收；
- 删除 `STRUCTURAL_SANCTIONED_WRITERS` 中的该项，白名单恢复为空。

不选择 A：扩大时间白名单会把“位置平移”和真实时间写入一起放过，使阶段屏障重新失效。
不选择 C：把环境文本拼进保留镜仍无法安全合并不同 KF/事件时序，本质上仍是语义重写。

需要补两条测试：

1. 两个空镜约束执行前后 semantic payload 与镜头数完全不变，只产生 warn/violation；
2. 审计段结构白名单为空，任何镜头增删/重排均形成 blocking。

### P1-A：结构屏障无法识别“等数量删除 + 新增/替换”

位置：`shared/quality_gate.py:233-265`。

`diff_semantic_structure()` 当前只在以下情况报告结构变化：

- 镜头数量不同；
- `set` 相同但 `order` 不同的纯重排；
- hook 有无变化；
- character identifier 集合变化。

若约束删除一镜并插入/替换另一镜，镜头总数不变、semantic digest 集合也改变，该函数会
把它归为普通 `content_writes`，不会 blocking。也就是说“禁止增删”只覆盖净数量变化，
没有覆盖等数量替换。

#### 根因修法

阶段屏障是同一进程内的运行时守卫，可以直接快照 shot 对象身份序列
（`id(shot)` 或进入链前分配的运行时 UID）：

- 同一对象字段原地改写 → content write；
- 对象消失/新增/替换 → structural write；
- 身份集合相同、顺序变化 → reorder。

运行时 UID 不进入落盘 semantic revision，仅用于本轮阶段屏障。补“pop 一镜 + append 新镜、
总数不变”对抗测试。

### P1-B：cap 测试没有覆盖模型实际收到的完整 prompt

位置：

- `tests/step01_script_gen/test_regen_acceptance_gates.py:175-201`
- `step01_script_gen/prompts/system.md:85`
- `step01_script_gen/prompts/main.md:19`
- `step01_script_gen/prompts/doubao_system.md:49-50,129`

现有测试只检查四个运行时追加块，没有检查最终的 system prompt + user prompt。静态模板仍
写着：

- 对话镜 `6-15s`；
- 动作/空镜 `3.5-8s`；
- Doubao `单镜 ≤15s`。

当有效 cap 为 6s 或 12s 时，模型会同时看到静态 15s 和运行时 6/12s。追加一句“以本值
为准”能降低冲突影响，但不等于单一事实源；长 prompt 中规则权重和位置会影响遵循率。

#### 根因修法

把模板中的硬数字改成占位符，生成期统一渲染推荐区间与硬上限。测试必须构造模型最终收到的
完整 `system_prompt` 和 `user_prompt`，在 cap=6/12/15 下检查不存在冲突的硬上限。

### 文档同步问题

当前两份文档仍有以下不一致：

- 汇总文档称 v6.98 “全部成立并落地”，但 P0-3a 明确仍待决定；本节已作决定 B。
- 审核文档第七节称“第一阶段 + 第三阶段已全部落地”，完整 prompt 插值仍列为未做。
- `quality_issues._semantic_write_audit_issues()` 的注释写 unauthorized 包含“逐镜语义改写”，
  实现实际只把结构变化列为 unauthorized，逐镜改写只是诊断。
- 本次全量结果为 `1804 passed, 1 skipped`；第七节的 `1784` 是 v6.98 当时记录，可保留为
  历史数据，但不能作为当前工作树验证结果。

## 九、当前是否应重跑 step01

**暂不建议重跑。** 先修 P0-A 与 P0-B：前者决定坏候选能否被提交，后者决定开场镜头
是否会被删除并触发互相矛盾的审计结果。修完后先跑新增对抗测试和全量测试，再用同一章节
重跑 step01。P1-A/P1-B 建议同批完成；否则重跑只能验证当前样本没有撞到潜在路径，不能
证明阶段契约和 cap 单源已经闭环。

---

## 十、二次复核发现的修复落地（2026-09-23，v6.99 / 规则 2026-09-23.1）

第八节提出 2 P0 + 2 P1，逐条落地；**四个反例都先复现、并对旧代码验证过「测试必失败」**。
全量测试 **1800 passed, 1 skipped**。

| 编号 | 落地 | 对抗回归（对旧代码必失败） |
|---|---|---|
| **P0-A** 统一验收没比较全部 blocking | `main.py::_blocking_not_degraded()`：**Counter 多重集**比较**全部** hard/visual_risk 身份；`_accept_regen_candidate()` 的第一道门就是它 | `test_candidate_adding_a_kf_hard_is_rejected`（候选只多一个 `t_sec=9.0` 的 KF：`integrity_ids` 完全相同，旧判据返回 `(True, [])`）+ `test_blocking_identity_multiset_counts_repeats`（1 次→2 次也算退化）+ `test_blocking_identity_is_stable_across_shot_renumber` |
| **P0-B** 空镜删除的 sanctioned 方案与时间屏障冲突 | 两个空镜约束**全部改 report-only**（不删/不合并/不重编号/不改时长）；`STRUCTURAL_SANCTIONED_WRITERS` **恢复空集**；顺带删掉不再使用的 `_shot_duration_cap()` | `test_pure_empty_opening_is_reported_not_removed`（逐字节不变）+ **`test_no_unauthorized_time_write_after_apply`**（旧实现下这条会看到 20 条未授权时间写入）+ 白名单为空/原名字已属越权 两条 |
| **P1-A** 结构屏障看不见等数量替换 | `diff_shot_structure()`：**对象身份 + 内容多重集**双信号。同一对象原地改写 = content；对象增删/替换**且**内容多重集也变 = structural；纯重排 = structural；深拷贝重建（内容逐字相同）= content | `test_equal_count_delete_plus_append_is_structural`（pop 1 + append 1，总数不变）+ `test_deepcopy_rebuild_with_identical_content_is_not_structural`（反向保护：不加这条就会把整份 deepcopy 误判成全量替换） |
| **P1-B** cap 只在运行时追加块里统一 | `.md` 模板改占位符（`{{SHOT_CAP}}` / `{{SHOT_CAP_DEC}}` / `{{NARR_MAX_CHARS}}` / `{{DIALOGUE_TARGET}}` / `{{DUR_RANGES}}`），`_load_prompt()` 导入期统一渲染；**残留占位符抛错**（fail-closed，宁可报错也不让模型看到 `{{...}}`） | `test_templates_render_placeholder_to_effective_cap`（cap=6/12/15 渲染模板本体）+ `test_shipped_constants_match_the_effective_cap`（实际出厂的 `SYSTEM_PROMPT`/`PROMPT_TEMPLATE`/`DOUBAO_SYSTEM_PROMPT`）+ `test_unrendered_placeholder_is_a_loud_error` |

### 文档同步问题（第八节末）逐条处理

| 问题 | 处理 |
|---|---|
| 汇总文档称 v6.98「全部成立并落地」，而 P0-3a 待定 | 已按你的决定改为 **B（report-only）**，两处文档同步 |
| 第七节称「第一阶段 + 第三阶段已全部落地」，但完整 prompt 插值仍列未做 | **P1-B 已做完**，第七节那两条「仍未做」只剩「真实三阶段执行入口」与「类型区间模板化」——后者也在本次一并解决 |
| `_semantic_write_audit_issues()` 注释说 unauthorized 含「逐镜语义改写」 | 注释已改正：unauthorized = **结构**写入（镜头数 / 对象增删替换 / 重排 / hook 有无 / 角色集合）；逐镜文本改写**不在其列**（那是审计段硬修复层的设计行为） |
| 全量结果 1804 / 1784 | 以本节 **1800 passed, 1 skipped** 为当前值（1804 是复核当时树上的数字；本次重写了开篇空镜的 5 个「合并」用例为 8 个「只报告」用例，条数有增减） |

### 一处必须说明的行为变化（不只是报告等级）

两个空镜约束从「删镜头」变成「只报告」之后，**开篇节奏不再由代码自动剪**：

- 以前：前 3 镜里多于 1 个空镜 → 代码 `del` 掉多余的（最多 2 个），时长相加钳到 cap；
- 现在：只留 `[CONSTRAINT-WARN] opening_empty_shot_cap/remove` 记录（含命中镜头 + 判空证据 + 建议保留顺序），脚本一个字节都不动。

**这是你选的 B**，代价是「前 3 镜全是空镜」的稿子会原样落盘（交给结构 regen / 人工）。
要把它接进结构 regen 的反馈里，需要一条**新的耦合**（约束违例 → 收敛轮的目标集合）——
那涉及执行顺序（约束在 `run_hard_fix` 里跑，而收敛轮在 `timecode` 之后），
建议单独做并配合同一批测试。本次没有做，以免把两个变更混在一次验证里。

### 当前状态

```text
规则版本 2026-09-23.1   |  全量 1800 passed, 1 skipped
待验证（需重跑）：P0-A/B、P1-A/B 四道门都还没有在真实章节上被走到过
```

第十三次重跑（09-23 07:16）验证的是 v6.98 的 P1-1，不是本次这四道门——
重跑后应重点核对：`kf_time_window_alignment` 类是否还被 `_blocking_not_degraded`
挡在门外（即「修好超载却引入 KF 越界」的候选不再被采纳）、`semantic_audit_write`
是否为 0、以及正片时长是否仍落在 88s 附近。

---

## 十一、v6.102 + 第十六次重跑同步复核（2026-09-23）

### 复核结论

第十六次重跑的 `step01_quality.json` 确实是 `PASS`（11 镜、73.02s、hard=0、
visual_risk=0），但这个 PASS **不能代表 step01 已形成可交付闭环**：

| 优先级 | 新发现 | 直接影响 |
|---|---|---|
| **P0-C** | `step01_audit.json` 的生产格式与 `require_semantic_gate()` 的消费契约不一致，而且审计发生在最终修复/BEST-GUARD 之前 | 当前正式产物被 step02a 确定性拒绝；“可以进 step02”结论不成立 |
| **P0-D** | BEST-GUARD 先 `finalize_step01_script()`，随后仍可整对象重生 hook，且不再跑完整 finalization / semantic audit | 最终 hook 不属于刚才审计过的 revision，恢复路径重新出现“审计后语义写入” |
| **P1-C** | `motion_segment_kf_align` 用稀疏 KF 反向重写 `motion_desc` 时间窗，把“缺 KF”改成“时段已合并” | 第十六次的 KF 归零有 5 条是通过改事实源获得，不是补齐证据；与“自动语义写入收紧”原则冲突 |
| **P2-C** | 全量测试中的候选早停用例存在并发竞态 | 文档中的“全量全绿”不再成立；本次为 1 failed / 1837 passed / 1 skipped，单测独立重跑通过 |

### P0-C：语义审计产物与下游门禁契约断裂

生产端位于 `step01_script_gen/main.py:5805-5825`，当前只写四个字段：

```text
status / model / error / issues
```

消费端 `shared/quality_gate.py:373-409` 明确要求：

```text
status=ok
input_revision == 最终 script revision
rule_source_fingerprint == quality report fingerprint
audit_scope 非空
```

step02a 在 `step02a_frame_gen/main.py:230-240` 无条件调用这份契约。对第十六次正式
产物直接执行同一调用，结果为：

```text
QualityGateError: step01 semantic audit is stale:
audit=missing current=6236ef931396b7cf4c0af8ab9a26b44d9ff3d7e7943f1def32951e1693e3aab5
```

这不是只漏写三个元数据字段。审计调用本身位于结构收敛之后、定向修复轮之前；其后还有
repair、hook 生成/重生、落盘前 finalize 与 BEST-GUARD 恢复。即使把当时 revision 填进
JSON，它仍会天然早于正式输出，仍是旧审计。

#### 根因修法

把“最终候选已确定”设为唯一提交点，顺序固定为：

```text
所有生成/repair/hook/BEST-GUARD 选择结束
→ finalize_step01_script（最后一次确定性改写）
→ 对这个不可再改的对象运行 semantic audit
→ 用同一对象写 step01_audit + step01_quality + script_result
```

`step01_audit.json` 必须由统一写入函数附加 `input_revision`、
`rule_source_fingerprint`、`audit_scope`；审计超时、异常、缺字段或 revision 不一致时，
step01 自己就应输出 BLOCK，不能等 step02a 才发现。回归测试必须调用真实 producer
生成 audit 文件，再立即用 `require_semantic_gate()` 消费，不能分别 mock 两端。

### P0-D：BEST-GUARD 在 finalization 之后重生 hook

`_cross_run_best_guard()` 当前顺序是：

1. `main.py:5333` 调 `finalize_step01_script(best_script, ...)`，记录时间/结构屏障；
2. `main.py:5341` 再调 `_ensure_hook_quality(best_script, ...)`；
3. `_ensure_hook_quality()` 在 `main.py:2120-2134` 可调用 LLM 并执行
   `script["hook"] = _hook_regen`，随后只跑 hook 子集检查和 timeline 重建；
4. 没有第二次完整 `finalize_step01_script()`，也没有针对最终 hook 的 semantic audit。

第十六次真实日志正好走到该分支：恢复 `script_result_20260923_213656.json` 后，hook 因
`ff_desc_length` 被互补模型整对象重生；正式 `script_result.json` 的 hook 在 prompt、
ff/lf、motion、KF、layout 等字段均与被恢复快照不同。随后 quality gate PASS，但这次
整对象替换发生在恢复版屏障之后。

#### 根因修法

不要在 BEST-GUARD 内部形成第二条“局部 finalize”路径。BEST-GUARD 只负责选出候选对象；
hook QC/regen 必须发生在公共最终提交管线内，之后统一跑一次完整 finalize 和最终语义审计。
如果保留现结构，至少在 hook 被替换后强制重新 finalize，并废弃旧 audit、重新审计新 revision；
但长期应收敛为单一提交函数，避免正常路径与恢复路径再次漂移。

### P1-C：`motion_segment_kf_align` 通过改写时间语义消除缺帧

`step01_script_gen/constraints/motion_segment_kf_align.py:143-178` 读取已有 KF 落点，把
未被 KF 覆盖的非空 motion 时段并入相邻窗口，然后重写 `motion_desc`。它被注册在审计
边界之前，因此屏障把它视为合法的 pre-temporal 写入。

“文本一字不丢”不能证明语义不变。最小反例：

```text
原：0-2秒 拔刀 / 2-4秒 刺入 / 4-6秒 倒地；KF 只有 t=1、t=3
后：0-2秒 拔刀 / 2-6秒 刺入；倒地
```

第三段原本明确发生在 4-6 秒，改写后“刺入、倒地”共享 2-6 秒窗口。检查器从“第 3 段
缺帧”变为零告警，但没有新增任何能证明倒地瞬间的 KF。第十六次最终 hard-fix 实际对
5 镜执行了这种改写，`kf_time_window_alignment` 5→0；这解释了 PASS 的来源。

该实现处理的是症状：模型没给足 KF；它通过降低 motion 时间分辨率让现有 KF 看起来够用。
这与此前已经否决的“依据不完整字段反向改写事实源”同型，也会让下游视频 prompt 丢失明确
动作边界。

#### 根因修法

- 将 `motion_segment_kf_align` 降为 report-only，非空事件段不得自动合并；只允许清理
  真正为空的隐式尾段或纯格式噪声。
- 把 `event_uncovered` / `kf_time_window_alignment` 作为原子事务交给 LLM：以原
  `motion_desc` 为锁定事实源，只新增/重生成对应 KF；若模型要改 motion，必须同时提交
  motion + KF，并通过事件文本保留、时序单调和内容不退化验收。
- 新增反例测试，断言上面的“刺入/倒地”不会因缺 KF 被合并，告警在证据未补齐前必须保留。

### P2-C：全量测试有并发竞态

本次全量：

```text
1 failed, 1837 passed, 1 skipped
```

失败项 `test_candidate_early_stop_skips_slow_candidates` 断言函数返回时
`calls["n"] == 3`。生产代码已 `submit` 三个 future，但命中早停后使用
`shutdown(wait=False)` 立即返回；第三个 worker 是否已进入 `fake_gen()` 取决于调度，实测只进入
2 个。该测试独立重跑为 `1 passed`，符合竞态特征。

测试应验证“三个 future 已提交”和“调用方没有等待慢任务”，不要在无同步原语时断言三个
worker 都已开始。可在 fake worker 中使用 `Barrier/Event`，或在 executor 提交层计数。

### 对文档现结论的修正

- `step01_quality.json=PASS` 是事实；“step01 已可进 step02”不是事实。
- 第十六次证明了最终结构化质量检查可归零，但没有证明最终语义审计闭环；当前
  `require_semantic_gate()` 已确定性失败。
- 在 P0-C/P0-D/P1-C 修复前，不建议启动 step02a；修复后必须重跑 step01，因为最终
  semantic audit 必须针对修复后的正式 revision 重新生成，旧产物无法补写成可信审计。

## 十二、step02a 启动实测：绑定 revision 之后仍被 4 条 model-audit blocking 拒绝（2026-09-23）

第十六次 PASS 之后**第一次真正执行「跑 step02a」**（用户指令：先出 shot 0 的帧），
结论是**跑不起来**——而且卡点不止 §十一 P0-C 记的「格式/revision」那一半。

### 12.1 实测命令与结果

```text
$ python tools/run_step01_final_audit.py
{"status": "ok", "issues": 21, "revision": "6236ef931396b7cf4c0af8ab9a26b44d9ff3d7e7943f1def32951e1693e3aab5",
 "fingerprint": "7f75221ed6263cc3"}

$ require_semantic_gate(step01_quality.json, step01_audit.json, script)
QualityGateError: step01 semantic audit contains 4 blocking issue(s):
  shot=1 frame=lf code=model_audit_issue ...
```

第一段是按 §十一 要求重生成审计（1 次 deepseek 文本调用，非生图），元数据齐了、
revision 与指纹都对上了；第二段说明**门禁照样拒绝**。所以 P0-C 的根因修法（把审计
挪到唯一提交点）只解决「审计过期/格式不符」这一半，**剩下的一半是：审计报出的内容
缺陷在整条链路里没有任何修复路径**（见 12.3）。

未生图、未改代码；`step02a_frame_gen` 在 gate 处即 `raise`，没有消耗任何图片额度。

### 12.2 这 4 条是真的（逐条对过 script，不是审计误报）

| # | 位置 | 矛盾 | script 里的证据 |
|---|---|---|---|
| 1 | shot 1 lf | 离场与在场互斥 | `lf_desc` = 「…老农已走远，红日完全没入地平线」，而 `frame_layouts.lf[2]` = 群演 `on_screen: true`、pose「牵牛行走」 |
| 2 | shot 2 ff | props 与「已脱手」互斥 | `ff_desc` = 「钓杆已脱手垂湖面，酒壶已脱手」，而 `frame_layouts.ff[0].props` = `["钓杆","酒壶"]` |
| 3 | shot 5 lf | lf 是结果态却写成过程态 | `frame_layouts.lf[0].pose` = 「纵身跃起」，`lf_desc`/原文是「飞掠上船…准确地落在船梢」 |
| 4 | shot 7 lf | lf 动作在 motion 里无过程依据 | `lf_desc` = 「反手一掌，一个清脆的耳光扇在她脸上」，`motion_desc` 4-5 秒只有「听香吓得再度尖叫」 |

四条**全部落在 shot 1/2/5/7，shot 0（钩子）在本轮审计里 0 条**——即「先出 shot 0 的帧」
这件事本身不受这 4 条影响，受影响的是整条 step02a 的准入。

### 12.3 机制：step02a 能否启动，最终由审计模型的 severity 标签决定

- 审计 schema 只让模型填 `severity: blocking | warning | info`
  （`step01_script_gen/audit.py`）；
- `audit.py:194-200` 把 `severity == "blocking"` **或** `impact` 以 `blocking` 开头
  显式映射成 `visual_risk`，其余一律降为 `advisory`（注释写明「不静默降级一个明确的
  blocking 声明」——这个取舍本身是对的）；
- `shared/quality_gate.py::require_semantic_gate()` 把 `hard | visual_risk | blocking`
  当阻塞 → step02a 在 `step02a_frame_gen/main.py:230` 无条件调用它。

于是：**同一份脚本 + 同一个结构化门禁 PASS（hard=0 / visual=0），step02a 能不能跑，
取决于这次审计的模型把几条写成 `blocking`。** 本轮两次审计的差异正好落在这里：

| 审计来源 | audit_focus | 结果 |
|---|---|---|
| `step01_script_gen/main.py:5816`（跑内联，在 finalize 之前） | 无 | 21 条，前 5 条全 advisory |
| `tools/run_step01_final_audit.py`（绑最终 revision） | 8 条，含「未决项必须保留并给出原文证据，**不得为了通过门禁而删除证据**」 | 21 条，其中 4 条 model 判 `blocking` |

即：final 审计的 focus 文案本身在**要求模型不要为了过门禁而软化**，而它报出的东西
又正好是门禁的阻塞项——这是设计意图，不是误伤；但它暴露了下一条。

### 12.4 未决项：model-audit 的 blocking 没有修复路径（本次真正的死结）

- step01 的**结构化质量门**看不见这 4 条（`hard=0 / visual_risk=0`）——它们不是任何
  现有约束的判据；
- 现有**确定性约束层 88 个约束里，0 个覆盖这四类**（lf 在场/离场互斥、ff props 与
  脱手互斥、lf pose 必须结果态、lf_desc 动作需有 motion 过程依据）；
- 唯一的消费点是定向修复轮的 `_repair_shot_warnings(audit_issues=...)`，而第十六次该轮
  **被验收拒绝**（`new_blocking=1`），对最终结果零贡献；
- 于是：**审计一旦报 blocking，step01 会带着它落盘（quality PASS），step02a 会据此
  拒绝启动，而中间没有任何环节会去修它** —— 这正是 §十一 「`step01_quality.json=PASS`
  是事实、『step01 已可进 step02』不是事实」的完整机制解释。

需要定的方向（本次未做任何选择）：

| 选项 | 内容 | 代价 |
|---|---|---|
| A | 给 step02a 的 gate 加显式 opt-in 调试开关，只本次出 shot 0 的 5 帧 | 改 step02a 代码；与 §十一 结论冲突 |
| B | 为 12.2 的四类矛盾补确定性约束 → 重跑 step01 → 新审计 → step02a | 一轮 step01；约束要通用（不写死这四镜） |
| C | 修 P0-C（审计挪到唯一提交点，step01 自己 BLOCK） | 一轮 step01；仍未解决「报 blocking 后谁来修」 |
| D | B + C 一起 | 最干净但最慢；hook 可能变，shot 0 的帧要按新脚本重出 |

### 12.5 顺带查证：ref_sheet 不消耗图片额度

`ref_sheet_*.png` 由 `shared/ref_compose.py::_stitch` → `shared/portrait_stitch.py::stitch_portraits`
生成，是**本地 PIL 拼接**（打开已有 PNG → 统一高度 LANCZOS 缩放 → 白底横向贴图），
**不调用图片模型、不消耗额度**。此前「shot 0 一次 ≈11 张（含 ref_sheet ~6 张）」的估算
把产出张数当成了 API 消耗；正确口径是**只数帧 + QC 重抽**：

```text
shot 0（钩子，4.0s，3 KF）= ff + lf + kf0 + kf1 + kf2 = 5 帧
资产图（照月湾场景 / 刺客匕首 / 杨旭·听香·刺客肖像）全部 chapter 缓存命中 → 0 张
QC 重抽：QC_MAX_ATTEMPTS=1 时每帧最多 1 张 → 上限 5 张
```
