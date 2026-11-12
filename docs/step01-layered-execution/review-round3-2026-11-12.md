# 复核第三轮：正确性缺口与承诺项收口（2026-11-12）

针对复核意见的五项，逐条核对与处置。基线 `1237553`（含 `ae26d11`、`38217a4`）；
本轮新增改动见 §6。

## 1. P4 的过宽豁免（已修）

**复核反例成立且已本地复现**：原输入返回 `time_ellipsis_lost / reject`，只增加一个
无关镜头（关键帧画一只鸟），原问题不变而拒收消失——旧 `_process_carriers` 把**全稿
任意**关键帧/动作分段当成过程证据。

### 改动

`shared/execution_semantics.py`：

- `_process_carriers(script, anchors=...)` 现在返回 `(linked, unlinked)`：
  载体镜的文本必须与 `causal_chain` 的**起因/结果**条目共享至少一个内容 2 字组合
  （`_content_bigrams` + 停用组合），才算「与被质疑的因果相关」。
- 只有 `linked` 能豁免 `time_ellipsis_lost`；`unlinked` 记进
  `unlinked_carriers`（**待语义裁决**），裁决与「完全没有载体」时一致。

### 证据

`tests/shared/test_execution_semantics.py` 新增两条：

| 用例 | 断言 |
|---|---|
| `test_causal_verdict_is_not_changed_by_an_unrelated_process_shot` | 加无关鸟镜后 codes 仍为 `["causal_gap","time_ellipsis_lost"]`；`carrier_evidence=[]`、`unlinked_carriers=["shot:2"]` |
| `test_related_process_shot_does_exempt_the_reject` | 相关过程镜（与「城门」共享内容）→ 只留 `causal_gap`，`carrier_evidence=["shot:2"]` |

判别力范围因此从「已测变异」扩到「变异 + 无关载体不得改变裁决」。

## 2. P2 的保护与覆盖（已修）

### 2.1 自洽不能证明合法

- `step02a_frame_gen/execution_plan.py` 新增 `verify_plan_changes(plan, script)`：
  把计划代表的合并结果还原成一份稿件，交给
  `shared.execution_contract.validate_execution_changes` 逐路径判定
  （受保护字段 / 需语义证据 / 越界 / 缺理由）。
- `build_visual_plan` 在候选存在时把实际变化写进 `binding["changes"]`
  （差异用新增的 `diff_script_paths`，与校验同一实现），否则每条合法变化都会被判
  `change_reason_missing`。
- 顺带修一处副作用：`planned` 现在**始终**经 `apply_execution_candidate` 深拷贝，
  计划构建不再把 `_scene_match` 这类运行期缓存写回调用方稿件。

### 2.2 计划外镜头不得回退原稿

- 新增 `verify_plan_coverage(plan, only_indices=..., frames=...)`：请求的镜头与帧
  必须在计划里。
- `gen_all_frames` 在计划模式（`scope` 给出）下：缺单元直接 `SystemExit(2)`，
  钩子同样处理；任务构造不再有「拿原稿顶替」的路径。

### 证据

`tests/step02a_frame_gen/test_m05_plan_consumption.py`（6 条）：

| 用例 | 断言 |
|---|---|
| `test_generation_consumes_the_planned_execution_unit` | 合法执行字段（`prompt`）改动 → 桩生成器收到计划值，原稿未被污染 |
| `test_protected_field_change_is_refused_even_if_plan_is_self_consistent` | 单元与提示一起改（自洽）→ 仍被拒（`protected_field_change` / `semantic_change_unverified`），零调用 |
| `test_requested_shot_outside_the_plan_is_refused` | 请求计划外镜头 → `plan_coverage_missing_shot`，零调用 |
| `test_plan_prompt_drift_is_refused_before_send` | 计划提示漂移 → 拒收，零调用 |
| `test_missing_reference_file_is_reported` | 参考缺失 → `plan_reference_missing` |
| `test_plan_without_execution_block_is_refused` | 旧计划缺执行参数 → `plan_execution_missing` |

## 3. M02 投影后仍成立（已修）

### 3.1 两处「后处理撤销修复」都已定位并修掉

| 现象 | 生产者 | 修法 |
|---|---|---|
| 镜 1 的 prompt 被还原成「大特写」 | `validation._shot_size_level` 按词表顺序取第一个**子串**命中（`远景` 先于 `大远景`），`framing_jump` 随后 `replace("远景","特写")` 恰好还原原值 | 改为**最长匹配**；`framing_jump` 在修复轮内跳过被授权镜头、在 final pass 只报不改 |
| 镜 6 的重复括号被加回去 | `constraints/v6_43_prompt_enforcement._fix_vague_anchor` 无条件追加「（X是独立物体…）」 | 追加前看紧邻文本是否已有「独立物体」；幂等 |

另把 `ConstraintContext.repair_shot_indices` 接进 `run_hard_fix`，修复轮的授权镜头
集合从 `picked` 传入约束层。

### 3.2 布局列表也进投影后差异检查

`main._authorized_changes_lost` 改用**保留下标**的扁平化（`_flatten_exact_paths`），
`frame_layouts.ff.0.props` 这类精确路径因此能参与比较——此前用 `*` 归一，布局侧
永远匹配不上，等于漏检。

### 3.3 U6 离线候选的新读数

| 项 | 修前 | 修后 |
|---|---|---|
| `accepted` | true | true |
| `audit_field_touched` | 3 | **5** |
| 实际变化字段 | 5 | **7**（镜 1/6 的 prompt 也落地） |
| `postprocess_reverted` | `{shot_1:[prompt], shot_6:[prompt]}` | **`{}`** |
| 投影后残余 | 未验证 | **7/7 全部成立** |

`artifacts/m02/diff.md` 现在带「投影后残余」一栏，逐字段记录。

## 4. 28 次发送的授权链（已补）

`tools/build_step01_call_index.py` 新增逐批 `authorization` 与逐调用 `budget_record`：

| 批次 | 授权来源 | 声明上限 | 实发 | 核实状态 |
|---|---|---|---|---|
| `s06_ab` | S06 报告 | 8 | 8 | `declared_in_report`（自述口径，独立凭证不在盘上） |
| `s06_ab_rerun` | **无** | **无** | 20 | `unverified` |

不能判定 20 次是否超出原八次额度，也不写成超限；每调用可关联通道/代码版本/
样本摘要，`budget_record.available=false` 并给出原因（该批未走预算凭据机制）。
原八次完整响应缺失的标注保持不变。

## 5. 剩余工作的状态（不再统一归入关口）

| 事项 | 状态 |
|---|---|
| 素材导入工具（来源/许可/摘要/用途/继承与排除/派生/候选态） | **本包未完成项** |
| 最终参考清单 → provider **prepare 结果**的对应（三分支） | **本包未完成项** |
| 声音「准备态 / 视频后确认态」两态拆分 | **本包未完成项** |
| 发布中断、并发与恢复验证（`M08` 原承诺） | **按原承诺补齐**（未做） |
| 一镜多片段 | **独立施工项**；一镜一片段先交付 |
| 正式门禁切换（`issue_ledger.closed` 接线、`framing_jump` 规则去留） | **保留明确关口** |
| 视频模型评估 | **按用户要求后置** |

## 6. 本轮验证读数

```powershell
python -m pytest tests/step01_script_gen tests/shared tests/step02a_frame_gen -q
# -> 5745 passed / 283 skipped / 4 xfailed / 0 failed
python -m pytest tests/step01_script_gen/constraints/test_m02_postprocess_no_revert.py -q
# -> 6 passed
python tools/build_step01_call_index.py     # 8 + 20 次，含授权链
python tools/build_m02_u6_candidate.py      # touched=5, 7 处变化, reverted={}, 投影 7/7
```

视频侧仍有 2 条**既有**失败（已在 HEAD `b942098` 上复现），与本轮改动无关。
