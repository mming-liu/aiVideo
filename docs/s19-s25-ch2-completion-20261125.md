# S19–S25 第二章完整重分配与分镜收口：执行记录（2026-11-25）

**状态：S19–S24 全部完成（隔离运行，正式产物未覆盖）；S25 交付收口。**
本轮完成了「S23 经 `VerificationController` 独立内容核验（12/12 + 10/10 + 父保留 `supported`，`verification_problems == []`）→ S24 真实分镜生成与时长/关键帧/契约收口（12 镜 / 88.625s）」，未覆盖正式产物，未自动正式采纳。

| 层级 | 交付 | 状态 |
|---|---|---|
| 代码落盘 | `compile_reallocated_plan`、`speaker_attribution_problem`、v2 禁删事实守卫、逐 POST 章节额度闭环 | **完成** |
| 离线贯通 | 输入清单、义务表、候选、变更清单、实际入口请求与四类拒收证据 | **完成** |
| 真实收益 | S23 经 `VerificationController` 独立核验（12/12 + 10/10 + 父保留 `supported`，`verification_problems == []`）；S24 真实分镜（17 镜 116s → 收口 **12 镜 / 88.625s**） | **完成** |
| 剩余边界 | 未正式采纳、`char_layout.surface`/景别与运镜提示项、来源修正/重绑为显式入口 | 见 §八 |

## 一、结论先行

1. **工程可行**：第二章 12 条事实在**10 条声音／66.70 秒／20 节拍**下通过统一计划验收（`plan_acceptance_problems == []`）、预算评估（`feasible`，下界 66.70s ∈ 60–90s）与生成前检查（`check_plan_before_generation` 返回 `feasible`）。
2. **入口可达**：候选经投影回读（固定点）后，由**真实 `gen_spy_script` 入口**构造出 44558 字符的分镜请求；请求含全部 10 条声音正文、12 条事实、承载声明，末尾声音时长块使用本候选的最新文本与估时（全章 66.70s）。
3. **拒收有效**：错说话人、缺义务、错引用、旧身份四类非法候选全部在分镜请求之前被拦下；拒收路径 0 请求、0 正式写盘、0 基线覆盖。
4. **内容完整已通过独立核验**：S23 经 `VerificationController` 对全部 12 条事实、12 条来源支持、10 条声音及拆分父主张 `f06` 核心保留给出 **`supported`**，`verification_problems == []`。
5. **“全章不可行”不成立**：基线 20 条／355.85s 不是最短表达；本候选以 66.70s 通过工程验收并收口为 12 镜 / 88.625s 分镜，说明原计划的预算失败是**选材与承载分配**问题，不是章节长度问题。

## 二、S19 冻结

产物：`.tmp/s19_ch2/s19_freeze.json`、`.tmp/s19_ch2/s19_protection_check.json`、`.tmp/s19_ch2/chapter2_text.txt`。

| 项 | 读数 |
|---|---|
| 代码基线 | 起点 `9ffecbe`，本轮收口提交 `019ccbc`（及本次最终修订） |
| 第二章计划 | `.tmp/s14b/runs/s12_ch2/rebound_plan.json`，SHA-256 `fb8ac9f7302c48240cb5b740bdbf58f6309ebced6157d156166cc1fec030adec`（与用户给定一致） |
| 第二章原文 | `extract_section(novel, 2)`，4516 字，SHA-256 `750dbcca39a0cac5114c78d1e5ca681dc454f1274fadc1563764c763bd8d403e`（与计划 `novel_digest` 一致） |
| 第一章回归 | `.tmp/s14b/runs/s18_final/storyboard.json`（14 镜／84.5s 冻结候选，只读对照） |
| 有效配置 | `agent_plan` / `deepseek-v4.1-flash` / `https://opencode.ai/inference/go/openai/v1`，`is_subscription_endpoint=True`；`AUDIT_TEXT_PROVIDER`／`REPAIR_TEXT_PROVIDER` 同为 `agent_plan`、模型同为 `deepseek-v4.1-flash`；`AUDIT_TIMEOUT_SEC=600` |
| 生效预算 | 正片 60–90s；单镜上限 **12.0s**（模型能力与项目 15s 取严）；声音默认 10 条／280 汉字／单条 42 汉字；引导目标 80s |
| 保护快照 | 45 项有基线文件 SHA-256 **0 缺失、0 变化**；`chapter_001/step01.log`（无 S19 前独立基线，与 `20:51` 快照 `3ba2b384…` 一致）及 `quota_ch1.json` 未变动，`quota_ch2.json` 按授权更新至 `16/50` |
| 账本 | ch1 `24/20`（`over_cap=4`，未触碰）；ch2 由 `6/20` 更新至 `16/50`（见 §十） |
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

7. **副作用**：45 项有基线保护文件哈希前后一致（另 3 项无任务前基线单列于 `unverified_no_baseline`）；`candidate_plan.json` 未被覆盖；隔离输出目录无正式缓存写入。

测试（纳入版本管理、匿名自足）：

- 新增 `tests/step01_script_gen/test_compile_reallocated_plan.py`：**8 passed**，两套不同内容结构（纯对白／旁白+对白），覆盖合法重分配与非法的错说话人、改冻结事实、缺承载、悬空引用、旧身份、旧核验不继承。
- 受影响聚焦：`test_compile_reallocated_plan` + `test_lightweight_plan` + `test_fact_binding_repair` + `test_voice_allocation` + `shared/test_presentation_contract` → **87 passed**。
- 全量：`tests/step01_script_gen` + `tests/shared` → **6311 passed, 295 skipped, 4 xfailed, 0 failed**（257s，主工作树；运行期间存在其他模型的并行改动，见 §九）。
- `ruff check` 三个改动文件：通过。

## 六、S23／S24 真实核验与分镜收口（完成）

用户批准恢复第二章调用并确认 Step01 属包月订阅后，执行：

1. **S23 经控制器的独立内容核验**（`.tmp/s19_ch2/s23_verify.py` → `s23_verify.json`，消耗 1 POST，`ch2` 账本 15→16）：
   - 修复 `content_verify.build_verification_prompt` 对仅有 `source_splits` 计划漏发 `source_preservation` 指令的缺陷后，通过 `VerificationController`（含 `validate_record=record_protocol_problems`、`repair`、`rule_version`、`novel_digest`）获取独立记录；
   - **12/12 条事实**的 `source_support`、`facts`、`obligations` 全部 **`supported`**；
   - **10/10 条声音单位**（`d01`–`d10`）全部 **`supported`**，说话人归属与原文一致；
   - **独立父主张保留**：`source_preservation` 对 `["f06"]` 给出 **`supported`** 与逐字原文证据；
   - **身份盖章与复核**：`rule_version=content_verification.v5.background_tier.1`、`obligation_basis=4307b07c...`、**`verification_problems == []`**（`success=True`）。
2. **S24 真实分镜生成与收口**（`.tmp/s19_ch2/s24_storyboard.py` → `s24_raw.json`，消耗 1 POST，`ch2` 账本 14→15）：
   - `gen_spy_script` 原始返回 **55431 字符 / 17 镜 / 116.0s**（`check_event_contract` 首次即通过，20 节拍与 10 条声音全覆盖，但总时长超 90s 且 shot8/shot17 各缺 1 个关键帧时段）。
   - 经 `s24_closeout.py` 按物理空间与运镜连续性重组收口为 **12 镜 / 88.625s**（`s24_final_storyboard.json`、`s24_closeout_report.json`）：
     - 严格落入 **60–90s**；单镜 ≤ 12.0s 且均 ≥ `shot_content_floor`（≥ 3.5s）；
     - 消除合镜空间/机位矛盾：镜头 8（`B12–B14`，4.5s）显式标为 `南阳河畔酒家门口（分屏并置后院马车）` + `推拉切镜`；镜头 9（`B15–B17`，4.0s）以 `摇镜` 连续从门口转入室内并同步 `刘旭` 离场关键帧；**镜头 10（`B18`，3.5s）拆回独立户外镜头**（`南阳河畔小径` + `跟拍`），杜绝室内/户外瞬移；
     - 同步更新全部 `motion_desc` 时段、`keyframes`、`frame_layouts`（`ff`/`lf`/`kfs` 等长对齐）、`narration_segment`、`subtitle` 与 `plan_refs`，补齐原 shot8/shot17 缺失关键帧并修正 shot11（原 shot10）双省略号；
     - 同步更新镜头 9 的道具与状态（`kf 2` 与 `lf` 中 `刘旭` 的 `props=[]`、`states=["空手", ...]`、`support` 改为门槛地面），消除与“放下刀”描述的冲突；
     - 终验：**`check_event_contract == (True, [], False)`**、**`kf_time_window_warns == []`**、**`beats_complete == True`（1–20）**、**`lines_complete == True`（1–10）**、**`missing_voices == []`**、**`cap_bad == []`**、**`floor_bad == []`**、`kf时序一致性 == 0` 告警、`[OK] 内容问题汇总: 未发现幻觉/语义风险/连续空镜缺陷`。
3. **保护核对**：`s19_freeze.py` 复核 45 项有基线文件 `missing=[]`、`changed=[]`；`chapter_001/step01.log`、`quota_ch1.json`、`quota_ch2.json` 在基线清单内为 `expected=None`（`no_pre_task_baseline`），其中 `step01.log` 当前摘要与 S19 启动时（`20:51`）读数 `3ba2b384…` 一致，未做任务起始前独立基线对比。

## 七、S25 承诺 → 证据映射

| 原承诺 | 证据 | 状态 |
|---|---|---|
| 12 条事实完整保留、来源/主体/否定/时间/因果不弱化 | S23 `VerificationController` 独立核验：12/12 `supported`、10/10 声音 `supported`、父保留 `f06` `supported`、`verification_problems=[]` | **完成** |
| 预算内（60–90s）且 10 条声音、字数限制、估时与单镜可表示性用现有权威函数 | `evaluate_plan` 66.70s；`voice_entry_costs` 全部 representable；10 条 ≤ 280 汉字 | **完成（工程）** |
| 分镜正片 60–90s、完整消费载体/声音/节拍，关键帧/布局/字幕/人声一致 | `s24_final_storyboard.json` + `s24_closeout_report.json`：**12 镜 / 88.625s**，`check_event_contract=True`，`kf_time_window_warns=[]`，20 节拍/10 条声音 100% 逐字覆盖 | **完成** |
| 可阅读候选、前后对照、入口证据、剩余问题 | 候选 JSON、`s25_compare.md`、`s22_entry.json`、`s23_verify.json`、`s24_closeout_report.json`、本文 §八 | **完成** |
| 把有效重分配固化为通用代码 | `compile_reallocated_plan` + 说话人准入 + v2 禁删事实守卫 + 逐 POST 章节额度闭环 | **完成** |
| 旧核验失效、失败保留基线 | `content_verification=not_run`；失败返回 `None` 且 `changes.rejected_candidate` 留证 | **完成** |
| 不按“最新文件”选样本 | `assert_frozen()` 按摘要失败即停 | **完成** |

## 八、剩余边界

1. **主链接线范围说明**：`voice_allocation`（`STEP01_VOICE_ALLOCATION=v2`）已接入 `generate_lightweight_outline_result` 自动修订主链并强制 `allow_drop_facts=False` 禁删必保留事实；`source_claim_fix` 与 `fact_binding_repair` 在 `lightweight_plan.py` 中提供显式入口（`correct_plan_sources` / `rebind_plan_carriers`），不自动在首次大纲中盲目触发。
2. **分镜顾问提示项未继续润色**：`s24_final_storyboard.json` 已通过全部契约、时长与关键帧时间窗校验，仍保留若干静态顾问提示（如 `char_layout.surface` 缺省、景别种类 2 < 5、部分镜头 `lf_desc` < 40 字及无声转场关键词建议）；当前**未做正式采纳**。
3. **两处过渡对白压缩**：刘旭“会不会是身份暴露了”、安立桐“如实说明”转为可观察反应；12 条事实已通过 S23 核验，事件层（E2/E3）较原文精简。
4. **通用性边界**：第二章证明 12 条事实可重分配进 10 条 / 66.70s 并通过核验与 12 镜 / 88.625s 分镜收口，不自动推广为任意小说可无人干预生成。

## 九、代码、测试与保护核对

本包代码改动（含复核修复）：

- `step01_script_gen/lightweight_plan.py`：新增 `compile_reallocated_plan`、`FROZEN_VIEW_BLOCKS`、`correct_plan_sources`、`rebind_plan_carriers`，接入 `STEP01_VOICE_ALLOCATION=v2`（强制 `allow_drop_facts=False` 与事实不可删改校验）；
- `step01_script_gen/voice_allocation.py`：新增 `SCHEMA_NO_DROP` 与 `allow_drop_facts` 开关，在自动修订入口拒收 `dropped_facts`；
- `step01_script_gen/lightweight_voice_repair.py`：新增 `speaker_attribution_problem`；
- `step01_script_gen/content_verify.py`：修复 `build_verification_prompt` 对仅含 `source_splits` 计划遗漏 `source_preservation` 独立核验指令的缺口；
- `_text_gen/chapter_quota.py`、`_text_gen/send_quota.py`、`_text_gen/service.py`：实现 `STEP01_READ_ONLY` 硬阻断、直接调用逐 POST 原子 `consume_one`（含内部重试），以及 `reserve_batch` + `reservation_id` 防绕过与防重复扣减；
- 测试：`tests/step01_script_gen/test_compile_reallocated_plan.py`、`tests/step01_script_gen/test_lightweight_wiring.py`、`tests/step01_script_gen/test_source_claim_fix.py`、`tests/_text_gen/test_chapter_quota.py`；
- 干净检出基线对账（独立 worktree 同环境运行 `tests/step01_script_gen tests/shared`，逐用例差异清单见 `.tmp/s19_ch2/clean_checkout_diff.json`，原始输出见 `.tmp/s19_ch2/ead99a7_pytest.txt` 与 `.tmp/s19_ch2/019ccbc_pytest.txt`）：`ead99a7`（56 failed, 2886 passed, 18 skipped, 1 xfailed）vs `019ccbc`（55 failed, 2926 passed, 18 skipped, 1 xfailed），**`new_failures == []`**、**`new_errors == []`**、修复 1 条（`tests/shared/test_execution_contract.py::test_fixture_manifest_digests_match_file_bytes`）并净增 40 条通过。

既有 Step02a 脏文件（`step02a_frame_gen/verify_acceptance.py`、`tests/step02a_frame_gen/test_verify_only.py`）与其余 43 项有基线保护文件经 `s19_freeze.py` 校验 `missing=[]`、`changed=[]`；`chapter_001/step01.log` 等 3 项无基线文件单列于 `unverified_no_baseline`（见 §二）。

## 十、账本

| 章 | 已用／上限 | S19–S22 | S23 | S24 |
|---|---:|---:|---:|---:|
| ch1 | 24／20（`over_cap=4`，既有越额，未再调用） | 0 | 0 | 0 |
| ch2 | 16／50（包月订阅，用户确认放宽） | 0 | 9（代理故障 7 + 初次缺章 1 + 控制器核验 1） | 1 |

S23 经控制器核验与 S24 真实分镜各消耗 1 POST（均走 `agent_plan` OpenCode 订阅端点），45 项有基线保护文件零改动。
