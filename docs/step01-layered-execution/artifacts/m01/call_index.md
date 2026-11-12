# Step01 调用索引（M01）

> 由 `tools/build_step01_call_index.py` 生成；不要手工编辑。

输入：`baselines/2026-11-12-pre-layered/s06_ab`；夹具：`tests/step01_script_gen/fixtures/audit_narrative_families`

## 批次 `s06_ab`

> S06 报告所称的八次发送：6 次审计 A/B + 1 次受限修订 + 1 次终局审计

**授权链**：来源 `docs/step01-s06-contract-ab-2026-11-12.md`；声明范围 审计对照 6 次 + 受限修订 1 次 + 终局审计 1 次；声明上限 8；实发 8；核实状态 **declared_in_report**

> 报告自述「实际发送 8 次，未超出授权」；独立授权凭证（工单/收据）不在盘上，属报告自述口径

调用 8 次；累计耗时 983.0s；token 合计 153426

| # | 阶段 | 样本 | 契约 | 状态 | issues | blocking | 耗时 | tokens | 代码版本 |
|---|---|---|---|---|---|---|---|---|---|
| runs_20261112T070909/ab-01 | audit_ab | time_ellipsis_legal | old | ok | 3 | 1 | 114.3 | 15779 | 7286516 |
| runs_20261112T070909/ab-02 | audit_ab | time_ellipsis_legal | new | ok | 1 | 1 | 83.2 | 13431 | 7286516 |
| runs_20261112T070909/ab-03 | audit_ab | state_broken | old | ok | 3 | 2 | 111.3 | 15633 | 7286516 |
| runs_20261112T070909/ab-04 | audit_ab | state_broken | new | ok | 4 | 3 | 72.1 | 11624 | 7286516 |
| runs_20261112T070909/ab-05 | audit_ab | voice_broken | old | ok | 2 | 1 | 105.7 | 15060 | 7286516 |
| runs_20261112T070909/ab-06 | audit_ab | voice_broken | new | ok | 1 | 1 | 47.8 | 8890 | 7286516 |
| phase2_20261112T072011/repair-01 | repair | u6_candidate | repair_shot_replacements | ok | None | None | 41.0 | 23391 | — |
| phase2_20261112T072011/final-audit-01 | final_audit | u6_candidate | script_audit | ok | 11 | 0 | 407.6 | 49618 | — |

**原始响应不可用**：8/8 次调用。原因：运行脚本只保存 `raw_text[:400]`，而该批记录的 `raw_head` 为空；原始响应未落盘，只有解析结果

## 批次 `s06_ab_rerun`

> S06 之后的复跑批次（现场数据，未纳入版本管理）；不计入报告的八次口径

**授权链**：来源 `—`；声明范围 —；声明上限 None；实发 20；核实状态 **unverified**

> 无报告、无授权记录、无预算凭据。只能确认「盘上有 20 次真实发送」，不能判定它获得过哪次授权、也不能判定是否超出原八次额度

调用 20 次；累计耗时 2021.1s；token 合计 250228

| # | 阶段 | 样本 | 契约 | 状态 | issues | blocking | 耗时 | tokens | 代码版本 |
|---|---|---|---|---|---|---|---|---|---|
| 20261112T131449/ab-01 | audit_ab_rerun | time_ellipsis_legal | old | ok | 3 | 1 | 119.5 | 13667 | 0a2bcc4 |
| 20261112T131654/ab-01 | audit_ab_rerun | time_ellipsis_legal | old | ok | 2 | 1 | 119.2 | 12288 | 0a2bcc4 |
| 20261112T131654/ab-02 | audit_ab_rerun | time_ellipsis_legal | new | ok | 2 | 2 | 117.7 | 11817 | 0a2bcc4 |
| 20261112T131654/ab-03 | audit_ab_rerun | state_broken | old | ok | 2 | 2 | 150.1 | 13314 | 0a2bcc4 |
| 20261112T131654/ab-04 | audit_ab_rerun | state_broken | new | ok | 3 | 2 | 162.1 | 15693 | 0a2bcc4 |
| 20261112T131654/ab-05 | audit_ab_rerun | voice_broken | old | ok | 3 | 1 | 57.2 | 7110 | 0a2bcc4 |
| 20261112T131654/ab-06 | audit_ab_rerun | voice_broken | new | ok | 1 | 1 | 54.9 | 7196 | 0a2bcc4 |
| 20261112T134738/ab-01 | audit_ab_rerun | time_ellipsis_legal | old | ok | 3 | 2 | 135.2 | 15263 | 0a2bcc4 |
| 20261112T135000/ab-01 | audit_ab_rerun | time_ellipsis_legal | old | ok | 3 | 2 | 120.0 | 14167 | 0a2bcc4 |
| 20261112T135000/ab-02 | audit_ab_rerun | time_ellipsis_legal | new | ok | 1 | 1 | 95.3 | 12460 | 0a2bcc4 |
| 20261112T135000/ab-03 | audit_ab_rerun | state_legal | old | ok | 1 | 1 | 90.6 | 11286 | 0a2bcc4 |
| 20261112T135000/ab-04 | audit_ab_rerun | state_legal | new | ok | 1 | 1 | 165.9 | 19319 | 0a2bcc4 |
| 20261112T135000/ab-05 | audit_ab_rerun | voice_legal | old | ok | 2 | 2 | 108.0 | 13127 | 0a2bcc4 |
| 20261112T135000/ab-06 | audit_ab_rerun | voice_legal | new | ok | 2 | 1 | 92.9 | 11823 | 0a2bcc4 |
| 20261112T140115/ab-01 | audit_ab_rerun | time_ellipsis_legal | old | ok | 2 | 1 | 84.1 | 13924 | 0a2bcc4 |
| 20261112T140115/ab-02 | audit_ab_rerun | time_ellipsis_legal | new | ok | 1 | 1 | 82.3 | 10196 | 0a2bcc4 |
| 20261112T140115/ab-03 | audit_ab_rerun | state_legal | old | ok | 2 | 1 | 92.6 | 15058 | 0a2bcc4 |
| 20261112T140115/ab-04 | audit_ab_rerun | state_legal | new | ok | 3 | 2 | 90.1 | 16062 | 0a2bcc4 |
| 20261112T140115/ab-05 | audit_ab_rerun | voice_legal | old | ok | 2 | 1 | 53.3 | 9401 | 0a2bcc4 |
| 20261112T140115/ab-06 | audit_ab_rerun | voice_legal | new | ok | 3 | 0 | 30.1 | 7057 | 0a2bcc4 |

**原始响应不可用**：20/20 次调用。原因：同批次脚本只保存 `raw_head[:400]`，该批同样为空

## 样本漂移核对

用**当前**夹具重建当时发出的审计提示词，与记录的 `prompt_sha1` 比对。

| 样本 | 契约 | 结果 | 记录 | 当前 |
|---|---|---|---|---|
| state_broken | new | match | 880fb79fbc9d | 880fb79fbc9d |
| state_broken | old | match | 89f94c406d21 | 89f94c406d21 |
| time_ellipsis_legal | new | drift | 6cca1c6aeb49 | fd3d8759c931 |
| time_ellipsis_legal | old | drift | 5b5cca632df8 | b8fe76ad7eb4 |
| voice_broken | new | match | 37724d885f0b | 37724d885f0b |
| voice_broken | old | match | 13db0f3e75b8 | 13db0f3e75b8 |

核对 6 项：一致 4，漂移 2。

漂移的含义：该样本**当时的结论属于另一版样本**，不能当成当前样本的证据；当前版本若未重新发送，其真实模型表现记为**未验证**。
