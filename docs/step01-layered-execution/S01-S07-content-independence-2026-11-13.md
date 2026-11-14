# S01–S07 阶段交付：内容无关性修复与决策对齐（2026-11-13）

基线 `9c63583`；本包提交见文末。范围按用户计划 S01–S07，视频模型选择后置。
2026-11-13 复核后，S02/S03/S04/S05 的确认缺口已按复核意见补修；S06 已补
G1/G2/G6/G7 的具体样本；2026-11-14 复核后 S03 歧义跨阶段保留、G3 三分支
隔离样本已出。**2026-11-14：S06 六项裁决已按获批口径接线（G1/G7/G2/G6/旧稿），
最终规则指纹 `db08353ea107b486`；G3 仍留隔离分支。当前状态：S06 接线完成、
工作区深度瘦身完成，进入 U6 shot 13 候选修订与重算。**

## S01 基线与决策对齐

### 冻结身份

| 项目 | 值 |
|---|---|
| 起始提交 | `9c63583` |
| 上一轮提交 | `2a91d7e`（复核第四轮：显式 `plan_refs` 承载绑定 + 逐问题残余）、`96f3cc3`（S02 内容无关性首版） |
| 本轮提交 | `3e7b0ad`（S03 歧义跨阶段保留 + G3 三分支真实 prepare 对照）、`2d51b92`（长期证据 `.keep` 安全网 + 临时目录约定）；更早 `a2b6621`（S05 计划消费入口 + S03 身份生产/重映射 + G3 首版 + 合并归因） |
| 未入库工作树 | `.gitignore`、`docs/dsh-handoff/`、`start_studio.bat`、`.workbuddy/`、`baselines/**/__pycache__/` 属**他方**改动，本包未触碰 |

### 脏工作树归属

计划 §2 提到的 `execution_semantics.py` / `repair_fields.py` / 夹具 / 候选工具 / 第四轮报告，
是本会话上一轮的产物，已在 `2a91d7e` 收口并纳入基线；其余未提交项属并行接入方，保持原样。

### G1–G7 状态（逐项核对）

| 项 | 已核实状态 | 依据 | 待批准动作 |
|---|---|---|---|
| G1 问题关闭接提交门 | **未接线**：`issue_ledger.closed` 只产出影子台账，`quality_gate`/提交门未消费 | `step01_script_gen/issue_ledger.py`、`shared/quality_gate.py` | 是否对阻塞问题要求关闭证据 |
| G2 景别规则 | **已改三项**（不是「全部未改」）：`530fdbb` 起 `_shot_size_level` 改最长匹配、`framing_jump` 在修复轮跳过被授权镜头、final pass 只报不改；**跨度规则本身仍在** | `step01_script_gen/validation.py`、`constraints/framing_jump.py` | 跨度自动改写是否整体取消 |
| G3 参考定稿顺序 | 隔离样本已出：3 条正文分支、参考清单逐项对应、2 条真实 prepare 通过、full_prompt 记 pending；生产接线未做 | `tools/build_s06_g3_isolated_diff.py`、`shared/vimax_bridge.py`、`_video_gen/*_provider.py` | 是否改正文构建（裁决阶段） |
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

## S03 因果绑定与**目标关系**（本轮按复核补完）

- 承载关系只认 `shot.plan_refs`；无绑定 → `needs_semantic_review`，既不豁免也不认定缺失。
- **引用先解析到本稿计划的实际条目**（`chapter_outline.shot_beats` / `dialogue_lines` /
  `fact_uids` / `main_events`）：目标缺失、越界、非数字的引用逐条记入 `invalid_refs`，
  **不参与承载关系**（`shared/execution_semantics._resolve_plan_refs`）。
- **目标级关联**（2026-11-13 复核）：
  - 先从 `causal_chain` 条目取得**显式单位身份**：编号引用（`B2`/`F1`/`事件3`）或与计划
    单位正文**完全相等**（比较前先去掉条目里的引用）；内容身份与编号在同一类单位上
    冲突 → 不选边站，记 unresolved。
  - 再按 `plan_refs` / 节拍 `事实：` 段定位承载镜头；**过程与时间证据都限定在该范围**。
    章节级时间文本只计入显式绑定到该关系的事件/旁白，不再拼整章 `main_events`。
  - 关系无法唯一解析、或结果单位没有承载镜头 → 明确输出 `relation_status`
    （`unresolved`/`no_carrier`）+ `needs_semantic_review`，只出 advisory，不按全章判。
- **身份生产与修订维护**（2026-11-14 复核，最小链路已贯通）：
  - 生成端：`step01_script_gen/outline.py` 的 `causal_chain` 规则要求每条末尾写
    `（Bk）`（真实节拍编号；无对应节拍写「（未落节拍）」）。
  - 校验：`execution_semantics.validate_causal_identity` 输出
    `resolved`/`unresolved`/`invalid`（越界编号记 `causal_ref_out_of_range`）。
  - 修订重映射：`execution_semantics.remap_causal_refs` 按**正文完全相等**把旧编号
    映射到新编号；删除/拆分/重复正文 → 改「未落节拍」并留说明。已接线到
    `outline.apply_replan_ops`（插入/替换/拆分节拍）与
    `plan_feasibility.normalize_plan_ranges`（承载范围展开）。
  - 测试：`tests/shared/test_causal_identity.py` 15 条，覆盖校验、插入/删除/重排/
    拆分/重复正文重映射，以及 `apply_replan_ops` 真实入口插入后的身份维护。
- **过程证据**：动作分段 ≥2，或 ≥2 个时刻/阶段不同的关键帧序列；单个关键帧只是静态
  定格，不再算过程证据。
- 复核复现（匿名）：原稿 `time_ellipsis_lost/reject`；加一镜鸟（真实节拍 B3 + 两关键帧）
  仍 reject，`relation_carriers` 不含鸟镜；仅在 `main_events` 增加「次日，远方的渔民出海」
  也不再改变裁决。
- 夹具 `temporal_ellipsis.json` 的因果链条目补了 `（B1）/（B2）/（B3）` 身份，
  变异版 `plan_refs` 修正为真实节拍，摘要已更新到 `manifest.json`。
- **旧稿影子差异**（2026-11-14）：没有身份的旧稿只出 `causal_gap` advisory，并带
  `relation_note="该项未完成语义判定"`；`time_ellipsis_lost` 不触发，**不新增拒收**。
  实际准入影响记录在 `docs/step01-layered-execution/artifacts/s03/identity_shadow_diff.{json,md}`
  （旧稿 vs 新稿 vs 新稿+鸟节拍），正式采纳按既定决策处理，本包不改变默认门禁。
- **歧义状态跨阶段保留**（2026-11-14 复核）：
  - `remap_causal_refs` 写出的「未落节拍」在 `validate_causal_identity` 里**保持失效**，
    不再按正文重新解析成 resolved；
  - 正文匹配到同类多个候选（如同一正文的两条节拍）→ `ambiguous`（unresolved），
    不把候选全部纳入身份集合；
  - 内容身份与编号引用冲突 → `invalid`（与函数文档契约一致，此前误报 unresolved）；
  - 串联测试覆盖「重映射 → 身份校验 → 关系检查」，断言最终 `relation_status`
    与 advisory 状态。

## S04 正向提示与下游消费（两处无依据注入已删）

- `shared/vimax_bridge._inject_object_size_anchor` 只注入**有来源**的名称/形制/长宽：
  - 删除「船宽约为人物肩宽的三倍，…落脚空档」——肩宽比例没有输入来源；
  - 删除景别词触发的「只画船头/船舷局部，船体向画外延伸」——裁切/构图由输入或计划决定；
  - 内嵌尺寸剥离改为**只在确有来源尺寸要注入时**执行，避免未命中 scene_objects 时净丢内容。
- `_text_gen/deepseek_schema` 两处作品示例改匿名（上一轮已完成）。
- 受影响测试已迁移：`tests/shared/test_frame_prompt_v652.py` 改为断言有源尺寸 +
  不含肩宽/裁切锚；新增「无来源时不剥离尺寸」用例。

## S05 完整链路（受限修订 → 投影 → 画面计划 → **计划消费入口**）

- 重写 `tests/integration/test_s05_content_chain.py`，走完整承诺链：
  1. **受限修订入口** `main._repair_shot_warnings`（授权字段 + 合法桩响应）；
  2. **提交投影** `main.project_for_commit`（产出最终 revision）；
  3. Step01 落盘 `script_result.json`；
  4. **画面计划构建** `execution_plan.build_visual_plan`；
  5. **计划消费入口** `gen_all_frames(visual_plan=..., scope=...)`——内部
     `validate_plan_identity` / `verify_plan_execution` / `verify_plan_changes` /
     `verify_plan_coverage`，并把计划里的 `execution.unit` 交给 `gen_shot_frames`
     （不再拿 `projected["shots"]` 直接调逐帧入口）；
  6. 生成器桩记录最终提示。
- 两个样本有实质差异：`river` 连续 1 镜、画面载体；`harbor` 非连续编号 2/5/9、
  旁白+画面载体、两种船型、不同因果身份。
- **入口断言**（6 条测试）：
  - 合法计划调整（尾帧运镜「缓慢推近」）经 `execution.unit` 到达生成器；
  - 计划提示漂移 / 保护字段越权 / 覆盖缺失时，`gen_all_frames` 拒收且生成器**零调用**；
  - 计划绑定的 `script_revision` 与投影稿一致；生成器桩收到的是计划里的最终提示；
  - 后处理修好的宽度/形制到达最终提示；其他实体形制、无关注入不出现。
- **完成状态**：受限修订→投影→画面计划→计划消费→逐帧生成已贯通；真实模型生成仍未执行。

## S06 决策包（样本与证据层级已更正）

`docs/step01-layered-execution/S06-decision-package-2026-11-13.md`：

| 决策 | 样本 | 关键读数 | 证据层级 |
|---|---|---|---|
| G1 | 沉默 vs 有证据关闭 | 现行门 resolved=1；G1 门=0（沉默样本） | 台账真实；门禁**推演** |
| G2 | 大特写→远景 | 现状改写成「全景」；validation 已报 6 级 warning | 约束/校验真实 |
| G6 | 声明 5s / 窗口 4s / 无音轨 | `actual_sec=5.0`、`verified=False`，仍产出 `audio_window_overflow` | 准备/语义真实 |
| G7 | 等价派生 / 目标丢失 / 保护违规 | A 记账过/保护过/R1 误拒；B R2 拒；C 保护门拒 | 三判据真实；门禁组合**推演** |
| G3 | 参考清单先行（1 例 × 3 分支，隔离样本） | 参考清单不变；绑定逐项对应；两条真实 prepare 通过，full_prompt 记 pending | 隔离样本；未接线生产 |

- G7 的「现状」列已拆成「记账规则」与「现行保护门」：C 的保护违规由既有
  `locked_changed` 拒，不是记账规则拒；B 的目标丢失两者都不拦。
- `R2_plus_protection_matches_expected=true` 只证明这组三例的判据组合，
  不代表真实提交入口的最终读数。

## S07 交付与限制

- 本轮提交内容：S02–S05 补修 + S03 身份生产/重映射 + S05 计划消费入口 +
  S06 样本与决策包 + 合并失败归因 + 临时产物治理（`cbff266`、`ef2ab0c`）+
  长期证据 `.keep` 安全网（`2d51b92`）+ 本文件。
- **合并运行失败归因（2026-11-14）**：`tests/step02_video_gen` 在
  `tests/step02b_video_gen/test_step02b_report_wp5.py` 之前运行时，后者失败
  `FileNotFoundError: chapter_001/step02b_quality.json`。最小触发组合：
  `test_seedance_prompt_practices.py` + `test_step02b_report_wp5.py`（1 failed）。
  根因：`step02_video_gen.video_prompt_quality` 在**导入时**绑定
  `from shared.paths import chapter_dir`；前一个测试导入过它之后，后一个测试只
  monkeypatch `shared.paths.chapter_dir`，模块内旧绑定仍写真实章节目录。
  涉及文件与基线 `43de241` **逐字节相同**（`git diff` 为空），属测试顺序依赖，
  不是本包引入；已在测试内补 `_vq.chapter_dir` 绑定后修复，完整合并套件
  **1735 passed / 8 skipped**。
- 已知限制：G1/G2/G3/G6/G7 未实施（决策材料见 S06，进入裁决阶段）；
  G3 只有隔离样本、未接线生产；S06 的 G4（正式采纳）按具体候选单独审批；
  真实模型/媒体调用未执行（模型效果仍待真实对照）。
- 内容收益：本包只证明「已确认的无来源补写/删除路径不再发生，且修好的内容能沿
  受限修订→投影→画面计划→计划消费入口到达生成器桩」；未做真实模型对照，
  不据此宣称画面质量提升。
