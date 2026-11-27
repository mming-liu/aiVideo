# 下游安全出片包（S01–S07）证据归档 — 2026-11-27

本目录位于任务 worktree 之外（`docs/` 随 fast-forward 合入 master 后仍在主仓库），
保存可复核的最小证据。**大型媒体、原始日志和缓存不在此目录**，仍保留在
`.tmp/worktrees/downstream-safe-delivery/` 直至清理前，见 `cleanup-manifest.md`。

## 1. 交付身份

| 项目 | 值 |
|---|---|
| 验收代码提交 | `482676b`（`M01-M03: recoverable publish batch, audited duration identity, subtitle required checks`） |
| 合并目标 | master（合并前 HEAD `507ccea`） |
| 分支 | `codex/downstream-safe-delivery` |
| 合并方式 | `git merge --ff-only <验收提交>` |

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

## 3. 离线验收读数（0 次真实模型调用）

| 范围 | 命令要点 | 读数 |
|---|---|---|
| 受影响全量 | `pytest tests/tts tests/step02_video_gen tests/step02b_video_gen tests/step03_tts_audio tests/step04_merge tests/integration/test_s01_isolation_baseline.py tests/integration/test_s05_duration_audit.py tests/integration/test_downstream_safe_delivery_s07.py` | **313 passed / 6 skipped** |
| 合并交叉 | 上述 + `tests/step02a_frame_gen` | **501 passed / 10 skipped** |
| 干净副本（`git archive 482676b`，无 `.env.keys`、无 `output/`） | 上述相关 10 份测试 + 新增发布/身份/字幕用例 | **85 passed** |

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

- 主仓库未跟踪文件 8 项（7 个文件 + 1 个目录）的路径与 SHA-256 见 `baseline.json`；
  清理前后用同一脚本复核。
- 主仓库正式 `output/`：3191 个文件 / 989,104,390 字节，未做任何写入；
  本包只在隔离 worktree 的 `output/` 与 `tmp_path` 内产生测试产物。
- 未触碰 `.tmp/s14a`、`aiVideo-s14base`。

## 6. 未执行

真实 Step02b/Step03 付费调用、正式媒体采纳、生产章节重跑、远端推送、Stage 3/4。
