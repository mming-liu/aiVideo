# Step01 时间语义写入者审计（2026-09-19）

## 调查范围

- 目标：核实 `step01_script_gen` 是否存在多个相互独立的关键帧时间语义写入者。
- 依据：当前工作树静态代码与 `chapter_001` 最新运行日志，不采用历史行号。
- “时间写入”指修改 `duration_sec`、`motion_desc` 时间段、KF 数量、`t_sec` 或 `time_ratio`。
- “描述写入”指修改 `keyframes[*].desc`，但不一定修改物理时间。
- `RepairRecord` 只记录已发生的修改；返回 `RepairRecord(action="warn")` 才表示纯报告。不能因为约束返回了 `RepairRecord` 就把它判为只读。

## 结论

结论成立，而且范围比最初列出的 9 个文件更大。

单次 `run_hard_fix` 中，正片 KF 的 `t_sec` 至少会被 7 个独立约束实例或结构处理路径写入；KF 描述还会被十余个约束修改。`KfTimeWindowAlignmentConstraint` 和 `KfEventOrderConstraint` 又各注册了一个 final 实例，因此同一实现会在一轮中执行两次。

根因不是缺少某一个去重规则，而是缺少单一的时间事实源和单一写入者。当前 `duration_sec`、`motion_desc`、KF 时间、KF 描述及 `frame_layouts.kfs` 分开维护，约束依次根据局部启发式改写，后一个约束无法证明前一个约束的语义假设仍成立。

## 点名的 9 个 KF 约束核实

| 文件 | 当前性质 | 新增/删除 KF | 改 `t_sec` | 删除 `time_ratio` | 改 `desc` | 备注 |
|---|---|---:|---:|---:|---:|---|
| `kf_time_window_alignment.py` | 真写 | 新增 | 是 | 是 | 新帧描述 | 越窗 KF 被搬到某个时段中点；缺段时从 `motion_desc` 派生新 KF。注册两次。 |
| `kf_timeline_normalize.py` | 真写 | 否 | 是 | 通过 `set_kf_t_sec` 清理 | 是 | 同刻簇重排、越界钳制、标签剥离、hold 帧改抄前一 KF。 |
| `kf_monotonic.py` | 真写 | 否 | 是 | 通过 `set_kf_t_sec` 清理 | 否 | 发现降序后按列表顺序均匀重排，不核对描述所属事件。 |
| `kf_t_sec_bounds.py` | 真写 | 否 | 是 | 通过 `set_kf_t_sec` 清理 | 否 | 越界按 stage 比例重定位；同刻塌缩按 stage 摊开。 |
| `kf_pre_post_conflict.py` | 真写 | 否 | 是 | 通过 `set_kf_t_sec` 清理 | 是 | 无显式 motion 时段时，按关键词删除 pre 子句并移动时间，或把 pre/post KF 移到固定比例。 |
| `kf_weak_template.py` | 真写 | 否 | 否 | 否 | 是 | 用 ff/lf 静态描述扩写弱模板 KF；有文本 marker，但不是时间写入 marker。 |
| `frame_anchor_rebuild.py` | 真写 | 否 | 否 | 否 | 是 | 从 `frame_layouts.kfs` 重建角色锚块，可能改变后续相似度和事件匹配结果。 |
| `final_quality_normalizer.py` | 混合 | 否 | 否 | 否 | 是 | 多项风险已改为报告，但 `_normalize_weapon_holder` 仍会改 KF 文本。 |
| `kf_event_order.py` | 报告 | 否 | 否 | 否 | 否 | 当前实现只记录冲突，不再搬时间、补帧、删帧或改描述；注册两次。 |

这 9 个文件中，5 个直接改写 KF 时间或数量，3 个继续改写 KF 描述，只有 `kf_event_order.py` 已完全降为审计。

## 额外时间写入者

点名清单没有覆盖以下路径：

| 路径 | 写入行为 | 风险 |
|---|---|---|
| `constraints/split_continuity.py` | 重写拆分镜 `motion_desc`，并对其 KF `t_sec` 减去 offset | 用正则推断局部时间；残缺或已局部化的时间串可能被二次换算。 |
| `constraints/hook_duration.py` | 扩展 hook 时长并补建固定 `3.5s` after KF | 固定“画面定格”不是来自故事证据。 |
| `timecode._do_pre_split_oversized_shots` | 按旁白字符比例估算 `cut_t`，截取 KF 并重算相对时间 | 旁白字符位置不等于事件边界；同时深拷贝语义字段。 |
| `constraints/runtime_extend.py` | 修改镜头 `duration_sec` | motion 事件区间和 KF 内容不随时长同步扩展，后续缺段逻辑会把新增尾部当作需要补帧的时间。 |
| `shared.kf_time.set_kf_t_sec` | 写 `t_sec` 并清除 `time_ratio` | 本身是工具函数，但多个约束用不同假设调用它。 |

因此，“至少 7 个独立约束实例依次覆写 `t_sec`”是保守结论：

1. `KfPrePostConflictConstraint`
2. `KfTimeWindowAlignmentConstraint`
3. `KfTimelineNormalizeConstraint`
4. `KfTSecBoundsConstraint`
5. `KfMonotonicConstraint`
6. `_KfTimeWindowAlignmentFinal`
7. `SplitContinuityConstraint`

> 说明：第 7 项 `SplitContinuityConstraint` 同时出现在上文“额外时间写入者”表中，
> 因为它在同一轮内被两条路径执行（约束链内 + 拆镜 pre-pass）。
> 该清单计的是**执行路径**，不是去重后的文件数。


此外，拆镜 pre-pass 会在约束执行前重算 KF 时间；hook 还有独立写入路径。

## 额外 KF 描述写入者

除上述文件外，当前约束目录中还能确认这些 `keyframes[*].desc` 写入：

- `emotion_visualization_repair.py`
- `boat_width.py`
- `facing_to_gaze.py`
- `frame_spec_repair.py`
- `frame_text_state_cleanup.py`
- `group_actor_continuity.py`
- `gaze_typo.py`
- `kf_blood_monotonic.py`
- `offscreen_action_consistency.py`
- `prompt_sanitize.py`
- `v6_43_prompt_enforcement.py`

其中有些属于词法清洗，有些会增加姿态、情绪、血迹、朝向或角色状态。即使它们不改 `t_sec`，也会改变事件匹配、相似度判定和下一轮约束的触发条件，因此属于时间语义链的间接写入者。

## 注册顺序造成的组合问题

当前注册表的相关顺序是：

1. `KfPrePostConflictConstraint`
2. `SplitContinuityConstraint`
3. `KfTimeWindowAlignmentConstraint`
4. `KfTimelineNormalizeConstraint`
5. `KfTSecBoundsConstraint`
6. `KfMonotonicConstraint`
7. `KfEventOrderConstraint`（报告）
8. `_KfTimeWindowAlignmentFinal`（再次真写）
9. 多个 KF 描述/锚点写入约束
10. `_KfEventOrderFinal`（再次报告）

这个顺序不能保证全链幂等：

- 前段按时间窗口补帧或搬时间；
- 中段按 stage 或列表顺序重新摊时间；
- 后段修改 KF 描述，使原先的事件归属判断失效；
- final alignment 再根据当前时间覆盖情况补帧；
- final event order 只能报告新矛盾，不能撤销前面的写入。

注册表注释仍写着“事件顺序定稿后补齐被删重复帧”等旧行为，但当前 `kf_event_order` 已经是只读审计。注释与实际行为不一致，会掩盖 `_KfTimeWindowAlignmentFinal` 仍在补建 KF 的事实。


### 补充证据 A：`is_final_pass` / `is_in_repair_loop` 是死标志

全仓检索 `ctx.is_final_pass`、`ctx.is_in_repair_loop`、`self.is_final_pass` -> **零匹配**。
两个标志只在 `constraints/context.py:50-51`、`:73-74`、`:103-104` 被写入，
`engine.py:116-117`/`:131` 的注释（“`is_final_pass=True` 让 `RuntimeExtendConstraint`
拉伸时长生效”）与 `main.py:3885` 的同款注释**都已过时** —— `runtime_extend.apply`
里没有任何 `is_final_pass` 判断。

**后果**：所谓 “final pass” 不是“最后再跑一次不同行为”，而是**行为完全相同地再跑一次**。
上文第 8 步 `_KfTimeWindowAlignmentFinal`（再次真写）与第 10 步 `_KfEventOrderFinal`
（再次报告）并不比第 3、7 步多做任何事，只是把同一条路径重复一遍。

### 补充证据 B：三个约束在同一链上确定性互相撤销

这使“不能保证全链幂等”从推断升级为**可复现的构造性证明**：

```
v6_43_prompt_enforcement.py:786-791
    写入 "...中景角色姿态定格...等待动作启动。"
        |  正好命中 kf_weak_template.py:30-34 的 _WEAK_MARKERS 8 个词
        v
kf_weak_template.py:112
    整段重写，并注入幂等锚 "（本帧画面即首帧静态状态：...）"
    幂等守卫在 :99 -- if not desc or _REPAIR_MARKER in desc: continue
        |  _REPAIR_MARKER = "本帧画面"（:38）
        v
emotion_visualization_repair.py:40   fixed = _strip_frame_state_wrapper(value)
        |  （:6 从 .kf_timeline_normalize 导入 _strip_frame_label）
        v
kf_timeline_normalize.py:49-57  _strip_frame_label
    剥掉 "本帧画面即首帧静态状态：" -- 把 "本帧画面" 一并删除
        v
    下一轮 hard-fix：v6_43 再写 -> kf_weak_template 守卫失效 -> 再次注入 -> 再次被剥
```

`kf_weak_template` 的幂等守卫依赖 `"本帧画面"` 这个子串存在，
而下游约束专门删除含该子串的标签 —— **幂等性被下游按构造破坏**。

### 补充证据 C：现存唯一的幂等测试**正在失败**，失败形态即本审计描述的缺陷

`tests/step01_script_gen/constraints/test_full_hardfix_kf_idempotency.py`
连跑两遍 `run_hard_fix(..., is_final_pass=True, return_report=True)`（`:93-95`），
断言 `keyframes`、`frame_layouts.kfs` 相等且 `compute_script_revision` 一致（`:96-98`）。

**当前工作树下该用例失败**（`pytest tests/step01_script_gen/` 基线：`1 failed, 966 passed`）：

```
AssertionError: assert [{'desc': '甲已...ge': 'after'}] == [{'desc': '甲已...ge': 'after'}]
Right contains one more item:
  {'desc': '纵身跃上船（画面延续，保持该状态）', 't_sec': 6.0, 'stage': 'after'}
```

即**每跑一遍 hard-fix 就多补一个伪 hold 帧**，第二轮比第一轮多一条。

失败形态与根因判断 §3 完全一致，构成端到端实证：

```
第 1 遍   runtime_extend    duration_sec 5.0 -> 8.0（1.60x，日志实测）
          motion_desc       仍只覆盖 0-1 / 1-3 / 3-4 秒
          _parse_motion_time_segments   末尾自动补 implicit 段 (4.0, 8.0)
          kf_time_window_alignment      该段无 KF 覆盖 -> append 中点帧 t=6.0
第 2 遍   同上再补一条 -> first != second
```

**这使“单约束各自幂等、组合起来不幂等”成为可复现的事实**，而非推断：
`kf_time_window_alignment` 自身有“该段已有 KF 覆盖则跳过”的守卫，
但别的约束会移动或重排该帧，使守卫在下一轮失效。

该用例因此可直接作为**第一阶段（写入者降为报告）的验收测试**：
降级后不应再有 KF 被追加，`first` 应等于 `second`。

## 最新运行证据

`chapter_001/step01.log` 显示：

- 初始 hard-fix 出现 `kf_time_window_alignment` 写入；
- 修复轮后出现大量 `kf_monotonic` 和 KF anchor 写入；
- 修复候选解决 24 个阻断，但又产生 17 个新阻断，因此被回退；
- 最终质量门仍有 `kf_time_not_strict`、`kf_event_duplicate` 和 `kf_event_window_mismatch`。

成品脚本中可见对应症状：

- 镜头 1、2 存在两个完全相同且同为 `4.5s` 的末尾 KF；
- 镜头 11.5 出现逆序 KF 和残缺的 `.378378秒` 文本；
- 多个 KF 的描述所属事件与 `t_sec` 所在 motion 窗口不一致。

这些现象不能归因于单一 LLM 输出错误，因为约束链在模型输出之后继续新增、移动和改写 KF。

## 根因判断

### 1. 没有规范化的事件时间结构

系统把自由文本 `motion_desc` 当作可反复解析的事实源。不同模块用不同正则和关键词重新解释它，无法稳定表示事件身份、事件边界和 KF 归属。


`motion_desc` 时间段有 **16 处互不一致的解析点**（初版数出 11 处，收尾时又发现 5 处——见下表末 5 行）。

**勘误（v6.77 复核）**：本表初版把「小数」列标错——`_KF_TIME_SEG_PATTERN` 与
`_parse_motion_time_segments` **其实支持小数**（正则里就是带可选小数位的写法）。
真正不认小数的**只有** `_split_motion_to_seg_texts` 里那段**剥标签正则**——
它就是 `.378378` 残片的**唯一**来源。下表已更正，并补上分隔符集一列
（初版漏了这项，而它才是「两套正则互不覆盖」的实证）。

| 函数 | 位置 | 分隔符集 | 小数 |
|---|---|---|---|
| `timecode._rewrite_motion_time` | `timecode.py:93` | `- ~ 至 到` | 是 |
| `timecode._split_motion_desc_at_cut` | `timecode.py:110` | `- ~ 至 到` | 是 |
| `timecode._extract_motion_duration` | `timecode.py:475` | `- – ~ 至 到` | 是 |
| `validation._KF_TIME_SEG_PATTERN` | `validation.py:6478` | `- － ~ ～` | 是 |
| `validation._split_motion_to_seg_texts`（**剥标签正则**） | `validation.py:4842` | `- － ~ ～` | **否** ← 残片来源 |
| `split_continuity._rebase_motion_times` | `split_continuity.py:15` | `- ~ 至 到` | 是 |
| `split_continuity._match_segment` | `split_continuity.py:47` | `- ~ 至 到` | 是 |
| `motion_time_bounds._RANGE_RE` | `motion_time_bounds.py:16` | `- ~ 至 到` | 是 |
| `kf_event_order._RANGE_RE` | `kf_event_order.py:31` | `- ~ 至 到` | 是 |
| `quality_issues`（区间支 + 提及支） | `quality_issues.py:141,189,195` | `- ~ 至 到` | 是 |
| `validation._TIME_SEGMENT_RE` | `validation.py:2331` | `[-–]` | **否** |
| `validation._TIME_SEG_RE`（局部 shadow） | `validation.py:2355` | `[-–]` | **否** |
| `validation._COMBAT_TIMING_PATTERN` | `validation.py:3038` | `[-–—]` | 是 |
| `weapon_state_consistency._SEG_RE` | `weapon_state_consistency.py:15` | `- ~ 至 到` | 是 |
| `final_quality_normalizer` | `final_quality_normalizer.py:116` | 复用 validation | — |

**两组分隔符互不覆盖**：`至/到` 只有一半解析器认，全角 `－/～` 只有另一半认——
同一份 motion_desc 交给不同模块会得到不同段数。

> **v6.77（2026-09-19）已统一**：全部收敛到 `shared/motion_segments`（唯一事实源），
> 分隔符取并集、小数全链路支持、时间标签统一截 2 位小数。
> 详见「落地进度」章节。

`.378378秒` 正是“一个支持小数、一个只认整数”的直接产物：

```
timecode.py:243            cut_t = orig_dur * (字符位置比例)  -> 1.621622（非整）
timecode.py:107            f"{a:g}-{b:g}秒：{body}"
                           -> "0-0.378378秒：庄中佃仆赶到岸边，向船上喊道"
validation.py:4842-4848    三个分支全用 \d+，第三支
                           ^\d+\s*[-－~～]\s*\d+\s*
                           对 "0-0.378378秒：..." 只剥掉 "0-0"
                           -> 残片 ".378378秒：庄中佃仆赶到岸边，向船上喊道"
kf_time_window_alignment.py:163-165   把残片写进 KF desc
```

因此“统一为单一严格解析器”应当是可验收项，而不是附带清理。

### 2. KF 没有稳定事件身份

KF 只有 `stage`、时间和描述，没有 `event_id`。约束只能根据时间窗、词语相似度或 stage 猜它属于哪个事件，移动时间后也无法证明描述仍匹配目标事件。

### 3. 时长扩展与内容扩展混为一体

`runtime_extend` 增加 `duration_sec`，但不增加有原文证据的事件。新增时间随后被缺段逻辑解释成需要补 KF 的剧情时间。


机制层的直接原因是：**系统里唯一设计来“时长变了同步 KF 时刻”的机制是死代码。**

`shared/kf_time.py:95-103` 的惰性重标定：

```python
new_dur = shot.get("duration_sec")
old_dur = shot.get("duration")
if new_dur and old_dur:
    ratio = float(new_dur) / float(old_dur)
    if abs(ratio - 1.0) > 0.01:
        t = t * ratio
```

但 `duration` 与 `duration_sec` 在生产链路上**总是被成对写成同一个值**：

| 写入点 | 成对同值 |
|---|---|
| `timecode.py:662/663`、`:831/834` | 是 |
| `runtime_extend.py:208/209`、`:230/231`、`:292/293` | 是 |
| `duration_budget.py:121/123` | 是 |
| `hook_duration.py:45/46` | 是 |
| `motion_time_bounds.py:67/69` | 是 |
| `opening_empty_shot_cap.py:155/156` | 是 |
| `short_shot_merge.py:51/52` | 是 |
| `duplicate_shot_merge.py:115` | **否（只写 `duration_sec`）** |

于是 ratio 恒为 `1.0`，**重标定从不触发**。而唯一非对称的
`duplicate_shot_merge.py:115`，紧接着在 `:125` 执行 `a.pop("keyframes", None)`，
把重标定唯一可能作用的对象整份删除。

实测后果（`chapter_001` index 12，`duration_sec = 7.88`）：

```
kf3 t=5.5     ...（画面延续，保持该状态）
kf4 t=7.088   ...（画面延续，保持该状态）
```

即 `t_sec` 是绝对秒数，`duration_sec` 在其下方被拉伸，尾部成为“缺段”，
由 `kf_time_window_alignment` 补出伪 hold 帧。
**修法必须是让时长由事件时间轴推导，而不是继续修补重标定。**

### 4. 拆镜发生得过晚

拆镜会复制并重算已经生成的 KF、frame layout 和视觉描述。它不是对结构化事件的切分，而是对旁白字符和已有产物的启发式切割。

### 5. 修复轮不是局部事务

LLM 返回字段补丁后，系统会重新计算 timecode、再次拆镜并运行整套 hard-fix。接受检查比较的是“补丁加全局副作用”，所以正确的局部修复也可能因后处理产生的新阻断而整体回退。

## 实施建议

### 第一阶段：停止继续制造时间语义

- 将 `kf_time_window_alignment`、`kf_timeline_normalize`、`kf_monotonic`、`kf_t_sec_bounds`、`kf_pre_post_conflict` 的时间/描述写入降为只报告。
- 将 `hook_duration` 的固定 KF 补建降为报告；hook 不足时走完整 hook regen。
- 修复轮中的 `_compute_timecodes` 禁止拆镜，只重建 timeline。
- `runtime_extend` 只报告内容时长不足；静态停留使用独立 `blank_hold_duration`，不进入事件时间和 KF 时间。

这一阶段会让质量门更诚实地 BLOCK，但不会再把推断结果标记为已修复。

### 第二阶段：引入单一时间事实源

为镜头增加结构化 `temporal_plan.events[]`：

```json
{
  "event_id": "s5_e2",
  "start_sec": 1.0,
  "end_sec": 2.0,
  "description": "刀刺中杨旭心口",
  "source_quote": "正刺中杨旭的心口"
}
```

每个 KF 必须引用一个 `event_id`。`motion_desc` 只作为展示文本，不再被后处理反向解析为事实。

唯一允许的确定性时间修改是帧率对齐，而且对齐后的 `t_sec` 必须仍位于同一 `event_id` 的区间内。缺帧、重复帧、错窗帧和描述矛盾都进入完整镜头 regen。

### 第三阶段：把拆镜移到 KF 生成前

- 只允许按 `temporal_plan.events` 边界拆镜。
- 无合法事件边界时，要求 LLM 重写镜头。
- 两个子镜分别生成完整的 ff、motion、KF、lf 和 frame layouts。
- 禁止深拷贝 KF、按旁白字符比例换算 `cut_t`，以及自动追加过肩、情绪、焦点等视觉事实。

### 第四阶段：修复轮改为完整镜头事务

- LLM 返回 `shot_replacements[]`，不再返回任意字段 patch。
- 在副本中校验替换镜头和相邻边界。
- 校验通过后原子替换；其他镜头的语义 revision 必须不变。
- 结构问题与内容问题分开处理，局部内容修复不得触发全局拆镜。

## 验收条件

1. 同一脚本连续运行 hard-fix 两次，语义 payload 字节级一致。
2. 调整总片时长不改变 KF 数量、描述、事件归属或 `t_sec`。
3. 每个 KF 恰好属于一个 `event_id`，且时间严格位于其事件窗口内。
4. 拆镜后两个子镜的事件集合互斥，合并后等于原事件集合。
5. 修复一个镜头不能改变其他镜头的语义 revision。
6. 修复轮内的 timecode 重建不得增删镜头。
7. `chapter_001` 回归必须稳定拦截重复 KF、错窗 KF、镜头 11.5 残缺时间文本，且不得自动改写这些语义。


### 可测性分级（落地前先补基础设施）

| 验收条件 | 当前可测性 |
|---|---|
| #1 连跑两次语义 payload 字节级一致 | **需先补**：现存唯一幂等测试只到 revision-hash 级，且 `shared/quality_gate.py:43-46` 的语义白名单仅 4 个键（`desc`/`stage`/`t_sec`/`time_ratio`）。需新增 `payload_signature()` 或扩白名单 |
| #2 调总时长不改 KF | 可测：构造不同 `duration_sec` 跑同一条 KF 链，断言 KF 集合不变 |
| #3 每个 KF 恰属一个 `event_id` | 需先有第二阶段 schema |
| #4 拆镜子镜事件集合互斥 | 需先有第二阶段 schema |
| #5 修复单镜不改他镜 revision | **需先补**：修复轮无逐镜 revision 快照通道 |
| #6 修复轮 timecode 不增删镜头 | 可测：断言 `len(shots)` 不变 |
| #7 `chapter_001` golden case | **需先补**：仓库无 `tests/**/fixtures` 目录（`find` 零结果），测试全部内联构造 dict；需落盘 `chapter_001` 基线 |

已有可复用的构造起点：`tests/step01_script_gen/conftest.py` 提供 `make_shot` /
`make_script` / `make_ctx` 三个 fixture 工厂（注：前两个仍带 `char_layout` 字段）。

## 最终判断

“多个相互独立的时间语义写入者”已经由当前代码和运行结果共同证实。继续增加去重、搬时刻或 final pass 只会扩大组合状态。应先把现有写入者降为审计，再建立 `temporal_plan + event_id` 单一事实源，最后恢复受控的确定性处理。

---

> ## ⚠️ 本文结论已被后续实现审核修正（2026-09-20）
>
> `docs/step01-temporal-writers-implementation-review-2026-09-19.md` 对本轮实现做了
> 11 项发现级复核，**推翻了本文若干"已完成"的结论**。阅读时请以下列为准：
>
> | 本文原结论 | 复核后的真实状态 |
> |---|---|
> | 第一阶段"已完成" | **局部完成**：五个 KF 写入者已降级，但**字段所有权与 late writers 当时尚未收敛**——`short_shot_merge` / `motion_time_bounds` / `duplicate_shot_merge` / `duration_budget` 仍在链尾写 `duration_sec`，而 KF 时间检查注册在它们之前（发现 1，P0） |
> | 选项 (c)：`duplicate_shot_merge` 显式按比例缩放 KF | **已否决作为生产语义策略**：Jaccard 相似 ≠ 事件等价，缩放会把 KF 移出它所属的事件（发现 2，P0）。该约束已降为 `warn + 完整镜头 regen` |
> | 7 个 `_Final` 已删除 | **保留但证据不足**：单一 fixture 的等价结果不等于全输入等价证明，需补顺序依赖测试（发现 6） |
> | 拆镜改为事件边界 | **方向正确但未完成**：`allow_split` 默认已反转为 `False`（停用生产自动拆镜）；"拆镜前移到视觉字段生成之前"的目标流程尚未实现（发现 3、决定 3） |
> | `temporal_plan` LLM 直出优先 | **当前只读**：过渡态下不得驱动拆镜/合并/duration/KF 写入；必填切换需按依赖顺序发布（发现 10/11、决定 1） |
>
> 修正在 v6.79 / v6.80 落地：四个链尾写入者降为只报告、`temporal_plan` 纳入
> semantic revision 并升级 `QUALITY_RULE_VERSION`、`validate_events` 补全整镜覆盖/
> grounding/motion 一致性、拆镜移除"按相似度删位置分句"、新增阶段屏障
> （`shared/semantic_barrier.py`）。

## 落地进度

### 版本号：v6.76（2026-09-19）

本批改动登记为 **v6.76**（`step01_script_gen/rules_index.py`）。同日已存在
**v6.75**「evidence-gated writes：不确定的语义修复保留原文并报告，禁止自动猜状态/删内容」
——两者属同一 campaign 的两半：

- **v6.75** 覆盖 **desc 层**写入者（`kf_pose_anchor` / `frame_spec_repair` /
  `v6_43_prompt_enforcement` / `final_quality_normalizer` / `group_actor_continuity` /
  `split_continuity` 等 16 个）
- **v6.76** 覆盖 **时间层**写入者（`t_sec` / `duration_sec`）+ 结构性闸门

若希望合并为一条版本记录，把 v6.76 的 `constraints` / `tests` 并入 v6.75 即可。

### 第一阶段：停止继续制造时间语义 —— **局部完成**（下表为当时的判断）

> **v6.79 复核更正**：下表是 v6.76 时点的结论，**不能读作端到端保证**。真实状态是
> 「五个 KF 写入者已降级，但字段所有权与 late writers 当时尚未收敛」——四个时长
> 写入者（`short_shot_merge` / `motion_time_bounds` / `duplicate_shot_merge` /
> `duration_budget`）当时仍注册在序号 57-80，**在 KF 时间检查（8-11）之后**，
> 链后写入足以让链前检查失效。它们已在 v6.79 全部降为只报告，v6.80 起有阶段屏障
> 守卫（`shared/semantic_barrier.py`），v6.82 起该守卫接进落盘前的 final pass。
> 详见 `step01-temporal-writers-implementation-review-2026-09-19.md` 发现 1。
>
> 另：表中「修复轮禁止拆镜」一行描述的是 `_compute_timecodes(..., allow_split=False)`。
> 复核发现该布尔**只关掉拆镜 pre-pass**，后续完整估时逻辑照跑，仍会覆盖每个 shot 的
> `duration` / `duration_sec`——修复轮已改调纯序列化函数
> `write_timeline_from_durations`（发现 3）。

| 项 | 改动 | 验证 |
|---|---|---|
| 5 个 KF 时间写入者降为只报告 | `kf_monotonic` / `kf_t_sec_bounds` / `kf_timeline_normalize` / `kf_time_window_alignment` / `kf_pre_post_conflict` 全部改为 `record_violation(repaired=False)` + `RepairRecord(action="warn")`，**不再写任何字段**。上报级别沿用各类自身声明的 severity | grep 确认 5 文件零 KF 字段赋值 |
| `hook_duration` 两项全降为报告 | 时长 < 4.0 **不再扩到 4.0**；kfs < 2 **不再补**硬编码 `t_sec=3.5` 的定格帧。两项检查**解耦**（原 `if dur >= 4.0: return` 会让「时长达标但 KF 只有 1 个」的 hook 完全逃过检查） | 测试锁定 |
| 修复轮禁止拆镜 | `_compute_timecodes(script, *, allow_split=True)`；修复轮（`main.py:2146`）传 `False`，只重建 timeline；有超 cap 镜时打日志报数 | 新增 `test_timecode_split_gate.py`（3 例） |
| `runtime_extend` 只报告 | 移除末镜夹取与按比例拉伸（两者都在无内容支撑下增加时长）；**保留**单镜 > cap 夹取（防 provider 截断，属减时长）；`total < 60s` 合并为**一条** HARD Violation，提示指向「补镜头」 | 测试锁定 |
| 删除 3 个纯重复的 `_Final` 注册 | `kf_time_window_alignment_final` / `kf_event_order_final` / `kf_pose_anchor_final`——其基类已降为只报告，实例只是把同一条只读检测再跑一遍 | `ALL_CONSTRAINTS` 92 → 89 |

**验收结果**：`tests/step01_script_gen/constraints/test_full_hardfix_kf_idempotency.py`
由**失败**转为**通过**（`first == second`，不再每跑一遍多补一个伪 hold 帧）。
全仓测试 `1465 passed`。

**顺带切断的一条实锤 bug 路径**：`kf_time_window_alignment` 原先调用
`_split_motion_to_seg_texts` 取段文本，而该函数的时间标签正则**只认整数**
（`validation.py:4842-4848`），会把 `0-0.378378秒：…` 剥成 `.378378秒：…`
残片写进 `kf.desc`。降为只报告后该写路径消失。

### `_Final` 重复注册：只删了 3 组，另 4 个实例**有意保留** —— 该判断已被推翻

> **v6.79 复核更正**：七个 `_Final` 在 v6.77 已**全部删除**。删除**成立**，但理由要
> 改写：不是「全输入字节级等价」，而是**顺序依赖**——原中段
> `_KfBloodMonotonicFinal` 有确定反例（`KfBloodMonotonicConstraint` 往 `lf_desc`
> 追加伤情锚 → `v6_43_prompt_enforcement._head_fix_context_ok()` 据此补造
> 「仰面朝上双目紧闭，头颈僵直不侧转」这类结果态；两约束不满足交换律）。
> 新链序 `v6_43 → kf_blood_monotonic` **有意**移除了这条「后加伤情锚触发死亡姿态
> 补造」的路径。回归见
> `tests/step01_script_gen/constraints/test_constraint_order_dependency.py`，
> 说明见 `constraints/__init__.py` 与审核发现 6。

判据是「基类是否仍写字段」——只有当基类已降为只报告时，`_Final` 实例才是纯重复。

| 实例 | 处置 | 原因 |
|---|---|---|
| `_KfTimeWindowAlignmentFinal` | 删除 | 基类 v6.76 降为只报告 |
| `_KfEventOrderFinal` | 删除 | 基类此前已只报告 |
| `_KfPoseAnchorFinal` | 删除 | 基类此前已只报告 |
| `_KfBloodMonotonicFinal` / `Final2` | **保留** | 基类仍写 `kf.desc` |
| `_GroupActorContinuityFinal` | **保留** | 同上 |
| `_FrameTextStateCleanupFinal` | **保留** | 同上 |

保留的原因是链序：`FrameAnchorRebuildConstraint`（`__init__.py:187`）会重建
`kf.desc` 的 `【角色锚定】` 块，**丢掉**血锚 / 姿态锚 / 群体在场标记——这 4 个
`_Final` 正是「重建后再补回」。删掉等于少跑一遍写入，属行为变更，须与各自基类
的降级一并进行。

### 顺带发现并修正的既有问题（非本批改动引入）

1. 补充证据 C 原写「该幂等测试通过」——实测为**失败**，已改正（见该节）。
2. `tests/shared/test_frame_spec.py::test_constraint_onscreen_true_with_exit_word_hard`
   断言旧文案 `"on_screen=false"`，而工作区 `frame_spec_consistency.py` 的未提交改动
   已把该行为改成「保留原 spec，仅报告并交 regen」——测试未同步。已改为锁定新语义
   （含「禁止自动翻转」断言）。
3. `rules_index.py` 的 v6.74 条目引用了本批删除的 `kf_time_window_alignment_final`，
   被守卫测试 `test_all_constraint_names_resolve` 抓到，已移除该引用。

### `duplicate_shot_merge` 的 KF 整份删除 —— 已修复（**该合并路径随后被整体删除**）

> **v6.79 复核更正**：本节记录的「不再 pop keyframes」只是消除了症状。复核发现
> `duplicate_shot_merge` 的**自动合成本身**不成立——Jaccard 相似 ≠ 事件等价，
> 按比例缩放前镜 KF 会把帧移出它所属的事件（实测「收刀」KF 被挪到没有正文的
> 空白尾段）。该约束已整体降为**只报告**：不删镜头、不拼接 narration、不求和
> duration、不缩放 KF；检出即 HARD 违例，交 LLM 完整重写其中一镜。
> 详见审核发现 2 与 `constraints/duplicate_shot_merge.py` 的模块说明。

原 `duplicate_shot_merge.py:125` 在合并相邻重复镜后执行 `a.pop("keyframes", None)`，
注释称「重复视觉的后镜 kf 无信息量，**丢弃由下游重派生**」。

**但那个下游已经不存在了**：唯一的重派生者是 `kf_time_window_alignment`（按 motion
缺段派生补帧），而它已在本版降为只报告。pop 之后没有任何模块会重建 KF。

实测后果比「无 KF」更重：

```
合并前:  keyframes=2 帧,  frame_layouts.kfs=1
合并后:  keyframes 键消失,  frame_layouts.kfs 仍为 1（陈旧）
再跑 frame_spec_repair:  frame_layouts.kfs 被弹到 0
```

`frame_spec_repair` 以 `len(keyframes)` 为基准对齐 `frame_layouts.kfs`，
keyframes 缺失（=0）会把 kfs 一并弹空 → **该镜在 step02a 一个关键帧图都不生成**。

**修法**：移除 `pop`，keyframes 保留前镜——这本来就是该文件 docstring 第 12 行
写明的语义（`keyframes 保留前镜`），是**实现与自己的 docstring 相矛盾**。

实测新行为（`duration` 与 `duration_sec` 都在的真实形态）：

```
合并后  duration=4.0  duration_sec=8.0  ratio=2.00
   stored t_sec=1.0 -> effective 2.0   (镜长 8.0)
   stored t_sec=3.0 -> effective 6.0
```

KF 被 `shared/kf_time.py` 的惰性重标定**等比摊到合并后的时间轴**——正好是合并想要
的语义，且**本处是全仓唯一会触发该重标定的位置**（只写 `duration_sec` 不写
`duration`，构成唯一的非对称写入）。已在代码注释中标注：若日后把此处改成对称写入，
KF 会退回旧时刻、尾部变成无覆盖时段（由 `kf_time_window_alignment` 上报）。

新增回归测试 `test_merged_shot_keeps_keyframes_and_layout_kfs`。

### `duplicate_shot_merge` 的 motion_desc 取前镜 —— 已修复（**「取前镜/后镜」语义已不存在**）

> **v6.79 复核更正**：该约束不再合并，因此「motion 取前镜、lf 取后镜」这组字段
> 拼盘语义整体消失——两镜各自的 `motion_desc` / `lf_desc` 都保持原样
> （回归：`test_duplicate_pair_keeps_both_motion_descs`）。本节只作为历史记录保留。

同一文件的 docstring 第 11 行写「`ff/motion` 取前镜（开场态），lf 取后镜（推进终点）」，
但实现是：

```python
if b.get("lf_desc"):
    a["lf_desc"] = b["lf_desc"]        # ✓ 与 docstring 一致
if b.get("motion_desc"):
    a["motion_desc"] = b["motion_desc"]  # ✗ 取后镜，与 docstring 相反
```

即 **ff 用 a 的开场态、motion 却写 b 的动作**，本身也不自洽。

**修法**：不再覆盖 `a["motion_desc"]`。a 无 motion_desc 时也**不回退**到 b——
空的 motion 是待上报的缺陷，用邻镜文本顶替等于拿另一个镜头的时间描述冒充本镜事实
（与 v6.75/v6.76 的 evidence-gated 原则一致）。

**验证不引入新阻断**（A/B 对比，同一合并镜分别取 a/b 的 motion 跑 `run_hard_fix`）：

| | HARD 未解决 | KF 只报告条目 | 告警总数 |
|---|---|---|---|
| 取前镜 a（改动后） | 1 | 3 | 10 |
| 取后镜 b（原实现） | 1 | 3 | 10 |

两者**完全一致**——因为 a、b 相似度 ≥0.85、时间分段结构相同，合并后 8s 都超出
motion 的 0-4s 覆盖，`kf_time_window_alignment` 在两种取法下都报同一件事。

新增回归测试 `test_merged_shot_takes_front_shot_motion_desc`（fixture 相似度实测
0.886 ≥ 0.85，确保真的走合并分支）。

### v6.77：motion_desc 时间解析统一 —— 已完成（16 处）

**动因**：§6.10 的 16 处解析点里，两组分隔符**互不覆盖**（`至/到` 只有一半认，
全角 `－/～` 只有另一半认），且剥标签正则不认小数——`0-0.378378秒：…` 被剥成
`.378378秒：…` 残片，经 kf_time_window_alignment 写进 `keyframes[*].desc`
（实测 chapter_001 index 11.5 / 12.5）。

**做法**：全部收敛到 `shared/motion_segments`（唯一事实源）。

| 原解析器 | 处置 |
|---|---|
| `validation._parse_motion_time_segments` | 薄委托 `parse_segments(implicit_tail=True, clamp=True)` |
| `validation._split_motion_to_seg_texts` | 薄委托 `split_segment_texts` |
| `validation._KF_TIME_SEG_PATTERN` | 删除，别名指向 `TIME_SEG_RE` |
| `timecode._rewrite_motion_time` | 用 `strip_time_label` + `fmt_time` |
| `timecode._split_motion_desc_at_cut` | 用 `atoms` + `atom_range` |
| `timecode._extract_motion_duration` | 薄委托 `extract_total_duration` |
| `split_continuity._rebase_motion_times` / `_match_segment` | 薄委托 |
| `motion_time_bounds._RANGE_RE` / `_SINGLE_RE` | 删除，改用 `TIME_SEG_RE` + `SINGLE_TIME_RE` |
| `kf_event_order._RANGE_RE` / `_segments` | 删除本地正则，改用统一解析 |
| `quality_issues`（区间支 + 提及支） | 改用 `TIME_MENTION_RE` |
| `validation._TIME_SEGMENT_RE`（:2331） | 改用 `mentions_duration`（含 共/时长N秒 声明） |
| `validation._TIME_SEG_RE`（:2355 局部 shadow） | 改用 `TIME_SEG_RE.finditer` |
| `validation._COMBAT_TIMING_PATTERN`（:3038） | 改用 `TIME_MENTION_RE` + `TIME_SEG_RE` |
| `weapon_state_consistency._SEG_RE` / `_motion_segments` | 改用 `atoms` + `atom_range` + `strip_time_label`（保留「无标签段 → (0, inf)」的调用方语义） |

顺带**合并了两处重复实现**：`motion_time_bounds` 与 `quality_issues._action_time_issues`
原先各写了一遍「文本提到的最大秒数」提取逻辑，现统一为 `max_mentioned_second()`。

**顺带解除了反向耦合**：`shared/motion_segments` 此前反向 `import step01_script_gen.validation`
（step02 用它 = step02→step01）。现在依赖方向是单向的。

**等价性验证**（迁移前，真实语料 + 合成边界共 29 例逐条对比新旧实现）：
每一个差异都是**预期修复，零回归**——

| 用例 | 旧 → 新 | 性质 |
|---|---|---|
| `至` / `到` 区间 | `[]` → `[(2,4)]` | 分隔符并集 |
| 全角 `～` 估时长 | `None` → `4.0` | 同上 |
| real idx=11.5 / 12.5 | `.378378秒：…` → `…` | **残片修复** |
| `前1.5秒：起手` | 未剥标签 → `起手` | 小数剥标签修复 |
| `0-3秒…3-9秒`（夹取） | `['A','']` → `['A','B']` | 旧实现拿未夹的 9 比已夹的段 → 丢文本 |

**时间标签不再出现长小数**：`rebase_motion_times` 现在输出 `0-1.38秒`
（原先 `:g` 给 `0-1.621622秒`）；`fmt_time` 统一截 2 位小数。

**新增测试**：`tests/shared/test_motion_segments.py` +29 例（含 `.378378` 残片回归守卫）。
全仓 `1496 passed`。

### 遗留（未做） —— 已于 v6.79～v6.82 收口，逐条对照见下

> **v6.79～v6.82 复核更正**：本节列出的「未做」现已全部有确定处置——
> 另 4 个 `_Final` 已删（理由改为顺序依赖，见上）；`kf_time.py` 惰性重标定已删除
> 并补契约测试（`tests/shared/test_kf_time_contract.py`，发现 8）；
> `duplicate_shot_merge` 的合并路径与其覆盖缺口一并消失（不再合并）。
> 以下原文保留作历史记录。

- 另 4 个 `_Final` 待各自基类降级后再删。
- `shared/kf_time.py` 惰性重标定「唯一触发点是 duplicate_shot_merge 的非对称写入」
  这一结构性问题未根治（第二阶段 `duration`/`duration_sec` 双字段清理范围）。
- `duplicate_shot_merge` 合并后 duration 翻倍（a+b）而 `motion_desc` 只覆盖前半段
  —— 覆盖缺口由 `kf_time_window_alignment` 上报，属预期行为；根治需第二阶段
  把 duration 改为由事件时间轴推导。

### 下一阶段前建议先补的测试基础设施

见「可测性分级」：`payload_signature()`（字节级一致性）、`tests/**/fixtures/`
落盘 `chapter_001` 基线、修复轮逐镜 revision 快照通道。

### v6.78：第二 / 三 / 四阶段落地 —— 已完成

#### 第二阶段：`temporal_plan` 事件表

- `shared.motion_segments.derive_temporal_plan(shot)` 产出带**事件身份**的结构
  （`event_id` / `start_sec` / `end_sec` / `description` / `source_quote`）
- **LLM 直出优先，不合格严格回退派生**：`validate_events()` 做严格校验
  （窗口越界 / 重叠 / 倒序 / 空描述一律拒），拒了就回退 `motion_desc` 派生
  并**上报原因**——遵循「格式严格、无歧义的才迁移；解析失败报 regen，不能猜」
- `_SHOT` / `_HOOK` 新增 `temporal_plan`（**非必填**，与 `duration_sec` 同级）；
  system.md / main.md 同步声明。`event_id` 由管线按 index 分配，模型不产出
- **为什么非必填**：强制会让 `source_hash` 缓存立即全量失效、且需要重跑基线验分，
  而这属无法离线验证的 prompt 变更。逐章积累「LLM 事件切分是否可靠」的证据后再收紧

#### 第三阶段：拆镜改为按事件边界

| 变更 | 说明 |
|---|---|
| 切点来源 | `cut_t` 由「旁白字符比例 × 原时长」改为 **`temporal_plan` 事件边界**（新增 `_event_boundary_cut`）。字符位置与事件时刻本无关系——实测 `cut_t=1.621622` 而 motion 分段在 2/4/5 秒，这正是「时间错配」的根因 |
| 无边界时 | **不拆**，打 `[SPLIT-BLOCKED]` 上报（原实现退化到「只要左段 ≤ cap」硬切） |
| 伪造文本 | 移除「从首帧推进到切分状态，完成：\<lf_desc\>」——那是把首尾帧描述冒充动作过程 |
| 语义注入 | 移除 `differentiate_split_visuals` 的**过肩视角 / 情绪递进 / 焦点轮换**——拆镜只应改变镜头语言，不该替故事决定「谁的视角、什么情绪、聚焦谁」。保留景别推近与 ff≈lf 推镜兜底（镜头层） |
| 重复调用 | 同一函数内被调两次的 `differentiate_split_visuals` 删掉一处 |

> **保留 E2b 的说明**：它把 lf 推近后与近景构图矛盾的「X位于画…」全景站位从句剥除。
> 这是**构图一致性维护，不是编造内容**；移除它会让拆出的两半相似度过高，
> 从而被拆镜自身的相似度校验回滚——等于拆镜功能失效。故把它从已移除的「焦点轮换」
> 下**移入** ff≈lf 兜底分支。

#### 第四阶段：修复轮改为完整镜头事务

| 旧 | 新 |
|---|---|
| `REPAIR_PATCH_SCHEMA`：字段级 `patches[]` | `REPAIR_SHOT_REPLACEMENT_SCHEMA`：`shot_replacements[]`，`shot` 复用 `_SHOT` |
| LLM 可只补想到的字段 → 结构字段互不一致 | **结构字段成套出现由 schema 保证** |
| `_apply_repair_patches` 逐字段定位 | `_apply_shot_replacements` 校验后**原子替换**；任一镜不通过**整条丢弃**（不做部分应用） |
| 接受判据只看聚合 blocking 身份 | 新增 **`non_target_changed`**：未被点名的镜头语义 revision 一变即拒 |

新增 `_validate_replacement_shot`：结构字段齐全 / index 不可改 / duration 合理 /
kf 的 stage 与 t_sec 合法 / `frame_layouts.kfs` 与 `keyframes` 等长 /
旁白不得暴涨（原文引用越界的代理判据）。

旧 patch 路径**保留但已不接线**，作为回滚兜底（确认新路径稳定后可整体删除）。

### 测试基础设施（本次补齐）

| 前置 | 落地物 |
|---|---|
| 字节级一致性比较 | `shared.quality_gate.semantic_payload_json()` / `payload_diff()` |
| `chapter_001` golden 基线 | `tests/step01_script_gen/fixtures/chapter_001_semantic_payload.json` |
| 修复轮逐镜 revision 快照 | `shared.quality_gate.per_shot_revisions()` / `changed_shot_keys()` |

golden 测试实测：

```
连跑两次 hard-fix 字节级一致        True    ← 验收条件 #1
KF 语义相对原始 fixture 未变        True    ← 验收条件 #7「不得自动改写」
payload 全部差异                    1 条（hook.clip_transition 转场补全，非 KF/时间语义）
结构化事件检查告警                   3 条（镜 1/2 重复采样 + 镜 12.5 缺帧），精确命中
```

### 约束表清理

7 个 `_Final` 重复注册实例**全部删除**（`ALL_CONSTRAINTS` 92 → **85**，无重名）：

- v6.76 删 3 个（基类已降为只报告）
- v6.78 删 4 个（基类仍是写入者，但**实证**链尾再跑一次不产生任何变化）——
  实证方法：造一个同时触发三个基类、且让 `frame_anchor_rebuild` 也实际改动
  `kf.desc` 的镜头，逐个摘掉 `_Final` 跑 `run_hard_fix`，输出**字节级相同**，
  改动明细显示「基类改动了 / `_Final` 均未改动」

---

## 原待确认事项（已于同步代码审核定案）

> **v6.79～v6.82 落地状态**：三项决定均已落地或已在代码里显式标注——
> 决定 1（`temporal_plan` 目标必填、当前只读）：字段可选 + 严格校验（整镜覆盖 /
> grounding / motion 一致）+ 已纳入 semantic revision（发现 10/11），必填切换待
> 真实模型跑批后发布；决定 2（保留整镜 replacement、禁止回退字段补丁）：旧
> `_apply_repair_patches` / `REPAIR_PATCH_SCHEMA` 保留但在 docstring 里显式标注
> **禁止运行时回退**，跑批通过后删除；决定 3（拆镜前移、停用 timecode 自动拆镜）：
> `_compute_timecodes` 默认 `allow_split=False`，生产入口不再自动拆镜，
> 超 cap 镜走 blocking + 结构 regen。

原 5 项中 4 项已落地（见上「v6.78」）。其余事项已在
`step01-temporal-writers-implementation-review-2026-09-19.md` 的
“对原审计‘待确认事项’的决定”中定案；以下保留问题背景并同步决策，不再表示开放选择。

### 1. `temporal_plan` 由「可选」收紧为「必填」

现状：schema 已声明该字段但**非必填**，LLM 给了就用（严格校验）、没给或不合格
就回退派生。这是**刻意的迁移态**——避免在没有真实跑批证据的情况下强制一个
无法离线验证的 prompt 变更（会立即全量失效 `source_hash` 缓存、且需重跑基线验分）。

**决定**：目标契约收紧为必填；当前版本先保持可选并改成只读审计，禁止未经完整验证的
plan 驱动拆镜或其他结构写入。完成整镜覆盖/连续性/grounding/motion 一致性校验、纳入
semantic revision、升级规则版本和旧缓存迁移后，再对 `_SHOT` / `_HOOK` / replacement
schema 一次性设为 required。模型跑批决定启用时机，不改变 fail-closed 原则。

### 2. 修复轮新契约的**真实模型表现**未验证

`shot_replacements`（完整镜头）的 schema 与 prompt 已改，管线端到端接线完成，
单元测试覆盖了校验/原子替换/revision 隔离。但**模型在真实调用下能否稳定产出
合规的完整镜头**，只能通过跑批验证——不跑 API 无法离线判定。

**决定**：保留完整镜头 replacement 作为唯一演进契约，禁止生产运行时自动回退旧字段
patch。新契约不合格时保留原镜并走 `WARN/BLOCK + full regen`。旧 helper 仅短期用于差异
测试，完成 hook 类型、duration 所有权、整批原子性和最终字段 mask 后跑真实样例；通过后
删除旧 schema/helper。

### 3. 拆镜「移到 KF 生成前」的 LLM 侧尚未做

文档第三阶段还要求「两个子镜**各自完整生成** ff/motion/KF/lf/frame_layouts」
（即拆镜发生在 LLM 生成阶段，而不是 timecode 阶段对已有产物切分）。
本次完成的是**切点正确性**（事件边界 + 不伪造），但仍在已有详细镜头上拆分。

**决定**：生产 timecode 阶段停用自动拆镜；超 cap 镜头作为 blocking issue 进入结构 regen。
目标流程增加 shot-plan 阶段，先生成 narration、稳定 shot/event ID、`temporal_plan` 和
duration，完成 cap 校验与拆分后再生成 ff/motion/KF/lf/frame_layouts。现有启发式拆镜只
保留作旧数据诊断，不能继续作为生产语义写入者。
