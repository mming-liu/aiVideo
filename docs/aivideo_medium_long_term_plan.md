# aiVideo 中长期系统架构与规模化演进计划（M1–M4 / L1–L4｜v2 源码校准版）

> **与当前在改计划的边界说明**：本计划为独立的中长期演进蓝图，**不打断、不替代**当前正在执行的近期收尾工作（`Post-C06` 运行器修复、`.dsh/step01_budget_frozen` 冻结稿续验与 `chapter_001` 成片交付）。待当前在改计划收口并交付第 1 章 `final.mp4` 后，按本计划顺序切入实施。
>
> **2026-11-16 源码自查与实测校准**：
> 1. 吸收 `19:00` 冻结稿续验三项教训（`§3.1` `content_identity` 失配未定位、`19:01` 修后稿未落快照被 `cap 2` 丢弃、探针误设 `OUTPUT_BASE` 追加写入 `step01.log`）；
> 2. 校准原版 4 处源码脱节：修正 M3 重复造 SSE 轮子、修正 M1/M2 实施顺序倒置（避免先改巨石 `main.py` 再拆）、修正 L1 指纹拆分破坏下游契约、补充 L4 去白名单化前的历史本地测试迁移前置条件。

---

## 1. 演进总览：从“单章受控跑通”走向“跨书跨章规模化生产”

经过前期的契约化改造（`R1–R6`、`Step A–D`、`C01–C06`），`aiVideo` 已经具备了严格的数据契约与零调用离线校验能力，但支撑架构仍停留在“单章脚本驱动 + 全局环境变量 + 人工门禁盯盘”阶段。

中长期演进分为两个周期：
- **中期（1–4 周｜M1–M4：架构解耦与运行韧性）**：先立 `RunContext` 骨架与只读守卫、拆出提交/修复子模块（**M1**），再接入双层出站网关与统一 `resume.py` 修复快照断点（**M2**），调优现有 `AGENT_PLAN_TEXT_STREAM_AUTO` 与连接池（**M3**），并把确定性防线左移至送审前（**M4**）。
- **长期（1–3 月｜L1–L4：多章连续性与规模化治理）**：将现有 AST 符号闭包机制扩展至 `plan_feasibility.py`（**L1**），建设跨章节状态继承与资产锁图库（**L2**），落地镜头级自适应媒体策略与音画前置对账（**L3**），完成历史本地测试迁出与沙箱 `session` 夹具改造（**L4**）。

---

## 2. 中期规划（1–4 周）：系统解耦与运行韧性（M1–M4）

### M1：显式运行上下文 `RunContext`（含只读探针保护）与核心子模块拆出

> **顺序调整说明**：M1 作为结构底座必须**先于或同步于 M2** 落地，否则在 716 KB 的 `main.py` 中直接写 `CP1–CP4` 与预算守卫，后续拆分会造成二次返工。

| 维度 | 当前痛点 | 中期目标方案 | 验收判据 |
| :--- | :--- | :--- | :--- |
| **配置与路径隔离 + 只读守卫** | `step01_script_gen/main.py` 等在 `import` 期求值 `OUTPUT_BASE`、`DURATION_*`，内部 `set_ledger_path` 会覆盖外部配置；诊断探针若误设 `OUTPUT_BASE` 还会直接向冻结目录的 `step01.log` 追加写入 | 抽象 `shared/run_context.py`（不可变 `RunContext` 对象），封装章节范围、路径解析、时长带、通道、预算守卫与 **`read_only: bool` 开关**；所有阶段入口接收 `ctx`，严禁内部覆写已绑定路径；`read_only=True` 时物理禁止打开/追加 `step01.log` 或写任何诊断文件 | ① 同进程连续以不同 `RunContext` 跑两章零串扰；<br>② `read_only=True` 下运行任何探针/确定性层，冻结根目录字节与 mtime **0 变化** |
| **巨石文件渐进拆分** | `main.py`（716 KB）、`outline.py`（446 KB）、`validation.py`（441 KB）、`vimax_bridge.py`（254 KB）单文件职责过载 | ① 第一阶段优先从 `step01_script_gen/main.py` 拆出 `commit_gate.py`（提交事务/G1/G7）与 `repair_loop.py`（受限修复事务），其余部分保留兼容壳；<br>② `shared/vimax_bridge.py` 拆出 `frame_prompt_compiler.py`、`frame_cache_and_lock.py`、`frame_gen_orchestrator.py` | 旧导入路径保留兼容壳；新拆出模块单文件 `< 150 KB`，纯函数可独立单测 |

### M2：统一双层出站网关、修后稿即刻落盘与 `resume.py` 断点统一

1. **统一出站发送守卫 `OutboundBudgetGuard`（`shared/outbound_guard.py`）**：
   - 将文本生成、钩子、语义审计、定向修复、视觉 QC、生图、生视频、TTS 全部纳入同一发送边界守卫，强制实行**双层账本**：
     - **逻辑调用层（`logical_calls`）**：按阶段分类独立设限（如 `hook_max_sends`、`repair_max_sends`、`audit_max_sends`）；
     - **底层网络尝试层（`http_attempts`）**：每次 `requests.post`（含初始请求、SSL 重试、429 退避重发）均计入分类尝试上限与全局总尝试上限，**触顶前在发出字节前 fail-closed 阻断**；
     - 统一输出 `logical_calls`、`http_attempts`、`responses_received` 三指标，彻底消除 `transmitted` 歧义。
2. **修复稿即刻快照化 + 成对额度预检（吸收 `19:01` 丢稿教训）**：
   - **复用并扩展现有 `step01_script_gen/resume.py`**（不另起第二套并行断点格式）：在 `repair_loop.py` 中，一旦 `_cand = _finalize(repaired)` 与 `_contract_reject_reason(_cand)` 通过，**必须在进入下一轮复审调用之前**，立即将 `_cand` 原子写入 `snapshots/repair_round_{k}.json` 并更新 `step01_resume.json`（标记检查点状态 `CP1_PLAN_VERIFIED` → `CP2_CANDIDATE_GENERATED` → `CP3_INITIAL_AUDIT_DONE` → `CP4_REPAIR_ROUND_K_DONE`）；
   - **成对额度预检**：进入一轮修复前，`OutboundBudgetGuard` 必须先检查剩余额度是否足够覆盖 **`1 次修复 + 1 次修后复审`**（除非显式运行在 `--repair-only` 模式）；额度不足以闭环时直接保存当前最新快照并报 `budget_exhausted`，不发起注定无法复审的孤儿调用。
3. **取消粗粒度全局 1200s 总闸丢进度**：
   - 改为**单阶段 SLA 超时 + 超时落盘当前检查点（`PAUSED_AT_CPk`）**；下次 `--resume` 直接从 `step01_resume.json` 续跑，**零重复生成、零重复初审**。
4. **审计复用失配诊断（`reuse_miss_reason`）**：
   - 为 `_reusable_final_audit` 增加字段级失配日志/返回值：当盘上已有同 revision 审计但未复用时，明确记录具体失配字段（`novel_sha256`、`plan`、`expected_identity` 含 `audit_focus`/`run_id`、或指纹/通道），杜绝再出现“原因靠猜、文档误归因”。

### M3：基于现有 `AGENT_PLAN_TEXT_STREAM_AUTO` 的流式扩展与连接池自愈

> **源码校准说明**：`_text_gen/agent_plan_provider.py` 已内置 `AGENT_PLAN_TEXT_STREAM_AUTO`（默认 `chars >= 20000` 或 `max_tokens >= 32000` 自动走 SSE），无需从零重写流式客户端。真正缺口在于：像 P05「计划核验」这类提示词未达 20KB 但推理仍可能超过 ~66s 代理空闲阈值的请求未触发流式，且部分通道/视觉 QC 缺少脏连接池销毁机制。

1. **按场景/阶段放开 SSE 流式阈值**：
   - 下调 `AGENT_PLAN_TEXT_STREAM_AUTO` 触发门槛（或对 `plan_verify`、`hook`、`repair`、`audit` 等推理耗时 > 30s 的阶段显式开启 `stream=True`），同时排查 `deepseek` 与 `opencode` 通道的流式 `usage` / `finish_reason` 兼容性。
2. **Session 级连接池与瞬态 SSL 自愈**：
   - 用受控生命周期的 `requests.Session` 替代裸 `requests.post`；遇到瞬态 `SSLError / ConnectionResetError / 429 / 502–504` 时，先关闭并重建受损连接池再按退避窗口（`1.5s → 3s`）重试，所有重试自动计入 `http_attempts` 上限。

### M4：「生成即合规」——确定性防线左移与送审前自愈

1. **后处理幂等化**：所有提示词注入与 `frame_layouts` 自动补齐函数在写回前强制校验 `C1–C7`（`shared/script_preflight.py`）与 `cross_field_checks`，杜绝“二次拼接导致 `duplicate_injection`（如 `19:00` 审计仍报 4 条）”或“把画外音角色塞进 `on_screen=true`”。
2. **送审前零调用预审门（Pre-Audit Gate）**：
   - 在发起耗时 160–400 秒的 LLM 终审前，先运行确定性质量门（含 `C1–C7`、`cross_field_checks`、`prompt_contradiction`）；
   - 若发现确定性 `hard / visual_risk`（如 `19:00` 跑出的 10 条确定性 blocking，含 `kf_event_order`、`frame_spec_consistency`），**先在送审前拦截或局部修复**，确保送交 LLM 终审的候选 100% 满足全部确定性不变量，不把已知确定性硬伤带进昂贵的终审循环。

---

## 3. 长期规划（1–3 月）：多章连续性与规模化生产（L1–L4）

### L1：扩展 AST 符号闭包提取（保持 `rule_source_fingerprint` 下游兼容）

> **源码校准说明**：`step01_script_gen/quality_issues.py` 已对 `style_prefix.py` 和 `vimax_bridge.py` 实现了 AST 符号闭包提取；若强行把单一 `rule_source_fingerprint` 拆成两个字段，会破坏 `step01_commit.json`、`production_context.py` 与 `require_semantic_gate` 的既有接口。

- **修正方案**：
  1. **保持 `step01_commit.json` 与 `quality_gate.py` 的单一 `rule_source_fingerprint` 契约不变**，不破坏跨阶段准入协议；
  2. 将现有“**只提取 Step 01 实际消费的函数及其模块级常量/词表依赖闭包（AST 提取）**”机制扩展到 `plan_feasibility.py`、`shared/quality_gate.py` 等同时服务多阶段的混合大文件——既剥离纯注释/空白与大纲重排辅助函数导致的无谓重签，又确保被判定函数引用的全局词表/正则常量变更**100% 触发指纹更新**。

### L2：跨章节连续性记忆与 `global/` 资产库治理

1. **跨章状态继承引擎（`chapter_N → chapter_N+1`）**：
   - 自动从第 `N` 章最终提交稿的末镜终态提取**角色服装/伤势状态、随身道具持有关系、时间/光照基调、场景空间坐标**，注入第 `N+1` 章的规划与分镜约束上下文中，杜绝跨章角色“上一章刚换衣/受伤，下一章瞬间复原”。
2. **资产库质量分级与智能复用**：
   - 为 `global/characters_bank/`、`scenes_bank/`、`props_bank/` 建立质量评分与 `.lock_frame` 锁图索引，跨章优先复用已人工确认的高一致性资产图，减少重复抽卡成本。

### L3：Step 02–04 镜头级自适应媒体策略与音画协同

1. **Step 02a 原生 `--qc-only` 与锁图保护常态化**：
   - 固化 `--qc-only` 模式，物理保证 0 次生图 POST、0 次误删；所有人工确认帧统一纳入 `.lock_frame` 清单保护。
2. **Step 02b 镜头级自适应媒体策略与 G3 全面闭环**：
   - 在合入 G3（参考清单先行）的基础上，允许按镜头动作幅度自动或显式指定 `media_policy`（静态/慢推镜头用 `first_only` 规避尾帧人脸漂移，大幅度动作切换镜头用 `first_last`），预计可降低 **30%–50% 生图与重抽成本**。
3. **Step 03 / Step 04 声音两态（G6）与音画时长前置对账**：
   - 全面贯彻 `prepared` vs `confirmed` 两态门禁；在 Step 03 TTS 合成完成后立即核对每句真实音频时长与 `duration_sec` 的弹性配比，提前拦截“视频生成完才发现台词超长塞不下”的跨阶段返工。

### L4：测试工程化、历史本地测试迁出与沙箱配额治理

> **源码校准说明**：当前 `tests/` 下仍有 236 个文件（9,852 处）含作品词的本地遗留用例受 `content_binding_baseline.json` 棘轮钉住且被 `.gitignore` 忽略。不能直接删除 `.gitignore` 白名单，必须先迁出再放开。

1. **两步走终结 `.gitignore` 150+ 行白名单地狱**：
   - **第 1 步（迁出隔离）**：将 `tests/` 中当前被 `.gitignore` 忽略且未入库的 200+ 个本地历史测试迁入 `tests_local/`（仅保留 `tests_local/` 在 `.gitignore` 中）；
   - **第 2 步（默认全量入库）**：确认 `tests/` 下仅剩匿名化、已入库的契约测试后，移除 `.gitignore` 中的 `tests/*` 与 150+ 行 `!tests/...` 例外清单，后续所有新测试默认受 `test_content_binding_ratchet.py` 保护并自动入库。
2. **沙箱 50 次删除门治本**：
   - 将高 I/O 集成测试（如 `test_chain5_artifact_protection.py`）的只读基线夹具提至 `session` 级共享目录，单进程临时文件增删量控制在 `< 10` 个，实现全仓 `pytest` 单进程一次性 100% 全绿。
3. **章节级生产可观测性看板**：
   - 扩展 `tools/workspace_size_audit.py` 与 `production_context.py`，自动汇总每章的 `logical_calls`、`http_attempts`、各阶段耗时、首次审计过审率、图片重抽率与磁盘占用水位（红线：核心代码库 `< 200 MB`、`.dsh`/`.p1a_exp` 只留最近 2 轮有效档案）。

---

## 4. 分阶段推进路线与里程碑门禁

| 阶段 | 时间窗口 | 核心交付物 | 进入下一阶段前置门禁 |
| :--- | :--- | :--- | :--- |
| **前置（当前在改）** | 当前 | 定位并修正 `step01-frozen-resume-verification-20261116.md §3.1` 的 `content_identity` 真实失配点；补齐冻结稿续验闭环与 `chapter_001` 的 `final.mp4` | 第 1 章 `final.mp4` 交付完成，`§3.1` 归因纠偏入库，工作树干净 |
| **Phase M-A（第 1–2 周）** | 第 2 章启动前 | **M1 核心（`RunContext(read_only=...)` + 拆出 `commit_gate.py`/`repair_loop.py`）** + **M2（`OutboundBudgetGuard` + 修后稿即刻落盘 `resume.py` + 失配诊断）** | ① `read_only=True` 探针 0 字节写入；<br>② 离线桩验证：修后稿在复审被 `cap` 拦下时完整落盘不丢；`_reusable_final_audit` 输出精确失配键 |
| **Phase M-B（第 3–4 周）** | 第 2–3 章试跑期 | **M3（调优 `AGENT_PLAN_TEXT_STREAM_AUTO` + Session 自愈）** + **M4（送审前确定性自愈左移）** | 第 2 章首次审计前确定性 `hard=0`，长连接不再触发 66s 代理断连 |
| **Phase L-A（第 2 月）** | 多章连跑期 | **L1（`plan_feasibility.py` 等扩展 AST 符号闭包提取）** + **L3（`--qc-only` + 自适应 `media_policy` + G6 音画对账）** | 保持 `rule_source_fingerprint` 兼容；改大纲辅助逻辑不废分镜报告；单章图片成本下降 >= 30% |
| **Phase L-B（第 3 月）** | 整书规模化期 | **L2（跨章连续性记忆）** + **L4（迁出 `tests_local/` 后去白名单化 + `session` 夹具 + 看板）** | 全仓 `pytest` 单进程无删除门阻断一次性全绿；支持 10 章以上连续无人值守跑批 |
