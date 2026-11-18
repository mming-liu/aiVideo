# 离线续干三项验收记录（2026-11-18）

对应指令：M2.3 调度闭环 / M4 先修后审 / M2.4 resume 指针化。
基线 `HEAD=0ba3855`（`fix(s05/s06): 账单拆分就绪待生成与缺帧阻断，Studio 展示前核对账单时效`）。
**全程零真实模型调用**：无 Step 01 文本生成/审计/修复、无 Step 02a 生图、
无 Step 02b 视频生成、无 TTS 合成 POST；只走离线桩、`--dry-run`、单测与
本地临时目录。正式三产物、`step01_commit.json`、accepted 成片均未改动。

## 0. 零调用与边界证据

- 四个新增/扩展测试文件均带 autouse `_zero_call_guard` 夹具：安装
  `shared.outbound_guard.OutboundGuard`（transport 直接抛错），每个用例结束时断言
  `logical_total == 0`、`http_attempts_total == 0`、`responses_total == 0`。
  用例全绿即证明没有逻辑调用、HTTP 尝试或响应。
- 严格预审门用例把 `step01_script_gen.audit.run_same_model_audit_with_status`
  monkeypatch 成直接抛错；`healed` / `blocked` 用例仍通过，证明自愈路径没有
  触碰审计 provider。
- 全局 `tests/conftest.py::_no_external_transport` 对非回环地址的 `connect`
  直接抛错，作为第二道断网护栏。
- 所有章节目录、快照、清单、指针、PAUSED 文件都在 `tmp_path` /
  `.pytest_tmp/` 隔离根内构造；未读写正式章节目录。
- `resume` 只用 `--dry-run`；未执行任何非 dry-run 恢复。
- 未设置/未使用任何新的默认开启开关：`STEP01_PAUSED_CHECKPOINT` 默认关闭；
  `STEP01_PRE_AUDIT_GATE` 仍默认关闭；`STEP01_TOTAL_BUDGET_SEC` 默认仍为 1200s，
  `0` 仍表示不限时。

## 1. T1 / M2.3：超时落 PAUSED + resume 从 checkpoint 续跑

### 1.1 设计

- `shared/step01_checkpoint.py`
  - 新增暂停态命名：`PAUSED_AT_CP1_PLAN_VERIFIED`、`PAUSED_AT_CP2_CANDIDATE_GENERATED`、
    `PAUSED_AT_CP3_INITIAL_AUDIT_DONE`、`PAUSED_AT_CP4_REPAIR_ROUND_K_DONE`
    （与四态同风格；记录里同时写 `checkpoint_stage` 四态名与 `resume_from=CP1..CP4`）。
  - `write_paused()`：原子写 `snapshots/_paused.json`；字段含 `stage`、
    `checkpoint_stage`、`resume_from`、`run_id`、`revision`、`written_at`、`reason`、
    `extra`。写失败只返回 `written=false/error`，不抛。
  - `read_paused()`：缺失 → `None`；存在但损坏/自相矛盾 → `CheckpointError`
    （fail-closed）。
  - 暂停态**不写** `_checkpoints.json`，`latest()` 仍只返回正常四态。
- `step01_script_gen/main.py`
  - 新增 `STEP01_PAUSED_CHECKPOINT`（默认关闭）与 `_record_budget_pause()`：
    取 `latest()` 的最新 CP（CP4 归一为四态），写 PAUSED；失败只告警。
  - 新增 `_budget_exceeded_with_pause(reason)`：未超时零副作用；超时先落 PAUSED，
    再返回与 `_budget_exceeded()` 相同的 `True`。
  - 三处调用点改为 `_budget_exceeded_with_pause(...)`：
    `commit_repair_round`（main.py:4324 附近）、`repair_round`（main.py:8642 附近）、
    `mid_pipeline_audit`（main.py:13925 附近）。`_budget_exceeded()` 与
    `_total_budget_sec()` 的判定/默认值未改。
- `step01_script_gen/resume.py`
  - `run()` 在 `validate_static` 之后、`apply_config` 之前读 PAUSED；
    缺失不改变默认路径，损坏抛 `ResumeError`。
  - 当 PAUSED 存在时，核对 `revision` 必须等于清单已审稿或 pending 候选稿之一；
    不符即拒绝恢复（不按错误身份续跑）。
  - 打印 `RESUME_FROM_CP1..CP4` 可读决策；dry-run JSON 仅在 PAUSED 存在时追加
    `resume_from` / `paused_stage`，默认输出不变。
  - 审计复用/pending 判据仍原样执行，没有因为“停在 CPk”跳过任何身份校验。

### 1.2 验收命令与读数

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/shared/test_step01_checkpoint_paused.py -q `
  --basetemp=.pytest_tmp\run_t1_ckpt -p no:cacheprovider
# 7 passed，EXIT=0
```

```powershell
python -m pytest tests/step01_script_gen/test_resume_from_checkpoint.py -q `
  --basetemp=.pytest_tmp\run_t1_resume -p no:cacheprovider
# 7 passed，EXIT=0
```

覆盖读数：
- PAUSED 写入字段完整；`latest()` 仍返回 CP3（暂停态不污染四态索引）。
- 默认关闭时 `_record_budget_pause()` 返回 `skipped=true`，不产生 `_paused.json`。
- `_budget_exceeded_with_pause()` 未超时零记录、超时记录一次且返回 `True`。
- resume 检测 PAUSED 后输出 `RESUME_FROM_CP4`，并同时输出
  `PENDING_CANDIDATE_REAUDIT`；无 PAUSED 时输出无 `RESUME_FROM_`。
- 损坏 PAUSED、revision 不符 PAUSED 均抛 `ResumeError`（fail-closed）。

## 2. T2 / M4：先修后审——确定性 hard 的局部自愈三态

### 2.1 设计

- `step01_script_gen/commit_gate.py`
  - 新增 `heal_pre_audit_hard(script, *, novel_text="", include_rules=True, runner=None)`：
    纯确定性；只调用**已有** `step01_script_gen.constraints.run_hard_fix()`
    （v6.98 起不调 LLM），对 `pre_audit_gate()` 的 hard 做一次局部修复，
    再重跑同一判据。函数不写盘、不采纳、不改 blocking 判据。
  - 三态返回：`healed` / `needs_llm_repair` / `unresolved`；另保留 `clean`
    表示入口本无 hard。
  - `healed` 返回修复后 `script` 副本；`needs_llm_repair` 仅限
    `LLM_REPAIR_CODES`（缺人/缺帧/重复注入/超单镜上限）；未归类 code 走
    `unresolved`，不猜修法。
  - `duplicate_injection` 列入 `NO_AUTO_HEAL_CODES`：预审门不自动消除它，
    保持 `assert_injection_idempotent()` 的硬拒绝语义。
- `step01_script_gen/main.py`
  - `_run_final_semantic_audit()` 的 strict 分支改为调用三态函数：
    - `healed` → 返回 `status=pre_audit_healed` + `healed_script` 建议，
      不调用审计 provider，不写盘；
    - `needs_llm_repair` / `unresolved` → 保持 `status=blocked`，`error` 仍以
      既有 `pre_audit_hard` 开头，另给 `pre_audit_status` 与可读原因；
    - `clean` → 继续原终审请求。
  - `_commit_step01_outputs()` 显式消费 `pre_audit_healed`：每轮提交最多一次；
    采纳到**本轮内存稿**后重算质量门/定向项/审计身份再送审；不写正式产物。
    自愈后再次要求自愈或自愈稿不可用 → 拒绝循环并保持 blocked。
- 未开启 `STEP01_PRE_AUDIT_GATE` 时入口行为不变（默认关闭）。

### 2.2 验收命令与读数

```powershell
python -m pytest tests/step01_script_gen/test_commit_gate_pre_audit.py -q `
  --basetemp=.pytest_tmp\run_t2 -p no:cacheprovider
# 11 passed，EXIT=0（原 5 条 + 新增 6 条）
```

```powershell
python -m pytest tests/step01_script_gen/test_commit_gate_pre_audit.py `
  tests/step01_script_gen/constraints -q `
  --basetemp=.pytest_tmp\final_constraints -p no:cacheprovider
# 747 passed，EXIT=0
```

覆盖读数：
- `healed`：匿名群体 `layout_presence_gap` 由既有 `group_actor_continuity`
  补回 kf1 条目，hard 归零；原稿未被就地改写；provider 调用计数为 0。
- `needs_llm_repair`：`duplicate_injection` 保持 blocked，hard 不被吞掉；
  `assert_injection_idempotent()` 仍抛 `PreAuditError`。
- `unresolved`：monkeypatch `run_hard_fix` 抛错时返回可读原因，入口保持
  blocked，审计 provider 零调用。
- 默认关闭：`heal_pre_audit_hard` 不被调用，审计桩被调用一次，行为与改造前一致。

## 3. T3 / M2.4：resume 自动消费修复轮指针

### 3.1 设计

- `step01_script_gen/resume.py`
  - `--manifest` 改为可选；省略时从 `--chapter-dir`（缺省当前目录）自动发现
    `snapshots/_repair_round_pointer.json`。
  - `build_auto_pointer_manifest()` 逐项校验：
    - 指针可解析、`written=true`、无写入错误；
    - `requires_fresh_audit=true`；
    - 快照在 `snapshots/` 下、存在、revision 与指针一致；
    - 审计 `status=ok`、`input_revision` 与指针 `audit_revision` 配对；
    - 已审快照按 revision 精确发现；
    - 冻结原文唯一（无提示清单时多于一份即 fail-closed）、冻结计划存在且摘要可核；
    - 待复审稿 revision 与已审稿不同（同 revision 拒绝构造 pending）。
  - 构造的清单复用 `build_pending_manifest()`：`pending_candidate`、
    `requires_fresh_audit=true`、`next_action=reaudit_pending_candidate`；
    后续仍走 `validate_static` / `validate_semantic` / pending 身份校验。
  - `run()` 接受清单路径或 dict；`source="auto_pointer"` 只在自动入口追加
    `discovery` 字段。显式 manifest 路径行为不变。
- 非法/缺失/损坏指针一律 `ResumeError` → CLI 退出码 3，不猜路径、不伪造候选。

### 3.2 验收命令与读数

```powershell
python -m pytest tests/step01_script_gen/test_resume_run_pending.py `
  tests/step01_script_gen/test_resume_auto_pointer.py -q `
  --basetemp=.pytest_tmp\final_t3 -p no:cacheprovider
# 12 passed，EXIT=0
```

覆盖读数：
- 合法指针（带/不带 `resume_manifest` 提示）均自动构造 pending 清单；
  `script_revision` 为已审稿 revision，`pending_candidate.script_revision` 为修后稿。
- `resume.main(["--chapter-dir", ..., "--dry-run"])` 输出
  `AUTO_POINTER_FOUND` + `PENDING_CANDIDATE_REAUDIT`，dry-run JSON 含
  `discovery=auto_pointer`、`next_action=reaudit_pending_candidate`。
- 无 `step01_resume.json` / `step01_commit.json` 写出；审计 provider 零调用。
- 无指针、损坏指针、`written=false`、指针 error、`requires_fresh_audit=false`、
  待复审稿缺失、同 revision、指针快照越出 `snapshots/` 均 fail-closed。

## 4. 合并回归

```powershell
python -m pytest `
  tests/shared/test_step01_checkpoint_paused.py `
  tests/step01_script_gen/test_resume_from_checkpoint.py `
  tests/step01_script_gen/test_commit_gate_pre_audit.py `
  tests/step01_script_gen/test_resume_run_pending.py -q `
  --basetemp=.pytest_tmp\final_combined -p no:cacheprovider
# 26 passed，EXIT=0
```

周边回归（检查点/修复轮落盘/恢复预算）：

```powershell
python -m pytest `
  tests/shared/test_step01_checkpoint.py `
  tests/step01_script_gen/test_repair_round_persist.py `
  tests/step01_script_gen/test_resume_pending.py `
  tests/step01_script_gen/test_resume_budget_init.py `
  tests/step01_script_gen/test_paired_budget_precheck.py -q `
  --basetemp=.pytest_tmp\run_regress -p no:cacheprovider
# 30 passed，EXIT=0
```

## 5. 未改项 / 保留项

- 未改 `_total_budget_sec()` 默认 1200s、未改 `0=不限时`。
- 未改 `pre_audit_gate()` 的 hard 判据、未改 `assert_injection_idempotent()` 的
  `duplicate_injection` 拒绝语义。
- 未改正式门禁 blocking 判据、未改默认开关。
- 未写/未采纳正式产物：`step01_commit.json`、accepted 成片、正式三产物均未触碰。
- 未编辑/未 add/未提交 `docs/dsh-handoff/session-reuse-lock-screen-decision-20261116.md`
  （WorkBuddy 主会话留档）。
- 未触碰 awesome-seedance 及非本任务文件。

## 6. 本任务提交文件

- `shared/step01_checkpoint.py`
- `step01_script_gen/main.py`
- `step01_script_gen/resume.py`
- `step01_script_gen/commit_gate.py`
- `tests/shared/test_step01_checkpoint_paused.py`（新增）
- `tests/step01_script_gen/test_resume_from_checkpoint.py`（新增）
- `tests/step01_script_gen/test_commit_gate_pre_audit.py`（扩展）
- `tests/step01_script_gen/test_resume_auto_pointer.py`（新增）
- `docs/step01-layered-execution/offline-dispatch-verify-20261118.md`（本记录）
