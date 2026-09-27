# step01 未完成事项清单（2026-09-26 晚）—— 收口版

> 本文件原为「未做完的事」清单。P0/P1/P2 已在本轮收口，此处改写为**结项记录 + 剩余项**。
> 过程记录见 `docs/step01-plan-feasibility-gate-2026-09-25.md`；分级盘点见
> `docs/step01-grading-inventory-2026-09-26.md`。
> 提交：`db95d0b`（v6.135~v6.141 主体，20 文件 +4701 −170）、`fc19b67`（P1-3/P1-4，4 文件 +304 −103）。

---

## 已收口

### P0-1 ✅ 重跑 3/3 候选台词破损 —— 根因定位并修复（v6.141）

**根因不是台词门，是生成侧镜数塌缩。** v6.138 往契约块加的
「四、镜头节拍 B1..Bn」+「五、每个节拍都必须被至少一个镜头承载」被模型读成**镜头骨架**，
按「每节拍恰好一镜」最小满足第五节的覆盖要求。

硬证据（同章同计划，唯一变量是契约块）：

| | 契约块 | 镜数 | 正片时长 | 台词门 |
|---|---|---|---|---|
| 18:06~18:41（加块前） | 825 字符 | 13-14 | 48-52s | 通过 |
| 20:21~21:35（加块后） | 1576 字符 | 5-7 | 28-40s | 3/3 候选 3/6 句 not_found |

`生成执行契约 N 字符` 逐 run 记在日志里，825→1576 的跳变与塌缩窗口**逐字对上**；
真正的烟枪是 `shot_count_bounds: 总镜头数 7 < 软下限 10` 这条 **WARN**，不是 FATAL。

**修**：第四节末尾显式切断「节拍→镜头」映射（「节拍数 ≠ 镜头数…镜头数由总时长目标
反推」），第五节允许一个节拍由多镜共同承载。判据编号口径不变（plan_refs 校验按 B/D 序号对齐）。

**实跑复验**：单候选探针 7镜/28s/3句破损 → **12镜/59.0s/6句全过**；完整 run
**14镜→15镜(拆1)、66s→75s、台词门 0 次失败**。
回归用例 `test_event_contract.py::test_execution_contract_beats_are_not_a_shot_list`
（对 v6.138 版 prompt 判红、v6.141 版判绿，已用回退-复跑验过判别性）。

### P0-2 ✅ 第 2/3 层验证重跑（run_id `20260926T215715-81240`）

| 判据 | 结果 |
|---|---|
| ① 身份比对日志 | ✅ 出现 `审计身份比对（(镜头,族) 归并措辞漂移）：仍在…｜消失…｜新现…`；第 3 轮「仍在 无」= 上一轮 4 条身份全部真解决 |
| ② 触达兜底单次记账 | ⚠️ **本轮未被触发**（无身份重复），仅单测覆盖；已补打「触达身份」使下次可核 |
| ③ 联动组暂缓 | ⚠️ **本轮未被触发**（3 轮失败原因全是 `new_blocking`，无 `no_target_resolved`） |
| ④ 收敛轨迹 | ✅ **7→4→1**（单调），未复现 20:49 的 21→10→6→3→7 回归 |

run 终局仍 BLOCK，但发生在**提交层**（真内容缺陷：末轮 shot 3 `PROP_CHAIN_BREAK`），
不再死在候选层；三份正式产物未替换（`script_result.json` 仍是 09-24）。

### P1 ✅ 四项

1. **日志污染 —— 原诊断有误**：pytest 侧早已隔离（`tests/conftest.py` v6.50 把
   `OUTPUT_BASE` 指到 `.pytest_tmp/`，该目录 gitignore，产线日志 grep 不到测试标记）。
   **真污染源是 `.tmp/` 探针脚本**：导入 `main` 即绑生产 `step01.log`，实测单跑写 142 行、
   事后无法区分归属（正是排查 P0-1 时踩到的取证障碍）。
   修：路径收敛 `shared.paths.step01_log_file()`，`STEP01_LOG_PATH` 可覆盖，
   main/timecode/validation 三处一起跟随；4 个回归用例（含源码级守卫）。
2. **BEST-GUARD「新稿胜出」补对称 WARN**（此前静默 return，快照为何失利不可复核）。
3. **审计坏目标降级**（用户决定，v6.142）：坏 repair_target 条目保留 blocking、
   本轮不授权它，**其余合法目标照常进修复轮**（旧口径「一条坏目标 → 整轮 BLOCK、
   一轮修复都不发」被 20:25 run 证伪——shot 3 的 12 条越界路径连坐了整轮）。
   审计 prompt 同步改写（旧文案承诺的「整条作废」已不成立），并补「数组下标必须落在
   本稿该镜实际长度内」。
4. **欠账台账按欠账轮次选**（用户决定，v6.142）：台账新增 `debt[revision]`（卷入过几次
   BLOCK 的 run），全饱和放开时**按欠账轮次升序优先选欠账最少的一份**（同档再比分数/风险），
   被选中者下轮 +1 自然降位。读写共用 `_coerce_debt_map` 容错口径（单条脏值不废整张台账）。

### P2 ✅ 记忆 + 提交

- 新增记忆：`aivideo-dialogue-integrity-gate-v6135`、`step01-log-path-isolation`、
  `bde-trial-verdict-and-prompt-surface-trap`；MEMORY.md 索引已加三条。
- 提交 `db95d0b`（主体）与 `fc19b67`（P1-3/P1-4）。

### B′/D′/E′ 合并对照试跑 —— 已跑，**三项全平，已回退**

- **〔09-26 复核修正〕** 本文档初版写「`prompts/system.md` 对当前 provider 不生效」——
  **错的**。剖面按**模型名**解析（`prompts.py:984 MODEL_PROMPT_PROFILES`，
  `main.py:273` 等 6 处走 `resolve_model_prompt_profile(model)`）；
  `SCRIPT_CANDIDATE_MODELS=deepseek-v4-flash` ⇒ 实际是 `build_prompt` + **`system.md`**，
  provider 级 `auto_select_*` 只是未注册模型的回退。初版只看 provider 就下了结论。
  三批最终落 `main.md`（`build_prompt` 经 `PROMPT_TEMPLATE` 用它）**仍然生效**，
  但两臂记的指纹取了 `DOUBAO_SYSTEM_PROMPT` ⇒ **记录的指纹不能标识真实请求的 system**。
  产物 `.tmp/trial_bde/20260926_2215/`。
- **读数**：B′ 相对时间词 5.33→5.33；D′ 中段镜 2.33→0（不可比）；E′ 承载句 0→0、
  ff_desc 均长 44.7→46.8；守卫镜数/时长/字数无塌缩。
- **免跑语料核验**（55 份稿件 / 703 镜）证伪两条前提：占位承载句 **0 次**、
  ff_desc 均 **39.6 字**（软指引 800）⇒ E′ 无从改善；B′ 的正则把顺序连接词
  （紧接着 276 / 然后 105）算成了相对时长词，只留真·时长词全语料仅 14 次，
  且两臂逐项完全一致（随后 4 vs 4）——**B′ 对规则自己点名的「随后」一个没少**。
- **〔09-26 复核修正〕两次「消融」都是无效方法**，原写「两条假设已排除」不成立：
  ① 去主要事件的探针改的是 `_compact_doubao_outline_anchor`——`build_prompt`
  **根本不调用**它；② 去节拍的探针只删了契约块，而 `build_prompt` 注入的**完整大纲
  锚点仍含【镜头节拍】8 条**（`outline_to_anchor` 标签实测），**节拍输入从未被移除**。
  ③ 且试跑脚本**绕过了计划门**（见下），10 份是「不可行计划下的诊断稿」，
  按正式候选解释不成立。⇒ 章 2「一节拍一镜」仍只是**未被证伪的线索**。
- **回退**：`prompts/main.md` 三段 + `rules_index` v6.143 条目已 revert；
  回退后 prompt 指纹回到 `67f86b877ffb6b7b`，rules_index 回到 43 条。

---

## 剩余项

1. **提示与硬门的切点冲突（09-26 复核第 2 项，本次已修）**：`system.md` 与
   `main.md` 一直写着「切点选在「，」「…」「。」处」，而统一判据
   （`plan_feasibility._SENT_END_CHARS`）**逗号切点一律非法**——提示教模型按逗号拆、
   硬门按句末拒收，章 2 第 1 句（80 字，通篇只有逗号）正是这条冲突的靶子。
   已改为「切点只许落在句末标点（。！？…；）之后」，并补「安全字数 ≠ 存在合法切点
   （超了却没有句末切点 ⇒ 不可表示，回规划层另选）」。两处示例的旧标定
   （37-64 字一镜可容纳）也一并改掉。
   守卫：`test_model_prompt_profile.py::test_split_guidance_cut_points_match_the_hard_gate`
   + `::test_system_prompt_split_examples_match_shot_cap`（回退旧文案两条都判红，已验）。
2. **测试文件未跟踪 ⇒ 回归不会随提交交付（09-26 复核第 3 项，本次已补）**：
   `tests/` 被 `.gitignore` 忽略，`test_model_prompt_profile.py` / `test_best_guard.py` /
   `test_step01_commit.py` 三个含本轮回归的文件此前 `git ls-files` 查不到。
   已 `git add -f` 纳入。
3. **★ 章 2 计划本身不可行（09-26 复核定位，比「8 镜」重要得多）**：
   `chapter_outline.cache.json` 的 `plan_replan.result = "infeasible"`、
   `after_unrepresentable = 2`（`skipped="already_attempted"`，重选已跑过且无改善）。
   逐句核验：第 1 句 80 字 ≈19.8s **句内无句末边界**（只有逗号/顿号）；
   第 3 句 70 字 ≈17.7s 只有 1 个句末边界、切不出每段 ≤12s 的合法解。
   ⇒ 正式流程在 `main.py:7519 check_plan_before_generation`（**候选扇出之前**）就停，
   **章 2 当前生成不出正式候选**。先前那 10 份诊断稿是绕过该门的产物，不代表本章正常产出。
   **下一步应先让章 2 计划变可行，再做镜数归因**；走哪条路（改选材/改重选策略/动切分判据）
   属已定判据与容量，需用户批准。
   - 判据面事实（独立于上述）：`runtime_extend` 与 `shot_count_bounds` 都只 WARN，
     候选层 `structural_ok` 不看总时长 ⇒ 低于 60s 的稿子不会被候选层拦下。

4. **`system.md` 陈旧标定（本次已修）**：原文「37-64 字（8.8-14.7s）**不必切**，一镜可容纳」
   是按 **15s** 上限标定的旧校准，与同段上文的 `SHOT_CAP=12s` / `NARR_MAX_CHARS=44` 自相矛盾，
   等于教模型把超容台词留在一镜（诊断稿实测：256 字对白塞进合计 27s 的 5 镜，需 64s）。
   已改为「≤44 字（≈12s 以内）不必切，超过就必须切」，并加守卫用例
   `test_model_prompt_profile.py::test_system_prompt_split_guidance_matches_shot_cap`。
5. **B′/D′/E′ 若要重跑**：须先解决第 2 项（退化配置上的对照没有判别力），
   并先修 B′ 判据口径（区分相对时长词与顺序连接词）。
6. **F′ 三视图**：规划见 grading-inventory §4；02a 报量确认、02b 逐项确认两个门保留。
7. **A′ 核心提案待批**：`rule_tier` 独立元数据 + 注册期 lint（craft/taste 约束禁止配套
   blocking code）。

## 预存已知项（勿误判为新问题）

- `_check_grounding` 单字 safe 词表缺陷（春药/媚药→药，5 字滑窗）——另一工作包；
- 拆分子镜 subtitle 无代码校验；`temporal_plan` 0/N 迁移；doubao 专属 prompt 的 stale 横幅；
- `test_threat_visible_v650` 曾记数据漂移失败，**本轮 23 条相关用例全绿，未复现**。
