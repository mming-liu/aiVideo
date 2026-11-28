# O10 测试工程化与 `.gitignore` 白名单根治（2026-11-28）

> 承接 O13（`05f0495` + `d7453f5`）。零模型调用；正式 `output/` 未触碰。

## 1. 白名单清零（核心交付）

`6a4a3e7` 实测：`.gitignore` 里 `!tests/...` 白名单 **319 行**（含 O0–O13 期间新增的 13 行），
`tests/` 下 **348 个 `.py` 未被 git 跟踪**。本包按内容分类后处理：

| 类别 | 数量 | 处置 |
| --- | --- | --- |
| 含作品词/绝对路径/疑似密钥 | **168** | 迁入 `tests_local/`（保留原相对路径，不入库） |
| 仅依赖上述本地模块（import 级耦合） | **8** | 一并迁入 `tests_local/`（否则收集期 ImportError） |
| 不含作品词、无敏感串 | **172**（含 2 个非 py） | **入库**，成为受控契约测试 |
| `.gitignore` 规则 | **339 行** | 删除（`tests/*`、各目录 `tests/xxx/*` 与全部 `!tests/` 例外） |
| 新增规则 | 1 行 | `tests_local/`（不入库、`pytest.ini` 的 `testpaths = tests` 也不收集） |

分类是**内容扫描**（作品词表：书名/部名/主要人物/地名；风险串：`C:\Users\`、
`sk-`、`output/锦衣夜行`、`DEEPSEEK_API_KEY`），不是按目录猜。

验收：`tests/` 下不再有未跟踪文件；`!tests/` 行数 **319 → 0**；`pytest --collect-only`
无收集错误（6214 项，比清理前少 2320 项——少掉的正是本地作品词测试，它们仍在
`tests_local/` 里可手动跑）。

## 2. 测试分层标记与快速守卫

- `pytest.ini` 注册 `unit` / `contract` / `integration` / `slow` 四个 marker
  （只声明不强制；现有用例按需要再加标记）；
- `tools/run_tests_grouped.py --fast`：单进程跑 10 个零 I/O 的判据/配置/身份用例文件，
  实测 **28.5 秒 / 171 passed**，`-m "not slow"` 自动跳过慢用例；缺文件直接算失败
  （不静默缩小集合）。

## 3. `.gitignore` 卫生守卫

`tests/tools/test_workspace_hygiene.py` 新增：`!tests/` 行必须为 0、`tests/*` 不得存在、
`tests_local/` 必须被忽略——白名单不允许以任何形式回流。

## 4. 未完成（如实记账）

1. **`tools/` 63 个脚本盘点归档未做**：需要逐个核对「代码/测试引用」再迁 `tools/archive/`，
   属独立的低风险但耗时工作；本包不动 `tools/`（避免误伤现役脚本）。
2. **高 I/O 夹具提至 session 级未做**：`test_chain5_artifact_protection.py` 等用例的
   夹具重构会改变测试隔离边界，需要单独回归；本包只完成白名单/标记/快跑三件。
3. 迁入 `tests_local/` 的 176 个文件**没有**逐个人工审阅；它们默认不参与回归，
   后续如需入库应按「匿名化 → 补 allowlist → 全量回归」流程单独处理。

## 5. 施工中暴露的耦合（重要，已按同一规则处理）

白名单清零后，	ests/ 里有一部分**看似通用**的用例其实 import 了被隔离的本地模块，
分三轮暴露并处理：

| 轮次 | 现象 | 处置 |
| --- | --- | --- |
| 1 | 8 个文件在**收集期**报 ModuleNotFoundError: tests.step01_script_gen.test_* | 连同依赖一起迁入 	ests_local/ |
| 2 | 11 个测试目录缺 __init__.py（原文件未跟踪）→ 	ests/shared 等处收集期 No module named 'tts.models'（包语义丢失后 basedir 变化） | 为 11 个目录补空 __init__.py，	ests/integration/__init__.py 恢复原 docstring |
| 3 | 4 个**已跟踪**集成用例（	est_chain3/4/5*、_text_gen/test_send_quota.py）import 未跟踪的 	est_chain2_audio_merge / 	est_agent_plan_provider；1 个 shared 用例**函数内**import 未跟踪的 	est_ch1_frozen_hybrid_delta；1 个 	ests/tools/test_p4_admission_check.py 依赖仓里根本不存在的 	ools/p4_admission_builder.py | 一并迁入 	ests_local/（git rm --cached 后物理移走） |

**这些是既有脆弱性的暴露，不是本包引入的回归**：那 4 个已跟踪集成用例在干净检出上
本来就跑不起来（依赖的 fixture 从未入库），只是这台机器上恰好存在未跟踪文件。
代价：跟踪用例数减少（本地作品词测试与上述耦合用例不再进入默认回归，但完整保留在
	ests_local/）。
