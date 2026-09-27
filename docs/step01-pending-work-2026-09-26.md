# step01 未完成事项清单（2026-09-26 晚）

> 本会话已完成：九审三根因实证 → 修复 1/2/3（v6.138 / v6.139 / v6.140）全部实施，
> 全量测试 **2416 passed**（基线 2404），代码已暂存未提交。
> 过程记录见 `docs/step01-plan-feasibility-gate-2026-09-25.md`（六审→v6.140 节）。
> 本文件只列**未做完的事**，按优先级排列。

---

## P0-1 诊断：最新重跑 3/3 候选台词破损（阻塞验证重跑）

**现象**（`.tmp/step01_rerun11_20260926_stdout.log`，21:xx）：
3 个候选全部被台词完整性硬门判无效，每个 **3/6 句破损**，
示例 `不……不知道，那人……那人一下子从水里跳出来，就……就杀了公子…（not_found）`
（计划第 3 句——听香供述，60+ 字长句）。破损候选不进池、不留快照，
run 以 `DialogueIncompleteError` 显式失败退出（exit 1）。

**为什么可疑**：18:46 的 run 用同一份 6 句计划能产出 100 分合格候选；
之后生成侧只新增了——
1. 生成执行契约块新增「四、镜头节拍（B1..Bn 编号）」+「五、事件履约声明
   （plan_refs 规则）」（`prompts.py::render_execution_contract`）；
2. main.md 骨架/规则 6、doubao_system.md RULE-31、shot schema `plan_refs` 必填；
3. 台词门新增排他性检查（extra_dialogue）与说话人前缀单源判据。

3/3 候选都破 **3/6 句**、破损数完全一致 → 疑似**系统性 prompt 副作用**而非随机。

**排查步骤**（建议顺序）：
1. 抓一份候选原文：`SCRIPT_CANDIDATE_TOPK=1` 跑一次，或在
   `_gen_one_candidate` 门拦截处把 `_cand` 的 6 个 `narration_segment` 落盘
   （对不上的是哪些句、句是否被改写/合并/截断/带上了 B/D 编号）。
2. 对照三种假设：
   a. 模型把多条台词**合并进一个 narration_segment**（排他性/跨镜拼接判不过）；
   b. 模型照抄契约块**编号**（"1. 公……"连编号进旁白）；
   c. `plan_refs` 必填挤占注意力 → 长句被截断/改写。
3. 若确认是契约块挤爆注意力：把第五节规则压缩为 2 行；或把 plan_refs 从
   「每镜必填」降为「台词镜必填」；或把节拍编号块改为紧凑单行格式。
4. 修后回归：`tests/step01_script_gen/test_event_contract.py` +
   `test_candidate_dialogue_integrity.py` 全绿再重跑。

## P0-2 第 2/3 层验证重跑（被 P0-1 阻塞）

修复 2（v6.139 触达兜底单次记账 + 审计身份比对）与修复 3（v6.140 组暂缓止损
局部化）已实施并单测覆盖，但**尚未在真实 run 验证**。重跑成功后按以下判据
判定生效：
- 日志出现 `[COMMIT] 审计身份比对（(镜头,族) 归并措辞漂移）：仍在…｜消失…｜新现…`
  （修复 2 的跨轮比对）；
- 同一 (shot, family) 身份第二次被触达时不再给兜底分（台账
  `audit_touched_ids` 只记一次，第二轮 accepted 走真实证据解决或 no_target_resolved）；
- 若联动组轮 `no_target_resolved`：日志出现
  `[COMMIT] 联动组 … 整组暂缓 … 组外目标继续`，且下一轮修复目标不含组内镜头
  （修复 3）；组的 blocking 保留、最终照常 BLOCK；
- 对比 20:49 run 的收敛轨迹（21→10→6→3 后审计回归 7）：看第 5 轮类别的
  回归是否被身份比对+兜底停用遏制。

## P1 本轮新发现的观察项（建议随后处理）

1. **pytest 提交层测试污染真实 step01.log**：
   `test_commit_defers_failed_group_and_keeps_repairing_others` 等直接调
   `_commit_step01_outputs` 的用例走真实 `_logger`（文件 handler 指向章目录
   step01.log），测试日志会混入生产日志（21:19 已观测到测试 payload 出现在
   step01.log）。修法：conftest 里对 step01 测试统一 monkeypatch logger 的
   文件 handler（或 `_commit_step01_outputs` 接受可注入 logger）。
2. **BEST-GUARD「新稿胜出」路径无日志**：
   `_best_guard` 比较里快照胜出有两行 WARN，新稿胜出（same_tier_within_margin /
   new_tier_not_worse）静默 return——20:49 run 无法从日志判断快照为何失利于
   新候选。补一行对称日志（含 best_rank/new_rank/margin）。
3. **审计声明不存在字段路径 → 直接 BLOCK**（20:25 run 实锤，预存问题）：
   审计把 `repair_targets` 写到 `frame_layouts.kfs[0][1].name` 等不存在路径，
   `_audit_repair_target_problems` 判「缺少可执行的定向修复目标」立即 BLOCK，
   一轮修复都不发。方向：提交层对「目标不可执行」条目降级为「该条保留 blocking、
   其余目标继续修」，并要求审计契约校验字段路径合法性（属审计侧工作包）。
4. **欠账台账饱和使棘轮护栏失效**（预存）：全部历史快照都在欠账台账内时
   「本轮不排除，仍按分数/风险选」——低风险但欠账的快照仍会被反复选中。
   与修复 2 的待验收状态记账（P1-3 方向）合并考虑。

## P2 例行收尾

- 记忆文件更新：`aivideo-dialogue-integrity-gate-v6135.md` 补 v6.138/138.1/139/140
  四节（含大纲缓存重生成事故与恢复、2416 基线）。
- 提交：全部改动已暂存（`git add`，含 `test_event_contract.py` 的 -f），
  **未 commit**（等 P0-1/P0-2 收口后一起提）。

## 预存已知项（勿在 P0 排查中误判为新问题）

- `_check_grounding` 单字 safe 词表缺陷（春药/媚药→药，5 字滑窗）——另一工作包；
- 拆分子镜 subtitle 无代码校验（P2，预存）；
- `temporal_plan` 0/N 迁移、doubao 专属 prompt 的 stale 横幅（预存）；
- `test_threat_visible_v650` 预存数据漂移失败（历史，勿误判）。
