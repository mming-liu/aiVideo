# Step01 时序契约与质量门收口计划

日期：2026-09-27。本文可独立用于实施和验收，不依赖其他评估材料。

## 1. 现状与证据边界

最近一次完整运行 `20260927T024814-88796` 的计划门与新稿生成均通过：新稿的契约拒收为零，时长在 60–90 秒预算内。BEST-GUARD 随后选择了 02:21 的历史快照进入提交管线；最终 BLOCK 的两条内容审计问题（转场交代缺失、同帧人物状态矛盾）属于**被选中的历史快照**，不能归到本轮新稿。正式 `script_result.json` 和质量产物未替换。

在该 11 镜快照的约束检查中，报告了 19 条未解决 HARD：11 条 `motion_time_integrity`、8 条 `kf_event_order`。同期质量门仍打印 PASS。前者已有可复核的格式冲突；后者需逐条核对，不能仅凭数量认定都是缺帧。实施时保留三个身份：新生成稿、BEST-GUARD 选稿、提交修后稿，并记录各自 revision。

## 2. 目标、边界与不变量

目标是让合法时序不再被误报，让真实关键帧缺口能准确进入质量门并获得可执行的修复路径，使约束报告和提交结论使用一致口径。最终内容审计问题单独归因、单独收敛。

保持以下不变量：合法时间标签按同一语法解释；片段顺序和时间边界可核；关键视觉事件在对应时间段有关键帧；任何修复不得静默改动已锁定台词、计划事实、事件顺序或跨字段的镜头状态。不能把所有约束层 HARD 一次性升级为提交阻塞，也不能恢复已停用的旧式整稿再生写回。

本计划不重构整条 Step01 流水线，不增加修复轮数，不调用 Step02a/02b，也不以单次成功提交作为稳定质量的证明。

## 3. 工作包 A：统一时间标签语法，消除误报

**首个失效点。** 提示要求 `前X秒：…；X-Y秒：…；Y秒后：…`，但 `motion_time_integrity.py` 的前缀正则只接受区间。只要中段含区间，检查就会扫描全镜，并把首段或末段的规范标签报为 HARD。`shared/motion_segments.py` 虽识别 `前N秒` 和 `后N秒`，目前也没有完整覆盖裸 `N秒后`；直接换用现有解析函数仍不能解决全部问题。还需避免搜索正文中的时间词冒充段首标签。

1. 在 `shared/motion_segments.py` 建立**段首锚定**的统一解析入口，返回原文、标签跨度、起止秒、标签类型和解析错误。明确区分 `后N秒`（末尾 N 秒）与 `N秒后`（从 N 秒起）；保留现有合法产物兼容性。
2. `motion_time_integrity`、时间段切分及依赖时段的关键帧检查共用该结果；校验负数、逆序、越界、重叠和无法解析的残片。无时序标签的自由文本沿用现有适用范围，不凭新增解析器扩大 HARD 范围。
3. 提示中的推荐格式由同一语法约定维护；禁止提示认可一种格式而检查器拒绝它。

**判别性验收：** `前2秒：…；2-4秒：…；4秒后：…` 合法；`后2秒` 按“末尾两秒”解释；`0.378378：…`、倒置区间和越界仍报错。用冻结的 11 镜快照重放，11 条格式误报归零，同时至少一个真实坏标签仍能被拦。先记录逐条前后差异，再改报告文案，不把误报消除写成质量提升。

## 4. 工作包 B：核实关键帧缺口并打通质量门

当前 `kf_event_order` 会记录 HARD，但质量门收集的是另一套结构化问题，因而约束报告的 HARD 与质量门 PASS 可以同时出现。`kf_event_order` 的时间窗口两端均包含边界；恰在边界的帧可能同时落入相邻段。8 条问题应先按原始镜头分为：确实缺少关键事件帧、边界归属歧义、文字匹配低置信、时间解析不一致，以及其他类型。

1. 冻结这 8 条的镜号、时段、帧时间、帧文案、约束代码与原始报告；逐条人工核对画面动作。对边界归属和解析误判先修共同判据，并再次重放全部 8 条。
2. 对确认的关键事件缺帧，定义窄的结构化质量问题：稳定代码、镜号、时段索引/时间、证据、明确的修复目标。只将这一类**已核实**的问题接入提交质量门；告警及其他约束 HARD 不顺带升级。
3. 修复由现有事务式修复路径处理。缺帧需要更新同镜的关键帧及其时间、画面布局和相关 motion/temporal 字段时，作为一个联动事务验收；无法给出合法联动目标则显式 BLOCK 并说明原因，不根据动作文字臆造关键帧。

**判别性验收：** 对确实缺帧的镜头，旧路径出现“约束 HARD + 质量 PASS”，新路径须产生同一镜的 blocking 问题；边界时刻但事件确实被帧覆盖的镜头不得误报。质量门 PASS 的含义限定为“已接入的阻塞判据均通过”，报告明确列出尚未接入的约束类别。

**语义决策点：** 现有规则接近“每个显式时段对应关键帧”。在真实样本分类完成后，若发现纯过渡时段不需要独立视觉锚点，需要由用户决定保留逐段必有帧，还是改为只对关键视觉事件强制承载。在决定前按现行规则核验，不以宽松解释放行。

## 5. 工作包 C：内容审计的独立收敛

对本轮被选中的历史快照，`MISSING_TRANSFER_BEAT` 应在上游镜头衔接中交代人物从船到车/府邸的转移；`SELF_CONTRADICTORY_FF` 应保证同一首帧的人物姿态和情绪描述一致。分别检查镜头计划、相邻帧描述和修复目标，定位审计所指的具体矛盾字段，再做局部联动修复。任何修改都重新经过台词门、计划引用、时间检查和最终审计。

这两条内容问题不由时间解析器或关键帧门自动解决。若修复轮继续 `new_blocking` 或 `no_target_resolved`，留存拒收前后的 revision 与新增问题；再决定改上游生成契约或人工内容取舍，不靠延长修复轮掩盖。

## 6. 实施顺序与验收

1. **只读取证：** 固定本轮三个稿件身份和 19 条约束明细，核实 8 条关键帧问题的类别与约束报告的真实消费者。
2. **A 后 B：** 先统一时间解析，再重算关键帧问题；随后只接入确认的阻塞问题。每一步用修复前后能区分的反例验收。
3. **C 独立处理：** 对最终内容审计的两条问题做局部修复，不把选稿切换造成的差异当成生成波动。
4. **小范围重放后再完整运行：** 先用冻结稿重放解析、约束和质量门，不写正式产物；获得运行授权后再跑 Step01。报告必须分开列新稿、选稿、最终审计/修复/提交，包含 `run_id`、revision、正式产物是否变化。

通过标准：规范时间标签误报归零、坏标签仍被拒；8 条关键帧问题全部可分类且确认的缺帧不会与质量门 PASS 并存；无台词完整性、计划引用、时长预算的新增回归。最终审计仍可能因内容问题 BLOCK，此时按具体问题继续收敛，不能将“未提交”直接判为本计划失败。

风险与回退：统一解析会改变历史稿件的检查结果；新门可能在早期增加可解释的 BLOCK。若重放发现合法标签或合法关键帧被误拦，先撤回该窄门的阻塞接线并保留诊断，再修判据；不放宽已确认的内容审计和台词门。

---

## 7. 实施结果（2026-09-27，工作包 A + B）

### A：统一时间标签语法（已完成）

- `shared/motion_segments.py`：`TIME_SEG_RE` 追加第 4 分支 **`N秒后`**（段首锚定，组号 6，前 5 组不变）；`TIME_LABEL_PREFIX_RE` 同步；新增 `HeadLabel` + `head_label()` + `overlapping_labels()`（返回原文 / 标签原文 / 标签跨度 / 起止秒 / 类型 / 解析错误）。
- `constraints/motion_time_integrity.py`：判据改走 `head_label()`，不再自持只认区间的正则。
- **「残片」用字符集判据**（标签区整段只由标签字符组成才算残片），否则 `画面中1-2人向前走3-5步`（v6.79 保下来的正文）会被判成残片。
- **部分越界仍是既有 clamp 语义**，不报；只有整段落在镜外才报。
- 冻结 11 镜重放：`motion_time_integrity` **11 → 0**；`kf_event_order` 8 → 8（该稿无 `N秒后`）。引擎日志由 `HARD 未解决 19 处 (motion_time_integrity@shot1…)` 变为 `8 处 (kf_event_order@shot3/6/7…)`。
- 真实坏标签仍被拦：`.378378秒：`/`0.378378：`/`2秒：`/`第3秒：` → 无法解析；`4-2秒` → 倒置；`9-12秒`、`9秒后` → 越界；`前3秒＋2-4秒` → 重叠。
- 提示同源守卫：`prompts/main.md` 的三段式逐字喂给 `head_label`，必须解析为 `pre/range/after`。

### B：统一判据 + 接入质量门（已完成）

用户裁决口径：保留「每个明确动作时段有独立关键帧」、**一帧只归属一个明确事件**（边界帧不得同时充当相邻两段的证据）、帧数 **≥ 时段数**。

- 新增 `step01_script_gen/kf_coverage.py`：唯一判据（`analyze()`）。归属 = 时间窗内取离段中点最近的段；`missing` / `shortfall` / `duplicates` / `out_of_window` / `mismatched` 一处算出。
- `constraints/kf_event_order.py`：计数 `!=` → `<`；归属/重复/缺段全部改走 `analyze`。**时间与内容分开报**——旧实现把「最佳匹配段不在窗口内」报成「时间不在任何时段内」（内容错位被说成时间问题）。
- `validation._check_kf_time_window_alignment`：覆盖率不再让边界帧重复计入，与约束侧同源。
- `quality_issues._kf_segment_missing_issues`（新）：**已核实的缺段**接入结构化门，`visual_risk`（blocking）、带 `repair_targets`（`keyframes` + `frame_layouts.kfs`）；**零帧也报**（最严重的一类，故在「无帧早退」之前判）。
- `repair_fields`：`kf_segment_missing` → `keyframes.*.t_sec` / `keyframes.*.desc` / `keyframes.*.stage` / `frame_layouts`。

**四镜重放（两个检查器结论一致）**

| 镜 | 段/帧 | 缺段 | 分类 |
|---|---|---|---|
| 3 | 4/3 | 1-3s「跃上船头」 | 真缺帧（相邻帧跨过该动作） |
| 6 | 3/2 | 2-4s「身体发抖加剧…」 | 边界帧归属错位（帧在、文案跨段） |
| 7 | 3/2 | 2-4s「侧身回望张十三」 | 边界帧归属错位 |
| 8 | 4/3 | 1-2s「反手一掌」 | 帧存在但时刻错位（kf@2.5 写「掌风扫向」） |

冻结稿走真实质量门：blocking **0 → 4**（全部 `kf_segment_missing`，镜号 3/6/7/8）——「约束报缺帧 + 质量门 PASS」并存的状态已关闭。

### 尚未完成 / 待定

1. **C（内容审计两条）未做**：`MISSING_TRANSFER_BEAT` / `SELF_CONTRADICTORY_FF` 的局部联动修复需改稿并重跑审计（等运行授权）。
2. **尾部空白**（shot 6/7/9 分段未覆盖到镜头末尾 1.5–3.1s）：按用户口径列为**独立的时长覆盖问题**，判据未定（需先定「尾段应呈现什么」），未自行加阈值。
3. **shot 8 的语义错位**（「掌风扫向」vs「打在脸上」）：现有**词面**判据未达阈值（0.45/0.12），确定性门抓不到；按「须同时核对击中瞬间与物理时刻」留给修复轮/regen。
4. **未跑流水线**：按计划「获得运行授权后再跑 Step01」，本轮只做冻结稿重放。
5. 全量测试 **2561 passed / 1 skipped**（本轮 +36 条）。

---

## 8. 两轮复核的补口（2026-09-27 续）

复核 2-1/2-2 先做，随后复核 1-1/1-2/1-3。

**2-1 零长度时段**：`head_label` 新增错误码 `zero_length`（起止相等不是时段）；`kf_coverage.segments()` 与 `parse_segments` 同口径丢弃退化区间。三处结论一致，判别用例钉住（`head_label` 报错 / 时段表丢弃 / 覆盖分析不当时段）。

**2-2 边界归属统一为半开区间**：新增 `kf_coverage.own_segment()`——`[start, end)`、末段闭合，与事件表事实源 `event_of()` **同一规则**（其冻结用例规定 `0-2秒/2-4秒` 的 `t=2.0` 归后段）。此前「双端闭区间 + 离中点最近」会让同一稿在事件表与覆盖检查里得到相反归属。**后果**：恰落交界的帧改归**后**段，冻结稿 shot 3 / shot 8 的时间缺段由第 2 段翻到第 1 段（0-1s）。

**2-3 时间占位 vs 内容对应分开记录**：`KfSlot.content_ok` / `KfCoverage.time_covered` / `content_unverified`。阈值沿用既有 0.45，不新造数字；**内容维度的阻塞范围未改**（待判别样例定案）。判别用例：无关文案（「画面整体偏暖，颗粒感增强」）照样清掉时间缺段、但内容对应全不通过。

**1-1 修复授权闭环**：`kf_segment_missing` 登记进 `_KF_STRUCTURAL_CODES`。不登记时 `validate_repair_targets(structural=False)` 把 `keyframes` / `frame_layouts.kfs` 两条成对目标全判无效 → 整条 issue 被跳过 → 质量门报得出、修复轮零授权。配套：成组获准（帧 + 布局一起）、非结构码不外溢、`kf_structural_guard` 三条补偿验收（越界帧 / 非单调 / motion 事件被改）、联动缺条引入 `kf_layout_count_mismatch`（blocking）→ 事务整组回退。

**1-2 修复指令放宽**：允许 ① 补帧 或 ② 调整相关帧的 desc/布局（②里改 `t_sec` 必须与文案改写成对做），并写明**裁决口径**：满足「每个需帧时段都有一帧、且该帧文案描写它自己那段」即算修好；同一帧若另有 `kf_event_window_mismatch`（要求不改 `t_sec`），照它改写文案 + 补帧即可。

**1-3 规则指纹**：`kf_coverage.py` 纳入 `_compute_rule_source_fingerprint()`（只改覆盖判据也会让旧报告失效）。

**冻结稿重放（时间覆盖 / 内容证据分开记录）**

| 镜 | 段/帧 | 时间归属 | 时间缺段 | 内容未核实 | 文案最匹配≠归属 |
|---|---|---|---|---|---|
| 1 / 2 | 3/3 | [0,1,2] | — | [0] | — |
| 3 | 4/3 | [1,2,3] | **[0] 0-1s 探出水面** | [1,2,3] | [0] |
| 4 / 5 | 3/3 | [0,1,2] | — | — | — |
| 6 | 3/2 | [0,2] | **[1] 2-4s 发抖加剧** | [0] | — |
| 7 | 3/2 | [0,2] | **[1] 2-4s 侧身回望** | [2] | — |
| 8 | 4/3 | [1,2,3] | **[0] 0-1s 右手扬起** | [1] | [0] |
| 9 / 10 | 3/3 | [0,1,2] | — | — | — |
| 11 | 3/3 | [0,1,2] | — | [0,2] | — |

质量门 blocking：**0 → 5**（4 条 `kf_segment_missing` + 1 条 `kf_event_window_mismatch`，后者是半开区间后 shot 8 的 kf0 归属变化暴露出的内容错位）。「时间缺段为零」的 7 镜里仍有 3 镜（1/2/11）内容对应未核实——即「缺段为零」不能当作动作证据齐全（复核 2-3）。

---

## 9. 完整运行 + 复核三轮（2026-09-27 续）

运行 `20260927T064856-89028`（06:43–06:51，440.7s）。**BLOCK，正式产物未替换**；BLOCK 的直接原因是**基础设施**：`final semantic audit request_failed: Connection error.`（调用失败 ≠ 0 issue），不是内容判定。

**三路证据**

| 稿 | revision | 镜 | 时间缺段镜 | 帧数<段数镜 | 内容未核实段 | 时长 |
|---|---|---|---|---|---|---|
| 本轮新稿 | — | 15 | **15** | **15** | 11 | — |
| BEST-GUARD 选稿（02:34 快照） | `eedf752287c8` | 13 | 2（shot3/9） | 0 | 13 | 78.5s |
| 提交修后稿 | `626f13647a3d` | 13 | 2（shot3/9） | 0 | 13 | — |

新稿 100.0 分/**风险55** vs 快照 100.0 分/风险7 → `snapshot_strictly_better`，选快照。新稿是**真实劣化**（每镜 1–2 帧对 3 段），非判据artifact：其 motion 全部是 `前N秒 / N-M秒`，无 `N秒后`，语法改动不影响段数。

**闭环实证（检出 → 授权 → 修复 → 再验收）**：选稿上 `kf_segment_missing` 对 shot 3/9 报 blocking（时间缺段 0-1s），修复轮日志：

```
[REPAIR] shot 9: 授权字段实际改动 1 处 （keyframes 3->4）: ['keyframes.__len__']
[REPAIR] shot 3: 授权字段实际改动 1 处 （keyframes 3->4）: ['keyframes.__len__']
[REPAIR] 替换被拒 3 条: ['shot 9: frame_layouts.kfs 长度 3 ≠ keyframes 长度 4',
                        'shot 3: frame_layouts.kfs 长度 3 ≠ keyframes 长度 4', 'shot_id 为空']
```

检出 ✅、授权 ✅（列表级成对目标获准，模型确实补了帧）、**但联动不完整 → 整条替换被拒 → 未修复**。修后稿与选稿覆盖完全一致（shots 3/9 仍缺 [0]）。下一步：把「两表必须同一次一起加长」写到不可忽略（或在语义决策后做确定性回填）。

**复核三轮 1（末段归属缺下界）**：`own_segment()` / `event_of()` 的 `末段 and t <= end` 补上 `t >= start`。判别用例：`0-2s、4-6s` 的 `t=3.0` 归属为 `None`（两处一致），末段终点仍闭合。

**复核三轮 2（零长度覆盖所有类型）**：零长度判定移到所有标签类型算完 `start/end` 之后（顺序 `reversed → zero_length → negative → out_of_range`）。`前0秒` / `后0秒` / `5秒后` / `2-2秒` 一律 `zero_length`。

**这两项修复对现有 4 份稿的结论是 0 变化**（逐份实测空档=0、零长度=0、其他坏标签=0）——属**预防性**收口，由单测钉住，不由本轮样本证明。

**内容维度（复核 2-3 的观察项，仍未接门）**：四份稿的 `content_unverified` 分别为 10 / 11 / 13 / 13 段——即便最好的选稿也有约 1/3 的时段「有帧但文案对不上」。这是决定内容维度阻塞判据的输入，本轮不动。

**顺带修复（假 PASS）**：提交提前中止时 `_commit_summary` 无 `quality` 键，旧代码会打 `[QUALITY-GATE] PASS: 无 HARD / VisualRisk 阻塞项`——而同一 run 的 manifest 里列着 10 条 blocking。现改为显式报「未产出质量报告，本次没有可用的质量判定」。

---

## 10. 用户取舍落地（2026-09-27 续）

用户决定：**内容对应暂不整桶接硬门**（保持观察项）；**联动修复改用「模型提供帧与布局、代码原子装配」**，不做语义回填；并先修提示冲突。

**① 提示冲突（真实）**：新的缺段契约允许调整已有帧的 `t_sec`/desc/布局，而主修复提示 ⑤ 与回退提示仍写「已有帧逐字保留、只在末尾追加」——模型本轮只加帧，与旧指令一致。三处已统一：输出契约补 `keyframe_additions` 成对形状；主提示 ⑤ 改为「默认不动已有帧；**仅当问题文案写明『时间/内容错位』时**才可一并调整该帧 t_sec + desc + 对应布局，且改时刻必须与改文案成对」；回退提示同一口径。三处都写明验收口径：**每个需帧时段都有一帧、且该帧文案描写的是它自己那段**。

**② 成对数据 + 原子装配**（`_assemble_keyframe_additions`）：模型只给 `{"frame": {...}, "layout": [...]}`，代码按 `frame.t_sec` 的**时间顺序**把两半插到**同一声序号**，再走既有授权 / 结构 / 提交验收。代码**只做配对与装配**，不从邻帧或 `motion_desc` 猜姿态/持物。缺任一半、desc 空、`t_sec` 越界、装配后两表不等长 → 整条拒收并记明原因（不留半条修改）。

**③ 内容聚合偏差（已证伪为真）**：`content_unverified` 原用 `{own: slot}` 且**滤掉 `duplicate_of` 的帧**，于是「同段第二帧才是写对的那一帧」被漏掉。按段聚合**全部帧**后，四份稿读数为 10 / 11 / **13→11** / **13→11** —— 与复核预测的「至少 2 段属聚合误计」逐段吻合（02:34 选稿 shot 3 的 1-3s 段 kf1=0.556、shot 9 的 1-3s 段 kf1=1.000）。

**测试**：+10（9 条成对装配 + 1 条聚合），全量 **2586 passed / 1 skipped**。

**下一轮验收口径**（用户指定）：成对修复能否被接受、时间缺段是否减少、内容未核实的**校正后**读数、最终审计是否取得完整响应。

---

## 11. 成对装配后的完整运行（`20260927T071102-96504`，07:11–07:24）

**BLOCK**（4 轮修复上限后仍有 4 条 blocking：审计 2 + 确定性 2）——本轮是**内容裁决**，不是基础设施失败：四轮审计均有完整响应。

**四项验收**

| 验收项 | 结果 |
|---|---|
| 成对修复能否被接受 | 联动修复**被接受**：第 2 轮模型返回完整镜头时**两表一起**从 3 长到 4（`keyframes.__len__` + `frame_layouts.kfs.__len__`），`resolved=4`、未被回退，保留到终稿。**`keyframe_additions` 成对路径本身未被模型采用**（日志无该键），仅单测覆盖 |
| 时间缺段是否减少 | 选稿 2 镜（shot 3/9 的 0-1s）→ 修后稿 **0 镜** ✅ |
| 内容未核实（校正后） | 选稿 11 → 修后稿 **11**（shot 3 的 [2] 与 shot 9 的 [] 均未变）；内容维度未被这轮修复推动 |
| 最终审计完整响应 | ✅ 四轮均有完整审计结论；`[QUALITY-GATE]` 打的是新写的「未产出质量报告」而非假 PASS |

**BEST-GUARD 连续两轮选历史快照**：本轮新稿 100.0/风险**68** vs 快照 100.0/风险6（上一轮 55 vs 7）。根因在**生成侧**：新稿每镜只给 2 帧而 motion 分 3 段（实测 shot1–6 全是 2 帧；提示要求 `time_ratio = 段中点` 逐段一帧）。覆盖判据报得对，是稿子欠帧——这也解释了新稿「时间缺段 15/15」。

**剩余 4 条 blocking（内容侧，属计划 §5 工作包 C）**：`model_audit_FF_STATE_CONTRADICTION@shot3`、`model_audit_ONSCREEN_ORDER_CONTRADICTION@shot12`、`multi_person_staging@shot9`、`split_boundary_replay@shot12`。

## 12. 重跑（`20260927T081103-90492`，08:11:03–08:19:37，8m34s）

**BLOCK**：5 个检出轮 / 4 个修复轮，终局 4 条 blocking（审计 2 + 确定性 2）。三份正式产物**未替换**（`script_result.json` / `step01_quality.json` / `step01_audit.json` 仍是 09-24 08:11 那版）。

| 项 | 值 |
|---|---|
| run_id | `20260927T081103-90492` |
| 新稿 | `candidate_pre_bestguard_20260927_081103.json`（11 镜 → 拆 1 → 12 镜，53s → 67s） |
| BEST-GUARD | 新稿 100.0 分/风险 **24** vs 历史快照 100.0 分/风险 **2**（`script_result_20260927_072416.json`）→ **选中快照**（即上一轮 BLOCK 的那份修后稿） |
| 终局 blocking | `model_audit_PROP_DROP_NO_PROCESS@2`、`model_audit_OFFSCREEN_ACTIVE_STATE@12`、`multi_person_staging@9`、`split_boundary_replay@12` |
| 修复账 | 4 轮**全部 `resolved=0`**（`step01_repair_rounds.json`） |

### 12.1 新稿的 24 条风险里 19 条是「关键帧—时段覆盖」族

对 `candidate_pre_bestguard_20260927_081103.json` 跑 `collect_structured_issues`：结构化 issue 共 130 条，其中 blocking 24 条——

| 类别 | 条数 |
|---|---|
| `kf_time_window_alignment` | 8 |
| `kf_segment_missing` | 8 |
| `kf_event_window_mismatch` | 3 |
| `timeline_consistency` | 2 |
| `frame_presence_consistency` | 2 |
| `shot_content_over_cap` | 1 |

帧-段实测：11 镜里 **8 镜 2 帧 / 3 段**（总帧 25）。**连续 4 次运行（07:11 的 15/15、08:11 的 8/11）都是「每镜恰好 2 帧」**，与段数无关。

### 12.2 生成面已无「每段一帧」这条规则（本轮的根因候选）

CLAUDE.md v6.33 记的「源头规则 16.4（`prompts.py`）：kf 个数 ≥ 时段数，按段表逐段生成 1 帧」在当前生成输入面上**不存在**：

- `prompts/system.md` 的硬约束 10 条（A/B/B1/B2/B2.1/C–J/K）无一条涉及帧数；`K` 只给 t_sec 范围，例子是「5s 镜 t=1.0/2.0/3.0/4.0（4 帧）」；`K3` 反而写了「微动作短镜（≤4s）写不出差异就 `keyframes=[]`」。
- `prompts/main.md` 业务规则 3 强制每镜写**三段式** motion_desc，但没有对应的帧数要求。
- 生成执行契约块（`prompts.py` `CONTRACT_BLOCK_*`，本轮 1770 字符）全文无 `keyframes` / `关键帧` 字样。
- `_text_gen/deepseek_schema.py` 的 `_KEYFRAME` 只描述单帧字段。
- 溯源：`0dda62b`(08-23) 把 prompts.py 拆成 `prompts/*.md`（−652 行）；`ec727d0`(08-29) 换掉规则优先级表，删掉 `| 关键帧时段对齐 | 16.4 | kf 数 ≥ motion_desc 时段数 |` 那行。`git log -S "时段数" -- step01_script_gen/prompts/` 只命中这两次。

即：**要求「每段一帧」的只有下游判据、质量门和修复提示，上游一个字都没说**。已排除代码侧删帧：`motion_segment_kf_align` v6.103 起只报告（不再合并时段），`kf_frame_redundancy` 是 WARN 且不删有时段承载的帧。

### 12.3 shot 12 的授权死锁（4 轮 + 上一轮 4 轮，同一条 key）

审计建议与确定性检查在同一镜上**互相抵消**：

1. `model_audit_OFFSCREEN_ACTIVE_STATE@12` 的建议原文：「12镜 ff 群演改为 `on_screen=true`、pose=退下，kfs[0][0] 同步」——授权面含 `frame_layouts.ff[0].on_screen` / `kfs[0][0].on_screen` / `pose` / `motion_desc`（**不含 `keyframes[0].desc`**）。
2. 模型照办后，`frame_presence_consistency` 立刻对同一镜报新 blocking：kf0 的 desc（原为「…目送庄仆们**退下**，庄仆们边走边哄笑」，含 `_EXIT_MARKERS` 的「退下」）与 `on_screen=true` 冲突。
3. 唯一的出路是改 kf0 的 desc——而它**不在授权面内**，被 `_merge_authorized` **强制回填成原文**（本轮日志：`shot 12: 未授权路径已回填 4 处: [... 'keyframes.0.desc', 'keyframes.1.desc']`）。
4. → 半条修复 → 新 blocking → 整组回退（`按目标回退 ['shot_12']`）→ `resolved=0`。

同一形态在 `multi_person_staging@9`（pose 缺朝向词的 lint）与 `split_boundary_replay@12`（12 镜 2-4s「张十三目送他们离去，目光闪烁」与镜 11 `lf_desc` 的「张十三目光闪烁」相似度 67%）上重复出现：4 轮都没解掉，且第 2/4 轮 shot_9 还被联动组容量挤出（`为联动组 ['shot_3','shot_4'] 让出单镜目标 ['shot_9']`）。

### 12.4 审计身份逐轮不重复，「resolved=0」不可归因

`[COMMIT] 审计身份比对` 五轮里出现两次「**仍在 无**」：每轮上一轮的 `(镜头, 族)` 身份**全部消失**、同时冒出新的一批（4→14→6→5→4 条）。所以「消失」并不等于「已核实修好」，`resolved=0` 也不能归因到修复无效——审计面本身每轮在换。

### 12.5 一处本工作包引入的重复计量（待处理）

`kf_time_window_alignment`（检测 1）与新建的 `kf_segment_missing` 现在报的是**同 8 条缺陷**（shot 2/3/4/5/6/8/9/10，同一个缺段），两条都在 `_QUALITY_BLOCKING_CATEGORIES` 且都在 `_KF_STRUCTURAL_CODES` → **一条缺陷计两次风险**。新稿 24 条风险里 8 条是这个重复（去重后 16）。`kf_segment_missing` 是本工作包新增的码，旧稿上两者从未同时触发（历史快照上帧数是补齐的），所以此前未暴露。

### 12.6 待裁决

1. **上游契约缺口**：恢复「每段一帧」到生成面（system.md K 或 main.md 规则 3），还是把下游判据按镜头时长放宽。恢复上游会**增加 kf 数**——每个 kf 在 step02a 是一张图（现 25–30 帧 → 约 45 帧量级）。
2. **shot 12 死锁**：把 `keyframes[*].desc` 纳入该镜授权面（审计目标补一条），还是让 `frame_presence_consistency` 区分「退下（进行中，仍在画面）」与「已退场」。
3. **重复计量**：`kf_time_window_alignment` 检测 1 让位给 `kf_segment_missing`（单一上报），还是保留双报。

## 13. 四项裁决的落地（2026-09-27，用户裁决 1-4）

来源：用户对本轮只读复核的三项决策 + BEST-GUARD 空转条件。实施顺序按要求：
**先去重校准读数与修复权限 → 再修 shot 12 的时态判据 → 然后补生成契约与
BEST-GUARD 空转条件**。

### 13.1 缺段只计一次（裁决 3）

`validation._check_kf_time_window_alignment` 的**检测 1（时段覆盖率）与「无任何 kf」
分支移出本类别**，收进返回值的新键 `coverage_warnings`，由调用方登记到**非阻塞**
诊断类别 `kf_segment_coverage_diag`。唯一缺段阻塞 = `quality_issues.kf_segment_missing`
（判据同为 `kf_coverage.analyze`）。本类别保留**越窗**与**顺序**两类检测（顺序那条
见 13.5）。

配套收窄授权面（`repair_fields`）：`kf_time_window_alignment` **移出**
`_KF_STRUCTURAL_CODES`——剩下的两类只需改叶子（`keyframes.*.t_sec`/`.desc`），
不再凭旧类别获得「可增删整张 KF 列表」的结构权限；`kf_segment_missing` 的列表级
授权不变。

实测（08:11 新稿，`collect_structured_issues`）：blocking **24 → 16**，
`kf_segment_missing` 8 条（唯一缺段阻塞）、诊断类别 8 条（advisory）。
`_quality_rank` 只看 integrity / blocking / structural / 分数，advisory **不进**排序。

### 13.2 「退下」的时态判据（裁决 2）

`cross_field_checks` 的离场词拆成两档：

| 档 | 内容 | 是否判离场 |
|---|---|---|
| `_EXIT_DONE` | `已走远`/`不见了`/`已没入`/`沉入水底`/`已死` … 绝对完成形式 | ✅ |
| `_EXIT_DONE_RE` | `已\|已经 + 动词`、`动词 + 了/完毕/干净`、`消失在/于 …` | ✅ |
| `_EXIT_IN_PROGRESS` | `退下`/`散去`/`退场`/`离场`/`离去`/`离开`/`走远`/`远去`/`消失` **裸用** | ❌（人物仍在画面） |

判别样本（冻结稿 shot 12）：FF「庄仆们转身退下，张十三站在船头，目送他们」、
kf0「…目送庄仆们退下，庄仆们边走边哄笑」→ **不报**；LF「庄仆们消失在夜色中」→
**仍报**。v6.115 的反例（`已倒下`、`仍在远处可见`）不复活。

**未做**：不给 shot 12 泛授权 `keyframes[*].desc`（审计若确需改某帧描述，
由该条 issue 自己点名帧路径）。

### 13.3 生成契约写回生成面（裁决 1）

三份生成面各加一条同措辞的铁律（判别短语 `每个正文非空的时段至少有一帧`）：

| 面 | 位置 |
|---|---|
| `prompts/main.md` | 时序三铁律后新增 **3b**；规则 5（kf 字段）加指向 |
| `prompts/system.md` | **K** 的采样点列表后新增「每段一帧」条；**K3** 的「短镜可零帧」加限定「只适用于 `motion_desc` 未显式分时段的单动作镜」 |
| `prompts/doubao_system.md` | 新增 **RULE-12b** |

同时**删掉固定帧数的教学**（旧文案 `t_sec = 1.0/2.0/3.0/4.0（4 帧）`——3 段镜与
5 段镜会被教成同一个数，与「每段一帧」直接矛盾）。

成本读数更正：本轮新稿 11 镜 / **25 帧** / 8 镜缺段 → 该稿**至少补 8 帧**；
§11 写的「约 45 帧」不是这份稿的成本读数，Step02a 的实际增量按入选稿再估。

### 13.4 BEST-GUARD 不再空转（裁决 4）

旧行为：全部快照都在台账内 → 整表放开、按欠账轮次升序选一份。实测 08:11 run
选中上一轮刚 BLOCK 的 `072416`（它的 2 分风险只是**确定性**风险，欠的语义问题
不在这个数里）→ 重跑同一套审计、同样 4 条 blocking、`resolved=0` → 再 BLOCK。

现行为：

1. 台账条目新增 `criteria[revision] = {rules, prompts}` 指纹（登记时写入）；
2. `_load_blocked_revisions()` 只把**指纹相符**的条目当欠账——判据/提示改过之后
   旧欠账不作数，该稿按现行判据**重新评估**（本轮两条实测正是被修掉的判据造成的）；
   无 `criteria` 字段的老条目按「仍然欠账」处理（保守方向：不凭无法核对的旧结论
   放它回来）；
3. **删除**「全欠账 → 放开限制」分支：没有合格历史候选时如实返回
   `(None, meta)`，`reason="all_snapshots_owed"`，日志给出欠账最重的几份 →
   **本轮新稿进提交门检验**（门仍可 BLOCK，正式产物仍受保护）；
4. 欠账轮次不再参与候选排序（候选池里已无欠账稿），该字段只留在日志里做可见性。

### 13.5 复核发现的既有死判据（未改，仅记录）

`_check_kf_time_window_alignment` 的**检测 3（时段顺序倒装）不可能触发**：
`mid_kfs` 在检测前已按 `t` 升序 `sort`，随后算出的段序号必然非递减。
同一缺陷已由 `quality_issues._keyframe_timeline_issues` 的**列表序**严格递增
（`kf_time_not_strict` / `kf_time_too_close` / `kf_event_duplicate`）覆盖，
故本轮**不复活**这条死判据（复活会新增阻塞面，属改判据，需另行裁决）。

### 13.6 验证

- 新增测试：`tests/step01_script_gen/test_kf_missing_single_report.py`（6）、
  `test_frame_presence_exit_tense.py`（9）、`test_generation_kf_contract.py`（6）；
- 更新既有测试中编码旧契约的用例：`test_kf_time_window_alignment.py`（缺段走诊断
  通道）、`test_motion_segment_kf_align.py`（两通道合读）、`test_kf_structural_repair.py`
  （结构码换成 `kf_segment_missing` + 新增「窗口类别不再授权列表」对照）、
  `test_repair_targets.py`、`test_review_fixes_0915.py`（`_EXIT_MARKERS` → 新常量）、
  `test_best_guard.py`（全欠账不选稿、判据变更重评、老条目保守处理）；
- **全量 2627 passed / 1 skipped**；真实数据复核：新稿 blocking 24 → 16、
  诊断 8 条 advisory、台账 34 条按现行判据全部仍欠账（首个 run 将走新稿进门的路径）。

## 14. 十次连跑取证（2026-09-27）

四项裁决落地并全量测试通过后，**连续跑 10 次第一章**（`20260927T165916-98960` …
`20260927T182017-97332`，合计 5447s）。结论与逐条证据另成一份文档：

→ [`docs/step01-ten-run-problem-report-2026-09-27.md`](step01-ten-run-problem-report-2026-09-27.md)

三条与本计划直接相关的读数：

1. **裁决 3（缺段只计一次）与裁决 2（退下时态）在本批 10 次里零回归**
   ——`kf_time_window_alignment` 只在越窗/顺序上报；`frame_presence_consistency` 只报 3 条，
   逐条都是完成态（`消失在田埂尽头` / `已走远`）。
2. **裁决 1（生成契约写回）提示侧已验证进入输入，但模型遵从度波动**：
   10 次新稿 143 镜里 100 镜（70%）仍是「3 段 2 帧」；唯一整篇遵守的 run 03 缺段镜 12→2。
3. **裁决 4（BEST-GUARD 不再空转）10/10 次生效**：本轮因此变成对**新稿**的 10 次独立检验，
   暴露出此前被「选历史稿空转」掩盖的事实——新稿终局 blocking 245 条（确定性 193 / 审计 52），
   且 **10 次全部 BLOCK，没有一次是「修到没得修」**：停止原因全是
   `locked_changed` ×4、超限联动组 ×4、`kf_structure_invalid` ×2。

## 15. 十次连跑根因收口（P1–P6，2026-09-27 用户裁决「全做」）

来源：`docs/step01-ten-run-problem-report-2026-09-27.md` 的四条死锁分析。实施顺序
按裁决 **P1 → P4 → P2/P3 → P5 → P6**（先止血粒度、再去重校准、最后口径）。

### 15.1 P1 审计补合规替换说明

`audit.py::_AUDIT_RULES_TEMPLATE` 新增「合规替换（不算缺陷）」段：点名
`shared/content_safety.NARRATION_SUBSTITUTIONS` / `SENSITIVE_SUBSTITUTIONS`，
写明「台词与原文的差异若属于既定合规映射则不判错词/改词」，并要求判错词前先确认
原文是否含被替换词。

根因链（实证）：`杀人→出人命`（合规）→ `validation.py:696-700` 已做双向归一化 →
审计侧无此说明 → `DIALOGUE_WRONG_WORDS` 要求改回「杀人」→ 修复照办 →
`narration_segment` 锁定 → `locked_changed` 整轮回退。10 次跑 6 次触发、4 次成为
停止原因、赔掉 35 条已完成修复。
`audit_prompt_version()` 覆盖该模板，提示一变旧审计自动失效（回归锁定）。

### 15.2 P4 回退粒度收窄到肇事目标（核心止血）

新增 `_locate_offending_targets(decision, picked, locked_diff)`：从三个闸门的**既有
消息格式**里定位肇事目标（`shot <id>.<字段>: 旧 -> 新` / `<目标名>: 违例`），
`locked_changed` 走 `_locked_signature_diff` 兜底（已授权锁定字段不进 mask 违规）。

- 触发条件从 `reason == "new_blocking"` 放宽为「**任一可定位闸门**」；
- `_dec_try` 的锁定字段判据**按回退后的稿子重算**（原样传轮级 `_locked_changed`
  会让 locked 型拒绝在回退后仍为真，按目标回退永远过不了验收——这正是死锁本身）；
- **不可定位的保持整轮语义**：`non_target_changed`（肇事镜头在点名集之外）、
  `evidence_removed`（改写检测输入）、`checker_failed`（基础设施）明确不在此列。

实测（`tests/step01_script_gen/test_repair_rollback_granularity.py`）：一轮里 shot_2
违规、shot_1 合法 → 改前 `resolved=2` 全丢；改后只回退 shot_2，**shot_1 的修复保留**。

### 15.3 P2 超限成对目标按镜拆条

取证先行：逐轮扫描审计台账里 `repair_targets` 覆盖 >6 镜的条目，4 次 BLOCK
（run 02/04/06/10）**全部是单条声明**（7 / 7+12 / 14 / 8+10 镜），没有一次是多条合并。

- 契约侧：`_AUDIT_RULES_TEMPLATE` 写明「单条 repair_targets 最多覆盖 6 个镜头，
  systemic 缺陷按镜拆条」；
- 代码侧：`_audit_payload_to_issues`（payload→Issue 的唯一转换点，也是联动组分组的
  唯一输入）按镜拆条，`_split_oversize_repair_targets` 保证**同一镜的全部目标留在
  同一块**（镜内原子性不变）、不丢不重、三态语义（`None`/`[]`/非空）不变；
- 拆的是**声明范围**，不是联动事务——联动组分组语义与「拆组=半条修复」禁令保持。

上限同值由回归锁定（`_AUDIT_MAX_TARGET_SHOTS == _REPAIR_MAX_SHOTS_PER_GROUP`）。

### 15.4 P3 缺帧接入结构收敛轮

`_structural_shot_issues` 纳入缺帧镜头（判据取自 `kf_coverage.analyze`，与质量门
同源），反馈走**独立分块** `_kf_frame_feedback`——「按时段补帧、镜头切分可以不动」，
与「重做 shot-plan」分开（混成一段会让模型去改切分）。反馈逐镜给出缺段中点 `t_sec`，
可直接照抄补帧。日志与采纳判据都分列「超载/过短 N + 缺帧 M」。

两处既有夹具（`test_duration_convergence` / `test_scoped_shot_split`）改为**帧合规**
（`_frames_for` 按时段中点生成帧），使它们继续隔离各自维度（超载/时长）。

### 15.5 P5 站位 lint 读权威字段

`char_layout` 是从 `frame_layouts` **派生的遗留字段**（`identity_unification:204`
把 ff 名单的 `pose` 抄进 `facing`），而方位事实在 `frame_layouts[*].gaze_target`
（schema 6 字段之一，必填）。新增 `_has_layout_direction_fact`：该角色在本镜
布局里有 `gaze_target` ∈ 四个画向 → 不再报「缺朝向词」；**没有**方位事实
（`gaze_target="无"` 的昏迷/闭眼角色、或不在布局里）**照常报**——只读权威字段，
不做文本推断。

**实测（10 份新稿，走完整质量门、未截断）**：

| | 改前 | 改后 |
|---|---|---|
| `multi_person_staging` | **34** | **7** |
| 全部 blocking | 550 | 523 |

同时拿到该口径下的**新稿确定性分布**（此前只能按截断清单读下限）：
`kf_segment_missing` 216、`kf_event_window_mismatch` 88、
`kf_person_missing_in_layout` **83**、`timeline_consistency` **40**、
`prop_possession_consistency` 25、`multi_person_staging` 34→7、
`shot_duration_below_min` 20、`frame_presence_consistency` 10。
（`kf_person_missing_in_layout` 与 `timeline_consistency` 是本轮新暴露的两大新类，
不在 P1–P6 范围内，留作下一轮取证。）

### 15.6 P6 清单截断显式化

`build_step01_commit_manifest`：清单从裸 `[:20]` 改为 `_cap_blocking_for_manifest`
（**每个 code 至少留一条**再用原顺序补齐），并新增 `blocking_truncated` 布尔；
`blocking_count` 始终是真值。形状不变（仍是原 record 子集，不新增/不改写）。

10 次跑里 36 条的 run 此前只留 20 条且整类可能消失——问题报告的逐 code 分布因此
只能给下限；现在每类都可见。

### 15.7 验证

- 新增回归 47 项：`test_audit_compliance_and_target_cap.py`（12）、
  `test_repair_rollback_granularity.py`（9）、
  `test_kf_missing_structural_convergence.py`（9）、
  `test_multi_person_staging_authoritative_field.py`（11）、
  `test_commit_manifest_blocking_coverage.py`（6）；
- 全量 **2674 passed / 1 skipped**（改动前 2627）；
- 10 次串行复测见 §16（`python .tmp/run10_step01.py` + `.tmp/agg_run10.py`）。
