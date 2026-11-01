# shot 1 定稿补丁应用记录（2026-11-01）

## 1. 补丁与门禁

- 工具：`tools/production_context.py --apply-shot-patch ... --shot 1 --write`
- 补丁字段：`camera_movement/prompt/ff_desc/lf_desc/motion_desc/frame_layouts/keyframes`
- 校验：
  - `changed_shot_keys` 仅 `[1]`；
  - `_locked_repair_signature` 前后一致（duration/subtitle/narration/plan_refs 等未动）；
  - `_build_step01_quality_gate`：`status=PASS, blocking=False`。
- 新 revision：`f9b06acecb11`；`script_result.json` SHA-256 与 `step01_adoption.json` 一致。

## 2. 关键帧口径修正

2 段 `motion_desc` 触发 `kf_segment_missing`（visual_risk，blocking）。
按门禁要求补 2 个关键帧（t=1.25 / 4.1）及等长 `frame_layouts.kfs`，不再置空；
`keyframes_required=false` 时 step02a 默认仍只生成 ff/lf（本次 Keyframes: NONE）。

## 3. S14 实跑（shot 1）

- 命令：`production_context --run -- python step02a_frame_gen/main.py --shots 1`
- 结果：`exit 0`，`[IMAGE-PRODUCT] status=PASS missing=0`
- 实际图片提交 **2 次**（首帧 19:51:13、尾帧 19:52:19，均 HTTP 200）；
- 角色/场景/道具命中缓存，未新增资产图；
- 产物：
  - `vimax_working/shots/1/first_frame.png`
  - `vimax_working/shots/1/last_frame.png`
- 未生成关键帧图片；未调用视频模型。

## 4. 边界

- 这是按定稿字段重新生成的 shot 1 首尾帧，不等于画面验收通过；
- 需人工查看船位移、听香侧身面朝、老农在远景是否成立；
- 视频（step02b）未重跑，需另行授权与报价。
