# 任务：自行排查并修复已确认的代码缺陷（排除决策项）

## 授权与范围
用户已授权 dsh 自行排查并修复"已确认的代码缺陷"。本任务**只修不需要产品语义裁决的代码 bug**；所有需要用户拍板的"决策项"一律**只查不改、停下报告**。

## 先读上下文
先读 `C:/myFloders/git/aiVideo/.pytest_tmp/dsh_tasks/remaining_problems_result_20261113.md`（上一轮独立核查报告，含每条问题的文件:行号证据与"文档自报 vs 代码实测"对照）。本报告就是你排查的基础；你仍须用 Read/grep 复核代码当前状态（不要只信报告）。

## 可以修复的"已确认问题"（非决策项，带测试改完单独 commit）

### F1（高）Step02a 全量运行漏镜不报
- 证据：`step02a_frame_gen/execution_plan.py:616-618` 的 `verify_plan_coverage` 在 `only_indices is None` 时 `wanted = sorted(entries)`，只检查计划内条目；`step02a_frame_gen/main.py:105-108` 随后把 `only_indices` 收窄为计划镜头。
- 修复：`only_indices is None` 时应拿**完整稿 `script["shots"]` 全集**与计划比较，缺镜报 `plan_coverage_missing_shot` 并零调用拒收。保留 `only_indices` 显式给定时的现有行为。
- 验证：补一条"计划漏掉镜头 + 全量运行"的用例，断言 `plan_coverage_missing_shot` 且桩生成器零调用；现有 `test_m05_plan_consumption` 不受回归。

### F2（高，条件触发）Step02b 在 plan-only / 候选模式写生产 `shots/`
- 证据：`step02_video_gen/prompt_builder.py:1945-1948` 的 `fit_refs` 把参考拼表目录设为 `shot_work_dir(...)`（生产 `shots/`）；`shared/ref_compose.py:543-563` 超限时 `mkdir`+写 `ref_sheet_*.png`；`step02b_video_gen/main.py:533-545` 的 `--plan-only` 只写 candidate `plans/`、`658-664` 的 production_snapshot 比较只在候选模式；`step02_video_gen/submit_poll.py:319-331` 的 `_get_shot_hash` 在 r2v 下也调 `_collect_r2v_references`，可能触发写。
- 修复：
  1. `fit_refs` 的拼表目录改为 attempt / 临时目录（绝不写生产 `shots/`）；
  2. `--plan-only` 也加 production snapshot 守卫；
  3. `_get_shot_hash` 不得产生写副作用（纯读路径）。
- 验证：构造"r2v + 参考超限"的桩场景，断言 plan-only / 候选运行后生产 `shots/` 未被修改（用 production_snapshot 前后 hash 比对）；补一条针对该写路径的测试。

### F3（中）Step02b 缓存命中绕过 `validate_requirements`
- 证据：`step02b_video_gen/main.py:263-266` 构造 `_reqs` 后仅 `logger.warn`；`:277-283` 先做 `cache_decision`，命中直接返回 `None`；真正的 `validate_requirements` 在 `step02_video_gen/submit_poll.py:1510-1519`，只在提交路径执行。
- 修复：在 cache 命中返回前先检查 `_reqs.issues`（含 `visual_plan_blocked` / `visual_plan_missing` / `start_end_state_mismatch`），有则跳过缓存、走 `validate_requirements`；或把 requirements 指纹纳入缓存 key。
- 验证：补一条"计划已 blocked + 旧缓存命中"用例，断言不复用缓存、仍走到 `validate_requirements` 并拒绝。

### F4（低）`verify_plan_execution` 对缺 path/sha 的参考条目静默跳过
- 证据：`step02a_frame_gen/execution_plan.py:535-541` `if not expected or not path: continue`。
- 修复：参考条目缺 `path`/`sha256` 时改报 `plan_reference_missing`（不要 `continue` 跳过）。
- 验证：补一条"被篡改计划删掉参考 path"用例，断言 `plan_reference_missing`。

### F5（低）Step02b 已知测试失败是夹具未跟上 `start_end_state_mismatch` 门
- 证据：`tests/step02b_video_gen/test_production_trace.py:164-169` 的 shot 只有 `motion_desc`、无 `ff_desc`/`lf_desc`；`step02_video_gen/execution_requirements.py:216-218` 产 `start_end_state_mismatch`；`submit_poll.py:1510-1519` 当阻塞。
- 修复：给该夹具补 `ff_desc`/`lf_desc`（或明确该测试只测缓存语义、不进 validate 路径）。优先补字段。
- 验证：该用例从 1 failed 转绿。

### F6（低）`issue_ledger` 身份不含"事实"维度
- 证据：`step01_script_gen/issue_ledger.py:60-70` 的 `issue_identity` 只返回 `(shot_id, field_path)`；模块 docstring 声称"镜头+字段+事实"。
- 修复：把 evidence/code 指纹纳入身份（避免同字段不同事实被合并）；同步修正 docstring 与 M03 文档口径。
- 验证：补一条"同字段两条不同事实"用例，断言身份不同、不被合并。

### F7（收尾）提交未提交的 `tools/build_m02_u6_candidate.py`（+26 行）
- 上一轮核查结论：这是复核第三轮"投影后残余"验证的合理延续，建议提交（不提交则 7/7 证据不可复现）。作为独立 commit 提交，不与源码修复混在一起。

## 必须停下、只查不改、报告的事项（决策项，用户保留现状）
- **G1** `issue_ledger.closed` 接进提交门（GATE #1，会改变准入语义）
- **G2** `framing_jump` 核心景别匹配口径变更（长词优先 / 跳过已授权字段，GATE #2）。注意：F 系列**不包含**核心匹配变更；若你顺手发现 `近景` 缺档位这类纯补表问题，可小修并测试，但**不要改动 `framing_jump` 的 replace / 匹配判定语义**。
- **G3** 提示词改"先定稿参考、再构建正文"（GATE #3，M06 结构性缺口）
- **G4** 正式候选采纳动作（GATE #4）
- **G5** `ADDED_DIALOGUE` 生产接线（用户已明确暂不接通，两条 `scan_script` 生产调用点保持不传 `source_text`）
- **G6** 声音"准备态/视频后确认态"两态拆分（P7，设计决策）
- **G7** `postprocess_reverted` 升级为拒绝/门禁（GATE，决策）

对上述任何一项，若你判断必须改默认行为才能修，停下并在报告里写明"需用户裁决"，不要静默改。

## 验证与提交纪律
- 用项目 Python `C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe` 跑 pytest。
- **避免全仓跑触发 safe-delete 批量门**：按目录小批独立跑（`tests/step01_script_gen`、`tests/step02a_frame_gen`、`tests/step02_video_gen`、`tests/step02b_video_gen`、`tests/shared`），每个目录单独 `--basetemp`、`-p no:cacheprovider`。若遇 safe-delete 误红，说明是环境问题，不要因此改代码。
- 每个 F 项修复后单独 commit，commit message 注明"非决策项代码修复 + 测试"；F7 单独 commit。
- **绝不碰**：`.workbuddy/`、`start_studio.bat`、4 个 GATE 决策相关默认行为、ADDED_DIALOGUE 生产接线、声音两态、postprocess 门禁升级。
- 不要做真实模型/视频/TTS 外部调用。

## 产出
1. 把每项 F1–F7 的实际改动 diff 要点 + 测试结果写入
   `C:/myFloders/git/aiVideo/.pytest_tmp/dsh_tasks/fix_confirmed_problems_result_20261113.md`
2. 末尾列"已修复 / 已停下待裁决(G1–G7)"清单，含 commit hash。
3. 若任何 F 项修复时发现其本质依赖某个 G 决策，立即停下该 F 项并标注"实际是决策项"，不要硬改。
