# Step01 约束删减候选（精简包 S08，仅历史数据）

状态：**裁决材料，未删除任何约束**。删除属于 GATE-C，需要用户逐类裁决。
本版只用了已有的历史产物；S07（带新埋点的 step01 实跑）未执行，所以
`observed_ran` 全部为「未知」。

## 输入

- 冻结清单：`tools/step01_fire_stats.py freeze --root output`，238 份产物
  （日志 205、metrics 11、quality 13、audit_rounds 5、repair_rounds 4），统计结果
  `.tmp/slim/fire_stats_hist.json`（本地，不入库）。
- 全部来自同一部测试小说（chapter_001/002 及其隔离副本），时间 09-23 至 11-21，
  跨越多个规则版本。
- 含 `[CONSTRAINT]` 行的日志 48 份，**48 份都可能被截断**：修复汇总只列前 5 个约束，
  合计数大于所列之和。没有任何一份 metrics 带逐约束计数（埋点是本包 S06 才加的）。

## 读数

- 88 个约束里，62 个在日志中出现过，26 个从未出现。
- 从未出现 ≠ 没有运行、也 ≠ 没有触发：
  - 日志里的 **warn** 按约束分组、每类必出一条样例，不受前 5 截断影响。所以**只会产出 warn**
    的约束，「从未出现」是可靠读数（对这部小说而言）。
  - 会产出修复记录（rewrite / set_field 等）的约束，低频时可能被前 5 截断吞掉。它们的
    「从未出现」**不可靠**，要等 S07 的 `metrics.constraints` 才能定。
- 修复轮（49 个决策）：接受 39、解决 85 条；拒绝原因 `new_blocking` 9、
  `no_target_resolved` 6、`evidence_removed` 2、`layout_structure_invalid` 1。
  数据量不足以支撑删除任何修复轮分支，本包不动修复轮。

## 分类与建议

### (a) 内容特化：来自测试小说的「荷塘小舟」场景

AGENTS「内容无关性」禁止把测试小说的场景写进生产规则。船相关规则分散在多处，
不只是一个约束：

| 位置 | 内容 | 本次读数 |
|---|---|---|
| `constraints/boat_crop.py`（`boat_crop_strip`） | 船体裁切短语只报不改 | 只出 warn，从未出现（可靠） |
| `constraints/boat_width.py` | 「窄身…舟」补「宽X米」 | 出现过 |
| `constraints/v6_43_prompt_enforcement.py` 规则 1 | 「小舟/木舟」→「一叶无篷舴艋舟（宽1.4米，可容3人）」 | 约束整体出现过（含其他规则） |
| `shared/vimax_bridge.py` 角色锚定 | 注入「完整小舟在画中、船头船尾齐全」 | Step02a 生效，不在本统计内 |
| `shared/style_prefix.py` `_NEGATED_OBJECT_REWRITES` | 「无篷舴艋舟」→「敞口平甲板木质舴艋小舟」 | 不在本统计内 |
| `validation.py` 船上人物位置检查、`_SCENE_OBJ_HINT_WORDS` | 小舟/舴艋舟等词表 | 不在本统计内 |
| `prompt_semantic/registry.py` `boat` | 舴艋舟/小舟别名 | 不在本统计内 |

其中「宽1.4米，可容3人」「舴艋舟」是这部小说的具体设定，属于硬编码的作品内容。
「画面里有船时船体完整」「物体要有尺寸锚」本身是通用需求，但应由
`scene_objects`（step01 从原文提取的物体尺寸）这类输入驱动，而不是写死船。

**建议**：作为独立工作包处理（改为由 `scene_objects` 驱动的通用「空间物体完整性/尺寸」
机制，再删掉船专用代码），不放进本包的删除批次。原因：牵涉 Step02a 提示和
跨章节效果，需要换一部不含船的小说做对照验收。`boat_crop_strip` 单独删除风险最低
（只出 warn、从未出现），可以先删。

### (b) 已禁用

| 约束 | 说明 | 建议 |
|---|---|---|
| `self_review`（`constraints/self_review.py`，196 行） | `is_enabled()` 恒为假：解析后再调一次 LLM 自审每镜。v6.98 起约束层不再调 LLM | 删除约束与 2 个专属测试 |

### (c) 重复实现：**撤回**

计划里列的三组疑似重复，核对后都不成立：

- `framing_variety`（全片景别种类 < 5）、`framing_diversity_v2`（连续 3 镜同景别）、
  `camera_variety`（全片运镜 < 3 种）是三条不同的规则。
- `gaze_typo` 的错字表已经从 `shared.vimax_bridge._LF_GAZE_TYPO` 单源导入。
- `lf_midstate` 是改写约束，`style_prefix.detect_lf_midstate` 是检测函数，职责不同。

### (d) 从未出现，读数不可靠：保留，等 S07

以下 24 个约束会产出修复记录，「从未出现」可能只是被前 5 截断。建议全部保留，
S07 跑完后按 `metrics.constraints` 的 `ran>0 且 repair=warn=violation=0` 重新筛。

`camera_variety`、`char_layout_sync`、`climax_distribution`、`combat_impact`、
`core_keyword_coverage`、`core_keyword_pollution_cleanup`、`ff_lf_temporal`、
`ff_motion_overlap`、`framing_diversity_v2`、`framing_variety`、`gaze_direction`、
`gaze_typo`、`identity_unification`、`kf_start_time_normalize`、`kf_weak_template`、
`lf_midstate`、`offscreen_action_consistency`、`overshoulder_consistency`、
`platform_compliance`、`post_combat_blank`、`prompt_repetition`、
`prop_prompt_consistency`、`split_content_similarity`，以及只出 warn 但属开篇节奏规则的
`opening_empty_shot_cap`（只出 warn、从未出现，读数可靠；但它是通用的开篇节奏规则，
不是内容特化，删除属于产品取舍，默认保留）。

注意 `gaze_direction`：约束从未出现，但校验器的同名问题码在历史产物中出现 28 次。
可能是约束触发了但被截断，也可能是约束改不掉校验器报的问题。S07 的计数能区分。

## 规模预期

即使 (a)(b) 全删、(d) 里再删一批，可删代码量也只有几千行，占 step01（8.3 万行）
的个位数百分比。step01 的体量主要在 `validation.py`（7.3k）、`outline.py`（6.4k）、
`plan_feasibility.py`（5.4k）和 orchestrator，约束层不是大头。

## 待裁决

1. `boat_crop_strip`：删除？（推荐：删）
2. `self_review`：删除？（推荐：删）
3. 船相关规则整体改为通用机制：是否另开工作包？（推荐：另开，需第二部小说做对照）
4. S07：是否执行带埋点的 step01 实跑（chapter_001、002 各 1 次，单次上限 40 调用，
   OpenCode Go 订阅通道），用于重筛 (d)？由我执行需要你批准；或交给 dsh 按其已有授权执行。
