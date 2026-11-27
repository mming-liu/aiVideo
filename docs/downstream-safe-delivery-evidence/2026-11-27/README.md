# 下游安全出片包（S01–S07）证据归档 — 2026-11-27

本目录位于任务 worktree 之外（`docs/` 已随 fast-forward 合入 master），保存可复核的最小证据。
**任务 worktree、验证副本、缓存与可再生临时文件已于 2026-11-27 清理完毕**，见
`cleanup-manifest.md`；本目录与主仓库 `output/`（未改动）是唯一保留证据。

## 1. 交付身份

| 项目 | 值 |
|---|---|
| 首次验收代码提交 | `482676b`（`M01-M03: recoverable publish batch, audited duration identity, subtitle required checks`） |
| 合并提交 | `c55cc4c`（docs/证据 + `.gitignore`；`482676b..c55cc4c` 无生产源码变化） |
| M01 崩溃/并发修复提交 | `631e269`（`fix(step04): persist publish intent before backup, verify recovery digests and hold one lock across recover/publish/cleanup`） |
| 合并目标 | master（首次合并前 HEAD `507ccea`；M01 修复 `631e269`；其后为 docs/`.gitignore` 收尾） |
| 分支 | `codex/downstream-safe-delivery`（已合并并删除） |
| 合并方式 | `git merge --ff-only` |

## 2. 本轮整改（M01–M03）

- **M01 发布批次**：新增 `step04_merge/publication.py`。成片、应交付 SRT、质量报告先全部准备，
  再按"备份旧值 → 替换 → 读回 → 失败回滚"发布；journal 记录进度，恢复失败报
  `publish_unconfirmed` 并保留材料；同目标用 `AttemptLock` 串行化；下次运行先恢复再开始。
  旧的"发布后单独写报告、失败只告警"已移除。
- **M02 审计身份**：`confirmed` 单元必须同时有非空 `audio_path` + `audio_sha256`，否则整份审计
  拒用并给出 `audit_identity_missing`；`prepared` 不贡献实测下限；坏 JSON / 非对象 / 错 schema
  分别返回 `audit_unreadable` / `audit_not_object` / `audit_schema_mismatch`。
- **M03 字幕必检**：有字幕合同即把 `subtitle_duration` 计入必检；缺失/不可读/未解析 ⇒
  `UNVERIFIED`，明确越界 ⇒ `BLOCK`；无字幕合同不新增要求；汇总按"必检项是否产出结果"判定。

### 2.1 M01 崩溃窗口与并发恢复（第二轮复核后，`631e269`）

- **先写意图再移动**：journal 在备份移动**之前**记录 `backup_pending` + 备份路径 + 旧摘要；
  崩溃落在"已移动、未写状态"的窗口时，恢复仍能凭 journal 找到备份。
- **恢复校验摘要与终态**：备份缺失/摘要不符/目标版本不明 ⇒ `publish_unconfirmed` 或
  `publish_conflict`，保留 journal 与备份，不再宣布 `recovered`；只有全部条目确定归位才删 journal。
- **单一目标锁**：主入口取得发布锁后把锁对象传给 `recover_pending` 与 `publish_batch`；
  恢复、旧状态读取、发布、读回确认、备份与 journal 清理都在同一锁内。第二个进程得到
  `publish_busy`，不会把别人正在发布的 journal 当残留恢复。
- 新增崩溃切点与并发测试：`STEP04_PUBLISH_FAULT=crash` 在"备份完成、状态未落盘"处退出 99，
  下一次运行先恢复再发布；另持锁进程使主入口失败且旧产物不变。

### 2.2 M01 恢复状态机（第三轮复核后，`fd2b124` 之后）

- **先验证再修改**：恢复时先确认备份有效，再决定是否改动目标；备份缺失/损坏时
  **不删除现存目标**，报 `publish_unconfirmed` 并保留 journal。
- **目标终态可识别**：目标摘要等于 `target_sha_before` 即视为"已完成恢复"（包括恢复移动
  成功、状态未落盘的窗口；旧值与新值相同时同样按已恢复处理）。
- **逐条持久化、可重复执行**：每恢复一个条目就写回 journal；中途失败后重试时，已恢复条目
  凭目标摘要识别为完成，不会重复消耗已用掉的备份。
- 新增失败对照：备份损坏且目标为合法新文件（目标保留）、旧值=新值且备份损坏（识别为已恢复）、
  多条目中途失败后重试（幂等）、恢复移动后状态未落盘（幂等）。

## 3. 离线验收读数（0 次真实模型调用）

| 范围 | 命令要点 | 读数 |
|---|---|---|
| 受影响全量（首轮） | `pytest tests/tts tests/step02_video_gen tests/step02b_video_gen tests/step03_tts_audio tests/step04_merge tests/integration/test_s01_isolation_baseline.py tests/integration/test_s05_duration_audit.py tests/integration/test_downstream_safe_delivery_s07.py` | **313 passed / 6 skipped** |
| 合并交叉（首轮） | 上述 + `tests/step02a_frame_gen` | **501 passed / 10 skipped** |
| 干净副本（`git archive 482676b`） | 相关 10 份测试 | **85 passed** |
| M01 修复后 step04+integration | `pytest tests/step04_merge tests/integration/test_s05_duration_audit.py tests/integration/test_downstream_safe_delivery_s07.py` | **117 passed / 2 skipped** |
| 恢复状态机修复后 step04+integration | 同上 | **121 passed / 2 skipped**（发布单测 `16 passed`） |
| M01 修复后合并交叉（主仓库，`output/` 在场） | `pytest tests/step02a_frame_gen tests/step02_video_gen tests/step02b_video_gen tests/step03_tts_audio tests/step04_merge tests/tts` + S01/S05/S07 | **626 passed / 10 skipped** |
| 干净副本（`git archive 631e269`，无 `.env.keys`/`output/`） | 发布/质量/身份/策略/样片相关 7 份测试 | **62 passed** |

skip 均为缺少正式 `output/` 章节产物的既有用例，已逐项核对原因；新增用例无 skip/xfail。

关键故障用例（均经真实入口 / 独立临时根）：

- `STEP04_PUBLISH_FAULT=srt|quality`：回滚后旧成片、旧 SRT、旧质量报告摘要不变，
  journal=`rolled_back`；
- `STEP04_PUBLISH_FAULT=quality,rollback`：`publish_unconfirmed`，备份与 journal 保留；
  去掉故障重跑时先恢复再发布，最终 `PASS` 且 journal 清理；
- 编码后终检 BLOCK（`MERGE_QUALITY_EXPECT_VIDEO_CODEC=h265`）：旧交付物不变；
- 审计身份：缺路径/摘要、摘要不符、坏 JSON、错 schema 均拒收且不产生超窗裁决；
  `prepared` 带 `actual` 也不贡献实测下限；
- 字幕：缺失/不可解析 ⇒ `UNVERIFIED`；越界 ⇒ `BLOCK`；无字幕合同 ⇒ `PASS`。

## 4. 生效配置

见 `config.json`。全部为默认值：`VOICE_TIER_POLICY=strict`、
`VIDEO_MEDIA_ENFORCE=0`、`MERGE_QUALITY_ENFORCE=0`、
`MERGE_AUDIO_OVERFLOW_POLICY=fail`。显式 `tiered` / `enforce=1` 的能力仍由既有测试覆盖，
未为通过默认测试而删除。

## 5. 保护清单

- 主仓库未跟踪文件 7 项（文件）的路径与 SHA-256 见 `baseline.json`，本轮复核后重新哈希一致；
  第 8 项 `baselines/2026-11-12-pre-layered/s06_ab/tools/__pycache__/` 是目录，原基线只记路径，
  现按内部 1 个文件的摘要记录在 `output-baseline.json`。
- 主仓库正式 `output/`：3191 个文件 / 989,104,390 字节，自 M06 起数量与总字节未变；
  **逐文件 SHA-256 清单**在仓库外
  `C:\myFloders\git\aiVideo\.archive\output-baseline-2026-11-27\sha256.txt`
  （清单文件 SHA-256 `a63b8255…a7f4b9`，见 `output-baseline.json`）。
  说明：M06 当时只有数量/总字节，**不能回溯证明历史内容未变**；逐文件清单是本次补充的
  前向基线，可证明此后未变。
- 未触碰 `.tmp/s14a`、`aiVideo-s14base`。

## 5.1 样片重建（新验证，非原运行产物）

首次合并前的 S07 样片已随 worktree 清理删除，归档中没有实体媒体。修复后从固定提交
`631e269` 隔离重建（0 模型调用），保存在仓库外：

`C:\myFloders\git\aiVideo\.archive\downstream-s07-rebuild-631e269\`

包含 `final.mp4`（10.0s，5,240,172 字节，SHA-256 `139c53da…99e91d`）、`subtitles.srt`、
`step04_quality.json`、`step04_media_quality.json`、`step03_duration_audit.json`、
`voice_state.json`、`step02b_quality.json` 与 `manifest.json`。
`manifest.json` 明确标注"新验证，非 2026-11-27 原运行产物"，并记录生成提交、生成器与模型调用数=0。

## 6. 未执行

真实 Step02b/Step03 付费调用、正式媒体采纳、生产章节重跑、远端推送、Stage 3/4。
