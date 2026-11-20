# step01 main.py 按职责拆分（S00–S10）交付记录（2026-11-19）

分支 `refactor/pkg1-offline-hygiene`。拆分前基线固定在分支 `refactor/step01-split-base`（`75dbdc5`），拆分工具每次都从该基线重新生成。全程离线，0 模型调用，未碰 `output/`。已于 2026-11-19 合并到 master 并推送（`2f1e3f9`）。门面恢复缓存的复核修正（P2-5）见 `docs/pkg1-offline-hygiene-20261119.md` 第 6 节。

## 1. 结果

- `step01_script_gen/main.py`：14561 行降到 **1280 行**，只保留运行范围 bootstrap、门面安装、`_fatal_summary`、`_presentation_mode_argv`、`main()`。
- 实现拆成 `step01_script_gen/orchestrator/` 下 14 个模块（另加 `__init__.py`、`_facade.py`），依赖只指向前面的模块，无环：

| 顺序 | 模块 | 行数 | 职责 |
|---|---|---|---|
| 1 | runtime | 1001 | 导入期副作用、路径与日志、控制台重定向、锁、超时、指标、计时、预算；项目内导入的汇集点 |
| 2 | scoring | 758 | 告警收集、候选质量与打分、质量门 |
| 3 | regen | 1398 | 退化判定、超限与结构性重生成、拆镜 |
| 4 | repair_apply | 1292 | 补丁与替换镜头的应用、字段写入审计 |
| 5 | final_audit | 1223 | 终审、审计复用、诊断台账、冻结预算续验 |
| 6 | repair_accept | 952 | 问题身份、已解决判定、修复采纳 |
| 7 | finalize | 357 | 确定性定稿、`project_for_commit`、钩子开关 |
| 8 | repair_targets | 886 | 目标定位、联动组、回滚、通道与字节预算 |
| 9 | repair_round | 2469 | `_repair_shot_warnings`（函数本身未拆） |
| 10 | persist | 440 | 资产提取、运行快照、修复轮落盘 |
| 11 | blocked_ledger | 412 | 阻塞修订台账 |
| 12 | generation | 789 | spy/hook 脚本生成、候选生成 |
| 13 | commit | 1256 | 提交、采纳凭据、钩子质量门 |
| 14 | best_guard | 388 | 跨运行 BEST-GUARD 选稿 |

## 2. 做法

- **搬迁工具** `tools/split_step01_main.py`（`plan` / `apply` / `verify`）：
  - 自带作用域分析，只改写指向其他模块的全局引用（写成 `_mrt.X` 这类模块属性访问）；局部变量、参数、闭包一律不动。
  - 跨模块写全局变量或引用仍留在 main.py 的名字，直接报错。
  - 迁出代码里的 `__file__` 改为 `runtime._MAIN_FILE`（原 main.py 路径）。
  - 先写临时目录，校验全部通过才替换文件。
- **等价校验**：把 `_mrt.X` 还原成 `X` 后，原 main.py 的 350 条顶层语句与新文件里的语句逐条比对 AST，全部一致，0 处不一致。
- **门面** `orchestrator/_facade.py`：对 `step01_script_gen.main.X` 的读、写、删转发到 X 所在模块。
  - 在 main 的 `__dict__` 里保留镜像，`mock.patch` 判为本地属性，退出时用 setattr 恢复原值，嵌套 patch 逐层恢复。
  - 访问 `__dict__` 时先刷新镜像，避免所属模块内部改写全局后镜像过期。
  - `delattr` 写回首次覆盖前缓存的原值，不做真删除。
  - 契约测试 11 条，覆盖 `mock.patch` 退出后所属模块原函数仍在、嵌套恢复、`monkeypatch.delattr`、from-import、镜像刷新。

## 3. 与计划的差异

1. **`_deps` 并入 `runtime`**：原 main.py 的项目内导入与 RunContext 安装、日志初始化、env 守卫交错执行。全部留在 runtime，可以按原顺序执行；测试对 `main.<导入名>` 的打桩转发到 runtime，所有使用方经 `_mrt.X` 都能看到。
2. **新增 `finalize` 模块**：`project_for_commit`（修复目标层要用）依赖 `finalize_step01_script` 等提交簇函数，会形成 repair_targets ↔ commit 回边，所以下沉为独立模块。另外 `_INTEGRITY_NUM_RE` 归 scoring，repair_apply 排到 final_audit 之前。
3. **以 `__main__` 运行时**，`plan_feasibility` 在函数内再导入一份 `step01_script_gen.main`。原来那份会重新执行头部，生成第二个 logger 和第二份章节常量；现在两份 main 共享同一组编排模块，头部只执行一次。

## 4. 验证

| 场景 | 结果 |
|---|---|
| 全量对账（`tools/run_tests_grouped.py` 8 组分进程，`junit_s00` 对比 `junit_s10`） | passed 7589 / skipped 303 / xfailed 4 / failed 12；**未解释差异 0**；新增通过 22 条（门面契约 11、S09 守卫 8、指纹 1、ruff 门禁 2）；12 条失败与基线相同 |
| 每个模块搬迁后 | AST 等价校验、ruff 门禁（含搬迁抵消）、`main.py --help`、7 个提交/修复/运行范围相关测试文件（157 条）全部通过 |
| S05 / S06 全量对账中发现的 7 条差异 | 都是测试按 main.py 源码文本断言（被别名前缀或文件位置打断），改为经 `tests/verification_helpers.step01_source_text()` / `step01_getsource()`（去掉别名前缀）读取后通过；S10 全量为 0 差异 |
| S09 静态守卫 | 只允许 `from step01_script_gen.orchestrator import <模块> as <别名>`；依赖只指向前面的模块；只有 main.py / orchestrator / tests / 拆分工具可以导入 orchestrator；main.py ≤1500 行；orchestrator+main 的 F401/F811 不超过拆分前 15 条；三类违规注入后都被拦下 |
| ruff | F821 0；搬迁带走的 F401/F811 按 `--moved-from/--moved-to` 抵消；main.py 不再导入的 5 条未用标准库导入随之消失，基线下调 |

## 5. 指纹

- `RULE_SOURCE_FINGERPRINT`：`940851c2689ca04f` 变为 `81a6566874e99b99`。原因是 S04 给 `quality_issues.py` 的提示词指纹加入 `orchestrator/`，而这个文件本身在规则指纹清单里。影响与第 1 包相同：已提交章节需要重跑 step01。
- `PROMPT_SOURCE_FINGERPRINT`（诊断字段）：现在覆盖 `main.py` + `orchestrator/*.py`，值为 `d4d749e5014fe8cc`。

## 6. 改动过的测试文件

全部原件在 `.tmp/step01_split_backup/tests`（672 个文件，打包摘要 sha256 `d49cc671…`）。

- 已入库：`verification_helpers.py`、`shared/test_step01_log_isolation.py`、`test_model_prompt_profile.py`、`test_p0_fault_injection.py`、`test_plan_feasibility.py`、`test_plan_phase_d_wiring.py`、`test_repair_answer_example.py`、`test_repair_shot_replacements.py`、`test_step01_commit.py`、`tools/test_ruff_gate.py`
- **仅本地（未入库）**：`shared/_stage_head_probe.py`、`test_fingerprint_split.py`、`test_metrics.py`、`test_outline_budget_and_repair_prompt.py`、`test_prop_state_anchor.py`、`test_repair_effects.py`、`test_repair_mask_baseline.py`、`test_review_fixes_0915.py`、`test_rules_index.py`
- 新增并入库：`test_main_facade_contract.py`、`test_orchestrator_guards.py`

## 7. 后续（未做）

- P2：把测试的打桩点迁到所属模块，然后去掉门面转发。
- P3：拆超大函数（`_repair_shot_warnings` 2400+ 行、`main()`、`_commit_step01_outputs`、`_apply_shot_replacements`、`_generate_script_candidates`），每个函数需要先补行为对照测试。
- 本包没有内容质量收益，只降低以后维护和改动 step01 的成本。
