# QC 断言 reliability 预分级（v6.55，2026-08-30）

> D 方案：把"不可靠问题"挡在 critical 通道之外，从源头减少 VLM 噪声 → critical 的浪费。
>
> 校准驱动：`output/video/锦衣夜行/part_01_杀青州/chapter_001/vimax_working/shots/0/first_frame_qc_log.json` 14 次实抽
>
> 关联：`shared/image_qc.py` `_LOW_TRIGGERS` + `_claim_reliability()` + `_claim_severity()` + `maybe_qc_sync()` 判定处

---

## 校准数据

| # | 问题 | 期望 | 矛盾率 | 类型 | reliability |
|---|---|---|---|---|---|
| Q1 | 「仰面朝上双目紧闭？」 | yes | 6/14 = 43% | 抽象语义 | **low** |
| Q2 | 「图中是否有发光物体或光刃？」 | no | 5/14 = 36%（加"自然光不算"限定词后仍 36%，无改善）| 抽象语义 | **low** |
| Q3 | 「船宽约为肩宽三倍？」 | yes | 3/11 = 27% | 空间/比例 | **low** |
| Q4 | 「人物躺卧后船舷两侧仍有空档？」 | yes | 2/13 = 15% | 空间/比例 | **low** |
| Q5 | 「杨旭素白袍袒腹仰卧船头？」 | yes | 1/14 = 7% | 存在性 | high |
| Q6 | 「手中握钓杆垂向水面？」 | yes | 1/14 = 7% | 存在性 | high |
| Q7 | 「红色痕迹/伤口/血迹？」 | no | 0/14 = 0% | 存在性 | high |
| Q8 | 「刀具已接触或切入身体？」 | no | 0/14 = 0% | 存在性 | high |
| Q9 | 「神秘人湿衣伏于舟侧水下？」 | yes | 0/14 = 0% | 存在性 | high |
| Q10 | 「手持匕首于水面之下？」 | yes | 0/14 = 0% | 存在性 | high |

### 关键发现：high 答错 = 真问题

Q5（杨旭姿态）只在 `2026-08-30 11:24:27` 一次答 no——那一次整图全废（背景全黑、3 人但描述全空）。**high 类问题答错几乎都是图本身废了，不是 VLM 噪声**。

→ A 方案（high 类矛盾自动降级）**撤销**：降级会放行废图。B 收窄为纯留痕（不改 severity）。

---

## 分级表

```python
# 实证驱动（first_frame_qc_log.json 14 次实抽）
_LOW_TRIGGERS = (
    # 空间/比例族（矛盾率 15-30%）
    "约为", "约是", "比例", "三倍", "两倍", "一倍",
    "两侧", "空档", "空隙", "间距", "宽", "窄",
    # 抽象语义族（矛盾率 35-50%）
    "紧闭", "微闭", "半闭",
    "发光", "光刃", "光晕", "光束",
    "表情", "神色", "神态", "目光惊愕", "神情",
    # 程度副词族（"明显"/"清晰"/"隐约"/"大致"/"差不多"）08-30 review 撤出
    # — 14 attempt 无矛盾样本，违反"≥5 样本+矛盾率 ≥15%"维护规则。
    # 风险方向是漏检真问题（"白袍上血痕清晰可见"类存在性断言被误降 low），
    # 待下次实抽数据补足再决定是否入表
)
# 视线/朝向类（"目光/凝望/面朝/面向"）**不**进 low：用户决策视线答错走 regen
```

### 默认 high

q 文本不含 `_LOW_TRIGGERS` 任意词 → reliability="high"，答错即 critical（现状不变）。

### 触发 low

q 文本含上述任意词 → reliability="low"，首错只 warning，连错升级 critical。

---

## 行为矩阵

| reliability | key | got | severity | streak 行为 |
|---|---|---|---|---|
| high | T | 答错（含 unclear） | **critical** | 不计次 |
| high | F | 答错 | warning | 不计次 |
| high | F | unclear | warning | 不计次 |
| high | T/F | 答对 | ok | – |
| low | T | 答错（明确 yes/no）| warning | streak +1（≥2 升级 critical）|
| low | T | unclear | warning | **不计次**（解析失败 ≠ 画面事实）|
| low | T | 答对 | ok | **不清零**（跨图无意义，反复 = 系统性）|
| low | F | 答错 | warning | streak +1（不升级）|
| low | F | 答对 / unclear | warning / ok | 同上 |

### 设计原则

1. **答对不清零**：每次 attempt 是全新图片，"连续答对"跨图无意义。`streak ≥ 2` = 该问题跨图反复出现 = 系统性，升级才合理
2. **unclear 不计次**：解析失败不等于画面事实（"我说不清是不是船篷" ≠ "画面有船篷"）
3. **low + 非 key 永不升级**：非 key 锚本就是软约束，warning 已经够用
4. **视线类保持 high**：用户决策视线答错走 regen，校准无视线矛盾样本，下次实抽补

---

## 维护规则（防退化为打地鼠）

新增 `_LOW_TRIGGERS` 词必须**附矛盾率证据**：

1. 从 qc_log 找同问题在不同 attempt 的答案分布
2. 至少 5 次样本
3. 矛盾率 ≥ 15% 才入 low
4. 入 low 后必须同步：
   - 本文件校准表加行
   - `test_image_qc_v655.py` 加 `_claim_reliability` 触发测试
   - `references/decision-tree-qc-repair.md`「走 low 类」分流表加行

**反模式**：
- 凭直觉加词（"我觉得这问题 VLM 也判不准"）→ 不入
- 单一 attempt 矛盾就加词（实抽已知 VLM 答 yes/yet 翻车是模型状态问题，不是问题本身不可靠）→ 不入
- 把"目光/凝望/面朝"加进 low（用户明确决策视线答错走 regen）→ **绝对禁止**

---

## 与既有修复树的交互

`build_repair_plan` 决策树（`references/decision-tree-qc-repair.md`）不变——它消费的是 QcResult.issues，D 方案改的是 issues 的生成。

但 effective 行为变化：

| 场景 | v6.54 行为 | v6.55 D 行为 |
|---|---|---|
| 船宽三倍答 no（low + key）| 立即 critical → 进 regen | 首次 warning，**不**进 regen |
| 船宽三倍第二次答 no（low + key）| （未实现）| streak=2 → critical → 进 regen |
| 红色痕迹答 yes（high + key）| critical → i2i 修红 | critical → i2i 修红（不变）|
| 目光紧盯答 no（high + key）| critical → regen | critical → regen（不变）|

### 节省的浪费

按 first_frame_qc_log.json 实抽统计：
- 14 次 attempt × 平均 2-3 条 low 类断言 = ~35 次 low 首错
- v6.54 每次都进 critical → 触发 regen
- v6.55 每次只 warning → 不触发 regen
- **节省 ~35 次无谓 regen**（按一次 ~5 元算，**~175 元/章**）

---

## 实抽校准数据汇总（原始）

见 `output/video/锦衣夜行/part_01_杀青州/chapter_001/vimax_working/shots/0/first_frame_qc_log.json`（14 次 attempt，2026-08-29 22:29:13 → 2026-08-30 14:42:05）

| ts | 关键答案 | 关键 issues |
|---|---|---|
| 08-29 22:29:13 | 仰卧=yes, 持杆=no | 持杆锚 critical |
| 08-29 22:31:52 | 双目=no, 黑色 2 次 | 人数错 + 仰卧 critical |
| 08-29 22:33:50 | 全部 yes | 无 critical（动作 warning）|
| 08-29 22:35:31 | 全部 yes | 无 critical（动作 warning）|
| 08-30 10:05:32 | 双目=no | 人数错 + 仰卧 critical |
| 08-30 10:10:37 | 双目=no, 发光=yes, 人数 0 | 多 critical |
| 08-30 10:14:54 | 双目=no, 发光=yes | 双 critical |
| 08-30 10:15:52 | 发光=yes | 发光 critical |
| 08-30 11:22:27 | 发光=yes, 船宽=no | 双 critical |
| 08-30 11:24:27 | 全部 no（整图废）| **5 critical**（high 真问题）|
| 08-30 11:26:05 | 发光=yes, 人数 4 | 3 critical |
| 08-30 11:27:05 | 双目=no, 人数 2 | 3 critical |
| 08-30 14:40:10 | 双目=no, 船宽=no, 人数 4 | 4 critical |
| 08-30 14:42:05 | 全部 yes | 无 critical |

---

## 与决策树文档的关系

`references/decision-tree-qc-repair.md` 记录的是 issues 产生**之后**的修/regen 分流。
本文档（D 方案）记录的是 issues 产生**之前**的可靠性预分级。

两者正交：
- D 方案改 severity（critical/warning），决定问题是否进 critical 通道
- 决策树改 action（edit/regen），决定 critical 怎么修

D 方案实施后，决策树的"走 regen 的 7 类情况"中"非局部失败"一类会**显著减少**（low 类首错不再 critical → 不再进决策树）。
