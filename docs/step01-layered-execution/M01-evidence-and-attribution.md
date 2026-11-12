# M01：冻结证据、实验归因与受控样本（2026-11-12）

本文件是统一收口包 **M01** 的交付物。它不改写历史报告，只把「当时到底发生了什么」
逐条落到可核对的证据上，并给出**修正后的归因**。旧结论保留原文，新解释另记版本
（口径见 S06 报告 §4 与 `AGENTS.md`「结论不越过证据」）。

## 1. 冻结身份

| 项 | 值 |
|---|---|
| 仓库 HEAD | `b9420989d617b9d1110af6b95be4ab28cc91ba09`（`b942098`） |
| 工作树 | 仅 `.workbuddy/`、`start_studio.bat` 未跟踪；本包改动另计 |
| S06 冻结原件 | `baselines/2026-11-12-pre-layered/s06_ab/`（已提交） |
| U6 候选 | `baselines/2026-11-12-pre-layered/u6_frozen/candidate_d1a/candidate.json` |
| 逐次索引 | [`artifacts/m01/call_index.json`](artifacts/m01/call_index.json) / [`call_index.md`](artifacts/m01/call_index.md) |
| 索引构建器 | [`tools/build_step01_call_index.py`](../../tools/build_step01_call_index.py) |

> 行号均按 HEAD `b942098` 核对；本包改动后行号会漂移，定位以**函数名**为准。

关键文件摘要（`sha256`，构建器与报告共用同一算法）：

| 文件 | 字节 | sha256 |
|---|---|---|
| `s06_ab/runs_20261112T070909/results.json` | 20,885 | `b8357f72b8712fa1…` |
| `s06_ab/phase2_20261112T072011/repair_diag.json` | 5,232 | `26fefba25ea3cc6a…` |
| `s06_ab/phase2_20261112T072011/final_audit.json` | 9,358 | `b74fc6938edb263f…` |
| `u6_frozen/candidate_d1a/candidate.json` | 149,305 | `3f88a060082173ca…`（与 S01 记录一致） |
| `fixtures/.../time_ellipsis_legal.json` | 3,116 | `0e915425ef9de813…` |
| `fixtures/.../time_ellipsis_broken.json` | 2,955 | `6b6945baf062a7cd…` |

## 2. 逐次索引：报告所称的八次确实存在，但**不是全部实发**

`call_index.json` 把每次发送关联到 请求摘要 / 解析结果 / 耗时 / usage / 通道 /
代码版本 / 候选身份。读数：

| 批次 | 调用 | 累计耗时 | token | 代码版本 | 说明 |
|---|---|---|---|---|---|
| `s06_ab` | 8 | 983.0s | 153,426 | `7286516` | S06 报告口径：6 次审计 A/B + 1 次修订 + 1 次终局审计 |
| `s06_ab_rerun` | **20** | 2,021.1s | 250,228 | `0a2bcc4` | S06 之后的复跑批次（`131449` / `131654` / `134738` / `135000` / `140115`） |

**修正**：盘上实际有 **28 次真实发送**，其中 20 次发生在 S06 报告之后、`0a2bcc4`
（文本通道绑定 OpenCode Go）之上，**没有任何报告记录**。它们的样本是改版后的
`state_legal` / `voice_legal`，属于跨内容通用性复核，与本报告的八次不是同一批。
两者混算会同时高估「八次」的统计意义和低估历史消耗。

另有一批 `150625` 的 **dry** 记录（6 条，未发送），其提示词摘要与当前夹具一致
（见 §4），是「夹具改完但没再真跑」的直接痕迹。

## 3. 原始响应：**0/8 留存**

| 调用 | 原始响应 | 盘上还剩什么 |
|---|---|---|
| 审计 A/B 6 次 | 未落盘 | `results.json` 的 `raw_head` 字段为**空**；只有解析后的 `issues` |
| 修订 1 次 | **500 字符片段** | `step01.log` 的 `[DEBUG] … response: … body=` 行 |
| 终局审计 1 次 | 未落盘 | 只有解析后的 `issues` |

截断点已定位：`_text_gen/agent_plan_provider.py:968-971` 写日志时用
`resp.text[:500]`。修订轮那 500 字符以 `{"shot_replacements":[{"shot_id":"7",
"updates":[{"field_path":"prompt"…` 开头，到 `"lf_desc","value":"水面波纹` 处截断。

后果：**S06 的修订轮无法逐字回放**。M02 因此改用「受控桩响应 + 实际修订入口」，
并在本文件 §5 明确区分「能证明的」与「不能证明的」。这不是降低标准，而是把
不可证的部分标成不可证。

## 4. 样本漂移：`time_ellipsis_legal` 的 S06 结论属于**旧版样本**

用**当前**夹具重建当时的审计提示词，与记录的 `prompt_sha1` 比对：

| 样本 | 契约 | 结果 | 记录 | 当前 |
|---|---|---|---|---|
| `state_broken` | old / new | **一致** | `89f94c406d21` / `880fb79fbc9d` | 同 |
| `voice_broken` | old / new | **一致** | `13db0f3e75b8` / `37724d885f0b` | 同 |
| `time_ellipsis_legal` | old | **漂移** | `5b5cca632df8` | `b8fe76ad7eb4` |
| `time_ellipsis_legal` | new | **漂移** | `6cca1c6aeb49` | `fd3d8759c931` |

漂移原因：S06 之后该样本被改写（修掉了 `lf`/`motion` 的「走向院门 / 走到院门」
自相矛盾，并做了内容无关化）。**当前版本从未真实发送过**，所以：

- S06 关于「合法时间省略」的结论只对**旧版样本**成立；
- 当前版本的模型表现记为**未验证**（不是「通过」，也不是「失败」）。

`state_broken` / `voice_broken` 两份逐字可重建，说明夹具内容无关化没有改动它们。

## 5. 四项归因修正

### 5.1 「授权面不足」尚未被单独证明；「shot 1 / shot 6 的改动已应用」不成立

**台账事实**（`repair_diag.json`）：

```
targets = [shot_1, shot_11, shot_6, shot_7]
accepted = false, resolved = 0, audit_field_touched = 0, reason = no_target_resolved
related_readback.requested = [] × 7 行, changed = 0
revision_before = revision_after = 84ff4112…
```

`audit_field_touched` 是在**回退之前**、对合并结果算的（`main.py:6609-6614`）。
它按 `_audit_issue_field_touched` 对「问题点名字段」做授权投影后比较原稿：
只要 `prompt` 真的被改过，shot 1 的 `prompt` 就在授权面内，计数必然 ≥1。
**计数为 0 ⇒ 这两个镜头的 `prompt` 根本没有发生变化。**

所以 S06 报告 §2 的「shot_1 / shot_6 的 `prompt` 改动已应用，但验收 resolved=0」
**与台账相反**，应予撤回。可成立的表述是：该轮**没有**任何授权字段发生改变。

再看「越权 `keyframes` 被拒」这一条。500 字符片段里能看见的是 shot 7 的**窄字段
补丁**（`updates[]`），且第一条就是 `prompt`——而 shot 7 当时的授权面只来自
`lf_desc`（#2）与 `ff_desc`（#10），`prompt` 本来就不在内。也就是说：

| 候选成因 | 判定 | 依据 |
|---|---|---|
| ① 输出形状不符合协议 | **不成立**（就可见部分而言） | `shot_replacements[].updates[]` 正是 `apply_scoped_field_updates` 的输入契约 |
| ② 授权粒度不合适 | **成立** | shot 7 的补丁是「同一镜的一组连贯修改」，但授权面只到被点名的两个字段 |
| ③ 确实缺少必要授权 | **不能判定** | 需要看完整响应才知道 `keyframes` 是否属于最小合法修法；盘上只剩 500 字符 |

结论：S06 报告的单一成因（「授权面不足，不是模型输出面过大」）**证据不足**。
可证明的只有一条机制：**补丁里只要有一条路径越权，整条补丁被拒收**
（`repair_fields.py:1269-1273`，fail-closed）。至于那条路径是否「必要」，
属于可以单独验证的问题——见 M02 的受控回放。

### 5.2 U6 的 `ACTION_OMISSION`（#1）应归为「合法省略被模型质疑」

S01 已按原文「一柱香的时间之后」判 #1 为**已有合法表达**（`docs/step01-s01-audit-baseline-2026-11-11.md:53`）。
因此：

- 它**不是**「真实因果缺失」的正例；把它当正例会污染判据校准；
- 终局审计**没有**再报 #1（`final_audit.json` 的 11 条里没有 shot 13 的搬运问题），
  这与 S01 的裁决一致，不是回归；
- 真正的「缺失必要因果」反例另设：`tests/step01_script_gen/fixtures/audit_narrative_families/time_ellipsis_broken.json`
  （送信 → 三日后敌营，省略掉「送出/被擒/投敌」这条观众必须知道的关系）。

成对样本现已齐备（`time_ellipsis_legal` / `time_ellipsis_broken`），且两者都不含作品专名。

### 5.3 R2V 的 `reference_image` **不**提供首帧锁定语义

代码层面已核实（不是推断）：

- `step02_video_gen/prompt_builder.py:1511-1531`：r2v 下首帧**只作 `reference_image`
  构图锚点**，并显式把 `role_claims` 记成 `{"first_frame": "reference"}`；
- `_video_gen/volcengine_provider.py:535-536`：所有图片一律以 `role="reference_image"`
  下发，r2v 分支不使用 `first_frame` 角色；
- 首帧是否进入参考、排在第几位、是否被拼图/过滤，由
  `prompt_builder.py:1499-1505`（收集）、`1928-1939`（`fit_refs` 定稿）与
  `volcengine_provider.py:523-533`（超限拼表）三处共同决定。

因此「画面从参考图 1 无缝开始」这类表述**只能在一次具体请求里**成立：
它取决于该次请求的最终材料顺序与是否拼图，不是 R2V 模式的固有语义。
提示词侧不得把「参考图 1」写死成「本镜首帧」。M06 按此改造。

### 5.4 提示词效果仍未验证

「删减画面复述能更保画风」「能提升起点画质」目前**没有任何真实对照**：

- 本轮没有发起 Step02b 真实调用（费用与选型均后置）；
- 两份官方文档的原始链接不在本轮材料里，适用模式（文生/图生/参考生）未核对；
- 现有证据只能到「请求内容变了、指代与实际材料一致」这一层。

记为**待验证收益**，不作为已完成的设计前提。

## 6. 问题计数重算：**4/7**，不是 5/7

按「镜头 + 字段 + 事实」的稳定身份，把 S01 的 7 条确认缺陷与终局审计 11 条逐条对照：

| S01 # | 事实 | 身份 | 终局审计对应条 | 严重度 |
|---|---|---|---|---|
| #2 | 酒壶回手 | shot7 / `lf_desc` | `STATE_CHAIN_CONFLICT` shot7 `lf_desc` | advisory |
| #4 | 首帧结果泄漏 | shot11 / `ff_desc` | `FF_RESULT_LEAK` shot11 `ff_desc` | advisory |
| #5 | 大特写 vs 大远景 | shot1 / `prompt` | `VISUAL_SPEC_CONFLICT` shot1 `prompt` | advisory |
| #6 | 括号说明重复 | shot6 / `prompt` | `TEXT_DUPLICATION` shot6 `prompt` | advisory |
| #8 | 掌掴前披头散发 | shot10 / `ff_desc` | **未报出** | — |
| #9 | 凭空提壶 | shot4 / `ff_desc` | **未报出** | — |
| #10 | 刺客握刃 | shot7 / `ff_desc` | **未报出** | — |

- **重报 4 条（#2/#4/#5/#6），未报出 3 条（#8/#9/#10）**，全部为 `advisory`，0 blocking。
- 报告写的「5/7」把 **#3（钓杆布局，S01 判为「改进建议」）** 算进了分子。#3 不是
  7 条确认缺陷之一，它的确被重报了（`PROP_CONTINUITY` shot5
  `frame_layouts.ff[2].props`），但只能作为「改进建议仍被看见」记账。
- 终局审计另有 6 条新增（`SOUND_PROP_MISMATCH`、`DIALOGUE_CONTENT_MISMATCH`、
  `MOTIVATION_GAP`、`DIALOGUE_CONTENT_MISSING`、`LAYOUT_VISIBILITY_CONFLICT`、
  `LAYOUT_OMISSION`）与 1 条与 #8 同镜不同字段的 `MOTIVATION_GAP`（shot10
  `motion_desc`）——后者**不能**当作 #8 已修复。

这条修正影响结论强度：**同一份未变稿件的确认缺陷重报率是 4/7，漏报 3/7**。
在 M03 的问题状态台账落地前，不得用「本次没报」推出「已经解决」。

## 7. 受控夹具与回放入口

| 用途 | 入口 | 数据依赖 |
|---|---|---|
| 逐次调用索引 | `python tools/build_step01_call_index.py` | `baselines/…/s06_ab`（已提交）+ 可选 `.pytest_tmp/s06` |
| 样本漂移核对 | 同上（内置） | 当前夹具 + 旧契约冻结副本 `s06_ab/tools/audit_old.py` |
| U6 证据 | `baselines/2026-11-12-pre-layered/u6_frozen/` | 已提交；摘要与 S01 记录一致 |
| 合法/反例成对样本 | `tests/step01_script_gen/fixtures/audit_narrative_families/` | 已提交；不含作品专名 |

索引工具是**专项回放**工具，不是干净检出即可跑的基础测试：它显式声明外部数据集
根，缺失时给出明确错误，不自动找「最新目录」。基础测试只读受控夹具。

## 8. 未验证项（不冒充缺陷，也不冒充通过）

1. 当前 `time_ellipsis_legal` 的真实模型表现（夹具已改，未再发送）。
2. 20 次复跑批次的样本版本与结论归属（无报告、无夹具摘要记录，只索引不解读）。
3. 修订轮完整响应里 `keyframes` 是否为最小合法修法（原件只剩 500 字符）。
4. 提示词的画风/起点质量收益（无真实对照）。
5. S06 的 `no_target_resolved` 是否也受「模型只回了 2 个镜头」影响——现有证据
   无法区分「只回 2 镜」与「回了 4 镜但 2 镜被拒、2 镜无实质改动」，因为逐目标
   拒绝清单当时没有进台账（该字段是 S06 之后才加的，见 `main.py:9486-9490`）。
   M02 已把这一栏补进台账，后续同类事故可以逐目标归因。
