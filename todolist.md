# TODO 待办清单

> 记录**尚未完成**的修复、待验证项、待决策事项。
> 已解决的问题与历史存档见 **[problems.md](problems.md)**（问题记录文档，含现象/根因/处理/教训）。
> 最近更新：2026-08-14

---

## 🔴 当前待办（按优先级）

### 1. hook 首帧渲染验收（⏳ Plan A 最后一步）
- 渲一次 hook 首帧（step02a `--shots 0`）→ 人工验收（不再反复手改）。
- **注意**：当前 hook 首帧/尾帧已渲染（08-14 22:50-22:53），但**图片模型不忠实渲染锚点**（见 #2）——
  图是「刺客出水举蓝光匕首」，prompt 是「潜伏水下刀尖微露」。验收前需先解决 #2。

### 2. 图片模型不忠实渲染 ff_desc 锚点 🔴
- **问题**（详见 problems.md P3）：最终 prompt 带全部锚点（含「刺客尚未出水/刀尖微露」），
  wan2.7-image 却画成刺客出水举刀 + 蓝光匕首幻觉；尾帧「已没入水中」画成「站水面」。
- **待办方向**（用户定夺）：
  - [ ] 简化 hook 首帧构图降载（纯水下黑影不要「刀尖微露」/删一件道具）
  - [ ] hook 帧换 `wan2.7-image-pro`（复杂指令遵循更强）
  - [ ] 修 QC（见 #3）让「画不对」当场拦住

### 3. 图片 QC 的 prompt_semantics 失效 🔴
- **问题**（详见 problems.md P4）：图明显不符但 sidecar 全 clean。
- **待办**：
  - [ ] 查 `_post_qc`/`maybe_qc_sync` prompt 传递链是否空串（`ff_desc_final` 未持久化时 `prompt=""` 非 None → 不触发 `build_qc_prompt` 兜底）
  - [ ] 关键锚点（服装色/威胁源状态/人物数量）不符 → 至少 warning、可配升级 critical

### 4. 关键帧数量 2→1 🟡
- script 有 keyframes×2（0.4/0.8），`images/` 只产出 1 张 `shot_0_kf.png`。
- **待办**：查 step02a 关键帧落盘/拷贝到 images/ 的逻辑。

### 5. 主角后镜省略服装色（⏳ 收敛中）
- 规则 13.7 提升后 24→10；剩 10 条（镜头 14-23）全为**杨旭**后镜省略服装色。
- 渲染期 `_inject_character_costume` 已兜底；待 regen feedback 收敛或规则补「主角后镜也必须重述」。

## 🟡 待决策

- **shot ff_desc ≤120 vs hook ≤160 数字**：是否统一（有意差异——hook 全景建场更宽）。
- **hook 与 shot 校验「两套」是否统一**（problems.md P10）：hook 挂载后重跑 `_run_prompt_checklist`。

## ⏳ 待人工核对

- **镜头 9 narration 原文未找到**（08-12 遗留）：原文锚点校验报台词未命中（「听香道：哎呀！…」），
  需对照小说原文确认 LLM 编造还是节选变形（非代码问题，须人工判断）。

---

## 归档说明

已完成项已从本清单移除，历史记录见：
- **[problems.md](problems.md)** — 问题记录（P1-P11：hook churn 教训 / LLM 输出丢失 / 模型不忠实渲染 / QC 失效 / kf 2→1 / 服装色收敛 / 校验误报 / qwen 遗留限制 / token 预估偏差 / 两套校验）
- 08-10 全流水线审计（A1-A7/B1-B10/C1-C5）已完成（commit b062c7b）
- 08-12 校验误报刷屏 P0/P1 修复 + prompt.py 精简（省 5.5k token）+ 第二轮校验反馈（v6.33）已完成
- 08-14 Plan A（hook 全锚点系统性修）代码已实施：schema/prompt/validation/A+B/方案1/Plan B/一致性
