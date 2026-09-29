# 修复调用失败可观测 + `max_output_tokens` 截断 2026-09-25

用户 2026-09-25 归因（原文摘要）：

> 真正让提交停下来的直接原因，是第二轮定向修复响应被截断；"零改善"只是外层看到稿件未变化后的
> 通用结论。…**先把修复结果分成至少三种可观测结论：修复有进展、修复无进展、修复调用失败/响应不完整**，
> 并让失败原因进入结构化诊断。再针对本次 `max_output_tokens` 截断做**最小复现和修复验证**；
> **不要把简单提高 token 上限当作已验证的修法**，因为这个上限同时受思维链 token 消耗影响。
> …这比改 BEST-GUARD 更接近本次 BLOCK 的根因。

## 改了什么

| 位置 | 改动 |
|---|---|
| `step01_script_gen/main.py`（提交层修复循环） | `summary["repair_outcome"]` 分**五态**（`improved` / `no_progress` / `call_failed` / `patch_unusable`，另把既有的联动组超限标为 `oversized_group`——四态是 09-25 版，09-26 补 `patch_unusable`）。BLOCK 的 `detail`：有 `call_failed` 记录时写**"修复调用失败／响应不完整（未得到完整结果，不是「修复无进展」）：<原因>"**；有 `patch_unusable` 记录时写**"修复响应不可用（拿到了完整响应、但补丁不可用，不是「修复无进展」）：<reason>"**；两者都没有才写"提交前修复轮零改善（稿件未变…）"。 |
| `step01_script_gen/main.py`（修复轮） | 新增 `_record_repair_call_usage()` / `_record_repair_call_failure()` / `_record_repair_unusable()`：台账里现在有**四类**记录——`kind="call_ok"`（**成功调用也留 usage**）、`kind="call_failed"`（异常/截断：`truncated` + `error` + `usage`）、`kind="patch_unusable"`（**响应完整但补丁不可用**：`parse_failed` / `invalid_structure` / `empty_patch` / `rejected_by_validation`）、以及原有的修复事务记录。三类调用记录都带 `max_tokens`（本轮请求的上限；`None` = 未收窄＝跟随通道全局值）与 `reasoning_tokens` / `output_tokens`。 |
| `step01_script_gen/main.py`（修复轮输出上限） | **不再硬编码 16000**：新增 `_repair_max_output_tokens()`，默认返回 `None`（= 跟随所在文本通道的全局上限），`REPAIR_MAX_OUTPUT_TOKENS` 只作**显式收窄**用；值非法则 WARN 后回退为不传（宁可放宽，也不因手误把上限变小）。 |

## 为什么（根因链，全部有实测支撑）

1. **直接原因链**：第二轮审计后仍有 8 条 blocking → 定向修复调用 → 22:17:34
   `DeepSeek Responses 响应不完整 (incomplete_details.reason=max_output_tokens)` →
   整份 JSON 丢弃 → 稿件一个字没变 → 提交层比较 revision 相同 → **"零改善"** → BLOCK。
   于是**结构化台账里没有"导致 BLOCK 的直接原因"**，只能翻 `step01.log`
   （`step01_repair_rounds.json` 只记了第一轮事务）。
2. **上限为什么不够**：`max_output_tokens` **包含思维链 token**（provider 注释已写明），
   而修复轮的 16000 是**写死的**，注释理由是"agent_plan 的全局上限是 32768，16k 不触顶"
   ——文本通道切到**原生 DeepSeek**（全局 50000）后该假设不再成立，且 `.env.content`
   对修复轮的推荐值本来就写着 **50000**。
3. **同一根因今天已打过一次**：审计路径 2026-09-25 17:33 也是
   `reason=max_output_tokens`（16000 处截断）→ 当时把审计专用上限抬到 32000 并写进 `.env.content`。
   本次是**同一个契约的另一个调用点**（准则 9：同类缺陷查其他入口）。

## 最小复现与修复验证（真实修复路径，两次调用；`.tmp/repair_truncation_probe.py`）

复现对象：本次被拦的那份稿（`snapshots/script_result_20260924_220539.json`，11 镜 / 73.02s）+
真实审计载荷（`step01_audit_rounds.json` 最后一条，phase=commit_round round=2）。只改
`REPAIR_MAX_OUTPUT_TOKENS`，其余走真实代码路径；**不写任何产物**。

| 上限 | 结果 | 用量 |
|---|---|---|
| 16000（生产现值） | 调用成功（`accepted=True resolved=11`） | `output_tokens=8172`（其中思维链 3695）｜**余量 ~7.8k = 16000−8172** |
| 32768 | 调用成功 | `reasoning_tokens=2380｜output_tokens=7553｜余量 ~25k` |

**余量口径（09-26 复核更正）**：`output_tokens`（= `completion_tokens`）**已经包含**思维链 token，`reasoning_tokens` 只是它内部的明细（DeepSeek 官方列在 `output_tokens_details` 下）——所以余量 = `上限 − output_tokens`，**不能**再减一次 `reasoning_tokens`（那会把思维链扣两遍）。两个数都留在台账里：`output_tokens` 算余量，`reasoning_tokens` 看思维链占比。

⇒ **不是结构性不足**（同一请求同一 16000 回放能成功），而是**余量在临界**：典型调用 ~8k，
思维链一长就越过 16k（那一次实测越过了）。所以修法不是"把 16000 随便抬大"，而是
**取消与真实通道无关的写死值**，让它跟随通道全局（deepseek 50000 / agent_plan 32768，
两者都 ≥ 实测需求），并把"够不够"变成台账里的数字（`call_ok` 的 usage）。
**残留风险**：思维链病态拉长仍可能越过全局上限——但那时台账会明确写
`kind="call_failed" + truncated + reasoning_tokens`，提交层也会报成"调用失败"而不是"零改善"。

## 影响面

- **BLOCK 归因变准**：调用失败不再被报成"修复无进展"（`step01_commit.json` 的 `detail` 可辨）。
- **修复轮输出上限**：默认不再收窄（deepseek 50000 / agent_plan 32768）；
  想收窄仍可设 `REPAIR_MAX_OUTPUT_TOKENS`。上限只在模型"想说更多"时生效，不影响正常调用。
  ⚠️ **适用范围（09-26 复核）**：只有 **DeepSeek** 与 **AgentPlan** 两条通道有项目级回退（`req.max_tokens or config.DEEPSEEK_MAX_TOKENS` / `AGENT_PLAN_TEXT_MAX_TOKENS`）；**DashScope / Agnes 把 `req.max_tokens` 原样透传**，`None` 不会回退到项目上限（交给服务端默认）。把文本通道切到这两条时，"默认跟随通道全局值"不成立。
- **台账形状变了**（新增两类记录）：按位置取"第 0 条 = 事务记录"的**消费方会读到 call_ok**。
  已核对无生产消费方按位置解析（只有测试与 `tools` 侧 dump 工具读），4 个按位置取数的用例已改为按字段取。
- **未做**：不改 BEST-GUARD 选稿策略（用户明确否掉：新稿风险更高、且没走最终审计，候选风险分
  不预测最终审计结果）；不加修复轮/重试（容量决定，须用户批准）；不改修复轮的 `effort`；
  不允许"截断响应部分应用"（与"禁止半条修复"的既有决定冲突）。

## 实跑测试数字与回退

- `pytest -q` → **2343 passed, 1 skipped**（09-26 收口后又跑了全量）。
  新增用例：`test_repair_call_failure_is_not_reported_as_no_progress`（含"台账里必须有 call_failed +
  usage"）、`test_record_repair_call_failure_keeps_usage_and_caps_the_error`、
  `test_record_repair_call_usage_enables_the_cap_decision`、
  `test_repair_cap_defaults_to_the_channels_global_not_a_hardcoded_16k`；
  改写：`test_repair_output_token_budget_matches_the_batch`（钉"默认跟随通道全局值且够用"）、
  `test_linked_group_capacity.py` 4 条（按字段取事务记录）。
- 回退：删三个新助手与两处调用、把 `max_tokens` 还原为写死的 16000、还原提交层的 if/else 与
  `repair_outcome`、还原上述用例。

## 备注（交付）

本工作包与"计划边界"是**两件事**：`step01_script_gen/main.py` 同时含两块改动，若提交时要严格分组，
需要按 hunk 拆分暂存（可做，等用户确认）。本工作包的用例落在 `test_repair_targets.py` /
`test_step01_commit.py` / `test_repair_shot_replacements.py` / `test_linked_group_capacity.py`
——这几个文件目前**不在 `.gitignore` 例外内**（未随仓库交付）。

## 收口补正（2026-09-26 用户复核）

复核结论：**修复方向成立，截断的真实触发链已补上结构化归因**；并指出两处边界未收口。
本轮据此收口，未改任何判据/容量。

### 1. token 余量的注释把思维链重复计算了

原注释写"要看的是 `reasoning_tokens + output_tokens` 离上限有多远"——**错**：
`output_tokens`（= `completion_tokens`）**已包含**思维链 token，`reasoning_tokens` 是它内部的
明细（DeepSeek 官方列在 `output_tokens_details` 下，见
<https://api-docs.deepseek.com/api/create-response/>）。两个数相加等于把思维链扣两遍
（本次读数会从 7828 变成 4133）。已改：

- `_record_repair_call_usage` / `_record_repair_call_failure` 的说明改成"余量只看 `output_tokens`"，
  台账仍**同时**保留两个数（`output_tokens` 算余量，`reasoning_tokens` 看占比）；
- 新增 `_repair_cap_margin(record)` 把这条口径变成可执行代码（`max_tokens − output_tokens`，
  兼容 `completion_tokens` 别名，数不全返回 `None`），并在每次成功调用后打一行
  `[REPAIR] 输出用量 output_tokens=…（其中思维链 …）｜本轮上限 …｜余量 …`；
- 调用记录新增 `max_tokens` 字段（`None` = 未收窄），否则台账里没有上限、余量算不出来；
  上限在修复轮里只取一次（`_repair_cap`），请求与记录同值。

### 2. "调用成功但补丁不可用"此前落进了 `no_progress`

四条早退路径（**JSON 解析失败 / envelope 结构无效 / 没返回任何替换 / 替换全被校验拒**）
只打日志，提交层看不到记录 → 与"补丁有效但零改善"共用 `no_progress`，把"收到不可用响应"
说成了"修复没有进展"。已改：

- 四条路径各落一条 `kind="patch_unusable"` + `reason`（`parse_failed` / `invalid_structure` /
  `empty_patch` / `rejected_by_validation`）+ 被拒原因样本（`detail`）；
- 提交层新增第四态 `patch_unusable`，`detail` 写"修复响应不可用（拿到了完整响应、但补丁不可用，
  不是「修复无进展」）：<reason>｜<detail>"；两类记录同时存在时按 `call_failed` 报
  （"根本没拿到完整响应"更靠前）；
- `no_progress` 的含义**收窄为"稿件没有改动"**（且台账里没有调用失败/不可用补丁记录），
  能取到上一轮事务时把它的 `accepted/resolved/reason` 一并写进 `detail`，
  以便分辨"改完被回滚"与"根本没动"。

### 3. 余量口径与适用范围的实测/核对

- 四条分支用**真实 `_repair_shot_warnings` 路径**（只打桩 LLM，桩返回 `success=True` + 不可用文本）
  逐条验证落台账；补丁正常应用时断言**不得**留下 `patch_unusable`（新文件
  `tests/step01_script_gen/test_repair_call_outcome.py`）。
- 提交层两态用 `_commit_step01_outputs` 端到端验证（`test_step01_commit.py` 新增两条：
  `patch_unusable` 独立成态 + `call_failed` 优先）。
- 上限默认不传、`REPAIR_MAX_OUTPUT_TOKENS` 只作显式收窄、且请求与台账同值：由
  `test_repair_cap_defaults_to_none_and_env_only_narrows` 与改写后的
  `test_repair_output_token_budget_matches_the_batch`（钉"只取一次"）覆盖。

### 4. 修复上限"只用于收窄"此前没有校验（09-26 复核第 2 轮）

原实现对 `REPAIR_MAX_OUTPUT_TOKENS` **接受任何整数**：`0` / 负数不是合法上限，
高于当前通道项目上限的值会**放宽**上限——与"只用于收窄"的说明矛盾。已改（`_repair_max_output_tokens`）：

- 非整数（原有）/ `<= 0` / 高于通道项目上限（新增 `_channel_text_max_tokens()` 解析当前通道）
  一律 WARN 后当作未设置 ⇒ 跟随通道全局值；
- 通道无项目级上限（DashScope / Agnes 原样透传）时没有可比较基准，显式收窄值原样生效。

当前配置未设该变量、通道为 DeepSeek（上限 50000）⇒ **默认路径不受这三条分支影响**。

### 5. 修复服务初始化失败此前不进归因（09-26 复核第 2 轮）

`service = create_service()` 原先在 `try` **之外**：工厂因配置/依赖抛异常时既不会被记成
`call_failed`，也进不了提交层归因（只会以未捕获异常收场）。已把初始化纳入同一条失败记录路径
（`error="服务初始化失败：<原始异常>"`，带本轮 `max_tokens`），并先取 `_repair_cap` 再初始化，
使该记录也能带出上限。

### 实跑数字

- `pytest -q` → **2345 passed, 1 skipped**（较上轮 +11：新文件 9 条 + 提交层 2 条；
  另有 2 条既有用例按新契约改写）。其中新增
  `test_repair_cap_rejects_non_positive_and_widening_values`、
  `test_service_init_failure_is_recorded_as_call_failed`。
- **未做**（同上轮，未变）：不改 BEST-GUARD 选稿策略、不加修复轮/重试、不改修复轮 `effort`、
  不允许"截断响应部分应用"。
