# S04 契约覆盖恢复与集合差异映射（2026-11-28）

> 对应计划 S04：把 O10（`e3ef2e2`）移出受控测试集的 7 个文件逐项恢复为受控、
> 自足的基础契约测试。零真实模型调用、零正式 `output/` 写入。

## 1. 基线与范围

| 项 | 值 |
|---|---|
| 基线提交 | `50965dd`（S03）+ 本模块工作树改动 |
| 验收解释器 | Python 3.14.2 |
| 退出受控集来源 | `e3ef2e2 chore(tests): retire the tests/ allowlist and add fast/marker guards (O10)`；7 个文件当时迁入 `tests_local/` |
| 本模块新增受控测试 | `tests/_text_gen/test_send_quota.py`、`tests/integration/test_artifact_protection_contract.py`、`tests/shared/test_s36_identity.py`、`tests/step01_script_gen/test_commit_identity.py`、`tests/step03_tts_audio/test_plan_cache.py`、`tests/tools/test_p4_admission_check.py`、`tests/tools/test_voice_unit_report_plan.py` |
| 新增受控支持 | `tests/_text_gen/text_service_support.py`（AgentPlan 服务/200 响应桩，替代跨测试文件导入） |
| 生产修复 | `tools/voice_unit_report.py::load_report_plan`：只有**没有 v2 schema**的旧缓存可降级读取；v2 缓存的章节/稿/摘要不符必须拒绝 |

## 2. 逐项覆盖映射

| 原测试 | 原保护契约 | 新入口／夹具 | 保留或改变的断言 | 实际结果 |
|---|---|---|---|---|
| `tests_local/_text_gen/test_send_quota.py` | 每次实际 POST 前原子占用额度（含重试）；第 41 次不到传输层；额度文件损坏/非法 fail-closed；并发不超额；`init_file` 不覆盖 | `tests/_text_gen/test_send_quota.py` + `tests/_text_gen/text_service_support.py`（受控构造器） | 断言逐条保留；仅把跨测试文件的 helper 导入改为支持模块 | 9 passed |
| `tests_local/integration/test_chain5_artifact_protection.py` | 正式产物摘要清单 → 逐字节复核；缺失/篡改/跨章污染分别报出；恢复后变绿；非正式日志单列 | `tests/integration/test_artifact_protection_contract.py`（自足临时产物组） | 保留通用保护机制与全部四类对照；**未恢复**依赖冻结 2026-10-03 基线、`chain_support` 与真实 `output/` 的活体用例（外部数据回放，见 §4） | 6 passed |
| `tests_local/shared/test_s36_identity.py` | 身份缺失补齐、错部/错章/类型冲突拒收；身份刷新只允许缺失字段、伪造来源/内容改动拒收 | `tests/shared/test_s36_identity.py` | 断言保留；作品名改为匿名占位（甲部/乙部） | 7 passed |
| `tests_local/step01_script_gen/test_commit_identity.py` | 提交点在质量门/审计前拒收身份冲突，不写成 COMMITTED | `tests/step01_script_gen/test_commit_identity.py` | 断言保留；作品名匿名化 | 1 passed |
| `tests_local/step03_tts_audio/test_plan_cache.py` | v2 计划缓存的 scope/revision/摘要绑定；缺失、错身份、摘要不符、旧 schema 分别拒绝；稿内计划优先；声音上下文消费缓存 | `tests/step03_tts_audio/test_plan_cache.py` | 断言保留；scope 匿名化 | 8 passed |
| `tests_local/tools/test_p4_admission_check.py` | 五个变体的准入裁定、解析期拒绝、零 service 卡口、CLI 落盘 | `tests/tools/test_p4_admission_check.py` | 断言保留；builder 从**不存在的** `tools/p4_admission_builder.py` 改为仓库已跟踪的 `tests/tools/p4_admission_builder.py`（路径加入 `sys.path`） | 10 passed |
| `tests_local/tools/test_voice_unit_report_plan.py` | 声音报告复用 v2 缓存校验；错章/摘要不符拒绝 | `tests/tools/test_voice_unit_report_plan.py` | 原 2 例保留并生效；新增 1 例钉住“只有无 schema 的旧缓存可降级读取” | 4 passed |

合计：**45 passed**（7 个文件；Python 3.14.2，本机零网络护栏生效）。

## 3. 干净副本验收

只含**受控代码与声明依赖**的隔离副本（`git archive 50965dd` 解包 + 本模块 9 个受控文件；
不含 `.env.keys`、`output/`、`.tmp` 现场数据）：

```text
python -m pytest tests/_text_gen/test_send_quota.py ^
  tests/integration/test_artifact_protection_contract.py ^
  tests/shared/test_s36_identity.py ^
  tests/step01_script_gen/test_commit_identity.py ^
  tests/step03_tts_audio/test_plan_cache.py ^
  tests/tools/test_p4_admission_check.py ^
  tests/tools/test_voice_unit_report_plan.py -q
```

| 轮次 | 结果 |
|---|---|
| 首次 | 45 passed；发现 `make_text_service` 只改实例属性，无 `.env.keys` 时构造失败 → 改为同时钉 `TextGenCfg` 类属性后通过 |
| 删除 `__pycache__` / `.pytest_cache` / `.pytest_tmp` 后重跑 | **45 passed**（独立重跑成立） |

证据目录：`.tmp/offline-closure-r1/S04/clean_copy.txt`（命令与读数）。

## 4. 未恢复项（显式外部数据回放，不算基础测试）

`tests_local/integration/test_chain5_artifact_protection.py` 中以下用例依赖本机历史现场，
不进入基础测试集；其通用保护逻辑已由 §2 的自足子集覆盖：

- `test_frozen_production_artifacts_are_byte_identical`：依赖 2026-10-03 冻结基线树（已退役 `output/video`）。
- `test_both_chapters_products_are_under_stage_protection` / `test_unchanged_chapter_products_pass...` /
  `test_tampered_chapter_product_is_detected` / `test_missing_chapter_product_is_detected`：依赖真实两章产物。
- `test_candidate_pipeline_does_not_touch_production`：依赖 `chain_support` 与真实章节目录。
- `test_transaction_tool_reports_production_unchanged`：子进程跑三包 fixture（已在 `tests/enhancements` 有独立受控覆盖）。
- `test_legacy_baseline_mismatch_is_documented`：依赖历史基线树。

## 5. 集合差异与后续

- 受控测试集新增 **7 个文件 / 45 例 + 1 个支持模块**；`tests_local/` 原文件保留不动（未删除、未改写）。
- `tests/_text_gen/test_agent_plan_provider.py` 改用同一支持模块；断言与用例数不变（52 passed 联跑）。
- 生产修复 `voice_unit_report.load_report_plan` 只收窄“旧格式兼容”的适用面，不改变 v2 缓存通过路径。
