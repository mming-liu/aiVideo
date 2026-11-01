# Step02a/b 矛盾兜底计划（不重跑 step01，2026-11-01）

## 1. 目标与成功判据

**目标**：不重跑 step01、不改 script revision、不重签门禁；在 step02a / step02b 的**下发前**建立确定性兜底——发现矛盾、按固定优先级消解、留痕，再发送。

成功判据：
1. step02b 视频 prompt 中 “运镜：” 恰好一次，且与 camera_movement 参数一致；
2. step02a 首帧 prompt 不再包含与 ff_desc 冲突的中段动作词（如“吹奏”）；
3. 每次兜底都有 fallback 记录（code / field / removed / reason），不静默；
4. SHOT_MOTION_STRICT=1 时硬矛盾直接 blocked；
5. 单测 + step02a/02b --plan-only 实测全绿。

## 2. 基线与现状

- shot 1 硬矛盾：camera_movement=固定机位，motion_desc 写“镜头掠过”；
- shot 1 状态矛盾：prompt 含“吹奏”，ff_desc 写“未吹”；
- 视频 prompt 里“运镜：固定机位”出现两次；
- 20 镜中 9 镜密度软告警（shot 4/5/6/7/8/9/10/13/16）；
- 现有 D 规则只**告警**，不消解；step02b 仍把矛盾原样下发。

## 3. 兜底优先级（确定性，零模型调用）

1. **相机权威**：camera_movement 是唯一相机参数；prompt 文本里的运镜词与它冲突时，删除这些词并记录 removed_camera_terms。
2. **帧状态权威**：首帧以 ff_desc 为准、尾帧以 lf_desc 为准；prompt 中与 ff_desc 冲突的中段动作词，在首帧 prompt 中删除并记录 removed_state_terms。
3. **位移不可见**：motion_desc 有位移、但 ff_desc 与 lf_desc 相似度 ≥0.85 时，不伪造位移；记录 displacement_not_visible；默认继续发送，strict 阻断。
4. **单源**：视频 prompt 只保留一处“运镜：X”。
5. **密度**：motion_desc ≥3 段且 duration ≤6.5s，默认软告警，不删内容。

## 4. 实现点

| 模块 | 责任位置 | 动作 |
|---|---|---|
| S01 | shared/shot_motion_checks.py | 新增 prompt_state_conflict、motion_density_high；新增纯函数 sanitize_camera_terms()、sanitize_state_terms() |
| S02 | step02_video_gen/submit_poll.build_frozen_for_shot、request_plan.build_frozen_request | prompt 只前置一次运镜；调用 sanitize_camera_terms |
| S03 | shared/style_prefix.py 的【首帧】/【尾帧】组装 | 调用 sanitize_state_terms，ff/lf 为权威 |
| S04 | step02a_quality / step02b plan / 日志 | 增加 fallback 字段（code、field、removed、reason） |
| S05 | tests/shared、tests/step02b_video_gen | 单测 + plan-only 实测 |

## 5. 边界与故障

- 只删除“与参数/帧状态冲突的词”，不改写语义；记录原文与被删词；
- 不重跑 step01、不改 script revision、不重签门禁（step02a/02b 不在规则指纹内）；
- strict 模式：硬矛盾直接 blocked，不发送；
- 删词后 prompt 为空或不可用：回退原 prompt 并 blocked（fail-closed）；
- 位移不可见时默认照发，但在报告里明确“船不会移动”，不冒充已满足。

## 6. 验收

| 场景 | 入口 | 断言 |
|---|---|---|
| 规则单测 | pytest tests/shared/test_shot_motion_checks.py | camera/状态冲突删除、无冲突不动、运镜计数=1 |
| 视频 prompt | step02b --shots 1 --plan-only | shot 1 prompt 只含一次“运镜”，fallback 记录含 camera_motion_conflict |
| 首帧 prompt | step02a --shots 1 --plan-only | 首帧 prompt 不含“吹奏”（或记录 removed_state_terms） |
| 严格模式 | SHOT_MOTION_STRICT=1 | blocked，generations=0 |

## 7. 假设与未决

1. 密度告警默认软（推荐）；是否升硬门待定。
2. 位移不可见时默认照发（推荐）还是 strict 阻断。
3. 老农/牛/娃娃文本保留，但记录“固定机位下不会出现”。
4. 之后是否按候选 A/B 修订 shot 1——不阻塞本计划。

## 8. 交付与证据

- 代码：shot_motion_checks、submit_poll/request_plan、style_prefix + 测试；
- 证据：单测输出、step02a/02b --plan-only 的 prompt 与 fallback 记录；
- 本计划不产生付费调用。