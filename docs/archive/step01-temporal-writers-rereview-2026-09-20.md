# step01 时间语义修复后二次代码审核（2026-09-20）

- 审核基准：`step01-temporal-writers-audit-2026-09-19.md` 与
  `step01-temporal-writers-implementation-review-2026-09-19.md`
- 审核快照：当前工作树 v6.79～v6.82 实现
- 审核范围：时间写入阶段屏障、`temporal_plan`、完整镜头 replacement、timecode 拆镜、
  quality/revision/cache 以及 chapter 001 当前落盘结果
- 代码改动：本次仅新增本审核文档，未修改运行代码

## 结论

本轮实现已经真实关闭了多项旧问题：四个链尾自动写入者已降为只报告；timecode 自动拆镜
默认关闭；修复轮改用 `write_timeline_from_durations()`；`temporal_plan` 已进入 script/per-shot
revision；规则版本已升级；完整镜头 replacement 已按普通镜头/hook 分 schema，并具备基础整批
回滚能力。

但当前仍不能标记为“全部修复完成”。还存在 **2 个 P0、3 个 P1、1 个 P2**。其中阶段屏障
仍然 fail-open，并明确放行一个能在 KF 检查之后缩短镜头的写入者；完整镜头事务也没有实现
“只改被问题点名字段”的运行时约束。这两项都能让通过局部测试的脚本在最终落盘前改变时间
或剧情语义。

## 修复状态（2026-09-20 收口，v6.83）

> 下表取代下文各发现的**开放状态**。发现正文保留原始证据与最小复现（它们仍是这些
> 不变量存在的理由）。规则版本升到 **2026-09-20.2**，`2026-09-20.1` 的报告一并失效。

| # | 发现 | 状态 | 落地 |
|---|---|---|---|
| 1 | 阶段屏障 fail-open，放行链尾时长夹取 | **已落地** | `SANCTIONED_TIME_WRITERS` **清空**（审计段不允许任何时间字段写入者）；`runtime_extend` 单镜 cap 夹取降为 **HARD 报告**（cap 属时长派生，只能发生在 KF 生成之前——`_compute_timecodes` 已在权威派生点按 `min(模型 cap, 8s)` 夹取）；新增 `finalize_step01_script()`，**正常生成 / 缓存命中 / BEST-GUARD 三条路径统一走**「hard-fix（含屏障）→ timeline 序列化 → 最终不变量」；屏障结果进质量门（`time_field_audit_write`，blocking）；新增最终时间不变量（`kf_time_out_of_shot` / `duration_two_calibers` / `timecode_span_mismatch`）；12s→8s 对抗回归断言「越权 + 越界帧」 |
| 2 | 整镜事务只锁固定字段，未按 issue 授权 | **已落地** | 新增 `step01_script_gen/repair_fields.py`：`allowed_repair_fields`（从 warning/audit issue 推授权路径）+ `canonical_path_diff` + `check_replacement_authorized`；允许集合按 target 下发到 prompt 的 `allowed_fields`；**推不出字段路径的目标不进定向修复**（交整镜 regen）；`temporal_plan`/`motion_desc`/`keyframes`/`duration` 迁移期默认锁定；`duration` 补进 `_FINAL_LOCKED_FIELDS`（此前 prompt 声称它是 Mask 字段、代码里没有实现） |
| 3 | hook-only replacement 主路径不可达 | **已落地** | envelope 两个键**都允许为空**，按 `picked` 校验类型；hook 必须由 `hook_replacement` 承载（塞进 `shot_replacements` 即拒）；两条修复 prompt 同步写明两种格式；新增**主流程**解析测试（断言 applier 真的收到 `hook_replacement`） |
| 4 | 「整批原子」未校验目标集合完整性 | **已落地** | 新增 `expected_targets`：每个 picked 目标**恰好出现一次**，缺项 / 多出 / 重复都整批回滚（重复由 `_seen_ids` 拒，与集合判据分开） |
| 5 | 事件表只比边界，不比事件语义 | **已落地** | `validate_events` 补「描述与对应 motion 段正文**无任何共同内容**」判据（剥运镜/方式/静态方位/功能字后比内容二元组——是**必要判据**，不做蕴含推断）；`source_quote` 范围从「整章任意位置」收到**本镜原文窗口**（台词在原文定位处 ±300 字），越界即 **blocking**；对抗测试：边界相同结果相反、同义改写不误报、纯运镜段不误报、引用同章另一场景 |
| 6 | `_source_hash` 被清理导致缓存永不命中 | **已落地** | 落盘键改 `source_hash`（非下划线，不再被 `_strip_runtime_fields` 删除），读侧兼容旧 `_source_hash`；规则源指纹纳入 `shared/motion_segments.py` / `shared/quality_gate.py` / `shared/semantic_barrier.py` / `_text_gen/deepseek_schema.py`；新增「首次落盘 → 第二次 `_prompt_changed == False` → 规则版本变化变 True」端到端测试 |

### 另修一处**审核未报告**的静默 bug

`main.py` 里 `_repair_acceptance_decision(...)` 的第 3 个实参写成了 `allowed_targets`，
而该名字**从未被定义**（HEAD 里传的是 `audit_issues`）。整段接受判定抛 `NameError`，
被外层 `except` 吞掉并 `script = _round_before_script` **回退整轮修复**——也就是说
v6.79～v6.82 新增的 non-target gate 与最终字段 mask **在生产里从未真正生效过**：
每次定向修复都在最后一步被静默丢弃。已按名字本义补齐（只把**本轮点名目标**的
audit issue 当作可解目标），并加回归锁住「不再有 NameError」。

> 这个 bug 是写「hook-only 主流程」测试时暴露的——直接调 helper 的测试永远看不到它。

### 未覆盖 / 需要你决定的一点

- `descriptions_conflict_with_body` 是**字符级必要判据**：场景名词无法穷举，段正文若
  只写「镜头固定，水面平静」这类纯环境词，残余词可能被判成「无共同内容」。因此它
  只作为 **plan 拒绝理由 + 告警**，没有升级成 blocking（引文越界才 blocking）。
  审核原文要求「冲突时 blocking + full regen」；把它设为 blocking 会假封正常镜头，
  且该表当前只读、不驱动结构（拒绝消费已足够安全）。若你要求严格 blocking，
  一行即可切换——但需要先补一份场景词白名单的维护方案。

## 发现 1（P0）：阶段屏障会放行链尾时长夹取，能在 KF 检查后制造最终越界帧

### 证据

`shared/semantic_barrier.py:56-58` 把 `runtime_extend` 列入
`SANCTIONED_TIME_WRITERS`。该约束位于 `kf_time_window_alignment`、`kf_t_sec_bounds`、
`kf_monotonic` 之后，却仍在 `constraints/runtime_extend.py:179-180` 把超过模型 cap 的
`duration` / `duration_sec` 直接缩短。

最小复现：一个 12 秒镜头有 `t_sec=10` 的合法 KF。先运行 `kf_t_sec_bounds`，没有违例；随后
`runtime_extend` 把镜长夹到 8 秒，KF 仍为 10 秒：

```text
before_clamp_violations = []
final_duration          = 8.0
final_kf_t              = 10.0
kf_out_of_bounds        = True
```

这正是原审计的根因：链后写入使链前结论失效。当前屏障还存在三层失守：

1. `main.py:4181-4199` 明确采用“报告-only”，发现越权只写日志，不阻断、不回滚，随后仍重建
   timeline、计算 revision 并落盘；
2. 缓存命中路径 `main.py:3595-3616` 直接运行 final hard-fix，没有接入 tracker；
   BEST-GUARD 恢复历史快照时也直接运行 hard-fix，没有同一屏障；
3. `quality_issues._keyframe_timeline_issues()` 只检查缺时刻、非递增、过近、重复事件和 layout
   数量，没有检查最终 `t_sec <= duration_sec`，所以该越界不一定进入最终 blocking gate。

### 根因与实施方案

根因是把 provider cap 夹取定义成“可以晚写的安全例外”。缩短时长虽然不虚构内容，却会改变
所有时间坐标的合法域，不可能在 KF 审计之后安全执行。

1. 删除审计段时间写入白名单；`runtime_extend` 的 cap 检测降为 HARD 报告，由 shot-plan/整镜
   regen 在 KF 生成前解决。若必须确定性夹取，必须移到所有 KF 生成与检查之前；
2. 阶段屏障必须进入质量门：任何审计段写入都生成 blocking issue，正式落盘状态不得为 PASS；
3. 抽出统一的 `finalize_step01_script()`，正常生成、缓存命中、BEST-GUARD 恢复三条路径都走
   “时间派生 → hard-fix → 最终不变量检查 → timeline → revision → gate → persist”；
4. 增加最终对象检查：每个 KF 满足 `0 < t_sec <= duration_sec`，motion/event 窗口不越界，
   `duration == duration_sec == timecode.end-start`；该检查必须发生在最后一个写入者之后；
5. 增加上述 12s→8s 对抗回归，并断言结果 BLOCK，而不是只断言 tracker 记录到了写入。

## 发现 2（P0）：完整镜头事务只锁固定结构字段，没有执行“按 issue 允许字段”

### 证据

`main.py:1758-1761` 的 `_FINAL_LOCKED_FIELDS` 只包含 index、旁白、字幕、场景、地点和
variation_type。它没有根据本轮 warning/audit issue 的 `field_path` 生成允许修改集合。
因此当本轮只要求修 prompt 时，replacement 仍可同时改写 `motion_desc`、`temporal_plan`、
keyframes、ff/lf 和 frame layouts；只要固定 mask 未动、未新增可识别 blocker，事务即可接受。

实测把目标镜头 prompt 改好，同时把 motion 和事件表改成“凭空改变剧情”：

```text
_apply_shot_replacements(...)       -> applied=1, rejected=[]
_final_field_mask_violations(...)   -> []
```

`temporal_plan` 进入 revision 只能证明这次变化被哈希记录，不能证明该变化得到授权；逐镜 revision
gate 又有意允许被点名镜头改变，所以两者都拦不住这条旁路。

### 根因与实施方案

根因是把“完整对象传输”误当成“完整对象都可以修改”。完整镜头 schema 解决字段缺失问题，
但修复授权仍必须精确到问题字段。

1. 从每个 target 的 deterministic warning / audit issue 收集允许的 `field_path`；没有明确
   field path 的问题不得走定向修复，改走整镜 regen；
2. replacement 应先和原镜做 canonical path diff，除本轮允许路径及纯派生字段
   `duration_sec/timecode` 外，任何差异都整批拒绝；
3. 当前迁移期把 `temporal_plan`、`motion_desc`、`keyframes[*].t_sec/stage/desc` 默认锁定；只有
   对应的时间问题明确点名这些字段时才允许修改，并在修改后重新跑整镜时间不变量；
4. anchor 保护不能只靠 prompt 文案；对致命伤、服装、角色状态和布局事实做 before/after
   结构差异检查；
5. 增加“只修 prompt 却顺带改 motion/plan/KF”的端到端拒绝测试。

## 发现 3（P1）：hook-only replacement 在主调用路径不可达

schema 已新增独立 `hook_replacement`，但两条修复 prompt 仍只要求输出
`shot_replacements`（例如 `main.py:2206-2220`）；解析后 `main.py:2370` 又要求
`shot_replacements` 必须是非空数组。若本轮只点名 hook，符合新 schema 的自然响应是：

```json
{"shot_replacements": [], "hook_replacement": {"shot_id": "hook", "shot": {}}}
```

该响应会在调用 `_apply_shot_replacements()` 前被“未返回有效替换镜头”直接拒绝。反过来若模型
依照旧 prompt 把 hook 塞进 `shot_replacements`，它会按普通 `_SHOT` 字段校验，也无法通过。

修复应让 envelope 始终包含两个数组（普通镜头 replacements 与 hook replacements，可为空），
根据 picked 集合校验至少一个目标且类型匹配；prompt 必须明确两种格式。增加 hook-only 的主流程
解析测试，不能只直接调用 helper。

## 发现 4（P1）：所谓“整批原子”没有验证目标集合完整性和唯一性

`_apply_shot_replacements()` 会逐项 stage，但不拒绝重复 `shot_id`，也不要求响应覆盖本轮全部
picked targets。最小复现结果：

```text
同一 shot_id 出现两次 -> applied=2，后一个静默覆盖前一个
picked={shot_1, shot_2} 但只返回 shot_1 -> applied=1，无拒绝原因
```

因此当前“全有或全无”只表示“返回的条目都合法”，不表示“本轮请求的事务完整”。应先构造
期望目标集合，要求每个 picked target 恰好出现一次，不得多出、遗漏或重复；集合校验通过后
再做逐镜内容校验和一次性提交。

## 发现 5（P1）：`temporal_plan` 与 motion 的“一致性”只比较段数和边界，不比较事件语义

`shared.motion_segments.validate_events()` 已补首尾覆盖、连续性、source_quote 非空和边界一致，
但 `motion_desc` 比较仅检查段数与逐段起止时刻。以下输入仍返回空串（通过）：

```text
motion: 0-2秒：举刀；2-4秒：收刀
plan:   0-2 凭空倒地；2-4 突然起火
validate_events(...) -> ""
```

`validation._quote_grounded()` 又只要求 quote 出现在整章任意位置；引用另一场景的真实句子，也能
给本镜虚构事件提供“grounded”标记。当前 LLM plan 尚未驱动拆镜，因此这是迁移阻塞项而非立即
结构破坏；在将 `temporal_plan` 设为必填或权威前必须解决。

实施时应把 source_quote 限定在本镜 narration 对应的原文窗口，并验证 event description 与
对应 motion 段正文/quote 的主体、动作和结果没有冲突。不能只用字符相似度决定剧情蕴含；冲突
时 blocking + full regen。加入“边界相同但结果相反”和“引用同章另一场景”的对抗测试。

## 发现 6（P2）：`_source_hash` 被统一运行时字段清理删除，缓存永远无法命中

`main.py:4224` 写入 `script["_source_hash"]`，但 `main.py:4239` 落盘前调用
`_strip_runtime_fields()`；该函数会递归删除所有下划线开头键。chapter 001 当前
`script_result.json` 实测 `_source_hash=None`，所以下次 `_prompt_changed()` 必然返回 true，
每次运行 step01 都会重新调用模型。

这不会复用旧规则结果，安全上偏保守，但会让“重跑”变成完整付费生成，也使缓存/version
迁移测试失去意义。应把持久化缓存身份改成非下划线字段（如 `source_hash`），或让清理函数
显式保留 `_source_hash`；增加“首次落盘后第二次 `_prompt_changed == False`、规则版本变化后
变为 True”的端到端测试。`RULE_SOURCE_FINGERPRINT` 也应纳入本轮新增的
`shared/motion_segments.py`、`shared/quality_gate.py`、`shared/semantic_barrier.py` 和
`_text_gen/deepseek_schema.py`，避免只改核心规则实现却仍接受旧报告。

## 测试结果

- `py_compile`：本轮相关 Python 文件通过；
- 新增/相关定向集：`193 passed`；
- `tests/step01_script_gen + tests/shared`：`1476 passed, 1 skipped`；
- 全量 `tests`：`1613 passed, 1 skipped`；
- 默认 pytest 临时目录仍有权限问题，本次使用工作区外可写 `--basetemp` 完成干净运行。

测试全绿与上述问题不冲突：现有屏障测试验证“谁被记录”，没有验证最终非法对象必须 BLOCK；
replacement 测试验证固定 mask 和单条校验，没有验证 issue-specific 字段授权、hook-only envelope、
重复目标和目标集合完整性。

## 是否需要重跑 step01

**需要，但不要在上述两个 P0 修复前重跑正式结果。**

当前 chapter 001 落盘结果生成于 2026-09-19 21:30，实测状态为：

```text
step01_quality.status       = BLOCK
step01_quality.rule_version = 2026-09-19.16
当前规则版本                = 2026-09-20.1
visual_risk_count           = 42
temporal_plan 镜头数        = 0 / 16
_source_hash                = missing
```

旧 quality report 已被当前 gate 规则版本判为过期，而且产物本身就是 BLOCK；继续直接跑 step02
不成立。建议顺序：

1. 先修发现 1、2，并补发现 3、4 的事务测试；
2. 重跑全量测试；
3. 再完整运行一次 `python step01_script_gen/main.py`。由于 `_source_hash` 当前缺失，这次会
   实际重新调用文本模型，不是缓存重验；
4. 只在新 `step01_quality.json` 为 PASS、rule version 为当前版本、script revision 匹配，且
   最终 KF/时间不变量检查通过后，再进入 step02a。

