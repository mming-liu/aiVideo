# Step01 规划层失败语料与基线（2026-11-21）

> 工作包模块：**S01**（Step01 规划层稳定性包）。状态：语料已冻结、登记与归因初稿完成。
> 本文件只读引用现场产物，不重新调用模型、不改判据；冻结副本在 `.tmp/step01_plan_corpus_20261121/`（过程数据，不入库）。
> 下游消费者：S02（离线修订回放）、S07（契约可满足性）、S09（核验员校准）、S11（实跑对照）。

## 1. 范围与口径

- **目标**：冻结可回放的规划层失败语料，建立"可核实运行"登记表与归因表，明确无法确定的分母缺口。
- **什么是"真实运行"**：`python step01_script_gen/main.py`（或等价真实入口）发起真实模型调用的运行。离线回放、干跑、单轮桩实验**不计入真实运行分母**，单独登记。
- **语料身份口径**：以文件哈希 + run_id 冻结，不以 mtime、"最新目录"或"最近成功"选择基线（S02 起只读 `corpus_manifest.json`）。
- **归因类别**：① 请求/分流缺陷；② 模型输出（内容/格式违约）；③ 判据误判；④ 契约挡住合理改编；另登记 ⑤ 调用/协议故障（`call_failed`/`outline_invalid` 等工程类，不属内容四类）。无法判定的写"待验证"。

## 2. 基线

### 2.1 代码与工作树

| 项 | 实际值 | 与计划基线的差异 |
|---|---|---|
| HEAD | `21cc7a7`（branch `refactor/slim-20261119`） | 计划写 `0b53f67` |
| diff `0b53f67..21cc7a7` | 仅 `.gitignore`、`tests/test_py311_source_grammar.py`、`skills/`/`.agents/` 取消跟踪（并行精简包 S01） | **无 Step01 业务代码差异**，规划层行为前提不变 |
| Python | 3.14.2（本机 `python`） | — |

工作树脏文件（**8 项，全部保留、不纳入本包提交**）：

```
 M step02a_frame_gen/verify_acceptance.py
 M tests/step02a_frame_gen/test_verify_only.py
?? baselines/2026-11-12-pre-layered/s06_ab/tools/__pycache__/
?? docs/dsh-handoff/offline_dispatch_20261118.md
?? docs/dsh-handoff/session-reuse-lock-screen-decision-20261116.md
?? docs/dsh-handoff/shot2_lf_redraw_20261121.md
?? docs/step01-layered-execution/R7.2-offline-projection-20261121.md
?? docs/step01-layered-execution/shot2-lf-redraw-20261121.md
```

### 2.2 生效配置（已用 `tools/print_effective_config.py --stage step01` 核对，`required_unscanned=[]`）

| 变量 | 生效值 |
|---|---|
| `TEXT_PROVIDER` | `agent_plan` |
| `AGENT_PLAN_TEXT_MODEL` | `deepseek-v4.1-flash` |
| `AGENT_PLAN_TEXT_BASE_URL` | `https://opencode.ai/inference/go/openai/v1`（订阅端点） |
| `AGENT_PLAN_TEXT_REASONING_EFFORT` | `none` |
| `AUDIT_TEXT_PROVIDER` / `MODEL` | `agent_plan` / `deepseek-v4.1-flash` |
| `REPAIR_TEXT_PROVIDER` / `MODEL` | `agent_plan` / `deepseek-v4.1-flash` |
| `SCRIPT_CANDIDATE_TOPK` / `STEP01_TOTAL_BUDGET_SEC` | `1` / `1200` |
| `HOOK_ENABLED` / `OUTPUT_BASE` | `0` / `./output` |

代码侧基线：呈现模式默认 `legacy_audio`（`step01_script_gen/outline.py:6631`，`MODE_LEGACY`）；修订调用上限 `plan_revision.DEFAULT_MAX_CALLS = 6`（`plan_revision.py:68`）；修订截止 `DEFAULT_REVISION_DEADLINE_SEC = 600.0`（`outline.py:1109`）。

### 2.3 正式基线（必须保护，本包不触达）

| 文件 | sha1 | sha256 | 字节 |
|---|---|---|---|
| `script_result.json` | `17719bd87fe93c4dbe1bfed525358da2177dda14` | `3273672c…` | 176515 |
| `step01_audit.json` | `90b84a6e…` | `c804de98…` | 8230 |
| `step01_quality.json` | `0c8f2718…` | `ef0cc272…` | 48345 |
| `step01_commit.json` | `14b61fdd…` | `b890deb8…` | 1371 |
| `step01_adoption.json` | `c1e797bb…` | `b9685eb6…` | 4406 |

- 正式稿 revision `0afa80749139a0…`，commit manifest run `20261114T201257-26444`，gate_refresh 于 2026-11-21T15:01（`8050857f… → 81a65668…`，script sha1 不变）。
- **口径事实**：adoption 记录的 `source_chapter` 指向 `.tmp/repair6_20261031/run1/…`（当前已不存在），即 11-14 那次 COMMITTED 草稿**不是**当次从头规划产物，而是修复流程复用稿 → 登记为"复用（来源证据缺失，待验证）"。
- R7.2 冻结候选（本包不碰）：`script_r7_2_frozen.json` sha256 `7886b7cf…`、`verify.json` `ff76d990…`、`audit/audit_package.json` `391843fe…`、`audit/binding_digest.json` `eb9b004d…`。R7.1 冻结候选终审 BLOCK（run `20261119T231611-13712`，blocking=1）属另一任务，不计入本包规划层分母。

## 3. 冻结语料包

根目录：`.tmp/step01_plan_corpus_20261121/`（已生成，全部只读复制，不重新生成）。

| 文件 | 内容 |
|---|---|
| `inventory.json` | 61 个产物目录 / 150 个相关文件的 path、size、mtime、sha256，按目录分组 |
| `runs_table.json` | 由 inventory 解析出的 terminal/commit/adoption/metrics/rounds 摘要（登记表原始数据） |
| `sha256_manifest.txt` | 23 个冻结副本的相对路径 + sha256 + 字节数 |
| `corpus_manifest.json` | **S02/S07 唯一输入清单**：回放目标、call_id、期望指纹、回复正文哈希、请求证据哈希 |
| `frozen/20261121T181234-6228/` | 11-21 运行的 `replan_rounds.jsonl`、`plan_contract_invalid.json`、`calls.jsonl`、`metrics.json`、`timing.json`、`log`、9 个 `call_bodies/*.txt`（原始回复正文） |
| `frozen/20261114-formal-evidence/` | 11-14 两次失败运行的 `plan_contract_invalid.json`、`plan_infeasible.json`、`replan_rounds.jsonl`、两份 `step01_run_*.log`、`calls.jsonl`、`metrics/timing` |

回放目标（`corpus_manifest.json` 固定）：

| round | kind 期望 | plan_in 指纹 | 期望 out 指纹 | 模型回复 call_id | 回复正文 sha256 | 请求字符 | 本轮 codes_in |
|---|---|---|---|---|---|---|---|
| 2 | `applied` | `76231a28…` | `b1653b43…` | `c4b690124ba944b0e` | `7e1a2a98…` | 37652 | 20（13×`orphan_uncarried`、`carrier_fact_narration_mismatch`、`verify_record_missing`、`budget_infeasible`、3×`budget_unrepresentable`） |
| 4 | `no_change` | `ecc6b6e4…` | —（不变） | `c15c11ca0db6341e2` | `4d559e56…` | 36249 | 3（2×`verify_fact_unsupported`、`verify_plan_coherence`） |

S07 目标计划：round 4 的 `plan_in`，指纹 `ecc6b6e4647230d21d7a1017fbddf6a7116fc73f`。
11-21 终局三问题：`verify_fact_unsupported`(f03)、`verify_fact_unsupported`(f06)、`verify_plan_coherence`(b06)。

冻结纪律：S02/S07 只按 `corpus_manifest.json` 读取；副本缺失、哈希不符或不可读时直接失败，不按 mtime 找"最新"、不补造材料、不自动触发模型调用。

## 4. 运行登记表

### 4.1 正式/生产根真实运行（`output/锦衣夜行/…` 与 `output/video/锦衣夜行/…`）

| run_id | 根 | 终局 | 关键 codes / 读数 | 续跑轮次 | 从头/复用 |
|---|---|---|---|---|---|
| `20261004T035906-30072` | output/video ch002 | COMMITTED | rev `466f3337…`，blocking=0 | — | 待验证 |
| `20261017T131353-23764` | output/video ch001 | 提交门 BLOCK | blocking=8，rev `0d245e53…` | — | 待验证（非规划层） |
| `20261018T131545-7108` | output/video ch001 | 计划层 `illegal_response` | 连续 3 次响应被拒收（末次 `view_voice_unit_unplaced` d01/n02） | 待验证 | 待验证 |
| `20261018T132103-25700` | output/video ch001 | 计划层 `plan_contract_invalid` | 9×fact partial（f02/f04/f06/f07/f08/f09/f10/f11/f12）+ D1/N5 超单镜 12s（无合法切分） | 待验证 | 待验证 |
| `20261031T142106-10528` | 正式章备份链 | COMMITTED | rev `b5ec17f6…`，blocking=0 | — | 待验证（基线） |
| `20261031T170331-14108` | output/video ch002 | 计划层 `plan_contract_invalid` | 1×`verify_fact_unsupported`（f05 partial） | 待验证 | 待验证 |
| `20261031T171111-2496` | output/video ch002 | 计划层 `illegal_response` | `delete.dialogue_units` 与 `upsert` 同 uid（自相矛盾） | 待验证 | 待验证 |
| `20261107T225039-18832` | 正式章备份链 | 提交门 BLOCK | blocking=4 | — | 待验证 |
| `20261107T232711-8676` | 正式章备份链 | COMMITTED | rev `58c1b6bd…` | — | 待验证 |
| `20261109T075259-24828` | 正式章备份链 | COMMITTED | rev `51978e8b…`，adopted 11-09 | — | 待验证 |
| `20261114T145644-22104` | 正式章 | 计划层 `plan_contract_invalid` | 2×`carrier_fact_narration_mismatch`、2×`carrier_fact_line_mismatch`、3×`budget_unrepresentable`、1×`verify_bad_response` | 待验证 | 待验证 |
| `20261114T150851-8476` | 正式章 | 计划层 `illegal_response` | `upsert.beats[0].voice_ref` 引用同批 `delete` 的 b07（额度 1 次即耗尽） | 待验证 | 待验证 |
| `20261114T201257-26444` | 正式章 | COMMITTED | rev `0afa8074…`，adopted 11-14 20:12 | — | **复用**（source_chapter 已缺失） |
| `20261119T231611-13712` | R7.1 冻结候选 | 终审 BLOCK | blocking=1（`model_audit_PROP_CONTINUITY`） | — | 非规划（不计数） |
| `20261121T181234-6228` | **隔离根**（真实调用） | 计划层 `plan_contract_invalid` | 2×`verify_fact_unsupported`(f03/f06)、1×`verify_plan_coherence`(b06)；9 次调用全 agent_plan | 5 行（ok→illegal→applied→applied→no_change） | **从头（已证）** |

### 4.2 隔离实验（真实调用，非生产根；不计入正式分母）

| run_id | 目录 | 终局 | 关键读数 |
|---|---|---|---|
| `20261018T201436-26936` | `step01_ab/…/iso/c/c_A1` | 计划层 `plan_contract_invalid` | 4×fact partial、b08 coherence、N4 超 12s 单镜 |
| `20261018T201659-32264` | `…/iso/c/c_B1` | 计划层 `illegal_response` | 末轮非法响应 |
| `20261018T201930-29644` | `…/iso/c/c_A2` | 计划层 `illegal_response` | 末轮非法响应 |
| `20261018T202125-32280` | `…/iso/c/c_B2` | **提交门 BLOCK** | 越过计划层；blocking=3 |
| `20261019T071332-20072` | `…/iso/d7_chapter_run` | 计划层 `call_failed` | 调用失败，非内容结论 |
| `20261019T072825-2360` | `…/iso/d8/d8_attempt_01` | 计划层 `call_failed` | 调用失败 |
| `20261019T220801-21200` | `…/iso/d8/d8_dryrun` | 计划层 `outline_invalid` | 4 轮均 `outline_invalid`（干跑输入） |
| `20261019T220929-10456` | `…/iso/d8/d8_attempt_02` | 计划层 `plan_contract_invalid` | fact partial + 预算不可表示 |

### 4.3 离线回放/干跑（无真实终局或非从头运行；不计）

`output/step01_ab/20261018T135622/` 下 `d10`/`d10v2`/`d11`/`d3`/`d5`/`d`/`runner`/`isolation` 等目录（共 1454 个文件）只有 `replan_rounds.jsonl`/日志/单轮产物，无完整 run 终局，**不按 run_id 计**；其轮次 kind 仅作机制证据（见 §5）。

### 4.4 仅文档记录（无冻结原始产物）

- 2026-09-27 十连跑：10/10 提交门 BLOCK，79% blocking 来自确定性判据（`docs/step01-ten-run-problem-report-2026-09-27.md`）。原始产物曾归档于 `.tmp/run10/`，**现已不存在** → 只能引用报告读数，列入分母缺口。
- `.tmp/repair6_20261031/run1`（11-14 COMMITTED 与 11-09 草稿的复用来源）**已不存在** → 复用链与 S09 样本 C 不可复核（见缺口）。

### 4.5 可核实计数（本包口径）

- 可核实真实调用终止记录：**22 条**＝ COMMITTED 5 + 提交门 BLOCK 3 + 计划层终止 14（`illegal_response` 5、`plan_contract_invalid` 6、`call_failed` 2、`outline_invalid` 1）。
- 另有：09-27 十连跑 10/10 BLOCK（仅文档）；R7.1 冻结候选终审 BLOCK 1 条（非规划层，不计数）。
- 从头规划真实样本（已核清/隔离干净根）：11-21 `20261121T181234-6228` 1 条（计划层停）；11-14 两条为生产根（无法区分是否复用缓存，待验证）。
- **同口径可见基线**：自 10-18 严格计划契约起，可核实的从头/隔离真实运行中，无一条产出可提交稿；唯一越过计划层的是隔离 c_B2，随后提交门 BLOCK。该结论只覆盖有产物子集，不代表全部历史运行。

## 5. 逐次失败归因

| run_id | 终局 | 直接证据 | 类别 | 说明 |
|---|---|---|---|---|
| `20261121T181234-6228` | `plan_contract_invalid` | `frozen/20261121T181234-6228/*` | ①请求/分流缺陷（已证）+ ②模型输出/③判据校准（待 S07/S09） | 轮2请求中仍带轮1 n02 拒收且标"上一轮……一个字节没改"（D1）；轮4 `content_slots.applicable=false, why=…verify_plan_coherence`，整轮退回 36K 增量请求（D2）；轮3 `route=source_basis_revision` 只改 f02 证据（D3 未记录 deferred）；终局 f03/f06 由核验员判 partial、b06 coherence 判顺序冲突（D7 校准问题待 S09） |
| `20261114T145644-22104` | `plan_contract_invalid` | `frozen/20261114-formal-evidence/step01_plan_contract_invalid.json` | ②模型输出 + ⑤调用故障 | 一节拍多载体 ×4、选句无合法切分 ×3、核验 `bad_response` ×1 |
| `20261114T150851-8476` | `illegal_response` | `frozen/20261114-formal-evidence/step01_plan_infeasible.json` | ②模型输出（协议违约） | 同一响应删 b07 又引用 b07（`delta_deleted_uid_referenced`），1 次额度即耗尽 |
| `20261018T131545-7108` | `illegal_response` | `output/video/…/chapter_001/step01_plan_infeasible.json` | ②模型输出（协议违约） | 连续 3 次拒收，末次 d01/n02 `view_voice_unit_unplaced` |
| `20261018T132103-25700` | `plan_contract_invalid` | `output/video/…/chapter_001/step01_plan_contract_invalid.json` | ②模型输出（9 条 fact partial）+ 契约预算 | 逐字/因果承载判据，待 S07/S09 区分"契约过严"与"计划确实没讲"；另有 D1/N5 无句末边界超 12s 不可切分 |
| `20261031T170331-14108` | `plan_contract_invalid` | `output/video/…/chapter_002/step01_plan_contract_invalid.json` | ②模型输出 | f05 partial（"杨旭已死/尸体藏马车"未表达） |
| `20261031T171111-2496` | `illegal_response` | `output/video/…/chapter_002/step01_plan_infeasible.json` | ②模型输出（协议违约） | `delete` 与 `upsert` 同批 uid 自相矛盾 |
| `20261018T201436-26936` | `plan_contract_invalid` | `step01_ab/…/c_A1/…` | ②模型输出 + ④待验证 | 4×fact partial、b08 coherence、N4 超单镜 12s |
| `20261018T201659-32264` | `illegal_response` | `step01_ab/…/c_B1/…` | ②模型输出（协议违约） | 末轮非法响应 |
| `20261018T201930-29644` | `illegal_response` | `step01_ab/…/c_A2/…` | ②模型输出（协议违约） | 末轮非法响应 |
| `20261019T071332-20072` / `20261019T072825-2360` | `call_failed` | `step01_ab/…/d7`、`d8_attempt_01` | ⑤调用故障 | 通道/调用失败，不作内容结论 |
| `20261019T220801-21200` | `outline_invalid` | `step01_ab/…/d8_dryrun` | ⑤输入/干跑 | 4 轮均 `outline_invalid` |
| `20261019T220929-10456` | `plan_contract_invalid` | `step01_ab/…/d8_attempt_02` | ②模型输出 + 契约预算 | 4×fact partial；b08–b13 因果链 coherence；发声下界 98.69s 超 90s；D3/N2/N4 三句无合法切分超 12s |
| `20261017T131353-23764` / `20261107T225039-18832` / `20261018T202125-32280` | 提交门 BLOCK | 各自 `step01_commit.json` | 提交门（本包非目标） | 规划层已过；记入 S13 积压 |
| 09-27 十连跑 | 提交门 BLOCK | 文档 §0/§3 | 提交门（非规划层） | 79% 确定性判据；原始产物缺失 |

**"④ 契约挡住合理改编"**：本包尚无确认样本，S07 用手写合法修订验证 11-21 终局计划是否因契约不可满足而被挡；在 S07 出结果前不列缺陷。

## 6. 分母缺口

1. **产物覆盖不完整**：同目录 terminal 文件被后续运行覆盖；`output/video/…chapter_001` 的 `replan_rounds.jsonl` 为 203 行 append-only 聚合，不是单次运行。可核实计数只覆盖有冻结产物的子集。
2. **原始归档丢失**：`.tmp/run10/`（09-27 十连跑）与 `.tmp/repair6_20261031/`（11-14 COMMITTED 复用来源）已不存在；09-27 只能引用报告，复用链只能引用 adoption 记录，均无法独立复核。
3. **fresh/reuse 不可判**：COMMITTED 记录多数未标注是否复用缓存大纲/历史稿；`output/video` 早期目录无法区分保存的是哪几次运行。
4. **离线回放混入累积日志**：`step01_ab` 下 d8/d10/d11/d3 等目录（1454 个文件）含离线单轮回放与干跑，没有 run_id 的可核实终局，不能计入真实运行分母。
5. **S09 样本 C 缺口**：11-09 采纳稿对应计划未冻结（`_pre_step01run_20261114_145605` 只有五件套，无计划文件）；S09 只能改用 11-21 计划 + S07 手写修订，S09 细则须据此收窄。

## 7. 下游消费

- **S02**：按 `corpus_manifest.json` 回放 round 2（期望 `applied`，out `b1653b43…`）与 round 4（期望 `no_change`，`plan_in=ecc6b6e4…`）；创建服务用冻结回复注入，禁止网络。
- **S07**：以 `ecc6b6e4…` 计划为基线写一份合法增量修订，走真实应用入口与完整验收，判定"可满足"或列出拦截 code。
- **S09**：样本 A＝11-21 终局计划（`ecc6b6e4`，问题 f03/f06/b06）；样本 B＝S07 手写修订计划；样本 C 缺失（见缺口 5），S09 细则按 A/B 执行并注明局限。
- **S11**：基线读数用 §4.5 的"可核实真实终止记录"口径，不引用无法复核的历史总数。
