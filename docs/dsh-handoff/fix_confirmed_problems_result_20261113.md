# 已确认代码缺陷修复结果（F1–F7，2026-11-13）

- 仓库：`C:\myFloders\git\aiVideo`
- 基线：`530fdbb`（上一轮独立核查报告 `remaining_problems_result_20261113.md` 的 HEAD）
- 本轮最终 HEAD：`9c63583`
- 范围：只修不需要产品语义裁决的已确认代码缺陷；G1–G7 决策项只查不改。
- 外部调用：**零**。没有发起真实模型 / 视频 / TTS 调用；全部为离线测试与桩 provider。
- 测试 Python：`C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe`
- 测试隔离：每个目录单独 `--basetemp`、`-p no:cacheprovider`；测试进程内关闭 safe-delete shim（`CODEBUDDY_SAFE_DELETE_ENABLED=0` / `CODEBUDDY_SAFE_DELETE_SANDBOX=0`），不改仓库文件策略。

## 0. 提交总览

| 项 | 内容 | commit |
|---|---|---|
| F1 | Step02a 全量运行用完整稿镜头全集校验计划覆盖 | `704ab54` |
| F2 | Step02b r2v 拼表只落 attempt/临时目录；plan-only 生产快照守卫；缓存哈希纯读 | `54bc040` |
| F3 | Step02b 执行要求有问题时跳过缓存，交给 `validate_requirements` 拒收 | `13602af` |
| F3 收口 | 候选提交/轮询日志落 attempt，避免跳过缓存后写正式 `step02.log` | `9c63583` |
| F4 | Step02a 参考条目缺 `path`/`sha256` 报 `plan_reference_missing` | `4e5e1ef` |
| F5 | Step02b `test_production_trace` 夹具补 `ff_desc`/`lf_desc` | `f80f973` |
| F6 | `issue_ledger` 身份纳入事实指纹；同字段不同事实不合并 | `cd4b332` |
| F7 | 单独提交 `tools/build_m02_u6_candidate.py`（+26 行） | `907fd06` |

## 1. F1（高）Step02a 全量运行漏镜不报

### 实际改动

- `step02a_frame_gen/execution_plan.py:600-633`
  - `verify_plan_coverage` 新增 `script` 参数。
  - `only_indices is None`（全量运行）时，`wanted` 改为完整稿 `script["shots"]` 的 index 全集；计划缺镜时仍报 `plan_coverage_missing_shot`。
  - 显式给 `only_indices` 时保持原行为（只核请求范围）。
  - 未给 `script` 的旧库调用退回计划条目（生产入口总是传 `script`）。
- `step02a_frame_gen/main.py:98`
  - 真实入口把完整 `script` 传给 `verify_plan_coverage`；随后 `only_indices` 才收窄为计划镜头。

### 测试

- 新增 `tests/step02a_frame_gen/test_m05_plan_consumption.py::test_full_run_with_shot_missing_from_plan_is_refused`
  - 计划只覆盖镜 1+2，稿件还有镜 3；全量入口断言 `plan_coverage_missing_shot`、桩生成器零调用。
- 现有 `test_m05_plan_consumption` 全部通过。

### 结果

- 定向：`7 passed`（F4 后同一文件 `9 passed`）。
- 目录回归：`tests/step02a_frame_gen` **105 passed / 4 skipped**（4 skip 为外部数据集缺失，非本次改动）。

## 2. F2（高，条件触发）Step02b plan-only / 候选写生产 `shots/`

### 实际改动

1. **拼表目录不再固定为生产 `shots/`**
   - `step02_video_gen/prompt_builder.py`
     - `_make_request(..., ref_sheet_dir=None)` 新增参考表目录参数。
     - `_collect_r2v_references(..., sheet_dir=None, materialize=True)`：
       - 有 `sheet_dir` 时只写该目录；
       - 未给时使用系统临时目录（`tempfile.mkdtemp(prefix="aivideo_r2v_refs_")`），**不再导入/调用 `shot_work_dir`**；
       - `materialize=False` 为纯读模式：不拼表、不落盘，返回未压缩源参考列表。
2. **plan-only 生产快照守卫**
   - `step02b_video_gen/main.py`
     - `build_plan(..., ref_work_dir=None)`：把 `ref_work_dir/<idx>` 传给 `build_frozen_for_shot(..., ref_sheet_dir=...)`。
     - `_plan_only`：`production_snapshot` 前后比对；任何正式文件新增/删除/改写 → `production_write_detected` + `EXIT_TOOL_FAIL`，不再只写候选 `plans/` 就算隔离成立。
     - plan-only 的参考表落候选根 `plans/refs/<idx>/`。
   - `tools/video_reroll.py` 的 `cmd_plan` 同样把参考表落 `out_root/refs/`。
3. **`_get_shot_hash` 纯读**
   - `step02_video_gen/submit_poll.py`
     - r2v 哈希收集改为 `_collect_r2v_references(..., materialize=False)`；不拼表、不写盘。
4. **真实提交路径落 attempt**
   - `_submit_video_candidate` 给 `make_request` 传 `ref_sheet_dir=attempt / "refs"`。
   - `build_frozen_for_shot(..., ref_sheet_dir=None)` 透传给 `_make_request`。
5. **F3 收口：候选日志隔离**
   - `_submit_video` 在 `candidate_context` 提供 `log_dir` 时，把提交/轮询日志写到该实验目录；否则才写 `chapter_dir()/step02.log`。
   - 背景：F3 让“执行要求有问题”的候选运行不再被缓存提前返回，于是会真正进入 `_submit_video`；此前该函数固定用章级 `step02.log`，会破坏候选运行“正式产物逐字节不变”的判据。

### 测试

- 新增 `tests/step02_video_gen/test_r2v_sheet_isolation.py`（3 条）
  - 6 张肖像 + 上限 3：真实 `_collect_r2v_references` 拼表只落给定 `sheet_dir`，生产 sentinel 目录无 `ref_sheet_*`。
  - `_get_shot_hash` 在 r2v 下不产生任何 `ref_sheet_*.png`（纯读）。
  - `materialize=False` 返回未压缩源参考。
- 新增 `tests/step02b_video_gen/test_plan_only_isolation.py`（2 条）
  - plan-only + 桩拼表：参考表落候选 `plans/refs/1/`，正式 `shots/clips` 前后 hash 一致。
  - 注入“拼表误落正式 shots”的假 `build_plan`：`_plan_only` 返回 `EXIT_TOOL_FAIL` 并报 `production_write_detected`。
- 新增 `tests/step02b_video_gen/test_candidate_isolation_r2v.py`（1 条）
  - 真实 `_submit_video_candidate` + 桩 provider：`ref_sheet_dir` 在 attempt 内，正式 `shots/clips` 前后 hash 一致。
- 定向结果：F2 三个文件分别 `3 passed` / `2 passed` / `1 passed`。

### 行为说明

- r2v 缓存哈希由“拼表后的 sheet 字节”改为“未压缩源参考字节”。这是一次性缓存失效（旧 r2v 产物不会命中新 key），不影响正确性；换来哈希路径零写副作用。

## 3. F3（中）Step02b 缓存命中绕过 `validate_requirements`

### 实际改动

- `step02b_video_gen/main.py::_process_one_shot_skip_frames`
  - 先取 `_req_issues = list(_reqs.get("issues") or [])`。
  - 仅当 `_req_issues` 为空时才执行 `cache_decision`（含 clip 补拷分支）。
  - 有问题时跳过整个缓存分支，直接 `_submit_video(..., execution_requirements=_reqs)`；提交路径在 `prepare_request` 后执行 `validate_requirements`，阻塞则零 POST 拒收。
- `step02_video_gen/submit_poll.py::_submit_video`
  - 候选上下文有 `log_dir` 时日志落实验目录（见 F2 第 5 点），保证跳过缓存后仍不写正式日志。

### 测试

- 新增 `tests/step02b_video_gen/test_cache_requirements_gate.py`
  - 构造 `visual_plan.json` 本镜 `status=blocked`。
  - 构造旧缓存：`video.mp4` + 匹配的 `prompt_hash.txt` + 无冻结快照（旧逻辑会 legacy 命中）。
  - 断言：`cache_decision` 零调用、`service.posts == 0`、失败记录 code 为 `visual_plan_blocked`、不返回可轮询任务。
- 定向：`1 passed`。

## 4. F4（低）`verify_plan_execution` 静默跳过缺 `path`/`sha256` 参考

### 实际改动

- `step02a_frame_gen/execution_plan.py:535-560`
  - 参考条目缺 `path`：报 `plan_reference_missing`（“参考条目缺少 path，无法核对参考身份”）。
  - 参考条目缺 `sha256`：同样报 `plan_reference_missing`（附路径）。
  - 不再 `continue` 静默跳过；`check_reference_files=False` 的既有语义不变。

### 测试

- 新增两条到 `tests/step02a_frame_gen/test_m05_plan_consumption.py`
  - 被篡改计划删掉参考 `path` → `plan_reference_missing`。
  - 参考文件存在但缺 `sha256` → `plan_reference_missing`。
- 定向：该文件 `9 passed`。

## 5. F5（低）Step02b `test_production_trace` 夹具未跟上 `start_end_state_mismatch` 门

### 实际改动

- `tests/step02b_video_gen/test_production_trace.py::test_step02b_entry_keeps_success_and_cache_semantics`
  - shot 夹具补 `ff_desc` / `lf_desc`，不再触发 `start_end_state_mismatch`。
- 该测试文件此前在 `.gitignore` 中被忽略（未跟踪）；本轮加入显式放行并随修复一起入库，保证干净检出可复现。

### 测试

- `tests/step02b_video_gen/test_production_trace.py`：`9 passed`（原 1 failed 转绿）。

## 6. F6（低）`issue_ledger` 身份不含“事实”维度

### 实际改动

- `step01_script_gen/issue_ledger.py`
  - `issue_identity` 由 `(shot_id, field_path)` 改为 `(shot_id, field_path, fact_fingerprint)`。
  - `fact_fingerprint` 优先取归一化 `evidence`；缺 evidence 时用 `code` 兜底区分。
  - `build_issue_ledger`：
    - 观察侧仍按 `(shot_id, field_path)` 建索引；
    - 同一字段只有**一个**事实时保留字段级匹配（避免 evidence 措辞增删细节把“仍被报出”误判成漏报）；
    - 同一字段有**多个**不同事实时才用事实指纹消歧：evidence 相等或互为子串算同一事实。
  - 行记录新增 `fact_fingerprint` 字段；默认 id 仍为 `shot@field`，保持稳定。
  - 模块 docstring 同步修正“镜头 + 字段 + 事实”的口径。
- `docs/step01-layered-execution/M03-issue-ledger.md`
  - 身份口径同步改为（镜头, 字段, 事实），并说明唯一事实保留字段级匹配的原因。

### 测试

- `tests/step01_script_gen/test_issue_ledger.py` 更新身份断言，并新增：
  - 同字段两条不同 evidence：身份不同；观察只命中第一条时，第二条为 `not_detected_this_round`，`observed_codes` 为空，`still_reported == 1`。
  - code 漂移不改变 evidence 身份；两侧都无 evidence 时 code 兜底区分。
- 定向：`11 passed`。
- U6 冻结回归仍为：确认 7、仍被报出 4、未检出 3、关闭 0。

## 7. F7（收尾）提交 `tools/build_m02_u6_candidate.py`

- 单独 commit `907fd06`，仅该文件 +26 行，不与其他源码修复混合。
- 内容：对 `_diff_rows` 的每个变化字段，在 `project_for_commit(revised, novel)` 后逐字段比对，记录 `proj_residual`；把“投影后残余”写入 `diff.md`。
- 仅离线工具输出，不触碰生产路径、无外部调用。
- `py_compile` 通过。

## 8. 回归测试结果（每目录单独 basetemp）

| 范围 | 结果 | 证据 |
|---|---|---|
| `tests/step01_script_gen` | **4268 passed / 278 skipped / 4 xfailed**（292.71s） | `.pytest_tmp/dsh_tasks/pytest_step01_final_20261113.log` |
| `tests/step02a_frame_gen` | **105 passed / 4 skipped**（53.29s） | `.pytest_tmp/dsh_tasks/pytest_step02a_final_20261113.log` |
| `tests/step02_video_gen` | **74 passed**（18.41s，日志隔离修复后重跑） | `.pytest_tmp/dsh_tasks/pytest_step02_final_afterlog_20261113.log` |
| `tests/step02b_video_gen` | **87 passed**（73.59s，单独重跑） | `.pytest_tmp/dsh_tasks/pytest_step02b_final_alone_20261113.log` |
| `tests/shared` | **1377 passed / 1 skipped**（39.66s，最终 HEAD 重跑） | `.pytest_tmp/dsh_tasks/pytest_shared_final_afterlog_20261113.log` |

### 关于一次并行运行中的 `test_candidate_cli` 失败

- 五个目录并行跑时，`tests/step02b_video_gen/test_candidate_cli.py::test_candidate_entry_leaves_production_snapshot_bytewise_unchanged` 曾报正式 `step02.log` 被修改。
- 该路径是多个测试目录共用的 `.pytest_tmp/锦衣夜行/.../step02.log`，并行运行存在跨目录日志竞争；同时暴露了 `_submit_video` 在候选模式下固定写章级日志的真实隔离问题。
- 已修复日志落点（commit `9c63583`），并单独重跑 `tests/step02b_video_gen`：**87 passed / 0 failed**。
- 结论：该失败不是业务断言回归；修复后单独运行与全目录运行均绿。

## 9. 已修复 / 已停下待裁决

### 已修复（含 commit）

- F1 Step02a 全量漏镜不报 — `704ab54`
- F2 Step02b r2v 拼表写生产 / plan-only 无守卫 / 哈希写副作用 — `54bc040`（日志隔离收口 `9c63583`）
- F3 Step02b 缓存绕过 `validate_requirements` — `13602af`（日志隔离收口 `9c63583`）
- F4 Step02a 参考缺字段静默跳过 — `4e5e1ef`
- F5 Step02b `test_production_trace` 夹具 — `f80f973`
- F6 `issue_ledger` 事实身份 — `cd4b332`
- F7 U6 候选工具投影后残余核对 — `907fd06`

### 已停下待裁决（G1–G7，均未改默认行为）

| 项 | 状态 | 说明 |
|---|---|---|
| G1 `issue_ledger.closed` 接提交门 | **未改** | 仍是离线台账；提交门仍用 `before_keys - after_keys`。接门会改变准入语义，需裁决。 |
| G2 `framing_jump` 核心景别匹配口径 | **未改** | 未改 replace / 匹配判定语义；本轮未顺手改 `近景` 档位或最长匹配锚点。 |
| G3 提示词“先定稿参考、再构建正文” | **未改** | 仍是现有请求级参考定稿 + provider prepare 顺序；结构性改造需裁决。 |
| G4 正式候选采纳动作 | **未改** | 未新增正式采纳/覆盖动作。 |
| G5 `ADDED_DIALOGUE` 生产接线 | **未改** | 两条 `scan_script` 生产调用点仍不传 `source_text`。 |
| G6 声音“准备态 / 视频后确认态”两态 | **未改** | 仍是单一 `build_voice_preparation`。 |
| G7 `postprocess_reverted` 升级为拒绝/门禁 | **未改** | 仍只记账/诊断，未进提交门。 |

### 是否出现“F 项实际是决策项”

- 未出现。F1–F7 均可通过确定性代码/测试修复完成，不需要改变产品语义、准入标准、正式采纳或费用授权。
- F2 中“r2v 缓存哈希改为源参考字节”是一次性缓存 key 变化，不改变准入语义；已在 §2 行为说明中标注。

## 10. 未覆盖 / 边界

- 未做真实 Step01/02a/02b/03 外部调用；真实模型遵从、真实视频质量、真实 TTS 未验证。
- 未运行全仓测试；只按用户指定目录回归（step01 / step02a / step02 / step02b / shared）。
- 未验证真实 r2v 配置下 `fit_refs` 的实际触发次数；F2 用桩场景覆盖写路径。
- 未改 `.workbuddy/`、`start_studio.bat` 或任何 GATE 默认行为。
