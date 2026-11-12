# M02：修订入口的合法修法、授权面与验收（2026-11-12）

本文件是统一收口包 **M02** 的交付物。目标只有一句：**证明合法修订能通过实际入口**，
并把「为什么 S06 那一轮零进展」从推断变成可复现的事实。

- 责任文件：`step01_script_gen/main.py`（修订轮、验收、台账）、
  `step01_script_gen/repair_fields.py`（授权面）。
- 证据：[`artifacts/m02/diff.md`](artifacts/m02/diff.md)（U6 离线候选）、
  `artifacts/m02/candidate.revised.json`、`artifacts/m02/repair_diag.json`。
- 测试：`tests/step01_script_gen/test_m02_revision_entry.py`（7 条）。

## 1. 第一步：冻结响应回放——先回答五个问题

S06 的修订轮只有 500 字符原始响应（M01 §3），所以「逐段回放」不能逐字做。
能回答的部分与依据如下。

### 1.1 为什么七条问题只进入四个镜头

`_REPAIR_MAX_SHOTS_PER_ROUND = 4`（`main.py`）。七条确认缺陷落在 6 个镜头上
（1/4/6/7/10/11），本轮按优先级取 4 镜：`shot_1`、`shot_6`、`shot_7`、`shot_11`
（`repair_diag.json` 的 `targets`）。这是**容量规则**，不是问题被丢弃。

### 1.2 shot 4、shot 10 未入轮的具体原因

同上：容量上限 4 已满。它们在 `max_rounds=1` 下没有第二次机会——这是 S06 的
授权上限，不是代码缺陷。**结论：这两条属于「本轮未尝试」，不属于「修了没修好」。**

### 1.3 shot 1、shot 6 的改动为什么没有被验收识别

**前提本身不成立**：台账显示 `audit_field_touched = 0`，而该计数是在**回退之前**
对合并结果算的；`prompt` 在 shot 1 / shot 6 的授权面内，只要真的改过，计数必然 ≥ 1。
所以这两个镜头的 `prompt` **没有发生变化**。S06 报告 §2 的「改动已应用但验收未识别」
与台账相反，按 M01 §5.1 撤回。

### 1.4 `keyframes` 是模型无关回显，还是目标修复必需变化

**不能判定**。500 字符里能看到的是 shot 7 的窄字段补丁（`updates[]`），
前三条是 `prompt` / `ff_desc` / `lf_desc`；`prompt` 当时就不在授权面内
（shot 7 的授权只来自 `lf_desc` 与 `ff_desc`）。`keyframes` 是否出现在后面、
是否属于最小合法修法，盘上没有证据。**记为不可判定**，不写成「授权面不足」。

### 1.5 现有协议能否表达最小合法修法

**部分能**。用合法桩响应经真实入口跑出来的结果（§2）：
单字段修法与整镜修法都能走通；**「描述 + 同帧布局」这种成对修法走不通**——
补丁里只要带一条未授权路径，整条被拒（fail-closed），而布局那一条此前不在授权面内。

## 2. 第二步：按证据选择修法

### 2.1 修法：帧描述 ↔ 同帧布局的按实体互授

`repair_fields.frame_layout_companions`：模型审计点名 `ff_desc` / `lf_desc` /
`keyframes[N].desc` 时，同一镜、**同一帧**、**同一实体**的布局条目叶子
（`props` / `on_screen`）一并授权。

收窄边界（三条同时成立，缺一不扩）：

1. **同一镜**——未点名镜头逐字节不动；
2. **同一帧**——按声明路径解析 `ff`/`lf`/`kfN`，不跨帧；
3. **同一实体**——`entity_id` 命中布局条目名时只授该条目；命中不了才退到该帧全部条目。

不授的东西：`pose` / `position`（姿态与站位由审计自己在 `repair_targets` 里点名，
`FF_RESULT_LEAK` 就是这么做的）、`prompt` / `motion_desc`（跨帧聚合字段）、
列表本身（增删条目仍走结构授权面）。被更宽路径覆盖的叶子不再单列
（例如结构码已授 `frame_layouts.kfs.0` 时，不再补该帧的叶子）。

反向（布局叶子 → 描述）**不做**：审计的 `minimal_fix` 对可见性/持物通常给
「改布局」或「改描述」两种方向，只授布局侧仍能修。

### 2.2 台账：请求允许范围 + 后处理抵消

`repair_diag.json` 每轮新增两栏：

| 栏位 | 内容 | 回答的问题 |
|---|---|---|
| `authorization[tid]` | `allowed` / `declared` / `beyond_declared` | 这一轮允许改哪些字段？其中哪些不是问题自己声明的？ |
| `postprocess_reverted[tid]` | 合并后已改对、又被后处理改回的授权路径 | 已接受的修复有没有被管线自己撤销？ |

另把 `rejected_by_validation` 的**完整**拒绝清单落进台账（此前只留前 3 条在 `detail`）。

`postprocess_reverted` **只记账、不改变接受判据**。把它直接升级成拒绝，等于在
没有判别证据时改准入语义——那属于 M09 的正式门禁裁决。

## 3. 第三步：五条路径的证明

`tests/step01_script_gen/test_m02_revision_entry.py`，匿名最小情节（甲 / 木杖），
两镜脚本，桩响应按当轮真实请求的 schema 构造：

| 路径 | 用例 | 读数 |
|---|---|---|
| 单字段修复成功 | `test_single_field_fix_is_accepted` | `accepted=True`、`audit_field_touched=1`、目标字段已改 |
| 必要关联字段联合修复成功 | `test_joint_layout_fix_is_accepted_and_authorization_is_recorded` | `accepted=True`；`declared=["lf_desc"]`，`beyond_declared` 含 `frame_layouts.lf.0.props`；`prompt`/`motion_desc`/别的帧都不在授权面 |
| 整镜无关修改被拒（窄补丁） | `test_unrelated_whole_shot_change_is_rejected_with_full_reason` | `rejected_by_validation`，`rejected` 逐条指名 `prompt` 越权；原稿逐字节不变 |
| 整镜回显的无关字段被回填 | `test_whole_shot_echo_keeps_unrelated_change_out_of_the_script` | 未授权内容不落稿；`prompt` 不在授权面 |
| 授权扩大后非目标仍受保护 | `test_non_target_shot_stays_protected_after_widening` | 未点名镜头的语义摘要不变；拒绝留在台账 |
| 投影不撤销已接受修复 | `test_projection_does_not_undo_accepted_fix` | `project_for_commit` 后描述与布局都在，`script_revision` 生成 |
| 互授不跨帧 | `test_companion_does_not_cross_frames` | 首帧授权、尾帧不在授权面；夹带即整条拒收 |

## 4. U6 离线修订候选（真实内容，桩响应）

`python tools/build_m02_u6_candidate.py`（外部数据集显式声明；原文缺失即失败）：

| 项 | 值 |
|---|---|
| 原稿 revision | `84ff4112…`（与 S01 冻结一致） |
| 修订后 revision | `abfd217d…` |
| 桩调用 | 1 次，`block` 为空 |
| 入轮目标 | `shot_1` / `shot_6` / `shot_7` / `shot_11` |
| 接受 | `accepted=True`，`resolved=0`，`audit_field_touched=3` |
| 实际变化 | 5 处：镜 7 的 `ff_desc`/`lf_desc`/布局道具、镜 11 的 `ff_desc`/布局姿态 |

与 S06 的对照：**同一入口、同一冻结候选、同一批问题**，S06 是
`accepted=false / resolved=0 / 字段触达=0`，现在是 `accepted=true`、三个目标被验收识别。
差别来自授权面（成对修法可表达），不是模型遵从。

### 4.1 一个新发现：后处理会撤销已授权的修复，且此前无记录

`postprocess_reverted = {shot_1: [prompt], shot_6: [prompt]}`。已定位 `shot_1` 的机制：

1. `constraints/framing_jump.py` 在相邻镜景别跨度 > 2 时改写**前一镜**的 prompt；
2. 它用 `_shot_size_level` 取景别，该函数按词表顺序做**子串匹配**：
   词表里 `远景` 排在 `大远景` 之前，于是「大远景」被判成 `远景`；
3. 与下一镜 `中近景` 相差 4 档 → 触发改写，`prompt.replace("远景", "特写")`
   作用在「大远景」上，**恰好还原成「大特写」**。

这是两条判据的冲突（审计要求的「prompt 与帧描述景别一致」vs「相邻镜景别跨度」），
且子串匹配让冲突精确落回原值。`shot_6` 的 prompt 同样被改写回原值（同类 prompt
强化约束，未逐一定位到具体函数）。

按 M09 口径**不改判据**：只记账，并把这条冲突放进影子评估清单（M09 §3）。

## 5. 边界与未验证

- 桩响应是**按 S01 裁决构造的合法修法**，不是模型真实输出；「模型会不会这么写」
  仍属未验证（M10 之后再做真实对照）。
- 本轮只覆盖 `max_rounds=1`；多轮收敛、`shot_4`/`shot_10` 的后续轮次未验证。
- `postprocess_reverted` 目前只比较**标量/文本叶子**；`frame_layouts.*` 这类列表路径
  在扁平化后带 `*`，与保留下标的授权路径不匹配，**布局侧的后处理抵消暂不进这一栏**。
  这是已知覆盖面限制，不是「布局不会抵消」。
- 正式门禁与正式采纳未改动。
