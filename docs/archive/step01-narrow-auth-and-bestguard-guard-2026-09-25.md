# step01 调整留档：缺人授权收窄 / BEST-GUARD 非分镜闸门 / 欠账台账双记 / 道具释放锚点（2026-09-25）

本档记录 2026-09-25 对 step01 的 6 项调整。全仓测试：**起点 2198 passed / 1 skipped → 结束
2248 passed / 1 skipped**（新增 50 条）。所有改动均已落地于工作树（未提交）。

---

## 1. `kf_person_missing_in_layout` 授权收窄到「点名到帧」

- **文件**：`step01_script_gen/quality_issues.py::_kf_person_visibility_issues`、
  `step01_script_gen/repair_fields.py`
- **根因**：该 code 曾被登记进 `_KF_STRUCTURAL_CODES`，于是授权面包含 `keyframes` 与
  `frame_layouts.kfs` **整张列表** —— 只报 kf0 缺人时，kf1 的描述与布局同样可改（用户两帧
  最小样例复现）。
- **改动**：检测器自带精确 `repair_targets`＝`keyframes[i].desc` + `frame_layouts.kfs[i]`；
  code 从 `_KF_STRUCTURAL_CODES` 与 `_REPAIR_CODE_FIELDS` **双双移除**（唯一授权来源＝成对
  目标；缺成对目标即 fail-closed）。
- **判别性取证**：旧登记 `allowed=['frame_layouts.kfs','frame_layouts.kfs.0','keyframes',
  'keyframes.0.desc']` → `kf1.desc 可改? True`、`kfs[1] 可改? True`；新登记
  `['frame_layouts.kfs.0','keyframes.0.desc']` → 两者 `False`。
- **生产验证**（run `20260925T193736-47236`）：`shot 4: 未授权路径已回填 1 处:
  ['frame_layouts.kfs.1']` —— 相邻帧被构造性挡住。
- **影响面**：只影响该 code 的授权面（更窄）；不影响其它 code。

## 2. 坏 `kfs` 行导致的帧错位

- **文件**：`quality_issues.py::_kf_person_visibility_issues`
- **根因**：原实现先 `[row for row in kfs if isinstance(row, list)]` 过滤再按序号取帧 ——
  `kfs` 里混进非列表行时，其后的帧整体前移一位，`kfs[i]` 与 `keyframes[i]` 错位（漏真帧）。
- **改动**：按**原始下标**对齐；非列表行＝该帧无有效条目，照常判缺人（坏数据不是「没问题」）。
- **判别性取证**：旧实现遇坏行 → `[]`（漏报）；新实现 → 命中 kf0。
- **影响面**：只影响该检测器在坏数据下的结果（更准）；行列结构未动。

## 3. BEST-GUARD：非分镜稿不得进入候选池（+ 诊断产物目录隔离）

- **文件**：`step01_script_gen/main.py::_non_script_snapshot_reason`（新增）、
  `tools/run_step01_final_audit.py::diag_out_base`（新增）
- **根因（两层）**：① 我的审计诊断产物沿用 `<稿名>.audit.*.json` 命名，而稿名以
  `script_result_` 开头 → 落进候选 glob `snapshots/script_result_*.json`；② 非分镜稿在
  `_candidate_quality_summary` 下查不出任何 issue → `structural_ok=True`、`integrity_ok=True`、
  `风险=0` → `_quality_rank` 给出 `(0,0,0)`，**优于任何真稿**（真稿至少带 blocking 风险）。
- **现场**：run `20260925T191358-10744` 选中
  `script_result_20260925_173311.audit.compare.json` 为终稿 → 审计报 `empty_storyboard` +
  `missing_end_hook`（`repair_targets` 为空）→ 提交点 BLOCK。
- **改动**：① 候选**先验明正身再评分**（只校验最小结构：非空 `shots` 数组 + 元素是对象 +
  至少一个带 `index`/`prompt`），跳过时显式记录原因并写 `meta["invalid_candidates"]`；
  两处读快照的循环都过这道闸，**「全部欠账 → 放开限制」的兜底分支不放宽这一条**；
  ② 审计诊断默认输出迁到 `<稿目录>/diag/`（glob 非递归 → 彻底隔离），`--out` 仍可覆盖。
- **判别性取证**：拆掉闸门后同一份非分镜稿立刻凭「风险 0」夺冠（测试复现 09-25 19:14 现场）。
- **文件搬迁**：7 份非分镜稿移入 `snapshots/diag/`（保留原名、未删除）；移动后候选池
  = 13 份真稿 / 0 份非分镜稿。引用同步：`diag_audit_facts_adjudicated_20260925.json` 的
  `per_run[].audit_file` 改指 `diag/<原文件名>`。
- **影响面**：选稿候选集合与诊断产物落点变化；真稿的评分/排序逻辑未动。

## 4. 欠账台账：BLOCK 时同时登记「进入提交点的原稿」revision

- **文件**：`step01_script_gen/main.py::_commit_step01_outputs`
- **根因**：台账只记 `ensure_script_revision(script)`（修后 revision）。实测 run
  `20260925T193736-47236`：台账新增修后的 `1d1cb454`，而被 BEST-GUARD 选中的
  `script_result_20260924_215530.json` 自身 revision `f434e5090d7b` **不在台账内** →
  下一轮选稿照样能再选它、再 BLOCK。
- **改动**：入口捕获 `_entry_revision`（纯计算，不改稿，写进 `summary["entry_revision"]`）；
  BLOCK 分支在登记修后 revision 之外**同时**登记原稿 revision（相等时只记一条），并打显式日志。
- **实跑证据**（测试日志原文）：`[COMMIT] 欠账台账同时登记进入提交点的原稿 revision
  fd5cafc22d07（修后 575c3b5b780e）`。
- **刻意不做**：不宣称永久解决——「全部候选都欠账 → 放开限制」的兜底分支仍会把它放回候选
  （有意保留，否则护栏整个失效）。代码注释与测试 docstring 均已写明。
- **回归**：`test_block_records_both_entry_and_repaired_revision`（提交点）、
  `test_blocked_revision_snapshot_is_skipped_in_selection`（选稿，含判别性对照）。
- **影响面**：仅台账内容（多记一条 revision）与一行日志；选稿的过滤口径不变。

## 5. 纠正「审计目标指错条目」的取证（不按错误结论改校验器）

- **文件**：`tests/step01_script_gen/test_prop_state_anchor.py`
- **纠正**：我曾在测试里手工造七个 `frame_layouts.ff[0].props`，据此断言「审计 7/7 指错
  条目」——**该断言作废**，那是夹具造出来的。正式 `step01_audit.blocked.json` 的
  `model_audit_PROP_NOT_RELEASED` 写的是 `ff[1]/[2]/[1]/[2]/[2]/[2]/[3]`，与稿件实际条目
  下标**完全一致**。
- **真实缺口（用真实 payload 复算）**：持有字段共 15 处 —— **ff 侧 7 处全覆盖、lf 侧 8 处
  全未覆盖**（shot3 `lf.props` + shot4–10 各自 `lf.props`）。
- **改动**：改为 `test_real_audit_payload_covers_the_ff_side_only`（读真实 payload 做覆盖率
  回归）；另留一条**明确标注为合成**的条目下标用例，仅用于钉住 helper 的条目级判据。
- **影响面**：仅测试；`validate_repair_targets` **未改动**（用户决定：不据错误取证改校验器）。

## 6. `prop_state_anchor.py`：道具释放锚点（离线阶段，**未接线**）

- **文件**：`step01_script_gen/prop_state_anchor.py`（新增）、
  `tests/step01_script_gen/test_prop_state_anchor.py`（新增，29 条）
- **根因**：原文写明释放（「咬钩的肥鱼便拖着那钓杆急急逃走了」）但大纲没记、稿件没写；
  现有 `cross_field_checks._possession_issues` **只在描述已出现释放词时**才比对布局 →
  报 0 条；真矛盾只由最终审计发现，而审计把它声明成 8 镜联动（超 6 镜上限 → 立即 BLOCK）。
- **内容**：
  - 有词表路径（`prop_assets` 的 `consistent_terms` 提供别名，如 钓杆/鱼杆）：
    `extract_prop_state_changes` / `missing_release_issue`（`repairable=False`、**不给**
    repair_targets —— 禁止「删 props 伪造剧情」）/ `post_release_possession_issues`
    （`ff`/`lf`/`kfs` 一视同仁）/ `uncovered_holding_fields`（授权覆盖率）。
  - **无词表**路径（v6.125.1，大纲阶段用）：`extract_release_candidates` 产出**候选锚点**
    （`kind="release_candidate"`、`confidence="candidate"`，带原文证据与位置
    `sentence_index`/`char_start`/`char_end`）；`match_candidates_to_props` 仅在**已有**词表
    时归并，未命中留 `prop=None`（不猜身份）。
- **精度实测**（`锦衣夜行_77119小说.txt`）：

  | 语料 | 字数 | 候选 | 挡下 |
  |---|---|---|---|
  | 本章（第1章） | 5,074 | 3（酒囊/钓杆/灯盏，**全真**） | 0 |
  | 同一部前5章 | 24,653 | 3（本章那 3 条） | 3（空名/刀/去） |
  | 抽样A 每 60 章取 1（32 章） | 65,165 | 4（章外仅 1：蒲扇＝真） | 6（戒备/小狗/出去/羊/肖祖杰的/心来） |
  | 抽样B 后 100 章每 20 章取 1 | 24,699 | **0** | 3（东西/心来/’呢） |

  → **章外 ≈10 万字只有 1 条候选，且是真**（误提 0/1）。
- **已知漏提（有意，已钉进回归）**：`marker_before` 形状（「<器物>脱手」）停用；单字道具
  （「刀」）、活物/抽象宾语一律漏。即**召回换精度**。
- **影响面**：**零**——本模块**未接线**（未接大纲、提示词、BEST-GUARD、质量门），不改任何
  生产路径；本轮亦未调用模型。
- **接线前必补**（用户口径）：`prop_release_missing(repair_targets=[])` **不会**自动经过只遍历
  审计 payload 的空目标契约门；接入质量门时必须显式为它设置「不可定向修复 → 拒绝候选/重生」
  的分支 —— 离线测试通过 ≠ 生产 fail-closed 已成立。

---

## 回退方式

| 项 | 回退 |
|---|---|
| 1、2 | 还原 `quality_issues.py::_kf_person_visibility_issues` 与 `repair_fields.py` 的两处登记/移除（单文件、无迁移） |
| 3 | 删除 `_non_script_snapshot_reason` 的两处调用与 `diag_out_base` 的默认分支；把 `snapshots/diag/` 下 7 份文件移回原位 |
| 4 | 删除 `_entry_revision` 的捕获与 BLOCK 分支的第二次 `_record_blocked_revision` 调用 |
| 5 | 仅测试，无生产回退 |
| 6 | 删除新增模块与测试（无调用方） |

## 相关的用户决定（本次未改）

联动组上限保持 **6**、普通单轮 **4**、提交点修复轮 **2**；`temporal_plan` 维持可选 + 保留统计；
`check_replacement_authorized` 的 `_norm` 折叠（纵深防御比 merge 松）不作为本次前置；
BEST-GUARD 分档顺序（structural → integrity → 风险 → 分）保持。
