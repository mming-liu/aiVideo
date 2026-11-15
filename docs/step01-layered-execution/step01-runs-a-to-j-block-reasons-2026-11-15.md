# Step01 连续运行 a→j 终局归因清单（2026-11-15）

> 性质：**只读现场台账盘点**，不重跑、不改稿、不提交。目的是回答「一直被拦，是同一个原因吗」。
> 取证范围：`.dsh/step01_full_20261115{,b,c,d,e,f,g,h}/`、
> `.dsh/step01_fix_verify_20261115{i,j}/` 共 10 个 run。
> a–d 停在计划层，**没有生成章节目录**，只有 `console.log` / `exit_code.txt`；
> e–j 有 `step01_commit.json`、`step01_metrics.json`、`step01_calls.jsonl`、
> `step01_repair_rounds.json`（i 无此文件）。
> U7 §12 表格从 b 起算共 7 个 run；本文把 a（前史）、i、j 一并纳入，共 10 个。

## 1. 逐轮终局（原文归因，非概括）

| run | 拦在哪一层 | 终局归因（现场原文/字段） | 稿件 | commit |
|---|---|---|---|---|
| **a** 前史 | 计划层 | `PlanInfeasibleError / illegal_response`：`order.narration_units delta_order_not_a_permutation`——改动后 3 条，order 给了 4 项，差值 `new:n05`；「连续 1 次响应被拒收…额度已耗尽」 | 未生成 | 无（exit=1） |
| **b** | 计划层 | `PlanInfeasibleError / illegal_response`：末次 `slot:fact:f08 repair_slot_conflict`——画面 b05 被 f06 / f08 两槽写成不同内容；额度耗尽 | 未生成 | 无（exit=1） |
| **c** | 计划层 | `PlanContractInvalidError`：事实 f04 / f09 的实际载体 `partial`（「她」未明确是听香、未展示庄丁被支走）。U7 §12 另记为「修订额度 4/4 用满」 | 未生成 | 无（exit=1） |
| **d** | 计划层 | `PlanContractInvalidError`：**主张超出处**——f02「便」把孝期刚过与考中诸生因果相连、f05 脑补「短刀」；另 f06/f09 承载 partial。U7 §12 记为「额度 5 轮没耗尽」 | 未生成 | 无（exit=1） |
| **e** | commit／补丁校验 | `rejected_by_validation`：「**修复响应不可用**（拿到了完整响应、但补丁不可用）」，shot 1/3/7 **装配后 `keyframes` 与 `frame_layouts.kfs` 长度不等**。**plan 层首次 0 残余**，11 镜 63.8s | 已生成 | BLOCK / 17 |
| **f** | commit／G1 规则 | 「G1：上一轮 1 条 blocking 本轮未再报出且无确定性关闭依据」——`MISSING_CAUSAL_EVENT` → `ACTION_CAUSALITY_GAP` 同镜换名。修复轮 `accepted=true / resolved=1`，**整轮仍 BLOCK**，14 镜 81.1s | 已生成 | BLOCK / 24 |
| **g** | 资源／通道 | `final semantic audit request_failed`：**186s 墙钟上限未返回**，SSE 4800 事件「终态收完：未收完」；`audit_call_failed / transient=true`。修复轮 `accepted=true / resolved=2`。plan 0/10 | 已生成 | BLOCK / **1**（非稿件缺陷） |
| **h** | commit／事务验收 | 「提交前修复轮零改善」：末轮 `accepted=false / resolved=2 / layout_structure_invalid`，`reindexed_kf_shots=["5"]`，整轮回退。12 镜 70.6s | 已生成 | BLOCK / 33 |
| **i** | 资源／通道 | `final semantic audit request_failed`：**126s 墙钟上限未返回**，SSE 3348 事件未收完；`audit_call_failed / transient=true`。**0 修复轮**，连 `step01_repair_rounds.json` 都未生成。17 镜 107.8s | 已生成 | BLOCK / **1**（非稿件缺陷） |
| **j** | commit／事务验收 | 「提交前修复轮零改善」：`accepted=false / resolved=0 / no_target_resolved`。10 镜 62.1s | 已生成 | BLOCK / 18 |

三次事务级失败的具体读数：

| | h | i | j |
|---|---|---|---|
| 调用 | 16，全 ok | 20，19 ok / 1 failed | 14，全 ok |
| 修复轮 | 2 | 0 | 1 |
| `layout_structural_violations` | 2 条「字段被改写」误报 | 无记录 | `[]` |
| `frame_mappings` | 无（旧实现） | 无 | 有（`source=keyframe_structure`，恒等） |
| blocking 主类 | `duplicate_injection 9`、`kf_segment_missing 4`、`FF_RESULT_LEAK 3` | `audit_call_failed 1` | `duplicate_injection 10`、`kf_event_window_mismatch 3` |

**10 个 run 没有一个 PASS**；g / i 的 `blocking_count=1` 是通道故障占位，不是稿件缺陷数，不能与 17/24/33/18 直接比大小。

## 2. 「一直被拦」不是同一个原因：按类归并

| 类别 | run | 性质 |
|---|---|---|
| **A 计划层契约 / 额度 / 证据对齐** | a、b、c、d | 答卷违反 plan delta 契约、槽位冲突、修订额度耗尽、主张超出原文出处或载体 partial |
| **B 修复答卷与授权 / 装配契约不一致** | e、h（部分）、j（部分） | 整表覆盖 `frame_layouts` / `keyframes`、kfs 长度不等、整表替换与 `keyframe_additions` 混用、越权提交未授权路径 |
| **C 框架 / 规则自身** | f、h（部分） | G1 关闭依据规则；守卫按同下标比较的**帧索引错配误判**（h，已由补正包修复）。j 原先归入本类的「writer 全数回退 36 字段」经 M01 回放**撤回**（实为事务整轮回退） |
| **D 基础设施 / 调用超时** | g、i | 最终语义审计 SSE 墙钟超时，与模型答卷、prompt、代码改动均无因果 |

层次确实在逐轮后移（plan → commit 补丁 → G1 → 资源 → 事务验收），但**每层的归因类别不同**，且 h / j 都是 B+C 混合。这只能支持「多个阻塞已被推进」，**不足以断言瓶颈收敛到单点**，也不足以归到「模型能力边界」（这两条结论用户此前已明确撤回）。

## 3. 关于「是 prompt 有问题，还是模型不遵从」

现场证据显示：**规则写了，拒因也带了修法，但答卷仍反复整表覆盖**。

**（a）prompt 里的硬约束是明写的**（`step01_script_gen/main.py:8931–8963`）：

```
请逐条修复下方列出的问题；每条问题只改解决它所需的已授权字段
优先只返回被点名镜头的窄字段补丁 updates（路径和值），无需重抄未改字段
只提交需要修改的授权路径……updates 与 keyframe_additions 可以在同一目标上并用
联动修复（缺帧 / 没有按时间归属它的关键帧时）用成对形状 keyframe_additions
同一个镜头只能用一种表达补同一帧……时刻重复会被判重复事件，整轮回滚
字段授权（硬约束）：allowed_fields 列出本轮允许修改的字段路径；未列入的字段
  必须逐字照抄原镜，改一个字节都会让整批回滚
结构字段必须彼此自洽：keyframes 与 frame_layouts.kfs 等长
```

**（b）拒因文本也带修法**：e 的 `detail` 写「装配后 keyframes 与 frame_layouts.kfs 长度不等」；h 的 `rejected` 直接写
「整表替换 frame_layouts 时 kfs 必须与 keyframes 一一同序对应……**缺帧/缺段请用
`keyframe_additions`（frame 与 layout 成对）补，不要整表重写 kfs**」，以及
「同一响应里不得既整表替换又追加同一帧；请只用其中一种表达」。

**（c）但答卷没有按它做**：

- e 的 `patch_unusable.raw_text_head`：`{"shot_replacements":[{"shot_id":"1","updates":[…,{"field_path":"frame_layouts","value":{…整表…}}]}]}` → 3 个镜头 kfs 长度不等。
- h：shot 1 整表替换导致 kfs 长度不等；shot 7 / 8 `keyframe_additions[0]` 与已存在帧 `t_sec=4.5` 重复（整表 + 追加混用）。
- j：4 个 shot **全部**用 `updates` 提交 `frame_layouts` / `keyframes` 整表，答卷里 `keyframe_additions` **出现 0 次**；`added_indices=[]`。其中 shot 1 / 5 / 6 的整表路径不在本轮 `allowed_fields` 内，在授权合并阶段即被拒。

**（d）但也不能就此定论「模型不听话」**，至少还有三项同样有证据的干扰因素：

1. **规则密度与表达形态过多**：同一段 prompt 里约 20 条硬约束，窄补丁 / 整镜替换 / 成对补帧三种表达并存，还要同时满足「同构成套出现」「联动时一次改全」。没有对照实验，无法区分「没读懂」与「做不到」。
2. **授权粒度不统一**：run j 的 `authorization` 里，`shot_4` 的 allowed **含整表 `frame_layouts`**（所以它的整表替换合法通过，并派生出 `source=keyframe_structure` 恒等映射），而 `shot_1` / `shot_5` 只给 `frame_layouts.ff.0.props`、`keyframes.*.desc` 等叶子。模型若采用「整表覆盖」一刀切策略，必然在部分镜头被拒。同时 prompt 要求「联动时一次改全」，授权却常只给叶子，二者存在张力。
3. **反馈闭环未接通**：`retry_state` 事务拒收反馈扩展按补正计划**仍留后续工作包**；h / j 这类在合并后事务验收阶段产生的拒因，是否回灌到下一次请求尚无入口验收。run j 只有 1 轮修复轮，模型没有第二次机会纠正表达。

**因此现有证据只能支持**：拦点分散，多数落在「答卷与契约/授权不一致」这一大类；要把它单归到 prompt 表达、模型遵从、协议设计或反馈闭环中的某一个，需要专门的诱导对照实验。本文不下这个结论。

## 4. 与本次修复包的关系

- 补正包修掉的是 **C 类中的一处确定 bug**（守卫帧索引错配）及零效果时账本观测缺口；它不处理 A / B / D 类。
- run j 在真实链路上验证了映射、四节点账本、越权拒收接线生效，但**没有出现合法中间补帧**（`keyframe_additions` 0 次、`added_indices=[]`），所以补帧误拒是否在真实答卷上被消除仍未验证；离线证据为 52 passed 事务/单元用例 + run h 派生样本。
- run j 的 `no_target_resolved` 由 3 个越权整表补丁（B 类）+ shot_4 补丁未命中原问题（B 类）共同造成，不是守卫误判，也不由本包改动引起。36 字段差异来自事务整轮回退、不是 writer 回退（M01 证据 `run_j_attribution_replay.json`）；「补丁未命中原问题」指答卷未补 `STATE_CHAIN_CONFLICT` 要求的抓发／步摇掉落事件，确定性 checker 90→87、`resolved=0`。

## 5. 复现与结构化证据

```bash
# 逐轮盘点（只读，输出 JSON 到临时目录）
python C:/Users/ee54/AppData/Local/Temp/wb_runs_survey.py
```

- 本文配套机读证据：`docs/step01-layered-execution/step01_runs_a_to_j_evidence.json`
  （每个 run 的 calls、metrics、commit、逐轮裁决、blocking 分布、拒因全文）。
- 历史表：`U7-consistency-contract-proposal-2026-11-15.md` §12 / §12.1 / §12.2。
- 修复包专题：`kf-frame-identity-alignment-2026-11-15.md`；
  真实重跑：`step01-fix-verify-20261115i.md`（含 run i / run j）。

**边界**：全程只读，未重跑、未改稿、未提交；`./output` 正式产物未触碰，HEAD 仍为
`88404b7cb49729dc082d8cc43941464a3c63c9f9`。本文不推算通过率、不下单点瓶颈或模型能力结论。
