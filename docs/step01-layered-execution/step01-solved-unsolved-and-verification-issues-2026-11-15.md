# Step01 已解决 / 未解决 / 验证轮次问题清单（2026-11-15）

> 性质：**只读整理**，把散在 U7 §8/§9/§11/§12、帧身份对齐专题、两次真实重跑记录、
> a→j 归因清单里的结论汇到一处，逐条标注证据层级。不重跑、不改稿、不提交。
>
> **读法**：「已解决」分三档——①代码/规则已落地且有测试；②在后续真实 run 中观察到生效；
> ③仅离线证据（真实模型未触发该分支）。三者不可混用。

## 0. 一句话现状

连续 **10 个 run（a→j）全部 BLOCK，没有一个 PASS**。每修掉一个拦点，才暴露下一层；
已解决的都是**具体机制**，未解决的是**端到端通过**与若干尚未验证/尚未接线的事项。

---

## 1. 已解决（按工作包）

### 1.1 U7 前四轮：prompt / 契约 / 判据（档①，部分有档②观察）

| 来源 | 修复 | 证据 |
|---|---|---|
| §8.1 | `storyboard_refs.py` 恢复 CAMERA_MOVEMENTS 词库；`prompts.py` 补运镜段与旁白「逐字=不增删任何字符含标点」；`system.md`/`doubao_system.md` 补 camera_movement 值域、audio_cue 留白、ff/lf/kf 时序因果契约；`main.md` 尺寸锚定升级为全章绑定；`outline.py` 分句切点收紧；`feedback.py` 补不可动字段条款 | prompt 相关 13 个测试文件 **303 passed / 0 failed** |
| §9.1 | delta R0 通用化：候选池 2→4 个 kind（补 `visual→beats`、`screen_card→screen_cards`）、新增 `screen_cards[].beat_uid` 修复位、规范键写法兼容、类型不一致与未声明诊断分离 | `test_delta_reconcile_r0.py` +8 条，**36/36**；`tests/shared/` **1500 passed, 1 skipped** |
| §11.1 | 背景档两档判据：新增计划级 `background_facts`；结构校验三类畸形出口；验收门双入口同口径、只放行 `partial`；缺省从严；规则版本升 `content_verification.v5.background_tier.1`；核验模型不感知档位 | `tests/shared/` 全套 + step01 接线 145 passed |
| §11.2 | 修复额度 `DEFAULT_MAX_CALLS` 3→4（用户批准，依据 `new:n05` 拒停实测） | §12.1 后续再放宽到 6 |

### 1.2 U7 §12.1：b–h 期间七项确定性修复（档①+档②）

1. **槽位同载体冲突规则**（`plan_content_repair.py`）：拒收信息带「合并文本」出路，**run c 直接生效**（模型把 f06+f08 合并进 b05）。
2. **修订额度 4→6**（用户批准）：run d/e 均未再耗尽。
3. **主张不得超出证据**（`outline.py` 契约）：f02/f05 违规形状写成显式规则。
4. **`route_field_violation` 带通道指引**（`revision_route.py`）：指明出处轴走 `source_basis` 通道。
5. **kfs 长度校验信息带计数 + 修法**；`rejected_by_validation` 全量且拒因全为配对表长度类时**导出带反馈重试**（v6.137 通道复用，混合拒因保持停止语义）。
6. **G1 causal 族归并**（`_AUDIT_FAMILY_PATTERNS` 首位加 `causality_gap`）：`MISSING_CAUSAL_EVENT` / `ACTION_CAUSALITY_GAP` 同族，跨轮换名不再误判。
7. **总预算 900→1200s**（`STEP01_TOTAL_BUDGET_SEC`，当前默认 `1200`，`main.py:10965`）：给最终语义审计留 480s 余量。

> U7 原文记「全部带测试，回归全绿」。本轮全目录读数见 §3.3 的 14 项既有失败——
> 两者**范围不同**（U7 口径是当时受影响回归集，全目录含 `test_plan_phase_*.py`），
> 不构成矛盾，也不把全目录写成全绿。

### 1.3 帧身份对齐补正包 S01–S04（档①，部分档②，核心分支仅档③）

**A. 撤回三处证据不足的结论**（用户补正要求）

| 初版（撤回） | 补正后 |
|---|---|
| 「不导出映射就退回旧坐标比较，是 fail-closed、保证拒收」 | 实测帧数 2→3、缺映射、末尾新增空布局时旧比较**静默放行**；改为结构化违例 + `staged` 前拒收保留基线，不再退回同下标比较 |
| 「用 `kfs[i]` 逐字恒等推导映射」 | 布局恒等**不能证明帧来源**（`[A, B改, B]` 得 `[0,2]` 放行真改写）；只认装配操作来源 / 整表替换保留关键帧完整结构 / 帧数不变且授权叶子可证的恒等 |
| 「终局仍不通过」的推断式措辞 | 历史终局（冻结台账）与修复后离线裁决**严格分列** |

同时撤回「单点瓶颈」「模型能力边界」，U7 §12 轮次数 6→7，run h 归因收窄为「守卫比较基准错误 + 原始整表答卷身份无法证明」。

**B. 源码落地**

| 模块 | 内容 |
|---|---|
| `repair_fields.py` | `validate_frame_mapping` 改 `type(x) is int`（拒绝 `[False, 2.9]` / `["0","2"]`）；校验 `source ∈ {assembly, keyframe_structure, identity}`、`expected_revision`；`derive_frame_mapping_from_keyframes` 只认关键帧完整结构唯一递增对应；`compose_frame_mappings` / `verify_frame_identity_by_authorized_leaves` 组成来源链 |
| `repair_fields.py` 守卫 | `frame_layout_structural_violations(..., frame_mappings, revision)`：合法映射时把候选 `kfs` 投影回原坐标；新增帧只认整表 `keyframes`/`frame_layouts`/`frame_layouts.kfs` 级授权并做形状校验；缺映射/歧义/失配/跨 revision 结构化拒收 |
| `main.py` 装配 | `_assemble_keyframe_additions` 进入即清空输出容器，成功导出 `source="assembly"` |
| `main.py` 替换入口 | `_apply_shot_replacements` 进入即清空；来源链无法建立时在 `staged` 前拒收；修正「更新阶段落点」缺口（过去拿仍等于基线的 `_kf_base` 导出恒等映射、直到最终核对才拦且拒因不结构化） |
| `main.py` 事务主链 | 每轮新建映射并绑定本轮 revision；守卫、效果登记、回退重验共用；回退目标恢复 `identity`、其余保留；台账写最终保留稿的映射 |
| `repair_effects.py` | `blocked_frames` / `build_layout_effects` 与守卫消费同一份映射与 revision；四节点从候选起按**本轮点名目标**冻结帧结构；`structure_changes()` 只比较 merged 之后；零效果但有映射也核验 `after_count` 与身份序列；观测回调错误进裁决；`filtered_view` 按目标回退过滤效果 |
| 诊断台账 | 新增 `frame_mappings`、`frame_locator_integrity`、`writer_structure_changes`、`writer_observation_errors`、`effect_ledger_nodes`、`effects_rolled_back` |
| 交付配套 | `.gitignore` 白名单放行两个新测试；`tools/replay_run_h_frame_identity.py` 移除旧 `derive_frame_mapping` 并按新契约分列历史终局 / 修复后裁决；`run_h_frame_identity_replay.json` 覆盖重跑两次 SHA-256 一致（`c300083a…`，11,456 字节） |

**C. 修掉的三处真实生产/基建缺陷**

- 守卫「按同下标比较」把补帧后位移的保留条目误判为改写（run h 根因）。
- **纯补帧零效果时账本漏观测**：旧实现只按 `effects` 取观测目标，writer 擅自插入第 4 帧被放行。现按点名目标冻结，用例⑦在真实 `_repair_shot_warnings` 上复现并验证拒绝。
- 测试基建污染：观测异常注入直接赋值 `RepairEffectLedger._freeze` 且不撤销，导致同会话后续文件 16 个假失败；改 `monkeypatch.setattr` 自动撤销。

**D. 验收读数（档①）**：单元 44 + 真实事务入口 8 = **52 passed**；聚焦回归 18 个实存文件 **447 tests / 0 failures / 0 errors / 15 skipped**。

**E. 真实链路接线生效（档②，run j）**：映射由代码生成写入台账（`source=keyframe_structure`，绑定 `d8781a99…`）；零效果时四节点仍对全部 4 个目标做帧结构快照；`writer_structure_changes=[]`、`frame_locator_integrity=[]`；3 个越权整表补丁在授权合并阶段拒收且基线逐字保留；`layout_structural_violations=[]`，旧「字段被改写」误报未复现。

### 1.4 边界保持

`./output` 三份正式产物 mtime 未变；`.dsh/step01_full_20261115h/` 未写入；HEAD 仍为 `88404b7`；两次真实重跑各用隔离根，preflight 确认走订阅端点 `https://opencode.ai/inference/go/openai/v1/chat/completions`。

---

## 2. 未解决 / 未验证 / 未接线

### 2.1 端到端（最重要）

- **10 个 run 无一 PASS**；修复后的 run i / run j 终局仍 BLOCK。三次 run 条件不同（调用数 16/20/14、生成稿 12/17/10 镜、审计是否超时、修复轮 2/0/1），**不构成可比对照，不推算通过率**。
- **核心成功判据「合法中间补帧贯通」未在真实模型上验证**：run j 答卷里 `keyframe_additions` 出现 **0 次**、`added_indices=[]`、映射为恒等 `[0,1,2]`；守卫「按映射投影回原坐标」分支没有被真实答卷触发。该分支目前只有离线 52 passed 与 run h 派生样本 `DERIVED-R2-ASSEMBLY-01`。
- 因此 `layout_structural_violations=[]` **不能倒推成「误拒已被真实运行证明修好」**：本轮 3 个越权补丁根本没进守卫，唯一进守卫的 `shot_4` 没有帧位移。

### 2.2 明确留口（补正计划原文）

- **`retry_state` 事务拒收反馈接线未做**：本包处理的是守卫比较基准与来源证明；给模型发「保留条目」反馈会把修法引向错误方向。注意与 §1.2 第 5 项区分——那一项只覆盖**替换校验阶段**的「配对表长度类」拒因（v6.137 通道复用，混合拒因保持停止语义），而 h / j 发生在**合并后的事务验收阶段**，用户最初核对时即指出需要在事务回退分支另行接线。反馈重试、模型选择、调用额度、审美判据留后续独立决策。
- 正式产物重签、正式采纳、`git commit` **均未执行**；补丁仍在工作树未提交。
- 未重写历史 run h 的两轮事务裁决；U7 §12 只改文档结论、未改任何历史运行产物。

### 2.3 a→j 归因清单里尚未消除的类别

| 类别 | 未消除项 | 现状证据 |
|---|---|---|
| **B 答卷/授权不一致** | 模型仍反复整表覆盖 `frame_layouts` / `keyframes`，`kfs` 长度不等、整表与 `keyframe_additions` 混用、越权路径 | e 的 `raw_text_head`、h 的 `rejected`、j 的 4 个 shot 全 `updates` 且 `keyframe_additions=0` |
| **B 协议设计** | 授权粒度不统一（j 的 `shot_4` 允许整表 `frame_layouts`，`shot_1/5` 只给叶子）；prompt 约 20 条硬约束 / 三种表达形态；「联动一次改全」与「只授权叶子」有张力 | `authorization` 字段、`main.py:8931–8963` |
| **B 闭环** | 事务级拒因是否回灌下一轮请求无入口验收；run j 只有 1 轮修复轮，模型没有第二次机会 | 补正包 §8 留口 |
| ~~C 既有后处理~~（M01 已撤回） | 「36 字段被 writer 全数回退」是诊断时点缺陷造成的误读：实为事务整轮回退；34 项模型改动在后处理阶段逐字保留，纯 writer 回退 0 项 | `run_j_attribution_replay.json` |
| **B 补丁未命中问题** | shot_4 答卷未补 `STATE_CHAIN_CONFLICT` 要求的抓发／步摇掉落事件，反而把 `ff_desc` 写成「秀发披散」；确定性 checker 90→87、`resolved=0` → `no_target_resolved` | 同上 `target_problem_analysis` |
| **D 通道** | 最终语义审计 SSE 墙钟超时：g 186s、i 126s；总预算已提到 1200s，i 仍在后续 run 复现 | `step01_commit.json` 的 `audit_call_failed / transient=true` |
| **C 既有测试** | `tests/step01_script_gen/` 全目录 **14 个失败**稳定落在 5 个 `test_plan_phase_*.py`（单独跑同样 14 failed / 105 passed） | 与本包 diff hunk 无依赖关系，属既有失败，未修 |
| **测试覆盖** | `tests/` 全量**只做收集**（7560 tests collected），未运行；此前 `-x` 因缺 `dashscope` 停止的记录本轮未独立复验 | 不写成「全量全绿」 |

> 关于 B 类：**没有对照实验**能把原因单归到 prompt 表达、模型遵从、协议设计或反馈闭环，
> 本文不下这个结论；也不恢复已撤回的「单点瓶颈」「模型能力边界」。

### 2.4 更早期项目级剩余（范围外，仅索引）

`PROGRESS.md` 尾部另有 Step02a / R7 冻结等未决项（G3 未合入、`prompt_contradiction` 是否纳入 `RULE_SOURCE_FINGERPRINT` 待裁决、shot 2 FF QC 待通道恢复、本机删除审计门导致大套件一次跑变红）。这些**不属于本次 Step01 帧身份对齐工作包**，此处只作索引，不并入上面的未解决清单。

---

## 3. 验证轮次中遇到的问题

### 3.1 真实运行 a→j（每修一个才暴露下一个）

| run | 遇到的问题 | 后续是否被推进 |
|---|---|---|
| a | `delta_order_not_a_permutation`（差 `new:n05`） | §9 R0 通用化 + §11.2 额度 3→4 |
| b | 槽位同载体冲突 f06/f08 同写 b05 | §12.1① 合并文本出路，run c 生效 |
| c | 额度 4/4 用满，f04/f09 承载 partial | §12.1② 额度 4→6 |
| d | 主张超出处（f02「便」、f05「短刀」） | §12.1③ 显式规则 |
| e | `rejected_by_validation`：shot 1/3/7 kfs 长度不等 | §12.1⑤ 长度校验带计数+修法、反馈重试 |
| f | G1 同镜换名 `MISSING_CAUSAL_EVENT→ACTION_CAUSALITY_GAP` 无关闭依据 | §12.1⑥ causal 族归并 |
| g | 审计 186s 墙钟超时（SSE 4800 事件未收完） | §12.1⑦ 总预算 900→1200s；**但 i 仍复现** |
| h | 守卫帧索引错配误判（保留条目后移被报改写）+ 模型整表/additions 混用 | 补正包修守卫；**混用/越权属 B 类未消除** |
| i | 审计 126s 超时，**0 修复轮**，链未走到守卫 | 经用户批准重跑 run j 才进入修复轮 |
| j | 越权整表补丁 3 条被拒 + shot_4 补丁未命中原问题 → `no_target_resolved`；36 字段差异实为事务回滚（M01 撤回「writer 回退」旧归因） | 未解决；也未触发合法补帧 |

### 3.2 交付 / 回归过程中自身遇到的问题（均已定位并修复）

1. **只读取证阶段结论三次修正**：先误判「模型越权」→ 回放确认是守卫同下标比较误判 → 用户补正再撤回「布局恒等即可证明来源 / 必须让原始答卷通过」。
2. 复现三项守卫缺口时夹具 `entry()` 少一个参数，`TypeError: takes 2 positional arguments but 3 were given`；补默认 `gaze` 参数。
3. 补正删除旧 `derive_frame_mapping` 后，测试与回放脚本残留 import → `ImportError`；重写测试并改用 `derive_frame_mapping_from_keyframes` / `compose_frame_mappings`。
4. 重写 `test_kf_frame_identity.py` 首轮 **6 failed / 37 passed**：夹具缺 `source` 标记、整表逐字保留按新契约应通过、新增帧授权误撞候选下标、「完整覆盖」用例先报计数不符；逐条修正后 **44 passed**。
5. 事务入口测试首轮 **2 failures / 7**：其一是把「多目标局部回退」误写成「整轮否决」（探针证实守卫已判 `layout_structure_invalid` 并只回退 `shot_5`），改为单目标验整轮否决 + 新增多目标用例；其二是**真实生产缺口**（零效果账本漏观测），见 §1.3C。修后 8 passed。
6. 聚焦回归一度 **16 个失败**：`_freeze` 直接赋值污染整个 pytest 会话，改 monkeypatch 后 **447 tests / 0 failures / 0 errors**。
7. `-q` 汇总行被 `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]` 吞掉、退出码 1；改用 JUnit XML + 独立 `--basetemp` 取得干净读数。
8. 初版文档提及的 3 个「受影响测试文件」（`test_frame_layout_entry_delete.py`、`test_kf_pairing_assembly.py`、`test_repair_effects.py`）**磁盘上不存在**，那组读数无法按所列命令复跑；改用 18 个实存文件重新定义聚焦集。
9. 回放时发现替换入口**更新阶段落点缺口**：无 `keyframe_additions` 的整表替换拿基线 vs 基线导出恒等映射、直到最终核对才拦且拒因不结构化；改为取授权合并后的候选，直接报「帧来源无法证明」。
10. 全目录回归 **14 个既有 plan 阶段失败**如实单列，不写成全绿，也不冒充本包引入。

### 3.3 回归读数口径（避免互相矛盾）

| 集合 | 读数 | 性质 |
|---|---|---|
| 单元 44 + 事务 8 | **52 passed**, exit 0 | 本包直接验收 |
| 18 文件聚焦回归 | **447 tests / 0 failures / 0 errors / 15 skipped**（432 passed, 14 skipped, 1 xfailed） | 本包可复跑确定读数 |
| `tests/step01_script_gen/` 全目录 | **4701 tests / 14 failures / 4389 passed / 294 skipped / 4 xfailed**（335.4s） | 14 项既有失败单列 |
| `tests/` 全量 | 仅 `--collect-only`：**7560 collected**, exit 0 | 未运行 |

---

## 4. 关联文件

- `U7-consistency-contract-proposal-2026-11-15.md` §8.1 / §9.1 / §11 / §12.1 / §12.2
- `kf-frame-identity-alignment-2026-11-15.md`（S01–S04 补正交付，§7 验收、§8 剩余缺口）
- `step01-fix-verify-20261115i.md`（run i / run j 真实重跑如实记账）
- `step01-runs-a-to-j-block-reasons-2026-11-15.md` + `step01_runs_a_to_j_evidence.json`（逐轮归因）
- `step01_fix_verify_20261115i_evidence.json`（h / i / j 三方结构化对比）
- `run_h_frame_identity_replay.json`（历史终局与修复后离线裁决分列）

**边界**：本文件为只读整理；未重跑、未改稿、未提交，HEAD 仍为
`88404b7cb49729dc082d8cc43941464a3c63c9f9`。不推算通过率，不下单点瓶颈或模型能力结论。
