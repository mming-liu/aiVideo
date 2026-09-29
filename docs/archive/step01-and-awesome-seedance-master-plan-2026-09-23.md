# step01 收敛与 awesome-seedance 吸收总计划

> 日期：2026-09-23  
> 状态：实施计划；本轮只写文档，不修改代码、配置、测试和生产数据。  
> 适用范围：step01 最终产物闭环、step02 准入、awesome-seedance 可吸收能力。  
> 上游复核：[step01 收敛修复复核](./step01-convergence-fixes-review-2026-09-22.md)  
> 既有对照：[awesome-seedance 与 aiVideo 功能对照及吸收计划](./awesome-seedance-comparison-adoption-plan-2026-09-22.md)  
> 既有下游计划：[awesome-seedance 非 step01 吸收计划](./awesome-seedance-non-step01-adoption-plan-2026-09-23.md)  
> 外部参考：[LearnPrompt/awesome-seedance](https://github.com/LearnPrompt/awesome-seedance)

## 1. 最终目标与总决策

目标不是让 `step01_quality.json` 单独显示 `PASS`，而是让同一份、不可再修改的
`script_result.json` 同时满足以下条件：

1. 确定性质量门通过；
2. 最终语义审计通过；
3. `script_result.json`、`step01_quality.json`、`step01_audit.json` 绑定同一 revision；
4. step02a 用真实 `require_semantic_gate()` 可以直接消费；
5. 进入 step02 后，再吸收 awesome-seedance 对审核、参考图和 A/B 评测有明确收益的能力。

本计划采用以下总决策：

- **不绕过 step02a gate**。目录 12 的选项 A 仅保留为紧急诊断预案，不进入正常实施。
- **采用改进版 D**：实现 C 的唯一提交点，同时补通用检测和受限字段的定向修复。
  不把四条真实语义矛盾全部写成确定性“自动改内容”的约束。
- **BEST-GUARD 只选择候选**。hook 检查、finalize、最终审计和三份产物提交只走一条公共路径。
- **最终审计之后禁止任何语义写入**。审计结果出现 blocking 时回到修复事务；修不掉则
  step01 自己输出 BLOCK，不能把问题留给 step02a 才发现。
- **`motion_desc` 是动作时序事实源**。缺 KF 时不得通过合并非空动作时段隐藏缺证据。
- **先收敛 step01，再做真实生图/视频 A/B**。不依赖 step01 的离线工具可以提前开发，
  但只能标记为 `diagnostic` 或 `upstream_blocked`，不能宣称生产可用。

### 1.1 方向复核与决策状态

2026-09-23 再次复核后，原计划有多处写成“实现时再选”，会导致开发过程中重新争论架构。
现在统一收敛如下：

| 议题 | 最终方向 | 状态 |
|---|---|---|
| 三份正式产物如何提交 | 先生成全部 staged 文件并完成自验，再按 `audit → quality → script` 替换，最后原子写 `step01_commit.json`；manifest 是提交完成标志 | 已确定 |
| 字段互相矛盾时听谁的 | 原文/明确旁白与对白 > 事件计划与 motion > FF/KF/LF 描述 > frame layout > 聚合 prompt；高层自身冲突时整镜 regen | 已确定 |
| 四类矛盾是否全部写正则 hard-fix | 采用混合方案：确定性层只做高置信结构关系检查；开放中文语义继续由 final audit 发现，禁止宽泛关键词自动改稿 | 已确定 |
| cache 命中是否重新调用 audit | revision + rule fingerprint + audit scope 完全相同且旧 audit 为 `ok` 时复用；任一变化才重新审计 | 已确定 |
| model-audit 波动怎么处理 | 显式 blocking 一律 fail-closed；修复后必须做目标复核，最终再做一次全量审计；不得靠重复抽样等问题偶然消失 | 已确定 |
| BEST-GUARD 候选最终审计失败怎么办 | 第一版不在多个历史快照间循环试运气；选中稿修复到上限仍失败就 BLOCK，保留诊断证据 | 已确定 |
| 是否绕过 step02a gate 先生成 shot 0 | 正常实施不绕过；只有用户以后明确要求一次性诊断生成时另立任务 | 已确定 |
| 当前脏工作树如何成为实施基线 | 用户先提交当前未提交和未跟踪改动；后续以该 Git commit 作为实施基线。提交完成前不开始代码实施 | **已确认（2026-09-23）** |
| semantic audit/repair 的调用预算 | 最多 2 个修复轮，每轮一次目标复核，最后一次全量审计；到上限仍有 blocking 就停止并输出 BLOCK | **已确认（2026-09-23）** |

上述方向均已确认，后续实施不再临时改方向。如果实际代码证明某项不可行，先更新本计划和
失败证据，再调整实现。

### 1.2 事实源与修复原则

跨字段不一致按以下优先级判断：

```text
小说原文、明确旁白/对白
  > temporal_plan / motion_desc 中的事件事实与顺序
  > ff_desc / keyframes / lf_desc 的阶段画面
  > frame_layouts 的在场、姿态和持物
  > 为模型拼接的聚合 prompt
```

这个优先级只用于决定修复目标，不能证明高层字段天然正确：

- 原文、旁白和事件计划互相冲突时，不猜测正确答案，整镜 regen；
- 高层事实清楚、低层派生字段矛盾时，只修低层字段；
- shot 7 这类“LF 结果有原文依据，但 motion 缺过程”的情况，保留 LF 结果，补 motion 和
  必要 KF，并做事件顺序与内容保留验收；
- 聚合 prompt 永远由结构字段重新生成，不作为反向修改结构字段的依据。

### 1.3 final audit 的稳定性规则

- 第一版沿用现有文本 provider/model，不新增供应商、不为审计切换付费模型；模型、温度和
  prompt 版本写入 audit 元数据；
- 目标复核只回答“上一轮 blocking 是否解决”，不能替代最终全量审计；
- 已出现的 blocking 进入本轮 unresolved ledger。只有确定性检查通过，或目标复核明确确认
  修复，才能从 ledger 删除；全量审计这次没有再次提到它，不算自动解决；
- 同一 revision 不通过反复调用 full audit 获取一次偶然 PASS；
- 新出现的 blocking 加入 ledger，并重新走字段授权与验收；到预算上限仍存在就 BLOCK。

## 2. 当前状态基线

### 2.1 已完成，但仍需一次完整真实重跑确认

以下修复已存在于当前工作树，并有针对性测试或历史重跑证据。后续不重复设计，只做回归和
真实链路复验：

| 项目 | 当前状态 | 计划中的处理 |
|---|---|---|
| 候选验收比较全部 blocking，而非只比较局部告警 | 已实现 | 纳入回归集 |
| 开篇空镜约束不再与结构屏障互相冲突 | 已改为 report-only | 纳入结构守卫回归 |
| 结构屏障识别等数量对象替换 | 已实现 | 纳入对抗用例 |
| prompt 时长上限与模型能力表同源 | 已实现 | 覆盖多 cap 用例 |
| 修复轮字段授权与越权拒绝 | 已有 `repair_fields.py` | 扩展语义审计 issue 映射 |
| audit issue 的“本轮新解决”判据 | 已实现 | 防止空转轮重复消耗模型调用 |

这些内容的状态应写作“代码已落地、最终生产链待复验”，不能写成“整个 step01 已完成”。

### 2.2 仍需解决的 step01 问题

| 优先级 | 问题 | 当前影响 | 目标状态 |
|---|---|---|---|
| P0 | 最终 audit 的生产时点与消费契约断裂 | step02a 会拒绝正式产物 | 三份产物同 revision、同规则指纹、同审计范围 |
| P0 | BEST-GUARD 在 finalize 后仍可能重生 hook | 最终 hook 未被完整审计 | BEST-GUARD 只选候选，所有写入进入统一提交管线 |
| P0 | 目录 12 的四条真实 blocking 没有闭环修复责任 | quality PASS 仍无法进入 step02a | 通用检测 → 授权修复 → 重新检查 → 最终审计 |
| P1 | `motion_segment_kf_align` 合并非空时段掩盖缺 KF | 告警归零但动作证据没有增加 | 非空动作段 report-only，缺帧交原子修复 |
| P2 | 候选早停测试依赖线程调度 | 全量测试偶发 1 failed | 测试提交事实和“不等待”，不用 worker 启动数作断言 |

### 2.3 目录 12 的四条真实阻塞

| 位置 | 类型 | 当前矛盾 | 通用规则名（计划） | 首选修复责任 |
|---|---|---|---|---|
| shot 1 LF | 在场状态 | `lf_desc` 已离场，layout 仍 `on_screen=true` | `frame_presence_consistency` | 定向修复 `frame_layouts.lf`；若文本本身含糊则整镜 regen |
| shot 2 FF | 道具持有状态 | 描述已脱手，props 仍持有 | `prop_possession_consistency` | 定向修复 `frame_layouts.ff.props`；不删除描述证据 |
| shot 5 LF | 帧阶段错误 | LF 应是落地结果，pose 仍是跃起过程 | `frame_stage_pose_consistency` | 定向修复 `frame_layouts.lf.pose` 或 LF 相关字段 |
| shot 7 LF | 动作链缺口 | LF 出现耳光，motion 无对应过程 | `result_action_support` | 优先补 `motion_desc`/KF；同时提交时做事件保留验收 |

这四个名称代表**检测器职责**，不代表四个无条件 hard-fix。检测可以是确定性的，语义改写
必须经过字段授权、内容保留和候选验收。对于否定、离场、脱手等中文语义，第一版宁可少报并
交 final audit，也不能用宽泛关键词批量改错其他镜头。

## 3. 目标流水线

最终执行顺序固定为：

```text
候选生成 / 结构收敛 / 定向修复 / hook 生成与 QC
                    ↓
BEST-GUARD 只选择“本次候选或历史候选”
                    ↓
公共 pre-commit 修复（允许语义写入的最后阶段）
                    ↓
finalize_step01_script（最后一次确定性写入）
                    ↓
生成 revision + 确定性 quality report
                    ↓
对同一不可变对象执行 final semantic audit
          ┌─────────┴─────────┐
          │无 blocking         │有 blocking / audit 失败
          ↓                    ↓
原子提交三份产物          回到受限修复事务；到上限则 BLOCK
          ↓
require_semantic_gate 自验
          ↓
step02a
```

这里的“原子提交”指逻辑上的同一提交批次，不宣称文件系统支持三个文件同时原子替换：

1. 在同一章节目录生成三份 staged JSON；
2. 对 staged 文件调用真实 `require_semantic_gate()`；
3. 自验通过后依次替换正式 `step01_audit.json`、`step01_quality.json`、
   `script_result.json`，把 script 放在最后；
4. 最后原子写 `step01_commit.json`，记录 revision、fingerprint、scope 和三份文件摘要；
5. 下游除三份原有检查外，还要核对 commit manifest；缺失或不匹配一律 fail-closed。

进程若在替换中途崩溃，正式路径可能暂时混合新旧文件，但 manifest 不会匹配，因此不会被误用。
最近一份完整 eligible 版本保留在 versioned snapshot 中用于人工恢复；本阶段不引入数据库或目录
指针系统，也不承诺崩溃后正式路径仍自动保持上一版可用。

## 4. 分阶段实施计划

### 阶段 0：冻结基线和建立可复验样本

**目的**：保护当前大量未提交工作，确保后续每项修改都能与第十六次产物比较。

工作内容：

1. 用户提交当前未提交和未跟踪改动后，记录该基线 commit、Git 状态和涉及文件；
   不执行 reset、clean 或覆盖用户改动；
2. 固定第十六次 `script_result.json`、quality、final audit 和四条 blocking 为只读 fixture；
3. 保存两类最小反例：
   - 三个非空 motion 时段、只有两个 KF；
   - 目录 12 的四类跨字段矛盾；
4. 明确基线数字：11 镜、73.02 秒、结构化 hard=0、visual_risk=0、final audit blocking=4；
5. 记录现有全量测试基线：历史复核为 `1 failed / 1837 passed / 1 skipped`，失败项为并发竞态。

交付物：fixture、基线说明、可重复的本地检查命令。  
退出条件：同一 fixture 能稳定复现四条 blocking 和 motion/KF 反例。

### 阶段 1：先纠正会隐藏问题的规则

**目的**：让后续 PASS 代表问题真的解决，而不是改写检测输入。

#### 1.1 收紧 `motion_segment_kf_align`

- 非空事件段没有 KF 覆盖时只报告，不合并相邻时段；
- 只允许清理纯格式噪声或真正空的隐式尾段；
- `event_uncovered` / `kf_time_window_alignment` 进入修复事务；
- 默认锁定原 `motion_desc`，只允许新增或调整相关 KF；
- 如果模型必须改 motion，必须把 `motion_desc + keyframes` 作为一个原子候选提交；
- 验收必须同时证明：原事件文本仍存在、事件顺序不变、KF 单调且落在镜内、没有新 blocking。

主要文件：

- `step01_script_gen/constraints/motion_segment_kf_align.py`
- `step01_script_gen/constraints/__init__.py`
- `step01_script_gen/repair_fields.py`
- 对应 constraint、repair acceptance 测试

退出条件：最小反例仍保留“尾段缺 KF”告警；只有补上有效 KF 后才归零。

#### 1.2 修复候选早停测试

- 测试 executor 是否收到三个 submit；
- 使用 `Event`/`Barrier` 控制慢任务；
- 验证早停后调用方没有等待慢 future；
- 不再断言函数返回瞬间三个 worker 都进入函数体。

退出条件：该测试连续运行和全量运行都稳定，无需修改生产早停行为。

### 阶段 2：为目录 12 的四类问题建立通用检测

**目的**：让结构化质量层看见确定存在的跨字段矛盾，并给修复事务提供准确字段范围。

实施顺序：

1. 先实现纯检查函数和最小反例；
2. 每个 issue 输出稳定的 `code / shot_id / frame_id / entity_id / field_path / evidence`；
3. 只有高置信矛盾进入 `visual_risk`；含糊语义输出 advisory，交 final audit 判断；
4. 在 `repair_fields.py` 登记每个 issue 允许修改的最小字段集合；
5. 检测器不得直接改写分镜内容。

建议字段授权：

| issue | 默认允许字段 | 禁止顺带修改 |
|---|---|---|
| `frame_presence_consistency` | 对应 frame 的 `frame_layouts` 条目 | narration、其他帧、角色身份 |
| `prop_possession_consistency` | 对应角色在对应 frame 的 `props` | 道具资产、其他角色 props、故事文本 |
| `frame_stage_pose_consistency` | 对应 frame 的 pose；必要时对应 desc | motion、旁白、时长，除非另有 issue 授权 |
| `result_action_support` | `motion_desc`、相关 KF desc/stage/t_sec | LF 结果事实、旁白、无关事件 |

测试至少覆盖：

- 四条真实正例；
- “仍在远处可见”不能被误判为已离场；
- “手边放着道具”不能被误判为持有；
- FF 的准备动作、KF 的过程动作、LF 的结果动作职责区分；
- LF 只是环境结果时不强求 motion 动作；
- 同镜多角色、多道具不串实体；
- issue 身份在文案轻微变化时保持稳定。

退出条件：四条 fixture 稳定命中，反例不误报，且没有任何自动语义改写。

### 阶段 3：闭合“审计发现 → 受限修复 → 重新验证”

**目的**：解决“final audit 报 blocking，但没有人负责修”的死结。

修复事务统一使用现有候选验收框架，补齐以下行为：

1. final audit blocking 按镜头归组；
2. `field_path` 可映射到安全字段时进入定向修复；无法映射时进入整镜 regen，而不是放宽权限；
3. LLM 收到原文证据、当前镜头、具体 issue、允许字段和禁止字段；
4. 合并候选时只接受授权字段；
5. 验收同时检查：
   - 本轮至少真正解决一个目标 issue；
   - 原 blocking 的证据消失或新的检测结果确认已解决；
   - 没有新增 hard/visual_risk；
   - 未点名镜头和锁定字段不变；
   - 对话、关键事件、角色和道具身份不退化；
6. 重新运行确定性 quality 和聚焦 semantic audit；
7. 最多两轮。连续一轮零改善、checker 失败、越权或内容退化时立即停止并 BLOCK。

对于目录 12 四条问题，验收不能只做 evidence 子串消失检查，还要重新运行相应通用检测。
否则模型只改写措辞就可能骗过字符串判据。

退出条件：四条 fixture 可以形成“检测 → 授权候选 → 验收”的完整测试；错误候选被拒绝，
合法最小修复被接受。

### 阶段 4：建立唯一最终提交点

**目的**：消除正常路径、缓存路径、BEST-GUARD 恢复路径之间的时序漂移。

#### 4.1 拆分选择与提交

- `_cross_run_best_guard()` 只返回被选中的候选及来源元数据；
- 它不落盘、不 finalize、不运行 hook regen、不写 quality/audit；
- 历史快照经过当前版本的质量初筛后才能成为候选，但所有正式处理仍走公共管线。

#### 4.2 统一 pre-commit

对选定候选依次执行：

1. hook QC/必要的 hook regen；
2. 资产和元数据补齐；
3. `finalize_step01_script()`；
4. `ensure_script_revision()`；
5. 确定性 quality report；
6. final semantic audit；
7. audit blocking 如可修复，回到阶段 3，然后重新执行 3–6；
8. 无 blocking 后写入三份正式产物；
9. 对 staged 三件套调用 `require_semantic_gate()` 自验；
10. 按 audit → quality → script 顺序替换正式文件，最后写 `step01_commit.json`；
11. 用正式路径再调用一次 `require_semantic_gate()` 并校验 commit manifest。

#### 4.3 统一 audit 契约

`step01_audit.json` 至少包含：

```text
status
model
error
issues
input_revision
rule_source_fingerprint
audit_scope
created_at / run_id（诊断字段，不参与准入语义）
```

失败语义：

- request timeout、request_failed、parse_failed、缺少 scope、revision 或 fingerprint 不一致，
  都由 step01 标记 BLOCK；
- “调用失败”不能等价于“0 issue”；
- audit 为 BLOCK 时只写带 run id 的诊断产物和 snapshot，不写新的 commit manifest；
  正式路径如因进程中断产生混合版本，manifest 校验必须拒绝。

#### 4.4 写后不可变守卫

- final audit 输入对象在 audit 前生成 revision；
- audit 后再次计算 revision，必须完全相同；
- audit 到三份文件提交之间不允许 hook、hard-fix、post-process 或元数据补写；
- 任何变化都使本轮 audit 作废并回到 pre-commit，不能只补写 revision。

主要文件：

- `step01_script_gen/main.py`
- `step01_script_gen/audit.py`
- `shared/quality_gate.py`
- `tools/run_step01_final_audit.py`（保留为诊断/补审计工具，不作为生产闭环替代品）
- `step01_commit.json` 的生成与消费逻辑
- producer-consumer 集成测试

退出条件：正常、缓存、BEST-GUARD 三条入口都只命中一个正式提交函数；不存在 audit 后写入。

### 阶段 5：测试闸门

按以下顺序运行，上一层不过不进入下一层：

1. **小单元测试**：四类一致性检测、motion/KF 反例、字段授权、audit 新解决判据；
2. **事务测试**：越权、删内容、新增 blocking、零改善、checker 异常全部拒绝；
3. **提交顺序测试**：正常、缓存、BEST-GUARD 的最终 revision 均一致；
4. **生产者—消费者集成测试**：真实 producer 写三份临时产物，随后真实
   `require_semantic_gate()` 读取，不分别 mock；
5. **step01 全量测试**；
6. **项目相关回归**：step02a gate、step02 prompt builder、共享质量门。

必须包含的关键断言：

- audit 后脚本 revision 不变；
- staged 三件套通过后才生成 commit manifest；manifest 缺失、旧 revision 或摘要不符均拒绝；
- BEST-GUARD 恢复且 hook 重生时，最终 audit 针对重生后的 hook；
- audit timeout/parse failure 使 step01 BLOCK；
- `step01_quality=PASS` 但 audit blocking 时整体仍是 BLOCK；
- 三份产物任一旧 revision 都被拒绝；
- 无 KF 证据时，motion 告警不会被合并文本消除；
- 并发早停测试稳定。

退出条件：全量测试无失败；跳过项必须与本次改动无关并有明确原因。

### 阶段 6：真实章节重跑与 step02a 准入

只在阶段 1–5 完成后执行一次完整 step01 重跑。不得把旧 final audit 手工补字段后当作验收。

真实重跑验收表：

| 检查项 | 通过标准 |
|---|---|
| 时长 | 主分镜 60–90 秒，hook 和 timeline 口径一致 |
| 结构质量 | hard=0、visual_risk=0 |
| 四条旧 blocking | shot 1/2/5/7 原证据消失，通用检测不再命中 |
| motion/KF | 非空事件段均有真实 KF 证据，未靠时段合并归零 |
| 三份 revision | 完全一致 |
| 规则指纹 | audit 与 quality 一致且等于当前规则源 |
| audit scope | 非空，覆盖 hook、全部 shots 和关键跨字段关系 |
| 最终不可变 | audit 前后 revision 相同 |
| 下游消费 | `require_semantic_gate()` 真实调用通过 |
| 内容完整性 | 对话、关键事件、人物和道具身份不比基线退化 |

若真实重跑仍有 blocking：保存本轮诊断产物，不启动 step02a；按 issue 类型回到阶段 2 或 3，
不通过增加绕过开关继续推进。

准入通过后，先做 shot 0 的小范围生图验证：

- FF + LF + 3 KF，共 5 个模型生成帧；
- `ref_sheet` 是本地 PIL 拼接，计入 `local_derived_images`，不计入 API 图片额度；
- 缓存资产命中计 0 次 API 调用；
- 检查角色一致性、动作阶段、构图、缺图和 QC 重抽次数；
- 通过后才扩大到整章 step02a。

### 阶段 7：吸收 awesome-seedance 的非 step01 能力

step01 收敛期间可以做离线实现，但真实 A/B 必须等待阶段 6 的 `eligible` 产物。

| 顺序 | 工作包 | 价值 | 与 step01 的关系 | 实施门槛 |
|---:|---|---|---|---|
| 1 | FF/KF/LF 审核联系表 | 快速发现角色漂移、方向翻转、阶段缺失 | 只读现有帧 | 可提前做离线工具 |
| 2 | 视频 prompt 基线与 diff 报告 | 看清 provider 最终收到的文本和参考图顺序 | 使用固定 fixture | 可提前做离线工具 |
| 3 | r2v 参考图继承/排除规则 | 降低背景、姿势、拼表布局污染 | 不改 step01 schema | 先单测；真实验证等 r2v 模型 |
| 4 | step02 质量报告增强 | 关联 prompt 版本、参考图、人工评分和成本 | 读取 step01 gate 状态 | 可提前实现 |
| 5 | 视频级 A/B 协议 | 判断改法是否真的改善成片 | 需要合格脚本和生成预算 | 阶段 6 后执行 |

详细范围、文件和验收沿用[非 step01 吸收计划](./awesome-seedance-non-step01-adoption-plan-2026-09-23.md)。

下游报告统一使用三种状态：

- `diagnostic`：静态或离线结果，仅用于观察；
- `upstream_blocked`：step01 最终 gate 未通过，禁止发起新生成；
- `eligible`：revision、fingerprint、audit scope 和 blocking 检查全部通过。

### 阶段 8：再评估 awesome-seedance 的 step01 增强项

以下三项确实有价值，但它们属于内容能力增强，不与本轮可靠性修复混在同一批：

1. **动作因果链**：明确动作触发、过程和结果；
2. **镜内状态交接**：前一阶段结果成为下一阶段输入，减少瞬移；
3. **对白表演链**：台词与表情、视线、停顿、动作配合。

实施规则：

- 每次只引入一个变量；
- 先用固定章节离线 prompt 对照，再做小样视频 A/B；
- 同时比较可执行性、人物一致性、动作完整性、时长、prompt 长度和模型费用；
- 任何增强都必须继续通过阶段 5 的全部门禁；
- 没有稳定提升就回退模板，不扩展 schema 和 constraint 数量。

## 5. 实施批次与依赖关系

```text
M0 基线冻结
 ├─ M1 motion/KF 规则纠偏 + 并发测试稳定
 └─ M2 四类通用检测
          ↓
M3 审计 blocking 修复事务
          ↓
M4 唯一最终提交点
          ↓
M5 全量测试与 producer-consumer 集成通过
          ↓
M6 真实 step01 重跑 + semantic gate 通过
          ↓
M7 shot 0 → 整章 step02a
          ↓
M8 下游 prompt/r2v/视频 A/B
          ↓
M9 step01 内容增强逐项 A/B
```

建议拆成独立提交，便于回滚和审查：

1. `test: freeze step01 semantic blockers and timing regressions`
2. `fix: stop rewriting nonempty motion segments to satisfy KF coverage`
3. `feat: detect cross-field frame and action contradictions`
4. `fix: constrain semantic-audit repair transactions`
5. `refactor: route all step01 outputs through one final commit`
6. `test: verify step01 producer-consumer semantic gate`
7. 后续再分别提交联系表、prompt bench、r2v 规则和报告增强。

不把全部内容放进一个大提交。每个提交只在自身测试通过后进入下一步。

## 6. 停止条件与回退原则

出现以下任一情况时停止当前批次，不继续真实生成：

- audit 或 deterministic checker 异常；
- 候选修复新增 blocking；
- 未授权字段、未点名镜头或关键内容被改；
- audit 前后 revision 不一致；
- 三份产物 revision/fingerprint 不一致；
- 同一修复轮零改善；
- 四条旧 blocking 仅靠换措辞消失，但通用检测仍命中；
- motion 缺 KF 仅靠合并时段归零；
- 全量测试出现新的稳定失败。

回退以提交为单位，不删除诊断产物，不修改 `.env.keys`，不切换付费模型来掩盖逻辑问题。
BEST-GUARD 的历史候选只能重新进入公共提交管线，不能直接覆盖正式输出。

## 7. 完成定义

本计划的 step01 部分只有同时满足以下条件才算完成：

- [ ] P0-C、P0-D、P1-C、P2-C 均有代码和回归测试；
- [ ] 目录 12 四类问题有通用检测和明确修复责任；
- [ ] model-audit blocking 能被安全修复，或由 step01 明确 BLOCK；
- [ ] 正常、缓存、BEST-GUARD 共用一个最终提交点；
- [ ] 最终 audit 后无语义写入；
- [ ] script、quality、audit 三份产物绑定同一 revision 和规则指纹；
- [ ] `step01_commit.json` 与三份正式产物的 revision、scope 和摘要一致；
- [ ] 生产者—消费者集成测试调用真实 `require_semantic_gate()` 通过；
- [ ] 全量测试通过；
- [ ] 真实章节重跑通过，四条旧 blocking 消失且内容无退化；
- [ ] shot 0 五帧小样通过人工检查后，才批准整章 step02a。

awesome-seedance 吸收部分按工作包独立验收。联系表和离线 benchmark 完成不代表视频质量已经
提升；只有固定素材、固定参数的视频 A/B 达到预设阈值，才把新规则并入生产默认路径。

## 8. 后续执行时的第一批任务

后续按本计划推进时，第一批只做以下四件事：

1. 建立第十六次产物和六类最小反例 fixture；
2. 把 `motion_segment_kf_align` 对非空事件段改为 report-only；
3. 稳定候选早停并发测试；
4. 为目录 12 四类矛盾先写检测测试和纯检测实现，不立即接 LLM 修复。

这一批完成并通过小测试后，再进入修复事务和唯一提交点。这样每一步都有可观察结果，失败时
也能准确判断是“检测不准”“修复越权”还是“提交时序错误”。

---

## 9. 实施记录（2026-09-23）

> 本节由实施过程追加，记录**实际落地了什么、证据是什么、以及两处刻意不做的事**。
> 计划正文（§1–§8）保持原样，作为契约。

### 9.1 已落地

| 阶段 | 交付 | 主要文件 |
|---|---|---|
| 0 | 第十六次产物 + 两处最小反例 fixture + 基线数字（内容摘要冻结） | `tests/step01_script_gen/fixtures/`、`fixtures/BASELINE.md`、`test_fixture_integrity.py` |
| 1.1 | `motion_segment_kf_align` **只报告不合并**（连空段也不动，见 §9.3） | `constraints/motion_segment_kf_align.py` + 重写的测试 |
| 1.2 | 候选早停测试改 `Barrier`+`Event`（不再断言线程调度事实） | `test_candidate_parallel.py` |
| 2 | 四类跨字段**纯检测**：在场 / 持物 / 帧阶段 / 结果动作支撑 | `cross_field_checks.py`、`quality_issues.collect_structured_issues`、`repair_fields` |
| 3 | 修复事务：精确字段授权、证据保留闸门、`evidence_removed` 拒收理由 | `repair_fields.py`、`main._cross_field_evidence_removed`、`_repair_acceptance_decision` |
| 4 | **唯一最终提交点** + audit 契约 + commit manifest + 写后不可变守卫 | `main._commit_step01_outputs`、`shared/quality_gate.py`、step02a/02b/03/04 |
| 5 | 生产者—消费者集成测试（真实文件 + 真实 gate 函数） | `test_step01_commit.py` |
| 7A | FF/KF/LF 审核联系表（本地 PIL，0 API 调用） | `tools/build_storyboard_contact_sheet.py` |
| 7B | 视频 prompt 三层基线报告（concise → +参考图 → provider 压缩） | `tools/bench_video_prompt_patterns.py`、`tests/fixtures/video_prompt_benchmark.json` |
| 7C | r2v 参考图「继承 / 禁止继承」边界 + 预算优先级 | `step02_video_gen/prompt_builder.py`（`_R2V_KIND_INHERIT_EXCLUDE` / `r2v_reference_budget`） |
| 7D | 视频级 A/B 协议（生成门槛 + 隔离目录 + 门槛判定） | `tools/video_ab_experiment.py` |
| 7E | step02 报告实验上下文字段 | `step02_video_gen/experiment_context.py` |

### 9.2 可复算证据

```bash
# 全量
python -m pytest tests/ -q
# 定向：四类检测 / 时段方向 / 提交时序 / 联系表 / prompt 基线 / r2v 策略 / A/B 协议
python -m pytest tests/step01_script_gen/test_cross_field_checks.py                  tests/step01_script_gen/constraints/test_motion_segment_kf_align.py                  tests/step01_script_gen/test_step01_commit.py                  tests/tools -q
```

```text
全量测试     1943 passed, 1 skipped（连续多次一致；阶段 1.2 的偶发失败已消除）
四类检测     5 条命中，覆盖目录 12 四条真实 blocking（shot 1 kf2+lf、2 ff、5 lf、7 lf）
检测性质     纯读（跑前跑后 `script` 逐字节相同）；身份 = code/shot/帧/实体/字段，不含文案
提交时序     staged 三件套 → 真实 require_semantic_gate() → audit→quality→script → manifest
拒绝路径     manifest 缺失 / 非 COMMITTED / 摘要不符 / 旧 revision 四种都拒绝
```

### 9.3 两处**刻意不做**（附理由）

1. **不为「推不出字段路径」的审计 issue 实现自动整镜 regen**（阶段 3 第 2 条）。
   现状：这类目标不进入定向修复，也不放宽授权；它们保留到最终审计，仍不通过就由
   提交点 **BLOCK**（`main.py` 里对应日志已明确写出「本实现没有自动整镜 regen 路径」）。
   理由：目录 12 的四条 blocking **全部**有可映射字段路径，新增一条从未被真实数据
   走到的 LLM 写入路径，与「每一步都有可观察结果」冲突；宁可以 BLOCK 暴露，也不发
   一条无法验证的自动改写。若将来出现真实「无路径」blocking，再按阶段 3 的验收补。

2. **`motion_segment_kf_align` 连「清理空段」也不做**（阶段 1.1 的括号项）。
   计划允许清理「纯格式噪声或真正空的隐式尾段」，但清理空段会**加宽时间窗**，而
   `_check_kf_time_window_alignment` 的检测 2（orphan KF）判的正是「KF 是否落在已声明
   时段内」——窗口一宽，原本落在描述空区的 KF 就被吸收、一条真实告警随之消失。对
   检测 1 中性、对检测 2 不中性，所以一个字节都不写。约束保留注册位（顺序契约不变），
   测试用「告警集合跑前跑后逐条相同」把这条不变式钉住。

### 9.4 已知取舍与遗留

- **钩子质量门在正常路径上会被调用两次**（main 的钩子分支一次、提交点一次）。钩子干净
  时第二次是**零成本 no-op**（不调模型）；只有钩子仍脏时才会多发一次重生调用——这是
  为「最终落盘的 hook 必须参加过审计」付的代价，且上限是每 run 1 次。
- **fixture 与测试不入库**：`tests/` 自 09-13 起按用户决策 gitignore。冻结结论（内容摘要、
  基线数字）因此只在本机可用；跨机器复现需要先跑一次真实章节并按 `BASELINE.md` 重算摘要。
- **`shared/paths.py` 的 `keyframe_files()` 仍按字典序**（`keyframe_10` 排在
  `keyframe_2` 前）。本计划不改 `shared/paths.py`，联系表工具在自己的读取路径上按数字
  排序；下游若依赖该函数的顺序，需要另立任务。
- **审计模型沿用现有文本 provider/model**（§1.3），未按章切换；`--model` 由
  `SCRIPT_CANDIDATE_MODELS` 单模型时取同源模型，多候选时交 provider 默认。

### 9.5 阶段 6：真实章节重跑（2026-09-23 23:40–23:54）

跑法：`python step01_script_gen/main.py`（TEXT_PROVIDER=agent_plan / TOPK=1）。

**结论：BLOCK，正式产物未替换** —— 这是设计行为，不是崩溃。

```text
23:43 候选 97 分（19 镜/119s 预投影）——只有「内容超载」，走定向拆分确定性路径
23:49 BEST-GUARD：新稿 93 分/风险 21 < 快照 100 分/风险 12 → **选中快照为候选**
23:50 提交点：hook QC → finalize → revision → quality → final audit
23:51 第 1 轮 audit：3 条 blocking（shot 2 / 5 / 7）
23:52 受限修复准入：before=11+audit2 after=9 resolved=2 new=0 → 接受（4 镜替换）
      同轮内第 2 批：resolved=0 → 拒绝并回退，循环收工
23:53 第 2 轮 audit：2 条 blocking（shot 2 / 4）——shot 1/5/7 已消解
23:54 修复轮零改善 → 停止 → BLOCK
```

落盘结果：`step01_commit.json` = `status: BLOCK / blocking_count: 2 / artifacts: {}`；
三份正式产物时间戳仍是上一版（21:55 / 22:12），**一个字节都没换**。

两条残余 blocking（都是模型审计给出的、带原文证据的真实矛盾）：

| shot | 帧 | 矛盾 | 说明 |
|---:|---|---|---|
| 2 | ff | `ff_desc` 写「钓杆已脱手垂湖面，酒壶已脱手」，而 `kf1` 写「左手钓杆垂湖面，右手提酒壶」 | **原文**是「左手钓杆垂在湖面，右手提着一只酒壶」→ 错的是**描述**，不是布局 |
| 4 | lf | `lf_desc` 写「双手捂嘴」，`motion_desc` 写「嘴巴张开…尖叫」 | 尖叫时捂嘴与张嘴互斥 |

### 9.6 真实重跑暴露的两个问题（已修）

1. **`frame_layouts.kfs` 的通配层级写少了**。它是「列表的列表」，规范化路径是
   `frame_layouts.kfs.*.*.<leaf>`（两个 `*`）；检测器只写了 `frame_layouts.kfs.*.<leaf>`
   → `path_allowed` 永不匹配 → 模型对 kf 帧的**正解被当成越权回填**（日志
   `shot 1: 未授权路径已回填 ['frame_layouts.kfs.2.1.on_screen']`）。
   已修，并加了「检测器 field_path 必须能授权真实 canonical 路径」的回归测试
   （直接从落盘 fixture 的 `flatten_paths` 取路径比对）。

2. **「证据保留闸门」的适用面错了**。原实现把在场/持物也纳入闸门，等于断言
   「描述一定是对的」——而 §1.2 的优先级里，**原文**才是真值来源，描述与布局谁错
   只有原文能判。shot 2 的正解恰恰是改描述，被闸门判成「删证据」→ 整轮 resolved=0。
   已修：闸门**只**保留 `result_action_support`（那一条方向是计划写死的：保留 LF
   结果、补 motion 过程）；在场/持物改为**两侧都授权**（布局叶子 + 同帧描述），
   方向交模型按原文切片决定，确定性层只保证「矛盾必须消失」+「不许越权改别处」。

### 9.7 第二次重跑（2026-09-24 00:01–00:12）

用 §9.6 的修正重跑。**结论仍是 BLOCK**，但失败点换了一个，并暴露出第三个问题。

```text
00:02 候选 97 分（13→16 镜，62s→97s）；00:08 三个镜仍需拆分（拆镜在生产路径已停用）
00:09-00:11 GAP 修复轮 3 轮都被接受（resolved 1/2/1）
00:11 钩子生成 → BEST-GUARD：新稿 97 分/风险 25 < 快照 100 分/风险 9 → 选中快照
00:12 提交点 → staged 自验失败 → BLOCK
```

`_staged/step01_quality.json` 显示这条被选中的快照在 finalize 之后有 **9 条确定性
blocking**：

```text
kf_time_window_alignment × 5   （motion 分了 3-4 段，只有 2 个 kf）
frame_presence_consistency × 2 （shot 1 kf2/lf——就是目录 12 原始阻塞）
kf_event_window_mismatch   × 1
result_action_support      × 1 （shot 7——也是目录 12 原始阻塞）
```

**第三处问题（已修）**：提交循环只把**审计** blocking 当作 blocking，确定性
`quality` 为 BLOCK 时既不进修复轮、也不写进 manifest——于是这条稿子一路走到 staged
自验才被拦下，`step01_commit.json` 还报 `blocking_count: 0`，失败原因写成
「staged 自验失败」。现在：

  - `blocking = 审计条目 ∪ 确定性质量门条目`（同一处判定、同一处记账）；
  - BLOCK 原因写清两侧条数：`达到 N 个修复轮上限后仍有 X 条 blocking（审计 A + 确定性质量门 B）`；
  - manifest 的 `blocking` 列表直接带上确定性条目的 `code / shot_id / field_path`，
    便于按类型回到阶段 2 或 3 定位；
  - 回归测试：确定性质量门阻塞必须真的进修复轮、且不许再出现「staged 自验失败」这种
    误导性原因。

**顺带确认的好消息**：两条目录 12 的原始阻塞（shot 1 在场、shot 7 结果动作）现在由
**确定性层**报出来（`frame_presence_consistency` / `result_action_support`）——
这正是阶段 2 的目标：模型审计看得见、确定性层也看得见，不再出现「PASS 与 blocking
互相矛盾」。

### 9.8 第三次重跑（2026-09-24 00:15–00:26）

用 §9.6 + §9.7 的修正重跑。**结论仍是 BLOCK**，但这次是「按规格 BLOCK」：

```text
00:25 BEST-GUARD 又选中同一份历史快照（100 分/风险 9）
00:25 [COMMIT] 第 1 轮发现 14 条 blocking（审计 5 + 确定性质量门 9）  ← §9.7 的修正生效
00:26 [REPAIR] before=9+audit3 after=9 resolved=0 → reason=no_target_resolved → 回退
00:26 [COMMIT] 提交前修复轮零改善，停止修复 → BLOCK（原因准确，不再是 staged 自验失败）
```

**第四处修正（已修）**：BLOCK 时清掉 `_staged/`。run 2 在 staged 自验失败留下的
`_staged/step01_quality.json` 在 run 3 之后仍滞留在章节目录里，看起来像「权威副本」。
现在 BLOCK 的四个出口都清 `_staged/`；诊断证据由 BLOCK manifest（含 blocking 明细）
+ `snapshots/` 承担。

**为什么这轮修不动**：那 9 条确定性阻塞里 5 条是 `kf_time_window_alignment`
（motion 分了 3-4 段、只有 2 个 kf）——归零**只能靠模型补有效 KF**（阶段 1.1 的
既定方向，代码不派生 kf desc）。本轮 `_repair_shot_warnings` 按
`_REPAIR_MAX_SHOTS_PER_ROUND=4` 只挑了 4 镜，模型给出的替换没有清掉任何一条
（`resolved=0`），按 §1.1「连续一轮零改善立即停止」被拒 → BLOCK。
也就是说：**机制按规格工作，剩下的缺口是模型能力**（补 KF / 消解具体内容矛盾），
不是提交时序、不是检测口径、也不是授权面。

### 9.9 尚未开始

- **阶段 6 的验收**：三次重跑全部 BLOCK（每次原因不同、每次都暴露并修掉一个真实缺陷）。
  到本记录为止**没有** COMMITTED 的正式产物 —— **step02a 未启动**，符合 §6
  「不通过增加绕过开关继续推进」。剩余阻塞集中在「模型补 KF 的能力」上，属于
  阶段 2/3 的能力问题，需要按 issue 类型单独推进（例如给补帧类问题更宽的修复轮预算、
  或允许 `motion_desc` 与 `keyframes` 作为原子候选一起改——计划 §1.1 已给出后者的方向）。
- **阶段 8**：step01 内容增强（动作因果链、镜内状态交接、对白表演链）——按计划需要
  固定素材 + 小样视频 A/B，本轮不动。

> 按 §6「回退以提交为单位，不删除诊断产物」：本轮诊断产物已保留
> （`snapshots/` 新快照 + BLOCK manifest），旧的三份正式产物未被覆盖。


---

## 10. 实施记录（2026-09-24，第四批）

> 本节由实施过程追加。§1–§8 是契约，本节记录**实际落地、证据、以及两处刻意不做的事**。

### 10.1 结论：阶段 6 通过（COMMITTED）

```text
run 11  08:48–08:50
  BEST-GUARD  选中历史快照（100 分 / 风险 0）——即 run 10 提交点打磨过的稿子
  提交点      第 1 轮：确定性 0 + 审计 5 → 修复轮 resolved=0 / audit_resolved=3 / new=1
              → 按目标回退 shot_3 → new=0 通过
              第 2 轮：对**新 revision** 全新审计 → 0 条 blocking
  [COMMIT] OK 三份产物已提交（revision=0e467d198d32，审计修复轮 1，复用审计 否）
```

验收表（§阶段 6）逐项核对，全部通过：

| 检查项 | 结果 |
|---|---|
| 时长 | 主分镜 73.0s（60–90 内），hook 6.1s |
| 结构质量 | `PASS`，hard 0、visual_risk 0 |
| 目录 12 四条旧阻塞 | 确定性侧与审计侧均不再命中 |
| motion/KF | 覆盖缺口 0 镜；关键帧 27 → 32 条（证据增加，非靠合并时段归零） |
| 三份 revision | 完全一致 `0e467d198d32` |
| 规则指纹 / audit scope | 一致 / `final_after_deterministic_fixes` |
| 最终不可变 | audit 前后 revision 相同 |
| 下游消费 | 真实 `require_semantic_gate()` + `verify_step01_commit_manifest()` 通过 |
| 内容完整性 | 镜数 11=11、非空旁白 7/11=7/11、角色集合一致（对比 §阶段 0 冻结基线） |

### 10.2 §9.9 的归因被证伪：缺口在代码，不在「模型补帧能力」

§9.9 把三次 BLOCK 归因为「模型补 KF 的能力」。按 issue 逐条查下来**不成立**——
修复 prompt 的决策树一直写着「报『kf 时段缺段』→ 按 motion_desc 时段表补 1 帧定格」，
模型也照做了，是被代码层丢掉的。本批修掉 7 处根因（每处都有真实重跑证据）：

| # | 根因 | 证据 | 修法 |
|---:|---|---|---|
| 1 | **补帧授权面缺失**：`merge_authorized` 对列表的规则是「列表路径**本身**未被授权 → 长度变化一律回填」，而 kf 类授权只有 `keyframes.*.desc/stage/t_sec` 这些叶子 | 确定性验证：授权叶子时模型返回 3 个 kf → 合并后 len=2（整表回填） | `_KF_STRUCTURAL_CODES`：对「只能靠增删帧修」的问题授权 **KF 列表本身**（`keyframes` / `frame_layouts.kfs`），结构级路径在 `_narrow` **之后**并入；配套验收见 #7 与 §10.3 |
| 2 | **帧重编号被记成「假新增」**：kf 类 issue 的定位符带位置序号，补帧后整体后移 → 同一缺陷换成新 key，`before/after` 差集记成「一减一增」 | run 6：`resolved=7 new=1` 全被这一条否掉 | `_reindexed_kf_shots`：帧数变了的镜头，kf 类身份**丢掉位置序号** |
| 3 | **整轮连坐**：一个目标引入新阻塞，同轮其它合法修复一起丢（应用侧 v6.86 已改「逐目标提交」，验收侧没跟上） | run 7：`resolved=7 new=1` → 整轮回退 | `_new_blocking_targets` + `_restore_targets`：只回退引入新阻塞的目标，重算验收后采纳；**被采纳的结果仍满足「零新增 blocking」** |
| 4 | **约束撤销修复**：`group_actor_continuity` 按 `pose not in (退下/退场/离场)` 双向赋值，把描述已写「老农已走远」的群体**翻回 true** | 真实数据实验：把 `on_screen` 改 false → `run_hard_fix` → 带名字的条目全部翻回 true（用 `on_constraint_done` 逐约束快照定位到该约束） | 对**已有条目只降不升**：pose 明确退场才写 False；非退场 pose 不构成在场证据（§1.2 描述 > 布局） |
| 5 | **告警不可执行**：只说「缺段 / 更匹配另一时段」，不说该写什么；模型的实际动作是改写/合并 motion（未授权→被回填） | run 7：`resolved=0`，日志只有「未授权路径已回填 ['motion_desc']」 | 两条告警给出**必须动作**：新增几帧、t_sec 取多少、缺段内容是什么；并显式禁止靠改写 motion 消警、禁止挪动已有帧 |
| 6 | **名额分配**：带确定性阻塞的镜头与纯审计目标在「告警条数」上打平，掉出前 4 | run 9：提交点 1 条确定性阻塞**整轮没被尝试过**（替换的是 2/5/8 等纯审计目标）→ `resolved=0` | `_pick_repair_targets`：排序主键改为「是否带确定性阻塞」，次键才是告警条数 |
| 7 | **审计判据空转**：审计证据是自由文本描述，永远不会字面出现在稿子里 → `_audit_issue_resolved` 对改前改后都返回 True → 「本轮新解决」恒为假 | run 10：确定性已归零、只剩 1 条审计条目；模型按 issue 点名的字段改了 `ff_desc`、无新阻塞，却因 `audit_resolved=0` 被判 `no_target_resolved` → BLOCK | 判据无区分度时退回「本轮是否**动过该 issue 点名的字段**」；最终仍由下一轮全量审计裁决，**不放宽最终门禁** |

### 10.3 两处流程保真（不动最终门禁）

1. **未决清单（§1.3「同一 revision 不通过反复调用 full audit 获取一次偶然 PASS」）**：
   BLOCK 时正式产物不能替换，但审计确实跑过。新增 `step01_audit.blocked.json` sidecar，
   `_reusable_final_audit` 在 revision + 规则指纹 + scope 全同时把它当**本轮未决清单**复用
   ——让修复轮去修，而不是重掷一次骰子。只复用 `status=ok` 的结论，调用失败不得被冻结。
   修复真正落地后 revision 变化，下一次审计自然是对**新对象**的全新调用。
2. **「零新增阻塞」仍是硬条件（§6）**：按目标回退后重算验收，只有 `new_blocking=0` 才采纳；
   定位不到目标（全局问题 / 新阻塞落在非目标镜头）时退回整轮回退。

### 10.4 刻意不做

1. **不降审计模型温度**：`audit.py` 已是 `temperature=0.1`。审计结论逐次波动（本轮观测 0/1/2/4/5/6/7）
   是判据本身的模糊性，§1.3 已定「不为审计切换模型」；靠调温度压方差治不了根因，只会把
   门禁变成另一个旋钮。
2. **不实现「代码派生 kf desc」**：§9.8 的既定方向仍然成立——补帧的**内容**必须来自模型，
   代码只负责把可确定的部分（段中点、缺段内容、已有帧逐字保留）算好交出去。本批两次真实
   重跑证明这条路是通的（关键帧 27 → 32，且无新增阻塞）。
3. **不在本轮动 §8 的内容增强**（动作因果链 / 镜内状态交接 / 对白表演链）：按计划需固定素材 +
   小样视频 A/B，等 step02 成片具备后再逐项做。

### 10.5 仍未做

- **阶段 7 第 5 项（视频级 A/B）**：需要 eligible 产物 + 生成预算；step01 已 eligible，待 step02 成片。
- **阶段 8**：同上。

### 10.6 同批发现的第 8 处缺陷：step02a 验收把中间产物当帧，掩盖缺图

进入 step02a 后按「shot 0 小样 → 整章」推进时，用修好的判定逐镜核对，发现：

```text
shot 3 缺 keyframe_0.png（脚本要求 4 帧：pre/approach/climax/exit，实物只有 1/2/3 号）
而 step02a_quality.json 报 PASS / missing 0
```

根因（`image_prompt_quality.write_image_product_quality`）：

```python
kfs = sorted(shot_dir.glob("keyframe_*.png"))   # 把二修标注图 keyframe_1_doodle.png 也算成帧
if len(kfs) < expected_kfs: ...                 # 数量凑够 4 → 不报缺
```

`keyframe_<N>_doodle.png` 是区域编辑的**输入**标注图（`vimax_bridge._doodle_refine_first_frame`
画了红框再送模型，结果覆盖原文件），不是产物。它带来两个后果：

1. **掩盖缺图**（本批实测）——数量被凑够，验收报假绿；
2. **下游把标注图当帧发**——`shared/paths.keyframe_files()` 与 `step02_video_gen/submit_poll.py`
   用的是同一个 glob，step02b 的 `frame_paths` 会带上带红框的图。

修法（v6.110）：

- `shared/paths.is_core_keyframe_file()`：只认 `keyframe_<N>.png`（按**整名**匹配，扩展名也算）；
- `core_keyframe_files()`：核心帧 + **数字序**（`keyframe_10` 不再排在 `keyframe_2` 前）；
  `keyframe_files()` 改为排除中间产物，**排序口径保持原样**（顺序迁移仍按 §9.4 另立任务）；
- step02a 验收改为**按序号逐一核对**（`missing=[i for i in range(expected) if not exists]`）
  —— 原实现只比数量，即便没有 doodle，「缺 0 号、多一个别的号」这种错位也照样通过；
- `submit_poll.py` / `tools/video_ab_experiment.py` 同步改用该判定。

补齐 `keyframe_0.png` 后验收转 PASS。最终：12 个单元共 58 张核心帧（24 张 FF/LF + 34 张 KF），
序号零错位，`script_revision` 与 step01 提交一致。

**教训与 §9.7/§10.2 同构**：门禁只要用「数量/存在性」这类**可被旁证凑数的指标**，
就会在真缺失时给假绿。判据必须锚在**身份**（哪一个文件）上，而不是计数。

### 10.7 外部审核 7 条（4 P1 / 3 P2）：逐条核对与修复（v6.111）

审核结论为「全量测试通过，但还有 4 个 P1 和 3 个 P2，暂不建议提交最终实现」。
逐条**先核对事实再改**——7 条全部属实，无一误报。

| # | 结论 | 实测证据 | 修法 |
|---:|---|---|---|
| P1-1 两轮预算被内部三轮放大 | **属实** | 提交点 `for _attempt in range(2+1)` 最多调两次 `_repair_shot_warnings`，而它内部 `_REPAIR_MAX_ROUNDS = 3` → 最多 **6 次** LLM 修复调用 | 新增 `max_rounds` 参数；提交路径显式传 `1`，由外层两轮精确控制预算。GAP 路径不传，保持分批收敛 |
| P1-2 跨字段检测异常静默放行 | **属实** | `cross_field_issues` 内 `except: continue`——四类矛盾只有它能看到，任何异常都让该镜**未被检测**而质量门照样可能 PASS | 改为返回 `(issues, failures)`；`collect_structured_issues` 置 `checker_failed=True` 并产生 blocking（fail-closed） |
| P1-3 参考图数量 ≠ 模型能力 | **属实** | `get_ref_limit("doubao-seedance-1-0-pro-250528")` = **12**（泛化 `seedance` 条目），而 `get_seedance_cap(...).supports_reference_image` = **False** | 新增 `shared.ref_compose.model_supports_reference_image()`：命中 Seedance 能力表以表为准，否则退回数量表；A/B 门槛改用它 |
| P1-4 `run` 没有执行生成 | **属实** | 通过全部门槛后只返回 `status=ready` + 手工说明，`generation_started` 恒为 `False`，且无输出重定向——照提示手工跑 step02b 会写生产 `vimax_working` | 实现 `_generate_isolated()`：按 A/B 两组、用 `_process_one_shot_skip_frames` 的 `shots_dir` 参数写进 `experiments/<id>/shots/<A\|B>/`；记录产出/计数，并核对生产帧 mtime 未变（隔离证据）。配套新增 r2v 策略切换点 `R2V_REF_POLICY`（A/B 的唯一变量） |
| P2-5 audit 缓存缺提示词版本 | **属实** | 复用条件只有 revision + 规则指纹 + scope，而指纹**不含** `audit.py` / `cross_field_checks.py` / `kf_structural_guard.py` | ① `audit_prompt_version()`（hash system+schema）与 `_audit_focus_version()`（hash 定向项模板）进入审计元数据并纳入复用判定；② 三份文件加入规则指纹 |
| P2-6 离线工具只验 manifest | **属实** | 联系表 / prompt 基线 / `experiment_context` 只调 `verify_step01_commit_manifest`，而 A/B 工具调两道 → 口径分叉 | 新增 `shared.quality_gate.require_step01_upstream()` 作为**唯一入口**（两道门），四个消费点全部改用它 |
| P2-7 零预算被当无限预算 | **属实** | `r2v_reference_budget` 返回 `max(0, cap-20-used)`，**0 可达**；而 `if max_chars and max_chars > 0` 把 0 当「不限制」→ 完整参考图块照发 | 改为 `max_chars is not None`，区分 `None`（未注册 cap）与 `0`（预算耗尽，按优先级裁剪） |

**回归测试**：本轮新增 12 条（策略切换 / 隔离生成 / run 真实执行 / 审计版本复用判定 / 跨字段 fail-closed），全量 **1991 passed, 1 skipped**。

### 10.8 待决项：工作包 C 的休眠（§5.6）

修复 P1-4 时发现一处**计划与实现的偏离**，但**不在本次修复范围内**，作为待决项上报：

【非 step01 吸收计划 §5.6】写明「代码即使准备完成，也**保持休眠**，直到……
工作包 D 的真实生成 A/B 达标」，而 `_R2V_KIND_INHERIT_EXCLUDE` 目前是**默认开启**
（生产 prompt 一直带着逐图「只取/不取」边界）。

本次**没有改动生产默认值**（改生产 prompt 超出审核范围，且会让 8 条既有测试失败）：
`R2V_REF_POLICY` 未设置/拼错一律按**生产现状**（开启）处理，`plain` 仅在显式设置时
生效，供 A/B 的对照组使用。是否按 §5.6 改回休眠，由用户决定。

### 10.9 刻意不做

- **不降审计温度压方差**（同 §10.4 第 1 条）：本轮已把「提示词/focus 版本」纳入复用
  判定与指纹，方差问题在**判据可追溯性**上解决，而不是靠调参掩盖。
- **不为 A/B 引入第二套提交路径**：`run` 复用 step02b 的逐镜入口 + `shots_dir`
  参数化实现隔离，不复制生成逻辑。

### 10.10 对齐新指纹：达成一次、随后被审计修复再次作废（v6.111–v6.114）

**goal**：规则指纹变更后让三份正式产物重新对齐（step02b 的 gate 依赖它）。

**进展**

```text
run 17  22:14  [COMMIT] OK 三份产物已提交（revision=d8800468ac08）
              复核：三份同 revision、指纹 1189300b69e02e93 == 当时的规则源、
              quality PASS(hard 0/risk 0)、audit ok(blocking 0)、真实 gate+manifest PASS
```

**随后作废（设计使然，不是回归）**：外部审核指出 `audit.py` 的解析器只检查
`issues` 是列表，`{"issues":[1,2]}` 与 `{"issues":[{"foo":1}]}` 都被判 `ok` + 零问题
——**比 BLOCK 更危险的静默假绿**（Agent Plan 通道把 `json_schema` 降级为
`json_object`，schema 的唯一执行点就是解析器）。修掉它 + 版本覆盖审查规则 +
送审范围补 `prompt` 之后，`audit.py` 在规则指纹里 → 产物再次陈旧。

**当前状态**：产物指纹 `1189300b69e02e93` ≠ 规则源 `3e1949140f836e39`（**对齐是断的**）。

**待决：提交点修复轮预算（§1.1 的值）**

最近 12 次提交点轮次的实测分布：

| 指标 | min | 中位 | max |
|---|---|---|---|
| 审计条数 | 1 | **3** | **7** |
| 确定性质量门条数 | 0 | 0 | **0** |

确定性侧始终为 0 → 结构性缺陷（补帧授权 / 约束撤销修复 / 授权死路径 / 规则-检测口径）
已清完；剩下的是**预算 vs 审计体量**：每轮修复调用平均面对 3 条、最多 7 条真实问题，
而 §1.1 只给 2 次调用（每轮 1 次 × 2 轮）。

- **A（推荐）**：提交点修复轮 2 → 4。依据是上表；每轮仍只发 1 次修复调用。
- B：保持 2 轮多跑几次（已采纳修复会累积进快照，每次净修约 2 条；但 `prompt` 入审后
  体量上了一个台阶，可能追不平）。
- C：暂不动 step01，把指纹陈旧记为待办。

**本轮顺带定位/修掉的真缺陷**（都有真实重跑证据）

| 缺陷 | 证据 | 修法 |
|---|---|---|
| `v6_43_prompt_enforcement` Rule 7 推翻事实源 | shot 2：`motion_desc` 写「仰卧饮酒…4-6秒丢了酒壶」、`lf_desc` 写「左手仍握钓杆」，Rule 7 却把 `ff_desc` 改成「已脱手」→ 审计判矛盾（判得对）→ 模型改一次它改回一次 → 永久 BLOCK | 叙述层（旁白/字幕/motion/尾帧）已交代该道具时不再改写 |
| 合规替换造非词 | `content_safety` 的逐字规则 `"杀": "击"` 把「杀气」换成不成词的「击气」→ 审计判错别字 | 补短语级映射（杀气→煞气 / 厮杀→缠斗 / 杀出→冲出…） |
| 审计授权死路径 | `field_path="ff_desc.props"` 授权出不存在于 schema 的路径 → 模型改布局被回填 → 审计每轮重报 | `_audit_props_paths`：props 类 issue **两侧**授权（含 `frame_layouts.<frame>.*.props`） |
| 裸叶子名不被识别 | 审计给 `field_path="props"` → 「字段是否被动过」判据认不出 → `audit_resolved=0` → 整轮回退 | `_LEAF_ROOTS`：按**该帧的布局条目**比对 |
| 晚约束写时间字段 | `final_quality_normalizer` 在时间派生边界后写 `motion_desc`（屏障守卫报出） | 重复清理移到早段 `post_fix_shot_prompts`；晚约束收窄到非时间字段 |
| 审计提示词丢 JSON 约束 | 扩写时替换掉「只输出 JSON」→ 模型返回 Markdown 报告 → `parse_failed` | 加回（保留扩写的全部要求） |
