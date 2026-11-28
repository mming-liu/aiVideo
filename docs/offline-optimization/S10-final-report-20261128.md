# S10 整包复核与两章只读刷新预演（2026-11-28）

> 对应计划 S10：汇总 S02–S09 交付、整包回归、指纹与正式产物保护对照，并给出
> 两章刷新候选。全程零真实模型调用、正式 `output/` 零写入。
> 基线：S01 `8531147` 的冻结证据（`.tmp/offline-closure-r1/S01/`）。

## 1. 交付总表

| 模块 | 交付 | 提交 |
|---|---|---|
| S02 | 只读 Bank 装载、锚点/肖像/旧注册表统一判据、预检与收集共用解析 | `a453c05`、`60ccab5`、`4dec60c` |
| S03 | 缺标记默认拒收（逐视图 provenance）、显式迁移候选工具 | `50965dd` |
| S04 | 7 个退出受控契约恢复 + 覆盖映射 | `66a2feb` |
| S05 | 四文件拆分施工单（依赖/打桩/插入点/验收） | `6ebd287` |
| S06 | `step04_merge/main.py` 拆分 | `33529f0` |
| S07 | `step02_video_gen/submit_poll.py` 拆分 | `a0d548c` |
| S08 | `step03_tts_audio/shot_tts.py` 拆分 | `f3ba3d2` |
| S09 | `step01_script_gen/outline.py` 拆分（orchestrator helpers） | `34bd4fe` |
| S10 | 本报告 + 两章只读预演 | 本次提交 |

分支 `master`，基线 `8531147`；本轮未推送远端。

## 2. 拆分结果（净行数按父模块计）

| 文件 | 拆分前 | 拆分后 | 净减 | 子模块 |
|---|---:|---:|---:|---|
| `step04_merge/main.py` | 1,891 | 1,216 | **35.7%** | `parts/track_collector.py`、`parts/timeline.py`、`parts/declarations.py` |
| `step02_video_gen/submit_poll.py` | 2,668 | 1,373 | **48.5%** | `parts/frozen.py`、`parts/qc.py`、`parts/recovery.py`、`parts/failures.py` |
| `step03_tts_audio/shot_tts.py` | 1,502 | 759 | **49.5%** | `parts/speakers.py`、`parts/units.py` |
| `step01_script_gen/outline.py` | 7,483 | 4,649 | **37.9%** | `orchestrator/outline_structure.py`、`outline_ops.py`、`outline_feedback.py` |

四个文件均 ≥30%；父模块保留配置/日志/运行状态与主编排，子模块导入期不回导父模块；
被测试替换的 callable/可变值在调用期经 `_pc`/`_pv` 解析，原签名与 re-export 不变。
`outline_feedback.py` 保留在 `orchestrator/`（提示指纹自动覆盖），不进入 `MODULE_ORDER`；
守卫只新增 `step01_script_gen/outline.py` 为允许导入者，其余模块仍被拒。

## 3. 整包回归（与 S01 冻结基线逐 nodeid 对照）

命令：

```
python tools/run_tests_grouped.py --out .tmp/offline-closure-r1/S10/junit
```

| 项 | S01 基线 | 本轮 | 差异 |
|---|---:|---:|---|
| 收集 | 6,152 | **6,259** | +107（S02/S03/S04 新增用例） |
| passed | 5,904 | **6,011** | +107 |
| failed | 63 | **63** | **0 新增、0 消失**（nodeid 集合逐项相同） |
| error | 0 | **0** | — |
| skipped | 185 | **185** | — |

- 63 项失败全部是 S01 冻结基线既有项（`test_image_qc_retry` 6、`test_plan_feasibility` 15、
  `test_reuse_miss_reason` 9、`test_duration_convergence` 7 等），无新增。
- `ruff_gate`：**PASS**（F401 189 / F811 12 / F821 0，低于基线 199/15）。
- 子模块独立 import：父模块与四个文件的全部子模块均可单独 import。
- 证据：`.tmp/offline-closure-r1/S10/junit/{merged.xml,summary.json}`。

## 4. 指纹

| 指纹 | 值 | 结论 |
|---|---|---|
| `RULE_SOURCE_FINGERPRINT` | `4fba3d45a6851f41` | **不变**（S02–S09 全程一致） |
| `PROMPT_SOURCE_FINGERPRINT` | `ac9f31ff5e807d84` → **`6ef3ebdb49d4e0ce`** | S09 把 outline 辅助逻辑迁入 `orchestrator/`，提示指纹按设计覆盖新源码；该字段为诊断项，不参与门禁/复用判定；未修改指纹计算器 |

## 5. 两章只读门禁复核与刷新候选

### 5.1 差异复核（`diff_step01_gate --require-safe`）

| 章节 | 旧指纹 → 新指纹 | 状态 | 新增 blocking | 警告变化 | `safe_for_offline_refresh` |
|---|---|---|---|---|---:|
| chapter_001 | `e5460e55f36d1d08` → `4fba3d45a6851f41` | PASS / PASS | 0 | 1 条 `wide_shot_facial` 文案（`星目`→`剑眉`） | true |
| chapter_002 | `e5460e55f36d1d08` → `4fba3d45a6851f41` | PASS / PASS | 0 | 无 | true |

证据：`.tmp/offline-closure-r1/S10/diff_chapter_001.json`、`diff_chapter_002.json`。

### 5.2 刷新预演（`production_context --refresh-gate --print-refresh`，只打印不落盘）

| 章节 | 候选 | quality | blocking | audit | commit | adoption |
|---|---|---|---|---:|---|---|
| chapter_001 | `.tmp/offline-closure-r1/S10/refresh_candidate_chapter_001.json` | PASS | false | ok | COMMITTED | adopted_and_verified |
| chapter_002 | `.tmp/offline-closure-r1/S10/refresh_candidate_chapter_002.json` | PASS | false | ok | COMMITTED | adopted_and_verified |

两份候选都记录 `reverified_from_fingerprint=e5460e55f36d1d08`、新指纹
`4fba3d45a6851f41`、`model_sends=0`，脚本/质量/审计/提交的 `script_revision` 与原稿一致。

### 5.3 正式产物保护对照

对 `output/锦衣夜行/` 重新逐文件 SHA-256（`.tmp/offline-closure-r1/S10/protection_after.json`）：

| 项 | S01 基线 | 预演后 |
|---|---:|---:|
| 文件数 | 656 | **656** |
| 字节数 | 401,740,834 | **401,740,834** |
| 缺失 | — | **0** |
| 新增 | — | **0** |
| SHA-256 变化 | — | **0** |

结论：两章预演与全包测试没有写正式 `output/`；刷新候选仅存在隔离目录。

## 6. 未完成/后续欠账（本包不销账）

1. **正式旧资产迁移**：`output/锦衣夜行/global` 的角色肖像与风格锚点仍是历史无标记资产；
   S03 起 Step02a 会按 `legacy_unstamped` 拒收。迁移工具 `tools/migrate_asset_style.py`
   已可用，但需要人工逐资产声明来源、操作者与候选摘要；本包未生成正式迁移候选，也未写源库。
2. **场景/道具隔离**：`SceneAssetsBank`/`PropAssetsBank` 尚未接入同一画风判据（计划后续包）。
3. **BGM 配置收口**、**O4 生产接入**、**跨章连续性**：按总计划仍欠；跨章包改
   `shared/script_preflight.py` 会改变规则指纹，须作为独立版本变更包裁决。
4. `tests_local/` 原文件保留未动；`chain5` 的活体基线/真实链/事务回放仍属显式外部数据回放，
   基础保护机制已由 S04 的自足子集覆盖。
5. S04 干净副本已验收 45/45；完整外部语料回放需要在具备冻结基线的环境另行运行。
6. 本轮提交未推送远端；推送与任何真实模型验证仍按授权另行办理。

## 7. 人工关口

1. **正式旧资产迁移**：审批逐资产声明、候选差异与源摘要后，才允许写正式资产库。
2. **O14 正式刷新**：审批明确章节与刷新候选后，才允许 `--refresh-gate` 落盘；
   本包只交付候选与差异，未执行正式刷新。

## 8. 证据索引

| 内容 | 路径 |
|---|---|
| S01 基线（保护清单/测试基线/未跟踪身份） | `.tmp/offline-closure-r1/S01/` |
| S04 覆盖映射与干净副本记录 | `docs/offline-optimization/S04-contract-coverage-20261128.md`、`.tmp/offline-closure-r1/S04/clean_copy.txt` |
| S05 施工单 | `docs/offline-optimization/S05-split-workorder-20261128.md` |
| 整包 JUnit | `.tmp/offline-closure-r1/S10/junit/` |
| 两章差异 | `.tmp/offline-closure-r1/S10/diff_chapter_00{1,2}.json` |
| 刷新候选 | `.tmp/offline-closure-r1/S10/refresh_candidate_chapter_00{1,2}.json` |
| 预演后保护清单 | `.tmp/offline-closure-r1/S10/protection_after.json` |
