# step01 时间语义落地实现代码审核（2026-09-19）

- 审核基准：`docs/step01-temporal-writers-audit-2026-09-19.md` 的“落地进度”
- 同步复审：2026-09-20 00:20（Asia/Shanghai），已纳入显式 KF 缩放、拆镜语义注入收敛、七个 `_Final` 全部删除、完整镜头修复协议接线及 LLM `temporal_plan` 接线
- 审核范围：v6.76～v6.78 涉及的约束注册、时间轴计算、相邻镜合并、统一时间解析器、修复轮接受判定及新增回归
- 审核方式：当前工作树静态审查、最小复现、定向测试与全仓测试
- 运行结果：最新增量 `py_compile` 通过；整镜替换、双 prompt profile 与 temporal 定向回归合并运行 `116 passed`。最近一次全测试运行因 pytest 临时目录权限产生 `92 errors`，当时另有 2 个 prompt 回归；后者已由后续增量修复并通过定向测试，因此当前没有一份无环境错误的最新全量结果
- 代码改动：本次仅新增本审核文档，未修改运行代码

## 结论

本批实现完成了以下局部目标：

1. `kf_monotonic`、`kf_t_sec_bounds`、`kf_timeline_normalize`、
   `kf_time_window_alignment`、`kf_pre_post_conflict` 已改为只报告；
2. `hook_duration` 不再补造时长和固定 `t_sec=3.5` 的关键帧；
3. 七个 `_Final` 重复实例已删除；其中三个只读实例的删除成立，四个写入型实例仍需按下文的顺序依赖复核；
4. motion 时间语法的主要消费点已集中到 `shared.motion_segments`；
5. `chapter_001` golden case 的 hard-fix 幂等测试能够稳定通过。

但当前仍不能把“第一阶段：停止继续制造时间语义”标记为完整完成。仓库内仍有未纳入清单的 `t_sec` 写入者和多个独立 `duration_sec` 写入者；部分写入发生在 KF 时间检查之后。最新改动已把 `duplicate_shot_merge` 的 KF 缩放显式落盘，解决了旧惰性重标定在最终归一时消失的机械问题，但这个缩放仍没有事件级证据，反而成为新的链尾 `t_sec` 写入者。更关键的是，新引入并被拆镜消费的 `temporal_plan` 没有进入 semantic revision，现有 quality gate 对事件表的单独变化完全失明。定向测试通过只能说明已覆盖样例通过，不能证明最终数据流满足文档声明的不变量。

## 修复状态（2026-09-20 收口，v6.82）

> 本节的结论取代下文各发现的**开放状态**。发现正文保留原始证据与最小复现（它们
> 仍是这些不变量存在的理由），但阅读时请以下表为准：表中「已落地」= 代码 + 回归
> 均已存在；「跑批验证」= 需要真实模型调用才能确认，本地无法完成。

| # | 发现 | 状态 | 落地位置 |
|---|---|---|---|
| 1 | 时间写入者未收敛，链后写入可撤销链前检查 | **已落地** | 四个链尾写入者 v6.79 降为只报告；`shared/semantic_barrier.py` 三段模型（`AUDIT_SEGMENT_START`）；`main.py` 落盘前 final pass 接入运行时守卫（报告-only，越权写入落 `_time_field_write_audit`）；`tests/shared/test_semantic_barrier.py` + golden 零写入断言 |
| 2 | `duplicate_shot_merge` 删除有独立语义的镜头 | **已落地** | 整体降为只报告（不删镜头/不拼 narration/不求和/不缩放 KF），检出即 HARD + 交整镜 regen；`test_structure_fixes_0912.py` 含高 Jaccard 反例（sim 0.877 仍零改写） |
| 3 | 修复轮重算全部时长；目标镜后处理变化不受 mask 约束 | **已落地** | 修复轮改调 `write_timeline_from_durations`（纯序列化）；`_final_field_mask_violations` 在**全部后处理结束后**比较；增删镜头由 `changed_shot_keys` 判出；`test_timecode_split_gate.py` 文档同步 |
| 4 | 解析器把无单位数量范围当时间，并能产生反向区间 | **已落地** | `TIME_SEG_RE` 分支 3 加「段首 + 尾部量词」双保护；`extract_total_duration` 改走唯一判定点 `raw_atom_span`；完全越界段丢弃且由 `out_of_window_atoms` 可观测；裸范围保留兼容但由 `bare_range_atoms` 上报迁移 |
| 5 | 事件边界与「重复采样」契约不明确 | **已落地** | `event_of` 改半开区间 `[start, end)`（末段闭合）；重复采样契约三处对齐（docstring / 告警文案 / 实现）＝「同事件内允许不同采样点，只有近似重合才算重复」；边界归属锁定到具体事件 |
| 6 | 四个写入型 `_Final` 的删除依据不成立 | **已落地** | 注释改为如实的**顺序依赖**结论（`constraints/__init__.py`）；`kf_blood_monotonic` docstring 声明「只表示伤情连续，不是死亡姿态证据」；交换律反例 + 另外三个实例的**链级不变式**回归（`test_constraint_order_dependency.py`：群体主体/血状态单调/陈旧姿态分句在最终对象里均成立，作用域诚实声明为「仅对这些触发 fixture 成立，不是全输入等价证明」） |
| 7 | 拆镜按相似度删除角色布局事实 | **已落地** | 删除「X位于…」分句的逻辑已移除；`test_split_shot_differentiation.py` 补三类事实（位置/道具/姿态）**守恒**回归 |
| 8 | `kf_time` 公共契约与文档互相矛盾 | **已落地** | 模块 docstring 与 `get_kf_t_sec` docstring 已改写为「读取期不缩放」并写明迁移后果；`tests/shared/test_kf_time_contract.py` 锁定两条分支 |
| 9 | 整镜替换事务边界漏掉后处理语义写入 | **已落地** | hook 与普通镜头 schema 分离（`hook_replacement`）；`duration_sec` 单一口径（仅由序列化派生）；整批全有或全无；最终字段 mask 在事务末尾比较 |
| 10 | 未覆盖整镜、无原文证据的 LLM `temporal_plan` 仍可成为拆镜权威 | **已落地** | `validate_events` 补整镜覆盖（首段起 0 / 末段到 duration / 相邻无空洞）、`source_quote` 非空、与 motion 分段一一对应；`_check_temporal_plan` 增原文 grounding 与越界段上报；`_event_boundary_cut` 拒绝 LLM plan；6 类对抗 plan 均回退派生 |
| 11 | `temporal_plan` 不属于 semantic revision | **已落地** | `_SEMANTIC_SHOT_FIELDS` 纳入 `temporal_plan` + `_canonical_temporal_plan`（只哈希 `event_id/start_sec/end_sec/description/source_quote`，浮点 round 3 位）；`QUALITY_RULE_VERSION` 升级使旧 audit 失效；`tests/shared/test_quality_gate.py` 覆盖描述/边界/出处/顺序/身份**五类变化必须失效**，诊断元数据与浮点噪声**不得失效** |

**建议实施顺序 1-5**：全部落地。**未完成的只有一类**：

- **真实模型跑批验证**（决定 1 第 5 步、决定 2 第 1-4 步、本表发现 1 的端到端效果）——
  需要 API 凭据与真实调用，离线不可完成。跑批前请先确认：`temporal_plan` 首轮结构
  合格率 ≥95%、一次定向 regen 后 100%；安全闸值为「错误接受 0 / 未点名镜头变化 0 /
  事务外语义写入 0」。任一不达标时行为是 `WARN/BLOCK + full regen`，
  **不得回退字段级 patch**。

**测试审核 12 项不变量的覆盖**：1（重复视觉只报告）、2（`short_shot_merge` 等写入者已消失，由阶段屏障守卫持续断言）、3（修复轮时长/mask/revision 三重不变）、4（裸范围与完全越界拒绝）、5（高 Jaccard 反例零改写）、7（顺序依赖交换律）、8（拆镜事实守恒）、9（`get_kf_t_sec` 兼容契约）、10（整镜替换端到端）、11（plan 准入与派生回退）、12（revision 失效矩阵）**均已覆盖**。
第 6 项「`frame_layouts.kfs` 与 KF 的**逐帧语义**一致（不只是长度相等）」仍**未覆盖**：
当前只断言长度相等，逐帧的角色/道具/状态一致性仍靠 `frame_spec_consistency` 与
LLM regen 兜底——这是下一个可做项，需要先定义「layout 与 KF 语义一致」的可判定判据。

## 发现 1（P0）：时间写入者并未收敛，链后半段仍可改写已审计的时间语义

### 证据

`ALL_CONSTRAINTS` 当前的关键顺序为：

```text
8  kf_time_window_alignment
9  kf_timeline_normalize
10 kf_t_sec_bounds
11 kf_monotonic
36 runtime_extend
57 short_shot_merge
62 motion_time_bounds
78 duplicate_shot_merge
80 duration_budget
81 final_quality_normalizer
```

五个 KF 检查在前，以下写入发生在它们之后：

- `constraints/short_shot_merge.py:47-52`：无新增事件证据时把短镜 `duration_sec` 拉到 3.5 秒；
- `constraints/motion_time_bounds.py:52-60`：按文本提及的最大秒数延长 `duration_sec`；
- `constraints/duplicate_shot_merge.py:129-143`：时长求和，并按 `new_duration / old_duration` 显式缩放前镜全部 `keyframes[*].t_sec`；
- `constraints/duration_budget.py:108-123`：按比例缩短多个镜头的 `duration_sec`。

此外，仓库仍有直接 `t_sec` 写入：

- `constraints/split_continuity.py:137-147` 会在重写 split shot 的 `motion_desc` 后直接平移 `keyframes[*].t_sec`；
- `timecode.py:216-244` 的常规拆镜会按旁白字符位置估算 `cut_t`，深拷贝并重写后半镜 KF；
- 修复补丁入口 `main.py:1460-1484` 仍允许 `keyframes.append` 和任意 `keyframes[N].t_sec` patch。该入口属于 LLM 事务，不是确定性约束，但说明 `t_sec` 仍没有唯一所有者。

`runtime_extend` 的单镜上限夹取位于这些写入者之前，因此不能保证最终上限。现有测试
`test_duplicate_visuals_merged` 还明确断言两个镜头合并后得到 `duration_sec=12.375`，超过项目 8 秒硬上限。链尾 `redguo_standard` 只产生 warning，且该类别不在 `_QUALITY_BLOCKING_CATEGORIES` 中；这个上限问题本身不会成为质量门的 blocking issue。

`motion_time_bounds` 也只使用模型 cap，没有再取项目 8 秒上限的较小值。当活动模型允许 12 秒、动作文本提到第 10 秒时，它可以在 `runtime_extend` 之后把镜头重新扩到 10 秒。

### 影响

- 同一 run 内，KF 检查看到的 duration 不是最终 duration；“审计通过”不代表最终镜头时间轴通过同一组检查。
- 上限夹取可以被后续写入撤销，最终脚本可带单镜超 cap 时长进入落盘和下游 gate。
- “五个点名文件零赋值”只是局部 grep 结论，不能证明时间字段只有一个写入者。

### 根因

本次修复以文件清单为边界，而没有建立字段所有权和阶段屏障。约束引擎仍是“按注册顺序边检查边改写”的单遍流水线；任何链后写入都能使链前检查失效。

### 实施方案

1. 建立显式的 writer registry 或测试期写入跟踪，对 `shots`、`duration_sec`、`duration`、`keyframes[*].t_sec`、`motion_desc` 做字段级快照；每个约束执行后记录实际 diff。
2. 把约束链拆成三段：
   - 结构生成/经批准的结构变更；
   - 一次性时间派生；
   - 纯审计与 gate。
   审计段开始后禁止再写上述时间字段。
3. `short_shot_merge` 改为只报告并请求补内容/完整镜头重生，不能再通过加秒数“修复”。
4. `motion_time_bounds` 改为报告动作超窗；只有权威事件表确认时才允许从事件末端派生 duration，并同时受 `min(项目上限, 模型上限)` 约束。
5. `duration_budget` 不再直接按比例压缩镜头；输出需要重写/拆分的镜头集合，交由完整镜头事务处理。
6. 在最终语义审计前只执行一次 `write_timeline_from_durations`，随后运行所有 KF、单镜 cap、总时长检查；此后禁止语义写入。

## 发现 2（P0）：`duplicate_shot_merge` 仍会删除有独立语义的镜头，并把无事件依据的 KF 缩放显式落盘

### 证据 A：Jaccard 相似不等于事件等价

合并条件是同地点、说话人兼容、`motion_desc + prompt + lf_desc` 的字符 bigram
Jaccard ≥ 0.85（`duplicate_shot_merge.py:108-118`）。长篇共同的场景、服装和构图文本会淹没很短但关键的结果差异。例如两镜只有结果分别为“刀尖停在胸前”和“收刀放人”，补上相同的长环境锚后，实测相似度为 `0.8872`，会进入自动合并。

合并后代码保留前镜 `motion_desc`，却取后镜 `lf_desc`（`duplicate_shot_merge.py:127-137`），因此上述例子会变成：动作仍是“刀尖停在胸前”，尾帧却是“收刀放人”。后一镜的关键事件和 KF 被删除，没有事件级证据证明它是冗余内容。

### 证据 B：显式缩放解决了持久化症状，但没有解决事件归属

最新实现已采用文档选项 (c)：合并时把 `duration` / `duration_sec` 都写成新时长，
并把前镜所有 KF 直接乘以 `new_duration / old_duration`。对应回归也更新为断言
`[1.0, 3.0] -> [2.0, 6.0]`。这解决了上一轮指出的“惰性比例在最终 timeline
归一后消失”，该机械缺陷现已关闭。

但缩放本身仍是假设，不是事件证据。最小复现中，前镜为：

```text
duration=4
motion_desc=0-2秒：举刀；2-4秒：收刀
KF=[1秒举刀, 3秒收刀]
```

与一个 4 秒相似镜合并后，代码保留上述 `motion_desc`、把 duration 改成 8 秒，并把
KF 改为 `[2, 6]`。`derive_temporal_plan()` 得到三个窗口：`0-2 举刀`、`2-4 收刀`、
`4-8 空白尾段`；当前双端闭区间规则又把 `t=2` 归到第一个事件，因此映射结果为：

```text
2秒 -> 举刀事件
6秒 -> 空白尾段
收刀事件 -> 无 KF 覆盖
```

也就是说，显式落盘把“收刀”KF 从其原事件移动到了没有正文的隐式尾段。代码没有同步
`motion_desc`、没有保留后镜事件/KF，也没有验证缩放后的事件覆盖。

测试仍只证明 KF 和 layout 数量相等。`FrameSpecRepairConstraint` 用最后一个 layout
补齐第二项，不能证明该 layout 与 6 秒处的 KF 描述、角色状态或道具状态一致。

### 影响

- 高相似但结果不同的连续镜可能被静默删除并拼成自相矛盾的单镜。
- 合并镜的 KF 仍集中在原前半段，后半段缺少动作和关键帧覆盖。
- 合并可制造单镜 >8 秒，且发生在单镜 cap 约束之后。

### 根因

`duplicate_shot_merge` 把“文本相似度”当成“事件集合相同”的证明，又把“时长扩大”
直接等同于“所有事件按比例变慢”。镜头拼接实际应保留两个镜头的事件序列，不能用
比例缩放前镜事件冒充后镜内容。

### 实施方案

1. 立即把自动 merge 降为 `warn + regen`；在没有事件身份前，不删除镜头、不拼接 narration、不求和 duration。
2. 第二阶段引入 `temporal_plan.events[]` 后，仅当两个镜头的事件 ID 集合、原文证据、角色/道具状态转换均可证明等价时，才允许确定性去重。
3. 真正的合并应生成一个新的完整镜头事务：新的 ff、motion、KF、lf、frame layouts 与 duration 一次性提交；禁止复用“前镜 motion + 后镜 lf + 前镜 KF”的字段拼盘。
4. 增加端到端测试：`merge -> write_timeline_from_durations -> final quality checks -> persisted payload`，断言单镜 cap、每个事件覆盖、KF 物理时刻和 layout 语义，而不是只测中间对象长度。

## 发现 3（P1）：修复轮 `allow_split=False` 只禁止拆镜，仍会重算所有镜头时长，目标镜头的后处理变化不受字段 mask 约束

### 证据

`_compute_timecodes(..., allow_split=False)` 仅跳过 `timecode.py:730-745` 的拆镜 pre-pass；后续仍执行完整估时逻辑，并在 `timecode.py:769-806` 覆盖每个 shot 的 `duration` 和 `duration_sec`。最小复现中，输入镜头 `duration=duration_sec=4`、`motion_desc="0-6秒：动作"`，调用后变成 6 秒。

修复轮在 `main.py:2293` 先计算 `_locked_after_patch`，随后才执行
`_compute_timecodes(..., allow_split=False)` 和全量 `run_hard_fix`。最终
`locked_changed` 比较的是 LLM patch 直接造成的变化，不包含这些确定性后处理对 duration
和目标镜头其他字段的副作用。最新代码已用 `per_shot_revisions()` 拒绝未点名镜头变化，
所以旧结论中“看不见其他镜头变化”的部分已经修复；但被点名镜头的事务外变化仍会随整轮接受。

这与 prompt 的“duration_sec 是 Mask 字段”和文档“只重建 timeline”不一致。新增
`test_allow_split_false_still_rebuilds_timeline` 反而明确允许 duration 重算，只验证有值，没有验证原值或未选镜头的逐镜 revision 不变。

### 影响

- LLM 只修改一个允许字段（尤其 `motion_desc`）时，修复轮可以顺带改变该镜及其他镜的 duration。
- 接受判定会把目标镜头上由后处理造成的语义变化排除在 locked-field 审核之外。
- 未点名镜头 revision 守卫虽已接入，但 `temporal_plan` 不在 revision 中，仍存在发现 11 所述盲区。

### 根因

`_compute_timecodes` 同时承担“估算语义时长”“拆镜”“写 duration”“序列化 timeline”四种职责。修复轮需要的只是最后一种，却只能通过布尔参数关闭其中一个副作用。

### 实施方案

1. 修复轮用已经存在的 `write_timeline_from_durations()` 重建 timeline，禁止调用 `_compute_timecodes()`。
2. 如果 patch 修改了 `motion_desc` 且必须重新估时，把 duration 变化作为同一镜头完整替换事务的一部分显式提交和审核，不能当作后处理副作用。
3. 在 post-fix、hard-fix、timeline serialization 全部完成后再取 locked signature。
4. 保留已接入的 `shared.quality_gate.per_shot_revisions()` 守卫，并按发现 11 补全其语义字段；新增/删除镜头也必须拒绝。
5. 新增回归：一个被选镜头只改 `prompt`，另一个未选镜头带非规范 duration；修复轮结束后两个镜头的 duration 与未选镜头 revision 必须保持不变。

## 发现 4（P1）：统一解析器仍把无单位数量范围当时间，并能产生反向区间

### 证据

`shared/motion_segments.TIME_SEG_RE` 为兼容 `2-5放下酒壶`，允许没有“秒/s”的裸范围，并使用 `search()` 在整个原子段内查找。负向尾部只排除了少量字符，未排除“人、名、步、次”等常见量词。

当前实测：

```text
parse_segments("画面中1-2人向前走3-5步", 6.0) -> [(1.0, 2.0)]
extract_total_duration(同文本) -> 5.0
```

更严重的是 `extract_total_duration()` 没有复用 `TIME_SEG_RE` 的尾部保护，而是在同一模块内又维护了一条更宽的裸范围正则（`motion_segments.py:162-168`）。这使“统一事实源”仍有两套语法判定，并直接影响 `_compute_timecodes` 与 `duration_budget`。

区间夹取也只执行 `b = min(b, total_sec)`，没有夹取或拒绝 start。当前实测：

```text
parse_segments("9-12秒：A", 4.0) -> [(9.0, 4.0)]
```

这个反向事件会进入 `derive_temporal_plan()`，但任何 KF 都不可能落入它。

### 影响

- 动作文本中的人数、步数、尺寸或次数范围可被误判为秒数，并进一步改写镜头 duration。
- 超出镜头边界的段可能生成 `start_sec > end_sec` 的非法事件；事件覆盖、排序和 KF 归属判断随之失真。
- 解析集中到了一个文件，但语法仍未形成一个经过验证的解析结果类型。

### 根因

为了兼容历史上省略单位的自由文本，解析器继续猜测裸数字范围；同时不同 API 仍各自用正则提取，集中模块没有等于集中语法。

### 实施方案

1. 权威时间只接受位于原子段开头、带 `秒/s` 的标签，或结构化 `temporal_plan`；裸范围只作为 legacy warning，不得驱动 duration/KF。
2. 如必须兼容 `2-5放下酒壶`，单独实现 legacy parser，要求段首、值落在合法模型范围、上下文不含量词，并返回 `confidence="legacy"`；只报告迁移，不参与确定性写入。
3. `extract_total_duration` 必须基于 `parse_segments` 的已验证结果，删除第二套范围正则。
4. 统一验证 `0 <= start < end <= duration`；完全越界的段报告错误，部分越界只能在明确策略下裁剪，裁剪后再次验证。
5. 增加人数/步数/尺寸/次数、完全越界、重叠段、空段、边界相接等对抗测试。

## 发现 5（P2）：结构化事件检查的边界和“重复采样”契约不明确

`event_of()` 对每个事件都使用双端闭区间并加 `±0.01` 容差（`motion_segments.py:283-289`）。相邻事件 `[0,2]` 与 `[2,4]` 在边界附近重叠；函数按列表顺序把 `t=2.0` 静默归给前一事件。现有测试只断言边界“非空”，没有锁定归属规则。

`_check_temporal_plan` 的文档写“同一事件窗口内 ≥2 个 KF 即重复采样”，告警文案也写“同段应有且仅有一个定格”，但实现只有在这些 KF 的总跨度小于
`0.05 * 数量` 时才报告（`validation.py:6624-6634`）。同一 0-2 秒事件内的 KF
`t=1.0` 与 `t=1.8` 不会报告；测试又明确把“同事件内不同采样点”定义为合法。当前契约三处互相矛盾。

建议先确定业务不变量，再实现：

- 事件窗口使用半开区间 `[start, end)`，最后一个事件允许闭合终点；
- 明确一个事件允许几个 KF。若允许多个，应按 `sample_id/state_id` 判断重复，不能再把“同事件”本身叫重复；若只允许一个，删除距离启发式；
- 对边界 KF 增加明确的归属测试，禁止依赖列表先后顺序。

## 发现 6（P1）：四个写入型 `_Final` 的删除不能由单一 fixture 证明等价；中段 blood 实例已有反例

### 证据

最新 `constraints/__init__.py:103-124` 已把七个 `_Final` 全部删除。三个基类已只报告的
实例确属重复检查；另外四个写入型实例则以一个“同时触发三个基类”的构造 fixture 中
`_Final` 未产生额外改动为依据。这个证明只覆盖该 fixture 的状态，不能证明所有输入下
的约束顺序等价。尤其是原中段 `_KfBloodMonotonicFinal` 已有确定反例。

旧顺序是在 `kf_event_order` 后先运行一次 blood monotonic，再运行
`v6_43_prompt_enforcement`；新顺序直到 `frame_anchor_rebuild` 后才首次运行 blood
monotonic。该约束不仅改 KF，还会把伤情锚追加到 `lf_desc`。而
`v6_43_prompt_enforcement._head_fix_context_ok()` 会读取 `lf_desc` 中的“伤处/暗红/洇痕”
决定是否追加“闭眼、颈部僵直”等结果态。两者不满足交换律。

最小复现：首个 KF 有“胸口暗红洇痕”，后续 KF 和 `lf_desc` 只有“仰卧甲板”。

```text
旧顺序 blood -> v6_43:
  lf_desc 同时出现伤情锚和“闭眼/颈部僵直”

新顺序 v6_43 -> blood:
  lf_desc 只有伤情锚，没有“闭眼/颈部僵直”
```

最终对象明确不相等。新结果可能更符合“不要凭伤情虚构死亡姿态”的方向，但这应作为
有意语义变更评审，不能登记为“实证 no-op”。

### 根因与实施方案

根因是用单一 golden/构造 case 的无差异，替代了约束交换律/依赖关系验证。应给约束注册表
建立读写集：blood monotonic 写 `keyframes.desc`、`lf_desc`，v6_43 读 `lf_desc` 并写
多个最终描述字段；删除或换序前必须对存在读写冲突的后继约束跑触发 fixture。

若业务决定接受新结果，应：

1. 把注释改为“有意移除中段伤情锚对 v6_43 的触发”，不要把四个写入型实例统一登记为“输出字节级相同”；
2. 增加上述触发回归，明确断言不再因后加伤情锚而补造闭眼/僵直；
3. 当前链尾保留的普通 `kf_blood_monotonic` 只表示最终伤情连续，不把它当成死亡姿态证据；
4. 分别为 `group_actor_continuity_final`、`kf_blood_monotonic_final2`、
   `frame_text_state_cleanup_final` 构造“基类之后的写入者重新制造缺陷”的 fixture；只有
   能证明不存在这种后继写入，或最终审计能拦截时，才可称全局冗余。

## 发现 7（P1）：拆镜已移除三类语义注入，但仍按相似度删除角色布局事实

### 证据

`split_visuals.differentiate_split_visuals()` 最新改动删除了过肩方向、情绪阶梯和焦点轮换，
这一部分符合 evidence-gated 原则。但 `split_visuals.py:137-160` 仍在 `ff≈lf` 时：

1. 给尾帧追加推镜；
2. 从 `narration_segment` 推断说话人；
3. 删除 `lf_desc` 中所有以“说话人 + 位于”开头的分句。

实测输入 `中景，张三位于画左，手持刀`，拆分后尾帧会删除“张三位于画左”。推近镜头
并不证明角色不再位于画左；这个位置还是 frame layout、轴线连续性和下一镜衔接需要的
事实。代码注释把它称为“构图一致性修复”，但没有检测该分句与近景是否真的互斥。

此外，模块顶层说明仍列出已经删除的 B/C 规则，`_MOOD_LADDER`、
`_char_inert_in_text` 也已无调用；文档和实现不同步会继续误导下一轮审核。

### 根因与实施方案

根因是仍以“降低文本相似度”为目标修改事实字段。相似度只是质量信号，不是删除位置
事实的证据。建议只保留景别/运镜词的结构化更新，不删除任何人物、道具、姿态、位置
分句；若 `ff≈lf` 仍高，交 `split_content_similarity` 报告并触发完整子镜重生。

增加回归：拆镜前后抽取人物位置/道具/姿态三类事实，断言集合不减少；现有测试只检查
没有继续注入“聆听反应”，没有覆盖位置事实被删除。

## 发现 8（P2）：本轮文档和 `kf_time` 公共契约存在互相矛盾的陈述

`shared/kf_time.py:28-37` 的模块说明和 `get_kf_t_sec()` docstring 仍承诺按
`duration_sec / duration` 做惰性重标定，但实现 `:94-101` 已删除该逻辑并直接返回原始
`t_sec`。原审计文档也同时存在以下互斥状态：

- “`duplicate_shot_merge` 的 KF 惰性摊开已修复”章节仍描述旧非对称写入；
- “遗留”仍说惰性重标定未根治；
- “待确认事项”仍写选项 (c) “未动手”，但代码和测试已经采用 (c)；
- “第一阶段已完成”没有反映显式 `t_sec` late writer 和本审核已证实的旁路。

这些不是运行时 P0，但会让后续实现者按已经失效的契约继续写代码。应在同一次变更中
更新模块 docstring、审计落地进度和遗留清单，并给 `get_kf_t_sec` 增加一条明确契约测试：
当 `duration != duration_sec` 时究竟返回原始秒数还是缩放秒数。当前实现选择前者，就应
把迁移策略写清楚，并评估旧缓存/旧 `script_result.json` 是否需要一次性升级。

## 发现 9（P1）：整镜替换协议已接通，但事务边界仍漏掉后处理语义写入

同步后的代码已经解决上一版审核所指出的“发送新协议、解析旧协议”：

- `main.py:2245-2252` 提交 `REPAIR_SHOT_REPLACEMENT_SCHEMA`；
- `main.py:2278-2282` 读取 `shot_replacements` 并调用 `_apply_shot_replacements`；
- `main.py:2367-2381` 用逐镜 revision 计算未点名镜头变化并传给接受判定；
- `tests/step01_script_gen/test_repair_shot_replacements.py` 已覆盖基础替换与拒绝路径。

这部分旧问题应关闭。但事务的真实边界仍不完整：`_locked_after_patch` 在
`_compute_timecodes(merged, allow_split=False)` 之前取值，随后还会运行
`post_fix_shot_prompts`、全量 `run_hard_fix` 和致命伤补锚。逐镜 revision gate 可以发现
未点名镜头的多数副作用，却不能证明被点名镜头只发生了本轮允许的字段变化；而
`_compute_timecodes(..., allow_split=False)` 本身仍会重算 `duration_sec`。也就是说，最终接受
的是“整镜替换 + 若干事务外写入”的合成结果，replacement 自身并非最终提交对象。

契约还剩三处缺口：

1. `shot_id` 允许 `"0"` / `"hook"`，但 replacement 的 `shot` 固定引用 `_SHOT`；真实 hook
   使用 `_HOOK`，两种对象的字段要求不同；
2. prompt 把 `duration_sec` 列入禁止修改字段，但 `_SHOT.required` 与
   `_REPLACEMENT_REQUIRED_KEYS` 都只强制 `duration`，之后仍由时间轴后处理补写；
3. `_apply_shot_replacements` 是逐条成功即提交到副本；同一轮两个相互依赖的目标中若一个
   失败、一个成功，会形成半批提交，注释中的“原子”只成立于单镜内部。

### 根因与实施方案

根因是把“对象替换原子性”当成了“修复事务原子性”，但时间轴、hard-fix 与 hook 类型契约
仍在事务之外。应按以下顺序收口：

1. 普通镜头和 hook 使用各自 schema；replacement 必须显式携带并校验 `duration_sec`，或
   明确只携带 `duration` 且由唯一纯派生函数生成 `duration_sec`，不能两种口径并存；
2. 在副本上完成整批 replacement、相邻边界与最终语义校验，跨镜修复整批全有或全无；
3. replacement 之后只调用不改镜头语义的 timeline 序列化函数；`post_fix`/hard-fix 若仍有
   必要，应在进入事务前运行并把问题交给 LLM，而不是在事务后补写；
4. 对每个目标保存“允许改字段 mask”，在所有处理结束后比较最终对象；未授权字段变化或
   未点名镜头变化都拒绝；
5. 增加一个端到端测试：两个目标一合法一非法、hook 替换、后处理试图改 duration、后处理
   试图改未点名镜头，四种情况都必须整体回滚或按明确的逐镜产品契约处理。

## 发现 10（P0）：LLM `temporal_plan` 在未覆盖整镜、无原文证据时仍会成为拆镜权威

最新 `_text_gen/deepseek_schema.py` 已给 shot/hook 增加可选 `temporal_plan.events[]`，
`shared.motion_segments.derive_temporal_plan()` 也会优先采用 LLM 事件表。这条迁移方向符合
单一时间事实源目标，但当前校验没有实现其自己声明的不变量。

`validate_events()` 的注释和 schema 描述要求事件“按时间升序、首尾相接、覆盖
`(0, duration]`”，实现却只检查：字段可转数值、description 非空、单段不越界、
`end > start`、不重叠/倒序。它没有检查：

- 第一段必须从 0 开始；
- 相邻事件之间不得有空洞；
- 最后一段必须到 duration；
- `source_quote` 必须能在原文中定位；
- LLM 事件与 `motion_desc` 的段数、边界和描述一致。

最小复现实测：10 秒镜头的真实 motion 为 `0-5 A；5-10 B`，LLM plan 只给
`2-3秒：编造事件，source_quote=""`。`validate_events()` 返回空串（通过），
`derive_temporal_plan()` 返回 `source="llm"`，`_event_boundary_cut()` 进一步选择 3 秒作为
拆镜边界。也就是说，一个无出处、覆盖率 10% 的事件表可以压过完整 motion 并改变镜头
结构；检查器只会在事后发 warning，不能阻止拆分。

### 根因与实施方案

根因是把“结构合法”误当成“证据充分”，并在迁移期让未验证的新事实源优先于旧事实源。
应先把 LLM plan 作为候选，满足以下条件后才升级为权威：

1. 严格验证 `start[0]=0`、`end[-1]=duration`、每个相邻边界在容差内相等；
2. 每个 `source_quote` 非空且能在本章原文中定位；允许无直接引文的镜头语言事件时，必须
   标记明确类型，不能伪装成剧情事实；
3. 与 `motion_desc` 做一致性检查：段数/边界/主体动作冲突时 blocking + regen，不能静默
   回退或任选一方；
4. 不合格 plan 的原因进入 validation/gate 报告，禁止只在函数内部丢弃；
5. 在迁移验收完成前，拆镜不得消费 `source="llm"` 的 plan；先用于只读对比统计，确认
   与原文和 motion 的一致率后再开放结构写入。

增加对抗测试：首段晚于 0、尾段早于 duration、中间空洞、空 source_quote、与 motion
边界冲突、与原文不存在的引文；这些输入都不得驱动 `_event_boundary_cut()`。

## 发现 11（P0）：`temporal_plan` 已成为时间事实源，却不属于 semantic revision

`shared/quality_gate.py:28-34` 的 `_SEMANTIC_SHOT_FIELDS` 没有 `temporal_plan`。
`compute_script_revision()`、`per_shot_revisions()`、修复轮的未点名镜头变化检测，以及下游
quality/audit revision gate 都基于这份白名单。与此同时，
`shared.motion_segments.derive_temporal_plan()` 会优先采用合法的 LLM plan，拆镜边界也会消费
这个结果。因此事件表已经能改变结构行为，却不改变任何审计身份。

最小复现中，只把 10 秒镜头的 `temporal_plan.events[0].description` 从“举刀”改成
“凭空倒地”，结果为：

```text
compute_script_revision(before) == compute_script_revision(after)  -> True
per_shot_revisions(before) == per_shot_revisions(after)            -> True
```

改变事件边界也有同样结果，但会改变后续 `_event_boundary_cut()` 的选择。这意味着已有
semantic audit 可以继续显示 revision 匹配，而实际用于拆镜的时间语义已经变化；修复轮的
non-target gate 也看不见对未点名镜头事件表的单独改写。

### 根因与实施方案

根因是 semantic revision 仍采用旧字段白名单，新事实源接入时没有执行“生产者、消费者、
revision、审计规则、持久化”五处同步登记。修复不能只在某个 gate 旁补一条特判：

1. 把规范化后的 `temporal_plan` 纳入 `_semantic_shot`；只保留会影响语义的稳定字段
   `event_id/start_sec/end_sec/description/source_quote`，不要哈希临时诊断信息；
2. 升级 `QUALITY_RULE_VERSION`，使旧 quality/audit 报告全部失效并重新生成；
3. 增加 revision 回归：只改事件描述、边界、出处或事件顺序，都必须改变 script revision
   和对应 per-shot revision；只改非语义诊断元数据则不得改变；
4. 修复轮和最终持久化前都用同一个 canonicalizer，禁止一处按原始 dict 哈希、另一处按
   派生 plan 消费；
5. 在完成发现 10 的 grounding/覆盖校验前，不得因为“已进入 revision”就把不可信 plan
   当成权威；revision 只能证明内容没变，不能证明内容正确。

上一版同步时发现的 prompt 字面花括号问题已由后续增量修复；DeepSeek/Doubao 双 profile
定向测试现为通过，不再作为当前缺陷保留。

## 对原审计“待确认事项”的决定

以下决定取代 `step01-temporal-writers-audit-2026-09-19.md` 原“待确认事项”中的开放状态。
三项都已有足够的代码证据作架构决定；真实模型跑批只用于验证可靠性，不能决定是否允许
不安全的旧写入路径。

### 决定 1：`temporal_plan` 是目标必填字段，但当前版本先保持可选且只读

**决定**：目标 schema 中必须有 `temporal_plan`，但现在不能直接把它加入 `_SHOT.required`
并继续“给了就优先拆镜”。当前过渡态应改为：字段可选、只参与审计和对比统计，不能驱动
拆镜、合并、duration 或 KF 写入。普通生成中缺失或不合格先记录可定位的迁移告警；任何
准备执行结构写入的路径遇到缺失或不合格 plan 必须 blocking 并转整镜 regen，禁止静默
回退后继续写入。

这不是继续观望。必填切换已经确定，只是必须按依赖顺序发布：

1. 先完成发现 10 的整镜覆盖、连续性、grounding、motion 一致性校验；
2. 按发现 11 把 canonical `temporal_plan` 纳入 script/per-shot revision，并升级
   `QUALITY_RULE_VERSION` 与 `_source_hash` 规则版本；
3. 所有结构消费者只读取同一个已验证 plan；`motion_desc` 降为展示文本，legacy 派生仅用于
   诊断，不得作为结构写入依据；
4. 再把 `_SHOT`、`_HOOK` 和 replacement schema 的 `temporal_plan` 同时改为 required，旧缓存
   统一失效或显式迁移，不允许混用新旧契约；
5. 对每个启用的文本 provider 至少抽样 100 个镜头：首轮结构合格率目标 ≥95%，一次定向
   regen 后合格率必须为 100%；未达到时流水线 fail closed，不恢复启发式写入。

选择“立即必填但沿用当前弱校验”会把虚构 plan 提升为强制事实源；选择“长期可选”则会让
系统永久保留两套时间真相。因此采用“目标必填、当前只读、依赖完成后一次切换”的单向迁移。

### 决定 2：保留完整镜头 replacement，禁止自动回退字段级 patch

**决定**：`shot_replacements[]` 是唯一继续演进的修复契约。模型输出不合格时保留修复前
镜头，记录拒绝原因并进入完整镜头 regen；不得在运行时自动切回
`REPAIR_PATCH_SCHEMA` / `_apply_repair_patches`。旧字段补丁恰好重新引入本轮要消除的字段
不一致和审计后语义写入，不能作为生产降级方案。

真实模型表现按以下方式验证：

1. 每个启用 provider 收集至少 50 个实际 blocking 修复样例，记录 strict-schema 解析率、
   replacement 校验通过率、一次 regen 后通过率、拒绝原因和最终 gate 结果；
2. 安全闸值是“错误接受数为 0、未点名镜头变化数为 0、事务外语义写入数为 0”；有效输出
   比例只影响是否启用自动修复，不影响是否回退旧 patch；
3. 新契约未达到可靠性目标时，自动修复开关关闭，行为是 `WARN/BLOCK + full regen`；
4. 完成发现 9 的 hook schema、duration 所有权、整批原子性和最终字段 mask 后，再做跑批；
   否则测到的是当前后处理混合事务，不是 replacement 契约本身；
5. 跑批通过后删除旧 schema/helper 和“可换回旧路径”的注释，避免形成长期双协议。

### 决定 3：拆镜前移到详细视觉字段生成之前，停用 timecode 自动拆镜

**决定**：接受原审计第三阶段目标。`_do_pre_split_oversized_shots()` 不再作为生产自动修复；
timecode 阶段只校验和写 timeline。超 cap 镜头必须在生成 ff/motion/KF/lf/frame layouts
之前完成结构拆分，或丢弃原详细镜头并让模型返回两个完整子镜。

落地分两步：

1. **立即止损**：所有生产入口使用 `allow_split=False`；发现超 cap 或缺少合法内部事件边界时
   生成 blocking issue，保留原镜并进入结构 regen。不得继续复制 KF、按文字切 narration、
   删除布局分句或制造半整数镜头 ID；
2. **目标流程**：增加 shot-plan 阶段，只生成 narration、稳定 shot/event ID、
   `temporal_plan`、目标 duration 和场景连续性；完成 cap/事件边界校验与必要拆分后，第二阶段
   才为每个最终 shot 生成完整视觉字段与 KF；
3. 子镜必须满足事件集合互斥且并集等于父镜、source_quote 可追溯、每个子镜独立满足 cap；
   失败就重生 shot plan，不对已有详细镜头做字段拼接；
4. 保留现有 `_do_pre_split_oversized_shots` 仅供旧数据诊断和迁移测试，移出默认生产路径；
   新流程稳定后删除其语义写入代码。

### 已落地选择的复核决定

此前为 `duplicate_shot_merge` 选择的“显式按比例缩放 KF”只解决了机械存储问题，发现 2 已
证明它会改变事件归属。**最终决定是不把该选项作为生产语义策略**：立即降为
`warn + 完整镜头 regen`；只有未来能用相同事件 ID、原文证据和状态转换证明两镜等价时，
才允许受控去重。七个 `_Final` 的删除可保留，但必须补发现 6 所列顺序依赖测试，不能把
单一 fixture 的等价结果解释为全输入等价证明。

## 测试审核

现有定向测试覆盖了已修改函数的主要 happy path并通过；全量测试受 pytest 临时目录权限错误中断，当前没有可据以宣称“全仓通过”的最新稳定快照。以下关键不变量尚未被覆盖：

1. `duplicate_shot_merge` 显式缩放后的事件归属与事件覆盖；
2. 任意 late writer 执行后重新检查单镜 cap 与全部 KF 时间不变量；
3. `allow_split=False` 不改变 duration 和未选镜头 revision；
4. parser 对裸数量范围和完全越界时段的拒绝；
5. 高 Jaccard 但事件结果相反的相邻镜不得自动合并；
6. `frame_layouts.kfs` 与 KF 的逐帧语义一致，而不只是长度相等。
7. 删除/换序中段约束后，存在读写依赖的后继约束输出是否等价；
8. 拆镜差异化不能删除人物位置、道具和姿态事实；
9. `get_kf_t_sec` 在双 duration 字段不一致时的明确兼容契约。
10. `REPAIR_SHOT_REPLACEMENT_SCHEMA` 从模型调用到整批提交、hook 类型校验、最终字段 mask 与逐镜 revision gate 的端到端路径。
11. LLM `temporal_plan` 的整镜覆盖、原文 grounding、motion 一致性和拆镜准入。
12. `temporal_plan` 任一语义字段变化都会使 script/per-shot revision 失效，规则版本升级后旧 audit 必须拒绝。

测试通过与以上发现不冲突：当前测试锁住了局部中间态和已知样例，没有贯穿“生成/修复 → hard-fix → timeline 归一 → final audit → 持久化”的完整生命周期。

## 建议实施顺序

1. **先封住审计盲区**：把规范化 `temporal_plan` 纳入 semantic revision，升级规则版本并使旧 audit 失效；同时在严格验证完成前禁止它驱动拆镜。
2. **停止链尾写入**：`duplicate_shot_merge`、`short_shot_merge`、`motion_time_bounds`、`duration_budget` 全部降为报告；移除拆镜中的人物位置分句删除；修复轮只调用纯 timeline 序列化。
3. **建立阶段屏障**：增加最终字段 diff 守卫，确保 final audit 前最后一次时间派生后不再发生语义写入。
4. **修解析器和事件验证**：权威语法拒绝裸范围和非法区间；事件表必须覆盖整镜、有原文证据并与 motion 一致。
5. **收紧整镜事务**：分离普通镜头/hook schema，明确 duration 所有权，整批校验和提交，通过事件覆盖、单镜 cap、相邻边界与字段 mask 后才允许接受。

完成前，建议把原审计文档的“第一阶段：停止继续制造时间语义 —— 已完成”改为“局部完成：五个 KF 写入者已降级，字段所有权与 late writers 待收敛”，并把选项 (c)、blood final 删除和拆镜策略更新为当前真实状态，避免版本登记把局部实现解释为端到端保证。
