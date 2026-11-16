# Step01 冻结稿续验结果（2026-11-16 真实调用）

> 由会话1（session-62894610）门禁放行后执行。执行载体为当前 WorkBuddy 会话
> （dsh 会话为 `standard` preset，无法非交互续跑；用户不在电脑旁，授权等效接管）。
> 严格按预检表上限：预算 1800s / `HOOK_ENABLED=0` / `--max-sends 2` /
> `--hook-max-attempts 0` / `--audit-repair-rounds 1` / 传输重试 0。

## 1. 结论先行

**终局 `BLOCK`，但通道打通了**：本轮真实发出并收到 **2 次 HTTP 响应**
（终审 1 + 定向修复 1），第 3 次（修后终审）在发送边界被 `cap 2` 拦下。
正式三产物未替换，`step01_commit.json` SHA 仍为 `15ea439a…8f6e7`（BLOCK 标记）。
冻结候选未被污染，SHA 仍为 `932af1af…00767`。

与上一次尝试（18:56）不同：那次因预算文件缺失零 HTTP 尝试就 BLOCK；本次是真正的
修复+审计闭环被执行。

## 2. 执行经过（时间线）

| 阶段 | 结果 | 证据 |
|---|---|---|
| dry-run 可装载 | `ok=true` / `audit_reusable=true` / `issues=11` | 冻结身份三 SHA 全吻合 |
| 终审调用 1 | ✅ 成功，服务端等待 **165.976s**（流式 165.928，来源 `agent_plan_trace`） | 台账 `script_audit ok` |
| 首轮 blocking | **10 条**（审计 0 + 确定性质量门 10） | `duplicate_injection`×4(镜2/3/4/5)、`kf_temporal_consistency`(镜11) 等 |
| 定向修复调用 | ✅ 成功，`output_tokens=2397`；shot 11 授权字段实改 5 处 | 台账 `repair_shot_replacements ok` |
| 确定性修复层 | 修 111 处 / 100 条告警 / 13 类约束；HARD 未解决 2（`kf_event_order@shot11`、`frame_spec_consistency@shot7`） | console |
| 修后终审调用 | ❌ 发送前被 `send_budget_exhausted / cap 2` 拦截 | 台账第 6 行 `exception` |
| 终局 | `BLOCK` / `attempts=2` / `responses_received=2` / `reused_audit=false` | console JSON |

## 3. 根因分析

### 3.1 首轮审计为何未复用（`reused_audit=false`）
预检 dry-run 判定 `audit_reusable=true`，但正式 commit 时两次报
`旧审计不可复用：content_identity 不匹配（原文/计划/定向项/契约已变）`。
原因：恢复入口会重跑**确定性修复层**（本轮修 111 处），修后稿与首轮审计所依据的
冻结稿 content_identity 已不同，故首轮审计不可复用 —— 预检表"复用首轮审计、
只做 1 修复 + 1 终审 = 2 调用"的前提在正式流程中不成立，实际需要
"终审 1 + 修复 + 修后终审" = 3 次调用。

### 3.2 预算 cap 2 命中
`--max-sends 2` 是硬上限（用户明令不得放宽）。终审 1 与修复各用 1 次，
修后终审是第 3 次，被发送边界拒绝。这是**预算阻断**，不是稿件内容终审失败 ——
修后稿尚未得到终审结论。

### 3.3 上一轮零尝试 BLOCK 的根因（已修）
`resume.py` 只设置 `STEP01_SEND_BUDGET_FILE` 指向章节目录，未创建文件；
`shared.send_budget.allow()` 读不到即 fail-closed「发送预算文件不可读」。
已补 `_ensure_send_budget_file()`（仅文件不存在时写零预算，dry-run 不触发），
新增 4 条单测（`test_resume_budget_init.py`），全绿。

## 4. 待决策（未自行放宽）

修后终审需要第 3 次逻辑调用，但预检上限是 2。按放行指令"遇任何阻断
（身份/预算/审计复用失效）立即停下汇报，不自行放宽上限或改判据"，本轮停在门禁。
可选路径：
1. **批准追加 1 次调用**（终审 1 + 修复 1 + 修后终审 1 = 3），预算相应调整，继续本轮；
2. **关闭确定性修复层**以保住首轮审计可复用（需改代码 + 回归，属方案变更）；
3. 认定当前 blocking（10 条，多为 `duplicate_injection` 等确定性门）可在下一阶段处理，本轮到此为止。

## 5. 边界

- 真实调用 2 次（成功应答 2 次），通道 `agent_plan` / `deepseek-v4.1-flash`，
  端点 `https://opencode.ai/inference/go/openai/v1/chat/completions`（订阅端点核验通过）。
- 正式产物零替换；冻结稿零污染；历史现场只读；`.dsh/` 隔离留痕。
- 零调用修复（resume 初始化缺口 + 单测）待提交。
