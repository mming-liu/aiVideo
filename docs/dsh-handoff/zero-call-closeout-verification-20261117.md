# 零调用收尾四项验收记录（2026-11-17）

对应任务单 [zero_call_closeout_20261117.md](zero_call_closeout_20261117.md)。
基线 `cf45af2`；本轮**零真实模型调用**、未改门禁判据/默认行为、未采纳正式产物、
未触碰未跟踪留档 `session-reuse-lock-screen-decision-20261116.md`。

| 项 | 结论 | 主要证据 |
|---|---|---|
| T1 X5-03 预检面板 | 完成 | `tools/studio_ui/index.html`；`tests/tools/test_studio_server.py` 20 passed；真实 `/api/plan` 渲染 13 镜 |
| T2 V01 全章视频预检 | 完成（隔离重建口径，原冻结 video 包不存在） | `plan_0001.json`：ready=0 / blocked=14；exit 0；`network_calls=0` |
| T3 M-A2 resume 端到端 | 完成 | `PENDING_CANDIDATE_REAUDIT`；`audit_reusable=false`；9 passed |
| T4 T01 声音准备 | 完成 | 8/8 句 `prepared` + `not_measured`；溢出变异 4 条 planning issue；6 passed |

## 1. T1：X5-03 逐镜 ready/blocked 面板

### 改动

- `tools/studio_ui/index.html:732` 新增 `planRows(rep)`：优先读后端显式
  `verdict/status/ready`，缺失时按 `shot_frames` 的 `generate/cached/locked`、
  `prompt_preflight.problems`、`visual_plan.shots[].issues` 派生逐镜状态与原因。
- `tools/studio_ui/index.html:835` 新增 `renderPlanReport(res)`：表格列
  「镜号 / 状态 / blocked 原因 / 02a 帧」，顶部汇总 ready/blocked 与预计图片数；
  重复点击替换旧卡片，不堆叠。
- `tools/studio_ui/index.html:903` `runPlan()` 仍只 `POST /api/plan`（原入口，
  零模型调用），不新增按钮或请求。
- 结构演进兜底：`planHasShotDetail()`（:825）判断无逐镜字段时回退原始 JSON，
  不臆造 ready/blocked、不抛 JS 异常。
- 新增回归 `tests/tools/test_studio_server.py:298`：`/api/plan` 返回逐镜
  `shot_frames` 且 `model_sends=0`。

### 验收

| 命令/动作 | 结果 |
|---|---|
| `python -m pytest tests/tools/test_studio_server.py -q` | **20 passed** |
| 内联脚本 `node --check`（UTF-8 提取） | exit 0 |
| 本地实例 `GET /` | 200，43,964 B，含 `planRows`/`renderPlanReport` |
| 本地实例 `POST /api/plan`（只读预检，不生成） | 200，`model_sends=0`，13 镜，`images_total=74` |
| 用真实响应跑 DOM 桩渲染 | 13 行；ready=1 / blocked=12；blocked 原因可读 |

**口径更正**：当前 `/api/plan` 实为 Step02a 预检，返回的是逐镜
`shot_frames` 帧状态（`generate/cached/locked`）与 `prompt_preflight`，并**没有**
显式的 `ready/blocked` 字段。前端把「无需 02a 生成」映射为 ready，并在卡片上明确
「ready=无需 02a 生成，不代表内容通过」。本任务未改后端判据。

## 2. T2：V01 全章零调用视频预检

### 候选包定位结果（必须先说明）

- 冻结身份已核实：`.dsh/step01_budget_frozen/20261116T003059-8920/frozen_script.json`，
  SHA-256 `932af1afd8652213e208c1c02d9f6a8f561c3c73b13c54d53efb2c5110a00767`，
  revision `e8f637620605c0d0…`，13 镜。
- **未找到**该冻结稿对应的既有 `<chapter>/_enhancement/<experiment_id>/video` 包。
  现有 `stepF_precheck/video` 是 2026-11-15 的另一稿（镜 1 内容不同），不能冒充。
- 为避免编造“原包路径”，本轮在 `.dsh` 隔离根**重建**候选包，并保持
  `script_result.json` 与冻结稿**逐字节相同**（SHA 校验通过）。该结果只证明
  plan-only 链路，不冒充原包现场。

### 命令与结果

```powershell
$env:OUTPUT_BASE=".dsh/zero_call_closeout_20261117/t2/output"
python -m step02b_video_gen.main --chapter 1 --book 1 --book-name 杀青州 `
  --plan-only --all-shots `
  --candidate-root ".dsh/.../chapter_001/_enhancement/zero_call_frozen_20261116/video"
```

- 退出码 **0**（成功≠内容通过）。
- 计划：`…/video/plans/plan_0001.json`，schema `video_reroll_plan.v1`。
- 逐镜：`ready=0`、`blocked=14`（hook 0 + 镜 1–13），全部
  `code=frames_missing`（`image` 模式；隔离稿没有首/尾帧，属预期缺口）。
- `estimated_generations=0`；每条 `generations=0`；计划字段 `network_calls=0`。
- 命令输出 `production_unchanged=true`；候选根内只有 `plans/` 与 `logs/`，
  章根只有 `script_result.json` / 三份 step01 门禁文件，无正式产物写入。
- 零 POST 证据：`step02b_video_gen/main.py:550` 的 `_plan_only()` 只调用
  `build_plan()`，从不调用 provider 生成；`main.py:598` 显式写
  `network_calls: 0`。日志无 provider 请求记录。

**遗留**：由于隔离稿没有 Step02a 帧，14 镜全 blocked；下一步必须先对同一冻结稿
补 Step02a 帧，再重跑本预检。若严格要求“既有冻结 video 包”，T2 应记为
**未完成（原包缺失）**；本记录按“隔离重建候选包”口径交付。

## 3. T3：M-A2 `resume --dry-run` pending_candidate 端到端

### 构造

在 `.dsh/zero_call_closeout_20261117/t3/.../chapter_001` 复制冻结快照/审计/计划/原文：

- 已审稿 `script_result_20261116_003848.json`，rev `e8f63762…`；
- 待复审稿 `script_result_20261116_185650.json`，rev `64a347a4…`；
- 审计 `step01_audit_20261116_003848.json`，status `ok`，input_revision 与已审稿一致。

用 `resume.build_pending_manifest()` 生成双块清单，写入临时章节目录，未碰正式章节。

### 命令与输出

```powershell
python -m step01_script_gen.resume --manifest <temp>/step01_resume.json --dry-run
```

- 退出码 **0**。
- 打印 `status=PENDING_CANDIDATE_REAUDIT`，`audited_revision=e8f63762…`，
  `pending_revision=64a347a4…`，`next_action=reaudit_pending_candidate`。
- 最终 JSON：`ok=true`、`audit_reusable=false`、`reuse_miss=""`、
  `script_revision=64a347a4…`（以修后稿为本次提交对象）、
  `pending_candidate=<待复审稿路径>`、`next=从待复审稿继续…`。
- 未打印 `AUDIT_WILL_BE_REFRESHED`，证明跳过「审计可复用」闸门。
- 仅在临时章节目录写 `snapshots/cp_CP1_PLAN_VERIFIED.json`；正式产物未动。
- 零调用：`resume.py:452` 的 `if dry_run` 在 `_acquire_step01_lock()` 与
  `_commit_step01_outputs()` 之前返回，未进入任何发送路径。

### 回归

- 新增 `tests/step01_script_gen/test_resume_run_pending.py`，走 `run(..., dry_run=True)`
  完整分支。
- 同 revision 待复审稿被拒沿用既有
  `test_same_revision_pending_is_rejected` / `test_build_rejects_same_revision_pending`，
  未重复造。
- `python -m pytest tests/step01_script_gen/test_resume_run_pending.py tests/step01_script_gen/test_resume_pending.py -q`
  → **9 passed**。

## 4. T4：T01 声音准备（零调用 prepared）

### 基线报告

用冻结稿（逐字节 SHA `932af1af…`）在 `.dsh` 隔离章重建 step01 门禁，并按
`plan_feasibility.voice_units()` 为临时副本补 `chapter_outline.voice_units`
（仅测试夹具，不是正式产物），然后走实际 plan-only 入口：

```powershell
python -m step03_tts_audio.main --chapter 1 --book 1 --book-name 杀青州 `
  --plan-only --candidate-root <temp>/_enhancement/t01_prepared_20261117/audio
```

- 退出码 0；日志 `已写出 8 个声音单元（未调用任何 provider）`。
- 报告：`…/audio/plan/voice_preparation.json`，schema `voice_preparation.v1`。
- **8/8** 单元：`state=prepared`、`measurement=not_measured`、`actual_sec=null`、
  `verified=false`；逐句含 `declared_sec` / `required_playback_sec` /
  `available_window_sec`。
- `status=ready`、`conflicts=[]`；`unverified` 列出全部 8 个 unit_id。
- 声音状态：`…/audio/plan/voice_state.json`，`state=prepared`，
  `source=plan_only`，`blocking` 含 `plan_only_no_synthesis`，`counts.confirmed=0`。
- 零 TTS：`step03_tts_audio/main.py:448` 的 plan-only 分支在
  `tts_config.validate()` / `gen_all_shot_tts()`（:516 起）之前 `return`；日志与
  产物均无 provider 请求。

### 跨镜窗口矛盾

在同一隔离夹具上做受控变异（D2/N2 声明 9.0s），重跑 plan-only：

- `…/t01_prepared_overflow_20261117/audio/plan/voice_preparation.json`
- `planning_codes=["audio_window_overflow_planned"]`，4 条 `planning_issues`
  （D2 超 0.5s、N2 超 0.38s，以及两镜合计超窗），单元仍为
  `prepared/not_measured`；`status` 仍为 ready（规划问题不冒充实测拒收）。

### 回归

- `python -m pytest tests/step03_tts_audio/test_voice_preparation.py -q`
  → **6 passed**。
- **口径**：prepared ≠ confirmed。按 `artifacts/s08/not_run.md`，本轮没有真实 TTS
  合成，不能把 prepared 当作实测通过；`confirmed` 需本地 ffprobe 实测或源侧
  `verified=true`。

## 5. 零调用与边界核对

| 项 | 核对 |
|---|---|
| 真实模型调用 | 0（Step01/02a/02b/TTS 均未发起） |
| 正式门禁语义 | 未改；未改任何 blocking 判据或默认行为 |
| 正式产物 | 未采纳、未替换；`step01_commit.json` 未动 |
| 未跟踪留档 | `session-reuse-lock-screen-decision-20261116.md` 未编辑/未 add |
| 工作树 | 仅 T1/T3/T4 相关代码、测试与本文档 |

## 6. 遗留风险与建议

1. **T2 原冻结 video 包缺失**：本轮是隔离重建候选包；若用户要求“原包现场”，
   需先找到/恢复该包，或明确接受隔离重建口径。14 镜全 blocked 的根因是缺
   Step02a 帧，不是视频请求本身失败。
2. **T1 数据源口径**：`/api/plan` 当前是 Step02a 报告，没有显式 `ready/blocked`；
   前端做了语义映射。若后续后端改为显式 verdict，`planRows()` 已优先读
   `verdict/status/ready`，但仍建议同步更新卡片文案与测试。
3. **T3 待复审稿来源**：本轮用 2026-11-16 真实运行的修后快照作为 pending；
   它是真实产物，但仍是离线 dry-run，未证明重审后的内容质量。
4. **T4 prepared vs confirmed**：8 句全部未实测；窗口矛盾只在声明时长上成立。
   真实 TTS 后必须用 `measure_units()`/ffprobe 回填，才能升级为 confirmed。
