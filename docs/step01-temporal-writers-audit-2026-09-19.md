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


`motion_desc` 时间段目前有 **11 套互不一致的解析器**：

| 函数 | 位置 | 支持小数 |
|---|---|---|
| `_rewrite_motion_time` | `timecode.py:93-107` | 是 |
| `_split_motion_desc_at_cut` | `timecode.py:110-152` | 是 |
| `_extract_motion_duration` | `timecode.py:475-514` | 是 |
| `_KF_TIME_SEG_PATTERN` | `validation.py:6478-6481` | **否** |
| `_parse_motion_time_segments` | `validation.py:6484-6530` | 否 |
| `_split_motion_to_seg_texts` | `validation.py:4788-4852` | **否** |
| `_rebase_motion_times` | `split_continuity.py:15-36` | 是（`:g` 输出） |
| `_RANGE_RE` | `motion_time_bounds.py:16-19` | 否 |
| `_segments` | `kf_event_order.py:31-62` | 否 |
| `_action_time_issues` | `quality_issues.py:141-142` | 是 |
| `final_quality_normalizer` | `final_quality_normalizer.py:116-117` | 否 |

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

### 第一阶段：停止继续制造时间语义 —— 已完成

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

### `_Final` 重复注册：只删了 3 组，另 4 个实例**有意保留**

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

### 遗留（未做）

- `duplicate_shot_merge.py:125` 的 `a.pop("keyframes", None)`（整份删除）未处理——
  它不在点名的 5 个写入者里，且属"删除"而非"写入"，需单独评估。
- `motion_desc` 的 11 套解析器未统一（第二阶段范围）。
- 另 4 个 `_Final` 待各自基类降级后再删。

### 下一阶段前建议先补的测试基础设施

见「可测性分级」：`payload_signature()`（字节级一致性）、`tests/**/fixtures/`
落盘 `chapter_001` 基线、修复轮逐镜 revision 快照通道。
