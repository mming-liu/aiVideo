# 第二次重跑（09-26 00:58）+ final audit `parse_failed` 归因收口 2026-09-26

## 一、这次重跑干了什么（用户授权：`可以按当前配置重跑 Step01`）

**配置**：`TEXT_PROVIDER=deepseek`、`AUDIT_TEXT_PROVIDER=deepseek`、`DEEPSEEK_MAX_TOKENS=50000`、
`AUDIT_MAX_OUTPUT_TOKENS=32000`、`REPAIR_MAX_OUTPUT_TOKENS` **未设置**（默认路径）。
运行 00:58:34 → 01:02:14（3 分 40 秒），退出码 0 = **BLOCK 正常收尾**。

| 阶段 | 读数 |
|---|---|
| 计划门 | 复用大纲缓存；`[PLAN] 对白 5 句/估时 27.55s（逐字核验剔除 0 句）；节拍 6 个/下限 18.00s；下界 27.55s；预算 60–90s`；`生成执行契约 1061 字符（选用台词 5 句）` |
| **新增的可观测性首次生效** | `[PLAN] 注意：必保留内容下界 27.55s 低于下限 60s——下界只说明「至少这么多」，**尚不能据下界判断**…`（上一轮补的可行路径日志，首次在真实运行里出现） |
| 新稿（完整生成路径**确实走过**） | `[CANDIDATE] 候选 1/1: 高潮3镜, 高潮完整, 对白5/16=31%, 失锚0条, 时长81s, 镜数16 → 95分…风险23`；finalize 后 `新终稿 95.0 分/风险18`，留档 `snapshots/candidate_pre_bestguard_20260926_010114.json`（rev `e23cd04a7502`，16 镜/81.00s） |
| 契约服从（**第 2 个真实采样**） | 计划 5 句 → 生成稿 **5/5 命中、0 句自选、0 句遗漏**（`公…公子他…杀…出人命啦…` 仍是合规替换 `杀人`→`出人命`，按同源映射还原后命中） |
| BEST-GUARD 实际选稿 | 新终稿 95.0/风险18 **<** 历史快照最优 100.0/风险17 ⇒ **又选中历史快照** `script_result_20260924_221412.json`（rev `d8800468ac08`，11 镜/73.02s） |
| 提交结果 | **BLOCK**：`final semantic audit parse_failed: audit output is not valid JSON or lacks issues[]`（提交入口 rev `45cbafbb24c0` → 修后 `2c570e2c11ac`）；三份正式产物**未替换**（仍是 09-24 08:11），欠账台账登记三个身份 |

⇒ 这次 BLOCK 的**直接原因既不是计划门、也不是修复轮、更不是新稿的契约服从**，而是
**被选中的那份历史快照在最终审计上解析失败**。新稿因此从头到尾没进过提交门
（BEST-GUARD 边界），也就没人核对它的计划履约——这正是用户先前指出的"最终提交门
没有明确的计划履约核对规则"。

## 二、根因调查：`parse_failed` 是**不确定**的，且失败原因没被留档

### 2.1 已有证据（台账）

`step01_audit_rounds.json` 本轮记录：`status=parse_failed`、`error='audit output is not valid
JSON or lacks issues[]'`、`issue_count=0`、`schema_deviations=[]`、
`usage={'input_tokens': 18438, 'output_tokens': 11271, 'reasoning_tokens': 9009, 'total_tokens': 29709}`。

- **不是截断**：`status` 是 `parse_failed` 而非 `request_failed`，说明 provider 认为响应完整
  （截断会走 `success=False`），且 output 11271 远低于审计上限 32000。
- 解析器（`audit.py:_parse_issues_with_status`）把三种情况都归为 `parse_failed`：
  ① 整段不是 JSON；② 是 JSON 但取不到 `issues` 列表；③ 是列表但**条目形状**不符
  （v6.114 的 required 字段判据）。台账里**看不出是哪一种**。

### 2.2 最小复现（`.tmp/audit_parse_failed_probe.py`，1 次审计调用）

同一份稿（`snapshots/script_result_20260926_010214.json`，rev `2c570e2c11ac`）+ 同一 plan
（稿自带 `chapter_outline`）+ 生产同一个 `focus`（`_final_audit_focus(script, quality)`，
quality 由**纯计算**的 `_build_step01_quality_gate` 得到，不落盘）：

| 项 | 结果 |
|---|---|
| status | **ok**（未复现），**17 条 issue** |
| usage | `input 18435 / output 10651 / reasoning 8435 / total 29086` |

⇒ **同一输入能成功**，与上一轮修复截断的形态一致：不是确定性的结构性缺陷，
单次采样不能定性。**注意这不能推出"下次就没事"**——上一轮已吃过这个教训。

### 2.3 诊断缺口（本次要收口的东西）

`AuditResult.raw_text` **本来带着模型原文**，但 `_run_final_semantic_audit()` 构造 payload 时
把它丢了，台账只留一句错误文案。后果：**"根本不是 JSON"（要改通道/提示词）与
"是 JSON 但形状不符"（要改 shape 判据）这两种处置完全不同的故障，事后无从区分**——
这正是"修复调用失败"那一轮用户要求补上的同一类缺口，只是这次发生在审计侧。

## 三、本轮改动（仅诊断，不动任何判据）

| 位置 | 改动 |
|---|---|
| `step01_script_gen/main.py`（`_run_final_semantic_audit`） | **非 `ok` 时**payload 追加 `raw_text_head`（封顶 `_DIAG_RAW_HEAD_MAX=2000` 字符）与 `raw_text_len`（**完整**长度）。成功轮**不加**这两个键 ⇒ 正式产物形状不变。 |
| `step01_script_gen/main.py`（`_record_audit_diag`） | 台账原样带上这两个字段（有才带）。 |
| `step01_script_gen/main.py`（`_record_repair_unusable`） | 新增 `raw_head` 形参并在 `parse_failed` / `invalid_structure` / `empty_patch` 三处传入响应原文 ⇒ 修复侧与审计侧同一标准。`rejected_by_validation` 不带（它的 `detail` 已是可执行的被拒原因）。 |

## 四、验证

- 新增判别性用例：`test_parse_failed_audit_keeps_the_raw_response_head`（失败轮带头部+总长、
  头部封顶、总长是完整长度；**成功轮不得新增字段**）、
  `test_ledger_carries_raw_head_only_when_the_audit_failed`；修复侧在既有
  `test_parse_failed_patch_is_recorded_as_unusable` 里补 `raw_text_head` / `raw_text_len` 断言。
- `pytest -q` → **2347 passed, 1 skipped**。
- **未实跑验证**：下一次真实 `parse_failed` 时台账里真的出现这两个字段（需再次发生才可核对）。

## 五、仍未解决 / 待用户决定

- **`parse_failed` 为何发生**：本次只证明"不确定"，没定性。下次复现时靠新增的 `raw_text_head`
  一句话就能判（非 JSON / 形状不符）。
- **BEST-GUARD 连续两轮选中历史快照**：新稿（95/风险18）永远进不了提交门，于是"生成是否遵守
  选材"始终只在候选层被验证。用户已明确**不据现有数据改 BEST-GUARD**，本轮亦未改。
- **日志文案**：审计调用打的是"正在调用 DeepSeek Responses … **生成分镜脚本**…"（文案硬编码，
  与生成调用同句），阅读日志时无法区分调用点——本轮排查时确实被它误导过一次。
  仅记录，未改。
- **交付**：`main.py` 已重新暂存（组 1 = 28 项）；本留档与上一份同属"BLOCK 归因"工作包但**是新增文件**，
  按用户"只精确放行四份计划边界留档"的口径**未放行**。

## 六、根因结论（v6.134，2026-09-26 补查；**推断**，非已证明事实）

「`parse_failed` 为何发生」的最佳解释是**采样噪声**（推断），证据链三条：

1. **采样钉不住**：审计虽设 `AUDIT_TEMPERATURE=0.0`，但走 DeepSeek Responses **思考模式**
   （`DEEPSEEK_REASONING_EFFORT=low`），而思考模式下 temperature/top_p 被 API 静默忽略
   （`deepseek_provider.py` 头注释，官方行为）——每次审计都是一次重采样，单次偏离不可避免。
2. **schema 执行是尽力而为**（推断）：官方 create-response 文档（2026-09-26 核对
   api-docs.deepseek.com）里 `text.format` **只有 `type/name/schema` 三个字段，没有 strict
   开关**；`AUDIT_SCHEMA` 里声明的 `"strict": True` 在 provider 组装请求时被丢弃
   （`deepseek_provider._generate_responses` 只读 name/schema）——但即使透传也是未文档化字段，
   不能作为修复手段。注意：文档只说输出「符合所给 schema」，**不能证明实现一定尽力而为**；
   但台账实测 19 轮审计 1 轮输出不合预期（01:02 这次），服务端执行存在实际漏洞率是事实。
3. **提交链把失败当确定性致命错误**：`_commit` 收到 `status != ok` 直接 break → BLOCK
   → 整章欠账，无重试、无抢救——这是**放大因素**，不是根因本身。

排除项：不是截断（output 11271 ≪ 32000，且截断走 `request_failed`）；不是确定性缺陷
（同一输入最小复现 ok，见 §2.2）；同类缺陷的生成路径 11 个 schema 全部声明 `strict: True`
却从未被传出去——其中 4 个（SCRIPT/HOOK/SPLIT_SHOT/REPAIR_SHOT_REPLACEMENT）形状不满足
strict 语义（含 optional 字段），**因此"透传 strict"不可行也不需要**。

## 七、根治实施（v6.134 + v6.134.1 复核修正）

| 位置 | 改动 |
|---|---|
| `audit.py`（`run_same_model_audit_with_status`） | `parse_failed` **有界重试**（`AUDIT_PARSE_RETRIES`，默认 1 次，非法值 WARN 回退）：请求不变、换一次采样；`request_failed` 不重试（截断/超时重试不收敛）；全部失败仍按原 status 返回，**fail-closed 语义不变**。`AuditResult` 新增 `attempts`（真实调用次数）。 |
| `audit.py`（`_parse_issues_with_status`，**v6.134.1**） | **只接受根对象里的 `issues` 数组**——顶层 JSON 数组（含 `[]`）判 `parse_failed`。此前顶层 `[]` 会被当成「零问题通过」= 静默假绿；AUDIT_SCHEMA 要求根对象必含 `issues`，服务端 schema 执行不能当作保障。 |
| `audit.py`（异常收口，**v6.134.1**） | `create_service()`（工厂初始化，含 config.validate / provider 注册）与 `service.generate()` 的漏网异常一律收进 `request_failed`——不再崩出提交链、台账有据。 |
| `audit.py`（失败尝试留档，**v6.134.1**） | `AuditResult.failed_attempts`：被重试覆盖的失败尝试记 {attempt, raw_text_head(≤2000), raw_text_len, usage}——「重试后成功」路径不再丢失坏响应与首次用量（总用量 = 各次之和）。 |
| `audit.py`（`_parse_issues_with_status`） | severity 门 `.lower()`——与 30 行之后的归一化同口径。此前 `"BLOCKING"` 会让整份审计 parse_failed，而归一化层本来就能吃下它；大小写不改语义，非放水（枚举外取值仍 fail-closed）。 |
| `audit.py`（调用前日志） | `[AUDIT] 最终语义审计第 N 次调用（provider=… model=…）`——审计调用自报家门，解决 §五 的日志文案问题（生成调用侧未动）。 |
| `main.py`（`_run_final_semantic_audit`） | payload 在 `attempts != 1` 时带 `attempts`、`failed_attempts` 非空时带上（单次无重试的产物形状不变，同 `raw_text_head` 口径）。 |
| `main.py`（`_record_audit_diag`） | 台账透传 `attempts` / `failed_attempts`（payload 有才带）。 |

预期效果（**理想化估算，非失败率预测**）：若把每次审计视为独立重采样、以台账基率
18/19 为单次成功率，默认重试后单次审计 BLOCK 概率 ≈ (1/19)² ≈ 0.28%——独立性无证据、
样本量不足以支撑该数字作为运行预测，仅用于说明重试的量级收益；实测失败率以台账
`attempts` / `raw_text_head` 的后续观测为准。残留失败会带原始响应头落台账，一眼可判
「非 JSON / 形状不符」。

**验证**：v6.134 + v6.134.1 合计新增 18 条判别用例（重试收敛/耗尽留原文/env 关闭与非法值回退/
request_failed 不重试/工厂与漏网异常收口/根数组 fail-closed/severity 门大小写/失败尝试留档/
产物与台账 attempts、failed_attempts 透传），`pytest -q` → **2365 passed, 1 skipped**（v6.133
基线 2347）。未实跑验证重试在真实 `parse_failed` 上生效——需下一次发生才可核对，
但收敛性由「同一输入曾 ok」直接支撑（§2.2）。
