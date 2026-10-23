# Step 01 受限内容修订执行包（2026-10-22）

## 1. 执行目标、基线与授权

先完成计划层的一条连续路径：显式选择本轮事实问题 → 模型只回答正文与承载选择 → Python 编译既有 delta → 范围与结构检查 → 完整内容重验 → 交付可阅读候选和全部残余。

用户可观察目标：补齐目标事实的完整表达，不增加重复节拍，不改变已有声音、其他事实与顺序。局部目标完成与整计划可执行分别报告。仅取得协议通过不能算内容收益。

本文件是调查后给执行者的规格，**不新增实施、真实调用、正式采纳、提交或发布授权**。本轮只创建计划文档。执行者取得实施授权后完成步骤 1—6；真实验证另按步骤 7 核对授权。无需为包内常规实现选择逐次确认。

取证基线：本地 HEAD `a353b9e`。制定计划时已有脏文件：

- `shared/presentation_contract.py`
- `docs/archive/step01-failure-analysis-2026-10-22.md`
- `docs/archive/step01-handoff-2026-10-21.md`

上述文件不得整文件回滚。计划收尾时检测到并行提交 `605d051`（update），将这三个文件的改动提交；已复核 scope 与 accept 的先后顺序仍同上文取证。开始实施时重新记录 HEAD、上述文件及所有实际受测依赖摘要，核对并行变化；本文件的行号仅作导航，函数名为定位依据。

本包不修改旁白预算、声音模式、事实标准、语义核验等级、分镜门禁或重生成策略；这些改变无法与本包共用一个收益归因。

## 2. 当前代码已确认的链路

| 入口 | 当前行为 | 对本包的含义 |
|---|---|---|
| `outline.run_plan_revision`（约 3546） | 控制器接收完整验收问题，再调用 `revise_plan_once` | 当前没有本轮范围参数；完整验收必须保留 |
| `outline.revise_plan_once`（约 3309） | 先按问题分路，再判断槽位能否覆盖整轮 | 事实问题混入声音问题时，可能整轮退出槽位路径 |
| `outline._content_slots_applicable`（约 2663） | 任一 `unexpressible` 即整轮不适用；身份错误另行零调用拒绝 | 不能靠给 `SLOT_CODE_KIND` 加常量假装实现声音修复 |
| `plan_content_repair.content_slots` / `slot_request` | 已支持事实画面改写、补绑、新建及卡片收窄，并提供事实、声音和保护上下文 | 复用；本包只启用事实改写/补绑所需部分 |
| `plan_content_repair.compile_delta`（约 804） | 改写复制原节拍，仅替换正文；由代码装配绑定、身份与顺序 | 不让模型回抄实体表、声音引用、order 或摘要 |
| `outline._replan_by_slots`（约 2705） | 生成、解析、编译，再调用 `apply_delta_view(..., accept=...)` | 还未把本轮 scope 传入应用器 |
| `presentation_contract.apply_delta_view`（约 2866） | 合并、reconcile、`apply_editable_view(..., accept=...)`，最后才检查 scope | 范围检查晚于 accept；生产 accept 可触发内容核验 |
| `outline.complete_candidate`（约 2611） | 投影、附核验、完整验收 | 继续作为唯一候选完成出口；核验身份不得伪造 |

范围守卫还有明确覆盖边界：当前 `scope_violations` 比较声音集合/类型/去空白正文、节拍顺序/voice_ref/部分正文、事实绑定；并未实现“除了允许字段，一切都冻结”的通用保证。调用前后的说话人、出处、事件归属、hook、卡片等保护不能仅凭函数注释认定已覆盖。

这些是静态调用链结论，本轮未执行入口回放或测试；实施包需用下面的对照证明行为。

## 3. 步骤 1：冻结样本与成功条件

1. 复核 `docs/archive/step01-failure-analysis-2026-10-22.md` 的 E1/E2 与 `docs/archive/step01-handoff-2026-10-21.md` 所指冻结文件。逐个核存在性、完整摘要、原文身份、当前候选和核验记录的绑定；不得按最新目录选择输入。
2. 历史专项以 `d11_run/frozen_rounds3/` 为指定候选来源，目标沿用事实 f08 / 节拍 b09；读取实际文件确认后才使用。历史路径只属于该专项，不进入生产逻辑或基础测试默认值。
3. 先读原文及完整主张，写清必须保留的主体、动作、条件与回应。该案不能只补“威胁”，漏掉完整主张中的其他内容。
4. 原样保存声音集合、全部声音字段及归属、节拍集合与顺序、非目标事实绑定、事实基础、出处、事件归属、hook 和卡片。确定性派生字段单列，标明唯一生产者，不混作可自由编辑字段。
5. 从受控匿名数据构造两个不同情节/关系的自足样本，长度、实体数量、身份及声音分布不同。先证明正向基线合法，再注入“事实只表达一部分”等目标缺陷；历史专项独立记账。

完成条件：输入清单、完整主张、目标问题、允许字段和保护清单均固定。历史文件缺失时停止依赖该文件的专项，仍可做匿名入口验证，不用模型重建现场。

## 4. 步骤 2：收窄修订任务，保留完整验收

在 `outline.py` 既有入口显式区分：

- **完整问题集**：由现有完整验收得到，最终状态与残余始终基于它。
- **本轮修订目标**：从当前完整问题中显式选定的 `(code, stable_uid)`，供槽位构造与本轮作答。
- **本轮编辑范围**：沿用 scope 的允许事实绑定及节拍正文集合；保护面按步骤 1 固定。

建议用可选关键字参数沿 `run_plan_revision → revise_plan_once → _replan_by_slots` 透传；具体参数名由执行者处理。不向既有位置参数插入参数，未启用受限任务时保持原行为。

执行要求：

1. 目标必须能在当前记录与当前视图中明确定位；目标不存在、空白、类型错误或权限自相矛盾，发送前结构化拒绝。不得猜目标或静默扩大范围。
2. 本包只支持事实画面槽位的 `rewrite_visual` / `bind_visual`。按 scope 收窄 `target_uids` 和动作集合，禁用新增节拍、声音编辑和卡片编辑；请求与回答 schema、准入检查消费同一份实际槽位。
3. 混合问题中的声音等非目标问题仍作为上下文和最终残余保留，但不迫使本轮事实修订退出槽位路径。不得删掉核验轴或从完整验收中过滤它们。
4. 显式受限任务表达不了时，返回具体能力缺口，不自动升级为完整视图、预算重选、来源改写或宽 delta 请求。普通非受限任务仍沿既有路由。
5. 本包离线与真实专项只执行一轮修订，用现有一次入口/额度完成；不新增通用调度器或控制器停止协议。若通过控制器运行，修订目标只约束 revise 输入，accept 仍看完整计划；额度耗尽不写成整计划成功。

完成条件：同一完整问题集中，事实目标进入窄槽位；声音问题保留；无法表达与身份失效可区分且零发送。不能通过 monkeypatch 路由器来证明生产分派正确。

## 5. 步骤 3：同源请求、编译和前置保护

### 5.1 请求与编译

- `slot_request` / `slot_prompt` 显式给出本轮可改范围、不可改内容及目标完整主张。事实和声音上下文继续保留，不能为缩短提示而删掉必要证据。
- 模型只交槽位回答。例如下列为匿名形状说明，实际身份均来自当轮请求：

```json
{"schema":"plan_content_repair.answer.v1","answers":[{"slot_id":"<请求中的事实槽位>","action":"rewrite_visual","target_uid":"<允许的节拍>","text":"<完整表达本轮主张的画面正文>"}]}
```

- 复用 `answer_problems → compile_delta`，不新建第二套编号/引用同步规则。已有承声节拍可被改画面正文，声音引用与声音单元保持不变；共享节拍上其他事实与 hook 的完整表达仍须保护。
- 请求留档复用 `shared/revision_request_evidence.py` 与现有调用归档机制；保存实际槽位/范围、实际 schema、原始回答及编译结果，不能只保存摘要代替内容。

### 5.2 保护顺序与覆盖

1. 复用并补齐当前 scope 机制，先验证范围对象的键、类型、合法身份和空值语义。`None` 表示兼容旧行为；空允许集合不能解释为无限制；非法对象不得静默当空范围继续调用。
2. 明确保护步骤 1 的实际非目标面，特别是声音说话人及其他字段、节拍出处/事件归属、hook、卡片。生产合法的确定性派生变化按权威投影解释；不要把所有元数据一律冻结而阻断必要派生。
3. 将范围检查放到**结构规范化和身份映射后的候选、调用 accept 之前**。建议复用 `apply_editable_view` 在关系/冻结检查后、no_change/accept 前的同一位置，delta 入口透传 scope。不要复制第二套合并与关系验收。
4. reconcile 后的实际候选必须在 scope 内。其自动归并/删除若触及冻结声音，本轮拒收并保留基线；不得先借 reconcile 消掉越权再宣称未越权。
5. 通过 `scope=None` 验证现有完整视图/delta 的兼容行为；本包激活的槽位路径必须显式传 scope。拒收给出结构化 code、字段路径、原因，进入现有反馈和轮次证据。

完成条件：结构合法的越权响应在 accept 前拒收，accept 调用数为 0，内容核验发送数为 0；合法回答可到达完整重验。全程在副本操作，拒收和异常均保留原基线。

## 6. 步骤 4—6：最小充分离线验收与可复现交付

先在真实入口使用合法响应和应拒绝对照，再跑受影响回归。离线环境在文本服务与传输层阻断外部调用，路径在导入前隔离。

| 场景 | 实际入口与关键断言 |
|---|---|
| 合法改写并绑定承声节拍 | `revise_plan_once` 槽位请求 → 回答解析 → 编译 → 应用 → `complete_candidate`；目标表达完整、声音全部保留、节拍数量与顺序不变、候选可投影回读 |
| 事实问题混有声音问题 | 目标仍走槽位；完整验收仍列声音残余，整计划状态未通过；不得只断言 `kind != illegal` |
| 非法/缺失目标与范围 | 错误身份、空白、错误类型、未知范围键、越界目标；结构化拒绝，零 create_service / generate / 传输 |
| 结构闭合的越权 | 分别改声音正文/说话人/归属、非目标正文或绑定、顺序、出处/事件、hook/卡片；核相应 code/path，accept 与核验均零调用 |
| 引用破坏 | 删仍被引用声音等；关系守卫拒收，和范围拒收分账 |
| reconcile 触及保护面 | 以真实可触发归并的输入构造，最终不得删除冻结声音或改变归属；拒收留基线 |
| 内容反例 | 目标只剩部分主张、主体互换、否定或因果变化；桩按实际请求身份返回合法负面记录，残余仍在。明确这只证明消费判别，不证明真实模型识别能力 |
| 核验故障与恢复 | 缺记录、过期摘要、核验失败/超时、截止耗尽；不冒充成功、不写正式产物；已接受记录按既有机制落盘，经正式装载入口可重读且无新调用 |
| 旧调用兼容 | scope 未启用时，原合法槽位/视图/delta、no_change、拒收行为保持；不同内容与合法编号重映射走同一入口 |

选择相关测试文件，至少检查现有 `test_plan_content_slots.py`、`test_slots_binding_policy.py`、`test_plan_revision_v2_wiring.py`、`test_plan_revision_delta*.py` 与 `test_delta_reconcile_r0.py` 的适用用例。生产路由测试须遵守 `tests/step01_script_gen/conftest.py` 的 `v2_request` 标记，避免 autouse 将测试切回旧协议。

交付限制：制定计划时 `git ls-files` 未列出若干上述本地测试；`.gitignore` 中存在测试不入库的既有决策。复用本地测试可作辅助读数，但新增/依赖用例必须通过已有允许交付的受控回归入口及 fixtures 提供，或连同完整依赖作为明确的补丁/验证附件交付。不要擅自取消测试忽略策略，也不要只交文件摘要。

在仅含受控代码与实际交付夹具/依赖的隔离副本中收集并运行本包用例；移除该副本的临时输出后独立重跑。历史现场回放单独声明数据集、路径、摘要和准备方式，不混进干净检出基础测试。运行命令按最终实际交付文件给出，报告收集数与退出码，不提前许诺“全量全绿”。

完成条件：合法路径贯通、相关拒收和故障保住基线、两个不同内容样本可复现。归档可读的前后稿、完整主张对照、全部残余及未覆盖维度；复用检查通过后不重复扩大测试矩阵。

## 7. 后续真实验证与止损

仅在步骤 1—6 完成且用户已有或补充的授权明确覆盖本次输入、通道、范围和调用上限时执行。历史文档中的“下一步”不自动构成本次授权。

- 冻结同一候选、原文、目标、保护清单和实际生效配置；不重新生成整章、不改模型档位、不扩大章节。
- 最多 **1 次修订发送 + 条件满足时 1 次内容核验发送**。核对初始化/候选完成入口会不会额外核验；用生产已接受记录复用，或显式把调用纳入上限，未核清不得开跑。服务层重试也计实际发送，不能用“1 次业务调用”掩盖多次发送。
- 沿用当前生产实际启用的核验轴与身份规则，核实 `require_coherence` 等配置；不使用中性记录、不预填 supported、不关闭争议轴来取得通过。
- 修订结构/范围拒收则停止，不发候选核验；超时/协议失败或越界即记录终局，不补采样、不换模型重试。合法候选才核验，正式产物保持原状。
- 分项判断：目标事实是否完整修好、声音及非目标是否保留、新问题是否出现、完整计划是否仍被既有问题阻断。目标完成但存在声音问题时只能报告局部收益。
- 若守范围但内容仍不完整，检查原始正文和具体缺失关系，收口为本方案未取得内容收益；若核验有争议，用冻结输入人工核对主张与理由，另记判据校准问题，不按第二次 partial 自动放行。

本包终点：交付受限入口、可复现离线证据及可阅读候选；真实部分若获授权则按上述上限给出实际结果。没有真实调用时明确写“模型遵从与内容收益待验证”。报告后依授权继续不受阻工作，停止规则统一遵守 CLAUDE.md。

## 8. 本包之后的分支

1. 本包取得目标修复且保护成立，再处理声音类型/安置能力；先用完整合法成功样本核清迁移、归属、原文和预算合同，再决定是否扩展现有槽位。当前范围不把声音冻结与声音修复混用。
2. 计划层达到可执行出口后，再做 motion_segments 单源方案：复用 `shared/motion_segments.py`，核清动作段、关键帧和首尾布局的语义关系及实际消费者，再定义编译契约；不先许诺一段必配一帧或钳制即可修好。
3. `regen_structural` 止损单独处理。`main._regen_structural_over_limit` 内先执行 `_scoped_split_over_cap_shots`，关闭整函数会同时移除单镜拆分；若决定关闭整篇重生成，应保留并验证该前置能力及剩余问题出口。
4. 启发式降 WARN 与旁白弹性预算另列产品/能力评估；以具体误拒和消费者证据裁定，不以规则数量、调用轮数或单测数决定准入等级。

## 9. v5 规格复核补充（2026-10-23）

本节在 HEAD `605d051` 上复核，用于细化步骤 2—6；与前文冲突处以本节为准。不启动实施或真实调用。

### 9.1 两阶段范围：请求上限与本次有效权限

采纳 v5 的动作级收敛，但复用当前 `scope_violations` / `apply_delta_view(scope=...)` 的单一守卫；若改名或升级结构，迁移全部相关调用并提供明确兼容适配，不并存两套判据。范围构造放在既有槽位模块，守卫放共享合同模块，共享模块不反向依赖 Step 01。

1. 请求范围由本次任务权限和槽位共同决定，不能仅从全章候选节拍反推权限。`build_editable_scope` 可作为纯函数，但本包仍只允许绑定与改写，不实施插入或卡片范围的执行能力。
2. 请求将 `bind_visual` 与 `rewrite_visual` 的候选目标分开。只有可改写目标需要提供其同载完整事实/hook 的保护语义；仅可绑定目标仍需提供用于判断承载成立的正文与必要上下文。
3. 回答必须先通过实际请求的 `answer_problems`，再由代码从同一份请求权限收敛有效范围。有效权限是请求权限的子集，不能因回答自报目标就扩大；槽位冲突继续由现有编译器拒收。
4. `bind_visual` 只允许按当前绑定策略改变目标事实的 carriers，不允许改任何节拍正文。`rewrite_visual` 只允许改变选中节拍的 text 和目标事实 carriers。声音、其他字段、非目标绑定保持冻结。
5. 比较规范化后的引用身份；必要时使用正式 UID mapping，不能比较错层身份，也不能借规范化或去空白掩盖本包承诺保护的真实内容变化。

`target_uids` 覆盖全章时，现有 `protected_facts` 覆盖许多事实是该候选集合的直接后果，并不单独证明越权缺陷。改为按目标索引可以改善表达与查询，但不能在模型尚未选定目标前删除合法候选所需的保护信息；先靠显式任务范围收窄候选。

### 9.2 确定性越权与语义退化分开处理

- 未授权字段/实体/绑定变化：在 accept 前拒收，`kind=illegal`，给出具体 code/path/reason，零内容核验发送。
- 授权正文被改写，但目标主张仍不完整或同载事实被破坏：字段守卫可以通过，继续走完整语义重验；记录目标残余或保护退化，不能伪称范围守卫已经识别内容错误。
- 不扩展现有“illegal 只表示协议/结构/保护越权”的含义。语义退化不得成为可交付的成功稿，也不得正式采纳；在隔离候选上保留负面记录与基线，沿既有完整验收报告未通过。
- 正例必须读完整主张；主体、受事、否定、条件、时间、因果和回应适用时逐项保留。离线核验桩通过只证明接线和消费，不证明真实模型识别能力。已有画面完整性 XFAIL 是未解决边界，不能用本包新增布尔值把它抹掉。

### 9.3 四维证据与三态结果

在既有 evidence 中增加 `revision_outcome`，不替代原有 `applied`、残余、调用身份与阶段。四项取 `true / false / null`；每项附理由、判定阶段、候选摘要和证据来源（静态差异/离线桩/真实记录/人工评阅），`null` 明确表示未评估或证据不足。

| 项 | 成立条件 | 不得据此推断 |
|---|---|---|
| `protocol_ok` | 回答解析、准入和编译成功 | 不代表应用成功、未越权或内容正确 |
| `content_repaired` | 有效且绑定当前候选的相关核验/评阅确认目标完整表达，并核当前全部目标残余 | 不能仅靠目标 code 消失；核验故障或记录缺失时不是 true |
| `protection_intact` | 确定性保护成立，且本次改写影响的同载事实/hook 经适用重验确认未退化 | 只有字段相等时不足以证明同载语义保留；未评估语义时整体为 null，并单列已成立的结构保护 |
| `final_accepted` | 当前完整验收所有要求及有效记录均满足，按既有终局交付条件判定 | 不代表正式提交；只有局部修复成功而仍有声音残余时为 false |

核验失败/未运行时，内容维度通常是 null，完整验收仍可因缺有效记录判 false；不要把未知记为内容失败，也不要写成成功。拒收后未运行的后续阶段不得填默认 true。

### 9.4 一致性与真实入口

`outline.complete_candidate` 没有 `require_coherence` 关键字参数。需要一致性时，按实际生产初始化构造 `VerificationController(require_coherence=True, ...)`，由 `attach_verification` 在计算身份前标记计划，再获取并消费符合要求的记录；不在调用末尾补参数或补造 coherence 字段。

范围透传、动作收窄和四维分账都要通过 `revise_plan_once` 的实际生产分派验证。不能只直接调用 `_replan_by_slots`，绕开“混合问题导致整轮退出槽位”的未接线点。非目标声音问题继续留在完整验收中，本轮输入只选显式任务目标，不能把“范围同源”误作“已解决任务选择”。

## 10. 实施记录（2026-10-23）

**已接线**

- `plan_content_repair.build_editable_scope(view, slots, *, answer=None)`：请求权限列出 `allowed_fact_uids` / `allowed_rewrite_beat_uids` / `allowed_bind_beat_uids` / `allowed_insert_after_uids` / `allowed_card_ids`、`beat_protected_fields`、`frozen_collections`、`co_carried_facts_by_target`、`protected_hook`；给了 `answer` 时收敛为**有效权限**（`bind_visual` 不给正文权限；`rewrite_visual` 只给所选 `target_uid`）。`slot_request` 消费同一份并下发 `editable_scope`。
- `shared.presentation_contract.scope_violations` 扩展（旧 `{fact_bindings, beat_text}` 形状保留兼容）：新 code `scope_voice_unit_modified` / `scope_beat_voice_ref_modified` / `scope_uneditable_beat_modified` / `scope_beat_protected_field_modified` / `scope_uneditable_binding_modified` / `scope_card_modified`。`apply_delta_view(editable_scope=…)` 在 **accept / 内容核验之前** 判 `kind=illegal, stage="scope"`（越权不再等核验跑完）。
- `outline._replan_by_slots`：传入有效范围；`evidence["revision_outcome"]` 四项取 `true/false/null`，每项附 `reason / stage / candidate_digest / source`。
- 任务分派：`ROUTE_FULL_PLAN` 分支先试槽位；`verify_voice_not_speech` / `verify_voice_speaker_missing` 记为 `deferred`，事实目标与声音残余同轮时**仍走窄槽位**，声音残余留在完整验收。

**离线证据**

- `tests/step01_script_gen/test_scoped_content_repair.py`（9 例）：请求/有效范围收敛、六类越权 code 与字段路径、插入拒收、`stage="scope"` 前置拒收、混合问题仍走槽位、四维三态。
- 回归：`python -m pytest tests/step01_script_gen -q` ⇒ **3509 passed / 277 skipped / 4 xfailed**；`tests/shared` 仍为既有 16 failed（与本节无关）。

**明确未覆盖（不得据此宣称已完成）**

- `apply_editable_view`（`plan_revision.v2` 完整视图入口）尚未接 `editable_scope`，本包只打通槽位→delta 入口。
- 插入与卡片仅"不阻断"：受限模式（`allow_create=False`）下槽位不再含 `create_visual`、范围也不再有 `"*"`；**旧路径保留** create，不等于本包提交了插入执行能力。
- `content_repaired` 按核验 `code` 判定，未按目标事实逐项核对残余；`protection_intact` 只证结构面，同载语义退化仍靠完整重验。
- 真实模型遵从与真实核验收益**未验证**（本包未发模型请求）。

## 11. 复核四项修复（2026-10-23 第二轮）

| 复核项 | 修复 | 对照 |
|---|---|---|
| P1 `slot_request` 引用已删局部变量 `hook` ⇒ `NameError` | 改用同源 `protected_hook`（`plan_content_repair.py:585`） | `test_the_request_scope_reports_a_hook_bound_to_a_target_beat`：hook 绑到目标节拍时 `slot_request` 不抛，且请求里的 `protected_hook` 与范围同源 |
| P1 声音"冻结集合"只比类型+正文，改 `source` 仍 applied | `scope_violations` 改为**逐字段**比较整个声音单元，路径点名第一个变化字段（如 `narration_units[n01].source`） | `test_a_voice_source_change_is_an_out_of_scope_edit`；入口对照 `test_the_compiled_delta_...` 增加 **accept 调用数 = 0** 断言 |
| P1 受限模式仍开放 `create_visual`、无单事实目标 | `content_slots(..., allow_create=False, target_fact_uids=None)`：受限时只开绑定/改写、只给目标事实出槽位，其余记为 `deferred`；`revise_plan_once(..., target_fact_uids=…)` 逐层透传（默认 `None` 保持旧路径） | `test_the_restricted_mode_opens_only_bind_and_rewrite_for_the_target_fact`：槽位仅目标事实、无 `create_visual`、非目标进 `deferred` |
| P1 四维归因 | `_slots_revision_outcome(problems, target_fact_uids=…, co_carried=…)`：`content_repaired` 只看**目标身份**相关残余；非目标声音残余不再判 false；缺有效记录 ⇒ `null`；同载语义无证据时 `protection_intact=null` 并单列 `protection_structural` | `test_non_target_voice_residuals_do_not_mark_the_target_as_unrepaired`、`test_co_carried_semantic_protection_is_null_without_verification_evidence` |

**读数**：`tests/step01_script_gen` ⇒ **3514 passed / 277 skipped / 4 xfailed**；`test_scoped_content_repair.py` 14 例全过。

**交付约束（未解决，不能据此宣称干净检出可复现）**：`tests/step01_script_gen/*` 被 `.gitignore:125` 忽略，新增用例属本机测试；按 §6 需作为**显式补丁/验证附件**交付，或由用户决定是否调整忽略策略。本轮未改忽略策略、未提交。

**仍未覆盖**：`apply_editable_view` 未接范围；插入/卡片仍只是"不阻断"（受限模式下不再出现 `"*"`，但旧路径保留）；真实模型遵从与真实核验收益未验证。
