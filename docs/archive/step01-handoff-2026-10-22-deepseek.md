# step01 / d11 交接给 DeepSeek Harness（2026-10-22 14:25）

> 本文件是**唯一权威交接**。上游已完成的部分不要重做；只做 §4 的三项。

---

## 1. 一句话状态

`step01_ab/20261018T135622` 这一包**已通过**：`final_acceptance.codes == []`。
剩下的不是"再修一遍计划"，而是**三个代码/规格层面的缺陷**，它们让这个包在同一处反复卡死了 4 轮。

---

## 2. 已完成，禁止重做

| 项 | 值 |
|---|---|
| 交付候选 | `output/step01_ab/20261018T135622/d11_run/handfix_r6/final_plan.json` |
| 核验记录 | `output/step01_ab/20261018T135622/d11_run/verify_r6/verification_record.json`（`status=ok`） |
| `final_acceptance.codes` | `[]` |
| 声音总估时 | **84.85 s**（上限 90） |
| 最长单条 | **8.70 s**（单镜上限 12） |
| 结构 | 事实 15 / 旁白 10 / 对白 4 / 节拍 16 |
| 核验覆盖 | `source_support` 15 条、`voice_units` 14 条，全部 supported |
| 真实调用 | `attempts=1 transmitted=1 blocked=0`（本包只花了 1 次） |

计划的演进链：`real_rounds3`（6 条问题）→ `handfix_r1`（槽位互换+补旁白，85.45s）→ `handfix_r2`（作废，123.43s 超预算）→ `handfix_r3`（拆 14 条事实）→ `handfix_r4`（拆回单镜 ≤12s，15 条事实）→ `handfix_r5`（修逐字性，**合规但发不出去**）→ `handfix_r6`（去掉 `source_revision_context` 后发出，0 问题）。

---

## 3. 已查清的三个根因

### 3.1 核验上下文是**单载体**的

`verification_context_of(plan, view)` 给每个事实只挂 `required_facts` 里「承载：Bn/旁白k」指向的**那一条**正文，**不聚合多条**。

证据：`d11_run/verify_rounds3_verify/calls/verify_archive.jsonl` 里发给模型的 prompt，`## 事实 f02` 段「实际展示正文」下只有一行 `n02`。

后果：给复合事实补第二条旁白是**无效加字**——那条旁白进不了核验上下文，判据照样 partial。

### 3.2 复合事实必须拆，而且规范本来就要求拆

`step01_script_gen/outline.py:76-83` 明写「一条事实只写一件事」，表达不了就各自另立一条。

f02 / f08 / f09 都是"几件事并成一条"，是这 6 条问题的共同源头。

### 3.3 单镜上限 12 秒是硬判据

把两句合并成原文完整整句会到 12.40 s，被判 `budget_unrepresentable`。
「旁白要含句末标点」只是写作建议，**不是**判据；r1 里那些无句末标点的旁白能过确定性验收。

### 3.4 `verify_bad_response` 是假警报（但不是无害）

`real_rounds3/candidate_plan.json` 里残留了一条 `status=bad_response` 的核验记录（响应是 `NoneType`）。
运行器把它列入白名单、排除在门控之外，所以它只污染日志。

**根因在落盘侧**：`output/step01_ab/20261018T135622/tools/d11_run2.py:241`

```python
if rec:
    (RUN / 'verification_record.json').write_text(...)
    probs = o.plan_acceptance_problems(dict(final, plan_verification=rec), NOVEL) or []
```

`if rec:` 不看 `rec['status']`。一条失败记录照样被挂到计划上，下一个消费者就把它当"已有核验记录"读，于是要么被误报、要么把真正的原因盖住。

---

## 4. 待修三项

### T1 —— 删死代码（无争议，零风险）

`step01_script_gen/plan_feasibility.py`

- 活代码：`5153-5176`（`verification_context_of` 两模式都跑 → `require_verification` 早退 → `if verification is None:` / `elif not _ctx_bad:` → `return problems`）
- **死代码：`5177-5204`**（`return problems` 之后的一段旧版重复实现，永远不执行）

要求：删除 5177–5204，只删不改语义。删完 `return problems` 应是该函数最后一个语句。

### T2 —— 落盘过滤失败记录

`output/step01_ab/20261018T135622/tools/d11_run2.py:241`

要求：
1. 只有 `rec['status'] == 'ok'` 才挂 `plan_verification`；
2. 非 ok 记录另存为 `verification_record.rejected.json`，并在 `run_log['verify']` 里记 `rejected_status`，**不许静默丢弃**；
3. 同样的问题如果在**产品侧**（候选定稿/缓存装载）也存在，一并修——症状是"候选落盘后带着一条 `status != ok` 的 `plan_verification`"。

先写一个**会失败**的回归测试复现它，再修。

### T3 —— 规格冲突（这一项要先复现再做，且不许静默放宽）

`step01_script_gen/source_revision.py:56-58` 的判据是：

```python
if (len(baseline) != len(old) or set(baseline) != set(current)
        or not set(ctx['targets']).issubset(baseline)
        or any(current[u] != claim for u, claim in baseline.items() if u not in ctx['targets'])):
```

含义：**事实 uid 集合必须与基线完全一致**；不在 `targets` 里的事实，`claim` 必须一字不动。

于是三条要求互斥：

| 来源 | 要求 |
|---|---|
| `outline.py:76-83` | 复合事实必须拆成多条 |
| 核验上下文（§3.1） | 每条事实只挂一条载体，不拆必 partial |
| `source_revision.py:56-58` | uid 集合必须不变 ⇒ **拆不了** |

实证：`handfix_r5` 结构完全合规，但**一次调用都发不出去**——输入准入直接 `verify_input_rejected`（`_request_admission` → `verify_source_revision_context`），transmitted=0。

上一轮的处理是**把 `source_revision_context` 整段移除**，让候选以「新计划」身份接受完整重核（`handfix_r6`），这才发出核验并 0 问题。这条路能跑通，但它**绕过了来源修正授权**——不是可以留着不管的做法。

要求：
1. 先写失败测试，把上述互斥固定下来（输入：一份需要拆 f09 的 plan + 带 `targets=[f02,f04,f08]` 的 ctx，期望当前报 `source_preservation_failed`）；
2. 然后给出**版本化**的解法，二选一（或两者）：
   - a) `source_revision_context.version` 升版，允许 `targets` 域内的事实**拆分为子 uid**，子 uid 继承父授权；旧版本行为完全不变；
   - b) 让 `verification_context_of` 支持**多载体**：聚合所有 `fact_refs` 指向该事实、或承载声明指向该事实的正文。
3. **禁止**直接把 `set(baseline)==set(current)` 删掉或放宽成"允许任意增删"——那会让来源修正授权彻底失效。
4. 改完必须跑 `tests/` 里 step01 与 R0 相关回归（基线是 3483 passed / 28 passed）。

选 a 还是 b、以及要不要为已验证的 r6 补做一次"带 ctx 的合规复核"，属产品标准决定，**在报告里提出建议即可，不要自己拍板执行**。

---

## 5. 边界与禁令

- **禁止发放任何真实模型调用**。尤其不要为 T1/T2 跑核验。真实调用成本由用户单独决定。
- 冻结输入只读：`d9_feasibility/input/{plan.json,novel.txt,problems.json}`
- 只读对照：`real_rounds3/`、`verify_rounds3_verify/`、`handfix_r1..r5/`、`verify_r5/`、`verify_r6/`
- 不新建 `dN_*.py` 运行器；运行器只有 `tools/d11_run2.py`
- 禁止改 `handfix_r6/final_plan.json`（它是已通过的交付物）
- 沙箱内代理是 `127.0.0.1:9`，出不了网；别把时间花在重试网络上

---

## 6. 出口判据

三条全中才算完成：

1. `plan_feasibility.py` 中 `return problems` 之后无死代码，`python -c "import step01_script_gen.plan_feasibility"` 正常
2. T2 的失败测试先红后绿；`run_log` 在拿到失败记录时会写出 `rejected_status` 而不是静默挂载
3. T3 的失败测试先红后绿，且 step01 + R0 回归全绿（3483 / 28）

报告中必须含：改了哪些文件哪些行、两个新测试的名字与红→绿证据、回归的 passed 数、以及 T3 你建议选 a 还是 b 及理由。

---

## 7. 如果要重发核验（仅备查，需用户批准后才可执行）

```bash
cd C:/myFloders/git/aiVideo
D11_STAGE=verify D11_CASE=<新case> D11_CAP=1 \
D11_BASE="output/step01_ab/20261018T135622/d11_run/handfix_r6/handfixed_plan.json" \
python output/step01_ab/20261018T135622/tools/d11_run2.py
```

每次核验前先跑 `_request_admission(view)` 自检，准入不过就是零成本失败，别直接发。

---

## 8. 复核结果（2026-10-22 14:50，独立验证，不采信自述）

### 8.1 T1 通过

`plan_feasibility.py` 中 `return problems` 现为 `presentation_records()` 最后一个语句，后面直接是
`def _reading_budget_sec()`；`import step01_script_gen.plan_feasibility` 正常。
回归测试 `test_plan_feasibility_has_no_top_level_statements_after_return` 用 AST 扫描全文件，
任何函数顶层 `return` 之后再有语句即失败——这条能防同类复发。

### 8.2 T2 通过

- `content_verify.verification_record_delivery()` 纯判定，`deliver_verification_record()` 负责落盘：
  `ok` → `verification_record.json`；其余 → `verification_record.rejected.json`，
  并且**互删另一种文件**（不留上一轮的规范记录冒充本轮）。
- `attach_verification()` 只在 `status == "ok"` 时写 `plan_verification`，失败记录挂
  `verification_rejected`。
- `outline.plan_acceptance_problems` 读到 `verification_rejected` 时**先删掉
  `verify_record_missing`**，再按失败 code fail-closed——这正好修掉"失败被吞成缺记录"的次生问题。
- `outline.py:3330` 附近：有失败记录直接返回 `verification_failed`，不触发重规划。
- 运行器 `:244-260` 用 `deliver_verification_record`，失败时 `run_log['final_acceptance']`
  带 `rejected_status` / `rejected_code`。
- 5 个用例全绿（`test_verification_record_delivery.py`）。

### 8.3 T3 通过（v1 行为逐字未变）

`source_revision.py` 新增 `VERSION_V2='source_revision.v2'`，`SUPPORTED_VERSIONS=(v1,v2)`。
`admission_problems()` 里 **v1 分支的判据与原文逐字一致**（`set(baseline)!=set(current)`
`or not targets⊆baseline or any(current[u]!=claim for u not in targets)`），v2 才走
`_v2_identity_reason()`。所以老计划的准入行为没有被放宽。
v2 只放开"targets 内父 uid → ≥2 个 `父uid.后缀` 子 uid"，非 targets 事实仍须原样保留。

### 8.4 回归（我自己跑的，注意环境）

| 命令 | 结果 |
|---|---|
| `py -3.14 -m pytest tests/step01_script_gen`（`--basetemp` 到仓库内） | **3500 passed**, 277 skipped, 4 xfailed, 0 failed |
| `tests/shared/test_delta_reconcile_r0.py` | **28 passed** |
| `tests/step01_script_gen/{test_verification_record_delivery,test_source_revision}.py` | **46 passed** |

**两个环境坑（下次复核别踩）**：

1. 用 WorkBuddy 的隔离 venv 跑会 **129 failed**，原因全是 `No module named 'tenacity'`（125 条）
   与 `'yaml'`（4 条）——不是代码问题。必须用系统 `py -3.14`
   （`C:\Users\ee54\AppData\Local\Python\pythoncore-3.14-64\python.exe`）。
2. 不加 `--basetemp` 时，teardown 清理 `%TEMP%\pytest-of-ee54\garbage-*`（429 个）会触发
   安全钩子 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`，**进程被中断，pytest 不打汇总行**，
   看起来像"失败了但没原因"。加 `--basetemp=<仓库内目录>` 才有汇总。

### 8.5 仍然开着的事

1. **两个新测试文件不在版本控制里**：`.gitignore:125` 是 `tests/step01_script_gen/*` 全忽略，
   靠后面的 `!tests/step01_script_gen/xxx.py` 白名单逐条放行。`test_verification_record_delivery.py`
   与 `test_source_revision.py` 都还没进白名单，**干净 clone 会丢掉这两组契约测试**，
   修好的行为下次可能被静默改回去。要补两行白名单（属于仓库策略，未擅自改）。
2. **运行器的 `rejected_status` 路径只有单元测试覆盖**，没有一次离线实跑——沙箱下
   `d11_run2.py` 在 `output/.../d11_run` 新建 case 目录即 `PermissionError`，零传输。
   真机重发前建议先在一个可写目录跑一次 `D11_CASE=dryrun` 验证落盘分支。
3. **工作树里仍混着 10-21 的未提交改动**（`outline.py`、`presentation_contract.py`、
   `docs/archive/step01-verification-baseline-2026-10-17.md`、`shared/delta_reconcile.py` 等），
   本轮改动叠加在其上，没有回滚也没有提交。提交前需要人工分辨哪些属于哪一轮。
4. **T3 的产品决策仍未拍板**：建议选 b（多载体核验上下文）为主、a（v2 拆分）作兼容层。
   `handfix_r6` 目前是"去掉 ctx 当新计划"的产物；若要求保留来源修正身份，
   得在 b 落地后重发 1 次带 ctx 的核验。
