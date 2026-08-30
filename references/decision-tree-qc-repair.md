# QC 修复决策树（v6.x.x，2026-08-30）

> `build_repair_plan` 决策速查：哪些 QC critical 走 i2i 红框修图（edit），哪些走重新抽卡（regen）。
>
> 维护人：本会话（08-30 末次汇总）
> 关联：`shared/image_qc.py:999` `build_repair_plan` + `shared/vimax_bridge.py` 重试循环
> **v6.55 增补**：D 方案 reliability 预分级——low 类断言（比例/语义/程度副词）首错只 warning，连错升级 critical 才进本决策树。详见 `references/qc-reliability-calibration.md`。

## 决策树

```
critical in shot?
├── 否 → 保留当前帧（QC 通过）
└── 是 → build_repair_plan:
    ├── 10 模式都不匹配?       → regen（"非局部失败"）
    ├── 无 bbox?               → regen（"无可选目标 bbox"）
    ├── extra_person 主体不明?  → regen（smaller_person 主体护栏保护）
    ├── 修复区域 > 模式阈值?    → regen（"修复区域占画幅 N% > 阈值%"）
    └── 全过 → edit（i2i + 复检）
        ├── 复检过        → 保留修复版
        └── 复检仍 critical → regen
            └── 末次 → 保留 critical 图（attempts_exhausted）
```

## 走「修图」（edit）的 10 个模式

| 模式 | 触发关键词 | 选区 | 阈值 | 修图指令摘要 |
|---|---|---|---|---|
| `knife_contact` | 刀具/接触/切入/已刺入 | `knife_zone`（持刀者 ∩ 躺卧者 + 20% 边距） | **70%** | 让刀具悬停空中，不接触任何人 |
| `red_mark` | 红色痕迹/血/洇 | `all_persons` | 95% | 去除红色痕迹 |
| `glowing` | 发光/光刃/光晕 | `all_persons` | 95% | 移除发光效果 |
| `eyes_closed` | 双目紧闭/睁眼 | `lying_face_top45` | 95% | 让仰卧者闭眼 |
| `canopy` | 篷 | `boat_upper_half` | 95% | 移除船篷 |
| `costume_color` | 服装错/白袍/红衣 | `all_persons` | 95% | 改服装颜色款式 |
| `holding_wrong` | 应持/未持/手中错 | `holder_bbox` | 95% | 改手中物品（无则空手）|
| `multi_finger` | 多指/少指/畸指 | `all_persons` | 95% | 修正手部为五指 |
| `face_expression` | 表情错/惊愕/痛苦 | `person_face_top45` | 95% | 改表情 |
| `extra_person` | 人数 vs 名单/角色被复制 | `smaller_person` + **主体护栏** | 95% | 减少 1 个人物（保留主体）|

**注意**：
- 同一 critical 可**同时命中多个模式**（指令 dedup 合并）
- `knife_contact` 阈值 70% 是接触带真小区域；其他模式 95% 是 multi-person 场景 union 天然 80%+
- 多模式同时命中取**最宽松**阈值（max）——knife 严选 70 + 其他宽选 95 → 按宽的来

## 走「重新抽卡」（regen）的 7 类情况

| # | 触发条件 | reason 示例 | 设计意图 |
|---|---|---|---|
| 1 | 无 critical | "无 critical" | QC 通过（不常见，主要分支） |
| 2 | critical 描述不在 10 模式中 | "非局部失败: ..." | 全局问题（视线/朝向/构图比例/场景错）——i2i 改不动 |
| 3 | 模式匹配但**无 bbox**（L1 检测失败）| "无可选目标 bbox（检测层缺实体）" | 检测链全挂 → 无修复目标 |
| 4 | 修复区域 > 模式阈值 | "修复区域占画幅 82%，超过 70% 阈值" | 改动范围太大 = 几乎全图重画 = 跟 regen 没区别 |
| 5 | **extra_person 主体护栏**触发 | smaller_person 找不到 `_is_knife_holder`/`_is_lying` 主体 | 防止误删主体（v6.x.x review 修） |
| 6 | i2i 修复**复检仍 critical** | （隐式：attempt 内部重试） | 修复失败 → 下一 attempt regen |
| 7 | 多次 regen 仍 critical | "attempts_exhausted" | max_retries 用完 → 末次保留（坏图比没图好，CLAUDE.md 铁律）|



## v6.55 补充：low 类断言的「升级条件」

D 方案实施后，**low 类断言（详见 qc-reliability-calibration.md）首错只 warning，**不**进本决策树**。仅当跨 attempt 累计 ≥ 2 次答错 **且** key → 升级 critical → 才进本决策树。修复模式如 glowing/eyes_closed/knife_contact 不受影响——只是从首错即修变确认系统性才修。

**反例**：用户单次跑批里 船宽三倍 答 no 一次 → 不进修复 → 第二次还答 no → 升级 critical → 走决策树 regen。

## 常见 critical 案例分流表

### ✅ 走 edit
- 「刀具接触身体」答 yes
- 「刀已刺入杨旭心口」
- 「画面任何红痕迹」答 yes
- 「画面发光物体」答 yes
- 「双目紧闭」答 no
- 「船有篷」答 yes
- 「服装颜色错」
- 「未持钓杆」
- 「多指/少指」
- 「表情错/惊愕不符」
- 「人数 4 vs 3」（主体明确时）

### ❌ 走 regen（不进 10 模式，i2i 改不动）
- 「**神秘人面朝画右的杨旭**」答 no（视线/朝向是**全局属性**，i2i 改头朝向 = 改整张脸）
- 「**船宽约为肩宽三倍**」答 no（构图比例是全局，i2i 改不了）
- 「**画面表现舴艋舟已倾倒**」答 yes（全局场景状态）
- 「**刺客蒙面**」答 no（服装整体不进 costume_color 形态词正则）
- 「**听香目光惊愕**」答 no（视线类不进 10 模式）

### ⚠️ 走 regen 兜底（特殊保护）
- 「**人数 2 vs 3，主体不明**」（如刺客在小框位置）→ extra_person 主体护栏保护 → regen 而非误删主体
- **3 次 regen 仍 critical** → 末次保留（`attempts_exhausted`）

## 不变量

1. **总尝试数 ≤ `QC_MAX_ATTEMPTS`**（默认 3-4）——次序 = repair → regen → 末次保留
2. **修复成功的帧哈希不删**（保留为可重入）
3. **C prompt 微扰动**（attempt 0 → 1 → 2 → 3）只走 regen 路径，edit 不变 prompt
4. **edit 失败** → 下一 attempt regen（attempt 0/1/2 都会试 repair）
5. **bbox 按 (issue, selector) 去重**
6. **sidecar**（`*_hallucination.json`）与本决策**正交**——QC 报告 critical，build_repair_plan 决定修/regen，末次保留都把 critical 状态写进 sidecar

## v6.x.x 演进要点（vs 旧版）

| 演进 | 旧行为 | v6.x.x 新行为 |
|---|---|---|
| 人数错 | 一票 regen | extra_person 修复（smaller_person + 主体护栏） |
| area 阈值 | 一刀切 70%（multi-person 命中率 ≈ 0%）| knife 70% / 其他 95% 分模式阈值 |
| 多模式合并 | first-match-wins（遮蔽问题）| 全部命中 + 指令 dedup 合并 |
| 正则 | 裸属性词（"白袍"必中）| "问题形态词"（错/不符/不对/错误/不一致）|

## 历史 root cause（v6.x.x 08-30 末次汇总时修复）

1. **3 抽 3 修全失败**：无多样性 + 5 模式窄 + 70% 护栏过严 + 夜景"发光"误报 + n=2 查重过严 + 不可序列化 dataclass 崩
2. **`_try_qc_repair` AttributeError**：旧 mock 单测未覆盖内部 i2i+复检路径，bug 藏到实抽才暴露
3. **hook prompt 矛盾**：v6.43 Constraint 给刺杀前 hook 误注入伤锚
4. **视线/朝向盲区**：`_CLAIM_KEY_WORDS` 缺视线词 + `_GROUNDING_PROMPT` desc 无 gaze direction

详见 `~/.claude/memory/qc-retry-diversity-abc-fix.md` + `hook-prompt-stage-conflict-ff-lf.md` + `step02a-qcresult-dump-fix.md`。
