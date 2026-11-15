# Step01 修复后真实重跑验证记录（run i / run j，2026-11-15）

> 性质：两次真实模型运行的**如实记账**。真实模型调用已发生；正式产物写入 0；仓库未提交。
> 结论分层：run i 因审计超时**未进入修复轮**；经用户批准后重跑的 run j **进入了修复轮**，
> 在真实答卷上验证到帧映射与四节点账本接线，但**没有出现合法中间补帧**，终局仍 BLOCK。
> 不以离线测试或历史回放冒充真实模型收益。

## 1. 授权、通道与运行前核对

按 `AGENTS.md`「授权与费用」§Step 01，真实调用走免申请的订阅制授权通道；两次均由
WorkBuddy 会话直接发起，通道与 run h 一致。每次运行前用项目自身配置加载机制核对最终
生效值（进程环境 + `.env.content` + `.env.keys` 叠加，`load_dotenv` 不覆盖进程环境），
未发起网络调用，两次 `PREFLIGHT_OK=True`：

| 项 | 生效值（两次相同） |
|---|---|
| `TEXT_PROVIDER` / `AUDIT_TEXT_PROVIDER` / `REPAIR_TEXT_PROVIDER` | `agent_plan` / `agent_plan` / `agent_plan` |
| `AGENT_PLAN_TEXT_BASE_URL` | `https://opencode.ai/inference/go/openai/v1` |
| 最终请求 URL | `https://opencode.ai/inference/go/openai/v1/chat/completions` |
| `is_subscription_endpoint(...)` | **True**（HTTPS/443、无 userinfo、host+path 精确匹配） |
| `AGENT_PLAN_TEXT_MODEL` / `AUDIT_TEXT_MODEL` / `REPAIR_TEXT_MODEL` | `deepseek-v4.1-flash` |
| `AGENT_PLAN_API_KEY` | 已配置（值不记录） |
| `NOVEL_NAME` / `BOOK_INDEX` / `BOOK_NAME` / `NOVEL_CHAPTER` | 锦衣夜行 / 1 / 杀青州 / 1 |
| `OUTPUT_BASE` | run i / run j 各自隔离根（见第 2 节） |

未使用 `deepseek` provider、`DEEPSEEK_API_KEY` 或 `api.deepseek.com` 按次计费端点。
`main.py` 对 `TEXT_PROVIDER` / `AGENT_PLAN_TEXT_BASE_URL` / `AGENT_PLAN_TEXT_MODEL`
有 shell 覆盖硬阻断；两次命令行均未覆盖这三个键，仅设置 `OUTPUT_BASE` 与
`PYTHONIOENCODING`。两次实际调用台账的 `provider` / `model` 均为
`agent_plan` / `deepseek-v4.1-flash`。

## 2. 运行范围与边界

- 解释器：Python 3.14.2（`C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe`），
  与 run h 一致。
- 命令：`OUTPUT_BASE=<隔离根> PYTHONIOENCODING=utf-8 python step01_script_gen/main.py`，
  单章 `chapter_001`，单次运行，不重试、不扩量、不加 `--force`。
- 隔离根：`.dsh/step01_fix_verify_20261115i/output`（run i）、
  `.dsh/step01_fix_verify_20261115j/output`（run j）。三份正式产物与
  `step01_commit.json` 全部落在各自隔离根内，不触碰 `./output`，不改写
  `.dsh/step01_full_20261115h/` 现场。
- 与 run h 的**唯一自变量**是当前工作树代码（含帧身份对齐补正修复）；输入、配置、
  章节范围、模型通道一致。两次 run 之间也只有运行随机性不同，**没有改代码或配置**。
- run j 是用户在获知 run i 结果后明确选择「再跑一次真实运行」才启动的；约定若再次
  因审计超时提前终止即停止，不再自动加跑。run j 未发生超时。

## 3. 三次运行对照

| 项 | run h（修复前，历史终局） | run i（第一次重跑） | run j（第二次重跑） |
|---|---|---|---|
| run_id | `20261115T144433-11820` | `20261115T172026-25432` | `20261115T174547-19332` |
| 时长 / 进程退出 | ~12 min / `exit=0` | 19m0s / `exit=0` | **10m24s** / `exit=0` |
| 真实调用 | 16，**16 ok** | 20，**19 ok / 1 failed** | **14，14 ok** |
| `metrics.calls` | `script_generation 1, final_audit 1, repair_round 2` | `script_generation 1, split_shot 1, final_audit 1` | `script_generation 1, final_audit 1, repair_round 1` |
| `repair_shot_replacements` 调用 | 2 | **0** | **1** |
| `metrics.repair` | `decisions=2`，末轮 `accepted=false / resolved=2 / layout_structure_invalid` | `{}`（零修复轮） | `decisions=1`，`accepted=false / resolved=0 / no_target_resolved` |
| `layout_structural_violations` | 2 条「字段被改写」误报 | 无记录 | **`[]`（空）** |
| `frame_mappings` | 无此字段（旧实现） | 无记录 | **有（见第 4 节）** |
| `commit.status` | `BLOCK` | `BLOCK` | `BLOCK` |
| `commit.blocking_count` | 33（稿件缺陷清单） | 1 | 18（稿件缺陷清单） |
| 终局归因 | 布局守卫误拒 → 提交前修复轮零改善 | `audit_call_failed`（`transient=true`） | `no_target_resolved`（模型答卷零有效改善） |
| 生成稿 | 12 镜 / 70.6s | 17 镜 / 107.8s（定向拆 1 镜） | 10 镜 / 62.1s |

**run i 为什么没验证到修复**：最终语义审计第 2 次调用「耗时 126.0s，timeout=126s，
output=None，success=False」，`[COMMIT]` 按失败处置直接 BLOCK；`step01_commit.json`
记为 `audit_call_failed / transient=true`，日志明确写「调用失败 != 0 issue，直接 BLOCK」
「不是稿件缺陷清单」。事务修复链、守卫、效果账本零调用，章节目录下连
`step01_repair_rounds.json` 都未生成。日志里唯一的「修复轮」字样是 `[SAVE-WARN]`
固定提示文本，不是执行记录。该 BLOCK 是通道超时，失败点在审计阶段，位于本包所有
diff hunk 之前，与代码改动无因果关系。

## 4. run j 修复轮逐项证据（来自 `step01_repair_rounds.json`）

本轮 4 个目标：`shot_1`、`shot_4`、`shot_5`、`shot_6`；1 条 usage 记录 + 1 条裁决记录。

**（a）新契约的帧来源映射在真实答卷上生成并写入台账**

```json
{"shot_4": {
  "before_count": 3, "after_count": 3,
  "old_to_new": [0, 1, 2], "added_indices": [],
  "source": "keyframe_structure",
  "source_revision": "d8781a99ab9e9befa1663ffb5393488b0d3f88954c7ea603dac631e389d6809f"
}}
```

`source` 是代码生成的来源标记，`source_revision` 与本轮 `revision_before` 一致。
这是整表替换由「逐字保留关键帧完整结构」形成唯一顺序对应后派生的**恒等映射**，
没有任何新增帧。

**（b）四节点账本在零效果时仍覆盖全部目标**（S03 补正的直连证据）

`effects_count = 0`（本轮没有条目增删效果），但账本节点为
`candidate → merged → writer × 约 100 个 → reverify`，
**每个节点都对 4 个目标记录了 `frame_structure` 快照**（每节点 4 项）。
`writer_structure_changes = []`、`frame_locator_integrity = []`、
`writer_observation_errors = []`：没有 writer 擅自改帧结构，观测无异常。
这正是补正包修掉的旧缺口（旧实现只按 `effects` 取观测目标，纯补帧时快照全空）。

**（c）越权整表补丁在进入 staged 前被结构化拒收**

`rejected` 共 4 条，其中 3 条是模型提交整表补丁但本轮只授权叶子路径：

```
shot 1: 越权字段补丁：'frame_layouts' 不在本轮授权集合 [...] 内
shot 5: 越权字段补丁：'keyframes' 不在本轮授权集合 [...] 内
shot 6: 越权字段补丁：'keyframes' 不在本轮授权集合 [...] 内
目标 ['1', '5', '6'] 本轮未返回替换（其余 1 个目标已提交）
```

这些目标没有污染 staged 稿；`revision_before == revision_after`，基线逐字保留。
注意：拒因是**授权合并阶段**的越权检查，不是「帧来源无法证明」，也不是旧守卫的
「字段被改写」。因此本轮没有复现 run h 那种按同下标比较的误报场景。

**（d）终局为何仍 BLOCK（M01 已撤回「writer 回退」归因）**

初版把 `postprocess_reverted` 的 36 个字段读成「既有后处理 writer 全数回退」。
**该归因已撤回**：该字段在事务裁决之后才计算，拒收分支已执行
`script = _round_before_script`，比较的是「合并后 A vs 已整轮回退的最终稿 D」。
M01 离线回放（`run_j_attribution_replay.json`）证实 36 个字段**全部**属事务回滚：
34 个模型改动在后处理阶段逐字保留，另 2 个虽被 `identity_unification` /
`v6_43_prompt_enforcement` 改动，也随整轮回退一并消失；纯 writer 回退为 0。

真实终局仍是 `resolved=0`、`accepted=false`、`reason=no_target_resolved`。原因是
**补丁没有解决原问题**：shot_4 原问题 `model_audit_STATE_CHAIN_CONFLICT` 要求
「首帧保持发髻 + `motion_desc` 补抓发碰落步摇事件」，答卷却把 `ff_desc` 写成
「秀发披散」、`motion_desc` 只改服装与饮酒措辞，未补所要求事件；确定性 checker
90 → 87 仅少 3 条。不是布局守卫误判，也不是后处理抹除；历史日志保持原样。

## 5. 结论分层：哪些被真实运行证明，哪些没有

**已验证（真实模型运行，run j）**

1. 补正后的代码确实接在真实修复入口上：修复轮执行、映射写入诊断台账、
   四节点账本与后处理保护字段全部进入裁决记录。
2. 帧来源映射由代码生成并绑定本轮 revision，`source=keyframe_structure`；
   没有模型答卷自带/覆盖映射的迹象。
3. 零效果时账本仍对全部点名目标做帧结构快照，writer 结构变化与观测异常均有
   独立诊断位，后处理未改帧结构。
4. 越权整表补丁在授权合并阶段被拒收、基线保留；旧「保留条目被改写」误报
   在本轮未出现（`layout_structural_violations=[]`）。

**未验证（不能写成收益）**

1. **合法中间补帧贯通**：本轮模型没有提交 `keyframe_additions`，
   `added_indices=[]`、映射为恒等；守卫「按映射把候选 `kfs` 投影回原坐标」的
   分支没有被真实答卷触发。该分支目前只有离线事务入口用例与 run h 派生样本的证据。
2. **整轮通过 / 质量收益**：三次真实运行终局都是 BLOCK。run j 的归因从
   `layout_structure_invalid` 变为 `no_target_resolved`，但两轮输入、答卷、
   轮次与后处理条件均不同，**不构成可比对照，不推算通过率，也不宣称修复让 run 通过**。
3. **「误拒已被真实运行证明修好」**：只能说本轮没有复现该误报，不能说误拒机制
   在真实补帧答卷上被验证为已消除。

**离线证据仍然成立（独立于真实运行）**：单元 44 + 真实 `_repair_shot_warnings`
事务入口 8 = 52 passed；聚焦回归 18 个实存文件 447 tests / 0 failures / 0 errors /
15 skipped；`run_h_frame_identity_replay.json` 把历史终局与修复后离线裁决分列，
原始整表答卷因「帧来源无法证明」拒收、操作来源的派生样本合法贯通；
`tests/step01_script_gen/` 全目录 4701 tests 中 14 个既有 plan 阶段失败已单列，
与本包改动无依赖关系。

## 6. 边界复核（两次运行后）

- `./output/锦衣夜行/part_01_杀青州/chapter_001/script_result.json` mtime 仍为
  `2026-11-14 20:12`，`step01_commit.json` 仍为 `2026-11-15 14:57`：**正式产物未被替换**。
- `.dsh/step01_full_20261115h/` 未写入；`git rev-parse HEAD` 仍为
  `88404b7cb49729dc082d8cc43941464a3c63c9f9`，未提交。
- `git status --porcelain` 仅含补正包的既有改动，加本文件与
  `step01_fix_verify_20261115i_evidence.json`；两次运行本身均未改写源码。

## 7. 后续边界

按与用户的约定，run j 未超时即收口，**不再自动加跑**。若还要在真实模型上直接观测
合法中间补帧，需要另一次明确的运行或专门的答卷诱导设计；本节不把它写成已完成的验证。
`retry_state` 事务拒收反馈、正式重签与 `git commit` 仍按原计划留在后续独立决策。

## 8. 结构化证据

- `docs/step01-layered-execution/step01_fix_verify_20261115i_evidence.json`
  —— 文件名沿用工作包起始 run i，内容含 **run h / run i / run j** 三次对照的
  calls、metrics、commit、逐轮裁决与信号位。
- `.dsh/step01_fix_verify_20261115i/`：`console.log`、`exit_code.txt`；
  章节目录下 `step01.log`、`step01_calls.jsonl`、`step01_metrics.json`、
  `step01_commit.json`、`step01_audit_rounds.json`、`step01_timing.json`；
  **没有** `step01_repair_rounds.json`（零修复轮）。
- `.dsh/step01_fix_verify_20261115j/`：`console.log`、`exit_code.txt`；
  章节目录下 `step01.log`、`step01_calls.jsonl`、`step01_metrics.json`、
  `step01_commit.json`、`step01_repair_rounds.json`（含第 4 节全部证据）。
