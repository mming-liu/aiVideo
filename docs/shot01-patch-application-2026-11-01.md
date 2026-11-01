# shot 1 定稿补丁应用记录（2026-11-01）

## 1. 补丁与门禁

- 工具：`tools/production_context.py --apply-shot-patch ... --shot 1 --write`
- 补丁字段：`camera_movement/prompt/ff_desc/lf_desc/motion_desc/frame_layouts/keyframes`
- 校验：
  - `changed_shot_keys` 仅 `[1]`；
  - `_locked_repair_signature` 前后一致（duration/subtitle/narration/plan_refs 等未动）；
  - `_build_step01_quality_gate`：`status=PASS, blocking=False`。
- 新 revision：`b5ec17f69c7e`；`script_result.json` SHA-256 与 `step01_adoption.json` 一致。

## 2. 首轮效果问题与修正（含修订假设）

首轮两张图：杨旭侧坐船舷、双脚在水面；首尾帧人物位置变化明显。

修正：
1. 画面字段显式约束“船头铺平直船板，杨旭盘膝坐于船板中央，双脚收在船板内、不搭船舷、不垂向水面”；听香“立于船板上”；二人与船体相对位置固定。
2. **修订假设（代码侧，非已确认唯一根因）**：`shared/vimax_bridge.py` 的尾帧 prompt
   曾无条件追加“构图需按尾帧描述重新布局，可与首帧/参考图的景别、机位、人物位置不同”。
   固定机位下这条文案可能放大 I2I 挪动人物/船体的倾向，但首轮图同时存在画面字段
   缺少边界描述等因素，不能据此认定它是唯一根因。当前处理：
   - 固定机位尾帧只约束相机：“机位固定：沿用首帧的同一机位与景别，相机不移动；
     人物、道具与环境的位置按尾帧描述的动作终态演进。”不锁定人物/船/道具位置；
   - 非固定机位仍保留原重布局文案。
3. “脚伸出船外源于缺少边界描述”同样是**修订假设及本次改善观察**：补充船板/双脚
   边界后复核图显示双脚被衣袍遮盖、未接触水面，但不能据此确定唯一根因。没有自动
   姿态核验属于**检测缺口**，不是本轮生成错误的根因。

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
- 本次两图人物在船上的相对位置一致（单次观察，不证明固定机位应锁定人物位置）。

## 5. 边界

- 视觉复核只回答可观察事实，不等于画面验收通过；
- 需人工查看船位移、听香侧身面朝、老农远景；
- 视频（step02b）未重跑，需另行授权与报价。

## 6. 防回归守卫（2026-11-01 追加，同日修正）

首版守卫对完整 prompt 做子串硬拦截（`可与首帧` / `人物位置不同` / `重新布局`），
把固定机位错误扩大为人物位置锁定，并会硬拒否定、一致性描述和合法主体运动；
该硬门已撤除，不再由这些词阻断生成或 `--plan-only`。

现行约束与回归：
- `compose_frame_prompt()` 的固定机位尾帧只注入相机约束（同一机位/景别、相机不移动），
  人物、道具与环境位置交给本镜尾帧描述演进；不再注入“人物在画面中的相对位置不变”
  或无条件重布局文案；
- 原始字段的运镜疑似冲突仍走既有报告链（`shared/shot_motion_checks.motion_report`
  → step01 `_check_motion_consistency` 告警、step02b `motion_warnings`），只报告，
  不自动成为生产硬门；
- `step02a --plan-only` 仍收集每帧组装异常到 `prompt_preflight`，但仅对真实组装
  异常报 `blocked`，不再因上述词表命中退出 2；
- 测试：`tests/shared/test_frame_prompt_camera.py`（6 passed），覆盖固定机位系统文案
  不锁定人物位置、固定机位别名不误走重布局分支、否定/一致性/合法主体运动不被拒、
  非固定机位重布局文案保留。
