# S44 复核收口：P1/P2 修复、A 清理与同组对账

**日期：** 2026-11-26
**基线：** `82a98c0`；本包修复提交 `179dcad`、`0512325`、`bee6395`。
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

修复方式：不重写分镜，不重烧终审；把计划放进候选的**正常计划缓存**：

- `.tmp/s35/ch1_committed/chapter_outline.cache.json` = `{"outline": ch1_verified_plan}`；
- `tools/voice_unit_report.py` 自动加载该缓存，作为 `build_speech_units(plan=...)` 的显式输入；
- `step03_tts_audio/main.py` 的正常/候选/plan-only 三个入口都通过 `_plan_for_script()` 读取同一缓存；
- `step03_tts_audio/shot_tts.py:build_speech_units()` 新增 `plan` 参数；不修改 `script_result.json`，不改变 `script_revision`。

新报告 `.tmp/s44/ch1_voice_final.json`：

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

在干净 worktree 中，对 `a4f56da`（基线）与 `bee6395`（最终交付）运行同一组：

- `tests/step01_script_gen/test_step01_commit.py`
- `tests/step02a_frame_gen/test_execution_plan.py`
- `tests/step02a_frame_gen/test_frame_protection.py`
- `tests/step03_tts_audio/test_tts_units_and_cli.py`

结果：基线 **78 passed**，最终交付 **78 passed**；JUnit 用例名集合完全一致，状态差异 **0**。Step03 计划缓存另加 2 条测试，`test_plan_cache.py + test_tts_units_and_cli.py` 共 13 passed。

主工作树全量（P2 后）：`tests/step01_script_gen tests/shared` **6333 passed / 295 skipped / 4 xfailed**（192s）。Step03 受影响的 11 条测试通过。

干净检出全量的两个既有环境问题仍保留：

1. `test_audit_agent_plan_sse` 的冻结 fixture 在主工作树是 CRLF、干净检出被 git 转成 LF，哈希不符；
2. `test_m05_plan_consumption` 需要 `VOLCENGINE_API_KEY/ARK_API_KEY`。

本轮用同组 78 条逐用例对账覆盖了受影响提交/计划/保护/声音路径，未把环境问题算作通过。

## 五、锁图复用与图片数量

- 正式第一章 `images/` 只有 11 张旧图（shot 1–3），无 `.lock_frame`、无帧 sidecar 身份；与最终 14 镜计划不一致，**逐项核实可复用 = 0**。
- 第二章无正式旧图。
- 首轮需要：第一章 28 张、第二章 28 张，共 **56 张帧图**；参考资产槽位 15+11=26，复用待核。

## 六、采纳收据

`adoption_plan.json` 已补：

- 正式提交后用 `_create_adoption_receipt` 生成新 `step01_adoption_receipt.json`；
- 旧收据移到 `snapshots/adoption_receipt_pre_s44.json`；
- 回退时恢复旧收据并删除新收据；
- 采纳清单加入 `chapter_outline.cache.json`。

正式替换仍未执行。

## 七、提交

- `179dcad` fix(step01/tts): bind identity evidence, strict identity types, and plan-backed voice report
- `0512325` fix(step03): load candidate plan cache for speech units
- `bee6395` test(step03): cover candidate plan cache loading

## 八、未完成/边界

- B 外部 worktree `.pytest_tmp` 未清理。
- 正式采纳、图片/视频/TTS 未执行。
- 原 S26–S34 真实 A/B 与端到端媒体质量仍未完成。
