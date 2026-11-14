# Step01 确定性检查器已知缺陷修复（step01_script_gen/script_findings.py）

## 工作背景与硬约束

- 你在本项目 `aiVideo` 的 `step01_script_gen` 模块工作。当前授权：Step01 真实调用免申请；**但本任务主要是「代码修复 + 测试」**，不发起模型调用（除非你需要用现有冻结/桩产物做验证）。
- 通道绑定（仅作风险提示，**本任务不改通道**）：`agent_plan` / `deepseek-v4.1-flash` / `https://opencode.ai/zen/go/v1/chat/completions`。
- **内容无关性硬约束（AGENTS.md）**：新增/修改的检查与夹具**不得**绑定任何具体作品、章节、人物。使用匿名角色（甲/乙/丙）与通用道具（茶盏/酒壶/剑/灯盏等）。夹具优先走 `tests/step01_script_gen/fixtures/audit_narrative_families` 的 `narrative_family_builder` 机制；单元测试可用内联匿名样本（参考现有 `tests/step01_script_gen/test_script_findings.py` 的写法）。
- **回退性**：所有编辑必须可回退。需要 git commit 时，**只提交本任务相关文件**（`script_findings.py`、相关测试与夹具），不要碰 `.workbuddy/`、`start_studio.bat` 等无关项，也不要 `git add -A`。
- Python：运行 pytest 用 `C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe`（全路径）。如遇环境干扰，加 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR=<临时目录>` 与 `--basetemp=<临时目录>` 隔离，`PYTHONUTF8=1` 防中文乱码。

## 已知缺陷清单（按优先级）

### T1（必做，P0）：`_acquire_in` 否定式误判

- 文件：`step01_script_gen/script_findings.py`，函数 `_acquire_in`（约 516–522 行）。
- **缺陷**：当前只要某分句同时含 `prop` 与取回动词（拿起/捡起/拾起/…见 `_ACQUIRE_MARKERS`），就返回 `True` 视为「已取回」。但否定式「茶盏未再被拿起」里的「拿起」也会被命中，使本不应成立的取回反证生效，从而**压制 `PROP_STATE_CHAIN`**（道具脱手后又回到手中、中间无取回动作 → 本应报缺陷，却被漏报）。
- **修复**：
  1. 新增否定词表 `_NEGATION_MARKERS`（至少含：未 / 没 / 没有 / 无 / 不 / 不再 / 不曾 / 未曾 / 并未 / 仍未 / 莫 / 勿 / 不可 / 未曾 / 不可），置于文件顶部通用词表区。
  2. 在 `_acquire_in` 内，当某分句含取回动词时，若**同一分句内**该取回动词之前出现任一否定词，则该分句不算取回、`continue` 继续检查其他分句；只有确实未被否定的取回动作才返回 `True`。
  3. 实现要点：取该分句内**最早出现**的取回动词下标，检查其前缀（该下标之前的部分）是否含否定词。
- **回归测试**（在 `tests/step01_script_gen/test_script_findings.py` 新增内联测试，沿用现有匿名样本风格）：
  - 构造脚本：
    - shot1：`ff_desc="甲将茶盏置于案上"`，`lf_desc="茶盏静置案上"`，`frame_layouts={"ff":[{"name":"甲","props":["茶盏"]}],"lf":[],"kfs":[]}`
    - shot2：`ff_desc="甲右手端着茶盏，茶盏未再被拿起"`，`lf_desc="甲饮茶"`，`frame_layouts={"ff":[{"name":"甲","props":["茶盏"]}],"lf":[],"kfs":[]}`
    - scene/location/duration 用 `_shot` 默认值（参考现有测试）。
  - 断言 `scan_script` 对该脚本产出**恰好 1 条** `PROP_STATE_CHAIN`，且 `shot_id=="2"`、`field_path=="ff_desc"`、`prop=="茶盏"`。
  - **阴性对照**：把 shot2 的「茶盏未再被拿起」改成「甲重新拿起茶盏」后，`PROP_STATE_CHAIN` **不应**出现（确认修复不误杀真实取回）。
- **验收**：运行下面命令全绿：
  `C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest tests/step01_script_gen/test_script_findings.py -q`
  并先用临时注释/`git stash` 验证该测试在**修复前失败、修复后通过**（验证完把改动还原）。

> 备注：`_prop_state`（约 486 行）同样存在否定盲区（如「甲未放下酒壶」会被误判为 release），但**不在本次必做范围**；若你要顺手修，必须新增测试且不引入回归，否则保持原样并在汇报里记下该局限。

### T2（P1，规则清晰则做，否则停下报告）：补两类确定性 finding 码

- 现有 6 码：`DUPLICATE_TEXT` / `FRAMING_CONFLICT` / `PROP_STATE_CHAIN` / `STATE_CHAIN_CONFLICT` / `STATE_CONFLICT` / `STATE_RESULT_LEAK`。
- **缺口 A「尾帧过程态」(`LF_PROCESS_STATE`)**：尾帧(`lf_desc`)仍停留在动作进行中/未收束的过程态，而本镜动作应在尾帧呈现已完成终态（与 `STATE_RESULT_LEAK` 互补：`STATE_RESULT_LEAK` 是首帧提前呈现结果态，本码是尾帧未落到结果态）。**必须先给出内容无关、可判定、低误报的规则**（例如：lf 含进行中标记「正在/仍在/着」且同 shot 的 `motion_desc` 未声明该动作收束，且无显式时间跳跃），再实现 + 测试。规则若不清晰、易误报，**停下报告，不要硬上**。
- **缺口 B「新增台词」(`ADDED_DIALOGUE`)**：脚本出现了原文/规划中不存在的台词或画外音。该判定**必须有对照源**（novel/plan 文本）才能确定「新增」，纯脚本内无法判定。因此：实现为**带对照源入参**的确定性检查——若调用方传入了 source 文本则对比台词集合；若未传入源则该码不产出（宁缺勿滥）。实现带源/无源两种测试；若无法取得对照源样本，停下报告设计缺口。
- 两类都要：在 `scan_script` 的汇总/去重/排序里登记新码；更新模块 docstring 的 finding code 列表；配套内容无关夹具或内联匿名测试。新码仍保持「**只读、不改脚本、不改变 issue severity/blocking、只落 shadow 报告**」的边界（与现有 6 码一致）。

### T3（P2，调查 + 谨慎实现）：门禁 severity 随契约/提示漂移

- 已知：同一份合法稿 `voice_legal/new` 在两轮里 blocking 由 1→0，说明 blocking 决策随契约/提示而非内容漂移。
- 先调查 `audit_adjudication.py` / `audit.py` 里 severity 与 blocking 的判定入口，定位哪些档位来自模型、哪些可改为内容决定。
- 仅对 **T1/T2 已确定的确定性 finding** 设计「内容决定 severity 下限」方案（例如 `PROP_STATE_CHAIN` / `STATE_CHAIN_CONFLICT` 始终不低于某档），使同内容同档位。
- **若方案会改变正式门禁的 blocking 语义，标注为「准入语义变更」，实现在可配置/可回退路径上，并在最终报告明确说明，不要静默改默认行为。**
- 若调查发现改动风险高或需用户裁决产品语义，**停下报告，不要擅自放宽/收紧门禁**。

## 执行顺序与停止条件

1. **先做 T1 并全绿**，再做 T2、T3。
2. 遇规则不清晰 / 需语义裁决 / 易误报 / 无法取得对照源 → **停下，在汇报里说明设计与缺口**，不要硬做、不要做未验证的语义变更。
3. T1 完成且相关测试绿后，把 `script_findings.py` + 本任务新增/修改的测试与夹具**单独 commit**（不要 `-A`，不要碰无关未跟踪项）。提交信息清晰标注 T1/T2/T3 完成范围。

## 收尾汇报（中文，简短）

- T1/T2/T3 各自：完成 / 未完成 / 停下原因。
- 关键改动文件与行号、测试结果（pytest 命令 + 通过数/失败数 + 报告路径）。
- 遗留风险与建议（尤其 T2 缺口 B 的对照源、T3 是否涉及准入语义变更）。
