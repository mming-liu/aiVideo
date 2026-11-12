# S03：画面执行计划（`step02a_frame_gen/execution_plan.py`）

> 离线可完成项：计划构建、真实入口装载、发送前身份重验、自动反例与失败隔离。
> 未发起图片生成；实际像素级内容仍标 `unverified`。

## 1. 真实入口与数据

| 项 | 位置 |
|---|---|
| 计划构建/重验 | `step02a_frame_gen/execution_plan.py::build_visual_plan`、`validate_plan_identity` |
| plan-only 入口 | `step02a_frame_gen/main.py::_plan_only_report`（`--plan-only`，`--plan-out` 显式落盘） |
| 真实生成入口 | `step02a_frame_gen/main.py::main` 在 `build_working_dir`/生成前构建并重验，写 `vimax_working/visual_plan.json`；`gen_all_frames(visual_plan=..., scope=...)` 发送前再次重验 |
| 绑定调用点 | `build_visual_plan` 内 `build_execution_binding` → `validate_execution_binding` → `attach_semantic_checks` |
| 生成后观测 | `verify_frame_observations` / `apply_frame_observations`（消费显式观测，不假装视觉模型） |

`visual_plan.json` 逐帧含：`frame_id`、`moment`（start/end/key_moment）、
`responsibility`、最终 `prompt`、`model`、`content_hash`、参考图 `role/purpose/sha256/
prepared`、`visible_characters`、`visible_props`、`offscreen_speakers`、`status`、
`unresolved`。`shots[*]` 另含必要道具、画外声音与每镜 `issues`。

## 2. 生成前检查

- 声明矛盾：`shared.prompt_contradiction.find_contradictions`；
- 引用缺失/未准备：参考 `prepared=false` 或文件不可读 → `reference_unprepared`；
- 参考能力：`active_image_ref_limit()` 与参考数量；
- 首尾状态：`ff_desc`/`lf_desc` 缺失、主主体不在可见布局 → `frame_description_missing` /
  `required_character_missing`；
- 语义：`shared.execution_semantics` 三族检查 + 绑定 `unresolved` 门禁；
- 精确文字卡：计划只记录 `unverified`，正式核验仍走覆盖层路径（未在本包伪造通过）。

## 3. 自动反例（`tests/step02a_frame_gen/test_execution_plan.py`）

| 反例 | 自动检出 |
|---|---|
| 画外声音错误出镜 | `offscreen_speaker_visible`；计划 `blocked`，绑定 `unresolved` + `route=step01` |
| 听者反应缺失 | `listener_reaction_missing` |
| 必要人物缺失 | `required_character_missing`（主主体不在可见布局） |
| 参考身份不符 | 观测 `reference_sha256` 与计划不符 → `reference_identity_mismatch` |
| 单帧失败 | `attempts[frame_id].status=failed`，其他已验证帧仍 `verified` |
| 过期计划 | `source_digest_stale` / `visual_plan_blocked`，发送前拒收 |

## 4. 命令与读数

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest `
  tests/step02a_frame_gen/test_execution_plan.py -q `
  --basetemp=.pytest_tmp/s03_module -p no:cacheprovider
```

读数：**8 passed**（本模块新增文件；不含仓库原有回归）。

模块级回归（仅作旁证）：`tests/step02a_frame_gen` = 91 passed / 4 skipped /
4 failed；4 failed 全部是本机 WorkBuddy safe-delete shim 在清理临时文件时
`EPERM`（与本包逻辑无关，见「未验证项」）。

## 5. 未验证项

- 未真实生成任何图片；`unverified` 明确记录“实际人物/道具/构图需视觉 QC 或人工”。
- 未验证真实视觉模型对计划的遵从度；参考图身份只核到文件摘要/角色，不核像素相似。
- 精确文字卡覆盖层路径未在本包执行。
