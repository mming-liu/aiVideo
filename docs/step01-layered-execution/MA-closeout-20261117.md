# M-A 收尾交付（2026-11-17）：双层成对预检 / 恢复指针 / 身份失配定案

对应 [中长期计划](../aivideo_medium_long_term_plan.md) M2 与用户 2026-11-17 复核意见。
**零模型调用**（离线桩 + 单测）。

## 1. M-A1 双层成对额度预检

用户复核指出的缺口：`--max-sends 1` 只配了 **HTTP 尝试**上限，逻辑层没有上限，
旧预检只看逻辑余额 ⇒ `short=False` 放行，直到修复请求撞到发送守卫才被拦。

`_paired_repair_budget()` 现在两层都查：

| 层 | 读数 | 判据 |
|---|---|---|
| 逻辑调用 | `remaining_logical(repair/audit/全局)` | 各 ≥1，且全局 ≥2 |
| 网络尝试 | `remaining_attempts(repair/audit/全局)` | 各 ≥1，且全局 ≥2 |

- `shared/outbound_guard.remaining_attempts(phase)` 新增：取「阶段上限余额」与
  「全局余额」的**较小值**（两层都在发前判定，必须按较紧的算）。
- `short_on` 明确列出不足项（`logical_repair` / `logical_audit` / `logical_total` /
  `attempts_repair` / `attempts_audit` / `attempts_total`），提交摘要逐项打印。
- 离线对照（用户点名）：**剩余 HTTP 尝试 0 / 1 / 2 → short=True / True / False**。

## 2. M-A2 修后稿恢复指针

`_persist_repair_round()` 现在除了 `snapshots/repair_round_{k}.json` 与指针，
还写**规范恢复清单** `step01_resume.json`，含三块：

| 块 | 内容 |
|---|---|
| `audited` | 已审稿快照路径/摘要/revision + 审计路径/revision/status |
| `pending_candidate` | 修后稿路径/摘要/revision/round + `requires_fresh_audit=true` + `status=pending_reaudit` |
| `next_action` | `reaudit_pending_candidate` |

- **保留硬约束**：`build_pending_manifest` 仍要求「已审快照 revision == 审计
  input_revision」，不符直接 `ResumeError`——审计只证明同 revision 稿件。
- `resume.load_pending_script()` 从**实际入口**读回待复审稿，校验顺序：
  文件存在 → 摘要相符 → 修订点相符 → **必须与已审稿不同**（同 revision 直接拒，
  否则等于拿旧结论盖章）。
- `resume.run()` 见到 `pending_candidate` 时：以修后稿为本次提交对象，
  **跳过"审计可复用"闸门**（设计使然，不是异常），打印
  `PENDING_CANDIDATE_REAUDIT` 与 `next_action`。
- 若找不到「已审快照 / 审计 / 原文」之一，清单不写并把原因记进指针
  （`resume_manifest.written=false` + `reason`），不伪造。

## 3. 采纳稿落盘失败 → 阻断后续调用

用户复核第 3 条：辅助检查点可以"写失败只告警"，**已采纳修后稿不行**。
`_commit_step01_outputs` 现在在落盘后立刻检查：`written=false` ⇒
`repair_outcome=repair_snapshot_failed`、终局 BLOCK、**不发起修后复审**
（继续只会产出一份无法恢复的结论）。

## 4. M-A3：19:00 `content_identity` 失配定案（可证部分）

### 4.1 已排除项（逐项与盘上审计核对，全部相同）

| 项 | 读数 |
|---|---|
| 原文摘要 | `aba1adc2…` 相同 |
| 计划摘要 | `3f4b1aab…` 相同（cache / fixture / run_log 三处一致） |
| 稿件 revision | `e8f63762…` 相同 |
| 规则源码指纹 | `8050857f18c1877f` 相同 |
| 提示源码指纹 | `e57bdcf27c4b80e4` 相同 |
| 审计提示词 / focus 版本 | `857e4768…` / `066a87e6…` 相同 |
| scope / 时长带 / 通道 | 相同 |

另：确定性修复层**幂等**且不改变 `compute_script_revision`、`_final_audit_source_identity`
与 `content_identity`（前一轮已证）；本轮再次确认簿记键（`_merge_candidate`/
`_run_metadata`）不影响 prompt 长度。

### 4.2 首次变化位置（定量）

用冻结物重建 00:38 那次审计请求，与台账记录的 `prompt_chars=103120` /
`prompt_sha256=cbe16210…` 对比：

| 变体 | 长度 | 与记录差 |
|---|---|---|
| 质量门派生 focus（6 项） | 102,077 | **−1,043** |
| `focus=None` | 101,894 | −1,226 |
| `focus=[]` | 101,740 | −1,380 |

请求分段：metadata 494 + focus 345 + context **94,741** + rules 6,474。
⇒ **差的 1,043 字符落在 `context` 段**（`novel + plan + script` 的渲染），
即 `revision` 白名单与 `plan_digest` 都覆盖不到的部分。

### 4.3 结论与残余

- **结论**：19:00 的失配不是清单/指纹/元数据/计划摘要/原文摘要/稿件 revision 任何
  一项造成的（全部逐字相同），而是**审计当时的请求正文比可重建的正文长 1,043 字符**；
  位置在 context 段，**但无法再定位到具体字段**——因为当时没有归档请求体
  （`step01_call_bodies/` 只存响应）。
- **机制修复**：`shared.call_ledger` 现在对 `script_audit` 调用额外归档
  `<call_id>.prompt.txt`，台账记 `prompt_sha256` / `prompt_bytes` / `prompt_path`。
  下次失配可直接 diff 出第一处不同字符，不必再量长度。
- **残余假设**（未验证，不作为结论）：context 段比冻结物长，最可能是审计时的
  `plan`/`script` 带有运行期派生字段（例如 `timeline`/`meta`/`temporal_plan` 一类
  不参与 revision 的字段）而落盘快照不含。

## 5. 验收

| 项 | 用例 | 结果 |
|---|---|---|
| 两层任一不足都在修复前拦下 | `test_paired_budget_precheck.py`（9 条，含 0/1/2 三点对照） | 通过 |
| 双块清单 + 从实际入口读回待复审稿 | `test_resume_pending.py`（8 条） | 通过 |
| 同 revision 待复审稿被拒（审计只证同稿） | `…::test_same_revision_pending_is_rejected` / `…::test_build_rejects_same_revision_pending` | 通过 |
| 审计请求体归档（其它 schema 不归档） | `test_audit_prompt_archive.py`（2 条） | 通过 |
| 采纳稿落盘失败阻断 | 既有 `test_repair_round_persist.py` + 提交循环分支 | 通过 |

回归：`tests/shared` + `tests/step01_script_gen` + `tests/tools/test_studio_server.py`
= **6110 passed, 295 skipped, 4 xfailed, 0 failed**。

## 6. 计划文档同步修正

[docs/aivideo_medium_long_term_plan.md](../aivideo_medium_long_term_plan.md) 已按复核意见更新：

- **顺序**：M-A 前移并与首章交付并行（不再是"成片之后才启动"）；
- **M3**：流式不保证消除首字节超时；429 不重建连接池；连接层/限流/5xx 分开定义；
- **L1**：`ast.dump` 不剥 docstring；指纹须覆盖依赖函数、配置、schema 与提示内容；
- **M4**：预审门保留确定性问题合法修复通路，分"已修/交修复轮/不可判"三态；
- **L2**：跨章继承须处理时间跳跃、回忆与明确状态变化（继承到有依据的变化为止）；
- **L3/L4**：30%–50%、<10 文件、只留两轮 → 改为待测量/验收门槛/明确保留策略。

## 7. 未完成（下一批）

- **X5-03 预检面板**：`/api/plan` 已能返回逐镜 `ready/blocked`，UI 展示未做。
- **V01 全章零调用视频预检**：`--plan-only --candidate-root … --all-shots` 未执行。
- **T01 声音核对与准备**：未开始。
- **M-A2 端到端**：`resume --dry-run` 走 `pending_candidate` 的真机验收未做
  （单测已覆盖清单构建与读取，未覆盖 `run()` 的完整分支）。
