# Step02a 通用帧契约修复与生成评阅 Skill（S01–S07 交付文档）

## 1. 基线与边界

- **代码基线 HEAD**：`59640c8ff1cbc6fc4d26142d150aa73f3057b5d0`
- **保留的并行工作树基线（未覆盖）**：`.env.content`、`_image_gen/volcengine_provider.py`、`shared/model_alias.py`、`shared/model_caps.py`、`shared/ref_compose.py`、`step02a_frame_gen/verify_acceptance.py`、`tests/shared/test_s27_boat_composition.py`、`tests/step02a_frame_gen/test_verify_only.py`
- **冻结现场 Step01 身份**：`run_id=20261126T075410-26828`，`status=COMMITTED`，`script_revision=e916207daf6d093fa3ba58fcff89efd03c72b6deb8495ced352d53a766b1c4da`
- **基线与复现记录**：[.tmp/step02a_contract/s01_baseline_freeze.json](../../.tmp/step02a_contract/s01_baseline_freeze.json)、[.tmp/step02a_contract/s01_repro_evidence.json](../../.tmp/step02a_contract/s01_repro_evidence.json)、[.tmp/step02a_contract/s06_verification_report.json](../../.tmp/step02a_contract/s06_verification_report.json)
- **调用与产物边界**：全程 **0 次**文本、图片、视频、TTS 真实模型调用；正式目录 `output/` 零修改。

## 2. S01–S07 模块与责任映射

| 模块 | 目标与实现要点 | 责任文件 / 入口 |
|---|---|---|
| S01 | 基线冻结（18 个代码/配置项 + 65 个正式受保护文件 SHA-256）与 4 类现场缺陷隔离复现 | `.tmp/step02a_contract/s01_baseline_and_repro.py` |
| S02 | `inspect_frame_spec` 区分合法全离屏、缺失与畸形契约；`project_frame_facts` 显式别名解析（歧义不猜测）、`states`/`props`/`support` 来源标记及无删边矛盾检测 | [shared/frame_spec.py](../../shared/frame_spec.py)、[shared/prompt_facts.py](../../shared/prompt_facts.py) |
| S03 | `add_image_strict_constraints` 按 `frame_layouts` 投影人数/性别/禁血例外并支持 `keyframe`；`_inject_frame_spec` 保留显式 `states` 列表不被 `dynamic_features` 或首帧动作剥离覆盖；`is_empty_scene` 不把 `frame_layouts` 在场帧误判空镜；`_extract_prop_layout` 优先直读 `frame_layouts[].props`；`compose_frame_prompt` 收敛 `first_frame` / `last_frame` / `keyframe` | [shared/style_prefix.py](../../shared/style_prefix.py)、[shared/vimax_bridge.py](../../shared/vimax_bridge.py) |
| S04 | `_collect_portrait_refs_for_shot` 在 `roster` 非空时直接按名册选肖像（不再被正文称谓屏蔽）并显式记录 `missing`；`_scoped_asset_identifiers` 覆盖别名与道具术语；`ImageGeneratorAiVideoAdapter.prepare_single_image(..., materialize=False)` 与 `VolcengineImageService.generate` 对 `frame_contract`、编码失败与回退重算 fail-closed；`execution_plan` 修复 `kf`（`kf_index`）及 `lf -> first_frame` 依赖 | [_image_gen/models.py](../../_image_gen/models.py)、[_image_gen/volcengine_provider.py](../../_image_gen/volcengine_provider.py)、[shared/ref_compose.py](../../shared/ref_compose.py)、[shared/vimax_adapters.py](../../shared/vimax_adapters.py)、[step02a_frame_gen/execution_plan.py](../../step02a_frame_gen/execution_plan.py)、[step02a_frame_gen/main.py](../../step02a_frame_gen/main.py) |
| S05 | `qc_state` 与 `build_qc_frame_meta` 绑定图片 SHA-256 与 `(shot_id, frame_kind, kf_index, rule_version, contract_digest)`；`_maybe_qc_frame` 不把 settled `qc_rejected` 写成 `qc_not_run` 或放行；`build_image_product_quality` 写入 `schema_version=step02a.product_acceptance/2` 并分账 `file_ok` / `qc_ok` / `admission_status`；`verify_acceptance` 与 `quality_gate.require_gate` 拒绝 `qc_rejected`、`qc_stale`、`qc_call_failed`、`qc_not_run` 与旧版无绑定报告（未改动 Step01 `RULE_VERSION`） | [shared/image_qc.py](../../shared/image_qc.py)、[shared/quality_gate.py](../../shared/quality_gate.py)、[step02a_frame_gen/image_prompt_quality.py](../../step02a_frame_gen/image_prompt_quality.py)、[step02a_frame_gen/verify_acceptance.py](../../step02a_frame_gen/verify_acceptance.py) |
| S06 | 控制匿名多领域测试矩阵（作坊修秤、北街药铺、官道马车）与隔离副本 Ch1 第 6/7 镜回归、65 个正式文件保护审计 | [test_frame_contract_matrix_s06.py](../../tests/step02a_frame_gen/test_frame_contract_matrix_s06.py)、`.tmp/step02a_contract/s06_verify_and_audit.py` |
| S07 | 交付项目评阅 skill、精简核对表、`AGENTS.md` 入口指针与本交付记录 | [SKILL.md](../../.agents/skills/aivideo-frame-review/SKILL.md)、[checksheet.md](../../.agents/skills/aivideo-frame-review/references/checksheet.md)、[AGENTS.md](../../AGENTS.md) |

## 3. 隔离 Ch1 回归前后对照（0 正式改动）

| 缺陷项 | 修复前（S01 复现） | 修复后（S06 隔离副本） |
|---|---|---|
| Shot 6/7 肖像参考收集 | `shot6_portrait_refs=[]`、`shot7_portrait_refs=[]` | Shot 6 返回 1 张（`杨旭/face.png`）、Shot 7 返回 2 张（`杨旭/face.png`、`听香/face.png`） |
| Shot 7 `ff` 在场人数 | `roster_count=2`，prompt 输出 `"总共1人"` | `roster=["听香", "杨旭"]`，prompt 含 `"总共2人"` 且涵盖双人 |
| Shot 6 `ff` 显式状态 | `states=["心口暗红"]`，prompt 缺失 `"心口暗红"` | prompt 保留 `"心口暗红"` |
| Shot 7 `qc_rejected` 与准入 | 盘上 `qc_rejected` 但 `build_image_product_quality` 返回 `PASS` | `build_image_product_quality` 返回 `status="BLOCK"`（`file_ok=True, qc_ok=False`），旧无绑定 sidecar 记 `qc_stale`，`require_gate` 抛出 `QualityGateError` 拦截 |

## 4. 测试、静态检查与正式产物保护结果

- **Ruff 检查**：对 S02–S06 涉及的 14 个生产文件与 5 个新增测试文件执行 `python -m ruff check`，结果 `All checks passed!`。
- **全量相关测试套**（`pytest`，`tests/tools/test_ruff_gate.py` + S02–S06 新增与现有受影响测试共 27 个测试文件 / 274 项用例）：**268 passed, 6 skipped, 0 failed**（6 个跳过均来自外部数据集缺失、未设 `U3_EVIDENCE_DIR` 或本地多正式章输入）。
- **正式产物保护审计**（`s06_verify_and_audit.py`）：`protected_count=65`，`changed_protected=[]`，`missing_protected=[]`，`protection_clean=true`。
- **Skill 校验与推演**：已核验 `SKILL.md` YAML frontmatter（`name: aivideo-frame-review`、`description`）与 `references/checksheet.md` 存在性（4422 bytes），覆盖只读诊断、合约冲突阻断、QC 拒收重试边界与工程合规/美感分离四类推演场景。

## 5. 五层分账记账

1. **代码落盘**：已完成（上述 S02–S05 责任模块 + Stage 1 剩余收口 `P1-1b`/`P1-2b`/`P1-4b`/`P1-5` + 4.1 节 `decision_ledger` + S07 skill 与文档）。
   - `execution_plan._image_model()` 解析有效生图模型（`VOLCENGINE_T2I_MODEL` 等）与规范 ID；
   - `shared/image_qc.py` 的 `build_qc_prompt` 与 `_expected_persons` 对 `first_frame`/`last_frame`/`keyframe` 统一经 `project_frame_facts` 核算人数与别名；
   - `build_image_product_quality` 与 `verify_acceptance` 显式输出 `locked_human_approved` 与 `admitted`；
   - `shared/vimax_bridge.py` 落地 `request_conflict`、`missing_asset`、`qc_dispute`、`stochastic_miss` 四类失败路由（前三类 0 重抽 POST，第四类严守 `QC_MAX_ATTEMPTS`）；
   - [shared/request_events.py](../../shared/request_events.py) 扩展 `decision_ledger` 事件写入 `chapter_scope`、`shot_index`、`frame_role`、`model_id`、`prompt_version`、`ref_strategy`、`ref_identities`、`attempt_index`、`action_type`、`defect_class`、`qc_verdict`、`human_usability`、`non_target_damage` 等 4.1 节台账字段。
2. **离线入口通过**：`inspect_frame_spec`、`project_frame_facts`、`compose_frame_prompt`、`prepare_single_image(materialize=False)`、`build_visual_plan`、`build_image_product_quality`、`run_verify_only`、`require_gate` 与 `emit_decision_ledger` 均已通过单元测试（含 [test_stage1_closure.py](../../tests/step02a_frame_gen/test_stage1_closure.py)）。
3. **桩端到端通过**：网络封锁下的 3 领域匿名全链路测试（`test_frame_contract_matrix_s06.py`，3 passed）、四类故障止损桩测试及隔离副本 Ch1 第 6/7 镜回归已通过。
4. **真实模型调用**：**0 次生图 POST、0 次视觉 QC 调用、0 次视频/TTS/文本调用**（实际画面质量与跨镜人物一致性收益待 Stage 2 小样本验证）。
5. **正式采纳状态**：未触碰正式 `output/` 产物；`test_image_retry_cap.py::test_output_sensitive_soft_skip_also_aborts` 存在既有 5.0 Pro+Flash 双模型队列下 `VOLCENGINE_IMAGE_MAX_ATTEMPTS=1` 触发尝试上限的既有交互（未修改该测试）。因工作树尚包含并行 5.0 Pro/Flash 及 Step02a 既有未提交改动，未自动混编提交。

## 6. Stage 2 小型冻结缺陷集 `[GATE]`（需人工批准确认额度后运行）

- **Stage 2 前置门禁已完成**：Stage 1（`P1-1 ~ P1-5` + 4.1 台账）已全绿通过，65 个正式文件保护 SHA-256 零变化。
- **建议冻结缺陷集（≤ 4–8 镜，隔离根 `--candidate-root`，单次显式审批额度）**：
  - 优先复用第 1、2 章代表镜头（含第 1 章第 2 镜尾帧吹箫姿态粘性、第 6/7 镜伤态与多人肖像、以及空镜/双人递物边界）；
  - 单次显式设置 `VOLCENGINE_IMAGE_MAX_POSTS`、`QC_MAX_ATTEMPTS=2` 与 `STEP02A_IMAGE_BUDGET_TOTAL`，等待你确认具体镜头列表与预算额度后启动 Stage 2 实跑对照。
