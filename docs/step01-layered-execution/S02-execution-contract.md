# S02：`shared/execution_contract.py` 执行合同（R1/R2/R3 补齐）

> 本文件记录 S02 的实现语义、真实装载入口、自动语义检查器、绕过封堵与离线读数。
> 未改变 Step01 正式门禁语义，未采纳正式产物，未发起模型/媒体调用。
> S01 的责任表与样本族见 [S01-baseline-and-responsibility.md](S01-baseline-and-responsibility.md)。
>
> 本轮补齐上一轮四个缺口：真实非测试调用点、`expected.codes` 自动检查器、
> `prompt`/`visual_plan.visible_characters` 语义保护、按模块分别记读数。

## 1. 接口与数据

模块：`shared/execution_contract.py`。三个公开合同函数，另有一个摘要助手：

```python
build_execution_binding(script: dict, *, scope: dict, stage: str,
                        source_digest: str) -> dict

validate_execution_binding(binding: dict, script: dict, *,
                           expected_scope: dict,
                           expected_stage: str) -> list[dict]

validate_execution_changes(before: dict, after: dict, *,
                           binding: dict,
                           allowed_changes: list[str]) -> list[dict]

script_digest(script: dict) -> str   # source_digest 的唯一算法
```

绑定形状（`schema="execution_binding.v1"`, `contract_version=1`）：

| 字段 | 含义 | 本模块行为 |
|---|---|---|
| `source` | 章范围、Step01 revision、规范化 JSON 摘要、摘要算法、输入身份 | 构建时从稿件提取；消费前逐项重验 |
| `requirements` | 本阶段负责履行的事实、镜头、人物、声音单元 | 从 `chapter_outline` / `shots` / `characters` / `voice_units` 确定性提取 |
| `allowed_changes` | 本次允许调整的执行字段 | 构建时为空；由调用方显式声明 |
| `changes` | 实际变化及理由 | 构建时为空；合法变化必须留下 `path` + `reason` |
| `checks` | 预检、实测结果与证据引用 | 构建时为空；由责任阶段填写 |
| `unresolved` | 尚未满足或尚未验证的要求 | 构建时为空 |
| `feedback` | 责任阶段、问题身份、受影响对象与所需动作 | 构建时为空 |

`source_digest` 必须由 `script_digest(script)` 计算：去掉下划线开头的运行期字段后，
按 `sort_keys=True` 的规范 JSON 做 sha256。构建时会核对传入摘要与当前稿件摘要，
不一致直接拒绝（`source_digest_mismatch`），避免把另一份输入的摘要写进绑定。

## 2. 校验语义

### 2.1 `validate_execution_binding`

返回空列表 = 通过。记录至少含 `code` / `severity`（统一 `reject`）/ `path` / `message`；
需要时附 `expected` / `actual`。

| 场景 | 典型 code | 路径/说明 |
|---|---|---|
| 绑定不是对象 | `binding_missing` | `binding` |
| 顶层字段缺失 | `field_missing` | `binding.<field>` |
| schema/版本/阶段为空 | `field_blank` | `binding.schema` / `binding.contract_version` / `binding.stage` |
| 未知 schema | `schema_unknown` | 只接受 `execution_binding.v1` |
| 未知合同版本 | `contract_version_unknown` | 只接受 `1`（整数或字符串 `"1"`） |
| scope 缺键/类型/空白 | `field_missing` / `scope_*_invalid` / `field_blank` | `binding.source.scope.*` |
| 跨章/跨部 | `scope_mismatch` | `binding.source.scope`，附 `expected`/`actual` |
| 绑定 scope 与稿件身份不符 | `scope_script_mismatch` | 绑定 scope 与稿件 `book_name`/`chapter_index` 不一致 |
| 阶段不符 | `stage_mismatch` | `binding.stage` |
| 缺少/空白 revision | `field_missing` / `field_blank` / `script_revision_blank` | 绑定侧与稿件侧分别报 |
| 稿件缺 book_name / chapter_index | `script_book_name_blank` / `script_chapter_invalid` | `script.book_name` / `script.chapter_index` |
| revision 不符 | `script_revision_mismatch` | `binding.source.script_revision` |
| 摘要格式非法 | `source_digest_invalid` | 必须是 64 位小写十六进制 sha256 |
| 摘要算法未知 | `digest_algorithm_unknown` | 只接受 `sha256.canonical_json.v1` |
| 输入已变化 | `source_digest_stale` | 当前稿件摘要与绑定摘要不同，派生计划不得沿用 |
| 期望 scope/stage 未声明 | `expected_scope_missing` / `expected_stage_missing` | 调用方契约 |

`expected_scope` 只比较 `shared.run_scope.scope_identity` 的 `chapter` / `book_index` /
`book_name` 三个身份键；`source` / `overrides` / `summary` 等诊断字段不参与。

### 2.2 `validate_execution_changes`

逐叶比较 `before`/`after`，对每个实际变化判定：

1. **语义保护优先**：路径命中 `PROTECTED_SEGMENTS` → `protected_field_change`，即使列进
   `allowed_changes` 也不放行；
2. **授权范围**：不在 `allowed_changes` 覆盖范围 → `change_not_allowed`；
3. **理由留痕**：已授权但 `binding.changes` 没有匹配的非空 `reason` → `change_reason_missing`。

受保护路径段（节选）：`facts` / `fact_uids` / `required_facts` / `fact_bindings` /
`hook_bindings` / `main_events` / `climax` / `causal_chain` / `end_hook` /
`character_motives` / `shot_beats` / `events` / `order` / `characters` / `story` /
`speaker` / `voice` / `voice_mode` / `audio_mode` / `dialogue_lines` /
`narration_lines` / `narration_segment` / `subtitle` / `spoken_text`。
`voice_units[*].text` 这类声音上下文中的 `text` 也受保护；普通画面字段 `prompt` /
`motion_desc` / `camera_movement` 不在保护集内。

`allowed_changes` 的匹配规则：

- 精确路径或子树：`shots[0].prompt` 覆盖自身；`shots` 覆盖 `shots[0].prompt`；
- 简单字段名（不含 `.`/`[`）按“任意镜头的同名字段”授权，如 `prompt` 覆盖
  `shots[0].prompt`；这是为按字段族声明保留的便利，不改变受保护字段优先级。

`binding.changes` 记录支持 `path` / `field` / `field_path` / `paths` + `reason`；
记录路径可以是实际变化路径的父路径（一个理由覆盖子树）。

### 2.3 纯函数与边界

- 不读文件、不访问网络、不写盘、不修改传入对象；
- 不生成时间戳/随机数，同一输入重复构建逐字相同；
- 不做自然语言语义等价判断；确定性校验只声明字段/摘要/权限能证明的范围；
- 未知/缺失/空白输入以结构化记录返回；只有构建期不可修复的输入才抛
  `ExecutionContractError`（带 `code`/`path`）。

## 3. R1：真实装载入口（非测试调用点）

| 调用点 | 调用 | 证据 |
|---|---|---|
| `step02a_frame_gen/execution_plan.py::build_visual_plan` | `build_execution_binding` → `validate_execution_binding` → `attach_semantic_checks` | `grep -n "build_execution_binding\|validate_execution_binding" step02a_frame_gen/execution_plan.py` |
| `step02a_frame_gen/main.py::_plan_only_report` | `build_visual_plan` / `validate_plan_identity`（plan-only） | `--plan-only` 输出 `visual_plan` / `execution_binding` |
| `step02a_frame_gen/main.py::main` + `gen_all_frames` | 真实生成前构建、落 `visual_plan.json`、发送前重验 | `main.py:583` 起、`gen_all_frames` 入口 |

`grep` 可复现：

```powershell
Select-String -Path step02a_frame_gen/execution_plan.py,step02a_frame_gen/main.py `
  -Pattern "build_execution_binding|validate_execution_binding|build_visual_plan|validate_plan_identity"
```

## 4. R2：自动语义检查器（三族 `expected.codes`）

模块 `shared/execution_semantics.py` 实现六个 code：`causal_gap`、
`time_ellipsis_lost`、`offscreen_speaker_visible`、`listener_reaction_missing`、
`audio_window_overflow`、`native_dialogue_unverified`。

- 基线三族 findings 均为 `[]`；
- 变异三族分别自动检出 `[causal_gap, time_ellipsis_lost]`、
  `[offscreen_speaker_visible, listener_reaction_missing]`、
  `[audio_window_overflow, native_dialogue_unverified]`；
- 每个 code 另有独立最小变异测试，不是靠同一处“一起报错”；
- `visual_plan` / `voice_preparation` 真实入口消费同一检查器。

## 5. R3：`prompt` / `visible_characters` 绕过封堵

- `GUARDED_SEGMENTS`（`prompt`、`visual_plan.visible_characters`、`frame_layouts`、
  `visible_props`、`main_subject`、`offscreen_speakers`、`on_screen` 等）属于
  **受验证语义承载字段**：`allowed_changes` 列出也不放行；
- 必须有 `binding.checks` 中 `checker/kind` 以 `execution_semantics` 开头、
  `status=pass`、路径覆盖本次变化的记录；
- 检查失败时 `attach_semantic_checks` 写 `unresolved`（`route=step01`）与 `feedback`，
  `validate_execution_changes` 返回 `semantic_change_unverified`；
- `protection` 可配置追加受保护/受验证字段（默认集 + 绑定内配置 + 调用参数）。

## 6. 离线单测命令与读数（按模块分开）

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest `
  tests/shared/test_execution_contract.py -q `
  --basetemp=.pytest_tmp/s02_contract -p no:cacheprovider
# 43 passed（含原有 39 + 本轮 R3 新增 4）

C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest `
  tests/shared/test_execution_semantics.py -q `
  --basetemp=.pytest_tmp/s02_semantics -p no:cacheprovider
# 11 passed（R2 新增；三族参数化 + 六个独立反例 + 合法细化/差分）
```

**不再把含原有回归的总数当本模块验收**：R2 看 `test_execution_semantics.py`
的 11 条；R3 看 `test_execution_contract.py` 新增的 guarded/bypass 用例。

本轮 S02 代码身份（sha256，file bytes 见交付报告；文件已改动，旧摘要作废）。

覆盖矩阵：

| 要求 | 用例 |
|---|---|
| 缺失 | `test_validate_binding_reports_missing_and_blank_fields`、`test_validate_changes_missing_or_unknown_binding`、构建期缺失字段参数化 |
| 空白 | 同上（stage/revision/book_name/digest/changes reason 空白） |
| 未知版本 | `test_validate_binding_unknown_schema_and_version`、`test_validate_changes_missing_or_unknown_binding` |
| 跨章 | `test_validate_binding_cross_chapter_and_book`、`test_validate_binding_rejects_scope_not_matching_script_identity` |
| 错误 revision | `test_validate_binding_wrong_revision_and_stale_input` |
| 过期输入 | 同上（同 revision 改内容 → `source_digest_stale`） |
| 合法调整通过 | `test_validate_changes_allows_declared_execution_field`、`test_validate_changes_list_growth_and_nested_paths`、`test_validate_changes_parent_permission_covers_child` |
| 越界/受保护拒绝 | `test_validate_changes_rejects_undeclared_field`、`test_validate_changes_rejects_protected_fields_even_if_allowed` |
| 变化理由 | `test_validate_changes_requires_reason`（无记录/空理由/空白理由） |
| 样本族自足 | `test_fixture_manifest_digests_match_file_bytes`、`test_fixture_families_build_and_validate_bindings`、`test_fixture_baseline_binding_is_stale_against_mutation`、`test_fixture_voice_timing_legal_change_and_text_protection` |
| guarded 字段绕过 | `test_guarded_prompt_change_without_semantic_check_is_rejected`、`test_offscreen_visible_bypass_is_rejected_even_if_allowed`、`test_semantic_pass_record_allows_guarded_execution_refinement`、`test_protection_is_configurable_per_binding` |
| 自动语义检查 | `tests/shared/test_execution_semantics.py` 11 条（三族参数化 + 六 code 独立变异 + 合法细化/差分） |

测试只读受控夹具、只在 pytest 临时根内运行；无网络、无模型、无媒体、无凭据。
测试文件与夹具已用 `.gitignore` 的独立标记块放行（`[contract/step01-layered-execution]`）。

## 7. 兼容与责任边界

- **已接入真实入口**：Step02a `--plan-only` 与真实生成前都构建/重验绑定；S04 冻结请求
  带 `requirements.execution`；S05/S06 消费各自报告。合同从「存在」变为「入口强制」。
- **不改正式门禁语义**：`shared/execution_contract.py` 不修改 Step01 的 quality/audit/
  commit 判定；正式切换清单属于 S07（只产出清单，不执行切换）。
- **旧产物**：没有 `execution_binding.v1` 字段的旧产物按旧入口规则处理，不能据此认定
  已通过新验收（S07 迁移时显式选择正式/候选输入）。
- **语义限制**：`validate_execution_changes` 只能证明字段/权限/摘要层面；台词改写、
  事实等价、因果是否成立等自然语言判断仍由内容合同与人工/模型复核承担。
- **受验证字段不是禁止细化**：合法执行细化仍可改 `prompt`/构图，但必须由真实入口
  运行语义检查并留下 `checks` 通过记录；没有证据不放行。

## 8. 未验证项

- 未在真实模型/媒体产物上验证 `source_digest` 跨进程稳定性和语义检查器的判别力；
  当前只对受控夹具与构造样本做了确定性测试。
- 未验证真实候选 manifest 的并发发布与中断恢复；S07 只复核既有事务/锁契约与
  消费前二次摘要核对，未模拟真实发布中断。
- 检查器只证明六个结构化主张；台词改写等价、因果是否成立等自然语言判断仍由
  Step01 审计/人工承担。

