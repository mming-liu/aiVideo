# step01 道具候选锚点：接线到提示词 + 首次诊断生成（2026-09-25）

用户决定（原文）：

> 同意做提示词验证，**不同意现在用候选锚点拒绝稿件**（拒绝候选就是生产门禁；章外仅 1 条正例、
> 0 条误提的抽样，还不足以支撑这个决定）。只让 `motion_away`、`marker_after` 两种候选进入
> 现有大纲提取，并带原文位置和"待核实"标记；生成及 GAP 重生若使用它们，须走**独立短提示**，
> 避免被 agent_plan 的 200 字大纲压缩吞掉。缓存键和回归测试同步覆盖。**暂不接 BEST-GUARD、
> 质量门，也不自动删 `props`。** 接线后先做一次诊断生成……

## 改了什么

| 文件 | 改动 |
|---|---|
| `step01_script_gen/prop_state_anchor.py` | 新增 `outline_prop_anchors()`（只取 `motion_away`/`marker_after`，带 `char_start/char_end` 与 `status="待核实"`）与 `render_prop_anchor_block()`（独立短提示块，块内写明"待核实、不构成硬性要求、拿不准不要删 props"） |
| `step01_script_gen/outline.py` | `generate_outline()` 内确定性产出候选锚点 → 挂到 `outline["prop_state_candidates"]`；**进 `cache_source`**（提取规则变化 → 缓存失效）；命中旧缓存时补 `setdefault` 该字段 |
| `step01_script_gen/prompts.py` | `build_prompt` / `build_doubao_prompt` 各加 `prop_anchor_block=""`，在**末尾**追加（**在** 200 字压缩**之后**）；空块不改变既有 prompt |
| `step01_script_gen/main.py` | 三个构建点传入该块：首次生成、GAP 重生、结构收敛轮（`_regen_structural_over_limit` 加 `prop_anchor_block` 形参） |
| `tools/diag_prop_anchor_generation.py` | 新增诊断工具：只写 `--out` 与 `<out>.diag.json`，拒绝正式产物名；大纲缓存默认写到 `--out` 同目录（不碰章节目录的正式缓存）；报告含"块是否进了 prompt / 释放写在哪一镜 / 释放之后各帧 props 是否仍持有" |
| 测试 | `test_prop_state_anchor.py` +6（形状过滤/位置/待核实/压缩不被吞/两条剖面注入/**缓存键覆盖锚点**）；`test_outline_budget_and_repair_prompt.py` 与 `test_outline_planning.py` 各按新契约更新 |

## 为什么（关键证据）

实测 `prompts._compact_doubao_outline_anchor` 对**真实**大纲的压缩比：

```
真实大纲锚点 1784 字 → doubao 压缩后仅 39 字（只保【高潮事件】一行）
独立块 286 字（3 条候选：酒囊 / 钓杆 / 灯盏）→ 完整进入最终 prompt（32477 字）
```

→ 并进 `outline_anchor` 必被吞；独立块是唯一可行路径。

## 首次诊断生成结果（run，2 次调用：1 大纲 + 1 生成）

- `generation_success=True`，`block_in_prompt=True`，prompt 34,344 字，稿 16 镜。
- **钓杆（目标事实）**：写出 1 处 —— `shot4 ff_desc: 杨旭仰卧船头，钓杆已脱手垂水，酒壶已脱手…`
  但：① **镜序可疑**——攻击在 **shot5**（神秘人持刃），释放却写在 shot4，与原文"中刃之后才松手"相反；
  ② **同镜自相矛盾**——`shot4 kf0(pre) desc` 仍写「左手钓杆垂水」；
  ③ **布局未跟随**——shot4 的 `ff/lf/kf0/kf1` 与之后各镜共 **14 处**布局仍列 `['钓杆','酒壶']`。
- **灯盏**：写出 2 处（`shot9 kf1/lf` 丢下灯盏）；工具报的"之后仍持有 2 处"其实是**同镜更早的
  ff/kf0**（按帧序在释放之前）→ 属合法，**是我的工具按"镜头"而非"帧"过滤导致的多报**，需修正。
- **酒囊**：0 处（未采纳该候选）。

**结论**：提示词接线**生效**（块确实进 prompt，模型也写出了释放措辞），但
**一致性未达成**（描述写了释放、布局仍持有），且出现**提前/错位**（释放早于攻击镜；同镜 kf0 与 ff 矛盾）。
因此**不能**升级为选稿依据；下一步要讨论的是帧级一致性与提示词措辞（现文案可能诱导"提前写出结果"）。

## 影响面

- 提示词多一段 ≤ 6 行的独立块（真实章节 286 字）；大纲缓存键变化 → **下一次运行会重新生成大纲**
  （1 次额外调用，属预期）。
- **不接** BEST-GUARD / 质量门；**不自动删 `props`**；不写任何正式产物。
- 已知风险：模型可能像本次一样"只改描述不改布局"，从而**新引入** `prop_possession_consistency`
  （描述称已脱手、布局仍持有）——该 code 已有确定性检测与成对授权，属**可修**范畴，但这意味着
  提示词接线本身**不会**自动减少缺陷，只是把"释放缺失"换成了"释放与布局不一致"。

## 验证

- `pytest -q`：**2254 passed, 1 skipped**（本轮起点 2248）。
- 诊断产物（均非正式产物）：`snapshots/diag_propgen_20260925_2050.json` 与 `.diag.json`。
- 首次运行因 `tools/diag_prop_anchor_generation.py` 从 `shared.chapter_reader` 导入 `read_chapter`
  失败（该函数在 `main` 里）——**导入期即失败、未发起任何模型调用**，修正后重跑。

## 回退方式

删除 `prop_state_candidates` 的产出/注入（`outline.py` 两处、`prompts.py` 两处、`main.py` 四处）、
删除诊断工具与新增测试即可；不含任何现有行为的改写。
