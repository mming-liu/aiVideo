# M2 首批交付：修复轮即刻落盘 + 复用失配诊断 + 统一出站守卫（2026-11-16）

对应计划：[docs/aivideo_medium_long_term_plan.md](../aivideo_medium_long_term_plan.md)
§2 M2 第 2、3 项与 §4 Phase M-A 门禁。**零模型调用**（离线桩 + 单测）。

## 1. 交付内容

### M2.2 修复轮落盘不变量（`step01_script_gen/main.py`）

- 新增 `_persist_repair_round(script, *, round_no, audit, novel_text)`：
  把已采纳的修后稿**原子**写入 `snapshots/repair_round_{k:02d}.json`，读回校验
  `compute_script_revision` 一致后，再原子写指针
  `snapshots/_repair_round_pointer.json`（含 `path` / `revision` / `round` /
  `requires_fresh_audit=true` / 修前审计路径与 revision）。
- 调用点在 `_commit_step01_outputs` 的修复事务里：`_finalize` 与
  `_contract_reject_reason` **两道门都通过之后**、`script = _cand` **之前**。
  于是"修复已花钱且被采纳"与"成果已落盘"是同一个原子步，后续复审无论被发送上限、
  网络还是进程退出打断，采纳稿都不会丢。
- 写失败**不抛**：记 `written=false` + `error`，只告警（快照是保护性副作用，
  不能反过来打断提交）。

**为什么不直接改 `step01_resume.json`**：`resume.build_manifest` 有一条硬不变量
——快照与审计必须同一 revision（`resume.py:127-134`，不符直接抛 `ResumeError`）。
修后稿 revision 已变、盘上审计仍是修前那份，写进正式清单会让 `resume --dry-run`
直接失败。因此本批只落「快照 + 独立指针」，并把"必须重审"写成
`requires_fresh_audit=true` 这一数据字段。让 `resume` 认识该指针属于后续工作。

### M2.3 复用失配精确诊断（`step01_script_gen/main.py`）

- 新增纯函数 `_reuse_miss_key(audit, *, revision, expected_identity,
  expected_source_identity, expected_identity_without_focus) -> str`：
  `""` = 可复用，否则返回**第一个失配项的名字**。
- 新增 `_REUSE_MISS_TEXT`（键 → 人读文案）与 `last_reuse_miss()`
  （最近一次失配的键、来源审计路径、审计 revision、两边取值）。
- `_reusable_final_audit` 改为逐项判定并命名；`_commit_step01_outputs` 传入
  「同稿同契约、`audit_focus=[]`」的身份作为探针，于是
  **仅定向项不同** 会被精确报成 `audit_focus`，不再混进 `content_identity`。

覆盖的失配键：`status_not_ok`、`content_identity_missing`、`content_identity`、
`audit_focus`、`source_identity_missing`、`source_identity`、`input_revision`、
`rule_source_fingerprint`、`duration_band`、`audit_scope`、
`audit_prompt_version`、`audit_focus_version`、`channel_provider`、`channel_model`。

### M2.1 统一出站守卫（`shared/outbound_guard.py`）

新增 `OutboundGuard`，把两层账本放在**同一个发送边界**上：

| 层 | 计数 | 上限 | 阻断时机 |
|---|---|---|---|
| 逻辑调用 | `logical_calls[phase]` | 分类 + 全局 | `begin()` —— 还没构造请求 |
| 网络尝试 | `http_attempts[phase]` | 分类 + 全局 | `__call__` —— **发出字节之前** |

- 阶段口径：`verify / generate / hook / audit / repair / image_gen / vision_qc /
  video_gen / tts / other`；由 `phase_of_schema()` 从请求 schema 名映射
  （`storyboard_script→generate`、`hook_shot→hook`、`script_audit→audit`、
  `repair_shot_replacements→repair`…），未知归 `other`，不靠 URL 或顺序猜。
- 三指标：`snapshot()` 只给 `logical_calls` / `http_attempts` /
  `responses_received`（各自分类明细 + 总计）+ `blocked`，**不再有 `transmitted`**。
- 连接池：`transport` 留空时内部持有一个 `requests.Session`（M3.2 的池化基础）；
  瞬态 SSL/连接/超时错误把会话标记为脏，**下一次发送前**重建，避免继续复用已
  半开的代理隧道。**重试判定仍归 `_text_gen.retry_policy`**——本模块不 sleep、
  不重发，避免"上层叠加重试"（该模块文档明令禁止）。
- 未安装时零行为变化：`install()` 之前 `current()` 为 `None`，
  `begin_for_schema()` 直接返回。

接线：

| 位置 | 改动 |
|---|---|
| `_text_gen/service.py` | 在唯一出口 `_LedgeredService.generate()` 调 `begin_for_schema(schema_name)` —— 一次 `generate()` = 一次逻辑调用，provider 内部重试只增 `http_attempts` |
| `step01_script_gen/resume.py` | `install(guard.guard)` / `finally` 还原；输出补 `http_attempts` 与 `logical_calls` 明细 |
| `tools/run_step01_from_verified_plan.py` | 同上；`run_log.json` 的 `logical_calls` 改为**分类计数**，原台账明细更名 `ledger_calls` |
| `shared/send_cap.py` | 变为兼容外壳：`CallCounter` 委托 `OutboundGuard`，`SendBudgetExceeded` 与 `SendBlocked` **是同一个类**（既有 `except` 照旧命中） |

### M2.2b 成对额度预检（`step01_script_gen/main.py`）

`_paired_repair_budget()` 给出「修复 + 修后复审」这一对的剩余额度读数：

* `remaining_repair` / `remaining_audit`：按阶段剩余；
* `remaining_total`：全局剩余；
* `short`：任一阶段 < 1 **或** 全局 < 2（两层共用一个池时，"各 ≥1" 并不等于
  "能跑两条"）。

提交循环在**发起修复调用之前**查它；`short=True` 时先 `_persist_repair_round()`
保存当前稿，再以 `repair_outcome=budget_exhausted` 停止，**不发起孤儿修复**。
未安装守卫时三项都是 `None`、`short=False`——行为逐字不变。

### M2.3 CP1–CP4 阶段检查点（`shared/step01_checkpoint.py`）

| 检查点 | 落盘位置 | 时机 |
|---|---|---|
| `CP1_PLAN_VERIFIED` | `resume.py`（计划/原文/时长带/通道核验后）、运行器（验收码为空 + 生成前统一门通过后） | 进入生成前 |
| `CP2_CANDIDATE_GENERATED` | `_commit_step01_outputs` 入口（候选已 finalize） | 提交点起点 |
| `CP3_INITIAL_AUDIT_DONE` | 首轮审计 ok 之后（`_attempt == 0`） | 首轮审计完成 |
| `CP4_REPAIR_ROUND_K_DONE` | `_persist_repair_round()` 内（按轮次 `_r{k}` 分文件） | 每轮修复采纳 |

- 文件名固定 `snapshots/cp_<stage>.json`，另写指针索引
  `snapshots/_checkpoints.json`（按 `written_at` 微秒精度取最近，**不靠 mtime 猜**）；
- 写盘后读回校验 `compute_script_revision` 一致；
- **写失败只告警**，不打断提交。

### M3.2 连接池生产接线（`resume.py` / 运行器）

`CallCounter(None, …)` ⇒ 守卫内部持有 `requests.Session` 并负责重建；守卫用
`session.post` 发送，与 `requests.post = guard` 不构成递归。重试判定仍归
`_text_gen/retry_policy.py`。

### M3.1 SSE 覆盖（核对结论：**已具备**）

`_text_gen/agent_plan_provider.py` 的 `_stream_requested()` 在
`AGENT_PLAN_TEXT_STREAM=auto`（默认）下按「prompt ≥ 20,000 字符 或
max_tokens ≥ 32,000」开协议流式。实测五类长调用都过线：分镜生成 45,736 字符、
审计 103,120 字符、钩子 20,854 字符；22:35 的修后终审实测走流式 162.4s。
**不需要新写实现**，本轮只做覆盖核对与记录。

## 2. 验收（Phase M-A 门禁两项）


| 门禁 | 用例 | 结果 |
|---|---|---|
| 修后稿在复审被 cap 拦下时仍完整落盘 | `tests/step01_script_gen/test_repair_round_persist.py::test_snapshot_survives_after_audit_block` | 通过 |
| 同上（落盘内容逐字一致 + 指针 revision 正确） | `…::test_accepted_repair_is_persisted_before_next_audit` | 通过 |
| 落盘失败不打断提交 | `…::test_write_failure_does_not_break_commit` | 通过 |
| `_reusable_final_audit` 给出精确失配键 | `tests/step01_script_gen/test_reuse_miss_reason.py`（13 条，含 `audit_focus` 单独成键） | 通过 |
| 双层上限在发前 fail-closed | `tests/shared/test_outbound_guard.py`（12 条） | 通过 |
| 三指标无 `transmitted` | `…::test_three_metrics_have_no_transmitted_key` | 通过 |
| 瞬态 SSL → 连接池重建（不重试） | `…::test_transient_error_marks_pool_dirty` / `…::test_non_transient_error_keeps_pool` | 通过 |

回归读数：

| 套件 | 读数 |
|---|---|
| 新增用例 | **39 passed**（M2.2/M2.3 16 + M2.1 12 + 检查点 6 + 成对预检 5） |
| `tests/shared` + `tests/step01_script_gen`（全量） | **6077 passed, 295 skipped, 4 xfailed, 0 failed** |
| `tests/step01_script_gen`（M2.1 落地前，M2.2/M2.3 阶段读数） | 4549 passed, 294 skipped, 4 xfailed |
| 既有复用/身份/事务回归（`test_audit_content_identity` / `test_duration_band` / `test_replay_transactions`） | 27 passed |

## 4. 真实单次调用验证（2026-11-16 23:53–23:56）

命令（隔离根 `.dsh/step01_m2_single_call_20261116/`）：

```powershell
$env:STEP01_TOTAL_BUDGET_SEC="1800"; $env:HOOK_ENABLED="0"
$env:TEXT_TRANSPORT_RETRIES="0"; $env:TEXT_TRANSPORT_CONNECT_RETRIES="0"
$env:AGENT_PLAN_TEXT_STREAM="1"
python -m step01_script_gen.resume --manifest <003848 清单> `
  --max-sends 1 --hook-max-attempts 0 --audit-repair-rounds 1 --no-hook `
  --accept-fresh-audit
```

| 项 | 读数 | 证据 |
|---|---|---|
| 真实调用 | **1 次**：`script_audit` ok，**161.8s**，流式（`agent_plan_trace`），输出 23,922 tokens | 台账第 10 行 |
| 守卫阻断 | 修复调用在**发出前**被拦：`网络尝试总量上限阻断：1/1`（台账第 11 行 2ms / 0 字符） | console + 台账 |
| 三指标 | `attempts=1` / `responses_received=1` / `hook_attempts=0` | console |
| CP1 | `snapshots/cp_CP1_PLAN_VERIFIED.json`（151,466 B，23:53:49） | 目录 |
| CP2 | `snapshots/cp_CP2_CANDIDATE_GENERATED.json`（151,418 B，23:53:49） | 目录 |
| CP3 | `snapshots/cp_CP3_INITIAL_AUDIT_DONE.json`（153,614 B，23:56:32） | 目录 |
| 复用失配键 | 预检阶段 `source_identity`；提交点 `content_identity` | 日志 |
| 终局 | `BLOCK` / `send_budget_exhausted` / 1 轮 11 条 blocking（审计 1 + 确定性 10） | console |

**结论**：M2.1 的双层账本、M3.2 的 Session 发送、M2.3 的 CP1–CP3 落盘、以及
「修后复审」的额度语义，在真实链路上一次跑通；阻断发生在**字节发出之前**
（第 11 条台账 2ms、0 字符），符合 fail-closed 设计。

### 4.1 一个被这次运行坐实的机制

预检阶段报 `source_identity` 不匹配、提交点报 `content_identity` 不匹配——两者
同一根因：**`PROMPT_SOURCE_FINGERPRINT` 哈希整个 `main.py`**，本轮为实现 M2 改了
`main.py`，旧审计按契约失效。这不是 bug，而是 L1 要解决的问题（改一行注释/加一个
函数就作废全部审计报告）。本轮的应对是新增 `resume --accept-fresh-audit`：
默认仍 fail-closed，显式加该开关才允许"本轮重新审计"，并把失配键打印出来。

## 5. 提交与合并

- `4c2bd71` M2.2 + M2.3（修复轮落盘 + 复用失配诊断）
- `484f280` M2.1（统一出站守卫）
- `df5d8ac` M2.2b + CP1–CP4 + M3.2 接线
- 推送：`origin/master`（Gitee）与 `github/master` 均 `98d3edb..df5d8ac`。

## 6. 未完成 / 后续

1. **`step01_resume.json` 指针化**：需要 `resume` 接受「快照 revision ≠ 审计
   revision + `requires_fresh_audit`」这一合法状态（当前 `build_manifest` 会拒）。
   在它落地前，`--resume` 仍只认「同 revision 的快照+审计」这一对。
2. **`content_identity` 真实失配点**：**已定案（2026-11-18）**——19:00 复用的
   00:38 审计属于 `e8f63762`，19:00 稿件是 `64a347a4`，首因是 `input_revision`
   不同；`content_identity` 先判的顺序遮蔽了它。已把具体契约项前移并补回归用例，
   详见 [step01-frozen-resume-verification-20261116.md](step01-frozen-resume-verification-20261116.md)
   §3.1 与 `test_reuse_miss_reason.py`。
3. **检查点续跑调度器**：CP1–CP4 已能如实落盘，但"从 `PAUSED_AT_CPk` 自动续跑"
   的调度逻辑（读 `_checkpoints.json` → 决定从哪一步开始）尚未接线；当前由
   `resume` + `--max-sends` 人工控制。
