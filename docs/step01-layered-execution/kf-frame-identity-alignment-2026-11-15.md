# Step01 补帧后的帧来源对齐（S01–S04 补正交付记录）

- 日期：2026-11-15
- 依据：用户下发的《Step01 补帧后的布局身份对齐修复计划》与随后的
  《帧身份对齐修复补正计划》。**本文件是补正后的最终版本**，替换初版的
  「S04 已完成」结论与三处证据不足的判断。
- 状态：S01 / S02 完成，S03 完成，S04 完成到**可评阅离线交付**。
- 交付边界：**真实模型调用 0，正式产物写入 0，仓库未提交**，未改任何
  `.dsh/` 现场产物，未重跑真实全流程。

---

## 1. 基线、工作树与差异摘要

- HEAD：`88404b7cb49729dc082d8cc43941464a3c63c9f9`（本包全程未提交）
- 基线口径：`88404b7` + 初版交付留下的未提交改动；补正在此之上继续，
  **未 stash、未 checkout、未丢弃任何既有工作树内容**。
- `git status --porcelain`：5 个已跟踪文件修改（`M`），5 个新增文件（`??`）。

| 类型 | 路径 |
|---|---|
| M | `.gitignore` |
| M | `step01_script_gen/main.py` |
| M | `step01_script_gen/repair_fields.py` |
| M | `step01_script_gen/repair_effects.py` |
| M | `docs/step01-layered-execution/U7-consistency-contract-proposal-2026-11-15.md` |
| ?? | `tests/step01_script_gen/test_kf_frame_identity.py` |
| ?? | `tests/step01_script_gen/test_kf_frame_identity_transaction.py` |
| ?? | `tools/replay_run_h_frame_identity.py` |
| ?? | `docs/step01-layered-execution/run_h_frame_identity_replay.json` |
| ?? | `docs/step01-layered-execution/kf-frame-identity-alignment-2026-11-15.md` |

`git diff --stat`（已跟踪文件）：**5 files changed, 862 insertions(+), 35 deletions(-)**。

交付文件 SHA-256（实算，算法 sha256，逐文件字节）：

| 文件 | SHA-256 |
|---|---|
| `step01_script_gen/main.py` | `1fb66c4b1bb39195830dc31b5b4b0461b4e3111d12d64ce767383d233626df0f` |
| `step01_script_gen/repair_fields.py` | `ed7ee2b9ace8fc1d8a4924f13d339de55468acdb2d32f92f27e535588ddf428d` |
| `step01_script_gen/repair_effects.py` | `515a8df294a75739bf005224d558d2382acbffc17bf3f44c0fb0d4a7cac9e6d8` |
| `.gitignore` | `98fd63411c9191ea60abb8a7dd172697878756ac45191e91e78ac80a77f8b2e1` |
| `tests/.../test_kf_frame_identity.py` | `df6732388cbac35d8e4301cba9cbc84de6ffe44f9ffeeec769a7f8b20bbd051a` |
| `tests/.../test_kf_frame_identity_transaction.py` | `79951e2d2343c45520887488b76f42f6e134e5eb71df8bea887c760986d55a0e` |
| `tools/replay_run_h_frame_identity.py` | `d411273b576e28675c511c4e7c0a252d388f12eb25565dbbe49054206d44f33a` |
| `docs/.../run_h_frame_identity_replay.json` | `c300083a1c2282a3e3cce0c612b1e6f0bcc27aa28be8185e2a08e8d1c716d0a3` |
| `docs/.../U7-consistency-contract-proposal-2026-11-15.md` | `3f5a18ef1adf4d76f3bf57007cb7ad4702559a3e74eef93eacfc5218135b1f11` |
| 本文件 | 自引用，不列固定值；按第 8 节命令实算 |

> 代码、测试、回放证据的 SHA-256 是本轮实算值；两份 `.md` 是叙述性交付物，
> U7 已列实算值，本文件因自引用不写死（写入自身摘要会改变自身摘要）。

---

## 2. 冻结输入与复跑

`compute_script_revision` 实算
`a1671c1756b50e2785b36e04ec149a65eaee95a6371d4e78b8c4af78eaeba588`，
与冻结快照声明、`step01_commit.json` 三方一致（`revision_identity_ok=true`）。
`run_id = 20261115T144433-11820`。输入 SHA-256（与回放 JSON 的
`input_sha256` 一致）：

| 输入 | SHA-256 |
|---|---|
| `snapshots/script_result_20261115_145644.json` | `cc24493e1af95a560e089ac59c2c20569aeb5673cb1c387b4e187f23cf05ba69` |
| 第 1 轮答卷 `c11efe467630b41aa.txt` | `a444cb11d9a0e742575cbe9fb14e864a6be889ca9fdbdd40b1ed53add574aeb6` |
| 第 2 轮答卷 `ca19a9eb8180f4d03.txt` | `376f12fac3a2480f9a1341a6a60f4072f4b1e0ae6227a7d85621e89a5b119e11` |
| `step01_audit.blocked.json` | `7685786ce4e9690c573819ae46a961956ede59b6c64ad5ed9aaa8b36637b6032` |
| `step01_commit.json` | `8f58ac09f1050d8813496ab1910ab5b96c4e45d7a924f62f1cdc3f9773e63629` |
| `step01_metrics.json` | `6a883380f76818a8bc64002243344fc40c301375d87e0b8fb68567a1c21340e1` |
| `step01.log` | `006587ff01775510bd86cf70abbd5f4545289eb1ff347cb3937fe91cae03b3cd` |

路径在 `tools/replay_run_h_frame_identity.py` 中**逐个显式列出**，不做「最近产物」
自动选择；现场缺失时输出 `missing_inputs`。模块初始化会向 stdout 打日志，
**纯 JSON 证据必须用 `--json-out`** 生成，不要重定向 stdout。

```bash
PY="C:/Users/ee54/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
$PY -m pytest tests/step01_script_gen/test_kf_frame_identity.py \
  tests/step01_script_gen/test_kf_frame_identity_transaction.py -q
$PY tools/replay_run_h_frame_identity.py --json-out \
  docs/step01-layered-execution/run_h_frame_identity_replay.json
```

回放输出**覆盖重跑两次**的 SHA-256 均为
`c300083a1c2282a3e3cce0c612b1e6f0bcc27aa28be8185e2a08e8d1c716d0a3`（11,456 字节，确定性）。
测试与回放均为纯内存/纯本地函数，不发起网络或真实模型调用。

---

## 3. 补正：三处结论修正与三组确定性反例

### 3.1 初版三处结论的修正（逐条对照）

| # | 初版表述（撤回） | 补正后的结论 |
|---|---|---|
| 1 | 「不导出映射 + 明确日志警告，守卫**退回原坐标比较**……退回旧行为是 fail-closed（不放行）」 | **不能保证拒收**。帧数 2→3、缺映射、末尾新增空布局时，旧比较会静默放行。现改为：帧结构变化而无可验证映射 ⇒ 守卫产出结构化违例、替换入口在 `staged` 前拒收并保留基线；**不再退回同下标比较**。 |
| 2 | 「用 `frame_layouts.kfs[i]` 的**逐字恒等**推导映射，因为 run h 的 desc 被改写」 | **布局内容恒等不能证明帧来源**。同一候选 `[A, B改, B]` 在布局推导下得到 `[0,2]` 而放行真改写；用操作来源 `[0,1]` 才正确报出改写。现只允许：①装配操作来源；②整表替换时**保留关键帧完整结构**形成唯一、顺序一致对应；③帧数不变且叶子授权差异可证明时的恒等映射。布局相似度一律不参与。 |
| 3 | 「**终局仍不通过**：解除误报不等于 run h 可以提交」 | 改为**历史终局与修复后裁决分列**（第 6 节）。历史终局按冻结台账抄录；修复后的实际裁决由离线回放给出：原始整表答卷 `rejected_identity_unprovable`，操作来源派生答卷 `accepted_with_assembly_source`；事务级入口结果另由 8 个用例给出。不再用「修好守卫 ⇒ 是否提交」这种推断式措辞。 |

用户另指出 `validate_frame_mapping` 通过 `int()` 转换字段，`[False, 2.9]` 被接受为
`[0,2]`。已改为 `type(x) is int` 严格契约（第 4 节）。

### 3.2 三组确定性反例（`test_kf_frame_identity.py::TestCounterexamples` 等）

| 反例 | 构造 | 旧行为 | 补正后 |
|---|---|---|---|
| 缺映射 | 帧数 2→3、无映射、末尾新增空布局 | 守卫返回 `[]`（放行） | 报「缺少可验证的来源映射」，结构化拒收 |
| 复制旧布局绕过真实改写 | 候选 `[A, B改, B]`，布局内容推导得 `[0,2]` | `[]`（放行） | 只认关键帧完整结构/操作来源；`[0,1]` 下正确报「字段被改写」 |
| 非法类型 / 跨 revision | `[False, 2.9]`、`["0","2"]`、缺 `source`、跨 revision | `int()` 隐式转换后接受 | 全部拒收；跨 revision **不再过滤后回退旧比较**，直接产违例 |

另有：重复布局不误认身份、多次插入、输出容器按单次调用重置、
只提交成功目标才导出映射、逐字点名 `kfs[i]` 不授权凭空新增帧等用例。

---

## 4. 源码落地（S02 / S03）

| 模块 | 函数 / 位置 | 落地内容 |
|---|---|---|
| 来源契约 | `repair_fields.py` `validate_frame_mapping` | 计数与下标必须 `type(x) is int`；校验 `source ∈ {assembly, keyframe_structure, identity}`；`expected_revision` 不符即拒；禁止模型答卷提供/覆盖映射 |
| 来源契约 | `repair_fields.py` `derive_frame_mapping_from_keyframes` | 整表替换唯一合法推导：保留关键帧**完整结构**逐字恒等、唯一递增落点；身份字段改写即 `None` |
| 来源契约 | `repair_fields.py` `compose_frame_mappings` / `verify_frame_identity_by_authorized_leaves` | 更新段 + 装配段组合成单一来源链；帧数不变时用授权叶子差异逐帧证明恒等 |
| 装配 | `main.py` `_assemble_keyframe_additions` | 进入即清空 `frame_mapping_out`；来源序号与 `keyframes`/`kfs` 同步插入；成功导出 `source="assembly"` |
| 替换入口 | `main.py` `_apply_shot_replacements` | 进入即清空 `frame_mappings_out`；`_pre is None`、装配来源不连续、回填破坏装配结构、最终核对失败，全部**在 `staged` 前** `rejected.append` 并 `return` |
| 替换入口（补正追加） | 同上 | 修正「更新阶段落点」：无 `keyframe_additions` 的整表替换过去拿 `_kf_base`（仍等于基线）推导出恒等映射，直到最终核对才被拦，拒因也不结构化；现取授权合并后的候选，直接报「帧来源无法证明」 |
| 守卫 | `repair_fields.py` `frame_layout_structural_violations(..., frame_mappings=None, revision=None)` | 合法映射时把候选 `kfs` 投影回原坐标；新增帧只认整表补帧授权并做形状校验；缺映射/歧义/失配/跨 revision 结构化拒收 |
| 效果登记 | `repair_effects.py` `blocked_frames` / `build_layout_effects` | 与守卫消费**同一份映射、同一份 revision**；效果落在合并后坐标，位移时带 `source_frame_locator` |
| 事务主链 | `main.py` `_repair_shot_warnings` | 每轮新建映射并绑定本轮基线 revision；守卫、效果登记、回退重验共用；回退目标恢复恒等映射，其余保留；台账写**最终保留稿**的映射 |
| 后处理保护 | `repair_effects.py` `frame_locator_integrity` / `RepairEffectLedger` | 四节点从候选起按本轮点名目标冻结帧结构；`structure_changes()` 只比较 merged 之后；即使零效果但有来源映射也核验 `after_count` 与身份序列；观测回调错误进入裁决 |
| 台账 | `main.py` 诊断记录 | 新增 `layout_structural_violations`、`frame_locator_integrity`、`writer_structure_changes`、`writer_observation_errors`、`frame_mappings`、`effect_ledger_nodes`、`effects_rolled_back` |

`.gitignore` 按既有白名单机制放行两个新测试文件，保证干净检出可运行。

---

## 5. 事务入口与效果保护链（S03）

`tests/step01_script_gen/test_kf_frame_identity_transaction.py` 跑**真实**
`_repair_shot_warnings`，只打桩 LLM 响应、确定性检查器、日志与后处理 writer；
装配、授权合并、守卫、账本、按目标回退、事务裁决全部是真实代码。
8 个用例（下表编号按**场景**归类，不是文件内的定义顺序）：

| # | 场景 | 实际裁决 |
|---|---|---|
| ① | 合法中间补帧（两镜） | `accepted=True`、`resolved≥1`、镜 5 返回稿 3 帧/3 布局、映射 `[0,2]`+`added=[1]`+`source=assembly`、四节点齐备、`frame_locator_integrity=[]` |
| ② | 插入中间帧 + 改写原 `kfs[1]` 条目（单目标） | `accepted=False`、`origin_reason=layout_structure_invalid`、违例含「字段被改写」、基线逐字保留 |
| ③ | 一个目标在替换入口被拒 | `shot_5` 保留映射、`shot_9` 无映射、被拒目标保持基线帧数 |
| ④ | 新阻塞触发按目标回退 | 回退目标恢复基线，同轮合法目标不被连带回退，效果视图过滤 |
| ⑤ | 插帧 + 合法布局增删 | `accepted=True`，四节点结构状态一致、无 writer 结构变化，新增条目落在位移后的 `kfs[2]` |
| ⑥ | 布局违例只回退肇事目标（多目标） | `origin_reason=layout_structure_invalid`、只回退 `shot_5`、整轮仍接受合法目标；台账中回退目标为 `identity` 映射、合法目标仍为 `assembly` |
| ⑦ | **纯补帧零效果 + writer 擅自插帧** | `frame_locator_integrity` 与 `writer_structure_changes` 均非空、`accepted=False`、回到 2 帧基线 |
| ⑧ | 观测回调异常被吞成 warn | `writer_observation_errors` 进台账、产生「观测回调失败」否决项、`accepted=False` |

用例⑦正是本轮补上生产缺口的那条反例：初版账本只按 `effects` 取观测目标，
纯中间补帧没有条目增删效果，writer 插入第 4 帧时四节点快照为空、
`structure_changes()` 为空、定位符完整性也只在 effects 循环里检查，
结果越权插帧被接受。现改为按**本轮点名目标**冻结帧结构，并对所有有来源映射的
目标（含零效果）核验 merged→final 的结构与 `after_count`。

> 测试基建修过一处自身缺陷：观测异常注入最初直接赋值
> `RepairEffectLedger._freeze` 且不撤销，污染同一 pytest 会话里后续所有测试文件
> （16 个假失败）。已改为 `monkeypatch.setattr`，用例结束自动撤销。

---

## 6. 历史终局与修复后裁决（分列）

### 6.1 历史终局（冻结台账 + `step01.log`，不重跑事务入口）

| 轮次 / 时刻 | 事件 | 裁决 |
|---|---|---|
| 第 1 轮 14:55:47 | 应用完整镜头替换 4 镜；新增 1 条 blocking（`shot_2`） | 按目标回退 `shot_2` 后 `layout_structure_invalid / resolved=5`；终局 `new_blocking / resolved=7` → 整轮回退 |
| 第 1 轮 14:55:47 | 带反馈重试（剔除 `shot_2`） | 提交层 `continue`，进入第 2 次 attempt |
| 第 2 轮 14:56:43 | 替换被拒 3 条；仅 shot 5 提交 | 按目标回退 `shot_5` 后中间裁决 `no_target_resolved / resolved=0` |
| 第 2 轮 14:56:43 | 事务终局 | `layout_structure_invalid / resolved=2 / 字段触达=0 / new=0`，回退本轮 |
| 14:56:44 | 提交层 | 零改善 → 停止修复 → `[BLOCK] 分镜脚本未提交` |

最终返回稿 = 基线 `a1671c17…`；`status=failed`、`outcome_code=commit_blocked`、
`commit={attempts:1, blocked:1}`。这一列**不因本包修复守卫而改写**。

### 6.2 表达差异（两轮 + 派生样本）

| 样本 | 表达形状 | 来源可证明性 |
|---|---|---|
| 第 1 轮答卷 | 窄 `updates` 改写 `frame_layouts.kfs.1` + `keyframe_additions` 追加 | `original_kfs1_was_rewritten_by_updates=true`：原帧 1 字段被真实改写，不是位移误报；本包不放宽判据 |
| 第 2 轮**原始答卷** | 整表替换 `frame_layouts` + `keyframes`（无 `keyframe_additions`）；三条关键帧 `desc/stage/t_sec` 全部重写，时间轴改为 `[1.0,3.0,4.5]` | `preserved_keyframe_full_structures=0` ⇒ 身份无法证明。布局内容逐字后移只证明内容保留 |
| 派生样本 `DERIVED-R2-ASSEMBLY-01` | 只给 `keyframe_additions`（`frame`+`layout` 成对），由装配器按 `t_sec=2.0` 中间插入；无整表替换、无布局相似度推导 | `source=assembly`，`old_to_new=[0,2]`、`added_indices=[1]`、`mapping_validation=""` |

派生样本 SHA-256（实算，规范化 JSON 字节）：
`c6584896a3b6eab35395bc3c86b79621d6b00b9d0008b5f1000d11a3129169a4`。
它是**重建样本**，取第 2 轮新增帧的 `desc/stage` 与布局内容，仅把 `t_sec` 设为
2.0 以产生合法中间插入；不是历史产物，也不冒充原始答卷通过。

### 6.3 修复后的实际离线裁决（与 6.1 分列）

| 对象 | 入口 | 结果 |
|---|---|---|
| 原始第 2 轮整表答卷 | 守卫（无映射） | 1 条「缺少可验证的来源映射」 |
| 同上 | `_apply_shot_replacements` | `applied=0`、`rejected=["shot 5: 帧来源无法证明…"]`、无导出映射、`baseline_kept=true` |
| 派生样本 | `_apply_shot_replacements` | `applied=1`、`rejected=[]`、导出装配映射并通过严格校验 |
| 派生样本 | 守卫（无映射 / 有映射） | 无映射 1 条结构化拒收；有映射 `[]` |
| 派生样本 | `build_layout_effects` | 0 条（纯位移，零条目增删，符合预期） |

`post_fix_offline_adjudication` 在回放 JSON 中单列：
`original_round2_answer=rejected_identity_unprovable`、
`derived_assembly_answer=accepted_with_assembly_source`。
这两项**不覆盖** 6.1 的历史终局；事务级合法/拒收/回退结论见第 5 节。

> 授权面证据边界：审计 issue 的 `repair_targets` 只给出 7 条叶子路径
> （含 `frame_layouts.kfs[0]` / `[1]`），事务中的 `_allowed_by_target` 另由确定性
> 检查器的结构码并入整表 KF 路径。回放按 `step01.log:1471` 实测的 6 处改动
> **重建**授权面，JSON 里明确标注为重建值，不冒充台账原始集合。

---

## 7. 验收读数

| 集合 | 命令 / 口径 | 结果 |
|---|---|---|
| 单元入口 | `test_kf_frame_identity.py` | **44 passed** |
| 事务入口 | `test_kf_frame_identity_transaction.py` | **8 passed** |
| 两者合并 | 同上两文件一次运行 | **52 passed in 2.24s**，exit 0 |
| 聚焦回归 | 18 个修复/布局/授权相关测试文件（磁盘实存），JUnit XML | **447 tests / 0 failures / 0 errors / 15 skipped / 10.579s**；pytest 汇总 `432 passed, 14 skipped, 1 xfailed in 10.07s`，exit 0 |
| 回放 | `--json-out` 覆盖重跑两次 | SHA-256 一致 `c300083a…`，11,456 字节 |

聚焦回归的 18 个文件为：`test_kf_frame_identity.py`、
`test_kf_frame_identity_transaction.py`、`test_repair_shot_replacements.py`、
`test_repair_target_rollback.py`、`test_m2_layout_autofill.py`、
`test_g1_g7_gates.py`、`test_repair_loop_contract.py`、`test_repair_targets.py`、
`test_issue_ledger.py`、`test_p0_optimizations_and_scoped_patch.py`、
`test_step01_commit.py`、`test_repair_call_outcome.py`、
`test_audit_authorization_guard.py`、`test_cross_field_checks.py`、
`test_p0_fault_injection.py`、`test_repair_related_fields.py`、
`test_source_revision.py`、`test_m02_revision_entry.py`。

> 初版文档「受影响 6 个测试文件 216 passed / 聚焦 14 文件 390 tests」里提到的
> `test_frame_layout_entry_delete.py`、`test_kf_pairing_assembly.py`、
> `test_repair_effects.py` **在磁盘上不存在**，那组读数无法按所列命令复跑。
> 本版改用上面 18 个实存文件重新定义聚焦集，读数全部来自 JUnit XML。

skip / xfail 原因：14 条 `test_audit_authorization_guard.py` 的
`external dataset: frozen candidate audit package not present`（外部冻结数据集缺失）；
1 条 `test_cross_field_checks.py` 的已知缺口 xfail（证据闸门未覆盖当事角色替换）。
两者都与本次改动无关。

环境提示：不带 `--basetemp` 时，pytest 会话结束清理临时目录会触发
`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]` 并吞掉 `-q` 汇总行、
进程退出码为 1，但 JUnit XML 仍是 `0 failures / 0 errors`。改用独立
`--basetemp` 后 exit 0、汇总行正常。**以 JUnit XML 与带 basetemp 的运行为准**。

### 全量回归（与上面的聚焦读数严格分列）

- `tests/` 全量：本轮**只做了收集**，未运行。
  `pytest tests/ --collect-only -q` → `7560 tests collected in 19.25s`，exit 0，
  收集期未出现 import 失败。此前交付窗口记录过
  `pytest tests/ -x` 在 `tests/_video_gen/test_duration_rounding.py` 因隔离环境
  缺 `dashscope`（`_video_gen/bailian_provider.py` import 期）停止；本轮**未**
  独立复验该运行期错误，不把它写成本轮结论。
- `tests/step01_script_gen/` 全目录（独立 `--basetemp`，335.4s）：
  JUnit XML **4701 tests / 14 failures / 0 errors / 298 skipped**，
  pytest 汇总 `14 failed, 4389 passed, 294 skipped, 4 xfailed`。
  **不写成「全目录全绿」**：14 个失败全部落在 5 个 `test_plan_phase_*.py`
  文件；单独运行这 5 个文件同样 `14 failed, 105 passed`（4.09s），说明是
  稳定失败，不是测试间污染。失败断言集中在 plan 控制器的
  `call_failed` 与 `outline_invalid` / `illegal_response` 归因、调用次数
  （5 vs 4、7 vs 4、6 vs 3），以及 `outline.py` 里 3 个未登记的
  `structure_background_facts_*` producer code。
- **与本包改动的关系（可核验）**：这 5 个文件的 import 目标是
  `step01_script_gen.outline / plan_feasibility / plan_revision /
  concurrent_extract` 与 `shared.call_ledger`，不含 `main`、
  `repair_fields`、`repair_effects`；`git diff --unified=0 -- main.py`
  的全部 hunk 都落在 `_assemble_keyframe_additions`（约 5399–5480）、
  `_apply_shot_replacements`（约 5620–5970）、`_repair_shot_warnings`
  （约 8160–10160）三个函数内。故这 14 项是**既有失败**，本包未修、
  也未触碰其依赖链；本包的可复跑确定读数是 52 passed 与 447 tests 聚焦集。

---

## 8. 兼容行为变化、剩余缺口、未执行项

**保守方向的行为变化（必须评阅）**

1. 整表替换若不能由「保留关键帧完整结构」或「授权叶子差异」证明来源，
   **在进入 `staged` 前拒收并保留基线**。初版是「不导出映射 + 退回旧比较」；
   现在不再退回旧比较，因此部分历史上的整表答卷会被更早、更明确地挡下。
2. 有来源映射时，新增帧只认整表 `keyframes` / `frame_layouts` /
   `frame_layouts.kfs` 级补帧授权；逐字点名 `kfs[i]` 不再因为「候选下标撞上
   原坐标授权」而放行凭空新增帧。
3. 后处理 writer 若改变帧结构，即使本轮零布局效果，也会因定位符/来源映射
   失效而被否决；观测回调异常不再是纯 warn。
4. 台账的 `frame_mappings` 写**最终保留稿**的映射（按目标回退后，回退目标为
   `identity`），不是回退前的装配映射。

**剩余缺口 / 后续工作包**

- `retry_state` 的事务拒收反馈接线**未做**：本包处理的是守卫比较基准与来源
  证明，给模型发「保留条目」反馈会把修法引向错误方向；反馈重试、模型选择、
  调用额度、审美判据仍按计划留后续独立决策。
- 真实模型验证、正式产物重签、正式采纳、`git commit` **均未执行**（真实调用 0）。
- U7 §12 只改文档结论，未改任何历史运行产物；轮次数 6→7、撤回「单点瓶颈」
  与「模型能力边界」、run h 归因收窄为「守卫比较基准错误 + 原始答卷身份无法证明」。
- 本包未重写历史 run h 的两轮事务裁决；历史与修复后结论严格分列。

**复跑与逐字节核对**

```bash
cd C:/myFloders/git/aiVideo
git rev-parse HEAD
git status --porcelain
git diff --stat
"$PY" -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('docs/step01-layered-execution/run_h_frame_identity_replay.json').read_bytes()).hexdigest())"
```

---

证据文件：`run_h_frame_identity_replay.json`（同目录，由回放脚本生成，
覆盖重跑 SHA-256 一致）。专题记录与 U7 §12 均为文档改动，不涉及生产产物。
