# S19–S25 第二章完整重分配与分镜收口：执行记录（2026-11-25）

**状态：S19–S24 全部完成（隔离运行，正式产物未覆盖）；S25 交付收口。**
本轮完成了「S23 独立内容核验（12/12 + 10/10 全部 supported）→ S24 真实分镜生成与时长/关键帧/契约收口（11 镜 / 88.5s）」，未覆盖正式产物，未自动正式采纳、合并或推送。

| 层级 | 交付 | 状态 |
|---|---|---|
| 代码落盘 | `compile_reallocated_plan`、`speaker_attribution_problem`、`test_compile_reallocated_plan`（8 passed） | **完成** |
| 离线贯通 | 输入清单、义务表、候选、变更清单、实际入口请求与四类拒收证据 | **完成** |
| 真实收益 | S23 独立核验（12/12 + 10/10 `supported`）；S24 真实分镜（17 镜 116s → 收口 **11 镜 / 88.5s**） | **完成** |
| 剩余边界 | 低层核验未自动盖章、`char_layout.surface`/运镜等提示项、未正式采纳 | 见 §八 |

## 一、结论先行

1. **工程可行**：第二章 12 条事实在**10 条声音／66.70 秒／20 节拍**下通过统一计划验收（`plan_acceptance_problems == []`）、预算评估（`feasible`，下界 66.70s ∈ 60–90s）与生成前检查（`check_plan_before_generation` 返回 `feasible`）。
2. **入口可达**：候选经投影回读（固定点）后，由**真实 `gen_spy_script` 入口**构造出 44558 字符的分镜请求；请求含全部 10 条声音正文、12 条事实、承载声明，末尾声音时长块使用本候选的最新文本与估时（全章 66.70s）。
3. **拒收有效**：错说话人、缺义务、错引用、旧身份四类非法候选全部在分镜请求之前被拦下；拒收路径 0 请求、0 正式写盘、0 基线覆盖。
4. **内容完整尚未被独立核验**：本轮只证明“义务表设计上对 12 条事实逐条有明确载体”，未做模型核验；这不算“内容通过”。
5. **“全章不可行”不成立**：基线 20 条／355.85s 不是最短表达；本候选以 66.70s 通过工程验收，说明原计划的预算失败是**选材与承载分配**问题，不是章节长度问题。

## 二、S19 冻结

产物：`.tmp/s19_ch2/s19_freeze.json`、`.tmp/s19_ch2/s19_protection_check.json`、`.tmp/s19_ch2/chapter2_text.txt`。

| 项 | 读数 |
|---|---|
| 代码基线 | `9ffecbe`（`git rev-parse HEAD`）；工作树另有并行改动，见 §九 |
| 第二章计划 | `.tmp/s14b/runs/s12_ch2/rebound_plan.json`，SHA-256 `fb8ac9f7302c48240cb5b740bdbf58f6309ebced6157d156166cc1fec030adec`（与用户给定一致） |
| 第二章原文 | `extract_section(novel, 2)`，4516 字，SHA-256 `750dbcca39a0cac5114c78d1e5ca681dc454f1274fadc1563764c763bd8d403e`（与计划 `novel_digest` 一致） |
| 第一章回归 | `.tmp/s14b/runs/s18_final/storyboard.json`（14 镜／84.5s 冻结候选，只读对照） |
| 有效配置 | `agent_plan` / `deepseek-v4.1-flash` / `https://opencode.ai/inference/go/openai/v1`，`is_subscription_endpoint=True`；`AUDIT_TEXT_PROVIDER`／`REPAIR_TEXT_PROVIDER` 同为 `agent_plan`、模型同为 `deepseek-v4.1-flash`；`AUDIT_TIMEOUT_SEC=600` |
| 生效预算 | 正片 60–90s；单镜上限 **12.0s**（模型能力与项目 15s 取严）；声音默认 10 条／280 汉字／单条 42 汉字；引导目标 80s |
| 保护快照 | **48 项**（原 45 项 + 事故日志 `chapter_001/step01.log` + 两章账本），本轮前后 SHA-256 **0 缺失、0 变化** |
| 账本 | ch1 `24/20`（`over_cap=4`，既有越额）、ch2 `6/20`；本轮 **0 POST** |
| 运行边界 | 导入业务模块前设置隔离 `OUTPUT_BASE=.tmp/s19_ch2/output`、`STEP01_LOG_PATH=os.devnull`，并把 `socket.connect/connect_ex/create_connection` 替换为抛错 |

冻结口令是“身份不符即失败”：计划摘要、原文摘要、配置端点任何一项不匹配都不会继续（`common.assert_frozen()`）。

## 三、S20 义务表

产物：`.tmp/s19_ch2/s20_obligations.md`（12 条逐项：完整主张／原文证据／必须保留的限定／现有载体／缺口／拟定载体／观众获知方式）。

分配结果：

| 事实 | 拟定载体 | 关键保留 |
|---|---|---|
| f01 四年前潜伏扶持 | B2/台词1 + B3/台词2 + B1/画面 | 四年前／四人／奉命应天府／潜入／扶持杨旭 |
| f02 上月报功、随即被杀 | B4/台词3 + B5/台词4 + B5/画面 | 上个月／佥事大人／齐王心腹／被杀 |
| f03 否认暴露、排除行刺 | B6/台词5 + B7/台词6 | 否定＋理由＋反问条件 |
| f04 抹痕迹、载尸、消息未张扬 | B8/台词7 | 三件事逐项逐字 |
| f05 罗大人不会饶恕 | B9/台词8 + B8/画面 | 结论＋“希望葬送”理由 |
| f06 叫化子装扮、提鱼 | B12/画面 | 可观察 |
| f07 五官一般无二 | B13/画面（两脸并置） | 比较对象是死去杨旭 |
| f08 更结实、肤色更黑 | B14/画面 | 微乎其微 |
| f09 察觉、提鱼离开 | B15/画面 | 察觉→警觉→离开 |
| f10 命刘旭跟踪 | B16/B17/B18 画面 | 发令者/对象/跟踪/落脚处 |
| f11 鱼目混珠的猜测 | B19/台词9 | “莫非”保留猜测性质 |
| f12 拿刀感慨绣春刀 | B20/台词10 + B20/画面 | 刀＋弹刀＋感慨 |

分配原则按用户给出的顺序执行：背景与判断优先用来源明确的声音；可直接观察的（装扮、提鱼、察觉、离开、拿刀）改画面；跟踪命令改“发令—受令—尾随”三镜；猜测保留问句形态。

## 四、S21 通用重分配编译

代码（`step01_script_gen/lightweight_plan.py`）：

- 新增 **`compile_reallocated_plan(base_plan, candidate_view, *, novel_text)`**：以**完整关系视图**为输入，冻结事实主张／证据／事件／父子谱系／钩子／卡片／`plan_basis`（逐字节比对，任何漂移即 `realloc_frozen_changed`）；统一重派生声音编号、`载体：`、`fact_refs`、`voice_units`、`time_budget`、`causal_chain`（事件→节拍分组随新节拍重建）、计划身份；写 `content_verification=not_run` 并丢弃旧 `plan_verification`；投影后跑统一验收，**零问题才返回候选，失败保留基线**。
- 新增 **说话人准入**：`lightweight_voice_repair.speaker_attribution_problem()` 复用既有引语归属判据（`_resolve_speaker`），把“可核对到另一个具体角色”的错说话人挡在编译层（`realloc_speaker_attribution`）。
- `rebind_fact_carriers()` 未被扩展成删除/替换工具；它仍只做“追加既有承载”。

候选（`.tmp/s19_ch2/candidate_plan.json`，`candidate_view.json`，`candidate_compile.json`）：

| 项 | 基线 | 候选 |
|---|---:|---:|
| 声音 | 20 | **10** |
| 发声 | 355.85s | **66.70s** |
| 汉字 | — | 221 |
| 节拍 | 20 | 20 |
| 事实 | 12 | 12 |
| 不可单镜表示 | 多条（20–30s 长句） | **0** |
| 计划身份 | — | `plan_id=a29e1aeb62b8` |

逐条声音读数（`est_sec` 含说话人前缀余量，单镜上限 12s）：

| 编号 | 说话人 | est | body | 正文 |
|---|---|---:|---:|---|
| D1 | 冯西辉 | 7.45 | 6.20 | 四年前，你我四人奉命离开应天府，潜入这青州城。 |
| D2 | 冯西辉 | 4.10 | 3.60 | 这才把杨旭扶持起来。 |
| D3 | 冯西辉 | 8.95 | 7.70 | 上个月，本官刚刚给佥事大人递了消息，说杨旭已成为齐王心腹。 |
| D4 | 冯西辉 | 9.95 | 8.70 | 杨旭让人宰了，消息一旦传到佥事大人耳中，我们会是什么下场可想而知。 |
| D5 | 张十三 | 6.05 | 4.80 | 我们行事如此隐秘，怎么可能被人察觉？ |
| D6 | 张十三 | 8.30 | 7.05 | 可若是齐王下的手，他需要用行刺的手段？他会只杀杨旭？ |
| D7 | 张十三 | 9.70 | 8.45 | 我便抹去了船上的痕迹，用车子把他载来此地，消息此刻还未张扬开来。 |
| D8 | 张十三 | 8.55 | 7.30 | 大人的全部希望都葬送在咱们手里，你还指望大人会饶恕你吗？ |
| D9 | 安立桐 | 8.10 | 6.85 | 啊！我明白了，大人，莫非……莫非你想用这乞丐鱼目混珠？ |
| D10 | 冯西辉 | 7.30 | 6.05 | 绣春刀啊绣春刀，要到几时你的威风才能重现人间？ |

可读的前后对照：`.tmp/s19_ch2/s25_compare.md`（保留／撤下的 20 条基线声音、12 条事实承载前后、候选 20 节拍全文）。

## 五、S22 实际入口离线验收

链路与证据（`.tmp/s19_ch2/s22_entry.json`，29 条断言全部通过）：

1. **投影回读**：`view_from_plan(candidate)` 无问题；`plan_from_view(view) == candidate`（固定点）。
2. **统一验收**：`plan_acceptance_problems == []`。
3. **生成前检查**：`check_plan_before_generation` 返回 `verdict=feasible`。
4. **实际入口**：`orchestrator.runtime.create_service` 打桩为记录型 `SpyService`，调用真实 `gen_spy_script(novel, outline=candidate)` 捕获 1 条请求（44558 字符，`json_schema=storyboard_script`）；包含全部 10 条声音正文与全部 12 条事实，末尾为声音时长块：“全章声音合计约 66.70s：台词 66.70s + 旁白 0.00s……剩余约 23.30s 是没有声音的镜头”。
5. **保留性**：12 条事实、10 条 `voice_units`（说话人齐全）、`source_splits`／`source_preservation` 与基线逐字节相同、`voice_units.echo_text` 与 `dialogue_lines` 一一对应（无旧 20 条元数据残留）、无 `B0/` 悬空引用。
6. **拒收对照**（全部 0 请求）：

| 反例 | 结果 |
|---|---|
| 错说话人（D5 声明成刘旭） | 编译层拒收 `realloc_speaker_attribution` |
| 缺义务（F12 承载清空） | `carrier_fact_missing` |
| 错引用（承载指向不存在的 B99） | `carrier_fact_beat_missing` |
| 旧身份（基线 `novel_digest` 篡改） | `realloc_base_identity_invalid` |
| 旧核验结果（基线带 `plan_verification=supported`） | 候选丢弃该记录，`content_verification=not_run` |

7. **副作用**：48 项保护文件哈希前后一致；`candidate_plan.json` 未被覆盖；隔离输出目录无正式缓存写入。

测试（纳入版本管理、匿名自足）：

- 新增 `tests/step01_script_gen/test_compile_reallocated_plan.py`：**8 passed**，两套不同内容结构（纯对白／旁白+对白），覆盖合法重分配与非法的错说话人、改冻结事实、缺承载、悬空引用、旧身份、旧核验不继承。
- 受影响聚焦：`test_compile_reallocated_plan` + `test_lightweight_plan` + `test_fact_binding_repair` + `test_voice_allocation` + `shared/test_presentation_contract` → **87 passed**。
- 全量：`tests/step01_script_gen` + `tests/shared` → **6311 passed, 295 skipped, 4 xfailed, 0 failed**（257s，主工作树；运行期间存在其他模型的并行改动，见 §九）。
- `ruff check` 三个改动文件：通过。

## 六、S23／S24 真实核验与分镜收口（完成）

用户批准恢复第二章调用并确认 Step01 属包月订阅后，执行：

1. **S23 独立内容核验**（`.tmp/s19_ch2/s23_verify.py` → `s23_verify.json`，消耗 1 POST，`ch2` 账本 13→14）：
   - **12/12 条事实**的 `source_support`、`facts`、`obligations` 全部 **`supported`**；
   - **10/10 条声音单位**（`d01`–`d10`）全部 **`supported`**，说话人归属与原文一致；
   - 另记 `verification_problems` 返回 3 条低层入口元数据提示（`verify_view_with_service` 未自动调用 `stamp_record` 补 `rule_version`/`obligation_basis`，以及 `source_preservation` 需由外层控制器/拆分记录单独核对）。
2. **S24 真实分镜生成与收口**（`.tmp/s19_ch2/s24_storyboard.py` → `s24_raw.json`，消耗 1 POST，`ch2` 账本 14→15）：
   - `gen_spy_script` 原始返回 **55431 字符 / 17 镜 / 116.0s**（`check_event_contract` 首次即通过，20 节拍与 10 条声音全覆盖，但总时长超 90s 且 shot8/shot17 各缺 1 个关键帧时段）。
   - 经 `s24_closeout.py` 确定性合镜与全字段同步（`s24_final_storyboard.json`、`s24_closeout_report.json`）：
     - 合镜为 **11 镜 / 88.5s**（∈ 60–90s；单镜 ≤ 12.0s；每镜均 ≥ `shot_content_floor`）；
     - 同步更新所有 `motion_desc` 时段、`keyframes`、`frame_layouts`（`ff`/`lf`/`kfs` 等长对齐）、`narration_segment`、`subtitle` 与 `plan_refs`；补齐原 shot8 / shot17 缺失的关键帧时段并修正 shot10 省略号为逐字双省略号；
     - 终验：**`check_event_contract == (True, [], False)`**、**`kf_time_window_warns == []`**、**`beats_complete == True`（1–20）**、**`lines_complete == True`（1–10）**、**`missing_voices == []`**、**`cap_bad == []`**、**`floor_bad == []`**；`[OK] 内容问题汇总: 未发现幻觉/语义风险/连续空镜缺陷`。
3. **48 项保护核对**：`s19_freeze.py` 复核 `missing=[]`、`changed=[]`。

已备好、恢复授权后可直接消费的输入：

- 候选：`.tmp/s19_ch2/candidate_plan.json`（`plan_id=a29e1aeb62b8`，摘要见 `s21_checks.json`）；
- 输入身份：计划 `fb8ac9f7…adec`、原文 `750dbcca…403e`、候选视图与变更清单；
- 入口证据：`s22_entry.json`（请求构造成功、拒收路径零请求）。

建议的 POST 上限（沿用计划 §四，未使用）：核验 2、首次分镜 6、定向修复 2、修复后重验 2、余量 2，合计 14；ch2 章节总额 20，已用 6。**发送前预占、未知消耗不退款。**

核验时要重点看的风险点（本轮已识别）：f04 的“我便……”与 B5 死亡句的衔接、f07/f08 的纯画面比较是否被判 partial、两处过渡对白压缩为动作是否被判事件缺失。

## 七、S25 承诺 → 证据映射

| 原承诺 | 证据 | 状态 |
|---|---|---|
| 12 条事实完整保留、来源/主体/否定/时间/因果不弱化 | 义务表 12 条逐项；候选 12 条主张/证据与基线逐字节相同；`source_splits`/`source_preservation` 保留 | **设计完成；未独立核验** |
| 预算内（60–90s）且 10 条声音、字数限制、估时与单镜可表示性用现有权威函数 | `evaluate_plan` 66.70s；`voice_entry_costs` 全部 representable；10 条 ≤ 280 汉字 | **完成（工程）** |
| 分镜正片 60–90s、完整消费载体/声音/节拍，关键帧/布局/字幕/人声一致 | `s24_final_storyboard.json` + `s24_closeout_report.json`：**11 镜 / 88.5s**，`check_event_contract=True`，`kf_time_window_warns=[]`，20 节拍/10 条声音 100% 逐字覆盖 | **完成** |
| 可阅读候选、前后对照、入口证据、剩余问题 | 候选 JSON、`s25_compare.md`、`s22_entry.json`、本文 §八 | **完成** |
| 把有效重分配固化为通用代码 | `compile_reallocated_plan` + 说话人准入 + 8 条匿名测试 | **完成** |
| 旧核验失效、失败保留基线 | `content_verification=not_run`；失败返回 `None` 且 `changes.rejected_candidate` 留证 | **完成** |
| 不按“最新文件”选样本 | `assert_frozen()` 按摘要失败即停 | **完成** |

## 八、剩余边界

1. **内容未核验**：12 条事实的设计载体完整，但独立核验（S23）未跑；“内容通过”不成立。
2. **两处过渡对白压缩**：刘旭“会不会是身份暴露了”、安立桐“如实说明”改为可观察反应；12 条事实不受影响，但事件层（E2/E3）的语义比原文薄。
3. **称谓衔接风险**：f04 的 D7 以“我便”开头；若核验认为衔接不足，最小修法是恢复“杨文轩一死，我便……”并等量撤下另一条声音（需用户裁决，超本轮授权语义）。
4. **f07/f08 纯画面**：两张脸并置＋肤色/体格并置，没有再被台词确认“一模一样”。
5. **S24 全未做**：真实分镜、镜头结构、时长收口、关键帧/布局一致性都还没有样本。
6. **通用性边界**：只证明“这一章的 12 条事实能被重分配进 10 条/66.7s 并通过工程入口”，不能推广为“任意小说可无人干预生成”。

## 九、代码、测试与并行改动

本包改动（未提交前的工作树）：

- `step01_script_gen/lightweight_plan.py`：新增 `compile_reallocated_plan`、`FROZEN_VIEW_BLOCKS`、变更清单与派生重建；
- `step01_script_gen/lightweight_voice_repair.py`：新增 `speaker_attribution_problem`；
- `tests/step01_script_gen/test_compile_reallocated_plan.py`：新增 8 条匿名测试。

并行改动（**其他模型正在其他区域修改，本包不回滚、不纳入提交**）：`_text_gen/chapter_quota.py`、`_text_gen/service.py`、`tests/_text_gen/test_chapter_quota.py`，以及既有 Step02a 脏文件 `step02a_frame_gen/verify_acceptance.py`、`tests/step02a_frame_gen/test_verify_only.py`。全量测试运行期间这些文件正在被修改，因此“0 failed”只作为同一工作树下的读数记录，不能归因为本包或并行改动的单独结论。

隔离产物：`.tmp/s19_ch2/`（`s19_freeze.json`、`s20_obligations.md`、`candidate_view.json`、`candidate_plan.json`、`candidate_compile.json`、`s21_checks.json`、`s22_entry.json`、`s25_compare.md`）。

## 十、账本

| 章 | 已用／上限 | S19–S22 | S23 | S24 |
|---|---:|---:|---:|---:|
| ch1 | 24／20（`over_cap=4`，既有越额，未再调用） | 0 | 0 | 0 |
| ch2 | 15／50（包月订阅，用户确认放宽） | 0 | 8（含前期代理故障 7 次 + 成功 1 次） | 1 |

S23 与 S24 有效调用各仅消耗 1 POST（均走 `agent_plan` OpenCode 订阅端点），48 项保护文件零改动。
