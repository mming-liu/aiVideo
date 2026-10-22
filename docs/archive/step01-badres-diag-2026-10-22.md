# step01 / d11 包：verify_bad_response 根因诊断与下一包任务包

> 2026-10-22 诊断。上游交接卡：[step01-handoff-2026-10-21.md](step01-handoff-2026-10-21.md)。
> 本文件**替代**交接卡 §4 的"下一步"，按这里的判据执行。

## 1. 一句话结论

`verify_bad_response` **不是**新问题、不阻塞任何门控：它是 `real_rounds3` 那轮修订留下的
**失败核验记录残留在候选计划里**。真正没修完的仍是那 6 条内容问题，且已逐条定位到
具体 uid；另外**多出 1 条漏网**（d02）和 1 处**预算硬冲突**，交接卡没写。

## 2. verify_bad_response 的根因

`real_rounds3/candidate_plan.json` 的 `plan_verification` 字段里躺着：

```json
{
  "schema": "content_verification.v4",
  "status": "bad_response",
  "code": "verify_bad_response",
  "plan_digest": "841bec5d8e27a77875315bcadfb60d149df071d27360176847019707ee5aaf1b",
  "error": "核验响应不满足记录契约：｜核验响应 必须是对象（当前是 NoneType）"
}
```

链条：

1. 07:15 那轮 `real_rounds3` 修订过程中，核验调用**拿回了 NoneType**（响应体不是对象），
   控制器按 `FAIL_BAD_RESPONSE` 写了一条失败记录进候选。
2. 该记录**没被剥掉**，跟着候选一起落盘 —— 于是候选自带一个"我核验没过"的结论。
3. 07:20 的 `verify_rounds3_verify` 拿这个候选做基线时，`plan_acceptance_problems`
   看到 `plan_verification.status != ok` ⇒ 在 `deterministic.codes` 里报 `verify_bad_response`。
4. `d11_run2.py:208` 把这两个码排除出门控：

   ```python
   bad = [c for c in codes if c not in ('verify_record_missing', 'verify_bad_response')]
   ```

   ⇒ `gate=[]`、`ok_det=True` ⇒ 核验照常发出（`transmitted=1, status=ok`）⇒
   `final_acceptance` 基于**新记录**算出 6 条。

**因此**：它在 `run_log` 里是噪音，不是故障。但它同时也说明一件事——
**候选落盘时没有清掉失败记录**，这本身是个该记一笔的缺陷（修法见 §6）。

## 3. 六条问题的精确位置

来源：`verify_rounds3_verify/verification_record.json`，
用 `plan_acceptance_problems(dict(cand, plan_verification=rec), NOVEL)` 复算确认复现 6 条。

### 3.1 槽位错配（3 条，可机械修）

| 问题 | uid | 现状 | 核验结论 | 修法 |
|---|---|---|---|---|
| `verify_voice_not_speech` | d03 | 在 `dialogue_lines[3]` | `novel_narration`（内心独白） | **移到 `narration_lines`** |
| `verify_voice_speaker_missing` | d03 | 同上，`speaker=''` | 旁白不需要说话人 | 上一条修好后**自动消失** |
| `verify_voice_not_speech` | n07 | 在 `narration_lines[7]` | `novel_speech`（张十三对听香说的话） | **移到 `dialogue_lines`** |

d03 与 n07 **正好互换**：

- `d03` = "我暂且隐瞒死讯，先行离开此地，寻来他们再共商对策吧。" —— 原文"心中暗道"，是独白
- `n07` = "你若不想吃官司，便听我吩咐，由我作主，莫要胡乱声张。" —— 张十三对听香说的

### 3.2 事实承载不完整（3 条，需补内容）

| 问题 | uid | 缺口 | 现有载体 |
|---|---|---|---|
| `verify_fact_unsupported` | f02 | 未展示"**十六岁丧父接手家业**" | n02 只覆盖"跻身十大豪门" |
| `verify_fact_unsupported` | f08 | 未展示"**掌掴听香**"、"听香答应听吩咐" | 答应了（d01），掌掴没有 |
| `verify_fact_unsupported` | f09 | 未展示"**借口生意急事**"、"连夜驾车离开" | n08 有"连夜驾车"，缺"生意急事" |

**逐字可用的原文候选句**（已用 `in NOVEL` 逐条验证通过）：

- f02：`四年前杨家老主人杨炳坤病逝的时候，把兴步维艰的的家当一股脑儿交到了他年仅十六岁的独生子杨旭手中。`
  （注意原文就是"**的的家当**"，两个"的"，逐字核验必须原样保留）
- f08：`反手一掌，一个清脆的耳光便扇在她的脸上，把听香的半边脸庞都打木了。`
- f09（张十三对庄仆的原话，替换 d02 即可一并解决）：
  `我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理` +
  `公子爷吃醉了酒，险些跌落水中` + `我和听香姑娘要扶公子马上回城`

## 4. 新发现的第 7 条：d02 漏网

`d02` = "公子爷吃醉了酒，险些跌落水中，所以惊得听香姑娘尖叫起来。"

- 它在原文里是**叙述句**（逐字存在，已验），却被放进 `dialogue_lines`（对白槽）。
- 核验记录把它判成 `novel_speech`、`speaker=张十三` —— **核验模型判错了**，
  于是 `verify_voice_not_speech` 没报出来，成了漏网。
- 后果：下一轮核验若判成 `novel_narration`，会**新增一条**槽位错误；若仍判 `novel_speech`，
  错误状态被固化。

**建议**：不要只是挪槽。把 d02 换成张十三对庄仆的**原文台词**（见 §3.2 f09），
它同时覆盖 f09 的两个缺口，且本来就该是台词。这样 d02 的槽位归属也就不再有争议。

## 5. 预算硬约束：余量只有 13.50 s

逐条估时（`plan_feasibility._estimate_duration`，约 **0.25 s/字**）：

| 单位 | 时长 | 字数 |
|---|---:|---:|
| d01 | 4.70 | 19 |
| d02 | 7.45 | 28 |
| d03 | 6.95 | 26 |
| n01 | 5.70 | 21 |
| n02 | 4.10 | 15 |
| n03 | 7.10 | 30 |
| n04 | 7.40 | 28 |
| n05 | 8.15 | 31 |
| n06 | 6.55 | 26 |
| n07 | 7.00 | 26 |
| n08 | 11.40 | 47 |
| **合计** | **76.50** | **297** |

上限 90 s ⇒ **余量 13.50 s ≈ 54 字**。而 §3.2 三处补齐最少要加 ~90 字。
**靠新增必然超预算，必须靠净减量**。

可腾时间的地方（按性价比）：

1. `n04` = "这个名唤听香的美人儿当真不错，生就一副如花似玉的俏模样。" 28 字 / **7.40 s**
   —— `fact_refs: []`，**不承载任何 required_fact**，删除不影响任何支持关系。
2. `n03` 30 字 / 7.10 s 承载 f03，不可删；但可截短到仍能承载 f03 的最短切片。
3. `d02` 换台词时，只摘"醉酒"+"生意急事"两小句，不要整段搬。

**算账示例**（删 n04 后余量 20.90 s）：

- 补 f02：44 字 ≈ 11.0 s
- 补 f08：20 字 ≈ 5.0 s
- d02 换两句台词：14+33 字，净增 ≈ 4.6 s
- 合计 ≈ 20.6 s ⇒ **仅余 0.3 s**，一次成功的概率很低，需按上表继续挤。

## 6. 附带发现：不可达代码

`step01_script_gen/plan_feasibility.py` 中，`require_verification` 分支里

```python
    if not require_verification:
        ...
        return problems
    if verification is None:
        _add("verify_record_missing", ...)
    elif not _ctx_bad:
        ...
    return problems
    if verification is None:            # ← 以下整段永不执行
        ...
```

`return problems` 之后还有一整段**重复的** `if verification is None: ... else: ...`。
属于死代码，建议单独一个包清理（**不要混进本包**，本包的判据只认 6 条归零）。

## 7. 本包执行步骤（判据可机器检查）

**额度上限：真实核验调用 1 次。不发修订调用。**

1. **零成本改计划**（不调模型）：按 §3.1 互换 d03/n07 槽位，按 §3.2 + §4 补内容，
   按 §5 做净减量。产物写到新目录 `d11_run/handfix_r1/`，**不要覆盖 `real_rounds3`**。
2. **确定性验收归零**（零发送）：

   ```bash
   python -c "from step01_script_gen import outline as o; ..."
   # plan_acceptance_problems(new_plan, NOVEL) 中除 verify_* 外必须为空
   # 且 _estimate_duration 合计 <= 90.0
   ```

   注意：**裸计划**（不挂 plan_verification）会且只会报 `verify_record_missing`，
   这是预期状态，不要为它改计划。
3. **发出 1 次核验**：

   ```bash
   D11_STAGE=verify D11_BASE=<新候选> D11_CAP=1 python output/step01_ab/20261018T135622/tools/d11_run2.py
   ```

4. **出口判据**（三条全中才算完成）：
   - `run_log.json` 的 `final_acceptance.codes == []`
   - `deterministic.voice_lower_bound <= 90.0`
   - `self_check.ok == true` 且 `归档数 == transmitted`

5. 若核验后仍有残留问题：**如实记录剩余清单，不要改提示词硬凑**，
   也不要为了清零而删掉承载事实的旁白。

## 8. 边界

- 冻结输入不动：`d9_feasibility/input/{plan.json,novel.txt,problems.json}`
- `real_rounds3`、`verify_rounds3_verify` 只读，作为对照
- 不新建 `dN_*.py` 运行器；只用 `d11_run2.py`
- 剩余 4 类硬阻塞才停：外部复核关口 / 改产品标准 / 需扩授权 / 规格冲突

## 9. 第一轮 handfix 复盘（2026-10-22 下午）——**根因是"一条事实挂多个载体"**

`handfix_r1/handfixed_plan.json` 已完成：槽位互换正确、总时长 **85.45 s**、
新旁白都配了独立节拍（B2/B3/B11）、只读文件未改。**但核验一定还会失败**，原因如下。

### 9.1 核验的上下文是**单载体**的

从 `verify_rounds3_verify/calls/verify_archive.jsonl` 的 prompt 实测，每个事实下面
"实际展示正文（**只有这些**是观众能看到的）"**只列一条**：

```
== f02  →  - [旁白 n02] 如今已跻身青州十大豪门之列了。      （只有 n02）
== f08  →  - [旁白 n07] 你若不想吃官司，便听我吩咐…          （只有 n07）
== f09  →  - [对白 d02] 公子爷吃醉了酒，险些跌落水中…        （只有 d02）
```

即：取 `required_facts` 里 `｜承载：Bn/旁白k` 指向的**那一条**，不聚合多条。
所以**加旁白但不改承载声明 = 白加**；f02/f08/f09 必然继续 partial。

### 9.2 规范本来就要求拆事实

`outline.py:76-83` 原文：

> **一条事实只写一件事**（一个命题）：身份、年龄、来历、继业、功名等要**各自成条**，
> 不要把几件事并成一条……**承载必须能完整表达这条事实的「原文证据」**——逐句都要表达得到。
> 表达不了（原文那句太长、一镜说不完，或画面拍不出来）就**各自另立一条事实**，
> 别硬挂一条只说得出一半的载体。

f02/f08/f09 都是"几件事并成一条"，**本身违反规范**。拆开是按规范修复，不是改产品标准。

### 9.3 必须做的拆分（载体已全部存在，不需要再加内容）

| 原事实 | 拆成 | 承载（两处都要登记） |
|---|---|---|
| f02 十六岁丧父接手家业 + 跻身十大豪门 | a 杨旭十六岁丧父接手家业 | `B2/旁白8` ＋ 旁白8 `fact_refs` 填该事实序号 |
| | b 杨旭使杨家跻身青州十大豪门 | `B4/旁白2` ＋ 旁白2 `fact_refs` |
| f08 掌掴 + 威胁 + 答应 | a 张十三掌掴听香 | `B11/旁白10` ＋ 旁白10 `fact_refs` |
| | b 张十三威胁听香不得声张 | `B13/台词3` |
| | c 听香答应听张十三吩咐 | `B14/台词1` |
| f09 谎称醉酒 + 生意急事 + 连夜离开 | a 张十三对庄仆谎称杨旭醉酒 | `B15/台词2` |
| | b 张十三借口生意急事要赶回城 | `B16/台词4` |
| | c 张十三连夜驾车离开 | `B17/旁白6` ＋ 旁白6 `fact_refs` |

每条事实的「原文证据」只写**该条本身**的引文，不许把整段都抄进去。
f01/f03/f04/f05/f06/f07 原样保留；拆开后的 `required_facts` 序号要连续，
**所有旁白的 `fact_refs` 必须同步改成新序号**（这是最容易错的一处）。

### 9.4 顺带修：n08 不是完整句

`n08` = "四年前杨家老主人杨炳坤病逝的时候，" —— **句内只有逗号，没有句末标点**，
规范要求"旁白要选一镜说得完的原文句（句内有句末标点）"。

改法：把 n08 与 n09 **合并成原文完整句**（这条在原文里是连续的一句）：

```
四年前杨家老主人杨炳坤病逝的时候，把兴步维艰的的家当一股脑儿交到了他年仅十六岁的独生子杨旭手中。
```

44 字 ≈ 11.0 s。合并后 B2、B3 收敛为一条节拍，旁白总条数 -1。

### 9.5 预算

`n04`（「这个名唤听香的美人儿当真不错…」28 字 / 7.40 s，`fact_refs` 为空、不承载任何事实）
是**第一优先可删项**。删掉后 85.45 → 约 78 s，再做 9.4 的合并，余量回到 12 s 以上。

## 10. 网络阻断：核验这一发不能由 codex 发

codex 的 `workspace-write` 沙箱把代理设成 `127.0.0.1:9`（discard 端口），
`opencode.ai` 连接被拒：

```
ProxyError('Unable to connect to proxy', ... 127.0.0.1:9 ... [WinError 10061] 拒绝连接)
```

⇒ codex 只负责**改计划 + 确定性验收**（零成本），**真实核验由外部发放**。

## 11. 结案（2026-10-22 14:21）——`final_acceptance.codes == []`

### 11.1 结果

```
stage=verify case=r6  plan_sha16=690e648b76da979f
deterministic.voice_lower_bound = 84.85   (<= 90 ✓)
deterministic.beats = 16
verify.transmitted = 1, status = ok
final_acceptance.codes = []               ← 0 问题 ✓
self_check.ok = true, archived = [send_01.json]  ← 归档数 == 传输数 ✓
```

核验记录：15 条事实**全部 supported**，14 条声音单位**全部 supported**。

交付物（挂好核验记录，独立复核仍为 0 问题）：
`d11_run/handfix_r6/final_plan.json`

### 11.2 计划最终形态

| 项 | 原 real_rounds3 | 最终 |
|---|---:|---:|
| required_facts | 9（3 条是复合命题） | **15**（每条一个命题） |
| 旁白 | 8 | 10 |
| 对白 | 3 | 4 |
| 节拍 | 14 | 16 |
| 声音下界 | 76.50 s | 84.85 s |

关键改动：
1. d03（内心独白）与 n07（张十三台词）**槽位互换**；
2. d02 换成张十三对庄仆的原文台词，并新增 d04 补"借口生意急事"；
3. 新增两条旁白补"丧父"与"掌掴"；
4. 删掉 `n04`（听香模样，`fact_refs` 为空、不承载任何事实）；
5. 复合事实拆开（f02→2、f08→3、f09→3）；
6. 一条非逐字旁白修正（原文「…把她买下**，**」曾被写成「…把她买下**。**」）。

### 11.3 ⚠️ 必须知情的决定：删掉了 `source_revision_context`

`handfix_r5` 是完全合规的版本，但它**发不出核验**，被这一条挡住：

```
verify_source_revision_context
  来源修正不得增删事实、改身份或改写未授权的已成立主张
```

`source_revision.py:admission_problems` 要求 `set(baseline_facts) == set(frozen_facts)`，
而 `source_revision_context.targets` 只授权 `[f02, f04, f08]`。拆事实必然增删 uid，
**f09 还不在授权名单里**。这是三条规定互斥造成的死结：

- 规范要求「一条事实只写一件事」，复合事实必须拆；
- 核验上下文每个事实**只挂一条**载体正文，不拆就必然 partial；
- 单镜上限 12 s，复合事实的完整载体装不进一条旁白/台词；
- 而 `source_revision_context` 禁止增删事实。

`handfix_r6` 的做法是**移除该字段**，让候选以「一份新计划」而非「来源修正的产物」身份
接受**完整重新核验**（15 条事实全部重核，保护强度不低于原来的 `source_preservation`）。
但这条决定绕过了原有授权约束，**需要产品侧裁决**：

- 若接受「拆事实 = 新计划，重新完整核验」⇒ 本包可结案，`final_plan.json` 可用；
- 若不接受 ⇒ 应回到 `handfix_r5`，并把「复合事实如何承载」写成明确的规格补丁
  （例如允许一条事实挂多条载体），否则这个包会一直卡在同一个地方。

### 11.4 成本

真实调用共 **1 次**（r6 的核验）。r5 因输入准入失败**零发送**。全程未发修订调用。

### 11.5 遗留

- `plan_feasibility.py` 中 `return problems` 之后的重复 `if verification is None: ...`
  仍是死代码（§6），建议单独立包。
- 候选落盘时不清除失败的 `plan_verification`（§2 的根因），建议在落盘处剥离
  `status != ok` 的记录。
