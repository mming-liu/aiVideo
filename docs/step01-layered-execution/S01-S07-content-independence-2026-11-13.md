# S01–S07 阶段交付：内容无关性修复与决策对齐（2026-11-13）

基线 `9c63583`；本包提交见文末。范围按用户计划 S01–S07，视频模型选择后置。
2026-11-13 复核后，S02/S03/S04/S05 的确认缺口已按复核意见补修；S06 已补
G1/G2/G6/G7 的具体样本（G3 仍缺差异样本）；S07 仍为阶段报告。

## S01 基线与决策对齐

### 冻结身份

| 项目 | 值 |
|---|---|
| 起始提交 | `9c63583` |
| 上一轮提交 | `2a91d7e`（复核第四轮：显式 `plan_refs` 承载绑定 + 逐问题残余）、`96f3cc3`（S02 内容无关性首版） |
| 本轮提交 | 见文末（S02–S05 补修 + S06 样本） |
| 未入库工作树 | `.gitignore`、`docs/dsh-handoff/`、`start_studio.bat`、`.workbuddy/`、`baselines/**/__pycache__/` 属**他方**改动，本包未触碰 |

### 脏工作树归属

计划 §2 提到的 `execution_semantics.py` / `repair_fields.py` / 夹具 / 候选工具 / 第四轮报告，
是本会话上一轮的产物，已在 `2a91d7e` 收口并纳入基线；其余未提交项属并行接入方，保持原样。

### G1–G7 状态（逐项核对）

| 项 | 已核实状态 | 依据 | 待批准动作 |
|---|---|---|---|
| G1 问题关闭接提交门 | **未接线**：`issue_ledger.closed` 只产出影子台账，`quality_gate`/提交门未消费 | `step01_script_gen/issue_ledger.py`、`shared/quality_gate.py` | 是否对阻塞问题要求关闭证据 |
| G2 景别规则 | **已改三项**（不是「全部未改」）：`530fdbb` 起 `_shot_size_level` 改最长匹配、`framing_jump` 在修复轮跳过被授权镜头、final pass 只报不改；**跨度规则本身仍在** | `step01_script_gen/validation.py`、`constraints/framing_jump.py` | 跨度自动改写是否整体取消 |
| G3 参考定稿顺序 | 未做：最终参考清单未成为 `prepare_request` 正文的唯一来源 | `shared/vimax_bridge.py`、`_video_gen/volcengine_provider.py` | 是否改正文构建 |
| G4 正式采纳 | 未做（离线候选） | `artifacts/m02/*` | 对具体候选审批 |
| G5 `ADDED_DIALOGUE` | 沿用已冻结决定，未改 | — | 无 |
| G6 声音两态 | 未拆：`build_voice_preparation` 仍是单态 | `shared/voice_preparation.py` | 是否拆准备态/确认态 |
| G7 后处理撤销接门 | 只记账（`postprocess_reverted`），未接拒绝判据 | `step01_script_gen/main.py` | **按目标丢失接门 + 保留既有保护门**（不是按任何残余；见 S06） |

> 更正：此前汇总写「G2 全部未改」不成立，`530fdbb` 已改变三项行为。
> G7 的旧表述「按残余接门会拒收等价派生」过宽；三个样本见
> `S06-decision-package-2026-11-13.md` §5。

## S02 内容修复（复核缺口已补）

| 行为 | 修前 | 修后 | 位置 |
|---|---|---|---|
| 缺规格补宽度 | 回退 `宽1.4米` | 返回空串，**不改写** | `shared/scene_objects_bank.boat_width_text` |
| 缺规格换船体 | 回退「敞口木舟（宽1.4米，可容3人）」 | 返回空串，**不替换** | `build_boat_replacement` |
| 水下姿态补高度 | 任意厘米数 → 固定「约 2cm」 | **只认绑定到头/头顶的露头高度**；只补否定说明，不写数值；归属不明 → 保留原文 + `warn` | `constraints/v6_43_prompt_enforcement.py::_fix_vague_anchor` |
| 「窄身」删除 | 字段出现船类词就全文删除 | 只在**可证明属于船**时替换（同一名词短语/船后括号/船名词+长度）；否则保留 + `warn` | `constraints/boat_width.py` |
| 船体展示名 | 非「舴艋」一律改「敞口木舟」 | 保留输入 `subtype`/`identifier`/库键；无名称不替换 | `shared/scene_objects_bank._boat_display_name` |
| 裁切短语删除 | 直接删除 | **只报不改**（`action="warn"`） | `constraints/boat_crop.py` |
| schema 作品示例 | 「照月湾小舟」「杨府别庄大门」 | 匿名占位 `<地点A>的<载具>` | `_text_gen/deepseek_schema.py` |
| GAP 兜底注释 | 注释称「走锦衣夜行第一章兜底」 | 更正为「通用词表兜底」 | `step01_script_gen/gap_analysis.py` |

复核点名的三个反例与新增断言（入库）：

- `潜水员潜伏水下，手持长10cm的探针` → 原文保留、不写露头高度、记 `warn`；
- `潜伏水下，仅头顶约5cm露出` → 保留 5cm、不出现 2cm、补「不是上半身出水」；
- `窄身机甲站在小舟旁。` → 原文保留、记 `warn`（船宽不借用给其他实体）；
- `subtype=封闭式救生艇` → 替换文本保留「封闭式救生艇（长6米宽2米）」。

验收：`tests/step01_script_gen/constraints/test_s02_content_independence.py`（13 条，已入库）。
**本地旧契约用例已迁移**（3 条）：`test_anchor_ambiguity::test_apply_integrates_all_4_rules`
（不再要求 2cm）、`test_prompt_enforcement::{test_muzhou_preserved_without_a_spec,
test_works_on_hook_via_enforce_all}`（无规格时保留原形制）。

## S03 因果绑定与引用解析（复核缺口已补）

- 承载关系只认 `shot.plan_refs`；无绑定 → `needs_semantic_review`，既不豁免也不认定缺失。
- **引用先解析到本稿计划的实际条目**（`chapter_outline.shot_beats` / `dialogue_lines` /
  `fact_uids` / `main_events`）：目标缺失、越界、非数字的引用逐条记入 `invalid_refs`，
  **不参与承载关系**（`shared/execution_semantics._resolve_plan_refs`）。
- **过程证据**：动作分段 ≥2，或 ≥2 个时刻/阶段不同的关键帧序列；单个关键帧只是静态
  定格，不再算过程证据。
- 时间标记仍只在**已绑定镜头**范围内核对。
- 复核反例已入库：原稿 `time_ellipsis_lost/reject`；加一镜鸟 + 单关键帧 +
  `plan_refs.beats=[999]` → 仍 reject，`invalid_refs` 记 `out_of_range`。
- 夹具 `temporal_ellipsis.json` 的变异版 `plan_refs` 修正为指向真实节拍（B3→B2），
  摘要已更新到 `manifest.json`。

## S04 正向提示与下游消费（两处无依据注入已删）

- `shared/vimax_bridge._inject_object_size_anchor` 只注入**有来源**的名称/形制/长宽：
  - 删除「船宽约为人物肩宽的三倍，…落脚空档」——肩宽比例没有输入来源；
  - 删除景别词触发的「只画船头/船舷局部，船体向画外延伸」——裁切/构图由输入或计划决定；
  - 内嵌尺寸剥离改为**只在确有来源尺寸要注入时**执行，避免未命中 scene_objects 时净丢内容。
- `_text_gen/deepseek_schema` 两处作品示例改匿名（上一轮已完成）。
- 受影响测试已迁移：`tests/shared/test_frame_prompt_v652.py` 改为断言有源尺寸 +
  不含肩宽/裁切锚；新增「无来源时不剥离尺寸」用例。

## S05 贯通与恢复（跨内容消费链已贯通）

- 新增 `tests/integration/test_s05_content_chain.py`：两个内容不同的匿名样本，
  走**真实消费链** `run_hard_fix`（Step01 后处理）→ 落盘 `script_result.json` →
  `_gen_first_frame_async`（Step02a 真实逐帧入口）→ `compose_frame_prompt`
  （唯一组装入口）→ 生成器桩。
- 断言：有规格的船宽到达最终提示；其他实体的「窄身」保留；水下 5cm/9cm 保留且无 2cm；
  无规格的船保留原文；非舴艋船型保留名称/形制；最终提示不含肩宽/裁切注入。
- 上一轮已有：`tools/build_m02_u6_candidate.py` 的「修订 → 后处理 → `project_for_commit`」
  逐问题残余 + 投影后残余两栏。

## S06 决策包（已补具体样本，G3 仍缺）

`docs/step01-layered-execution/S06-decision-package-2026-11-13.md`：

| 决策 | 样本 | 关键读数 |
|---|---|---|
| G1 | 沉默 vs 有证据关闭 | 现行门 resolved=1；G1 门=0（沉默样本） |
| G2 | 大特写→远景 | 现状改写成「全景」；validation 已报 6 级 warning |
| G6 | 声明 5s / 窗口 4s / 无音轨 | `actual_sec=5.0`、`verified=False`，仍产出 `audio_window_overflow` |
| G7 | 等价派生 / 目标丢失 / 保护违规 | R1 误拒 A；R2 拒 B；R2+保护门三例全对 |
| G3 | **未出** | 需先实现「参考清单先行」再出逐字段 diff |

## S07 交付与限制

- 本轮提交内容：S02–S05 补修 + 入库用例 + S06 样本与决策包 + 本文件。
- 已知限制：G1/G3/G4/G6/G7 未实施（决策材料见 S06）；G3 差异样本未出；
  S06 的 G4（正式采纳）仍无样本；真实模型/媒体调用未执行。
- 内容收益：本包只证明「已确认的无来源补写/删除路径不再发生，且修好的内容能到达
  最终提示与生成器桩」；未做真实模型对照，不据此宣称画面质量提升。
