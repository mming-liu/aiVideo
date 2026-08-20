# TODO 待办清单

> 记录**尚未完成**的修复、待验证项、待决策事项。
> 已解决的问题与历史存档见 **[problems.md](problems.md)**（问题记录文档，含现象/根因/处理/教训）。
> 最近更新：2026-08-18

---

## 🔴 当前待办（按优先级）

### 1. hook 首帧渲染验收（⏳ Plan A 最后一步）
- 渲一次 hook 首帧（step02a `--shots 0`）→ 人工验收（不再反复手改）。
- **注意**：当前 hook 首帧/尾帧已渲染（08-14 22:50-22:53），但**图片模型不忠实渲染锚点**（见 #2）——
  图是「刺客出水举蓝光匕首」，prompt 是「潜伏水下刀尖微露」。验收前需先解决 #2。
- **08-15 进展**：qwen-image-3.0-pro 直测已验收（钩子「水下黑影+刀尖微露」忠实渲染、船体比例修复）；
  模型全局切换 + 色调锚（color_tone 注入）+ 空间充裕锚规则已落地，可重渲 step02a `--shots 0` 验收。

### 2. 图片模型不忠实渲染 ff_desc 锚点 🔴
- **问题**（详见 problems.md P3）：最终 prompt 带全部锚点（含「刺客尚未出水/刀尖微露」），
  wan2.7-image 却画成刺客出水举刀 + 蓝光匕首幻觉；尾帧「已没入水中」画成「站水面」。
- **08-15 已修复**：
  - [x] 图片模型切换 `qwen-image-3.0-pro`（全局，`.env.content` BAILIAN_T2I_MODEL + BAILIAN_IMAGE_RPM=1 预限速，403 回退 wan2.7-image → wan2.7-image-pro）——08-15 钩子直测验收通过
  - [x] 色调锚：`shared/vimax_bridge.py` 新增 `_inject_tone_anchor`，首帧/尾帧/关键帧/QC 核对 prompt 统一注入 `color_tone`
  - [x] 空间充裕锚：`step01_script_gen/prompts.py` HOOK_PROMPT_TEMPLATE 增「物体上有人物须写容纳性（船身宽大/空间充裕）」+「ff_desc 色调重述」规则
- **待办方向**（用户定夺）：
  - [ ] 简化 hook 首帧构图降载（纯水下黑影不要「刀尖微露」/删一件道具）——已验证 qwen-3.0-pro 可忠实渲染，暂不需要
  - [ ] 修 QC（见 #3）让「画不对」当场拦住

### 3. 图片 QC 的 prompt_semantics 失效 🔴
- **问题**（详见 problems.md P4）：图明显不符但 sidecar 全 clean。
- **08-15 已修复**：
  - [x] `shared/image_qc.py` `qc_image` 空串/纯空白也触发 `build_qc_prompt` 兜底（`not (prompt and prompt.strip())`）
  - [x] 关键锚点（服装色/人物数量/威胁源状态）不符 → warning；`QC_SEMANTICS_CRITICAL=1` 可升级 critical 删帧重生成

### 4. 关键帧数量 2→1 🟡
- script 有 keyframes×2（0.4/0.8），`images/` 只产出 1 张 `shot_0_kf.png`。
- **08-15 已修复**：根因=provider `_download` 所有关键帧共用 `kf` 前缀同名覆盖；
  bridge 传 `frame_desc="关键帧{i}"` + 两 provider 解析序号 → `shot_N_kf_0.png`/`shot_N_kf_1.png`。

### 5. 主角后镜省略服装色（⏳ 收敛中）
- 规则 13.7 提升后 24→10；剩 10 条（镜头 14-23）全为**杨旭**后镜省略服装色。
- 渲染期 `_inject_character_costume` 已兜底。
- **08-15**：13.7 规则已补「主角/已出场角色后镜也必须重述（禁止『前镜已述』省略）」；
  待下一轮 step01 regen 验证收敛。

### 6. prompts.py 优化（代码审计发现，08-18）
**文件**：`step01_script_gen/prompts.py`

- [x] **🔴 死 import 清理**：第 20 行 `import style_prefix` 和第 1027 行 `import style_prefix` 均无调用
  （全文无 `style_prefix.`），纯死代码。删掉两处。
- [x] **🔴 PROMPT_TEMPLATE 末尾重复注入 `{scene_object_anchors}`**：第 41 行已有
  「场景物体尺寸锚定库」section，第 138 行 `{novel_text}{scene_object_anchors}` 又拼一次，
  LLM 看到两段相同锚定内容。删掉末尾重复注入。
- [ ] **🔴 skill 全量加载 token 浪费**：`_load_knowledge_skills()` 一次性加载 20 个 skill
  （battle-layered/face-shapes 等与本章无关），推高 token 消耗且稀释核心指令注意力。
  方案：按本章场景类型（打斗/对话/情感）做相关性筛选，或改为目录式索引（只列 skill 名+摘要）。
  **08-20 进展**：技能合并完成——已合并 7 组共 30 个技能 → 7 个（seedance-craft / absorption-log / camera-mastery / combat-mastery / portrait-mastery / transition-mastery / character-performance），加载数从 22→9。但仍是全量加载，未做相关性筛选。
  - 合并明细：seedance 13→1 + absorption 3→1 + camera 5→1 + combat 4→1 + portrait 3→1 + transition 3→1 + character 2→1
  - 当前 _load_knowledge_skills() 加载 9 个 skill，总 ~28k chars (~7,000 tokens)
  - 总删除：30 个 skill 目录（46 → 16 → 7 个 skill 类别）
- [x] **🟡 钩子缺少 `_duration_cap_guidance`**：主分镜 `build_prompt` 有模型时长上限引导，
  `build_hook_prompt` 没有。对低上限模型（如 Hailuo v1 6s）钩子可能生成超长镜头。
  对齐补上。
- [x] **🟡 合并 `_format_list` / `_format_dict` / `_format_movement_params`**：
  三个函数功能重叠，本质都是「格式化为 `- key: value` 列表」。抽一个多态 `_format_kv`。
- [x] **🟡 抽 `_get_skills_loader()`**：`_load_local_skill_context` 和 `_load_knowledge_skills`
  重复了 importlib 加载 `skills_loader.py` 的代码，DRY。
- [x] **🟢 第 226 行示例笔误**：「剑眉星目身穿白袍的杨旭白色长袍]杨旭坐在船头」
  中「白色长袍]」是残留错误文本，删掉。
- [ ] **🟢 规则编号重排**：3 / 3.5 / 3.6 / 4 混乱（3.5/3.6 是后插入的），长期维护堪忧。
  考虑重编号或用命名常量。

## 🟡 待决策

- **shot ff_desc ≤120 vs hook ≤160 数字**：是否统一（有意差异——hook 全景建场更宽）。
- **hook 与 shot 校验「两套」是否统一**（problems.md P10）：hook 挂载后重跑 `_run_prompt_checklist`。
- **反馈聚合模式 A vs C 效果对比**（08-18 A/B 实验，**08-19 结论：C 胜出，已改默认 C**）：
  - 实验设计：修复轮（`_repair_shot_warnings`）的 WARN 聚合方式
  - **A 模式（旧默认）**：按分镜聚合，每镜头看到自己的 WARN，不知全片同类总数
  - **C 模式（新默认）**：每条 WARN 末尾追加"（同类全片共 N 处）"+ prompt 开头加「全片错误分布」
  - 触发方式：`--mode A` / `--mode C`（CLI flag，**默认 C**）
  - 基线：step01.log 中 2026-08-18 23:21:33 之前 = A；之后 = C
  - 产物：`script_result_A.json`（A 模式结果，已保存）/ `script_result.json`（C 模式结果，现用）
  - **对比结论**（08-19 实测 chapter_001）：
    - 总 WARN：90 → 73，**-17（-18.9%）** ✓
    - top-6 被选镜头：WARN 35 → 24（-31%）；**非 top-6 的 21 个镜头也改善**（55 → 47，-15%），13 个镜头受益 = **全局视图迁移效应** ✓
    - 最大赢家：多人站位 13→0（-100%）、打斗打击 1→0、视线 11→6（-45.5%）、FF 时间线 4→1（-75%）
    - **代价 1**：lighting_position 3→12（+9，建议类 WARN，不是错误）
    - **代价 2**：服装色锚定 4→7（+3，C 模式改 lf_desc 时漏写张十三服装色）
    - **代价 3**：致命伤锚定 16→14（-2 镜）——C 模式 LLM 删掉了 shot 14/24/25 的"心口暗红洇痕"（违反规则 8）
    - **代价 4**：总时长 +10s（173→183s）
  - **防护措施**（08-19 已加）：
    1. 默认模式改为 C
    2. 修复轮返回后再跑一次 `hard_fix_shot_prompts`（幂等兜底）
    3. 追加**轻量致命伤兜底**：build_idx 之后的镜头，杨旭仰卧/倒下 + 无 anchor → prompt 补伤处锚（只补刺杀后的镜头，不误伤夜宴仰卧喝酒）
  - 评估维度：总 WARN 数变化（↓幅度）、关键类别（致命伤/锚点/衔接）收敛率、未被选中镜头是否受益（全局视图迁移效应）、新引入问题数量（是否越修越糟）

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
