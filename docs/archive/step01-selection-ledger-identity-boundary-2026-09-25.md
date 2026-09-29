# step01 选稿台账的身份边界：快照自身 revision 贯通登记（v6.129，2026-09-25）

用户 2026-09-25 根因（原文摘要）：

> **旧稿反复入选：台账记错了身份。** BEST-GUARD 按快照原稿算出 `f434e509…`；选中后，
> 主流程用本轮大纲覆盖它，而 `chapter_outline` 参与 revision 哈希，于是**分镜一字未改**，
> 进入提交点时已变成 `380da4c1…`；修后是 `0fe1f2b2…`。BLOCK 只登记后两者，BEST-GUARD
> 下次仍用 `f434…` 查台账，所以漏拦。现有两条回归分别测提交点和选稿，**没有覆盖中间这次
> 大纲覆盖**。
>
> **8 镜 BLOCK：容量上限挡住了不完整的修法。**……**不应把组上限从 6 提到 8**：那只会放行
> 这份半修复。
>
> 最小修正方向是：在**选稿边界**保留快照原始 revision，BLOCK 时连同提交入口和修后
> revision 一起登记，并用"选中旧稿→覆盖大纲→BLOCK→下次跳过"的贯通测试验证。
> 道具问题则先维持 BLOCK，不扩容量、不接受只删 `props` 的修法。

---

## 一、改了什么

| 文件 | 改动 |
|---|---|
| `step01_script_gen/main.py` | ① `_cross_run_best_guard()` 选中历史快照时，把**快照自身**的 revision 与路径写进 `meta["selected_revision"]` / `meta["selected_path"]`（失败时记空串 + WARN，不影响选稿）；② `_commit_step01_outputs()` 新增 `source_revision` 参数，BLOCK 分支在原有"修后 + 提交入口"之外**再登记选稿边界那个 identity**（三者互不相同时各记一条，带各自的日志）；③ 主流程落盘前提交把 `meta["selected_revision"]` 传下去。 |
| `tests/step01_script_gen/test_step01_commit.py` | 新增**贯通回归** `test_selection_ledger_covers_the_snapshot_identity_boundary`：选中旧稿 → 覆盖本轮大纲（`chapter_outline`）→ BLOCK → 三个 identity 全在台账 → **下一次选稿跳过该稿、改选另一份**。 |
| `tests/step01_script_gen/test_prop_state_anchor.py` | 原"真实审计 payload 覆盖率"用例**重写**为 `test_real_audit_payload_targets_point_at_real_entries`（理由见下）。 |

**没改**（用户明确要求）：联动组容量（仍 6，**没有**提到 8）、道具问题的 BLOCK 现状、
"全部候选都有欠账就放开限制"的兜底分支、摘要 `props` 的修法口径。

## 二、为什么

1. **身份边界**：`chapter_outline` 在 `shared/quality_gate._stable_payload` 的哈希里。
   历史快照被选中后，主流程用**本轮大纲**覆盖 `script["chapter_outline"]` → 分镜一字未改，
   revision 却变了（实测 `f434e509` → `380da4c1` → `0fe1f2b2`）。guard 判欠账用的是
   **快照自身** revision，台账却只有后两者 → 同一份欠账稿可以被**反复**选中。
2. **两次回归各自的盲区**：一条测提交点（entry/修后），一条测选稿（按 revision 排除），
   中间这次大纲覆盖两边都不覆盖 → 需要一条**贯通**用例。
3. **原 payload 用例的写法不成立**：它固定断言"8 个目标 / ff 全盖 / lf 全漏"。实测目标
   集合**不稳定**——同一份旧稿，上一轮 8 个、本轮 16 个（已含部分 `lf`，还有重复项）。
   写死数字会让用例随运行结果漂移。改为钉**稳定契约**：每个目标必须解析到稿件里**真实
   存在的条目**（这才是当初被纠正的下标正确性）、覆盖率按实测算并报出、且声明里只有
   `props`/描述类字段——**"路径存在"不等于"剧情已保"**（今天第 2 点）。

## 三、实跑读数

| 项 | 读数 |
|---|---|
| 贯通用例 | 通过；三个 identity（`f434…` 选稿边界 / 提交入口 / 修后）**互不相同且全在台账** |
| 判别性（一次性脚本，非测试） | 不传 `source_revision` → 快照自身 revision **不进台账**；传了 → 进台账。证明该参数是**载荷性**的 |
| 同一脚本加固的另一个读数 | 只有**一份**候选时，即使补记了快照自身 revision，**下次仍会选中它**——命中"全部候选都有欠账 → 放开限制"兜底分支（用户已指出这是后续复发路径，本轮**未改**） |
| BLOCK 内容 | 未变：10 条 = 6 × `kf_person_missing_in_layout`(hard) + 4 × model_audit；其中 8 镜联动组超限走"显式 BLOCK"，**组容量保持 6** |
| 全量测试 | `pytest -q` → **2286 passed, 1 skipped** |

## 四、影响面

- **生产行为**：仅台账**多记一条**（快照自身 revision；台账上限仍 64 条，按序淘汰）。
  选稿、修复、门禁、容量、BLOCK 判定均未改动。
- 未接新的判据；未改 `props` 修法口径；未动兜底放开分支。
- 成本：无变化。

## 五、回退

- 还原三处：`meta["selected_revision"]` 的赋值、`_commit_step01_outputs` 的
  `source_revision` 参数与那段登记、主流程的传参；测试同步删除即可。台账里多出的那条
  只需删 `snapshots/_blocked_revisions.json` 中对应 revision（或整文件）。

## 六、仍未处理（等你定）

1. **兜底放开分支**：`全部候选都在台账内 → 不排除` 会把已 BLOCK 的旧稿放回候选（本轮实测
   可复现）。台账 64 条上限同样只是削弱而非消除复发。
2. **半修复口径**：审计给的 16 个目标只动 `props`/描述，不补原文"鱼拖走钓杆"的事件；
   旧稿 shot6 的 prompt 还写着"钓杆已脱手无力垂在船舷"且不在授权目标内。
   `validate_repair_targets` 只证明路径存在。**维持 BLOCK，不扩容量**。
