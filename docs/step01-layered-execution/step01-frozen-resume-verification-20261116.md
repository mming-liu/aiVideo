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

**本节初版归因（2026-11-16 19:05）已被证伪，现更正如下。**

初版写的是「恢复入口重跑确定性修复层，修后稿与首轮审计所依据的冻结稿
content_identity 已不同」。2026-11-16 22:18 的零调用复核（在冻结快照上直接跑
`constraints.engine.run_hard_fix`）给出相反读数：

| 读数 | 修复层前 | 修复层后 |
|---|---|---|
| `compute_script_revision` | `e8f637620605c0d0…` | `e8f637620605c0d0…`（**相同**） |
| `_final_audit_source_identity` | 同左 | **相同** |
| `content_identity`（同 focus、`audit_focus=[]`） | `2d444336…` | `2d444336…`（**相同**） |

且该层**幂等**：第二遍零改动；第一遍的 4 处差异全部是 `_merge_candidate` /
`_run_metadata` 这两个下划线簿记键（落盘时被 `_strip_runtime_fields` 剥掉），
不进入 prompt 字节。所以「确定性层改了内容身份」不成立。

**仍未定位的部分（如实记账）**：盘上审计的 `content_identity=1af6773e…` 与本次
重算值（含 `audit_focus` 为 `73f08ff3…`、不含为 `2d444336…`）都不相等，而
原文摘要、计划摘要（`3f4b1aab…`）、`rule_source_fingerprint`（`8050857f…`）、
`prompt_source_fingerprint`（`e57bdcf2…`）、审计提示词版本、focus 版本、时长带、
通道全部逐字相符。剩下的可疑项只有 **`audit_focus` 的内容**——它由当轮确定性
质量门派生，而质量门的阻断清单在 00:38（13 条）与 19:01（10 条）之间确实变了。
这属于 L1「判据代码变 → 身份变」的同一机制，尚需一次带诊断的复现才能定案。

**已落地的补救**：`_reusable_final_audit` 现在对每一项**单独命名**
（`input_revision` / `rule_source_fingerprint` / `duration_band` / `audit_scope` /
`audit_prompt_version` / `audit_focus_version` / `channel_provider` /
`channel_model` / `content_identity` / `audit_focus`），并把「同稿同契约、只有
定向项不同」从 `content_identity` 里单独分出来；失配读数存进
`main.last_reuse_miss()`，下次复现可直接读出精确键与两边取值，不必再从一句话里猜。

> 结论口径：预检的「2 次调用」前提**并未被证明成立或不成立**——初版给出的否证
> 理由是错的；现在改为「复现时由 `reuse_miss_reason` 给出精确键」。本轮实跑最终
> 用了 3 次调用（终审 + 修复 + 修后终审），这一点不受归因更正影响。

**2026-11-18 定案（零调用复核）**：19:00 试图复用的旧审计来自 00:38
（run `20261116T003059-8920`），其 `input_revision=e8f63762`；19:00 本次稿件是
`64a347a4`（run `20261116T185814-15860`）。**两者不是同一稿**。因此：

- 此前把两次审计请求的 103,120 vs 100,845 字符差当作「同稿 context 多出
  1,043 字符」的前提不成立——差值是不同稿件的正文差，不是运行期派生字段；
- `content_identity` 判定先于 `input_revision`，把真实原因遮成了
  「原文/计划/定向项/契约已变」；已把**具体契约项前移**
  （`input_revision` / 规则指纹 / 时长带 / scope / 提示词版本 / focus 版本 /
  通道），聚合身份只作兜底；
- 回归用例：`tests/step01_script_gen/test_reuse_miss_reason.py`
  `test_revision_mismatch_is_not_masked_by_content_identity`（revision 不同且
  `content_identity` 不同 ⇒ `input_revision`）、
  `test_prompt_version_is_not_masked_by_content_identity`。

00:38 **同稿请求体**与重建值仍有 1,043 字符残差，属另一个问题（请求体归档已上线，
下次同稿复现可直接 diff），不影响 19:00 复用失配的定案。

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

## 6. 追加执行：3 次调用续跑（2026-11-16 22:35–22:40）

用户批准「直接批准 3 次调用重跑」后按预检参数执行（`--max-sends 3`、
`--hook-max-attempts 0`、`--audit-repair-rounds 1`、`--no-hook`、
`STEP01_TOTAL_BUDGET_SEC=1800`、`HOOK_ENABLED=0`、传输重试 0）。命令与原始输出见
`.dsh/step01_frozen_resume_20261116_cap3/`（`console.log`、`exit_code.txt`）。

| 项 | 读数 | 证据 |
|---|---|---|
| 终审调用 1 | 成功，119.1s，2,715 字符 | 台账第 7 行 |
| 定向修复 | 成功，15.8s，4,318 字符 | 台账第 8 行 |
| 修后终审 | 成功，162.4s，3,039 字符 | 台账第 9 行 |
| 计数 | `attempts=3` / `responses_received=3` / `hook_attempts=0` | console |
| 终局 | `BLOCK` / `blocking_count=9` / `audit_status=ok` | `step01_commit.json` |
| 终局原因 | G1：上一轮 1 条 blocking 本轮未再报出且无确定性关闭依据 | manifest `detail` |
| run_id | `20261116T223459-28284` | 同上 |
| 正式产物 | 未替换（`output/…/chapter_001` 仍 `COMMITTED`，blocking=0） | 目录 mtime |
| 冻结稿 | 未污染（SHA `932af1af…` 不变） | `.dsh/step01_budget_frozen/` |

**本次暴露的新缺口（已由 M2.2 修复）**：修后终审**成功**之后，提交点判定 BLOCK，
而 `22:39:59` 只写了 `step01_audit.blocked.json` / `_adjudication` / `_rounds` /
`calls` / `commit` / `log`，**没有新的 `snapshots/script_result_*.json`**——即这一轮
"终审 + 修复 + 修后终审"三次付费调用的成果中，**修后稿本体再次未落盘**。
这与 `19:01` 丢稿是同一机制，已在 M2.2（`_persist_repair_round`）修复。
