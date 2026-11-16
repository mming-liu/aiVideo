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
| 新增用例 | **28 passed**（M2.2/M2.3 共 16 + M2.1 共 12） |
| `tests/shared` + `tests/step01_script_gen` | **6066 passed, 295 skipped, 4 xfailed, 0 failed** |
| `tests/step01_script_gen`（M2.1 落地前，M2.2/M2.3 阶段读数） | 4549 passed, 294 skipped, 4 xfailed |
| 既有复用/身份/事务回归（`test_audit_content_identity` / `test_duration_band` / `test_replay_transactions`） | 27 passed |

## 3. 未完成 / 后续

1. **`step01_resume.json` 指针化**：需要 `resume` 接受「快照 revision ≠ 审计
   revision + `requires_fresh_audit`」这一合法状态（当前 `build_manifest` 会拒）。
2. **成对额度预检**（计划 M2 第 2 项后半）：进入修复轮前要求剩余额度覆盖
   「1 次修复 + 1 次修后复审」，否则直接 `budget_exhausted` 不发起孤儿调用。
   M2.1 已提供 `OutboundGuard.remaining_logical()` / `remaining_attempts()` 与
   模块级 `remaining_logical()`；**预检本身尚未接线**（下一步）。
3. **CP1–CP4 检查点**（计划 M2 第 3 项前半）：本批只落了 CP4 的"修复轮"部分。
4. **`content_identity` 真实失配点**：仍未定案（见
   [step01-frozen-resume-verification-20261116.md](step01-frozen-resume-verification-20261116.md)
   §3.1 更正）。现有可疑项只剩 `audit_focus` 的内容——它由当轮确定性质量门派生，
   而质量门阻断清单在两次运行之间确实变了。诊断已就位，下次复现可直接读键。
