# 第 1 包：离线卫生整改 交付记录（2026-11-19）

分支 `refactor/pkg1-offline-hygiene`，基于 master `8bea852`。全程离线，0 模型调用，未碰 `output/` 正式产物。未合并、未推送。

## 1. 冻结基线（M00）

| 项 | 读数 |
|---|---|
| 全量分组测试（`tools/run_tests_grouped.py`，8 组分进程） | passed 7534 / skipped 303 / xfailed 4 / error 3 / failed 12 |
| import 吞错位点（`tools/scan_import_swallow.py`） | 71 处（A 组 43、B 组 28） |
| ruff 0.17.5（F821/F401/F811，清单目录） | 267 条：F401 238、F811 17、F821 12 |
| `RULE_SOURCE_FINGERPRINT` | `b9fb2311246e0dfa` |
| `PROMPT_SOURCE_FINGERPRINT` | `ce7a6b699a8de4fd` |

基线里 15 条失败/错误与本包无关（`test_run_provenance` 4、`test_disk_governance` 3、`test_tts_providers` 2、`test_video_reroll` 2、`test_content_binding_ratchet` 1，以及 `test_plan_phase_m_composition` 3 条 setup error）。

## 2. 改动与位点处理

| 模块 | 提交 | 内容 |
|---|---|---|
| M00/M01 | `01d9bbe` | 分组跑测、JUnit 对账、吞错扫描脚本；`shared/degrade.py`（`note_degraded` 自身不抛异常，同一 site 最多输出 5 条日志，登记上限 200） |
| M02 | `910a085`、`9db3c01` | A 组 40 处：import 提到 `try` 之前，失败直接报错；`try` 里其余语句的异常改为 `note_degraded` 登记。`9db3c01` 补回 codemod 丢掉的两段说明注释 |
| M03 | `b11ba4a` | F821 安全修复：`shared/config.py` 补 `Optional`；`vimax_adapters` 两处超时告警补 `log_utils` 导入；`content_verify` 补 `PresentationContractError` 导入；删掉 `main._kf_structural_violations` 里写入未定义 `_roster` 的死代码 |
| M04-a | `fcfc748` | `vimax_bridge._maybe_qc_frame` 补 `maybe_qc_sync` 导入，恢复图片 QC |
| M04-b | `5fe6abf` | `_repair_shot_warnings` 补 `allowed_repair_fields` 导入，锁定字段审计项预过滤生效 |
| M04-c | `47b1ee5` | `prompt_builder._collect_r2v_references` 补 `vimax_working_dir` 导入，r2v 首帧缺失兜底生效 |
| M05 | `c355001` | B 组 28 处，加上从 A 组移过来的 3 处（`style_prefix` 的 `sanitize_prompt`/`sanitize_script_shots`、`vimax_bridge._extract_char_layout`，这 3 处在指纹的符号闭包里）；`validation` 两处 F821；`engine` 补 `Callable` |
| M06 | `44d867d`、`37b6ff6` | `ruff.toml`、`requirements-dev.txt`、`tools/ruff_gate.py`（含 `--init`/`--update`）、`tools/ruff_baseline.json`；排除 `skills/`、`.agents/` |
| M07 | `7b8cb2b` | `tools/print_effective_config.py` |
| M08 | `9167f74` | README 3 处；`AGENTS.md`/`problems.md`/`todolist.md` 的 `TODOLIST.md` 改为 `todolist.md`。git 历史里从未有过 `TODOLIST.md`，所以 `todolist.md:98` 也一并改了 |

例外处理：
- `constraints/engine.py:_raise_env_fault` 的 import 留在 `try` 内。原语义是“日志模块坏掉也必须抛出原异常”，提到 `try` 外会改成抛 ImportError。handler 已改为登记降级，不再静默。
- 71 处位点整改后重新扫描：0 处。

## 3. 验证

| 场景 | 结果 |
|---|---|
| 全量对账（`junit_base` 对比 `junit_final`，`tools/compare_junit.py`） | passed 7567 / skipped 303 / xfailed 4 / failed 12；新增通过 30 条（本包测试）；变化 3 条（`test_plan_phase_m_composition` 从 error 变 passed，见下）；未解释差异 0 |
| 规则指纹 | M02–M04 后为 `b9fb2311246e0dfa`，未变；M05 后为 `940851c2689ca04f` |
| 新测试的判别力 | M03（5 条）、M04-a（2 条）、M04-b（2 条）、M04-c（1 条）、M05（2 条）都在修复前的代码上失败、修复后通过 |
| ruff 门禁 | 本地 F401 256、F811 17、F821 0，返回 0；临时注入一条 F821 和一条 F401 后返回 1 并逐条列出；删除注入后恢复为 0 |
| 干净检出（`git worktree`，没有 `.env.keys`） | 本包 30 条新测试通过；ruff 门禁返回 0（F401 241，比本地少 15 条，差额来自本地未跟踪文件；按路径计数，不影响已跟踪代码的判定） |
| 生效配置工具 | `--stage step01 --json --chapter 3`：`NOVEL_CHAPTER` 显示为 `cli`/`3`；输出里没有 `sk-` 明文 |

`test_plan_phase_m_composition` 的 3 条：基线中 setup 阶段报 `FileNotFoundError: .pytest_tmp\…\chapter_001_scenes.json`，依赖其他用例在测试临时目录里写出的文件，是已有的测试隔离问题，与本包无关。

运行环境问题：中途有一次全量运行（`junit_m05`）出现 710 条 setup error，报错是 `%TEMP%\pytest-of-ee54\pytest-0` 不存在。原因是两次全量运行时间重叠，pytest 的临时目录被另一个会话回收。单独复跑相关用例全部通过，作废该次运行，改为单独完整重跑 `junit_final`，结果见上表。

## 4. 合并后的行为变化（需要知晓）

1. **图片 QC 恢复**（M04-a）：11-10 之后 step02a 生成的帧都没有经过 QC。合并后每张图会调用一次 QC（`QC_PROVIDER=opencode`），判为 critical 时最多重抽 `QC_MAX_ATTEMPTS=3` 次，会产生 02a 费用。
2. **锁定字段预过滤生效**（M04-b）：只涉及锁定字段的阻塞审计项不再派给修复轮，step01 的修复结果可能因此改变。
3. **规则指纹变化**（M05）：chapter_001/002 已提交的 step01 结果会被下游质量门（`shared/quality_gate.py:552`）拒绝，重跑 step01 后恢复。
4. **原先静默的失败现在会输出** `[DEGRADED] <site>: <异常>` 告警；仓库内模块 import 失败会直接报错。

## 5. 未覆盖

- 没有真实模型运行，QC 恢复后的实际效果要等下一次 step02a 才能看到。
- 本包新增测试之外的大部分测试仍未入库，干净检出只证明本包新增的 30 条测试可复现，不代表全量测试通过。
- F401/F811 存量没有整理（不做 `--fix`，以免删掉被测试打桩的名字）。
