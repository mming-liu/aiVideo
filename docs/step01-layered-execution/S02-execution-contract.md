# S02：`shared/execution_contract.py` 最小派生合同

> 本文件记录 S02 的实现语义、离线单测读数与未验证项。只做纯函数与离线测试；
> 未接入生产主门，未改变任何正式门禁语义，未发起模型/媒体调用。
> S01 的责任表与样本族见 [S01-baseline-and-responsibility.md](S01-baseline-and-responsibility.md)。

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

## 3. 离线单测命令与读数

命令（KICKOFF 指定 Python）：

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest `
  tests/shared/test_execution_contract.py -q
```

本轮读数：**39 passed**（`tests/shared/test_execution_contract.py`）。与 S01 的 R3 五个
测试文件组合运行读数为 **106 passed**（39 + 67）。

本轮 S02 代码身份（sha256，file bytes）：

| 路径 | 字节 | sha256 |
|---|---:|---|
| `shared/execution_contract.py` | 35624 | `a9960009441b3cefa2ebdad28dd1c02ff7a822831d68c27544e3aff0df5c540d` |
| `tests/shared/test_execution_contract.py` | 24055 | `2e412f62bcca679d7b20e664a0da2f92b0976e36ebbab26bad536135bbe32ea8` |
| `tests/shared/fixtures/execution_contract/manifest.json` | 1881 | `21e4999cfdba390953f3b20d046dea4e7a38584746a723006be1b31766042c1c` |

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

测试只读受控夹具、只在 pytest 临时根内运行；无网络、无模型、无媒体、无凭据。
测试文件与夹具已用 `.gitignore` 的独立标记块放行（`[contract/step01-layered-execution]`）。

## 4. 兼容与责任边界

- **尚未接入生产入口**：本轮只交付纯函数与单测；S03–S06 才把绑定写入候选 manifest/
  冻结请求/声音记录，并在消费前调用校验。没有生产调用点意味着“合同存在”不等于
  “生产已强制”。
- **不改正式门禁语义**：`shared/execution_contract.py` 不修改 Step01 的 quality/audit/
  commit 判定；正式切换清单属于 S07 GATE。
- **旧产物**：没有 `execution_binding.v1` 字段的旧产物按旧入口规则处理，不能据此认定
  已通过新验收（S07 迁移时显式选择正式/候选输入）。
- **语义限制**：`validate_execution_changes` 只能证明字段/权限/摘要层面；台词改写、
  事实等价、因果是否成立等自然语言判断仍由内容合同与人工/模型复核承担。

## 5. 未验证项

- 未在真实 Step02a/02b/03/04 入口装载/消费绑定；未验证候选 manifest 的写入与恢复。
- 未验证 `source_digest` 在真实稿件（含运行期字段、浮点/Unicode）上的跨进程稳定性；
  当前只对受控夹具与构造样本做了确定性测试。
- 未实现 `checks`/`unresolved`/`feedback` 的生产填写与消费；本轮只定义槽位。
- 未做并发、发布中断、accepted 指针恢复测试；这些属于 S07。
