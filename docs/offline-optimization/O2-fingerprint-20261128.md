# O2 规则指纹边界治理与确定性差异复核（2026-11-28）

> 承接 O1（`60ee425`）。本包是计划里**唯一允许**改变 `RULE_SOURCE_FINGERPRINT` 的工作包；
> 本包之后 O3–O13 必须字节级不变。零真实模型调用、正式 `output/` 零写入。

## 1. 完成判据与结论

| 判据 | 结果 |
| --- | --- |
| 整文件指纹改 AST 归一化（注释/空行/CRLF 免疫） | ✅ 实测（见 §3） |
| 非阻断展示元数据符号移出清单 | ✅ `inject_global_config_into_script` 已移出；剩余 8 符号闭包不再触及渲染配置簇 |
| 新增只读差异复核工具 | ✅ `tools/diff_step01_gate.py`（语义 blocking 对比 + `safe_for_offline_refresh`） |
| 两章只读复核 `safe_for_offline_refresh: true` | ✅ chapter_001 / chapter_002 均 SAFE |
| 指纹卫生单测（注释/空行/CRLF 不变、判据变则变） | ✅ 12 个用例 |
| 全量离线测试 0 新增失败 | ✅ 8380 项 / 7993 passed / 74 failed（与 O1 基线同一集合）/ 313 skipped |

**新指纹（锁定为 O3–O13 基线）：`4fba3d45a6851f41`**
（O0 基线 `6f612fa3d96482bb` → O1 `3cb3049c25224ad3` → O2 `4fba3d45a6851f41`）

## 2. 改动清单

| 文件 | 变更 |
| --- | --- |
| `step01_script_gen/quality_issues.py` | 新增 `_module_ast_digest()`（`ast.dump(include_attributes=False)` + `sha256`，`utf-8-sig` 容忍 BOM）；`_compute_rule_source_fingerprint()` 的整文件条目改为「相对路径 + AST 摘要」；`_STEP01_STYLE_PREFIX_SYMBOLS` 移出 `inject_global_config_into_script` 并写明取证 |
| `step01_script_gen/validation.py` | `_check_wide_shot_facial` 取词改为 `min(...)`：原先 `next(... for w in set)` 的文案取决于进程 hash seed |
| `step01_script_gen/frame_presence.py` | `names` 排序改为 `(-len(name), name)`：原先同长度名字之间顺序随机（同一稿两次重算报不同角色） |
| `tools/production_context.py` | 抽出 `_stage_chapter()` / `recompute_step01_quality()`（`build_refreshed_gate` 复用，行为不变）；`verify(..., allow_rule_source_drift=False)` 允许差异复核跳过「规则源指纹必须等于当前源码」一步，仅核提交清单 |
| `tools/diff_step01_gate.py` | 新增：只读重算 + 语义级差异报告 |
| `tests/step01_script_gen/test_rule_fingerprint_hygiene.py` | 新增 12 个用例 |
| `.gitignore` | 放行新用例文件 |

## 3. 指纹归一化实测（可复现）

同一工作树、逐项改动后立即重算（脚本见 `.tmp/`；每次改完都按字节还原并复核）：

| 实验 | 指纹 |
| --- | --- |
| 基线 | `4fba3d45a6851f41` |
| `style_prefix.py` 末尾追加注释 | 不变 |
| 改 `_GLOBAL_RENDER_DEFAULTS` 的展示默认值（`推镜速度约 0.3→0.4`） | 不变（解耦生效） |
| `timecode.py` 整文件转 CRLF | 不变 |
| `timecode.py` 追加注释/空行 | 不变 |
| `timecode.py` 新增一个模块级常量（真实 AST 变化） | `c281ece3d4949df2`（已变） |
| 还原全部文件 | 回到 `4fba3d45a6851f41` |

**为什么移出 `inject_global_config_into_script` 是对的（取证）**：它只写 script 顶层的
`resolution/ratio/fps/render_style/global_lighting/negative_prompt`；`validation.py:351-353`
仅在字段**缺失**时打 WARN（非阻断），`main.py:1275` 只打印，`validate_fps_consistency`
也只 `print` WARN。没有任何约束或质量判据读这些值，因此它不属于「什么算 blocking」。
逐根符号闭包核对：9 个符号里只有它会拉到 `get_global_render_config` /
`_GLOBAL_RENDER_DEFAULTS` / `frame_canvas_ratio`，移出后其余 8 个符号的闭包干净。

## 4. 本包顺带修掉的两个**真实确定性缺陷**

差异复核工具第一次跑就暴露了它们——同一份稿子两次重算，报告文案会变：

1. `validation.py::_check_wide_shot_facial`：`next((w for w in _FINE_FACE_WORDS if w in probe))`
   且 `_FINE_FACE_WORDS` 是 `set` → 命中词随进程 hash seed 变化（实测 chapter_001 第 3 镜
   在「星目」与「剑眉」之间跳）。改为 `min(...)` 后确定。
2. `frame_presence.py::presence_assessment`：`sorted({...}, key=len, reverse=True)` 在
   **同长度**名字之间顺序随机（实测 chapter_001 第 1 镜在「听香」与「群演」之间跳）。
   改为 `(-len(name), name)` 后确定。

验证：`--refresh-gate --print-refresh` 连跑两次（两个独立进程），
`step01_quality.json` **字节一致**，`step01_audit.json` 除 `reverified_at` 外一致。

## 5. 两章只读差异复核（`tools/diff_step01_gate.py`）

| 章 | 指纹 | 质量门 | 新增 blocking | 移除 blocking | 新增/移除告警 | 文案变化 | safe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| chapter_001 | `e5460e55f36d1d08` → `4fba3d45a6851f41` | PASS → PASS | 0 | 0 | 0 / 0 | 1（上条缺陷 1 的既有文案） | **true** |
| chapter_002 | 同上 | PASS → PASS | 0 | 0 | 0 / 0 | 0 | **true** |

`blocking_issues_added` 按**语义身份**（码 + 严重度 + 定位）判定：检查器只改措辞不算
新增 blocking，文案变化单列在 `blocking_messages_changed` / `warning_messages_changed`。
`safe_for_offline_refresh` = 无新增语义 blocking ∧ `status_new == "PASS"` ∧ 原审计 `ok`。

## 6. 测试与静态门禁

| 项 | O1 基线 | O2 后 |
| --- | --- | --- |
| 全量分组测试 | 8368 / 7981 passed / 74 failed / 313 skipped | 8380 / 7993 passed / 74 failed / 313 skipped |
| 新增失败 | — | **0**（逐用例比对 `.tmp/o1_junit` vs `.tmp/o2_junit`） |
| `tools/ruff_gate.py` | PASS（F401 199 / F811 15） | PASS（同读数） |
| `tools/scan_import_swallow.py` | — | exit 0（`total=9 A=9 B=0`） |
| 新增单测 | — | 12 个用例全过 |

## 7. 残余与已知限制

1. **导入成本**：`RULE_SOURCE_FINGERPRINT` 在 import 期解析 122 个文件（1.85 MB）并
   `ast.dump`，实测约 +0.35–0.55s/进程（`tokenize` 方案约 0.20s、`compile+marshal` 约 0.41s，
   都在同一台机器同一时刻实测）。按计划要求保留 AST 口径；分组测试 step01 组因此
   +33s（220s vs 187s），未做缓存以避免 import 期写盘。
2. **`ast.dump` 的 Python 版本敏感**：跨小版本升级（如 3.10→3.14）可能改变 AST 表示，
   从而改变指纹。当前运行环境固定 3.14；升级解释器时须按「O2 级变更」重算并重签。
3. **docstring 计入指纹**（保守口径）：改模块/函数文档字符串也会让指纹失效，已用用例锁死说明。
4. 本包只审计了差异复核**实际走到**的那两个 set 顺序点；其它检查器的文案顺序未做全量审计。
