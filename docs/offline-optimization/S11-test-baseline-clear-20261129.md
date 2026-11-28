# S11 既有失败用例清零（2026-11-29）

> 目标：把 S01 冻结基线里的 63 个失败测试全部修到通过，且不降低生产语义、
> 不真实调用模型、不写正式 `output/`。
> 前序：S02–S10 已完成并推送 `6975603`；本包只改测试/测试支撑代码。

## 1. 结果

| 项 | S01 基线 | 本包后 |
|---|---:|---:|
| 收集 | 6,152 | **6,259** |
| passed | 5,904 | **6,074** |
| failed | 63 | **0** |
| error | 0 | **0** |
| skipped | 185 | **185** |

`tools/run_tests_grouped.py` 八组全部 `rc=0`（step01 289.3s / shared 25.5s /
step02a 34.8s / step02b 58.2s / step03 8.1s / step04 136.4s /
tools_text_integration 299.9s / rest 19.4s）。

证据：`.tmp/offline-closure-r1/S11/junit/{merged.xml,summary.json}`。

## 2. 根因与修法（按组）

### 2.1 时长合同已从 60–90s 演进到 60–120s（约 40 项）

`DURATION_MAX_SEC` 的生产默认已放宽到 120s，目标带 85–110s；一批旧用例仍按
60–90s 构造夹具或写死 90s 断言，于是“超预算/必须收敛”的前提消失。

- `constraints/test_duration_budget.py`：按 `duration_budget._MAX_TOTAL` 构造超限；
  “最低总需求仍超限”分支把模块上限固定为 40s；“issue 是 blocking”改用
  `quality_issues.DURATION_MAX_SEC + 20`。
- `test_duration_convergence.py`：本文件验证的是“超过生效上限才收敛/采纳”的判定，
  用 autouse fixture 把 `regen._mrt.DURATION_MAX_SEC` 固定为 90s，保留原 96s/104s
  夹具的判别力。
- `test_plan_feasibility.py`：本文件按 60–90s 合同编写（90.62s 判不可行），
  autouse fixture 固定 `validation._REDGUO_DURATION_*` 为 60/90；提示词预算断言
  改用 `shared.config` 的生效值。
- `test_outline_budget_and_repair_prompt.py`：用 `DURATION_MAX_SEC + 60` 触发校准，
  目标区间改为 `DURATION_TARGET_MIN/MAX_SEC`；不再写死 70–80s。
- `test_plan_phase_c/k`、`test_plan_phase_a/g`：把“31 句/8 条整句 Long”夹具改为
  可单镜表示、但总量超 120s 的 13–35 条 11.4s/3.65s 条目，恢复
  `budget_infeasible`（总量）而不再落到 `budget_unrepresentable`（逐条不可表示）。

### 2.2 审计复用合同新增 `duration_band` 字段（6 项）

`_reusable_final_audit` / `_memoizable_final_audit` 现在要求 payload 带生效
`duration_band`；旧测试 payload 缺该字段，于是“全部相符”也被判 `duration_band`
失配。修法：在 `test_reuse_miss_reason._payload`、`test_audit_content_identity._payload`、
`test_audit_round_diag._mk_reusable_audit`、`test_final_audit_reuse_inloop` 的
最小 payload 中写入 `shared.config.duration_band()`。

### 2.3 预算问题码区分总量与逐条（3 项）

`budget_infeasible` 表示“发声下界总量超上限”，`budget_unrepresentable` 表示
“单条台词/旁白一镜放不下”。旧夹具用整条 13.13s 长句（>12s 单镜 cap），只能
触发逐条不可表示；现改为 13 条 11.4s 可表示条目，总量 148s > 120s，恢复总量
记录，并同步断言 13 个逐条成本。

### 2.4 视觉 QC 改走受管 `requests.Session`（6 项）

`shared/image_qc._opencode_vision_chat` 已从 `requests.post` 改为
`_get_qc_session().post`（连接池/脏池重建）。旧测试仍打桩 `requests.post`，
于是真实请求撞上探针隔离。修法：测试替换 `image_qc._get_qc_session` 返回假
Session，保留 session.post 这一真实调用点；429/5xx/SSL/Retry-After/次数上限
的断言全部保留。

### 2.5 合并静音轨采样率（1 项）

后端已用 `AUDIO_SAMPLE_RATE=48000`，旧断言写死 44100。改为从
`step04_merge.backends.ffmpeg.AUDIO_SAMPLE_RATE` 取值。

### 2.6 视频桩身份与原生发声判据（3 项）

- 桩视频此前固定 `testsrc2`，不同 task 字节完全相同；现在用 task 序号改变
  `tone_hz`（440 + n×20），保留动态画面同时让字节/音频身份可区分。
- `generation_requirements()` 现在要求 `active_video_uses_native_voice()` **且**
  `active_video_supports_audio()`；测试补上第二个打桩，`audio_policy` 才进入 key。

### 2.7 测试磁盘治理 API 迁移（3 项）

`_pid_is_alive` / `_reap_stale_pid_dirs` 已迁到 `shared/test_scratch.py`，由
conftest 的 `_reap_planned` 执行。测试改用 `pid_is_alive` +
`plan_pid_reap(...)` + `conftest._reap_planned`，仍覆盖“死 PID 回收、活 PID 保留、
非 PID 形态不误删、根缺失不抛错”。

### 2.8 调用索引夹具漂移（1 项）

S06 记录的 6 份历史提示词 sha 与当前构建器已全部不同，旧用例却假设两份样本
仍 `match`。改为**自足判别**：用当前 `build_audit_prompt` 为一份临时夹具生成
recorded 基线（应 match），再改其原文（应 drift）——同时锁住“能认匹配”和
“能认漂移”，不依赖现场历史版本。

## 3. 约束核对

- `RULE_SOURCE_FINGERPRINT`：`4fba3d45a6851f41`（不变）
- `PROMPT_SOURCE_FINGERPRINT`：`6ef3ebdb49d4e0ce`（不变；仅测试改动）
- `ruff_gate`：PASS（F401 189 / F811 12 / F821 0）
- `output/锦衣夜行/`：656 文件 / 401,740,834 字节，缺失/新增/SHA 变化均为 0
- 零真实模型调用；所有媒体由本机 ffmpeg 桩生成
- 未改生产代码，只改测试与 `tests/enhancements/video_stub_provider.py`

## 4. 证据

| 内容 | 路径 |
|---|---|
| 全绿 JUnit/分组汇总 | `.tmp/offline-closure-r1/S11/junit/` |
| 分组日志 | `.tmp/offline-closure-r1/S11_grouped.log` |
| 正式产物保护对照 | `.tmp/offline-closure-r1/S10/protection_after.json`（656/656 不变） |
| 前序总报告 | `docs/offline-optimization/S10-final-report-20261128.md` |
