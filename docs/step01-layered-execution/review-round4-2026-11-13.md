# 复核第四轮：显式承载关系与逐问题残余（2026-11-13）

基线 `9c63583`（含 `530fdbb`）。本轮只做复核点名的两处补修，不扩大范围。

## 1. P4：用显式绑定替换文本相似度

### 问题

第三轮的二字组合关联仍会漏判：加一镜「城门上方一只鸟飞过」，它与结果共享地点
「城门」，被当成相关过程，`time_ellipsis_lost` 消失。共同地点不足以证明镜头承载了
送信/被捕这类必要关系；这也违反项目「无唯一映射时不得用相似度猜关系」的约定。

### 改动

`shared/execution_semantics.py`：

- **删除**二字组合与停用词表（`_content_bigrams` / `_shares_content` /
  `_LINK_STOP_BIGRAMS` 全部移除）。
- `_process_carriers` 改为按**显式 `plan_refs`**（事件履约契约的既有字段：
  `beats` / `lines` / `facts` / `events`）划分三组：
  - `bound_with_process`：已绑定且有过程载体；
  - `bound_without_process`：已绑定但无过程；
  - `unbound`：没有显式绑定。
- 判定：
  - `binding_status == "unbound"` → 只输出 `causal_gap`（advisory，
    `needs_semantic_review=true`）：**不豁免**（不能拿它证明过程存在），
    **也不认定缺失**（不能拿匹配失败当证据）。
  - `bound` 且 `bound_with_process` 为空、已绑定镜头内无时间标记、结果已断言完成
    → 才输出 `time_ellipsis_lost`（reject）。
- 时间标记按**镜头范围**核对：`_has_temporal_marker(script, shot_indices=...)` 只统计
  已绑定镜头的正文与 `context_bridge`；章节级 `narration_lines` / `main_events` 仍计入。

### 受控样本

`tests/shared/fixtures/execution_contract/temporal_ellipsis.json` 的两版脚本补上
`plan_refs`（`{"beats": [i], "lines": []}`）；变异版把剩余镜头的动作分段也去掉，
使「过程消失」在**已绑定镜头**里成立，`expected.codes` 仍为
`["causal_gap", "time_ellipsis_lost"]`。

### 对照（`tests/shared/test_execution_semantics.py`）

| 用例 | 断言 |
|---|---|
| `test_unbound_carrier_is_pending_not_exempt_and_not_rejected` | 无绑定 → 只有 `causal_gap`，`binding_status=unbound`、`needs_semantic_review=true` |
| `test_unrelated_process_shot_does_not_change_an_unbound_verdict` | 加一镜**共享地点**的鸟（无 `plan_refs`）→ 裁决不变，`carrier_evidence=[]` |
| `test_bound_carrier_with_process_exempts_the_reject` | 已绑定且有过程 → 只留 advisory |
| `test_bound_without_process_still_rejects` | 已绑定但无过程、无标记 → 仍 reject |
| `test_temporal_marker_outside_the_bound_shots_does_not_exempt` | 未绑定镜头里的「次日」不影响裁决 |

**未覆盖（明确记录）**：主体替换与否定仍无法由确定性层判断——没有「主体↔事实」的
显式绑定，这类样本落在 `needs_semantic_review`，不冒充已判定。

## 2. M02：逐问题残余（已接受变化保留 ≠ 问题解决）

### 问题

第三轮的「7/7」只证明七处**变化**在投影后保留，没有证明七条**缺陷**消除。
候选里仍有明确残余：镜 7 的 `ff_desc` 改成「双手空空」、`prompt` 仍写「右手反握短刃」；
`lf_desc` 改成酒壶落甲板、`prompt` 仍写「右手酒壶倾倒」。

### 改动

1. **授权面显式扩大**（`repair_fields.allowed_repair_fields`）：模型审计点名
   **帧描述**（`ff_desc` / `lf_desc` / `keyframes[N].desc`）时，同镜聚合 `prompt`
   一并纳入授权。`prompt` 是逐帧描述与动作的聚合稿，只改描述会让下游请求继续带着
   矛盾。台账 `authorization.beyond_declared` 逐条记录这次扩大；`motion_desc`
   等跨帧聚合字段仍不放开。
2. **逐问题残余检查**（`tools/build_m02_u6_candidate.py` +
   `fixtures/u6_confirmed_issues.json` 的 `residual_probe`）：对每条确认缺陷，在
   `prompt` / `ff_desc` / `lf_desc` / `motion_desc` / `frame_layouts` **全部相关字段**
   上重新扫描探针，逐条给出「已消除 / 仍有残余」与「本轮是否入轮」。

### 新读数

| 问题 | 镜头 | 探针 | 结论 | 入轮 |
|---|---|---|---|---|
| u6-02 酒壶回手 | 7 | `右手酒壶` | 已消除 | 是 |
| u6-04 首帧竖指 | 11 | `食指竖于唇前` | 已消除 | 是 |
| u6-05 大特写 | 1 | `大特写` | 已消除 | 是 |
| u6-06 括号重复 | 6 | `寒光是独立物体`（≤1） | 已消除 | 是 |
| u6-10 刺客握刃 | 7 | `反握`+`短刃` | 已消除 | 是 |
| u6-08 披头散发 | 10 | `披头散发` | **仍有残余** | **未入轮** |
| u6-09 凭空提壶 | 4 | `右手提青瓷酒壶` | **仍有残余** | **未入轮** |

**5 条已消除，2 条仍有残余**；两条残余都是容量上限（4 镜/轮）下**未入轮**的镜头，
按「未处理」记账，不是「已修复」。

同时保留第三轮的两栏：`postprocess_reverted` 为空、投影后 7 处变化全部成立。

## 3. P2 与授权链（复核已接受，按记录保持）

- P2：源码接线已核对（`verify_plan_changes` / `verify_plan_coverage` / 缺失即拒收），
  完成状态按执行报告的测试读数记录；**最终参考清单 → provider prepare** 仍是
  未完成项。
- 授权链：`s06_ab` = `declared_in_report`（8 次），`s06_ab_rerun` = `unverified`
  （20 次）；授权合规性核实仍待凭证。

## 4. 本轮验证

```powershell
python -m pytest tests/step01_script_gen tests/shared tests/step02a_frame_gen -q
# 读数见执行报告
python tools/build_m02_u6_candidate.py
# 5 条探针已消除 / 2 条未入轮残余；postprocess_reverted={}；投影 7/7
```

## 5. 下一步（按复核给出的顺序）

1. 素材导入工具（来源/许可/摘要/用途/继承与排除/派生/候选态）。
2. 最终参考清单 → provider prepare 的对应（三分支）。
3. 声音「准备态 / 视频后确认态」两态。
4. 发布中断、并发与恢复验证。
5. 统一提交门禁切换评阅；视频模型评估继续后置。
