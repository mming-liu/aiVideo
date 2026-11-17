# 零调用收尾四项（2026-11-17）：X5-03 面板 / V01 视频预检 / M-A2 端到端 / T01 声音准备

## 工作背景与硬约束

- 工作目录：`C:/myFloders/git/aiVideo`（aiVideo 工作区）。当前 HEAD `cf45af2`，工作树基本干净。
- **本任务严格零真实模型调用**：用户 2026-11-17 明确「不需要真实调用的修复」。不得发起任何
  Step 01 文本生成/审计/修复、Step 02a 生图、Step 02b 真实视频生成、TTS 合成 POST。所有工作
  只走离线桩、`--plan-only`、`--dry-run`、单测与本地文件读写。
- **不改正式门禁语义、不采纳正式产物**：`step01_commit.json`、accepted 成片、正式三产物一律不动；
  不改任何门禁 blocking 判据或默认行为。若发现必须改门禁语义或必须真实调用才能继续，**停下报告**，
  不要自行放宽或改判据。
- **保护工作树现有未跟踪文件**：`docs/dsh-handoff/session-reuse-lock-screen-decision-20261116.md`
  是 WorkBuddy 主会话留档，**不要编辑、不要 add、不要提交**。提交时只 `git add` 本任务明确相关文件，
  绝不 `git add -A`。
- **内容无关性**：新增/修改的检查、夹具与示例不得绑定具体作品/章节/人物；用匿名角色与通用道具，
  优先复用现有 `narrative_family_builder` 机制或内联匿名样本。
- Python：`C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe`（全路径）。
  遇环境干扰加 `--basetemp=<临时目录> -p no:cacheprovider`，`PYTHONUTF8=1` 防乱码。
- 修复与编辑必须可回退。

## 任务清单

### T1（X5-03）：预检面板逐镜 ready/blocked 可视化

- 现状：`tools/studio_ui/index.html` 的 `runPlan()`（约 727–740 行）调用 `GET /api/plan` 后，
  只把 `res.report` 用 `<pre>` 原样 dump，没有逐镜 `ready/blocked` 的结构化展示。
- 后端 `tools/studio_server.py` 的 `/api/plan`（约 931 行）已能返回逐镜 `ready/blocked`，
  **本任务不改后端判据**，只做前端展示。
- 目标：在面板里以列表/表格呈现每个镜头的 `ready`/`blocked` 状态与 blocked 原因，零调用特性不变
  （仍只调 `/api/plan`，不新增任何会触发生成的按钮或请求）。
- 验收：`/api/plan` 返回结构变化能正确渲染；无 JS 报错；`grep -n "runPlan" tools/studio_ui/index.html`
  可读；不新增真实调用路径。可用 `tools/studio_server.py` 起本地服务人工核对（仅 GET，不触发生成）。

### T2（V01）：全章零调用视频预检

- 入口：`python -m step02b_video_gen.main --plan-only --candidate-root <DIR> --all-shots`（参数定义见
  `step02b_video_gen/cli.py:42–50`）。`--plan-only` 语义为「零网络、零生成 POST」。
- 先自行定位冻结候选包根（形如 `<chapter>/_enhancement/<experiment_id>/video`；冻结身份与候选 SHA
  见 `docs/step01-layered-execution/step01-frozen-continuation-preflight-20261116.md` 与
  `step01-frozen-resume-verification-20261116.md`）。找不到或单源校验不过 → 停下报告，不要编造路径。
- 目标：对候选全量镜头跑通 plan-only预检，产出/核对计划文件，逐镜列出 `ready/blocked` 与 blocked 原因；
  确认对候选目录外无写入、无网络、无生成 POST。
- 验收：命令退出码与预告一致（0 成功≠内容通过 / 2 输入身份非法 / 3 IO / 4 拒收）；
  报告里逐镜 ready/blocked 可读；明确记录「零 POST」证据（日志/代码路径），不把桩结果冒充真实生成结论。

### T3（M-A2 端到端）：`resume --dry-run` 走 pending_candidate 真机验收

- 入口：`python -m step01_script_gen.resume --manifest <chapter>/step01_resume.json --dry-run`
  （零调用、不写正式产物，见 `step01_script_gen/resume.py:15–18`）。
- 单测已覆盖清单构建与读取（`tests/step01_script_gen/test_resume_pending.py`，8 条），
  **未覆盖 `run()` 完整分支**：清单含 `pending_candidate` 时，应打印 `PENDING_CANDIDATE_REAUDIT`、
  跳过「审计可复用」闸门、以修后稿为本次提交对象。
- 目标：用现有冻结/桩产物构造一个带 `pending_candidate` 的清单（落在临时目录，不碰正式章节），
  跑 `--dry-run` 走通该分支，核对输出的 `next_action` 与跳闸门行为；再补一条「同 revision 待复审稿被拒」
  的端到端断言（若单测已覆盖则复用，不重复造）。
- 验收：命令零调用、零正式产物写入；逐项打印身份核验与将要消费的阶段；结果与 `MA-closeout-20261117.md §2`
  一致；不修改冻结稿/审计产物。

### T4（T01）：声音准备与核对（零调用 prepared）

- 目标：基于 `step03_tts_audio` 的 plan-only `prepared` 入口（`step03_tts_audio/main.py:481–496`，明确
  `prepared_reason="plan_only_no_synthesis"`），对候选镜头产出/核对声音准备报告：逐句核对
  `declared_sec` / `required_playback_sec` / `available_window_sec`，标出 `prepared`（未实测）与
  `measured`，不触发任何 TTS 合成。
- 参考 `docs/step01-layered-execution/artifacts/s05/voice_preparation.json` 的结构，但**不要改写既有
  产物**；如需新报告落到临时目录。
- 验收：报告中每句状态为 `prepared` 且 `measurement="not_measured"`；零 TTS POST；跨镜时长窗口矛盾能
  被列出；参考 `artifacts/s08/not_run.md` 的口径，不把 prepared 当作 confirmed。

## 执行顺序与停止条件

1. 按 T1 → T2 → T3 → T4 顺序推进；每项先确认入口/路径真实存在再动手。
2. **任何时刻**若发现需要真实模型调用、需要改正式门禁语义、需要采纳正式产物、或候选路径无法定位，
  立即停下并在汇报里说明，不要自行放宽或编造。
3. T1/T4 若涉及 UI/人工目视核对，以「不新增真实调用路径」为硬边界；无法自动核对的部分明确记为
  「需人工确认」，不要假装已验证。
4. 四项完成后，把本任务相关文件**单独 commit**（只 add 明确文件，不 `-A`，不碰那份未跟踪留档），
   提交信息标注四项完成范围；push 到 Gitee/GitHub 双远端（按项目惯例）。

## 收尾汇报（中文，简短）

- 四项各自：完成 / 未完成 / 停下原因；关键文件与行号。
- 每项验收命令 + 通过数/失败数 + 报告路径；零调用证据。
- 遗留风险与建议（尤其 T2 候选路径、T3 端到端分支、T4 prepared vs confirmed 边界）。
- 明确说明：未发起任何真实调用、未改门禁语义、未采纳正式产物、未触碰那份未跟踪留档。
