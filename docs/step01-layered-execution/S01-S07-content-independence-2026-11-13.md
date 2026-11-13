# S01–S07 阶段交付：内容无关性修复与决策对齐（2026-11-13）

基线 `9c63583`；本包提交见文末。范围按用户计划 S01–S07，视频模型选择后置。

## S01 基线与决策对齐

### 冻结身份

| 项目 | 值 |
|---|---|
| 起始提交 | `9c63583` |
| 本轮提交 | `2a91d7e`（复核第四轮：显式 `plan_refs` 承载绑定 + 逐问题残余）、本包提交 |
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
| G6 声音两态 | 未拆：`build_voice_preparation` 仍是单态 | `shared/audio_policy.py` | 是否拆准备态/确认态 |
| G7 后处理撤销接门 | 只记账（`postprocess_reverted`），未接拒绝判据 | `step01_script_gen/main.py` | 是否按残余接门 |

> 更正：此前汇总写「G2 全部未改」不成立，`530fdbb` 已改变三项行为。

## S02 内容修复（已完成代码，本地用例部分迁移）

| 行为 | 修前 | 修后 | 位置 |
|---|---|---|---|
| 缺规格补宽度 | 回退 `宽1.4米` | 返回空串，**不改写** | `shared/scene_objects_bank.boat_width_text` |
| 缺规格换船体 | 回退「敞口木舟（宽1.4米，可容3人）」 | 返回空串，**不替换** | `build_boat_replacement` |
| 「窄身」删除 | 全字段无条件删 | 仅在字段**提到船**时处理 | `constraints/boat_width.py` |
| 裁切短语删除 | 直接删除 | **只报不改**（`action="warn"`） | `constraints/boat_crop.py` |
| 水下姿态补高度 | 无条件加「仅头顶约 2cm」 | 仅输入已给高度时才补说明 | `constraints/v6_43_prompt_enforcement.py` |
| schema 作品示例 | 「照月湾小舟」「杨府别庄大门」 | 匿名占位 `<地点A>的<载具>` | `_text_gen/deepseek_schema.py` |
| GAP 兜底注释 | 注释称「走锦衣夜行第一章兜底」 | 更正为「通用词表兜底」 | `step01_script_gen/gap_analysis.py` |

验收：`tests/step01_script_gen/constraints/test_s02_content_independence.py`（10 条，已入库）
覆盖「无规格不写尺寸/不替换」「非船物体的窄身保留」「有规格仍正确改写」「裁切只报不改」
「水下高度有来源才补」。

**未完成**：本地未入库用例（`tests/step01_script_gen/constraints/*` 多数不在 git 白名单）
仍有 3 条断言编码旧契约（`test_anchor_ambiguity::test_apply_integrates_all_4_rules`、
`test_prompt_enforcement::{test_muzhou_preserved_without_a_spec, test_works_on_hook_via_enforce_all}`），
需随下一轮迁移；入库套件不受影响。

## S03 因果绑定（第四轮已完成，本轮复核结论）

- 承载关系只认 `shot.plan_refs`；无绑定 → `needs_semantic_review`，既不豁免也不认定缺失。
- 时间标记按已绑定镜头范围核对。
- 新增对照：同地点无关镜头、未绑定镜头里的「次日」均不改变裁决。
- **缺口（计划 §2 点名）**：当前未校验「引用目标是否真实存在、类型/版本是否正确、是否指向被质疑关系」。
  `plan_refs.beats` 的越界与类型问题尚未在 `check_execution_semantics` 里报告 → 列入 S03 待办。

## S04 正向提示与下游消费（部分）

- 已完成：`deepseek_schema` 两处作品示例改匿名。
- 未完成：`shared/vimax_bridge.py:2084` 仍无条件注入「只画船头/船舷局部…船体向画外延伸」；
  最终参考清单 → `prepare_request` 的三分支对应未做。

## S05 贯通与恢复

- 已有：`tools/build_m02_u6_candidate.py` 走「修订 → 后处理 → `project_for_commit`」，
  逐问题残余 + 投影后残余两栏（5 消除 / 2 未入轮）。
- 未做：`画面计划 → 生成器桩` 的跨内容回放（P2 已接线，未在此工具里串联）。

## S06 决策包

| 项 | 选项 | 影响 | 推荐 |
|---|---|---|---|
| G1 | ① 阻塞问题必须有关闭证据才放行 ② 维持影子 | ① 会拦下「模型沉默」被当已修 ② 保持现状 | ①，但只对阻塞级 |
| G2 | ① 保留跨度自动改写 ② 取消自动改写只报 warning ③ 保留但限定非修复轮 | ② 最保守、不再撤销修复；① 会与审计修复冲突（已实测） | ③ 现状已接近，建议再评估② |
| G3 | ① 用最终参考清单构建正文 ② 维持现状 | ① 改动大、影响全部请求 | 需先出差异样本 |
| G6 | ① 拆两态 ② 维持单态 | ① 需改 `audio_policy` 消费方 | ① |
| G7 | ① 按残余接门 ② 只记账 | ① 会拒收「等价派生」 | 需先区分等价派生与目标丢失 |

## S07 交付与限制

- 本轮提交内容：S02 代码 + 入库用例 + 本文件。
- 已知限制：G1/G3/G4/G6/G7 未实施；S03 引用有效性未校验；S04 下游注入未改；
  S05 跨内容回放未串联；本地 3 条旧契约用例待迁移。
- 内容收益：本包只证明「不再编造无来源内容」，未做真实模型对照。
