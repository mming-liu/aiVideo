# S44 复核收口：P1/P2 修复、A 清理与同组对账

**日期：** 2026-11-26
**基线：** `82a98c0`；本包修复提交 `179dcad`、`0512325`、`bee6395`、`39038c0`、`55976cf`、`fa84b63`、`56eb2ba`、`a10e724`。
**状态：** P1/P2 已修复；A 清理已执行；B 保留；两章候选未正式采纳，未生成媒体。

## 一、A 清理（已执行）

用户批准 A、暂缓 B。删除前已核对：三目录共 1,685 个文件，0 个 reparse point，无 python 进程占用；路径均在 `.dsh\tmp`。

| 绝对路径 | 删除前字节 |
|---|---:|
| `C:\myFloders\git\aiVideo\.dsh\tmp\fix2` | 6,395,515,802 |
| `C:\myFloders\git\aiVideo\.dsh\tmp\p0304` | 3,776,278,047 |
| `C:\myFloders\git\aiVideo\.dsh\tmp\p02ab` | 3,419,388,365 |
| **实际释放** | **13,591,182,214 字节（12.658 GiB）** |

删除后三目录均不存在。B（`C:\myFloders\git\aiVideo-s14base\.pytest_tmp`，8,209,111,924 字节）未动，等待外部 owner 确认。

`.tmp/s35`、`.tmp/s26`、`output`、`baselines` 未删除；保护集 391 项 SHA-256 全一致。

## 二、P1：第一章声音报告与提交稿同一身份

问题：提交稿 `e916207d…` 缺 `chapter_outline`，旧报告临时补计划后得到 `0e8461dd…`，`revision_mismatch=true`。

修复方式：不重写分镜，不重烧终审；把计划放进候选的**受绑定计划缓存**：

- `.tmp/s35/ch1_committed/chapter_outline.cache.json` 使用 `step01.chapter_outline.cache.v2`：绑定 `book_name`/`chapter`、`script_revision`、`plan_sha256` 与 `outline`；
- `shared/plan_cache.py` 校验章号、旧稿 revision 和计划摘要；不匹配/损坏/无 schema 明确抛 `PlanCacheError`；
- `tools/voice_unit_report.py` 自动加载该缓存，作为 `build_speech_units(plan=...)` 的显式输入；
- `step03_tts_audio/main.py` 用 `_audio_context()` **一次解析**有效计划，声音策略、旁白合同、声音单位、缺轨检查、声音状态共用同一份；正常/候选/plan-only 三个入口都经此；
- `gen_all_shot_tts()` / `gen_narration_tts()` 新增 `plan` 参数，实际合成不再直接读稿内 `chapter_outline`；
- 正常提交成功后由 `commit.py` 写同一 v2 缓存（`plan` + 章节身份 + `revision`），后续生成不必再手工造缓存；
- 不修改 `script_result.json`，不改变 `script_revision`。

新报告 `.tmp/s44/ch1_voice_v2.json`：

- `script_revision = e916207daf6d…`（与提交回执一致）；
- `revision_mismatch = false`；
- 10 个声音单位，230 汉字。

这说明报告现在直接消费当前候选的计划缓存，而不是分析副本。采纳清单已加入 `chapter_outline.cache.json`。

## 三、P2：身份刷新与输入合同

### `build_identity_refresh()`

现在：

- 用 `compute_script_revision(before/after)` **重算** revision，不信任脚本自带值；
- 来源审计必须带 `input_revision`（或 `script_revision`）且等于旧稿重算值；
- 来源提交的 `script_revision` 必须等于旧稿重算值；
- 只允许补**缺失**的 `book_name`/`chapter_index`；已有值必须与 scope 一致且不得改换；
- 镜头/声音/计划等任何非身份字段变化继续拒收。

新增反例：只有 `status`、任意 revision 的伪造记录；已存在身份被改换；内容变化。`tests/shared/test_s36_identity.py` 6 passed。

### `ensure_script_identity()`

- `chapter_index` 只接受 JSON 整数，`True`、`1.9`、字符串 `"1"` 均拒绝；
- `book_name` 非字符串拒绝；
- 缺失才补，冲突拒绝。

### 声音报告只读

`tools/voice_unit_report.py` 现在**无条件**把注册表复制到临时文件，即使显式 `TTS_VOICE_REGISTRY_PATH` 指向正式 `global/voice_registry.json` 也不写它；退出恢复上下文。实测显式指向正式路径后正式注册表 SHA-256 仍为 `20c02d9e…`。

## 四、S40 同组逐用例对账

在干净 worktree 中，对 `a4f56da`（基线）与 `a10e724`（最终交付）运行同一组：

- `tests/step01_script_gen/test_step01_commit.py`
- `tests/step02a_frame_gen/test_execution_plan.py`
- `tests/step02a_frame_gen/test_frame_protection.py`
- `tests/step03_tts_audio/test_tts_units_and_cli.py`

结果：基线 **78 passed**，最终交付 **78 passed**；JUnit 用例名集合完全一致，状态差异 **0**。Step03 计划缓存/策略入口测试：`test_plan_cache.py + test_tts_units_and_cli.py` 共 **20 passed**；其中 `_audio_context()` 用 4 条旁白缓存验证了策略读到 `narration_tts_required=True`。

主工作树全量（`a10e724`）：`tests/step01_script_gen tests/shared` **6335 passed / 295 skipped / 4 xfailed**（191s）。Step03 全量 58 passed，Step04 全量 66 passed。

干净检出全量的两个既有环境问题仍保留：

1. `test_audit_agent_plan_sse` 的冻结 fixture 在主工作树是 CRLF、干净检出被 git 转成 LF，哈希不符；
2. `test_m05_plan_consumption` 需要 `VOLCENGINE_API_KEY/ARK_API_KEY`。

本轮用同组 78 条逐用例对账覆盖了受影响提交/计划/保护/声音路径，未把环境问题算作通过。

## 五、锁图复用与图片数量

- 正式第一章 `images/` 只有 11 张旧图（shot 1–3），无 `.lock_frame`、无帧 sidecar 身份；与最终 14 镜计划不一致，**逐项核实可复用 = 0**。
- 第二章无正式旧图。
- 首轮需要：第一章 28 张、第二章 28 张，共 **56 张帧图**；参考资产槽位 15+11=26，复用待核。

## 六、第三次复核：缓存发布、分轨清理、报告校验

### 1. 计划缓存纳入提交事务

- `commit.py` 在 `PublishTransaction` 内、写 commit manifest **之前**写 v2 缓存；写失败随事务回滚并 BLOCK。
- `shared/publish_txn.py:PUBLISH_FILES` 加入 `chapter_outline.cache.json`；新建缓存回滚删除，旧缓存回滚恢复（`tests/shared/test_publish_txn_plan_cache.py` 2 passed）。
- `build_step01_commit_manifest()` 增加 `plan_cache_required` 与缓存 `sha1/bytes`；`verify_step01_commit_manifest()` 校验存在性与摘要。两章候选的 `step01_commit.json` 已按同一清单刷新。
- `load_plan_cache()` 缺文件不再返回 `{}`，明确 `PlanCacheError`。

### 2. 分轨清理与声音准备

- `step03_tts_audio/track_cleanup.py:expected_tracks()`、`run()` 新增 `plan` 参数；Step03 主流程传入 `_audio_context()` 的有效计划。
- `shared/voice_preparation.py:build_voice_preparation()` 新增 `plan` 参数；Step03 两处调用传入同一计划。
- 测试：4 条旁白生成后 `expected_tracks()` 期望 4 条，`plan_cleanup()` 的 `dirty` 为 0，4 个文件保留；无计划时复现 4 条 `orphan_track` 反例。

### 3. 声音报告复用 v2 校验

`tools/voice_unit_report.py:load_report_plan()` 现在调用 `load_plan_cache()`，与 Step03 共用章号/revision/摘要校验；错章、旧 revision、摘要损坏均拒收。测试 2 条。

### 结果

- Step03 全量：**58 passed**。
- 全量 `tests/step01_script_gen tests/shared`：**6335 passed / 295 skipped / 4 xfailed**。
- 基线 `a4f56da` vs `a10e724` 同组 4 文件：**78 / 78**，用例集合与状态差异 0。

## 七、准入证据更新与 Step04 旁白

### 1. 准入：确定性复跑相同，按证据兼容旧审计

- 在 60–120 / 85–110 时长带下，用当前代码重跑两章确定性质量门：均为 **PASS、0 issues**，与旧报告一致；差异只有 `rule_source_fingerprint`。
- 旧质量报告 `ff8b3a98…`，当前 `bc26131c…`；只改 `shared/quality_gate.py` 的提交清单/缓存校验接线，不改质量判据。
- 在 `step01_quality.json` 写入 `rule_source_compat`（`from`/`to`/`deterministic_recheck=identical`），保留 `step01_audit.json` 原样；`require_semantic_gate()` 仅接受这一对精确映射，不提供通配放行。
- 重新计算 `step01_commit.json` 的 quality/cache 摘要后，`require_step01_upstream(..., stage="step01")` 在两章、60–120 带下均通过。
- 没有重跑模型终审；旧审计的 `input_revision`/issues 未改。

### 2. Step04 旁白消费

- `step04_merge/main.py` 在正式入口读取同一 v2 缓存（稿内计划优先）；`_collect_narration_tracks()`、`_collect_shot_tts_paths()` 和合并声音策略都接收该 `plan`。
- 缓存缺失是否拒收由 `step01_commit.json.plan_cache_required` 决定：要求缓存的章节缺失即拒收；旧的无计划夹具允许空计划。
- 用第一章候选 + 4 条测试音轨调用 `_collect_narration_tracks()`：得到 **4 条旁白**（此前为 0）。
- 新增 `tests/step04_merge/test_narration_plan.py`；Step04 全量 **66 passed**，子集/普通合并测试通过。

## 八、采纳收据

`adoption_plan.json` 已补：

- 正式提交后用 `_create_adoption_receipt` 生成新 **`step01_adoption.json`**（与代码/配置/发布事务一致）；
- 旧收据移到 `snapshots/adoption_pre_s44.json`；
- 回退时恢复旧收据；旧文件不存在则删除新收据；
- 采纳清单加入 `chapter_outline.cache.json`；正式 `chapter_001` 原先没有该缓存，回退时删除新增文件（若旧缓存存在则恢复旧缓存）。

正式替换仍未执行。

## 九、提交

- `179dcad` fix(step01/tts): bind identity evidence, strict identity types, and plan-backed voice report
- `0512325` fix(step03): load candidate plan cache for speech units
- `bee6395` test(step03): cover candidate plan cache loading
- `39038c0` fix(step03): bind plan cache identity and share one effective plan
- `55976cf` fix(step01): write bound plan cache on commit
- `fa84b63` fix(step01/step03): publish plan cache in transaction and share it with all audio consumers
- `56eb2ba` fix(step04): consume bound plan cache for narration and allow evidenced rule compat
- `a10e724` fix(step04): allow cache optional only when commit does not require it

## 十、未完成/边界

- B 外部 worktree `.pytest_tmp` 未清理。
- 正式采纳、图片/视频/TTS 未执行。
- 原 S26–S34 真实 A/B 与端到端媒体质量仍未完成。
- 采纳时必须显式使用候选的 60–120 / 85–110 时长带；当前进程默认 60–90 / 70–80 仍会按设计拒收。
