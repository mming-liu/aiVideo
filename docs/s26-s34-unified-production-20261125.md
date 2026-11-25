# S26–S34：合并两版能力、放宽单集时长与统一生产主链

**执行日期：** 2026-11-25
**代码基线：** `93ea975`（工作树另有 Step02a 并行脏文件，本包未触碰）
**结论先行：** 配置、统一入口、共同事务与离线评分链已落盘并有无模型测试；真实跑批完成了受控样本的“计划 + 独立内容核验”，但完整分镜候选仍暴露 `plan_refs` 缺失/时长不足，**尚未达到可正式采纳的媒体候选**。本包未覆盖正式产物、未批量生图、未发起任何视频付费调用。

## 一、交付状态（按承诺分层）

| 层 | 结果 | 证据 |
|---|---|---|
| 代码落盘 | 完成：时长/声音统一配置、统一规划入口、验证接线、共同事务、镜头设计结果、缓存身份 | `shared/config.py`、`step01_script_gen/unified_plan.py`、`plan_transaction.py`、`orchestrator/regen.py` |
| 离线验收 | 新增 6 条契约测试通过；`tests/step01_script_gen` 4756 passed／`tests/shared` 1564 passed | `tests/step01_script_gen/test_s34_contract.py` |
| 离线回放 | 完成：两章原始稿在旧/新时长带各回放一次，0 模型调用 | `.tmp/s26/s28_replay_summary.json`、`.tmp/s26/s28_report.md` |
| 真实计划 | 受控样本通过统一入口 + `VerificationController`（9 事实／2 声音／9 节拍）；第二章 S23 verified plan 回挂新带后 `plan_acceptance_problems=[]`、生成前 `feasible` | `.tmp/s26/real_sample/`、`.tmp/s26/ch2_verified_plan.json` |
| 真实分镜 | 受控样本 9 镜／39.0s（BLOCK）；第二章实际候选 20 镜／130.5s、契约通过但 6 类 blocking；完整 main 链 20 镜／140s＋钩子、`commit=BLOCK`；**无可直接消费候选** | `.tmp/s26/real_sample/candidate_storyboard.json`、`.tmp/s26/ch2_story/`、`.dsh/step01_budget_frozen/20261126T002932-27928/` |
| 正式产物 | **未触碰**：10 个冻结输入 + 391 个正式/图片保护文件 SHA-256 无变化 | `.tmp/s26/protection_check.json` |
| 图片／视频 | 未发起；下游资产仅给准备清单，不冒充可消费 | 见 §八 |

## 二、S26 冻结与保护

产物：`.tmp/s26/freeze.json`、`.tmp/s26/protection_check.json`、`tools/s26_freeze.py`。

冻结的 10 个输入：小说原文、第一/二章原文、第一章原始分镜（从响应封装 `text` 中解析出的 18 镜／92.5s）、第一章人工收口稿（14 镜／84.5s）、两章计划、第二章原始响应／解析稿／收口稿。新配置文件（60–120／85–110／12 条／360 字）在导入业务模块前写入进程，冻结记录显示 `agent_plan`、`deepseek-v4.1-flash`、订阅端点 `is_subscription_endpoint=true`、单镜 cap 12.0s。

保护集：正式 `chapter_001` 与 `global` 下 391 个文件（含图片、提交/采纳记录；排除大体量日志/备份/快照），基线 SHA-256 与任务后 `--check` 全部一致；无“未变化”被误记成“无基线”的条目。运行脚本不写正式目录。

## 三、S34 时长与声音配置贯通

### 3.1 生效值

| 配置 | 旧默认（保留） | 本包候选 |
|---|---:|---:|
| 合法正片 | 60–90s | **60–120s** |
| 优先目标 | 70–80s | **85–110s** |
| 声音条数 | 10 | **12** |
| 总汉字 | 280 | **360** |
| 单条策略 | 42 字 | 42 字（实际可播性仍由单镜估时判定） |
| 软目标 | 75s（目标带中点） | **97.5s**（不是硬门） |

`shared.config.voice_allocation_limits()` 统一返回条数、总汉字、单条策略、硬上限、软目标、单镜能力与目标带；`lightweight_voice_repair`、`voice_allocation`、`lightweight_plan`、提示与缓存键消费同一结果。未显式配置时旧默认保持不变；条数显式大于 12 仍按历史行为夹到 12，汉字非法值在发送前失败。

### 3.2 提示与修订语义

删改的冲突指令：

- 删除“未到目标下限就给镜头加秒”。
- 删除“超过目标上限就减秒/合镜”。
- 删除“超时强制截断”；改为重新安排表达与镜头，不按比例压缩、不截断声音/动作。
- 目标带只作优先；84s、116s 等合法稿不因偏离目标带被强制改写。
- 镜头数按内容安排，不再规定必须 15–20 镜；提示里的镜数只是反推参考。

受影响位置：`prompts._build_total_duration_constraint`、`prompts._build_regen_iron_rules`、`outline.OUTLINE_PROMPT`、`outline._calibrate_time_budget`、`orchestrator/regen._regen_over_limit/_structural_feedback`、`validation` 与 `quality_issues` 的时长文案。

### 3.3 缓存与旧章

- `outline.generate_outline_result` 缓存键加入 `duration_band` 与 `voice_limits`。
- `lightweight_plan._cache_key` 同样加入两项；统一入口另有 `chapter_outline.unified.cache.json`。
- 旧正式章仍通过 `production_context` / 采纳收据继承原时长带；`shared.config` 的默认值未改，旧身份继续是 60–90／70–80／10／280。
- 脚本缓存：默认旧模式且旧配置时 `RULE_VERSION` 逐字不变；统一/轻量或显式新配置时才把 `plan_mode` 与配置身份并入，避免旧稿被当成本轮结果。

## 四、S27／S29 共同计划与生产入口

### 4.1 统一入口

`step01_script_gen/unified_plan.py::generate_unified_outline_result()`：

- `lightweight` 源（默认）：轻量规划 → 原版 `VerificationController` → 记录盖章 → `lightweight_plan.content_verification=verified` → 统一验收；失败即 `executable=False`。
- `legacy` 源：复用原版完整规划（因果、动机、情绪、资产、核验与修订）。
- 两者共用同一结果骨架、同一 `executable` 判据、同一缓存身份与同一失败记录形状。
- `concurrent_extract.extract_all_concurrent()` 在 `STEP01_PLAN_MODE=unified` 时路由到该入口；`main → concurrent_extract → 共同计划 → 生成前检查 → _generate_script_candidates → gen_spy_script` 链未新增默认粗分镜调用。

### 4.2 共同事务

`step01_script_gen/plan_transaction.py` 抽出冻结块清单、冻结差异、可重分配关系块、视图准入与投影；`lightweight_plan.compile_reallocated_plan()` 保留原签名并改用 `frozen_drift`，旧入口兼容。内容变化后旧核验失效：统一入口只在没有有效记录时重验，成功后记录绑定新摘要。

### 4.3 本次修复的真实缺陷

1. 轻量计划的 `lightweight_plan.content_verification` 从 `not_run` 改为 `verified` 会改变 `plan_digest_of()`，导致刚核验的记录被判 `verify_digest_mismatch`。修法：该身份块是过程状态，加入 `_NON_CONTENT_KEYS`，不参与内容摘要。
2. `_fail()` 原先只写 failure、不把内层已置的 `executable=True` 拉回 false。现在失败结果显式 `executable=False`。
3. 统一缓存只在记录有效且身份块为 `verified` 时写入；空记录不再跳过核验。

## 五、S28 新时长离线回放（0 模型调用）

| 章／配置 | 正片 | 时长问题 | 其他质量码 |
|---|---:|---:|---:|
| ch1 旧 60–90／70–80／10／280 | 92.5s | 1 | 30 |
| ch1 新 60–120／85–110／12／360 | 92.5s | **0** | 30 |
| ch2 旧 60–90／70–80／10／280 | 116.0s | 1 | 29 |
| ch2 新 60–120／85–110／12／360 | 116.0s | **0** | 29 |

结论（只证工程与结构，不证媒体效果）：

- 放宽后，第一、二章原始稿的“总时长超上限”问题消失；92.5s 与 116s 均合法，未被自动改写。
- 时长以外的结构/质量风险仍在：ch1 30 类、ch2 29 类（含动作重叠、关键帧缺失、轴线/景别、角色一致性等）。**“总时长通过”不等于全稿通过。**
- 原始稿与人工收口稿的逐镜差异见 `.tmp/s26/s28_report.md`。ch1 原始 18 镜 → 收口 14 镜，ch2 原始 17 镜 → 收口 12 镜；收口稿解决了部分合镜/关键帧问题，但仍有顾问项，不能证明放宽时长会自动带来质量提升。

## 六、S30 结构修订与按需镜头重规划

新增 `orchestrator.regen.generate_shot_design_result()`，仅结构修订路径使用：

- 输入点名超载镜头与完整稿，输出子镜、**显式父子映射**、`plan_refs_inherited`、拒绝原因与调用数；
- 子镜由既有 `_scoped_split_one` 生成并校验台词逐字连续、容量、视觉重复；
- 不把 `temporal_plan` 当自动合镜依据，不按相似度猜引用；
- 失败保留基线，不写入正式产物。

新增回归 `test_shot_design_result_has_explicit_mapping`。本次**未**在真实稿上触发结构修订，因此“合法响应经实际入口到达终验”只在离线契约层成立，真实结构修复仍是下一阶段。

## 七、S31 离线验收

新增 `tests/step01_script_gen/test_s34_contract.py`（6 条，全过）：

1. 新带 60–120／85–110、12 条／360 字同时到达配置、提示、预算判断、应用器与缓存键；84s、116s 无时长问题，120.5s 正确拒收；目标带内/外不触发校准。
2. 未显式配置时仍是 60–90／70–80／10／280，证明没有悄悄改旧默认。
3. 统一入口返回统一合同、写统一缓存并在第二次调用命中（含“未验证不写缓存”的修复）。
4. 镜头设计结果给出显式前后映射与 `plan_refs` 继承标记。
5. `verified` 身份块变化不改变内容摘要（真实 `verify_digest_mismatch` 的回归）。
6. `_fail()` 失败结果显式 `executable=False`（内层已置 true 也拉回）。

最终全量（工作树）：`tests/step01_script_gen` **4756 passed / 294 skipped / 4 xfailed**（188s），`tests/shared` **1564 passed / 1 skipped**（17s）；新增契约测试 6 passed。skip 主要来自缺少归档回放 harness/外部数据集，属既有边界。

干净检出：`git worktree add --detach .tmp/s26/clean2 9227c6f`（第二轮代码提交；早期 `5f85b3a` 的 184 passed 见 `.tmp/s26/clean_junit.xml`），运行**已跟踪**的受影响测试（`test_s34_contract` / `test_duration_band` / `test_lightweight_plan` / `test_voice_allocation` / `test_compile_reallocated_plan` / `tests/shared/test_content_verification.py`）→ **184 passed**（10.9s），JUnit 证据 `.tmp/s26/clean2_junit.xml`。注意：`test_redguo_duration_guard.py` 在 `.gitignore` 的测试白名单里未入库，干净检出无法收集，故未计入；这属于既有测试入库边界，不是本包新增失败。

## 七bis、第二轮真实推进：第二章 verified plan 与超时修订（2026-11-26）

### 7bis.1 第二章计划在新时长带重新通过

S23 的 `VerificationController` 记录原绑定 `ad5ce471…`。第一轮曾把 `lightweight_plan` 整块加入非内容摘要，导致旧记录被判 `verify_digest_mismatch`；本轮撤回该改动，改为**不改身份块**，只在计划上挂 `plan_verification`。同时修正两处：

- 多承载事实的结构校验不再以“轻量/无核验”为条件；verified 计划也走完整多承载集合核对。
- 多承载递归只做单载体结构校验（`require_verification=False`），避免在收缩副本上误报摘要不符；完整记录的检查留在完整计划末尾。

结果：`plan_acceptance_problems(ch2_verified_plan)` 返回 `[]`，`check_plan_before_generation` 在新带 `feasible`；产物 `.tmp/s26/ch2_verified_plan.json`。

### 7bis.2 真实分镜候选仍然超时

从同一计划经实际 `_generate_script_candidates` 入口生成：

| 次数 | 结果 |
|---|---|
| 第 1 次完整候选 | 20 镜／130.5s，`plan_refs` 20/20、事件契约通过；质量 82、BLOCK；6 类 blocking（duration、kf、weapon_holder 等） |
| 超时修订第 1 次 | 128.0s；未通过不退化验收（武器锚点退化、关键词丢失），**保留 130.5s 基线** |
| 超时修订第 2 次 | 140.5s，反而更差；**保留基线** |
| 超时修订（新增“镜数≈节拍数就先合并节拍”反馈） | 133.0s，仍超 120s；**保留基线**。该机制未能让模型合并镜头 |
| 从 verified plan 走完整 `main()`（20 次发送上限、2 轮审计） | 20 镜／140.0s＋钩子 10.6s；6 镜未解决，运行预算不足，`commit=BLOCK`；冻结候选 `.dsh/step01_budget_frozen/20261126T002932-27928/`，正式产物未替换 |

结论：S30 的“无改善保留基线”有效；“>120s 重新安排”在真实模型上没有收敛到合法稿。继续同一提示/同一计划重试属于无效加码，需先改计划节拍/镜头安排，而不是继续加提示或加预算。

### 7bis.3 既有收口稿在新带下的复核

| 稿件 | 镜数／时长 | 结果 |
|---|---:|---|
| ch1 `.tmp/s14b/runs/s18_final/storyboard.json` | 14／84.5s | 时长合法；blocking 5 类（kf_event_window、kf_segment、kf_temporal、multi_person、shot_duration_below_min），不可直接消费 |
| ch2 `.tmp/s19_ch2/s24_final_storyboard.json` | 12／88.625s | 时长合法；blocking 3 类（kf_event_window、layout_presence_gap、weapon_holder），不可直接消费 |

因此“时长放宽”只解除了时长门，**没有自动解除关键帧/布局/武器持有等阻塞**；这正是不能用“总时长通过”替代全稿验收的证据。

## 八、S32 有限真实验证（只列已发生的事实）

| 调用 | 输入 | 结果 | 账本 |
|---|---|---|---:|
| 轻量统一计划（第二章冻结原文，新带/12/360） | 第二章 4516 字 | 2 次回答均不合格：18 声音/8 事实，`budget_unrepresentable` 多条；修订又把 `unfixable` 写成非法形状 | 2 |
| legacy 统一计划（第二章，新带） | 同上 | 9 事实/9 节拍，但核验 4 次后额度耗尽，`verification_failed` | 4+ |
| 第二章 S23 记录回挂（新带） | `.tmp/s19_ch2/candidate_plan.json` | 12 事实／10 声音／20 节拍，`plan_acceptance_problems=[]`，生成前 `feasible` | 0 |
| 第二章实际候选（新带） | 上述 verified plan | 20 镜／130.5s，`plan_refs` 20/20、事件契约通过；质量 82、BLOCK、6 类 blocking | 1 |
| 第二章超时修订 | 130.5s 候选 | 第 1 次 128.0s，因武器/关键词退化拒收；第 2 次 140.5s，无改善；均保留 130.5s 基线 | 2 |
| 第二章完整 main 链 | 上述 verified plan | 20 镜／140.0s＋钩子 10.6s；`commit=BLOCK`，冻结候选，正式产物未替换 | 5 |
| 受控样本统一计划 | 4 句匿名账册情节 | **成功**：9 事实／2 声音／9 节拍，`plan_verification.status=ok`，验收空 | 1 计划 + 核验 |
| 受控样本单候选直调 | 同一已验证计划 | 9 镜／40.0s，`plan_refs` 整稿缺失，`check_event_contract=false` | 1 完整生成 |
| 受控样本**实际候选入口** | 同一已验证计划 | 第 1 次响应 710 个 shots 元素中 702 个非对象，判解析失败；第 2 次通过结构门与事件契约（9/9 `plan_refs`），但 9 镜／39.0s、评分 73、BLOCK | 2 完整生成 |

说明：

- 受控样本计划首次真实核验后因上面的 `plan_digest` 缺陷被判“摘要不符”；修复后从同一真实响应/缓存重放通过，**不是新的一次成功调用**。
- 完整分镜先走真实 `gen_spy_script` 单候选，因整稿缺 `plan_refs` 未过事件契约；再走**实际 `_generate_script_candidates` 入口**，第一次 JSON 结构失败，第二次通过事件契约（9/9 声明），但 9 镜／39.0s、质量分 73 / BLOCK。说明“共同计划可达完整生成与契约重试”成立，“生成结果可消费”仍不成立。
- 9 节拍被压成 9 镜、总时长 39s，是“按节拍逐条拍一镜”的典型塌缩；本包未用静默加秒/合镜掩盖，保留为真实质量风险。
- 第二章轻量/legacy 两次失败分别暴露选材/预算与核验额度问题，不能据此推算成功率，也不能宣称章节不可行。
- 真实 A/B（同一第二章计划只换 60–90 与 60–120 各生成一次）**未执行**；当前只有离线 A/B 回放，不能冒充真实收益对照。
- 用户已授权的订阅通道为 `agent_plan` / `deepseek-v4.1-flash` / `https://opencode.ai/inference/go/openai/v1`；本包未使用 DeepSeek 官方按次计费 API。已并入账本的真实调用 **23 条**（`real` 14 + `real_sample` 3 + ch2 直调 1 + ch2 完整链 5，含核验重试）；另有受控样本实际候选 1 次、ch2 超时修订 2 次由各自脚本发起、未并入这些账本。**0 图片、0 视频付费调用**。

## 九、S33 下游准备与正式采纳边界

- **可消费候选：** 第二章 verified plan（12 事实／10 声音／20 节拍、66.7s 下界）已在新带通过计划验收与生成前门；但由它生成的实际分镜为 20 镜／130.5s（完整 main 链 140s＋钩子），超上限且质量 BLOCK，**不能进入 Step02a 批量生图**。受控样本计划同样只到计划层；其 9 镜／39s 分镜也不足 60s。当前可交付的是**计划 + 真实失败/拒绝证据**，不是媒体候选。
- **正式产物：** 未覆盖、未切换全局配置、未更新采纳收据；旧正式章继续用自己的 60–90／70–80／10／280 身份。
- **资产/帧清单：** 受控样本需要 2 个角色（管理员、助手）、档案室/铁柜/工位台灯/备用钥匙等场景与道具；首尾帧数量按最终分镜镜数计算，**当前分镜不合法，不生成帧清单**。第一、二章沿用既有候选/收口稿，不因本次代码变更自动重做。
- **费用：** 图片按实际帧数×模型档位、视频按每镜秒数×模型单价另算；本包没有可验证的统一涨幅，不写“增加 25%–35%”。
- **恢复：** 正式产物拒绝/异常不覆盖基线；新配置不得通过全局环境泄漏到旧章进程；所有真实验证产物在 `.tmp/s26/`。

## 十、剩余缺口（阻断“可评阅媒体候选”的问题）

1. 受控样本实际候选已能声明 `plan_refs`，但 9 节拍仍被压成 9 镜／39s，质量分 73 BLOCK；需真实重生成/结构修订验证“补内容而非加秒”，当前不能进入生图。
2. 第二章轻量源的声音预算/协议遵从仍不足；legacy 源的核验额度在 9 事实时耗尽，需判断是模型反复 partial 还是核验提示/额度策略问题，不能直接加额度掩盖。
3. 真实 A/B 未做；S28 只证“时长门不再误伤 92.5/116”，不证“放宽后质量更好”。
4. 干净检出对账未做；当前全量测试仍需在交付提交上复跑并逐用例报告新增失败。
5. `char_layout.surface`、运镜/景别、关键帧锚点等顾问项仍在；未做 Step02a 消费、图片复用和费用清单。
6. S27 的“合并”目前是**合同/核验/入口层归一**（legacy 与 lightweight 两种来源），还不是单一提示同时产出“因果/动机/资产 + 紧凑声音归属”的一次请求；该提示级合并未做，不能记为已完成。
7. 第二章当前计划的内容下限 66.7s，真实分镜却按“20 节拍≈20 镜×6.5-7s”落到 130-140s；三次超时修订（128／140.5／133s）一次退化、两次变差；即便加入“镜数≈节拍数就先合并节拍”的反馈也没有收敛。继续同提示重试已无收益，下一步应先改**计划节拍/镜头安排**（合并同场连续节拍、明确每镜承载多节拍），不是继续加提示或加发送预算。
8. 受控样本只有 4 句原文，模型自然落到 39s；它不能证明真实章节的时长能力，也不能用“补空镜/加秒”凑到 60s。

## 十一、代码与证据索引

- 配置与限制：`shared/config.py`（`voice_allocation_limits`）
- 声音事务：`step01_script_gen/voice_allocation.py`、`lightweight_voice_repair.py`
- 规划：`step01_script_gen/unified_plan.py`、`plan_transaction.py`、`lightweight_plan.py`、`outline.py`
- 提示与修订：`step01_script_gen/prompts.py`、`orchestrator/regen.py`、`quality_issues.py`、`validation.py`
- 门面/入口：`step01_script_gen/concurrent_extract.py`、`step01_script_gen/main.py`
- 工具：`tools/s26_freeze.py`、`tools/replay_duration_budget.py`
- 测试：`tests/step01_script_gen/test_s34_contract.py`
- 现场证据：`.tmp/s26/freeze.json`、`protection_check.json`、`s28_report.md`、`real_sample/result.json`、`real_sample/storyboard.json`、`real/result_ch2_legacy.json`
