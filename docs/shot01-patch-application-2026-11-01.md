# shot 1 定稿补丁应用记录（2026-11-01）

## 1. 补丁与门禁

- 工具：`tools/production_context.py --apply-shot-patch ... --shot 1 --write`
- 补丁字段：`camera_movement/prompt/ff_desc/lf_desc/motion_desc/frame_layouts/keyframes`
- 校验：
  - `changed_shot_keys` 仅 `[1]`；
  - `_locked_repair_signature` 前后一致（duration/subtitle/narration/plan_refs 等未动）；
  - `_build_step01_quality_gate`：`status=PASS, blocking=False`。
- 新 revision：`b5ec17f69c7e`；`script_result.json` SHA-256 与 `step01_adoption.json` 一致。

## 2. 首轮效果问题与修正

首轮两张图：杨旭侧坐船舷、双脚在水面；首尾帧人物位置变化明显。

修正：
1. 画面字段显式约束“船头铺平直船板，杨旭盘膝坐于船板中央，双脚收在船板内、不搭船舷、不垂向水面”；听香“立于船板上”；二人与船体相对位置固定。
2. 根因（代码）：`shared/vimax_bridge.py` 的尾帧 prompt 无条件追加
   “构图需按尾帧描述重新布局，可与首帧/参考图的景别、机位、人物位置不同”。
   固定机位下这会直接诱导 I2I 挪动人物。已改为：
   “构图与首帧保持一致：同一机位、同一景别，人物在画面中的相对位置不变；仅动作与环境按尾帧描述演进。”
   非固定机位仍保留原重布局文案。

## 3. S14 实跑

- `--shots 1`：重生成 ff/lf，2 次图片提交（均 200）；
- 代码修正后 `--shots 1 --frames lf`：仅重生成尾帧，1 次图片提交；
- 角色/场景/道具命中缓存，无新增资产图；
- 关键帧：NONE（未生成图片）。

产物：
- `vimax_working/shots/1/first_frame.png`
- `vimax_working/shots/1/last_frame.png`

## 4. 视觉复核（OpenCode Go）

- 两图均为盘坐船板/船舱内，非船舷；
- 双脚被衣袍遮盖、未接触水面、未垂在船外；
- 两图人物在船上的相对位置一致，无明显位置变化。

## 5. 边界

- 视觉复核只回答可观察事实，不等于画面验收通过；
- 需人工查看船位移、听香侧身面朝、老农远景；
- 视频（step02b）未重跑，需另行授权与报价。

## 6. 防回归守卫（2026-11-01 追加）

- `shared/vimax_bridge.py` 新增 `frame_prompt_problems()`：固定机位尾帧若出现
  “可与首帧 / 人物位置不同 / 重新布局”，返回 `fixed_camera_relayout_conflict`；
- `compose_frame_prompt()` 在尾帧组装末尾调用该守卫，命中即抛错（fail-closed）；
- `step02a --plan-only` 把每帧组装异常收集到 `prompt_preflight`；
  状态为 `blocked` 时退出码 2；
- 测试：`tests/shared/test_frame_prompt_camera.py`（4 passed）。
