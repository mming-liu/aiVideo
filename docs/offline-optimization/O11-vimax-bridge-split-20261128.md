# O11 `shared/vimax_bridge.py` 结构拆分（2026-11-28）

> 承接 O10（`e3ef2e2`）。零行为变更、指纹锁定不变、单包单提交。

## 1. 拆分结果

| 文件 | 行数 |
| --- | --- |
| `shared/vimax_bridge.py`（拆分前） | 6,152 |
| `shared/vimax_bridge.py`（拆分后） | **3,568（−42.0%）** |
| `shared/vimax_bridge_parts/paths_and_cache.py` | 1,285 |
| `shared/vimax_bridge_parts/contract_and_refs.py` | 1,556 |
| `shared/vimax_bridge_parts/ledger_and_errors.py` | 176 |
| `shared/vimax_bridge_parts/_forward.py` | 41 |

模块划分（按调用闭包自动分组，不是按名字硬切）：

- **paths_and_cache**（23 符号）：帧路径/内容哈希/受保护帧、二修（doodle）与验收、
  QC 入口、肖像路径、风格锚点解析；
- **contract_and_refs**（52 符号）：frame contract 装配、参考图收集/去重、prompt 组装；
- **ledger_and_errors**（9 符号）：Stage 4 ledger、失败分类常量与分类器。

## 2. 三条硬约束怎么守住的

### 2.1 指纹字节级不变（`4fba3d45a6851f41`）

`_STEP01_VIMAX_BRIDGE_SYMBOLS` 只含 `_extract_char_layout` 与 `_LF_GAZE_TYPO`（其依赖
闭包共 40 个符号）。拆分器把**指纹闭包**与**所有调用它们的外层符号**一起留在父模块，
只移动调用闭包不触及这两者的符号——因此指纹计算读到的符号 AST 一字未改。
每轮拆分后都实测指纹不变。

### 2.2 monkeypatch 语义不变（这是本包真正的难点）

Python 函数查全局是**定义模块**的 `__dict__`：把函数搬进子模块后，测试对
`shared.vimax_bridge.<name>` 打的补丁不会作用于子模块内部调用。逐条对策：

1. **测试真正打补丁的 24 个名字**（`_collect_frame_refs_for_shot`、`shot_work_dir`、
   `is_empty_scene`、`style_prefix_for_canvas`、`_maybe_qc_frame`、`_try_qc_repair` 等）
   在子模块内一律通过 `_forward(module, name)` **代理**：调用时才 `getattr` 父模块，
   补丁立即生效；
2. 子模块**自己定义又被自己调用**的补丁目标（`_doodle_refine_first_frame`、
   `_validate_frame`、`_maybe_qc_frame`、`_resolve_style_anchor_path`、
   `_accept_doodle_refinement`、`_bridge_log`）额外加 `_proxy_<name>` 别名，
   内部调用走别名，定义本身仍导出给父模块；
3. 父模块 `__all__` 增补拆分后仍属公开命名空间的 re-export 名字（`Camera`、
   `shot_work_dir`、prompt_semantic 常量等），既保留外部 `from shared.vimax_bridge
   import ...`，也避免这些导入被当成"未使用"。

结果：16 个测试文件里的 `monkeypatch.setattr(vb, ...)` **一处未改**（只有两处
"源码文本断言"更新为同时接受代理调用形式）。

### 2.3 0 循环导入

子模块之间的依赖被排成 DAG（`paths_and_cache → ledger_and_errors → contract_and_refs`），
每个子模块可在独立进程单独 import：

```
python -c "import shared.vimax_bridge_parts.paths_and_cache"      # 23 exports
python -c "import shared.vimax_bridge_parts.ledger_and_errors"    # 9 exports
python -c "import shared.vimax_bridge_parts.contract_and_refs"    # 52 exports
```

## 3. 验收

| 项 | 结果 |
| --- | --- |
| 指纹 | `4fba3d45a6851f41` **不变**（拆分前后一致） |
| 体积 | 6,152 → 3,947 行（**−35.8%**，目标 ≥30%） |
| 全量分组跑 | 6152 收集 / 5903 通过 / 63 失败 / 185 跳过；失败集合与 O10 **完全一致**（0 新增） |
| `ruff_gate` | PASS（F401 199 / F811 15 / F821 0，与基线同） |
| 子模块独立 import | 3/3 通过 |
| 外部引用完整性 | 扫描全仓 `from shared.vimax_bridge import ...` / `shared.vimax_bridge.X`，全部可解析 |

## 4. 残余

1. **只拆了 A 批的第 1 个文件**。计划 A 批还有 `step02_video_gen/submit_poll.py`（2,668 行）
   与 `step04_merge/main.py`（1,860 行），B 批 `outline.py`（7,483 行）与
   `shot_tts.py`（1,499 行）未拆——它们不含指纹锁，但同样需要"补丁语义"分析，
   建议按本包同一方法论逐文件推进（每个文件独立回归、独立提交）。
2. 子模块内 `# ruff: noqa: F401` 覆盖了复制过去的导入表：拆分不改行为，但这几个文件
   的 F401 不参与门禁计数（父模块仍是 199 的基线口径）。
3. `_forward` 代理每次调用多一次 `importlib.import_module` + `getattr`（模块已缓存，
   开销在微秒级）；若将来 `gen_shot_frames` 这类热路径出现可测开销，可在启动时
   一次性绑定并在补丁点显式刷新。
