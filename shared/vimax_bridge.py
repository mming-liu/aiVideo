"""script_result.json → ViMax working_dir 翻译器.

把 aiVideo 01_script_gen 产出的分镜（narration + shots + characters）翻译成 ViMax 的
working_dir 缓存产物，让 Script2VideoPipeline 命中缓存、跳过全部 LLM 规划步骤
（extract_characters / design_storyboard / decompose / construct_camera_tree /
generate_frames_for_single_camera），只跑 generate_video_for_single_shot。

v3: 摄像机分组 + 镜头分解 + 首帧锚定 + 尾帧生成 + 视觉衔接
  - cam_idx 从 script 实际读取（不再硬编码 1），算法构建多摄像机树
  - variation_type / ff_desc / lf_desc / motion_desc 从 script 实际读取
  - 同摄像机内镜头 2+ 的首帧引用该摄像机首帧（而非跨摄像机混用）
  - medium/large 变幅镜头额外生成 last_frame.png
  - context_bridge / character_profile 写入 shot_description
  - 向后兼容：所有新字段缺失时回退到旧硬编码值
"""

import asyncio
import json
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import List

from vimax.interfaces import (
    Camera,
    CharacterInScene,
    ShotBriefDescription,
    ShotDescription,
)

from shared.character_bank import CharacterBank, StyleBank
from shared.paths import (
    script_file,
    shot_work_dir,
    FIRST_FRAME_FILENAME,
    LAST_FRAME_FILENAME,
)

import hashlib

# ─── API 冻结（v6.60.1，硬编码清理后的工程卫生项）───────────────────────────
# 本模块 3900+ 行，拆分为包的计划已排期但延后；冻结期内：
#   1) 下列名字是全部被跨模块消费的 API（含 image_qc/validation/prompt_builder
#      引用的下划线名——历史上它们按"模块私有"命名却被外部 import，冻结起见
#      一并登记，改名前必须先迁移消费方）；
#   2) 新增公共函数必须同步 __all__；
#   3) 不在 __all__ 内的名字视为模块内部实现，外部新引用一律禁止。
__all__ = [
    # 公共入口（step02a / step02b 主链路）
    "build_working_dir",
    "gen_shot_frames",
    # validation 回调（视线修复 / 空镜判定 re-export / kf 布局提取）
    "fix_lf_desc_gaze",
    "is_empty_scene",  # style_prefix 实现，经本模块 re-export
    "_extract_char_layout",
    # image_qc 门控 / 尺寸锚
    "_threat_visible_in",
    "_inject_object_size_anchor",
    # prompt_builder / subject_state / asset_gen
    "_extract_prop_layout",
    "_char_aliases",
    "_extract_kf_pose",
    "_KF_POSE_STATES",
    "_maybe_qc_frame",
    # 测试消费（tests/shared/test_hook_shot0_v661）
    "_inject_frame_spec",
    "_doodle_refine_first_frame",
    "_build_char_anchor",
]

# 静默日志: 桥接日志写入 step02.log，不污染控制台
def _bridge_log(msg: str):
    """写入文件日志."""
    try:
        from shared.tee_logger import get_logger
        from shared.paths import chapter_dir
        log = get_logger(chapter_dir() / "step02.log", console_level="INFO")
        log.verbose(msg)
    except Exception:
        pass

# v6.x 重抽多样性：per-attempt prompt 微扰动菜单。
# 仅在 QC 重试时按 attempt 下标取模追加到 desc_full 尾部，**仅局部变量**——
# 不回写 shot 字段（08-29 膨胀教训），哈希基于 shot 字段不受影响，
# QC 断言源也不受影响。attempt 0 恒为原 prompt（variation[0]=""）。
# 维度选「构图/机位/人物间距」：QC 断言不查的视觉维度，廉价偏移构图分布。
#
# 🔴 红线（v6.x.x P3）：换扰动词时**必须避开 QC 断言维度**——
#   - `build_claim_checks`（image_qc.py）拆的原子子句会扫这些词
#   - 避开：「画左/画右/画中/前景/中景/远景/船头/船尾/中舱/甲板/
#          水中/水下/水面/岸边/门口/车辕/仰卧/仰面/躺/跪/蹲/探出/
#          俯身/倒/刀/匕首/短刃/武器/持/握/蒙面/白袍/血/伤/洇/红/
#          伞/灯笼/马车/箫/肩宽/船宽」等
#   - 安全：「构图/机位/角度/透视/间距/松紧/视野/景别/色温/氛围/笔触」等
#   - 命中红词 → 扰动维度被 QC 判"与描述不符"产生 critical → 反向放大问题
#     （修复为「保持维度与描述一致」反而把扰动抹掉）
_ATTEMPT_VARIATIONS = (
    "",                                            # attempt 0: 原 prompt
    "镜头构图稍紧，人物间距略近。",                  # attempt 1
    "机位角度略低，仰角透视自然。",                  # attempt 2
    "构图稍宽松，人物间距略远。",                    # attempt 3+ 取模回到 0/1/2
)

# 首帧/尾帧生成需要风格前缀（与视频生成保持一致）
from shared.style_prefix import STYLE_PREFIX, sanitize_prompt, ensure_prefix, is_empty_scene, add_image_strict_constraints, preprocess_prompt_for_image
from shared.style_prefix import IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT, image_lock_prompt


# 注意: 视频级缓存哈希 (prompt_hash.txt) 的唯一读写实现在
# step02_video_gen/submit_poll.py 的 _get_shot_hash / _poll_video 中，
# 此处不再保留第二套实现，避免读写算法分歧导致缓存永不命中。


# ---------------------------------------------------------------------------
# 摄像机树构建（算法驱动，无 LLM）
# ---------------------------------------------------------------------------

def _build_camera_tree(shots: list[dict]) -> list[Camera]:
    """根据 shot.cam_idx 算法构建多摄像机树.

    规则：
    1. 按 cam_idx 分组镜头（同一摄像机内镜头连续不交错，由 LLM 保证）
    2. 摄像机按 idx 升序排列
    3. 前一台摄像机作为后一台的 parent（线性链）
    4. parent_shot_idx = 父摄像机最后一个镜头（转场衔接点）
    """
    # 按 cam_idx 分组（保持插入顺序）
    cam_to_shots: dict[int, list[int]] = OrderedDict()
    for shot in shots:
        cam = shot.get("cam_idx", 1)
        cam_to_shots.setdefault(cam, []).append(shot["index"])

    cameras: list[Camera] = []
    sorted_cam_ids = sorted(cam_to_shots.keys())

    for i, cam_id in enumerate(sorted_cam_ids):
        shot_idxs = cam_to_shots[cam_id]
        parent_cam_idx = None
        parent_shot_idx = None
        reason = None
        if i > 0:
            # 前一台摄像机是本摄像机的 parent
            prev_cam = cameras[i - 1]
            parent_cam_idx = prev_cam.idx
            parent_shot_idx = prev_cam.active_shot_idxs[-1]  # 父摄像机最后一个镜头
            reason = f"摄像机{cam_id}承接摄像机{parent_cam_idx}的空间关系，"
            reason += f"以摄像机{parent_cam_idx}最后一镜（镜头{parent_shot_idx}）为转场起点"

        cameras.append(Camera(
            idx=cam_id,
            active_shot_idxs=shot_idxs,
            parent_cam_idx=parent_cam_idx,
            parent_shot_idx=parent_shot_idx,
            reason=reason,
            is_parent_fully_covers_child=False,
            missing_info="机位切换，空间关系与景别发生变化" if parent_cam_idx else None,
        ))

    _bridge_log(f"  [bridge] camera_tree: {len(cameras)} 台摄像机 "
          f"{[c.idx for c in cameras]}")
    for c in cameras:
        if c.parent_cam_idx:
            _bridge_log(f"    cam_{c.idx} -> parent=cam_{c.parent_cam_idx}"
                  f" 镜头{c.parent_shot_idx}")
    return cameras


async def build_working_dir(
    script_path: Path,
    working_dir: Path,
    image_adapter,
    force_first_frame: bool = False,
    skip_frames: bool = False,
):
    """读 script_result.json，写 ViMax working_dir 缓存产物 + 预生成 first_frame.

    v3: 所有镜头字段从 script 实际读取，缺失时回退到旧硬编码值。
    """
    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    shots = script.get("shots", [])
    characters_data = script.get("characters", [])
    if not shots:
        raise ValueError("script_result.json 中无 shots")

    working_dir = Path(working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)
    shots_dir = working_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    n = len(shots)

    # ---- 加载 bank ----
    bank = CharacterBank()
    bank.load()
    style_bank = StyleBank()
    style_bank.load()

    chapter_idx = int(os.getenv("NOVEL_CHAPTER", "1"))

    # ---- 角色肖像：生成缺失的 ----
    registry = {}
    if characters_data:
        registry = await _generate_missing_portraits(
            characters_data, bank, image_adapter, chapter_idx,
        )

    # ---- 1. characters.json ----
    chars_path = working_dir / "characters.json"
    if not chars_path.exists() or force_first_frame:
        char_objects = []
        for i, char in enumerate(characters_data):
            char_objects.append(
                CharacterInScene(
                    idx=i,
                    identifier_in_scene=char.get("identifier", f"角色{i}"),
                    is_visible=char.get("is_visible", True),
                    static_features=char.get("static_features", ""),
                    dynamic_features=char.get("dynamic_features", ""),
                ).model_dump()
            )
        # 原子写：防中断留半截 JSON（2026-09-13 审计修复，收敛 shared.json_io）
        from shared.json_io import atomic_dump_json as _adj
        _adj(chars_path, char_objects, ensure_ascii=False, indent=2)
        if char_objects:
            _bridge_log(f"  [bridge] characters.json: {len(char_objects)} 个角色")
    elif not characters_data:
        if not chars_path.exists():
            from shared.json_io import atomic_dump_json as _adj
            _adj(chars_path, [], ensure_ascii=False, indent=2)

    # ---- 2. character_portraits_registry.json ----
    # 每次都重写：registry 由 bank 实时导出，旧文件可能残留迁移前的失效路径
    registry_path = working_dir / "character_portraits_registry.json"
    if registry:
        from shared.json_io import atomic_dump_json as _adj
        _adj(registry_path, registry, ensure_ascii=False, indent=2)
        _bridge_log(f"  [bridge] character_portraits_registry.json: {len(registry)} 个角色")

    # ---- 3. storyboard.json（cam_idx 从 script 读取）----
    storyboard_path = working_dir / "storyboard.json"
    storyboard = [
        ShotBriefDescription(
            idx=shot["index"],
            is_last=(i == n),
            cam_idx=shot.get("cam_idx", 1),          # ← 不再硬编码
            visual_desc=shot.get("prompt", ""),
            audio_desc=shot.get("subtitle", ""),
        )
        for i, shot in enumerate(shots, 1)
    ]
    _dump_json(storyboard_path, storyboard)

    # ---- 4. camera_tree.json（算法构建多摄像机树）----
    camera_tree_path = working_dir / "camera_tree.json"
    camera_tree = _build_camera_tree(shots)
    _dump_json(camera_tree_path, camera_tree)

    # ---- 5. shot_description.json（所有字段从 script 读取）----
    def _find_visible_char_idxs(desc: str, chars: list[dict]) -> list[int]:
        """在描述文本中匹配角色名，返回可见角色 idx 列表."""
        if not desc:
            return []
        idxs = []
        for i, char in enumerate(chars):
            ident = char.get("identifier", "")
            if ident and ident in desc:
                idxs.append(i)
        return idxs

    last_idx = shots[-1]["index"]
    for shot in shots:
        idx = shot["index"]
        prompt = shot.get("prompt", "")
        # ---- 所有字段从 script 读取，缺失时回退 ----
        ff_desc = shot.get("ff_desc", "").strip() or prompt
        lf_desc = shot.get("lf_desc", "").strip() or ""
        motion_desc = shot.get("motion_desc", "").strip() or prompt
        variation_type = shot.get("variation_type", "small").strip() or "small"
        # 写 reason（若 script 无 variation_reason，自动生成）
        variation_reason = shot.get("variation_reason", "").strip()
        if not variation_reason:
            if variation_type == "small":
                variation_reason = "minor expression/pose/camera change"
            elif variation_type == "medium":
                variation_reason = "new character appears or composition shifts"
            else:
                variation_reason = "significant composition change"

        sd = ShotDescription(
            idx=idx,
            is_last=(idx == last_idx),
            cam_idx=shot.get("cam_idx", 1),
            visual_desc=prompt,
            variation_type=variation_type,
            variation_reason=variation_reason,
            ff_desc=ff_desc,
            ff_vis_char_idxs=_find_visible_char_idxs(ff_desc, characters_data),
            lf_desc=lf_desc,
            lf_vis_char_idxs=_find_visible_char_idxs(lf_desc, characters_data),
            motion_desc=motion_desc,
            audio_desc=shot.get("subtitle", ""),
        )
        _dump_json(shots_dir / str(idx) / "shot_description.json", sd)
        (shots_dir / str(idx)).mkdir(parents=True, exist_ok=True)

    # ---- 6. 首帧 + 尾帧生成（摄像机组间并行）----
    if skip_frames:
        _bridge_log(f"  [bridge] skip_frames=True，跳过帧生成")
        return

    script_mtime = script_path.stat().st_mtime if script_path.exists() else None

    # 摄像机首帧锚定：同一摄像机内，镜头 N 的 first_frame 引用该摄像机第一个镜头的 first_frame
    camera_first_frames: dict[int, str] = {}  # {cam_idx: first_frame_path}

    # 按摄像机分组处理（确保锚点先生成）
    cam_groups: dict[int, list[dict]] = OrderedDict()
    for shot in shots:
        cam = shot.get("cam_idx", 1)
        cam_groups.setdefault(cam, []).append(shot)

    # 风格锚点（asyncio.Lock 保护，只在 Ch1 首次锁定）
    style_lock = asyncio.Lock()

    # 摄像机组间并行：每组独立生成帧，组内串行（首帧锚定依赖）
    tasks = [
        _gen_frames_for_camera_group(
            cam_idx, cam_shots, shots_dir, image_adapter, force_first_frame,
            script_mtime, bank, style_bank, characters_data, camera_first_frames,
            style_lock, chapter_idx, cam_groups,
        )
        for cam_idx, cam_shots in cam_groups.items()
    ]
    await asyncio.gather(*tasks)

    _bridge_log(f"[bridge] working_dir 就绪: {working_dir} ({n} shots, {len(cam_groups)} 摄像机)")


async def _gen_frames_for_camera_group(
    cam_idx: int,
    cam_shots: list[dict],
    shots_dir: Path,
    image_adapter,
    force_first_frame: bool,
    script_mtime: float | None,
    bank: CharacterBank,
    style_bank: StyleBank,
    characters_data: list[dict],
    camera_first_frames: dict[int, str],
    style_lock: asyncio.Lock,
    chapter_idx: int,
    cam_groups: dict[int, list[dict]],
):
    """单摄像机帧生成：首帧串行(锚定依赖)，尾帧+关键帧后台并行.

    规则：
    1. 每个镜头首帧生成完 → 立即启动该镜头的尾帧+关键帧(后台)
    2. 下一镜的首帧不等尾帧/关键帧完成，立即用新的 Key 开始提交
    3. 所有帧完成后汇总，确保该摄像机所有帧生成完毕后才返回
    """
    bg_tasks: list[asyncio.Task] = []

    for i, shot in enumerate(cam_shots):
        is_first_in_cam = (i == 0)
        idx = shot["index"]
        shot_dir = shots_dir / str(idx)

        # -- 首帧生成（串行：需要前镜锚定） --
        ff_path = shot_dir / FIRST_FRAME_FILENAME
        refs = _build_reference_list(
            shot, bank, style_bank, characters_data, camera_first_frames,
            cam_idx=cam_idx, is_first_in_cam=is_first_in_cam,
        )
        ff_result = await _gen_first_frame_async(
            image_adapter, shots_dir, shot, force_first_frame, script_mtime,
            reference_paths=refs,
            empty_scene=is_empty_scene(shot, characters_data),
            characters=characters_data,
        )
        if is_first_in_cam and ff_result:
            camera_first_frames[cam_idx] = ff_result
            # 风格锚点锁定（全局首帧 = 首个摄像机的首个镜头首帧）
            async with style_lock:
                if chapter_idx == 1 and not style_bank.is_locked() \
                        and cam_idx == min(cam_groups.keys()):
                    style_bank.lock(ff_result, chapter_idx)
                    style_bank.save()

        # -- 尾帧+关键帧 后台并行（不等它们完成，下一镜首帧立即开始） --
        # 用当前镜头的 ff 完成状态启动后台任务
        async def _gen_rest_frames(shot, ff_path, i_idx):
            lf_refs = [str(ff_path)] if ff_path and ff_path.exists() else []
            char_ref = _select_best_reference(shot, bank, style_bank, characters_data)
            if char_ref and char_ref not in lf_refs:
                lf_refs.append(char_ref)
            await _gen_last_frame_async(
                image_adapter, shots_dir, shot, force_first_frame, script_mtime,
                reference_paths=lf_refs,
                empty_scene=is_empty_scene(shot, characters_data),
                characters=characters_data,
            )
            kf_refs = [str(ff_path)] if ff_path and ff_path.exists() else None
            await _gen_keyframes_async(
                image_adapter, shots_dir, shot, force_first_frame, script_mtime,
                reference_paths=kf_refs,
                max_retries=1,
                empty_scene=is_empty_scene(shot, characters_data),
                characters=characters_data,
            )

        bg_tasks.append(asyncio.create_task(_gen_rest_frames(shot, ff_path, idx)))

    # 等待所有后台尾帧/关键帧任务完成
    if bg_tasks:
        await asyncio.gather(*bg_tasks)


# ---------------------------------------------------------------------------
# 参考图列表构建（摄像机锚定）
# ---------------------------------------------------------------------------

def _build_kf_prop_scan(shot: dict, kf_idx_in_shot: int | None, kf_desc_raw: str) -> str:
    """per-frame 道具扫描文本（09-13 逐帧重配根因：合并全文让 ff 武器词漏进无关帧）.

    Returns:
        ``<kf_desc_raw> <spec_parts>`` 当本帧 spec 存在且非空；否则降级 ``kf_desc_raw``。

    静默 fallback（review 5）：
      - frame_layouts 缺失 / 非 dict / 缺 kfs → kf_desc_raw
      - kf_idx_in_shot 越界 / 为 None → kf_desc_raw
      - kfs[kf_idx_in_shot] 是非 list / 元素非 dict → skip 该 entry
      - 任何异常（数据污染）→ swallow，回 kf_desc_raw
    永不返回空字符串（kf_desc_raw 前置非空保证）；不抛异常给上游。
    """
    try:
        _kfs_spec = (shot.get("frame_layouts") or {}).get("kfs") or []
        if kf_idx_in_shot is None or kf_idx_in_shot >= len(_kfs_spec):
            return kf_desc_raw
        _spec_parts: list[str] = []
        for _e in (_kfs_spec[kf_idx_in_shot] or []):
            if not isinstance(_e, dict):
                continue
            _spec_parts.extend(t for t in (_e.get("position"), _e.get("pose")) if t)
            _pr = _e.get("props") or []
            _spec_parts.extend(str(p) for p in (_pr if isinstance(_pr, list) else [_pr]) if p)
        if _spec_parts:
            return f"{kf_desc_raw} {' '.join(_spec_parts)}"
    except Exception:
        pass
    return kf_desc_raw


def _build_reference_list(
    shot: dict,
    bank: CharacterBank,
    style_bank: StyleBank,
    characters_data: list[dict],
    camera_first_frames: dict[int, str],
    cam_idx: int,
    is_first_in_cam: bool,
) -> list[str]:
    """为镜头构建参考图列表，优先级：
    1. 同摄像机首帧锚定（非首镜头时）
    2. 角色正面肖像（prompt 中提到的角色）
    3. 风格锚点（跨摄像机回退）

    空镜不注入任何参考图：锚定帧/肖像里的人物会被图生图带进空镜画面。
    """
    refs: list[str] = []

    # 空镜：不注入角色肖像（人物会渗入画面），但传入背景参考（风格锚点/
    # 摄像机首帧），确保场景一致性而非纯 text-to-image 无约束生成。
    if is_empty_scene(shot, characters_data):
        # 优先使用摄像机首帧锚定（同摄像机的已生成帧，场景一致）
        if not is_first_in_cam and cam_idx in camera_first_frames:
            anchor = camera_first_frames[cam_idx]
            if anchor and Path(anchor).exists():
                refs.append(anchor)
                _bridge_log(f"  [bridge] 镜头{shot['index']}: 空镜，参考图=摄像机{cam_idx}首帧锚定（背景）")
        # 回退到风格锚点（Ch1 锁定的全局风格）
        if not refs and style_bank.is_locked():
            style_anchor = style_bank.get_anchor_path()
            if style_anchor and Path(style_anchor).exists():
                refs.append(style_anchor)
                _bridge_log(f"  [bridge] 镜头{shot['index']}: 空镜，参考图=风格锚点（背景）")
        if not refs:
            _bridge_log(f"  [bridge] 镜头{shot['index']}: 空镜，无可用背景参考图，降级为 text-to-image")
        return refs[:1]

    # 非首镜头：引用本摄像机首帧作为锚定（保证场景连贯，优先级最高）
    if not is_first_in_cam and cam_idx in camera_first_frames:
        anchor = camera_first_frames[cam_idx]
        if anchor and Path(anchor).exists():
            refs.append(anchor)
            _bridge_log(f"  [bridge] 镜头{shot['index']}: 参考图=摄像机{cam_idx}首帧锚定")

    # 角色肖像：参考图上限 = min(用户偏好 IMAGE_MULTI_REF, 模型能力 active_image_ref_limit)
    # 模型上限来自 shared.ref_compose 注册表（qwen=3 / seedream 4.5=14 / image-01=1 ...），
    # 不写死在此；IMAGE_MULTI_REF 只是省钱偏好（软上限），模型能力是硬上限（防 400）。
    from shared.config import IMAGE_MULTI_REF
    from shared.ref_compose import active_image_ref_limit
    ref_cap = min(IMAGE_MULTI_REF, active_image_ref_limit())
    if ref_cap >= 2:
        # 多参考图：剩余名额分给出镜角色（同框多角色各自传入肖像）
        remaining = ref_cap - len(refs)
        char_refs = _select_all_character_refs(shot, bank, characters_data, limit=remaining)
        for cr in char_refs:
            if cr not in refs:
                refs.append(cr)
        # 一个角色都没匹配到时，回退到单张最优参考图（含风格锚点）
        if not char_refs:
            char_ref = _select_best_reference(shot, bank, style_bank, characters_data)
            if char_ref and char_ref not in refs:
                refs.append(char_ref)
        return refs[:ref_cap]

    # 单参考图（默认，向后兼容）：锚定帧优先，否则单张最优角色肖像
    char_ref = _select_best_reference(shot, bank, style_bank, characters_data)
    if char_ref and char_ref not in refs:
        refs.append(char_ref)

    return refs[:1]  # 单参考图模式，取最优第一个


# ---------------------------------------------------------------------------
# 角色肖像生成
# ---------------------------------------------------------------------------

async def _generate_missing_portraits(
    characters: list[dict],
    bank: CharacterBank,
    image_adapter,
    chapter_idx: int,
) -> dict:
    """用 CharacterPortraitsGenerator 为新角色生成前/侧/背面肖像.

    已有肖像的角色直接复用，新角色生成后注册到 bank.
    返回 ViMax 兼容的 character_portraits_registry dict.

    肖像生成需要横屏白底纯角色图（不加风格前缀），与首帧视频的竖屏+前缀不同。
    因此创建专用 portrait_adapter：横屏尺寸 + 跳过风格前缀/合规清洗。
    """
    from vimax.agents.character_portraits_generator import CharacterPortraitsGenerator
    from shared.vimax_adapters import ImageGeneratorAiVideoAdapter

    # 肖像专用适配器：16:9 横屏、不加风格前缀、不替换敏感词（英文 prompt）
    # 2026-08-22 升级 2560x1440（= 3,686,400 像素）满足 Seedream 4.5 像素下限 3686400
    portrait_adapter = ImageGeneratorAiVideoAdapter(
        size="2560x1440",
        skip_prefix=True,
        skip_sanitize=True,
    )
    portrait_gen = CharacterPortraitsGenerator(image_generator=portrait_adapter)
    registry = {}  # 注掉的代码保持不变

    for char in characters:
        identifier = char.get("identifier", "")
        if not identifier:
            continue

        # 已有肖像且图片文件存在 → 直接复用
        if bank.has_character(identifier):
            entry = bank.get_character(identifier)
            if entry and entry.get("portraits"):
                portraits = entry["portraits"]
                # 检查实际文件是否存在（bank 有记录但旧图片已删除时需重生成）
                front_p = portraits.get("front", {}).get("path", "")
                if front_p and Path(front_p).exists():
                    registry[identifier] = portraits
                    _bridge_log(f"  [bank] 复用角色 {identifier} 的已有肖像")
                    bank.update_character_chapter(identifier, chapter_idx)
                    bank.update_dynamic_features(identifier, char.get("dynamic_features", ""))
                    continue
                else:
                    _bridge_log(f"  [bank] 角色 {identifier} 肖像文件不存在，重新生成...")

        # 新角色 → 生成肖像
        char_in_scene = CharacterInScene(
            idx=0,
            identifier_in_scene=identifier,
            is_visible=char.get("is_visible", True),
            static_features=char.get("static_features", ""),
            dynamic_features=char.get("dynamic_features", ""),
        )

        portrait_dir = bank.get_portrait_dir(identifier)
        portrait_dir.mkdir(parents=True, exist_ok=True)

        # === 基础图优先策略 ===
        # Step 1: 生成基础全景图（front = base），锁定核心特征
        base_path = portrait_dir / "base.png"
        if not base_path.exists():
            _bridge_log(f"  [bank] 生成 {identifier} 基础全景图 ...")
            try:
                img_out = await portrait_gen.generate_base_portrait(
                    char_in_scene,
                    style="photorealistic Chinese ancient costume character portrait, full-body, white background, 16:9 landscape canvas, realistic skin texture, natural lighting, film photography",
                    save_path=str(base_path),
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(base_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(base_path))
                else:
                    _bridge_log(f"  [bank] {identifier} 基础全景图: 未知返回类型 {type(img_out)}")
                    continue
                _bridge_log(f"  [bank] {identifier} 基础全景图 -> {base_path}")
            except Exception as e:
                _bridge_log(f"  [bank] {identifier} 基础全景图生成失败: {e}")
                continue

        # Step 2: 基于基础图生成侧面照
        side_path = portrait_dir / "side.png"
        if not side_path.exists() and base_path.exists():
            _bridge_log(f"  [bank] 基于基础图生成 {identifier} 侧面肖像 ...")
            try:
                img_out = await portrait_gen.generate_side_portrait(
                    char_in_scene, base_image_path=str(base_path),
                    save_path=str(side_path),
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(side_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(side_path))
                _bridge_log(f"  [bank] {identifier} 侧面肖像 -> {side_path}")
            except Exception as e:
                _bridge_log(f"  [bank] {identifier} 侧面肖像生成失败: {e}")

        # Step 3: 基于基础图生成背面照
        back_path = portrait_dir / "back.png"
        if not back_path.exists() and base_path.exists():
            _bridge_log(f"  [bank] 基于基础图生成 {identifier} 背面肖像 ...")
            try:
                img_out = await portrait_gen.generate_back_portrait(
                    char_in_scene, base_image_path=str(base_path),
                    save_path=str(back_path),
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(back_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(back_path))
                _bridge_log(f"  [bank] {identifier} 背面肖像 -> {back_path}")
            except Exception as e:
                _bridge_log(f"  [bank] {identifier} 背面肖像生成失败: {e}")

        # Step 4: 基于基础图生成独立高清面部特写（组合参考图法，锁脸防变脸）
        # 面部是"变脸"重灾区——三视图 + 单独面部特写同时输入，模型优先锁面部特征。
        face_path = portrait_dir / "face.png"
        if not face_path.exists() and base_path.exists():
            _bridge_log(f"  [bank] 基于基础图生成 {identifier} 面部特写肖像 ...")
            try:
                img_out = await portrait_gen.generate_face_closeup(
                    char_in_scene, base_image_path=str(base_path),
                    save_path=str(face_path),
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(face_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(face_path))
                _bridge_log(f"  [bank] {identifier} 面部特写肖像 -> {face_path}")
            except Exception as e:
                _bridge_log(f"  [bank] {identifier} 面部特写肖像生成失败: {e}")

        # 构建 portraits dict（兼容旧 front 字段名）
        portraits = {}
        if base_path.exists():
            portraits["front"] = {
                "path": str(base_path.resolve()),
                "description": f"{identifier} 基础全景肖像（参考源）",
            }
            portraits["base"] = {
                "path": str(base_path.resolve()),
                "description": f"{identifier} 基础全景肖像",
            }
        if side_path.exists():
            portraits["side"] = {
                "path": str(side_path.resolve()),
                "description": f"{identifier} 侧面全身肖像",
            }
        if back_path.exists():
            portraits["back"] = {
                "path": str(back_path.resolve()),
                "description": f"{identifier} 背面全身肖像",
            }
        if face_path.exists():
            portraits["face"] = {
                "path": str(face_path.resolve()),
                "description": f"{identifier} 面部特写肖像（锁脸防变脸）",
            }

        # 注册到 bank
        if not bank.has_character(identifier):
            bank.register_character(
                identifier=identifier,
                static_features=char.get("static_features", ""),
                dynamic_features=char.get("dynamic_features", ""),
                chapter_num=chapter_idx,
            )
        bank.set_portraits(identifier, portraits)
        bank.save()
        if portraits:
            registry[identifier] = portraits

    return registry


# ---------------------------------------------------------------------------
# 参考图选择（增强版：视角检测 + 多字段搜索 + 摄像机优先）
# ---------------------------------------------------------------------------

def _detect_view_preference(text: str) -> str:
    """从中文文本中检测角色视角偏好.

    返回 "front" / "side" / "back"，默认 "front".
    """
    back_kw = {"背面", "背影", "背向", "背对", "远去", "背身", "后背", "背朝"}
    side_kw = {"侧身", "侧面", "侧脸", "侧目", "斜视", "半侧", "侧对", "侧立", "侧坐", "侧卧"}
    front_kw = {"正面", "面对", "面向", "正视", "直面", "看向镜头", "正对", "直视"}

    for kw in back_kw:
        if kw in text:
            return "back"
    for kw in side_kw:
        if kw in text:
            return "side"
    for kw in front_kw:
        if kw in text:
            return "front"
    return "front"  # 默认正面


def _select_best_reference(
    shot: dict,
    bank: CharacterBank,
    style_bank: StyleBank,
    characters_data: list[dict],
) -> str | None:
    """为镜头选择最优单张参考图（增强版）.

    增强点:
    1. 搜索 ff_desc、prompt 两个字段（不含 context_bridge —— 它描述的是
       上一镜头画面，常含上一镜头角色名，会把无关角色肖像注入本镜头）
    2. 根据描述中的视角词（侧身/背面/正面）选择最合适的角色肖像视图
    3. 视角不存在时按 front → side → back 降级
    4. 空镜（画面不应出现人物）不注入任何参考图，避免参考图里的人物
       被图生图带进空镜画面

    优先级:
    1. ff_desc/prompt 中提到某角色 + 有匹配视角的肖像 → 用该视角
    2. 风格锚点（Ch2+ 或 Ch1 已锁定；仅非空镜）
    3. None（text-to-image）
    """
    # 空镜：任何含人物的参考图都会诱导模型把人画进空镜
    if is_empty_scene(shot, characters_data):
        _bridge_log(f"  [bridge] 镜头{shot['index']}: 空镜，不注入参考图")
        return None

    ff_desc = shot.get("ff_desc", "").strip() or ""
    prompt = shot.get("prompt", "")
    search_text = f"{ff_desc} {prompt}"

    # 视角检测
    view_pref = _detect_view_preference(search_text)

    # 检查角色名是否出现在搜索文本中
    for char in characters_data:
        identifier = char.get("identifier", "")
        if identifier and identifier in search_text:
            entry = bank.get_character(identifier)
            if entry and entry.get("portraits"):
                portraits = entry["portraits"]
                # 特写镜头：face 面部特写优先（锁五官防变脸，组合参考图法）；
                # 否则按优先级尝试：检测到的视角 → front → side → back
                is_closeup = any(kw in search_text for kw in ("大特写", "特写", "面部", "表情", "脸部"))
                if is_closeup:
                    face_p = portraits.get("face", {}).get("path")
                    if face_p and Path(face_p).exists():
                        _bridge_log(f"  [bridge] 镜头{shot['index']}: 参考图=角色 {identifier} 面部特写")
                        return face_p
                for view in [view_pref, "front", "side", "back"]:
                    p = portraits.get(view, {}).get("path")
                    if p and Path(p).exists():
                        view_tag = f"（视角={view}" + (" 自动匹配)" if view == view_pref else " 降级)")
                        _bridge_log(f"  [bridge] 镜头{shot['index']}: 参考图=角色 {identifier} {view}肖像{view_tag}")
                        return p
                # 极端情况：portraits dict 里没有任何视图
                break

    # 回退：风格锚点
    anchor = style_bank.get_anchor_path()
    if anchor and Path(anchor).exists():
        _bridge_log(f"  [bridge] 镜头{shot['index']}: 参考图=风格锚点")
        return anchor

    return None


def _select_all_character_refs(
    shot: dict,
    bank: CharacterBank,
    characters_data: list[dict],
    limit: int = 2,
) -> list[str]:
    """为镜头选择所有出镜角色的肖像参考图（多角色一致性）.

    与 _select_best_reference 的区别：后者只返回单张最优参考图（含风格锚点回退），
    本函数遍历 ff_desc/prompt 中提到的**所有**角色，返回各自最优视角肖像，
    用于同框多角色镜头——每个角色都传入自己的肖像，避免次要角色长相漂移。

    Args:
        limit: 最多返回的角色肖像数量（默认 2，配合锚定帧共 3 张）

    Returns:
        角色肖像路径列表（按 ff_desc/prompt 中出现顺序），不含锚定帧/风格锚点
    """
    if limit <= 0:
        return []

    ff_desc = shot.get("ff_desc", "").strip() or ""
    prompt = shot.get("prompt", "")
    search_text = f"{ff_desc} {prompt}"
    view_pref = _detect_view_preference(search_text)

    refs: list[str] = []
    is_closeup = any(kw in search_text for kw in ("大特写", "特写", "面部", "表情", "脸部"))
    for char in characters_data:
        identifier = char.get("identifier", "")
        if not identifier or identifier not in search_text:
            continue
        entry = bank.get_character(identifier)
        if not entry or not entry.get("portraits"):
            continue
        portraits = entry["portraits"]
        # 特写镜头：face 面部特写优先（锁五官防变脸）
        if is_closeup:
            face_p = portraits.get("face", {}).get("path")
            if face_p and Path(face_p).exists():
                refs.append(face_p)
                _bridge_log(f"  [bridge] 镜头{shot['index']}: 多角色参考=角色 {identifier} 面部特写")
                if len(refs) >= limit:
                    break
                continue
        # 按优先级尝试：检测到的视角 → front → side → back
        for view in [view_pref, "front", "side", "back"]:
            p = portraits.get(view, {}).get("path")
            if p and Path(p).exists():
                refs.append(p)
                _bridge_log(f"  [bridge] 镜头{shot['index']}: 多角色参考=角色 {identifier} {view}肖像")
                break
        if len(refs) >= limit:
            break

    return refs


# ---------------------------------------------------------------------------
# 首帧/尾帧生成（带校验重试）
# ---------------------------------------------------------------------------

def _validate_frame(path: str, min_kb: float = 5) -> bool:
    """校验生成的帧图片文件是否有效（文件大小 > min_kb KB）."""
    if not path:
        return False
    p = Path(path)
    if not p.exists():
        return False
    size_kb = p.stat().st_size / 1024
    if size_kb < min_kb:
        _bridge_log(f"  [bridge] 帧图片过小: {p} ({size_kb:.1f}KB < {min_kb}KB)，判定无效")
        return False
    return True


async def _maybe_qc_frame(shot: dict, frame_path: str | None, frame_kind: str,
                          characters: list | None = None, empty_scene: bool = False,
                          prompt: str | None = None, variation: str = "",
                          kf_index: int | None = None) -> bool:
    """帧生成后执行视觉质检（软失败，不阻断流水线）.

    通过 shared/image_qc.py 调用 ds-vision-skill 的 vision-router.ps1 对帧图做内容质检；
    QC 成功（ok=True）即写 sidecar（检出与否都写，has_hallucination=false 表示"已检过无问题"，
    供 should_run_qc 靠 sidecar 新鲜度跳过重复质检）。默认封顶 warning，不触发 step02b 自动重生成。
    asyncio.to_thread 避免阻塞事件循环（单次质检 2-4 分钟）。

    prompt: v1.1 语义核对用——实际生成图片的最终 prompt。不传时从
    shot["ff_desc_final"]/["lf_desc_final"] 取（_gen_first_frame_async 挂载）。

    Returns:
        bool（B7 修复 08-10）：True = 检出 critical（调用方应 unlink 坏帧 + 删哈希
        触发重生成——修复 QC 只记日志、坏帧继续进视频的断链）；False = 无 critical。
    """
    if not frame_path:
        return False
    # 未显式传 prompt 时，从 shot 挂载的最终 prompt 取（QC 语义核对用）
    if prompt is None and isinstance(shot, dict):
        if frame_kind == "last_frame":
            prompt = shot.get("lf_desc_final") or shot.get("ff_desc_final")
        elif frame_kind == "keyframe":
            prompt = (shot.get("kf_0_desc_final") or shot.get("kf_desc_final")
                      or shot.get("ff_desc_final") or shot.get("lf_desc_final"))
        else:
            prompt = shot.get("ff_desc_final") or shot.get("lf_desc_final")
    try:
        from shared.image_qc import maybe_qc_sync, sidecar_path, write_sidecar
        result = await asyncio.to_thread(
            maybe_qc_sync, frame_path, shot, frame_kind, characters, empty_scene, prompt,
            kf_index,
        )
        # v6.54.1：完整结果暂存——QC critical 分支据此构建局部修复计划
        try:
            shot["_qc_last"] = result
        except Exception:
            pass
        if not result.ok:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 质检失败跳过（未写 sidecar）: {result.error}")
            return False
        if result.error == "skipped":
            # v6.50：skip 静默是 15:42 坏图带 clean sidecar 流出的排障盲区——
            # 免检必须留痕（帧此前已质检过才允许 skip）
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} sidecar 新鲜免检")
            return False
        # QC 成功 → 写 sidecar（检出与否都写），供下次跳过与 step02b 消费
        try:
            write_sidecar(Path(frame_path), result)
        except Exception as e:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} sidecar 写入失败: {e}")
        # v6.53 A.4：尝试日志落盘（人数/问答答案/判定），重抽对比「哪里变了」
        try:
            import time as _time
            log_path = Path(frame_path).parent / f"{Path(frame_path).stem}_qc_log.json"
            log = []
            if log_path.exists():
                try:
                    log = json.loads(log_path.read_text(encoding="utf-8"))
                except Exception:
                    log = []
            log.append({
                "ts": _time.strftime("%Y-%m-%d %H:%M:%S"),
                "provider": os.getenv("QC_PROVIDER", "auto"),
                "has_hallucination": result.has_hallucination,
                "person_count": getattr(result, "person_count", None),
                "answers": getattr(result, "answers", None),
                "issues": [{"category": i.category, "severity": i.severity,
                            "description": i.description} for i in result.issues],
                # v6.x C 项留痕：per-attempt 变体
                "variation": variation,
            })
            # v6.55 D 方案窄化 B 留痕：high 类关键断言跨 attempt 翻转点
            # （理论上矛盾率 <10%，出现意味着模型状态异常而非 VLM 噪声）
            _flips = getattr(result, "unstable_flips", None)
            if _flips:
                log[-1]["unstable_flips"] = _flips
            log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 尝试日志写入失败: {e}")
        has_critical = result.has_hallucination and any(
            getattr(i, "severity", "") == "critical" for i in result.issues)
        if has_critical:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 检出 critical，"
                        f"将删帧重生成: " + "; ".join(
                            f"[{i.severity}]{i.category} {i.description}" for i in result.issues))
            return True
        if result.has_hallucination:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 检出问题: "
                        + "; ".join(f"[{i.severity}]{i.category} {i.description}" for i in result.issues))
        else:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 质检通过")
        return False
    except Exception as e:
        _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 质检异常（不阻断）: {e}")
        return False


async def _try_qc_repair(image_adapter, shot: dict, frame_path: str, frame_kind: str,
                         characters: list | None, empty_scene: bool, idx, kf_index: int | None = None) -> bool:
    """QC critical 后先试局部修复（v6.54.1 先修后抽）.

    build_repair_plan 分类：局部可修（睁眼/刀接触/红痕/篷/发光）→ doodle 红框
    i2i 改写 1 次（复用 v6.30 机制，bbox 来自 L1 检测）→ 复检；复检通过 = True
    （帧已替换，哈希不删——下次缓存命中的就是修好的图）。全局失败/编辑失败/
    复检仍 critical → False（调用方走原删帧重抽路径）。
    """
    from shared.image_qc import build_repair_plan
    qc_result = shot.get("_qc_last")
    if not qc_result:
        return False
    plan = build_repair_plan(shot, frame_kind, qc_result)
    if plan.get("action") != "edit":
        _bridge_log(f"  [REPAIR] shot_{idx} {frame_kind} 走重抽: {plan.get('reason', '')[:60]}")
        return False
    _bridge_log(f"  [REPAIR] shot_{idx} 局部修复 ({','.join(plan.get('matched_patterns', []))}) "
                f"region={plan['bbox_ratio']} area={plan.get('area_pct')}%")
    tmp = Path(frame_path).parent / f"{Path(frame_path).stem}_repair.png"
    edited = await _doodle_refine_first_frame(
        image_adapter, frame_path, plan["bbox_ratio"], plan["edit_prompt"], str(tmp), idx)
    if not edited or not _validate_frame(edited):
        _bridge_log("  [REPAIR] 编辑失败/输出无效，回落重抽")
        return False
    Path(edited).replace(frame_path)
    # v6.x.x bug fix：_maybe_qc_frame 返回 bool，完整 QcResult 在 shot["_qc_last"]
    # （旧代码用 `qc2.has_hallucination` 触发 AttributeError，触发条件：_try_qc_repair
    # 实际被调用时——之前所有单测都 mock 掉 _try_qc_repair 返 True/False，从未走到
    # 内部 i2i+复检路径，所以 bug 一直藏到真实跑批才暴露）。
    qc2_critical = await _maybe_qc_frame(shot, frame_path, frame_kind, characters, empty_scene, kf_index=kf_index)
    # v6.x.x：runtime type assertion 防回归——_maybe_qc_frame 必须返 bool，
    # 任何返回类型漂移都会立刻定位到本行（静态检查 / 单测都覆盖不到这种类型契约）。
    assert isinstance(qc2_critical, bool), \
        f"_maybe_qc_frame 应返 bool，实测 {type(qc2_critical)}（_qc_last={shot.get('_qc_last')!r}）"
    if qc2_critical:
        _bridge_log("  [REPAIR] 修复后复检仍 critical，回落重抽")
        return False
    _bridge_log("  [REPAIR] 修复后复检通过")
    return True


def _del_frame_and_hash(frame_path, hash_path, label, idx, logger):
    """QC critical 后删除坏帧 + 哈希文件（B7 修复，触发下次重生成）."""
    try:
        Path(frame_path).unlink(missing_ok=True)
    except Exception:
        pass
    try:
        Path(hash_path).unlink(missing_ok=True)
    except Exception:
        pass
    logger.verbose(f"  [FRAME] {label} shot_{idx} QC critical，已删坏帧+哈希，重新生成")


def _get_frame_content_hash(shot: dict, empty_scene: bool = False,
                            reference_paths: list | None = None,
                            lock_kind: str = "layout",
                            extra_fields: dict | None = None) -> str:
    """计算首帧/尾帧的稳定哈希值 v4.

    v4: 新增 _prompt_sig() 对实际图片 prompt (ff_desc/lf_desc) 做规范化哈希，
    解决分镜词（ff_desc/lf_desc/motion_desc）变更后图片缓存不失效的问题。
    规范化（strip + 统一空白字符）避免 LLM 微小格式差异导致永不命中。

    v3: 使用稳定字段（main_subject/scene/core_keywords）代替完整 prompt，
    避免 LLM 生成的 prompt 文本有微小差异（换行/空格/词汇顺序）导致
    哈希永远不匹配，缓存永远失效的问题。

    lock_kind: 图片锁类型——"layout"（完整锁图，含场景布局不变）或
    "no_layout"（动作镜头解除布局锁）。锁类型是 prompt 语义的一部分，
    作为哈希键而非魔法后缀（替代 v6.19 的 _ff_nl/_lf_nl/_kf_nl 后缀补丁，
    审计 S4）。

    reference_paths: 参考图路径。语义变化（增删/换图）应触发重新生成——
    但路径本身是文件引用，哈希路径的规范性已由调用方保证（内容不变则路径语义
    不变）。实际参考图的 mtime/size 变化由上层文件系统缓存处理。
    """
    def _normalize(text: str) -> str:
        """规范化文本：strip + 统一空白字符，消除 LLM 格式噪音。"""
        import re
        t = (text or "").strip()
        t = re.sub(r'\s+', ' ', t)
        return t

    # 稳定字段作为基线
    stable_fields = [
        shot.get("main_subject", ""),      # 主体最稳定
        shot.get("location", ""),          # 物理地点（新增）
    ]
    # 如果 location 为空则回退到 scene
    if not stable_fields[1]:
        stable_fields[1] = shot.get("scene", "")
    # core_keywords 是列表，转为字符串
    keywords = shot.get("core_keywords", [])
    if isinstance(keywords, list):
        keywords_str = ",".join(sorted(keywords))
    else:
        keywords_str = str(keywords) if keywords else ""
    stable_fields.append(keywords_str)

    key_parts = [
        STYLE_PREFIX,                      # 风格前缀变化时首帧也应重新生成
        f"lock={lock_kind}",               # 锁类型是 prompt 语义的一部分
    ]
    key_parts.extend(stable_fields)

    # v4: 规范化后的实际图片 prompt 文本 — 分镜词变了图片就该重新生成
    for field_name in ("ff_desc", "lf_desc", "motion_desc"):
        val = shot.get(field_name, "")
        if val:
            key_parts.append(f"{field_name}={_normalize(val)}")

    # extra_fields: 关键帧专用——哈希含 kf_desc + time_ratio，使每个中间帧独立
    # 哈希且改 kf desc 能触发该帧重出（原复用 shot 哈希，改 kf desc 缓存不失效）。
    for k, v in (extra_fields or {}).items():
        if v is not None:
            key_parts.append(f"{k}={_normalize(str(v))}")

    if empty_scene:
        key_parts.append("empty_scene")    # 空镜判定翻转时帧应重做

    # 参考图路径语义纳入哈希（审计 S4：此前不入哈希，参考图变了但哈希不变）
    for ref in (reference_paths or []):
        key_parts.append(f"ref={_normalize(str(ref))}")

    key = "|".join(key_parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _collect_scene_asset_refs(shot: dict) -> list[str]:
    """为本镜头匹配场景资产图（scenes_bank wide/near）作环境参考.

    用于 step02a 首帧图生图：注入与 r2v 同源的场景资产图，保证首帧的
    场景/环境/光线/船型与全局资产库一致，避免图生图自由发挥导致场景漂移。

    时间段匹配（重要）：资产库的 wide（大场景建立镜头）和 near（近景功能场景）
    常带不同时段/光线——如「照月湾小舟」wide=黄昏暖金、near=夜晚冷月。
    首帧的时间由 ff_desc 决定，若注入时段冲突的场景图（黄昏 wide 配夜晚
    首帧），会把时间带偏。故：画面描述含夜晚/月光/夜色 → 优先 near；
    含黄昏/夕阳/落日 → 优先 wide；无法判断时近景 near 兜底（含正确船型）。
    匹配逻辑与 prompt_builder._collect_r2v_references 保持一致（location 双向子串匹配）。
    """
    try:
        from shared.assets_bank import SceneAssetsBank
        sbank = SceneAssetsBank()
        sbank.load()
        loc = shot.get("location", "") or shot.get("scene", "")
        if not loc:
            return []
        for sid in sbank.get_all_identifiers():
            if not sid:
                continue
            scene = sbank.get_scene(sid) or {}
            match_sources = [sid] + list(scene.get("consistent_terms", []) or [])
            if scene.get("location"):
                match_sources.append(scene["location"])
            # 模糊匹配：公共连续子串 ≥2 字即算同一地点
            # （"照月湾小舟" vs "云河镇照月湾" → 共"照月湾"3字命中）
            from shared.assets_bank import scene_matches_location
            matched = any(scene_matches_location(src, loc) for src in match_sources if src)
            if not matched:
                continue
            wide = sbank.get_wide_image(sid)
            near = sbank.get_near_image(sid)
            # 时间段判定：夜间 → near；黄昏/白天 → wide；默认 near（含正确物体尺寸）
            probe = " ".join([shot.get("ff_desc", ""), shot.get("motion_desc", ""),
                              shot.get("lf_desc", ""), shot.get("prompt", "")])
            night_kw = ("夜", "月", "星光", "星", "灯笼", "烛火", "黑暗", "入夜", "暮色")
            dusk_kw = ("黄昏", "夕阳", "落日", "傍晚", "余晖", "晚霞", "西下")
            is_night = any(k in probe for k in night_kw)
            is_dusk = any(k in probe for k in dusk_kw)
            picked = None
            if is_night and near:
                picked = near
            elif is_dusk and wide:
                picked = wide
            else:
                picked = near or wide
            if picked and Path(picked).exists():
                _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 首帧参考图=场景 {sid} {Path(picked).name}"
                            f"（{'夜→near' if is_night else '昏→wide' if is_dusk else '近景兜底'}）")
                return [picked]
        return []
    except Exception as e:
        _bridge_log(f"  [bridge] 场景资产参考图收集异常: {e}")
        return []


def _is_space_object_prop(entry: dict) -> bool:
    """判断道具 bank 条目是否为空间物体（船/马车/轿等，作环境/尺寸锚点）.

    与 prompt_builder._is_space_object_prop 保持一致（两处各自持一份判定，
    避免跨模块耦合——vimax_bridge 不 import prompt_builder）。
    空间物体决定画面空间比例（防模型画小），手持道具不决定，故拆出优先。
    """
    category = (entry.get("category") or "").strip()
    return category in ("交通工具",) or any(
        w in category for w in ("船", "车", "轿", "辇", "舟")
    )


def _collect_space_object_refs(shot: dict) -> list[str]:
    """收集本镜头出现的空间物体参考图（船/马车/轿，category=交通工具）.

    v6.25：空间物体是环境/尺寸锚点，决定画面空间比例（防船画小），与手持道具
    分开收集——组装时排在场景图后、角色肖像前。图片模型默认按「一个人躺满
    整艘船」画船，船图在 5 名额里若不提前必然被手持道具挤掉（code-audit 08-08
    钩子首帧「3角色+2道具」占满名额，舴艋舟无位）。
    """
    try:
        from shared.assets_bank import PropAssetsBank
        pbank = PropAssetsBank()
        pbank.load()
        search_text = " ".join([
            shot.get("ff_desc", "").strip() or "",
            shot.get("motion_desc", "").strip() or shot.get("prompt", ""),
            shot.get("lf_desc", "").strip() or "",
        ])
        loc = shot.get("location", "") or shot.get("scene", "")
        refs: list[str] = []
        for pid in pbank.get_all_identifiers():
            if not pid:
                continue
            entry = pbank.get_prop(pid) or {}
            if not _is_space_object_prop(entry):
                continue
            match_sources = [pid] + list(entry.get("consistent_terms", []) or [])
            if not any(src and (src in search_text or src in loc) for src in match_sources):
                continue
            img = pbank.get_asset_image(pid)
            if img and Path(img).exists() and img not in refs:
                refs.append(img)
                _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 首帧参考图=空间物体 {pid} {Path(img).name}")
        return refs
    except Exception as e:
        _bridge_log(f"  [bridge] 空间物体参考图收集异常: {e}")
        return []


def _collect_prop_asset_refs(shot: dict, limit: int = 2,
                             frame_desc: str | None = None,
                             characters: list | None = None) -> list[str]:
    """为本镜头匹配手持道具资产图（props_bank prop.png）作道具参考.

    与 r2v 同源匹配：道具名/consistent_terms 出现在本镜 ff_desc/motion_desc/
    lf_desc 或 location 中。注入道具资产图保证首帧出现的道具外观/材质/尺寸
    与全局道具库一致（如钓杆、弯刀），避免图生图自由发挥画错道具。

    v6.25：只收集手持道具（空间物体走 _collect_space_object_refs，单独提前），
    limit 为手持道具上限。由调用方按「场景 → 空间物体 → 角色 → 手持道具」组装。

    frame_desc: 该帧自己的描述（08-16 hook ff 钓竿幻觉根因）——原版 search_text
    把 ff/lf/motion 合并，lf 里的「钓杆」错装进 ff ref sheet，模型照钓杆参考图
    凭空画出脱离手的钓竿。传 frame_desc 后只匹配该帧的道具（首帧=ff_desc /
    尾帧=lf_desc / 关键帧=合并）。None 时回退到合并文本（旧行为）。

    characters: 威胁武器帧级门控（08-16 hook ff 整把匕首画出根因）——威胁源
    在该帧不可见（仅以黑影/刀尖微露方式呈现）时，威胁武器参图也会诱导模型
    照完整匕首放大渲染。传 frame_desc + characters 后做帧级可见性门控：
    威胁不可见 → 丢弃威胁武器参图（ff 只见指甲盖尖端，完整匕首参考图反而
    误导）。kf0「刺客半身探出举匕首」威胁可见 → 保留参图。
    """
    try:
        from shared.assets_bank import PropAssetsBank
        pbank = PropAssetsBank()
        pbank.load()
        if frame_desc is not None:
            search_text = frame_desc
        else:
            search_text = " ".join([
                shot.get("ff_desc", "").strip() or "",
                shot.get("motion_desc", "").strip() or shot.get("prompt", ""),
                shot.get("lf_desc", "").strip() or "",
            ])
        loc = shot.get("location", "") or shot.get("scene", "")
        refs: list[str] = []

        # ---- 先收集所有命中 (pid, hit)，再过滤别名嵌套/重复 ----
        # 同一件道具不得注入两件参考图：匕首同时命中「刺客弯刀」「刺客匕首」时
        # （后者的「寒光匕首」是更具体别名），只保留命中词最长的那件。
        # 08-16 刺客弯刀残留：register_prop 别名去重防源头，此处匹配层兜底。
        hits: list[tuple[str, str]] = []
        for pid in pbank.get_all_identifiers():
            if not pid:
                continue
            entry = pbank.get_prop(pid) or {}
            if _is_space_object_prop(entry):
                continue
            match_sources = [pid] + list(entry.get("consistent_terms", []) or [])
            # 命中词：identifier 精确命中优先，否则按别名长度降序（长词更具体，
            # 如「寒光匕首」优先于「匕首」）。
            hit = None
            if pid and (pid in search_text or pid in loc):
                hit = pid
            else:
                hit = next(
                    (t for t in sorted(match_sources, key=len, reverse=True)
                     if t and t != pid and (t in search_text or t in loc)),
                    None,
                )
            if hit:
                hits.append((pid, hit))

        # 过滤：hit 是另一命中的子串 → 同一件，丢弃（保留更具体别名的那件）；
        # 命中词完全相同时保留先出现的。
        kept = []
        for i, (pid, hit) in enumerate(hits):
            subsumed = False
            for j, (op, oh) in enumerate(hits):
                if op == pid:
                    continue
                if (hit == oh and j < i) or (hit in oh and len(hit) < len(oh)):
                    subsumed = True
                    break
            if subsumed:
                _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 道具「{pid}」命中词"
                            f"「{hit}」被更具体别名覆盖，视为同一件，跳过")
                continue
            kept.append((pid, hit))

        # 08-16 hook ff：威胁武器帧级门控——威胁源在该帧不可见（仅黑影/刀尖
        # 微露）时，威胁武器参图（完整匕首）会诱导模型照放大渲染成大宝剑。ff
        # 只露指甲盖尖端时丢弃，kf0 刺客半身探出举匕首威胁可见→保留。
        if frame_desc is not None and characters:
            from shared.vimax_bridge import _threat_visible_in  # 已在同文件，防循环
            if _threat_visible_in(frame_desc, characters) is not True:
                _dropped = [p for p, _ in kept if _is_threat_weapon_prop(p)]
                kept = [(p, h) for p, h in kept if not _is_threat_weapon_prop(p)]
                for _p in _dropped:
                    _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 威胁武器「{_p}」"
                                f"该帧威胁源不可见，丢弃参图防完整渲染")

        for pid, _ in kept:
            img = pbank.get_asset_image(pid)
            if img and Path(img).exists() and img not in refs:
                refs.append(img)
                _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 首帧参考图=道具 {pid} {Path(img).name}")
            if len(refs) >= limit:
                break
        return refs
    except Exception as e:
        _bridge_log(f"  [bridge] 道具资产参考图收集异常: {e}")
        return []


def _compose_frame_refs_by_category(
    shot: dict,
    for_frame: str,
    scene_refs: list[str],
    space_refs: list[str],
    portrait_refs: list[str],
    prop_refs: list[str],
    limit: int = 5,
) -> list[str]:
    """按类别（环境/人物/道具）把参考图压缩到 IMAGE_MAX_REFS 以内.

    总数不超上限时原样返回（截断到 limit，兼容 wan2.7 等多参考图模型行为）；
    超上限时每个多图类别横向拼成一张同质参考表，三类别合计 ≤3 张：
      - 环境（scene）= 场景资产图 + 空间物体（船/马车，同为空间比例锚点）
      - 人物（chars）= 出镜角色肖像
      - 道具（props）= 手持道具资产图
    相比适配器层「前N张原样 + 尾部混拼」，每张参考表语义同质——模型能明确
    知道这张全是人物长相、那张全是环境，不再出现「2角色+1道具」混拼表。
    上限按当前 IMAGE_PROVIDER 主模型从 shared.ref_compose 注册表解析
    （qwen-image=3 / wan2.7-image=9 / ...），未注册模型回退 IMAGE_MAX_REFS。
    拼图失败时 fit_refs 内部裁剪兜底（保优先级，不中断生成）。
    """
    from shared.ref_compose import fit_refs, active_image_ref_limit
    from shared.paths import vimax_working_dir

    max_refs = active_image_ref_limit()
    buckets = [
        ("scene", list(scene_refs) + list(space_refs)),
        ("chars", list(portrait_refs)),
        ("props", list(prop_refs)),
    ]
    n_total = len({p for _, items in buckets for p in (items or []) if p and Path(p).exists()})
    frame_tag = "ff" if for_frame == "首帧" else "lf"
    shot_dir = shot_work_dir(shot.get("index", 0))
    composed = fit_refs(buckets, max_refs, shot_dir, frame_tag)
    if n_total > max_refs:
        _bridge_log(
            f"  [bridge] 镜头{shot.get('index', '?')} {for_frame}: 参考图 {n_total} 张超上限 "
            f"{max_refs}，按类别无损压缩为 {len(composed)} 张"
        )
    return composed


def _collect_frame_refs_for_shot(shot: dict, characters: list | None = None,
                                 limit: int = 5, for_frame: str = "首帧") -> list[str]:
    """为 step02a 首帧/尾帧图生图收集参考图（场景 → 角色 → 道具）.

    与 r2v 参考图同源、同一套匹配逻辑，保证图生视频前导帧（首帧/尾帧）的
    场景/人物/道具与全局资产库 + r2v 视频一致，避免「首帧画对、视频画偏」
    或「首帧场景/道具与资产库不一致」的漂移（code-audit 08-05）。

    顺序：场景资产图（环境锚定）→ 出镜角色肖像（人物形象）→ 出镜道具资产图。
    空镜只注入场景资产图（禁止角色/道具参考，防人物渗入）。

    for_frame: "首帧"/"尾帧"。动作镜头首帧跳过场景资产图（场景图被硬约束
    "禁止人物"，与多角色参考图矛盾时场景图占上风、人物被压制——钩子首帧
    几乎无人）。尾帧/关键帧保留场景参考图（环境锚定，NO_LAYOUT 锁下无碍）。
    """
    empty_scene = is_empty_scene(shot, characters or [])
    # 动作镜头首帧：不注入场景资产图
    is_action_ff = (for_frame == "首帧") and _is_action_shot(shot, characters) and not empty_scene
    scene_refs = [] if is_action_ff else _collect_scene_asset_refs(shot)
    # 09-13 特写帧场景门控：spec 含「特写」的帧是局部构图，广角场景资产图会
    # 把构图拉回全景（shot_4 lf 手部特写 3 次 QC 未中根因）——跳过场景参考。
    if scene_refs and for_frame in ("首帧", "尾帧"):
        _fk = "ff" if for_frame == "首帧" else "lf"
        if _frame_spec_closeup(shot, _fk):
            scene_refs = []
            _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 特写帧跳过场景资产图（防广角拉构图）")
    if empty_scene:
        return scene_refs[:limit]

    # v6.26：按类别分组拼图。类别内配额放宽（多图类别会被拼成 1 张），
    # 道具不再被角色名额挤掉——旧版平面列表 limit=5 时 3 角色+空间物体
    # 就占满名额，道具永远进不了参考图。
    space_refs = _collect_space_object_refs(shot)
    # 08-16：该帧的描述（首帧=ff_desc / 尾帧=lf_desc / 关键帧=合并全文）。
    # 威胁源（刺客）仅以非实体方式出现时（水下黑影/刀尖微露/没入水中），
    # 在该帧里没有完整刺客 → 不注入肖像，避免图片模型照肖像画出完整
    # 刺客还回头盯镜头。
    if for_frame == "首帧":
        frame_desc = shot.get("ff_desc", "") or ""
    elif for_frame == "尾帧":
        frame_desc = shot.get("lf_desc", "") or ""
    else:
        frame_desc = " ".join(filter(None, [
            shot.get("ff_desc", ""),
            shot.get("lf_desc", ""),
            shot.get("motion_desc", "") or shot.get("prompt", ""),
        ]))
    # 09-13「用到才拼」：肖像按本帧 spec 出镜名单过滤（v6.57 per-frame 事实源），
    # 文本提到≠出镜——「刺客已遁水」类离场语境下文本匹配会把缺席角色拼进
    # 参考图（shot_0 lf extra_person 3 vs 1 根因）。spec 缺失时 roster=None
    # 回退纯文本匹配（旧行为）。
    _roster = None
    if for_frame == "首帧":
        _roster = _frame_spec_roster(shot, "ff")
    elif for_frame == "尾帧":
        _roster = _frame_spec_roster(shot, "lf")
    portrait_refs = _collect_portrait_refs_for_shot(
        shot, characters, limit=max(2, limit - 1), frame_desc=frame_desc, roster=_roster,
    )
    prop_refs = _collect_prop_asset_refs(shot, limit=3, frame_desc=frame_desc, characters=characters)

    return _compose_frame_refs_by_category(
        shot, for_frame, scene_refs, space_refs, portrait_refs, prop_refs, limit=limit,
    )


def _detect_action_subject(shot: dict, characters: list | None = None) -> str:
    """从镜头描述中检测动作主体（动词主语）.

    组合参考图法：多人镜头参考图名额优先给动作主体——它是镜头画面里做核心
    动作的角色（钩子"刺客自水面窜出、匕首劈落"→刺客）。用 motion_desc /
    ff_desc 里「主体+动作动词」的最早出现判定：遍历已知角色名，找到
    「角色名+（了/地/一/向/自…）+动作动词」在描述中出现位置最靠前的那个。

    返回角色名；无法判定时返回空字符串（调用方保持原顺序）。
    """
    text = " ".join([
        shot.get("motion_desc", "").strip() or shot.get("prompt", ""),
        shot.get("ff_desc", "").strip() or "",
        shot.get("prompt", "").strip() or "",
    ])
    if not text:
        return ""
    # 角色名候选：参数 characters（script 顺序优先）→ 注册表 key 兜底
    known = [c.get("identifier", "") for c in (characters or []) if c.get("identifier", "")]
    if not known:
        try:
            from shared.paths import vimax_working_dir
            reg_path = vimax_working_dir() / "character_portraits_registry.json"
            if reg_path.exists():
                import json as _json
                known = list(_json.load(open(reg_path, encoding="utf-8")).keys())
        except Exception:
            known = []
    if not known:
        return ""
    # 动作动词（做核心动作的标记）
    # v1.1：加出水/入水动作（破水/出水/没入/入水/落水）——钩子「刺客破水而出，
    # 刺中杨旭心口；杨旭仰面后倒」里，「破水」最早出现且主语是刺客；缺它时
    # 会误识别「仰面后倒」的主语杨旭，导致 action_ref 生成"杨旭自捅"
    # （code-audit 2026-08-08）。
    action_verbs = (
        "破水", "出水", "窜出", "冲出", "跃出", "没入", "入水", "落水",
        "刺", "劈", "砍", "扑", "杀", "倒", "喊", "叫",
        "尖叫", "仰面", "后倒", "摔", "跑", "追", "跪", "拜", "夺", "抢",
        "拔", "抽", "挥", "斩", "轰", "踹", "踢", "挥手", "怒喝", "厉喝",
        "哭", "笑", "转身", "回头", "出手", "攻击",
    )
    best: str = ""
    best_pos = len(text)
    for verb in action_verbs:
        pos = text.find(verb)
        if pos < 0:
            continue
        # 动词往前找最近的角色名（主语在动词前，通常在 0~8 字内）
        window = text[max(0, pos - 8):pos]
        for ident in known:
            if ident and ident in window:
                if pos < best_pos:
                    best = ident
                    best_pos = pos
                break
    return best


def _head_portrait_path(src: str) -> str:
    """把角色立绘转换为头肩胸像缓存（v6.52 参考图防姿态/持物泄漏）.

    全身站姿会被图生图照抄成画面构图（hook ff 要「半身探出水面」画出站立
    巨人、船尾多出第二个蒙面人实测）；刺客像里的发光匕首也随参考图泄入
    画面。裁到头肩胸后姿态与持物不再入参考，身份特征（脸/发型/上装）保留。

    裁切缓存于 vimax_working/portrait_crops/（源 mtime 变化才重裁）——
    放在收集层而非拼图层，fit_refs 的「≤limit 原图直传」单图路径与拼图
    路径拿到的是同一份已裁图。失败返回原路径（graceful degradation）。
    """
    try:
        from shared.portrait_stitch import crop_head_portrait
        sp = Path(src)
        if not sp.exists():
            return src
        from shared.paths import vimax_working_dir
        out_dir = vimax_working_dir() / "portrait_crops"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{sp.parent.name}_{sp.stem}.png"
        if out.exists() and out.stat().st_mtime >= sp.stat().st_mtime:
            return str(out)
        from PIL import Image as _PILImage
        img = _PILImage.open(sp).convert("RGB")
        crop_head_portrait(img).save(out, "PNG")
        return str(out)
    except Exception:
        return src


def _frame_spec_closeup(shot: dict, frame_kind: str,
                        kf_index: int | None = None) -> bool:
    """特写帧判定（场景资产图门控用）.

    spec 条目（ff/lf/kfs[i]）的 position/pose 含「特写」→ 本帧是局部特写。
    场景资产图是广角建立镜头，塞进特写帧参考图会把构图拉回全景
    （09-13 shot_4 lf「手部特写」3 次 QC 未中根因：参考图全是广角场景+肖像，
    模型只会画全景）。shot_size 字段不可靠（实测为 None），特写信号以 spec
    文本为准（如「画中央（手部特写）」）。
    """
    fl = shot.get("frame_layouts") or {}
    if frame_kind == "keyframe":
        kfs = fl.get("kfs") or []
        entries = (kfs[kf_index]
                   if (kf_index is not None and 0 <= kf_index < len(kfs)) else [])
    else:
        entries = fl.get(frame_kind) or []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if "特写" in (str(e.get("position") or "") + str(e.get("pose") or "")):
            return True
    return False


def _frame_spec_roster(shot: dict, frame_kind: str,
                       kf_index: int | None = None) -> set[str] | None:
    """本帧出镜名单（frame_layouts spec，v6.57 per-frame 事实源）.

    09-13「用到才拼」根修：参考图/拼图只放本帧实际出镜的角色。文本提到 ≠
    出镜——「刺客已遁水」「钓杆已脱手」类离场/否定语境会把缺席角色带进
    文本匹配，全量拼接诱导模型画全员（09-13 shot_0 lf extra_person 3 vs 1
    根因：QC 名单 1 人但 3 张肖像都在 refs 里）。

    frame_kind: "ff" / "lf" / "keyframe"（keyframe 需 kf_index——keyframes
    数组序号，frame_layouts.kfs 与其一一对应）。
    返回 on_screen=true 的 name 集合（**空集 = spec 明确本帧无人出镜**——
    空镜/离场帧，调用方必须照空名单处理，禁止回退文本）；spec 缺失/无条目
    → None（调用方回退文本匹配）。on_screen 缺省判定走 shared.frame_spec.
    is_on_screen（True，单一事实源，与 QC 侧一致）。
    """
    from shared.frame_spec import spec_roster_or_none
    return spec_roster_or_none(shot, frame_kind, kf_index)


def _collect_portrait_refs_for_shot(shot: dict, characters: list | None = None,
                                    limit: int = 3,
                                    frame_desc: str | None = None,
                                    roster: set[str] | None = None) -> list[str]:
    """从 character_portraits_registry.json 为本镜头出镜角色收集肖像参考图.

    用于 step02a 首帧/尾帧图生图：给图片模型注入本镜出镜角色的肖像作参考，
    复杂多人构图（如钩子"刺客半身踞船舷+杨旭仰倒+听香画右尖叫"）才能画出
    正确的人物形象与站位，避免纯文生图把三人构图简化成单人。

    判定"出镜角色"与 _collect_r2v_references 同源：角色名出现在本镜
    ff_desc/motion_desc/lf_desc 中。视角匹配：画面描述含背面/侧面词则优先
    对应视角，否则默认 front。空镜/无角色匹配返回空列表（调用方降级纯文生图）。

    frame_desc: **该帧自己的**描述（ff_desc/lf_desc/combined）。威胁源若在本
    帧仅以非实体方式呈现（水下黑影/刀尖微露/没入水中）→ 不注入肖像——画面
    没有完整刺客，参考图会诱导图片模型画出完整刺客还回头盯镜头（08-16
    hook ff 根因）。返回 None/False 皆跳过。

    roster: **本帧出镜名单**（frame_layouts spec，_frame_spec_roster 返回值）。
    非 None 时只拼名单内角色——文本提到≠出镜（「刺客已遁水」类离场语境
    文本匹配拦不住），全量拼接诱导模型画全员（09-13 shot_0 lf extra_person
    3 vs 1 根因）。None = 回退纯文本匹配（旧行为）。

    characters: script['characters']（含 identifier）；缺省时只依赖注册表 key。
    """
    try:
        from shared.paths import vimax_working_dir
        registry_path = vimax_working_dir() / "character_portraits_registry.json"
        if not registry_path.exists():
            # 回退：全书级 CharacterBank 导出（step02b 直接跑时工作目录可能还没有 registry）
            from shared.character_bank import CharacterBank
            bank = CharacterBank()
            bank.load()
            reg = bank.get_all_portraits_as_registry()
        else:
            import json as _json
            with open(registry_path, "r", encoding="utf-8") as f:
                reg = _json.load(f)
        if not isinstance(reg, dict) or not reg:
            return []

        # 本镜出镜角色：ff_desc/motion_desc/lf_desc 中出现角色名
        search_text = " ".join([
            shot.get("ff_desc", "").strip() or "",
            shot.get("motion_desc", "").strip() or shot.get("prompt", ""),
            shot.get("lf_desc", "").strip() or "",
        ])
        if not search_text:
            return []

        # 全局视角匹配（背面/侧身词对整镜生效）
        view_pref = "front"
        for kw in ("背面", "背影", "背向", "背对", "背身", "后背", "背朝"):
            if kw in search_text:
                view_pref = "back"
                break
        for kw in ("侧身", "侧面", "侧脸", "侧目", "半侧", "侧对", "侧立"):
            if kw in search_text:
                view_pref = "side"
                break

        # v1.1：动作主体「面向画左/画右/朝画X」→ 侧面朝向（如钩子"刺客盯向画右的
        # 杨旭" → 刺客用 side 侧面肖像，避免 front 看镜头把刺客画成面向镜头；
        # code-audit 2026-08-08 钩子首帧刺客面向镜头）。仅对动作主体生效——
        # "画左/画右"也可能出现在其他角色描述里，全局触发会误伤。
        action_subject = _detect_action_subject(shot, characters)
        subject_view = None
        if action_subject:
            _s_idx = search_text.find(action_subject)
            if _s_idx >= 0:
                _tail = search_text[_s_idx:_s_idx + 30]
                from shared.style_prefix import ORIENTATION_KW
                if any(kw in _tail for kw in ORIENTATION_KW):
                    subject_view = "side"

        # 出镜角色名集合：优先用 script characters（identifier），否则注册表 key
        char_names: list[str] = []
        seen = set()
        for c in (characters or []):
            ident = c.get("identifier", "")
            if ident and ident not in seen:
                seen.add(ident)
                char_names.append(ident)
        for ident in reg:
            if ident not in seen:
                seen.add(ident)
                char_names.append(ident)

        # 角色重要性排序（组合参考图法：多人镜头参考图上限内的名额优先给动作主体）：
        # 1. 动作主体（motion_desc/ff_desc 的动词主语，如钩子"刺客自水面窜出"→刺客）
        # 2. 其余出镜角色按出现顺序
        # 这样 IMAGE_MAX_REFS 裁剪/拼图保留下限内的名额时，动作主体肖像优先保住，
        # 而不是被"场景→角色→道具"顺序里的次要角色挤掉。
        if action_subject and action_subject in char_names:
            char_names.remove(action_subject)
            char_names.insert(0, action_subject)

        refs: list[str] = []
        for ident in char_names:
            # 09-13 逐帧出镜名单过滤（spec 事实源，用到才拼）：roster 非 None
            # 时本帧只拼名单内角色肖像——名单外的缺席角色不进入参考图/拼图。
            if roster is not None and not any(
                    a in roster for a in _char_aliases(ident, characters)):
                continue
            # 出镜判定支持威胁源别名（刺客→神秘人/黑衣人——合规替换后文本用
            # "神秘人"，但角色注册表 key 是"刺客"，仅靠 ident in search_text 会漏掉
            # 刺客肖像，导致关键帧/首帧参考图缺威胁源形象、图片模型画不出刺客）。
            if not any(a in search_text for a in _char_aliases(ident, characters)):
                continue
            # 威胁源帧级可见性门控（08-16 hook ff）：该帧自己的描述里威胁源若仅
            # 以非实体方式出现（水下黑影/刀尖微露/没入水中）→ 不注入肖像——
            # 画面没有完整刺客，参考图会诱导图片模型照肖像画出完整刺客。
            if frame_desc is not None and ident in _THREAT_IDS \
                    and _threat_visible_in(frame_desc, characters) is not True:
                continue
            views = reg.get(ident)
            if not isinstance(views, dict):
                continue
            picked = None
            # 动作主体单独判定视角（优先 subject_view，否则用全局 view_pref）
            _ident_view = subject_view if ident == action_subject else view_pref
            for view in [_ident_view or "front", "front", "side", "back"]:
                p = views.get(view, {})
                path = p.get("path", "") if isinstance(p, dict) else ""
                if path and Path(path).exists():
                    picked = path
                    break
            # v6.52 A2：威胁源优先用无武器版立绘（<stem>_no_weapon.png 同目录
            # 存在即换）——原刺客像持发光蓝匕首，是发光武器幻觉的源头之一。
            if picked and ident in _THREAT_IDS:
                _nw = Path(picked).parent / f"{Path(picked).stem}_no_weapon.png"
                if _nw.exists():
                    picked = str(_nw)
                    _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: {ident} 使用无武器版立绘 {_nw.name}")
            # v6.52 A1：立绘 → 头肩胸像缓存（裁姿态/持物，保脸/发型/上装）
            if picked:
                picked = _head_portrait_path(picked)
            if picked and picked not in refs:
                refs.append(picked)
                _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 首帧/尾帧参考图=角色 {ident} {view_pref}肖像")
            if len(refs) >= limit:
                break
        return refs
    except Exception as e:
        _bridge_log(f"  [bridge] 肖像参考图收集异常: {e}")
        return []


# ---------------------------------------------------------------------------
# 动作镜头判定
#
# v6.19 注：action_ref 动作姿态参考图已废弃（code-audit 2026-08-08）——
# 前一刻改造后首帧是静态画面（潜伏水下/水面微澜），静态肖像作参考图锚定
# 身份即可；「破水而出」等动作描述无法翻译成可画的静态姿势，生成的
# action_ref 姿态怪异（半只脚出水/双手伸开），且与首帧/尾帧状态冲突。
# 参考图直接用角色肖像 + 场景/道具资产（gen_shot_frames）。
# ---------------------------------------------------------------------------

# 强动作动词（无具名主体时的兜底判定）。
# 刻意收窄：不含"转身/回头/起/坐/站"等对话镜头高频静态词，避免普通镜头
# 被误判为动作镜头而解除布局锁、导致构图漂移。
_ACTION_STRONG_VERBS = (
    "窜出", "跃出", "冲出", "破水", "腾空", "翻身", "下坠", "跌",
    "劈", "刺", "挥", "扑", "砍", "斩", "撞", "抓", "夺", "举", "捅", "踹",
    "尖叫", "惊呼", "怒喝", "厉喝",
)


def _is_action_shot(shot: dict, characters: list | None = None) -> bool:
    """统一动作镜头判定（修改1/2/3 共用）.

    1. _detect_action_subject 返回非空（motion_desc/ff_desc 找到「角色名+动作动词」）
    2. 兜底：motion_desc/ff_desc 含强动作动词（无具名主体也视为动作）
    钩子镜头（index==0，如破水/惊觉/尖叫）经上面两条天然命中。
    空镜/纯静态描述不判定为动作镜头。
    """
    if is_empty_scene(shot, characters or []):
        return False
    if _detect_action_subject(shot, characters):
        return True
    text = " ".join([
        shot.get("motion_desc", "").strip() or shot.get("prompt", ""),
        shot.get("ff_desc", "").strip() or "",
    ])
    return any(v in text for v in _ACTION_STRONG_VERBS)


def _inject_object_size_anchor(shot: dict, text: str) -> str:
    """把场景物体尺寸锚定注入帧 prompt（防船/车等物体比例漂移）.

    图片模型默认按「一个人躺满整艘船」的直觉画船——尺寸信息在
    script_result.json 的 scene_objects（如 照月湾小舟 6米×1.4米），
    但帧 prompt 只用了 ff_desc/lf_desc（LLM 生成时不一定带尺寸）。
    按 shot.location 匹配 scene_objects，命中则把尺寸+称呼追加到 prompt，
    让首帧/尾帧的物体比例与原文一致（code-audit 2026-08-06 钩子"船太小"）。

    B5 修复（08-11）：
    1. **匹配增强**——旧实现 `src in (loc + text)` 是纯子串匹配，location 写
       「湖上」不写「小舟」（或镜头文本里用别称/漏词）就漏配、尺寸永不注入。
       改为：location 走 `scene_matches_location` 模糊匹配（公共连续子串 ≥2 字，
       如"照月湾小舟"↔"湖上"… 仍不强，但对象名/consistent_terms/形制字
       （舴艋舟/乌篷/扁舟）在镜头全文（ff/lf/motion/prompt/main_subject）任一
       命中即算同一物体），大幅减少漏配。
    2. **比例后验**——注入前校验长宽比合理性（船 2-15m 长宽比应 ∈ [2, 12]，
       超出说明 LLM 推导的尺寸比例可疑，只注入宽/长单值不注入离谱比值），
       防止「长15m 宽0.5m」这类比例崩坏进帧 prompt。

    shot: 镜头 dict（含 location/scene）
    text: 已组装的帧 prompt
    """
    loc = shot.get("location", "") or shot.get("scene", "") or ""
    if not loc:
        return text
    # v6.52：剥离 desc 内嵌的 LLM 尺寸残留——「（宽1.4米，可容3人）」「（长6米
    # 宽1.4米无篷舴艋舟）」与 scene_objects 注入的尺寸自相矛盾（16:34 脚本
    # 实测 6 处 1.4米 vs 注入 1.6米），模型两侧都不信、船宽随机抽奖。
    # 尺寸一律以本函数注入的 scene_objects 锚为准。幂等：剥完再跑无变化。
    text = re.sub(r"[（(][^（）()]*\d+(?:\.\d+)?米[^（）()]*[）)]", "", text)
    try:
        from shared.paths import script_file
        from shared.scene_objects_bank import iter_matched_scene_objects
        import json as _json
        script_path = script_file()
        if not script_path.exists():
            return text
        script = _json.load(open(script_path, encoding="utf-8"))
        objs = script.get("scene_objects", {})
        if not isinstance(objs, dict) or not objs:
            return text
        # 镜头全文（匹配对象名/consistent_terms/形制字用；比 loc+text 覆盖面广）
        probe = " ".join(filter(None, [
            loc,
            shot.get("scene", ""), shot.get("ff_desc", ""), shot.get("lf_desc", ""),
            shot.get("motion_desc", "") or shot.get("prompt", ""),
            shot.get("main_subject", ""),
        ]))
        for name, obj in iter_matched_scene_objects(objs, loc, probe):
            subtype = str(obj.get("subtype", "") or "")
            length = obj.get("length_m")
            width = obj.get("width_m")
            # B5 比例后验：长宽比离谱（长/宽 <1.5 或 >12）说明 LLM 推导的比例可疑，
            # 只注入长度单值（宽度不可信），防「长15m 宽0.5m 的木条」进帧 prompt
            if length and width:
                try:
                    ratio = float(length) / float(width)
                    if ratio < 1.5 or ratio > 12:
                        _bridge_log(
                            f"  [bridge] 场景物体 {name} 长宽比 {ratio:.1f} 离谱，"
                            f"仅注入长度，跳过宽度（B5 比例后验）"
                        )
                        width = None
                except (TypeError, ValueError):
                    width = None
            if not length and not width:
                return text
            # 只注入尺寸/称呼，不注入容量结构（避免与画面描述冲突）
            size_desc = f"该{name}为{subtype}{'，长' + str(length) + '米' if length else ''}{'、宽' + str(width) + '米' if width else ''}"
            # v6.52 船宽相对锚：绝对米数对图片模型无效（P22 精确空间约束无解，
            # hook ff「宽1.6米」照画 0.6 米实测），改用相对人体的语义锚。
            if (obj.get("category") or "") == "船":
                size_desc += "，船宽约为人物肩宽的三倍，人物躺卧后船舷两侧仍有空档"
                # 无篷形制反向锚：「舴艋舟」易被联想成乌篷船（hook ff 实测画出
                # 大竹篷遮挡舱面）——subtype 带「无篷」时显式钉死敞舱。
                if "无篷" in subtype or "无篷" in name:
                    size_desc += "，无船篷无遮盖，舱面完全敞开可见"
            # 近景/中景构图提示（08-09 首帧船太小）：图片模型对纯数字尺寸不敏感
            # （「长6.0米」不认），且全船入画会压缩人物比例。用镜头语言替代：
            # 近景/中景只画船头/船舷局部、人物占画面放大，从构图比例暗示船大。
            if "近景" in text or "中景" in text or "中近景" in text:
                size_desc += "，镜头取近景构图，只画船头/船舷局部，人物占画面比例放大，船体向画外延伸暗示全船长宽"
            if size_desc not in text:
                return f"{text}。{size_desc}"
        return text
    except Exception:
        return text


def _inject_tone_anchor(shot: dict, text: str) -> str:
    """把镜头色调锚点（color_tone：夜景冷蓝/暖金烛光等）注入帧 prompt.

    图片模型对「夜色中」这类弱词不敏感，容易自造色调导致前后镜头漂色
    （08-15 hook 验证：加「冷蓝色调夜景」后风格稳定）。shot.color_tone
    由 LLM 按场景生成（如「冷月清辉，深蓝夜色」），但此前从未进入图片
    prompt，这里追加到帧 prompt，让每镜色调一致。
    """
    tone = (shot.get("color_tone") or "").strip()
    if not tone or tone in text:
        return text
    return f"{text}。场景色调：{tone}"


# ============================================================================
# 角色姿态/位置/朝向锚定块（代码级兜底，关键帧/首帧共用）
#
# 背景（code-audit 08-10）：hook 关键帧「刺客破水而出」被图片模型画成没有刺客、
# 听香站水面（船断）、杨旭站立后倒（本应仰卧）——根因是 keyframes desc 只写一
# 行动作动词，无角色姿态/位置/朝向的代码级强制，完全依赖 LLM 遵守 prompt 规则。
# 本函数从 shot 的 ff_desc/lf_desc/motion_desc 提取出镜角色的姿态/位置/朝向，
# 生成一段「【角色锚定】」追加到帧 prompt，代码级兜底（不依赖 LLM）。
# 覆盖 hook + 普通分镜（走同一 _gen_keyframes_async / _gen_first_frame_async）。
# ============================================================================

_KF_POSE_STATES = (
    "仰卧", "仰面躺", "仰面倒", "躺", "卧", "跪坐", "跪", "坐", "蹲", "趴",
    "俯卧", "直立", "站立", "站", "立", "半身探出", "半身出水", "潜伏",
)
# 位置词：角色名后窗口找第一个出现的位置词（含画面方位 + 船体部位）
_KF_POSITION_WORDS = (
    "船头", "船尾", "船中", "中舱", "船梢", "船舷", "船板", "船侧",
    "画左", "画右", "画中", "画上方", "水中", "水下", "水面", "岸上",
)
# v6.52 前景体型锚的动态入场词：命中 + 画左/画右前景 → 该角色按前景透视
# 被画成巨人的重灾组合（hook ff 神秘人/shot6 张十三跃上船实测）
_FG_DYNAMIC_WORDS = (
    "探出", "跃上", "跃下", "跃入", "扑出", "扑向", "破水", "入画",
    "落水", "窜出", "半身出水", "半身探出", "潜伏", "俯身", "逼近",
)
# 威胁源词表 + 攻击动词（统一从 prompt_semantic.registry 引入）
from step01_script_gen.prompt_semantic import (
    THREAT_WORDS_ENLARGED as _THREAT_WORDS_ENLARGED,
    THREAT_STAB_VERBS as _THREAT_STAB_VERBS,
    THREAT_VISIBILITY_KEYWORDS as _THREAT_VISIBILITY_KEYWORDS,
)


def _char_aliases(char_id: str, characters: list | None = None) -> list[str]:
    """角色名可能出现的称谓别名（供姿态/位置提取用）.

    关键：合规替换会把"刺客"→"神秘人"（content_safety.SENSITIVE_SUBSTITUTIONS），
    script_result 里可能存"神秘人"而非"刺客"。威胁源角色（刺客/杀手/黑衣人）额外
    匹配"神秘人/黑衣人/蒙面人"等替换称谓；普通角色只匹配自身名。
    """
    aliases = [char_id]
    threat_ids = ("刺客", "杀手", "黑衣人")
    if char_id in threat_ids:
        aliases += ["神秘人", "黑衣人", "蒙面人", "黑影", "杀手", "刺客"]
    # 去重保序
    seen = set()
    out = []
    for a in aliases:
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


# 威胁源标识（从 prompt_semantic.registry 引入；identifier 通常为"刺客"）
# _char_aliases 仍只对核心三词展开通称别名（"刺客"+"神秘人"+"黑衣人"等）。
# 与 registry.THREAT_KEYWORDS / WEAPON_KEYWORDS 的关系：相关、互补——
# _THREAT_IDS 是身份词的**精确前缀匹配**（identifier 命中），与词表的**文本
# 子串匹配**（字级 vs 词级）语义不同源，勿互相替代或宣称对齐。
_THREAT_IDS = ("刺客", "杀手", "黑衣人")  # 保留精确匹配；通用检测走 THREAT_KEYWORDS

# v6.56：威胁源可见性门控用的扩展词表——覆盖合规替换称谓（神秘人/蒙面人/黑影）。
# 词表单源 prompt_semantic.registry.THREAT_VISIBILITY_KEYWORDS（v6.60.1 收敛：
# 历史上本处与 shared.image_qc._THREAT 各持一份 6 词字面量手工同步）。
# ident 命中即视为威胁源，别名表命中也算（解决「ident=刺客但 lf_desc 写神秘人」
# 类偏移场景）。下游 _threat_visible_in 仍以「是否伴随可见实体特征」判定是否注入。
_THREAT_KEYWORDS = _THREAT_VISIBILITY_KEYWORDS


def _is_threat_weapon_prop(pid: str) -> bool:
    """道具名是否为威胁源武器（刺客/杀手/黑衣人的刀/匕首/剑/刃/暗器/锤/斧/钩）.

    08-16 hook ff「刀悬浮」根因：刺客匕首这种威胁源武器在帧描述里只露刀尖
    （"一截刀尖微露水面"），通用位置词"在水面"太模糊，配合"正对杨旭方向"
    图片模型把整把刀抬到船与水之间。识别为威胁武器后单独改写位置措辞。

    ⚠️ 与 registry.WEAPON_KEYWORDS 的关系：相关、互补——本表是道具 identifier
    的**字级**匹配（startswith + 单字子串，针对 id 命名形态），WEAPON_KEYWORDS
    是帧描述**词级**匹配；语义不同源，勿互相替代或宣称对齐。
    """
    if not pid:
        return False
    return any(pid.startswith(t) for t in _THREAT_IDS) and any(
        c in pid for c in ("刀", "匕", "剑", "刃", "暗器", "锤", "斧", "钩"))

# 威胁源「可见实体」特征（08-16 hook ff 刺客回头盯镜头根因修复）：
# 威胁源常以**非实体**方式出现在帧描述里——水下黑影轮廓 / 一截刀尖微露水面 /
# 已没入水中。此时画面里没有完整可见的刺客，若仍按「出镜角色」把它的肖像/服装/
# 站位/人数注入，图片模型会照肖像画出完整刺客、还回头盯镜头。
# 判定：别名出现处 8 字前 / 18 字后窗口内，至少一处伴随下列可见特征（身体部位/
# 姿态/持械/目光/贴靠实体表面）→ 可见；全部只落在「黑影/轮廓/刀尖/水下/没入」
# 等非实体语境 → 不可见。刻意不含 坐/站/跪/船头/船尾 等通用词——威胁源水下时
# 邻句角色的这些词会误伤（如「…潜伏水下，听香跪坐船头」会把刺客判成可见）。
_THREAT_VISIBLE_HINTS = (
    # 形态/身体部位
    "头肩", "半身", "全身", "身影", "蒙面", "面孔", "湿透", "身形",
    # 姿态（蹲踞/探出/露出；坐站跪是邻句高频通用词，不进提示词）
    "蹲", "踞", "探出", "露头", "露出", "浮出", "冒出",
    # 动作（破水而出/窜出/攻击/持械）
    "破水", "窜出", "冲出", "跃出", "腾空", "刺向", "刺入", "下刺",
    "劈", "砍", "挥", "扑", "抓", "拔", "抽", "攥", "举", "握", "持", "执",
    # 目光/五官（v6.50 修复：「斜」单字太宽，"斜阳"/"斜坡"等无关命中）
    "目光", "盯", "瞪", "斜望", "斜视", "看", "望",
    # 贴靠实体表面（非水下）
    "船舷", "船帮", "船头", "船尾", "岸边", "堤",
)

# v6.69（09-12 shot4 尾帧拦帧事故）：贴靠表面词紧邻方向词时是**移动目标**不是
# 位置证据——「水面下黑影更清晰，朝船头方向移动」≠ 黑影贴在船头。旧逻辑把
# 「船头」当可见证据 → 判可见实体 → 默认分支注入「禁止…黑影」与剧情黑影对称
# 矛盾，prompt_contradiction fail-fast 拦下整镜帧图。
_SURFACE_HINTS = ("船舷", "船帮", "船头", "船尾", "岸边", "堤")
_DIR_PRECHARS = "朝向往至到近"
_DIR_BIGRAMS = ("游向", "移向", "滑向", "扑向", "划向", "逼向", "逼近", "靠近")
_SENT_ENDS = "。！？；\n"


def _surface_hint_all_direction(window: str, h: str) -> bool:
    """表面词 h 在 window 内的**全部**出现都紧跟方向词 → 移动目标非位置证据.

    任一真实贴靠出现（如「踞蹲船舷」）即不算全方向 → 返回 False（保留证据）.
    """
    starts = [p for p in range(len(window)) if window.startswith(h, p)]
    if not starts:
        return False
    return all(
        (p > 0 and window[p - 1] in _DIR_PRECHARS)
        or (p > 1 and window[p - 2:p] in _DIR_BIGRAMS)
        for p in starts
    )


def _threat_visible_in(desc: str, characters: list | None = None) -> bool | None:
    """威胁源（刺客/杀手/黑衣人）是否在 desc 中作为「可见实体」出现.

    出镜判定对威胁源加可见性门控：仅当威胁源别名出现在 desc 中且至少一处伴随
    可见实体特征才返回 True；只以「黑影/轮廓/刀尖/水下/没入水中」等非实体方式
    出现 → False（画面没有完整刺客，不注入参考图/服装/站位/人数）；desc 未提及
    威胁源 → None（不适用）。

    例：
      hook ff   "…水下黑影轮廓，一截刀尖微露水面…"         → False（不可见）
      hook lf   "神秘人已彻底没入水中，水面只剩剧烈涟漪…"   → False（已没入）
      hook kf0  "画右水面刺客半身探出、右手举匕首…"         → True（可见）
      shot6 ff  "神秘人潜伏于画左水下，只露头肩，目光斜望…" → True（露头肩）
      shot7 ff  "神秘人浑身湿透踞蹲船舷，右手紧握匕首…"     → True（踞蹲船舷）
      shot4 lf  "…水面下黑影更清晰，朝船头方向移动…"        → False（v6.69：
                「朝船头」是移动目标非贴靠位置；且前窗不跨句，「上一句的船舷」
                不再充当本句黑影的位置证据）
    """
    if not desc:
        return None
    # 威胁源别名集合（含合规替换称谓：神秘人/黑衣人/蒙面人/黑影/杀手）
    aliases: list[str] = []
    seen = set()
    for tid in _THREAT_IDS:
        for a in _char_aliases(tid, characters):
            if a not in seen:
                seen.add(a)
                aliases.append(a)
    occ: list[int] = []
    for a in aliases:
        if a not in desc:
            continue
        start = 0
        while True:
            i = desc.find(a, start)
            if i < 0:
                break
            occ.append(i)
            start = i + len(a)
    # v6.50（08-28）：剔除「视线提及」型出现——「目光凝望刺客/看向神秘人」
    # 里威胁源是「看」的宾语（可能在场也可能画外），不是可见实体证据。
    # 08-28 实测：ff 唯一的「刺客」出现是视线宾语 → 被旧逻辑判在场 →
    # 「禁止黑影」尾巴拼进模板 → 与剧情黑影对称矛盾（矛盾自检器拦下）。
    _GAZE_REF_TAILS = ("望向", "看向", "盯向", "望着", "看着", "盯着",
                       "凝望", "注视", "凝视", "瞪向", "望", "看", "盯")
    occ = [i for i in occ
           if not any(desc[max(0, i - 2):i].endswith(s) for s in _GAZE_REF_TAILS)]
    if not occ:
        return None
    # 08-23 防御加固：ff 描述"黑衣身影轮廓若隐若现/一截刀尖微露水面"时，命中
    # 的 hint 是弱形体词「身影/黑影」，但"轮廓/若隐若现/水下/水面下"说明威胁源
    # 仍是**非实体**（水下黑影、未完整现形）。若该窗口同时含弱形体词 + 非实体词
    # → 判不可见（否则参考图注入会逼图片模型画出完整刺客——用户反复反馈的
    # "刺客被完整画出且戴动物面具"根因之一）。
    _NON_ENTITY_HINTS = ("轮廓", "若隐若现", "隐约", "水下", "水面下", "水底", "黑影")
    _WEAK_FORM_HINTS = ("身影", "黑影", "轮廓")
    # v6.50 修复（2026-09-04）：否定语境优先级——窗口含「未/不/没/无 + 露出/露面
    # /浮出/冒头/破水/跃出」类显式否定 → 即使命中 visible hint，也算不可见证据。
    # 样本：「蒙面神秘人完全潜伏水面下未露出任何身体部位」——窗口命中"蒙面" +
    # "露出"（"未露出"里的子串），旧逻辑判 True → 默认分支硬塞「禁止…黑影」与
    # ff_desc 「一道黑影…潜伏水下」对称矛盾，矛盾自检器 fail-fast 拦下。修后
    # 窗口含「未露出」类显式否定 → 跳过该位置（不当可见证据），函数最终返 False →
    # 走不可见分支（精化模板「正向陈述黑影」绕开正则匹配）。
    _NEG_TOKENS = ("未", "不", "没", "无", "无任何", "未有任何", "未露出任何")
    _NEG_VISIBLE_TARGETS = ("露出", "露面", "浮出", "冒头", "冒出", "破水", "跃出", "探出",
                            "露头", "出水面", "窜出", "冲出", "现身", "出现")
    for i in occ:
        # 否定语境检查用更长后文窗口（默认 visible hint 窗口是 i+18 不够——
        # 「没冒出」/「未露出」可能在威胁源别名后 13-25 字符处）。
        _neg_window = desc[max(0, i - 8):i + 30]
        _has_neg = any(
            (neg + tgt in _neg_window or tgt + neg in _neg_window)
            for neg in _NEG_TOKENS
            for tgt in _NEG_VISIBLE_TARGETS
        )
        if _has_neg:
            # 找到「否定 + 可见动作」组合 → 跳过该位置（不当可见证据）
            continue
        # 无否定语境 → 走原 visible hint 判定（用原 18 字符窗口）
        # v6.69：窗口不跨句——8 字前窗曾跨过句号把上一句的「船舷」捞进本句
        # 威胁源窗口（「钓杆仍靠船舷垂入水中。船边水面下黑影…」实测），上一句
        # 的贴靠证据被误记到本句黑影头上 → 误判可见 → 同上对称矛盾拦帧。
        _head = desc[max(0, i - 8):i]
        _hcuts = [_head.rfind(_b) for _b in _SENT_ENDS if _head.rfind(_b) >= 0]
        if _hcuts:
            _head = _head[max(_hcuts) + 1:]
        _tail_full = desc[i:i + 18]
        _tcuts = [_tail_full.find(_b) for _b in _SENT_ENDS if _tail_full.find(_b) >= 0]
        _tail = _tail_full[:min(_tcuts)] if _tcuts else _tail_full
        window = _head + _tail
        hit = [h for h in _THREAT_VISIBLE_HINTS if h in window]
        hit = [h for h in hit
               if not (h in _SURFACE_HINTS and _surface_hint_all_direction(window, h))]
        if not hit:
            continue
        # 命中全是弱形体词且窗口含非实体词 → 不可见
        if all(h in _WEAK_FORM_HINTS for h in hit) and any(n in window for n in _NON_ENTITY_HINTS):
            continue
        # v6.50 修复（2026-09-04）：「露出 Xcm/几厘米/一截/小截」= 部分露出
        # （弱实体）→ 不当完整可见证据。样本 hook ff_desc「头顶斜上方一道黑影
        # 潜伏水下，仅头顶约 2cm 露出水面」——命中"露出"+窗口含"2cm"，应判
        # 不可见 → 走精化分支，否则默认分支硬塞「禁止…黑影」与描述「一道黑影」
        # 对称矛盾。
        # 注意：只接受具体小尺寸词，不含"只/部分/仅"（太宽泛，会把「只露头肩」
        # 「部分露出上半身」等真实可见也误判）。
        _PARTIAL_HINTS = ("露出", "露面", "露头", "浮出", "冒出")
        _PARTIAL_QUANTIFIERS = ("小截", "一截", "Xcm", "cm",
                                 "几厘米", "2cm", "3cm", "5cm", "10cm",
                                 "0.5cm", "1cm", "1.5cm",
                                 "0.1", "0.2", "0.3")
        if any(h in _PARTIAL_HINTS for h in hit) and \
           any(q in window for q in _PARTIAL_QUANTIFIERS):
            continue
        return True
    return False


def _extract_kf_pose(text: str, char_id: str) -> str:
    """从文本中角色名附近提取姿态词（长词优先，'立'排除立刻/独立等）.

    按角色名在文本中的出现顺序逐个窗口匹配——取**最早出现且有姿态**的位置
    （ff 在前、lf 在后，ff 的"仰卧"优先于 lf 的"仰面倒"，保证关键帧取基础姿态）。
    """
    if not text:
        return ""
    poses = sorted(_KF_POSE_STATES, key=len, reverse=True)
    # 收集该角色所有出现位置（按别名合并，按位置排序）
    occ = set()
    for alias in _char_aliases(char_id):
        idx = text.find(alias)
        while idx >= 0:
            occ.add(idx)
            idx = text.find(alias, idx + len(alias))
    for pos0 in sorted(occ):
        window = text[pos0:pos0 + 12]
        for pose in poses:
            if pose in window:
                if pose == "立" and any(w in window for w in ("立刻", "独立", "立时", "立地")):
                    return ""
                return pose
    return ""


def _extract_kf_position(text: str, char_id: str) -> str:
    """从文本中角色名后 12 字窗口提取位置词（船头/画左/水中 等）."""
    if not text:
        return ""
    occ = set()
    for alias in _char_aliases(char_id):
        idx = text.find(alias)
        while idx >= 0:
            occ.add(idx)
            idx = text.find(alias, idx + len(alias))
    for pos0 in sorted(occ):
        window = text[pos0:pos0 + 12]
        for pos in _KF_POSITION_WORDS:
            if pos in window:
                return pos
    return ""


def _extract_char_layout(
    shot: dict,
    characters: list | None = None,
    role_scan_text: str | None = None,
    water_state: bool = True,
    frame_kind: str | None = None,
    kf_index: int | None = None,
) -> str:
    """提取本镜出镜角色的姿态/位置/朝向锚定块（关键帧/首帧共用）.

    策略：ff 是动作「前一刻」的静态完整布局（谁在船头/船尾/画左/画右/什么姿态），
    关键帧是动作「中间态」——以 ff 提取的基础姿态为准，只叠加关键帧自身的动作
    变化，避免姿态锚定与 kf_desc 的动作描述冲突（如 kf 写"杨旭后仰"时仍锚定
    "杨旭仰卧船头"——两者其实一致，后仰是仰卧的延续）。

    water_state=True（关键帧）：补"完整小舟"+"水面浪花"（场景状态锚定）。
    water_state=False（首帧）：只取姿态/位置/朝向，不补水面浪花——但船体完整性
    （"完整小舟、船头船尾齐全"）是基础构图约束，无论首/尾/关键帧只要画面有船有
    角色都应补（08-23 用户反馈：首帧"船身延伸出画外"把船尾裁掉）。

    v6.61（2026-09-05）spec 驱动迁移（治 09-05 hook lf 站立刺客 / kf 三重矛盾根因）：
    - 传 frame_kind 时角色出镜表 = frame_layouts.{ff/lf/kfs[i]} on_screen=true
      （与 _inject_frame_spec / QC _expected_persons 同源），威胁源可见性由
      spec on_screen 字段判定，**不再用合并 scan 跑 _threat_visible_in**——
      旧合并文本（含 ff/motion 的「潜伏/破水」）会让 lf 帧误判威胁源可见，
      锚定块写出「神秘人…朝攻击目标」逼模型画站立刺客。
    - on_screen=false 角色完全不进锚定块（lf 互斥在锚定块路径的复发即此修）。
    - 老章降级：shot 无 frame_layouts 或未传 frame_kind → 旧合并 scan +
      _threat_visible_in 启发式（保留兼容）。

    返回形如：
      【角色锚定】杨旭仰卧船头（面向画右），听香跪坐船尾（面向画左），
      刺客半身探出画右水面（面向画左杨旭）。完整小舟在画中，水面浪花翻涌。
    无可用信息时返回 ""（调用方不追加，绝不堆叠）。
    """
    if not shot or not characters:
        return ""
    try:
        if is_empty_scene(shot, characters):
            return ""
        # 角色判定文本：role_scan_text 优先（关键帧合并全文），否则合并 shot 字段
        scan = role_scan_text or " ".join(filter(None, [
            shot.get("ff_desc", ""), shot.get("lf_desc", ""),
            shot.get("motion_desc", "") or shot.get("prompt", ""),
        ]))
        if not scan:
            return ""

        # ── v6.61 spec 驱动出镜表（新章；与 _inject_frame_spec/QC 同源）──
        # onstage/on_screen 决定谁进锚定块；威胁源可见性由 spec 判定，
        # 不再走合并 scan 的 _threat_visible_in（旧路径误判 lf 可见）。
        _fl = shot.get("frame_layouts")
        _spec_items: list | None = None
        if frame_kind and isinstance(_fl, dict):
            try:
                from shared.frame_spec import get_frame_spec
                _spec_items = [
                    s for s in get_frame_spec(shot, frame_kind, kf_index)
                    if isinstance(s, dict) and s.get("on_screen") is True
                ]
            except Exception:
                _spec_items = None
        if _spec_items is not None:
            # spec 模式：spec 里的名字经 alias 折叠映射回 characters
            _spec_names = {str(s.get("name", "")).strip() for s in _spec_items
                           if str(s.get("name", "")).strip()}
            def _char_in_stage(c):
                ident = c.get("identifier", "")
                if not ident:
                    return False
                # spec 名（可能是合规替换后的"神秘人"）↔ identifier（"刺客"）
                return any(nm in _spec_names for nm in _char_aliases(ident, characters))
            onstage = [c for c in characters if _char_in_stage(c)]
        else:
            # 旧章降级：合并 scan 启发式 + 威胁源可见性门控（原逻辑）
            def _char_in_stage(c):
                ident = c.get("identifier", "")
                if not ident:
                    return False
                if not any(a in scan for a in _char_aliases(ident, characters)):
                    return False
                if ident in _THREAT_IDS and _threat_visible_in(scan, characters) is not True:
                    return False
                return True
            onstage = [c for c in characters if _char_in_stage(c)]
        if not onstage:
            return ""

        # 结构化站位（规则51/站位台吸收 v6.35）优先：shot.char_layout 直接给
        # 位置/朝向，文本扫描提供姿态（pose）作基础，缺项回退到文本提取。
        layout = shot.get("char_layout") or []
        layout_by_name = {}
        if isinstance(layout, list):
            for item in layout:
                if isinstance(item, dict):
                    nm = str(item.get("name", "")).strip()
                    if nm:
                        layout_by_name[nm] = item

        parts = []
        # v6.61：spec 模式下建立 per-frame 站位表（name → spec item），
        # 优先级高于 char_layout 与 scan 提取——spec 是该帧自己的出镜表，
        # 治「神秘人潜伏水面（取自 ff_desc）vs 单膝跪船舷（kf1 desc）」三重矛盾。
        _spec_by_ident: dict[str, dict] = {}
        if _spec_items is not None:
            for _si in _spec_items:
                _snm = str(_si.get("name", "")).strip()
                if not _snm:
                    continue
                # spec 名折叠回全部 identifier 别名（神秘人→刺客）
                for _c in characters:
                    _cid = str(_c.get("identifier", "")).strip()
                    if _cid and _cid not in _spec_by_ident and _snm in _char_aliases(_cid, characters):
                        _spec_by_ident[_cid] = _si
        for c in onstage:
            ident = c.get("identifier", "")
            pose = _extract_kf_pose(scan, ident)
            # v6.53 alias 感知查找：char_layout.name 经 alias 折叠（刺客→神秘人、
            # 船夫→群演），与 identifier 字面不同——按别名链逐个试。
            layout_item = None
            for _nm in _char_aliases(ident, characters):
                if _nm in layout_by_name:
                    layout_item = layout_by_name[_nm]
                    break
            # v6.61：spec 站位优先（该帧自己的出镜表；char_layout 是全镜共享兜底）
            _spec_item = _spec_by_ident.get(ident)
            if _spec_item is not None:
                layout_item = _spec_item
                # spec 模式下 pose 以 spec 为准：scan 是合并全文（含 ff/lf/motion
                # 的跨帧姿态「潜伏/仰倒」），与 kfs[i] 本帧 pose 直接矛盾——
                # kf1 实测「潜伏（单膝跪舷）」双姿态并存。spec 有 pose 时丢弃
                # scan 提取值，保证单帧单一姿态。
                if str(_spec_item.get("pose", "")).strip():
                    pose = ""
            seg = ident
            if pose:
                seg += pose
            if layout_item:
                pos = str(layout_item.get("position", "")).strip()
                # v6.61：spec 项无 facing 字段——映射 pose（"单膝跪舷"）+
                # gaze_target（"画中下方杨旭"）到朝向（与 derive_char_layout
                # 的 pose→facing 映射同源）；char_layout 老结构仍读 facing。
                facing = str(layout_item.get("facing", "")).strip()
                if not facing:
                    facing = str(layout_item.get("pose", "")).strip()
                _gaze = str(layout_item.get("gaze_target", "")).strip()
                if _gaze and _gaze not in ("无", "无视线", ""):
                    facing = f"{facing}，目光看向{_gaze}" if facing else f"目光看向{_gaze}"
                # v6.45 char_layout.surface 注入（站位错位兜底）：角色所在承重
                # 面（如"船尾"/"船头"/"甲板"）必须在锚定块里出现，否则 LLM 自由
                # 发挥成"船外的水面"——08-26 hook 0 听香站在水里实测。
                # 强化策略：surface 用"在{surface}上"括号显式（"立船头" 弱 →"立船头（在舴艋舟上）"强），
                # 防 image model 把"画右"读成远离船的方位把听香画到水里。
                # v6.62（09-06）：_FALLBACK_SURFACE="未指定"（shared/frame_spec.py
                # 派生兜底值）是 truthy 占位符，不能当真实 surface 拼进锚定块
                # （实测 13/13 镜锚定块出现「在未指定上」垃圾文本）。
                surface = str(layout_item.get("surface", "")).strip()
                if surface in ("", "未指定"):
                    surface = ""
                if surface and not pose.endswith(surface):
                    seg += surface
                if surface and f"在{surface}" not in seg:
                    seg += f"（在{surface}上）"
                if pos:
                    seg += pos
                if facing:
                    seg += f"（{facing}）"
            else:
                pos = _extract_kf_position(scan, ident)
                if pos:
                    seg += pos
                # 朝向：extract_orientation 有(主体,目标) → 面向{目标}
                try:
                    from shared.style_prefix import extract_orientation
                    orient = extract_orientation(scan, characters)
                    for subject, target in orient:
                        if subject == ident:
                            seg += f"（面向{target}）"
                            break
                except Exception:
                    pass
            # v6.52 前景体型锚：前景机位 + 动态入场姿态（探出/跃上/扑出/破水…）
            # 或威胁源时，图片模型按前景透视把该角色画成巨人（hook ff 神秘人
            # 手臂比船宽还长实测）。相对体型锚压回正常人体比例。只挂本角色
            # seg 末尾，中景/背景条目不受影响。动态词查 scan 全文而非姿态表——
            # 跃上/扑出/破水这类动词不在 _KF_POSE_STATES 里，但恰是巨人化重灾。
            if ("前景" in (pos or "")) and (
                    any(w in scan for w in _FG_DYNAMIC_WORDS) or ident in _THREAT_IDS):
                seg += "（体型与画面其他人物相当，透视正常，非巨人）"
            parts.append(seg)

        if not parts:
            return ""
        block = "【角色锚定】" + "，".join(parts)

        # 威胁源朝向兜底：有威胁源而 scan 无朝向表达 → 追加描述句
        # v6.61：spec 模式下「威胁源在 onstage」= spec 判定该帧可见，scan 含
        # ff/motion 的「潜伏/破水」不再影响判定——兜底只对真正的可见威胁源触发
        # （旧逻辑 has_threat 查 scan 全文 → lf 威胁源已没入仍追加朝向句）。
        if _spec_items is not None:
            has_threat = any(
                _spec_by_ident.get(c.get("identifier", ""), {}).get("name", "")
                and any(a in _THREAT_KEYWORDS for a in _char_aliases(c.get("identifier", ""), characters))
                for c in onstage
            )
        else:
            has_threat = any(w in scan for w in _THREAT_WORDS_ENLARGED)
        has_orient = any(k in scan for k in (
            "面向", "朝向", "侧身", "紧盯画", "盯向画", "望向画", "朝画", "逼近",
        ))
        if has_threat and not has_orient:
            # v6.43（08-23 审计）：原指令式「威胁源必须面向其攻击目标，禁止
            # 背对目标或正对镜头」被 image-facing 锚定块吸入，会被 kf 文本
            # 当作结构化 pose 描述「正对镜头」/「背对目标」误读，触发自噬。
            # 改写为描述性短句「身体朝攻击目标方向，目光与攻击对象同侧」。
            block += "，身体朝攻击目标方向，目光与攻击对象同侧"

        # v6.53.1：收集「水面角色」（surface/pose 表明身体在水里）——船体锚的
        # 「所有角色必须严格在船上」与 desc「神秘人半身探出水面」直接矛盾，
        # 模型随机调和（hook ff 画成推船随行实测）。分句后矛盾消除。
        _WATER_MARKS = ("水面", "水中", "水下", "半身", "潜")
        _water_names: list[str] = []
        for c in onstage:
            ident_c = c.get("identifier", "")
            li = _spec_by_ident.get(ident_c)
            if li is None:
                for _nm in _char_aliases(ident_c, characters):
                    if _nm in layout_by_name:
                        li = layout_by_name[_nm]
                        break
            _surf = str((li or {}).get("surface", "") or (li or {}).get("position", "")) \
                if isinstance(li, dict) else ""
            # v6.61：spec 模式水面状态只看 spec 的 position/pose（该帧自己的
            # 事实）——scan 含 ff/lf/motion 跨帧水词（「潜伏水下」），KF2 刺客
            # 半俯身船上会被误收「处于水面/水中」。
            if _spec_items is not None:
                _water_probe = _surf + str((li or {}).get("pose", "") if isinstance(li, dict) else "")
            else:
                _water_probe = _surf + (_extract_kf_pose(scan, ident_c) or "")
            if any(w in _water_probe for w in _WATER_MARKS):
                _water_names.append(ident_c)

        # 船体完整性锚（首/尾/关键帧共用）：场景含船且画面有角色 → 补"完整小舟、
        # 船头船尾齐全"。08-23 从 water_state 条件拆出——首帧（water_state=False）
        # 原先不补，图片模型照 ff_desc 的"延伸出画外"裁掉船尾；船体完整是基础
        # 构图约束不是动作状态，不应随首帧省略。
        try:
            import json as _json
            from shared.paths import script_file
            script_path = script_file()
            if script_path.exists():
                script = _json.load(open(script_path, encoding="utf-8"))
                objs = script.get("scene_objects", {}) or {}
                boat = next((o for o in objs.values()
                             if isinstance(o, dict) and o.get("category") == "船"), None)
                # 2026-09-12 根因修复：船体完整性条款加「本镜在船上」门控——原实现
                # 只要 bank 有船条目就对本章所有镜头全局开火，把岸上镜头的角色也硬
                # 扳到船上（镜8 张十三在湾堤上，连续 3 次被画成在船上/水中）。
                # 仅当本镜 location/scene 表明是船上场景（船/舟/舱/甲板）才注入。
                _loc_scan = " ".join(filter(None, [
                    str(shot.get("location", "") or ""), str(shot.get("scene", "") or ""),
                ]))
                _on_boat_scene = any(w in _loc_scan for w in ("船", "舟", "舱", "甲板"))
                if (boat and _on_boat_scene
                        and "完整小舟" not in block and "整船" not in block):
                    # v6.45（08-26）⚠️ 强调前缀——image model 对末尾约束遵守率低，
                    # 加 ⚠️ 符号提升视觉权重（与"画面稳定·不闪烁"等约束同款模式）。
                    if _water_names:
                        # v6.61：spec 模式用各角色自己的 spec pose 描述水面状态——
                        # 旧模板硬编码「半身浸在船侧水中、呈袭击/威胁姿态」，
                        # 对 ff「潜伏水下仅暗影」过于激进（把模型推向画完整
                        # 袭击者，09-05 hook ff 4 次 critical 链一环）。
                        _pose_by_name = {
                            str(((_spec_by_ident.get(c.get("identifier", "")) or {}).get("pose", "")) or "").strip()
                            for c in onstage if c.get("identifier", "") in _water_names
                        }
                        _pose_by_name.discard("")
                        if _spec_items is not None and _pose_by_name:
                            _wnames = "、".join(_water_names)
                            _poses = "、".join(sorted(_pose_by_name))
                            block += (f"。⚠️ 完整小舟在画中、船头船尾齐全不缺失。"
                                      f"{_wnames}处于水面/水中（{_poses}），"
                                      f"身体朝向船上目标（禁止推船、撑船、扶船随行）；"
                                      f"其余角色必须严格在船上，禁止出现在船外水面")
                        else:
                            # 有水面角色：船上/水中分句，并点名袭击姿态（防「推船随行」）
                            _wnames = "、".join(_water_names)
                            block += (f"。⚠️ 完整小舟在画中、船头船尾齐全不缺失。"
                                      f"{_wnames}半身浸在船侧水中、身体静止面向船上目标，"
                                      f"呈袭击/威胁姿态（禁止推船、撑船、扶船随行）；"
                                      f"其余角色必须严格在船上，禁止出现在船外水面")
                    else:
                        block += "。⚠️ 完整小舟在画中、船头船尾齐全不缺失，**所有角色必须严格在船上**，禁止任何角色出现在船外水面/船头船尾缺失"
        except Exception:
            pass
        # 关键帧补水面浪花（动作状态锚定，仅关键帧）
        if water_state:
            if ("破水" in scan or "刺" in scan or "浪花" in scan or "出水" in scan) \
                    and "浪花" not in block and "水花" not in block:
                block += "，水面浪花翻涌"

        return block
    except Exception:
        return ""


# ============================================================================
# 道具空间方位锚定块（A3 修复 08-10）
#
# 背景（rule50/22.9）：同框多件手持道具必须写清各自落点，但 ff_desc 里的道具方位
# （如"左手钓杆垂于水面、右手酒壶倾洒"）**从未被提取进帧 prompt 锚定**——step02a
# 三帧只有【角色锚定】，图片模型自由摆放道具导致酒壶钓杆重叠（08-09 钩子坑）。
# 本函数从 shot 文本提取出镜道具及其方位词，生成「【道具锚定】」块注入三帧，
# 代码级兜底（不依赖 LLM 遵守 rule50/22.9）。
# ============================================================================

# 道具方位词（画面方位 + 手部 + 位置落点；覆盖 rule22.9 的空间词）
_PROP_POSITION_WORDS = (
    "画左", "画右", "画中", "画上方", "画下方",
    "左手", "右手", "双手", "手中", "手边", "身侧", "身前", "身下",
    "前方", "身后", "头顶", "肩头", "腰间", "背后",
    "水中", "水下", "水面", "船头", "船尾", "船板", "船舷", "船侧", "甲板",
    "地上", "岸上", "桌上", "几上", "案上",
)
# 道具方位词命中窗口（道具名后找方位）
_PROP_POSITION_WINDOW = 16

# v6.45：威胁武器入体方位词——匕首/刀/剑「插/扎/刺在角色身体部位」时，
# 标准方位词表没有胸口/心口等身体词，且角色名距武器常超 12 字窗口，
# 导致【道具锚定】整体丢失（08-26 hook kf0 匕首消失根因）。仅威胁武器 +
# 身体部位词命中时兜底，普通道具不触发。
_BODY_POSITION_WORDS = ("胸口", "心口", "胸前", "腹部", "后背", "后心",
                        "咽喉", "喉头", "肩头", "腰眼")


def _extract_prop_layout(
    shot: dict,
    characters: list | None = None,
    role_scan_text: str | None = None,
) -> str:
    """提取本镜出镜道具及其方位锚定块（首帧/尾帧/关键帧共用）.

    从 shot 文本（ff_desc/prompt 等）匹配 prop_assets 库中的道具名/consistent_terms，
    对每个匹配道具提取其周围 16 字的方位词（画左/右手/身侧/水中…），生成
    「【道具锚定】」块。命中多个道具且各带方位 → 输出（防模型自由摆放重叠）。

    策略：文本已有方位 → 保留；无方位 → 从同句角色方位推断（如"杨旭左手持钓杆"
    在前、"钓杆"在后 → 用角色方位兜底）；仍无 → 跳过该道具（不强加）。

    返回形如：
      【道具锚定】钓杆在杨旭左手垂入画右船头外侧水面，酒壶在杨旭右手倾洒画左甲板。
    无可用道具方位时返回 ""（调用方不追加，绝不堆叠）。
    """
    if not shot:
        return ""
    try:
        if is_empty_scene(shot, characters or []):
            return ""
        scan = role_scan_text or " ".join(filter(None, [
            shot.get("ff_desc", ""), shot.get("lf_desc", ""),
            shot.get("motion_desc", "") or shot.get("prompt", ""),
        ]))
        if not scan:
            return ""

        # 从 script_result.json 读 prop_assets（道具名 + consistent_terms）
        try:
            import json as _json
            from shared.paths import script_file
            script_path = script_file()
            if not script_path.exists():
                return ""
            script = _json.load(open(script_path, encoding="utf-8"))
        except Exception:
            return ""
        prop_assets = script.get("prop_assets", {}) or {}
        if not isinstance(prop_assets, dict) or not prop_assets:
            return ""

        # 幂等：desc 已含"【道具锚定】" → 返回 ""（防多轮组装堆叠）
        if "【道具锚定】" in scan:
            return ""

        # 第一阶段：收集所有匹配道具的出现位置（用于检测并列清单模式）
        prop_hits = []  # (pid, hit_pos, hit_term)
        for pid, entry in prop_assets.items():
            if not isinstance(entry, dict):
                continue
            if _is_space_object_prop(entry):
                continue
            match_sources = [pid] + list(entry.get("consistent_terms", []) or [])
            hit_pos = -1
            hit_term = None
            for term in sorted(match_sources, key=len, reverse=True):
                if not term:
                    continue
                p = scan.find(term)
                if p >= 0:
                    hit_pos = p
                    hit_term = term
                    break
            if hit_pos < 0:
                continue
            # 隐藏武器排除：道具命中词周围含"未出水/未出鞘/潜伏/藏"等 → 不在画面同框
            from shared.content_safety import WEAPON_HIDDEN_WORDS
            hid_win = scan[max(0, hit_pos - 6):hit_pos + len(hit_term) + 8]
            if any(w in hid_win for w in WEAPON_HIDDEN_WORDS):
                continue
            prop_hits.append((pid, hit_pos, hit_term))

        if not prop_hits:
            return ""

        # 第二阶段：先给每件道具算「独立方位」（前后 12 字窗口，**不含**后向远端
        # 捞方位——"船头"这种角色站位不是道具落点）。有方位的道具正常输出；
        # 无方位的道具若与相邻道具构成「、」分隔清单 → 标记"单独摆放"。
        def _near_pos(pid, hit_pos, hit_term):
            """道具前后 12 字窗口找独立方位词；返回 (pos_word, 是否清单分隔).

            后向窗口在**遇到下一个道具名**时截断——避免跨道具捞到下一件道具后的
            方位（"手持酒壶、钓杆、匕首，站在船头"里钓杆后 8 字会跨过匕首捞到
            "船头"，那是角色站位不是钓杆落点）。
            """
            pos_word = ""
            best_dist = 999
            end_pos = hit_pos + len(hit_term)
            # 后向边界：下一个道具名位置（若无则 +8）
            next_prop_pos = end_pos + 8
            for other_pid, other_pos, other_term in sorted_hits:
                if other_pos > end_pos and other_pos < next_prop_pos:
                    next_prop_pos = other_pos
            for w in _PROP_POSITION_WORDS:
                pre_idx = scan.rfind(w, max(0, hit_pos - 12), hit_pos)
                if pre_idx >= 0:
                    d = hit_pos - pre_idx
                    if d < best_dist:
                        best_dist, pos_word = d, w
                # 后向只找到下一个道具名或 +8 内（不跨道具捞方位）
                post_idx = scan.find(w, end_pos, min(next_prop_pos, end_pos + 8))
                if post_idx >= 0:
                    # 排除"站/立/坐/跪/躺"等姿态动词后的位置词——那是角色位置
                    # （"站在船头"= 角色站位，不是道具落点；"匕首，站在船头"里
                    # 的船头不该算匕首方位）
                    verb_gap = scan[end_pos:post_idx]
                    if any(v in verb_gap for v in ("站", "立", "坐", "跪", "躺", "卧", "伏")):
                        continue
                    d = post_idx - end_pos
                    if d < best_dist:
                        best_dist, pos_word = d, w
            # v6.45：威胁武器入体方位兜底——标准方位词未命中时，找身体部位词
            # （胸口/心口/腹部…），让「匕首插在杨旭胸口」产出【道具锚定】。
            # 普通道具不触发（防「酒杯在胸口」类误锚）。
            if not pos_word and _is_threat_weapon_prop(pid):
                for w in _BODY_POSITION_WORDS:
                    pre_idx = scan.rfind(w, max(0, hit_pos - 12), hit_pos)
                    if pre_idx >= 0:
                        d = hit_pos - pre_idx
                        if d < best_dist:
                            best_dist, pos_word = d, w
                    post_idx = scan.find(w, end_pos, min(next_prop_pos, end_pos + 8))
                    if post_idx >= 0:
                        d = post_idx - end_pos
                        if d < best_dist:
                            best_dist, pos_word = d, w
            return pos_word

        parts = []
        sorted_hits = sorted(prop_hits, key=lambda x: x[1])
        for i, (pid, hit_pos, hit_term) in enumerate(sorted_hits):
            pos_word = _near_pos(pid, hit_pos, hit_term)
            if pos_word:
                # 08-16 hook ff「刀悬浮」根因：通用「刺客匕首在水面」+ ff_desc
                # 「正对杨旭方向」让图片模型把整把刀抬到船与水之间（刀身悬空）。
                # 威胁源武器在水位、命中词或上下文暗示水下/微露时，改写为「刀身
                # 没入水下，仅刀尖微露水面」——刀身锚定水下，正对杨旭只影响刀尖
                # 指向，不会把整把刀抬起来。「右手紧握匕首」这类完全可见的威胁
                # 武器（pos_word=右手/左手/船舷…）不触发。
                if (_is_threat_weapon_prop(pid)
                        and pos_word in ("水面", "水中", "水下")
                        and ("刀尖" in hit_term or "刃尖" in hit_term or "枪尖" in hit_term
                             or any(w in scan[max(0, hit_pos - 4):hit_pos + len(hit_term) + 8]
                                     for w in ("没入", "微露", "水下", "一截")))):
                    _tip = hit_term if "尖" in hit_term else "刀尖"
                    # 08-16 hook ff 第四次补刀：模型非确定性，positive「刀身竖直/正
                    # 对杨旭」经常被忽略（hilt 浮出水面 / 刀与水面平行 / 刀尖乱指）。
                    # 改 strong negative anchor：禁止 + 禁止 + 禁止 + 强正向钉死。
                    # 用户原话「这是匕首不是大宝剑」+「必须指向杨旭」→ 显式钉死。
                    parts.append(
                        f"{pid}（短小匕首，不是长剑/不是大宝剑）：整把藏于水下，"
                        f"护手/刀把/刀身完全没入水下不可见，水面外只露约指甲盖大小的"
                        f"极小一截{_tip}且刀身与水面垂直竖立；"
                        f"禁止匕首与水面平行、禁止匕首悬浮空中、禁止匕首横躺、"
                        f"禁止护手/刀把/刀身露出水面；{_tip}必须正对画左杨旭胸口方向")
                else:
                    parts.append(f"{pid}在{pos_word}")
                continue
            # 无独立方位：判断是否在「、」分隔清单中（相邻道具 + 逗号/顿号分隔）
            in_list = False
            gap_prev = scan[sorted_hits[i-1][1] + len(sorted_hits[i-1][2]):hit_pos] if i > 0 else ""
            gap_next = scan[hit_pos + len(hit_term):sorted_hits[i+1][1]] if i < len(sorted_hits) - 1 else ""
            sep = lambda g: any(s in g for s in ("、", "，", "和", "与", ","))
            if (i > 0 and sep(gap_prev)) or (i < len(sorted_hits) - 1 and sep(gap_next)):
                in_list = True
            if in_list:
                # 清单中的无方位道具：明确"单独摆放"而非伪精确方位
                parts.append(f"{pid}单独摆放")
            else:
                # 孤立无方位道具 → 找角色名兜底（"杨旭…钓杆"）
                pre_win = scan[max(0, hit_pos - 12):hit_pos]
                role = next((c.get("identifier", "") for c in (characters or [])
                             if c.get("identifier") and c.get("identifier") in pre_win), "")
                if role:
                    role_pos = _extract_kf_position(scan, role)
                    parts.append(f"{pid}在{role_pos or role + '手中'}")
                else:
                    continue  # 无方位无角色 → 跳过（不强加）

        if not parts:
            return ""
        block = "【道具锚定】" + "，".join(parts)
        # 清单模式下补"分开摆放"指令（有单独摆放的道具时）
        if any("单独摆放" in p for p in parts):
            block += "（多道具分开摆放，禁止重叠）"
        return block
    except Exception:
        return ""


# 服装词提示（dynamic 是服装词开头用"身着"连接；统一从 prompt_semantic.registry 引入）
from step01_script_gen.prompt_semantic import WARDROBE_WORDS as _COSTUME_WORDS


# 镜中临时状态覆盖（08-15 hook 尾帧听香盘发泄漏）：dynamic_features 含这些
# 发型状态词时，static_features（肖像发型）中与之冲突的静态发型短语必须剔除。
# v6.43 重构：统一从 prompt_semantic.registry 引入（不再写死）
from step01_script_gen.prompt_semantic import (
    HAIR_STATE_WORDS as _HAIR_STATE_WORDS,
    HAIR_STATIC_PHRASES as _HAIR_STATIC_PHRASES,
    ANIMAL_FACE_KEYWORDS as _ANIMAL_FACE_KEYWORDS,
    NO_ANIMAL_FEATURES_GUARD as _NO_ANIMAL_FEATURES_GUARD,
    ANIMAL_METAPHOR_FACE_REPLACEMENTS as _ANIMAL_METAPHOR_FACE_REPLACEMENTS,
)

# v6.45：角色惰性/失能状态词——本镜呈躺/卧/倒/昏迷/闭目/中刀等状态时，
# 不可能手持任何物件。chapter 级 dynamic_features 的"手持钓杆"等跨镜手部物件
# 不得注入该角色（08-26 hook kf0 杨旭"手持钓杆"污染根因）。
_INERT_STATE_WORDS = (
    "仰卧", "倒地", "倒下", "躺", "卧", "瘫坐",
    "昏迷", "晕倒", "闭目", "双目紧闭", "已死", "身亡", "毙命",
    "中刀", "中剑", "胸口插", "心口插", "被刺",
)


def _char_inert_in_shot(ident: str, scan_text: str, characters: list | None) -> bool:
    """角色在本镜文本中是否呈惰性/失能状态.

    逐别名检查角色名出现位置的前 8 字/后 12 字窗口内是否有惰性状态词
    （"杨旭仰卧船头"→仰卧在杨旭后 → 惰性）。威胁源别名（刺客/神秘人）也覆盖。
    """
    if not ident or not scan_text:
        return False
    for alias in _char_aliases(ident, characters):
        for m in re.finditer(re.escape(alias), scan_text):
            win = scan_text[max(0, m.start() - 8):m.end() + 12]
            if any(w in win for w in _INERT_STATE_WORDS):
                return True
    return False


def _inject_character_costume(desc: str, characters: list | None,
                              shot: dict | None = None,
                              frame_kind: str | None = None,
                              role_scan_text: str | None = None) -> str:
    """给首帧/尾帧 prompt 注入出镜角色的脸型（static）+ 服装特征（dynamic）.

    v6.57+：保留为老章降级路径（spec 不可用时回退）。新章统一走 `_inject_frame_spec`——
    spec 驱动 on_screen=true 角色注入，无「名字启发式」+ 无 `_threat_visible_in` 窗口匹配
    （治 lf 互斥 + kf ff+lf 拼接的结构性必败）。

    出镜角色 = desc 中出现的角色名。**两帧（ff/lf）一律注入完整 static+dynamic**，
    不做"已有服装词就跳过"的幂等——旧逻辑按角色名周围窗口检测是否已有单个服装词
    （如"一袭春衫"）就跳过，导致 ff 只带"春衫"、lf 才带完整"春衫+步摇"，两帧服装
    锚定不同源 → 图片模型画出的听香衣服不一致（code-audit 08-09 首尾帧衣服差异）。
    现在：dynamic 已整段出现在 desc 中才跳过，否则追加完整锚。

    **static_features（脸型/五官）注入（A1 修复 08-10）**：图片模型在 NO_LAYOUT
    锁（尾帧/关键帧）下对肖像参考图的遵循度被放宽，脸型全靠模型自觉 → 变脸/性别
    漂移。此处把 static_features 一并注入（`{ident}：{static}，身着{dynamic}`），
    用文字锚定脸型，与参考图双重保险。static 缺失时只注入 dynamic（保持兼容）。

    frame_kind="first_frame" 时过滤动作结果/武器词——首帧是动作「前一刻」（破水前），
    不该有溅血/持刀等动作结果。dynamic_features 是**全章状态**（含"脸上溅血""手持
    寒光匕首"），全量注入首帧会让图片模型画出血污/武器（08-09 首帧杨旭胸口血迹、
    刺客血眼）。尾帧/关键帧不过滤（动作已发生，结果应保留）。static_features 无
    动作结果词，首帧不过滤。

    "黑衣刺客"里的"衣"只算刺客的服装，不影响杨旭。空镜（empty_scene）不注入
    （禁人物）。dynamic_features 缺失时跳过该角色。

    role_scan_text: 关键帧专用——kf_desc 常只含动作主体（如"刺客破水而出"），
    杨旭/听香等配角名不在其中，需合并 shot 全文（ff/lf/motion）判定出镜角色，
    否则关键帧画出的角色服装/姿态与首尾帧不一致（code-audit 08-10 hook
    keyframe 杨旭站立/听香撑伞/刺客消失）。传 None 时回退到 desc 判定。
    """
    if not desc or not characters or not shot:
        return desc
    try:
        if is_empty_scene(shot, characters):
            return desc
        additions = []
        # 角色判定文本：默认 desc；关键帧用合并全文（含配角名）
        scan_text = role_scan_text if role_scan_text else desc
        for c in characters:
            ident = c.get("identifier", "") or ""
            # 出镜判定支持威胁源别名（刺客→神秘人/黑衣人——合规替换后文本用
            # "神秘人"，但角色名是"刺客"；仅 ident in scan_text 会漏注入刺客的
            # 脸型/服装，导致刺客形象全靠模型自由发挥。与 _collect_portrait_refs
            # 的别名修复同源（08-10 A1/A2）。
            if not ident or not any(a in scan_text for a in _char_aliases(ident, characters)):
                continue
            # v6.56（P0 威胁源注入可见性门控收紧）：原门控只判 ident in _THREAT_IDS
            # （"刺客/杀手/黑衣人" 3 词），lf_desc 出现「神秘人已没入水中不见踪影」
            # 时别名表里有「神秘人」但 ident「刺客」是 _THREAT_IDS 真子集——本就走
            # 这条路；但跨小说/合规替换后 ident 可能是「蒙面人/黑影」（不在 _THREAT_IDS
            # 但属于威胁源），旧门控直接放行 → 静态/动态锚块注入 lf 最终 prompt
            # → 图片模型被引导画出威胁源（19:01:09 count=3 实证）。
            # 修：threat keyword 扩到 6 词并对**别名表**判可见（不只看 ident），
            # 检测文本用 scan_text（kf 路径 role_scan_text 合并全文，照旧工作）。
            _aliases = _char_aliases(ident, characters)
            _is_threat = any(a in _THREAT_KEYWORDS for a in _aliases) or ident in _THREAT_KEYWORDS
            if _is_threat and _threat_visible_in(scan_text, characters) is not True:
                continue
            static = (c.get("static_features") or "").strip()
            dynamic = (c.get("dynamic_features") or "").strip()
            if not static and not dynamic:
                continue
            # 首帧过滤动作结果/武器词（溅血/持刀/血迹/洇痕等），保留静态服装/发型
            if frame_kind == "first_frame":
                dynamic = _strip_frame_action_result(dynamic)
                if not dynamic and not static:
                    continue
            # v6.45：尾帧/关键帧——角色在本镜呈惰性/失能状态（仰卧/倒地/昏迷/闭目/
            # 中刀…）时不可能手持物件。chapter 级 dynamic_features 含跨镜"手持钓杆"
            # 会被注入本镜（08-26 hook kf0 杨旭"手持钓杆"+起身看镜头根因）。与首帧
            # 同剥动作结果词；威胁源（刺客半身探出/举刀）不处于惰性态，武器保留。
            elif _char_inert_in_shot(ident, scan_text, characters):
                dynamic = _strip_frame_action_result(dynamic)
                if not dynamic and not static:
                    continue
            combined = _build_char_anchor(ident, static, dynamic)
            if combined in desc:
                continue
            additions.append(combined)
        if additions:
            # v6.50：desc 尾部句号 + 本处固定 "。" → 双句号（矛盾门会拦），
            # 先剥再拼
            return f"{desc.rstrip('。')}。{('，'.join(additions))}"
        return desc
    except Exception:
        return desc


def _build_char_anchor(ident: str, static: str, dynamic: str) -> str:
    """组装单个角色的脸型+服装锚字符串（v6.57+ 提取自 _inject_character_costume 复用）.

    行为与原 _inject_character_costume 末尾 2468-2518 行一致：
      - conn: 服装词开头用「身着」，否则用「，」
      - hair_state 前置（披头散发/散落等已配 dynamic 时不重复）
      - X 系脸 → 人类五官词替换（治听香鹿角幻觉）
      - 动物头面特征护栏
    """
    # v6.61（09-05 hook kf1 刺客猫耳根因之一）：LLM 在 static_features 里
    # 自带的负向兽化声明（「无兽化特征」「无任何兽化特征」）是**提到即渲染**
    # 的 priming——Seedream 5.0-lite 对否定列举服从度弱，画了猫耳。入口剥掉
    # 负向句（正面的 NO_ANIMAL_FEATURES_GUARD 已改正向人类形态锚）。
    # 剥离必须在 canonical_anchor 组装**之前**（组装后改已无意义）。
    import re as _re_beast
    static = _re_beast.sub(r"无任何兽化特征[，。]?|无兽化特征[，。]?", "", static)
    dynamic = _re_beast.sub(r"无任何兽化特征[，。]?|无兽化特征[，。]?", "", dynamic)
    conn = "，身着" if any(w in dynamic for w in _COSTUME_WORDS) else "，"
    _hair_state = next((w for w in _HAIR_STATE_WORDS if w in dynamic), "")
    try:
        from step01_script_gen.prompt_semantic import Character as _PSC
        _char = _PSC(
            name=ident, static_features=static, dynamic_features=dynamic,
            hair_state=_hair_state,
        )
        combined = _char.canonical_anchor()
    except ImportError:
        # 兜底：保持原硬编码行为（无 prompt_semantic 包时回退）
        if _hair_state:
            for _phrase in _HAIR_STATIC_PHRASES:
                static = static.replace(_phrase, "")
            _parts = [p for p in static.split("，") if p.strip()]
            static = "，".join(_parts)
            if _hair_state in dynamic:
                if static:
                    static = f"{static}，"
            else:
                if static:
                    static = f"{static}，此刻{_hair_state}"
                else:
                    static = f"此刻{_hair_state}"
        combined = (f"{ident}：{static}{conn}{dynamic}" if static
                    else f"{ident}{conn}{dynamic}")
    # v6.50（08-28）：先做「X系脸」比喻词 → 人类五官词替换——比喻词
    # （鹿系脸/猫系脸）本身诱导兽化，靠下面的否定护栏对抗是下策；
    # 替换后 static/dynamic 不再含动物字根，护栏自然不触发，prompt 更短。
    for _meta, _human in _ANIMAL_METAPHOR_FACE_REPLACEMENTS:
        if _meta in static:
            static = static.replace(_meta, _human)
        if _meta in dynamic:
            dynamic = dynamic.replace(_meta, _human)
    # 08-16 听香鹿角幻觉：角色脸型含动物关键词（虎目/狼腰等残余动物词）
    # + 头饰（步摇/簪钗/冠）→ 模型把动物延伸成兽耳/角/犄角。追加护栏
    # 明确禁止动物头面特征。检查 static+dynamic 任一含关键词即触发。
    if any(k in (static + dynamic) for k in _ANIMAL_FACE_KEYWORDS):
        combined = f"{combined}{_NO_ANIMAL_FEATURES_GUARD}"
    return combined


def _closeup_directive(shot: dict, frame_kind: str, kf_index: int | None = None) -> str:
    """spec 含「X特写」时生成特写构图指令（注入帧 prompt）.

    09-13 shot_4 lf 根因：QC 锚点要「手部特写」但帧 prompt 无特写指令——
    spec 构图要求只进 QC 不进 prompt，模型只能画全景，重抽必然复败
    （场景参考图门控见 _frame_spec_closeup，只是半价；prompt 指令才是根修）。
    spec 构图要求必须与 QC 同源地进 prompt。
    """
    fl = shot.get("frame_layouts") or {}
    if frame_kind == "keyframe":
        kfs = fl.get("kfs") or []
        entries = (kfs[kf_index]
                   if (kf_index is not None and 0 <= kf_index < len(kfs)) else [])
    elif frame_kind in ("first_frame", "last_frame"):
        entries = fl.get("ff" if frame_kind == "first_frame" else "lf") or []
    else:
        entries = []
    import re as _re
    for e in entries:
        if not isinstance(e, dict):
            continue
        for field in ("position", "pose"):
            v = str(e.get(field) or "")
            m = _re.search(r"（([^）]*特写)）", v)
            if m:
                obj = m.group(1)                                # 手部特写 / 面部特写…
                core = obj[:-2] if obj.endswith("特写") else obj  # 手部 / 面部
                base = _re.sub(r"（[^）]*）", "", v).strip() or "画中央"
                return (f"【特写构图】{obj}镜头：画面聚焦{core}，主体位于{base}，"
                        f"背景大幅虚化，禁止全景式环境展示")
    return ""


def _inject_frame_spec(
    desc: str,
    shot: dict,
    characters: list | None,
    frame_kind: str,
    kf_index: int | None = None,
) -> str:
    """v6.57+ per-frame spec 驱动的角色锚定注入（替代 _inject_character_costume）.

    出镜角色 = frame_layouts.{ff/lf/kfs[kf_index]} on_screen=true 的角色。
    不再用「desc 中名字出现」启发式 + `_threat_visible_in` 窗口匹配——
    spec on_screen=false 的角色直接跳过（治 lf 互斥：威胁源已没入水中时不被注入；
    治 kf 角色消失：刺客离场 kf 不会再画刺客）。

    优势：
      - on_screen=false 角色不注入任何锚 → lf prompt 不引导模型画已退场角色
      - kf 各取自己的 spec（kfs[i]）→ kf 独立角色表，不再用 ff+lf 拼接
      - 威胁源可见性 = spec on_screen 字段判定，治 8-30 实证 lf count=3 烧 200 张根因

    老章降级：shot 无 frame_layouts / spec 为空 → 回退 _inject_character_costume
    旧逻辑（保留兼容）。
    """
    if not desc or not characters or not shot:
        return desc
    try:
        if is_empty_scene(shot, characters):
            return desc
        from shared.frame_spec import get_frame_spec
        spec = get_frame_spec(shot, frame_kind, kf_index)
        # 老章降级（spec 为空——无 frame_layouts / 全 off_screen）
        if not spec:
            return _inject_character_costume(
                desc, characters, shot,
                frame_kind=frame_kind,
            )
        # spec 驱动：on_screen=true 才注入
        on_screen_items = [s for s in spec
                           if isinstance(s, dict) and s.get("on_screen") is True]
        if not on_screen_items:
            return desc
        char_by_ident = {c.get("identifier", ""): c for c in characters
                         if isinstance(c, dict)}
        additions = []
        for item in on_screen_items:
            ident = str(item.get("name", "")).strip()
            if not ident:
                continue
            c = char_by_ident.get(ident)
            if not c:
                continue
            static = (c.get("static_features") or "").strip()
            dynamic = (c.get("dynamic_features") or "").strip()
            if not static and not dynamic:
                continue
            # 首帧过滤动作结果/武器词；非首帧（lf / kf）做惰性态剥离
            if frame_kind == "first_frame":
                dynamic = _strip_frame_action_result(dynamic)
                if not dynamic and not static:
                    continue
            elif _char_inert_in_shot(ident, desc, characters):
                dynamic = _strip_frame_action_result(dynamic)
                if not dynamic and not static:
                    continue
            combined = _build_char_anchor(ident, static, dynamic)
            if combined in desc:
                continue
            additions.append(combined)
        if additions:
            return f"{desc.rstrip('。')}。{('，'.join(additions))}"
        return desc
    except Exception:
        return desc


def _inject_frame_spec_and_closeup(desc: str, shot: dict, characters: list | None,
                                   frame_kind: str, kf_index: int | None = None) -> str:
    """_inject_frame_spec + 特写构图指令（统一注入点，生成链/QC 重建链同走）."""
    text = _inject_frame_spec(desc, shot, characters,
                              frame_kind=frame_kind, kf_index=kf_index)
    if "特写" not in text:
        directive = _closeup_directive(shot, frame_kind, kf_index)
        if directive:
            text = f"{text.rstrip('。')}。{directive}"
    return text


# 首帧禁注入的动作结果/武器词：收敛到 shared/content_safety（审计 S2），
# 与 step02_video_gen/prompt_builder 视频起点过滤共用单一词表，不再各持一份。
from shared.content_safety import ACTION_RESULT_WORDS, strip_action_result as _safety_strip_action_result


def _strip_frame_action_result(dynamic: str) -> str:
    """从 dynamic_features 中剥离首帧不应出现的动作结果/武器词（复用 shared 单一实现）."""
    return _safety_strip_action_result(dynamic)


# 尾帧目光确定性修复：错别字 + 看向动作结果对象
_LF_GAZE_TYPO = (
    ("倒倒", "紧紧"), ("倒到", "紧紧"),
    ("望望", "望着"), ("看看", "看着"),
)
# 目光看向"不在场/消失对象"的模式（触发改为看动作结果对象）
_LF_GAZE_LOST_OBJECT = (
    "消失之处", "消失处", "水面刺客", "刺客消失", "画右水面", "画左水面",
    "水中刺客", "刺客没入之处", "没入之处",
)
# 动作结果对象出现（场景含被刺倒的同伴）→ 才触发目光改写
_LF_VICTIM_HINTS = ("倒下", "仰面倒", "仰面躺", "中刀", "被刺", "心口", "洇开", "倒地", "受伤")


def fix_lf_desc_gaze(lf_desc: str, shot: dict | None = None,
                     characters: list | None = None) -> str:
    """修复尾帧 lf_desc 的角色目光（错别字 + 看向动作结果对象）.

    用户反馈：尾帧听香「目光倒倒盯着画右水面刺客消失之处」——盯刺客消失的
    水面，且"倒倒"是错别字。期望听香看向被刺倒的杨旭（被刺的同伴）。

    修复逻辑（确定性，保守）：
    1. 错别字：倒倒/倒到→紧紧 等叠字错写
    2. 目光语义：若 lf_desc 含「目光/盯着/看向 + 消失/水面/刺客」且场景含
       受伤结果对象（杨旭倒下/心口洇开），把该目光改写为「目光看向倒下的
       杨旭」——看被刺的同伴，而非已消失的刺客。

    保守触发：只修"明显在看不在场/消失对象"的目光；不含受伤结果对象时不动。
    """
    if not lf_desc:
        return lf_desc
    # 1. 错别字修复（叠字错写）
    text = lf_desc
    for old, new in _LF_GAZE_TYPO:
        text = text.replace(old, new)

    # 2. 目光语义修复：看消失对象 → 看向动作结果对象
    try:
        victim = None
        for c in (characters or []):
            ident = c.get("identifier", "") or ""
            if ident and ident in text and any(h in text for h in _LF_VICTIM_HINTS):
                victim = ident
                break
        if not victim:
            return text
        # 检测"目光 + 看/盯 + 消失对象"
        gaze_patterns = (
            ("目光倒倒盯着", "目光紧紧看向"),
            ("目光紧紧盯着", "目光紧紧看向"),
            ("目光盯着", "目光看向"),
            ("目光望向", "目光看向"),
            ("双眼盯着", "双眼看向"),
            ("盯着", "看向"),
        )
        for gaze_old, gaze_new in gaze_patterns:
            if gaze_old in text:
                idx = text.find(gaze_old)
                # 从 gaze 位置后到句尾/标点前 = 目光宾语段（"画右水面刺客消失之处"）
                seg = text[idx + len(gaze_old):]
                # 宾语段终止于中文标点
                for _i, _ch in enumerate(seg):
                    if _ch in "，。；！？":
                        seg = seg[:_i]
                        break
                # 确认宾语段含消失对象（水面/消失/刺客）
                if any(l in seg for l in _LF_GAZE_LOST_OBJECT):
                    text = text.replace(gaze_old, gaze_new)
                    # 整个宾语段替换为"倒下的{victim}"
                    text = text[:idx + len(gaze_new)] + f"倒下的{victim}" + text[idx + len(gaze_old) + len(seg):]
                    break
    except Exception:
        pass
    return text


def _build_final_frame_prompt(shot: dict, desc: str, frame_kind: str,
                              characters: list | None = None) -> str:
    """构建实际生成图片的最终 prompt（供 QC 语义核对用）.

    与 _gen_first_frame_async / _gen_last_frame_async 的组装链一致：
    normalize(已在 shot["ff_desc"] 回写) → preprocess_prompt_for_image(青丝→黑发等)
    → _inject_object_size_anchor → ensure_prefix + sanitize_prompt → add_image_strict_constraints。

    缓存命中路径（QC 在 ff_desc_full 计算前调用）用此函数补算最终 prompt，
    保证 QC 拿到的"画面描述"与图片模型实际收到的一致（code-audit 2026-08-08）。
    """
    try:
        from shared.style_prefix import (
            preprocess_prompt_for_image, sanitize_prompt, ensure_prefix,
            add_image_strict_constraints,
        )
        text = preprocess_prompt_for_image(desc)
        # v1.1：注入出镜角色服装特征（与 _gen_first_frame_async 生成链一致）
        # 首帧过滤动作结果/武器词，保证 QC 与图片模型看到的 prompt 一致
        text = _inject_frame_spec_and_closeup(text, shot, characters, frame_kind=frame_kind)
        # v1.1：尾帧目光修复（与 _gen_last_frame_async 一致，QC 看到修复后 prompt）
        if frame_kind == "last_frame":
            text = fix_lf_desc_gaze(text, shot, characters)
        text = _inject_object_size_anchor(shot, text)
        text = _inject_tone_anchor(shot, text)
        # 2026-09-04 W16/W17 兜底：钩子水面/夜泊类场景追加泳池禁词。
        # Seedream 全档 supports_negative_prompt=False（shared/model_caps.py:53），
        # 必须走 prompt 内文禁词。W17 step01 已在 [ENV-ANCHOR] 前缀注入过同类禁词，
        # 哨兵存在则跳过避免重复叠加。
        if shot.get("is_hook") is True and "[ENV-ANCHOR]" not in text:
            import re as _re_hook
            if _re_hook.search(
                r"水|夜泊|泊|月色|月|夜|河|湖|湾|渡|舟|舴艋|船", text
            ):
                text += (
                    " Strictly no swimming pool, no indoor pool, no ceramic tile, "
                    "no white tile surface, no bathtub, no modern architecture."
                )
        text = sanitize_prompt(ensure_prefix(text, STYLE_PREFIX), verbose=False)
        text = add_image_strict_constraints(text, "首帧" if frame_kind == "first_frame" else "尾帧", shot=shot, characters=characters)
        # v6.50：多模板拼接点的双句号归一化（防御性兜底，矛盾门此前会拦）
        while "。。" in text:
            text = text.replace("。。", "。")
        return text
    except Exception:
        return desc


# ──────────────────── Doodle 区域编辑 (Seedream 负向词替代品) ────────────────────
# 2026-08-16 hook ff「刀悬浮/横躺」根因：Seedream 全档不支持 negative_prompt / seed
# （见 shared/model_caps.py SEEDREAM 全档 supports_negative_prompt=False / supports_seed=False），
# 文本 prompt 无法可靠约束「指甲盖大小/竖直/正对某人」等**精确空间约束**——模型非确定性
# 反复画出完整匕首。官方 Seedream 4.0-4.5 提供 image-to-image + 视觉标注（doodle/bbox）
# 作为空间控制手段：模型会优先改写红框区域，红框外严格保持原图。
#
# 把这条做成自动 fallback：常规出图 → 检测 prompt 含空间精确词 → 调 doodle 二修 →
# 用 refined 覆盖 ff；doodle 失败保留原图（graceful degradation）。Per-shot 优先：
# shot["doodle_refine"] = {"bbox_ratio": (x1,y1,x2,y2), "refined_prompt": "..."}
# 命中即跳过 auto-detect，用 shot 提供的 bbox + 专用 prompt 覆盖通用模板。
_SPATIAL_FALLBACK_DEFAULTS = {
    # hook 案例：右下水面对应 1024×1792 上 (500,850)-(1020,1550)
    # ratio = (0.488, 0.474, 0.996, 0.865)
    "微露水面": {
        "bbox_ratio": (0.49, 0.47, 1.0, 0.86),
        "refined_prompt": (
            "在红框区域: 水面上只允许出现一截指甲盖大小的极小物体顶端（高度≤1厘米），"
            "物体主体/护手/手柄/刀身等必须完全没入水下不可见，"
            "禁止物体与水面平行、禁止物体悬浮空中、禁止物体横躺。"
            "红框外: 完全保持与原图一致，人物/船/夜色/荷叶等一切不变。"
        ),
    },
    "悬浮空中": {
        "bbox_ratio": (0.30, 0.05, 0.95, 0.60),
        "refined_prompt": (
            "在红框区域: 物体必须被某只手握住或落在物体表面，禁止悬浮空中。"
            "红框外: 完全保持与原图一致。"
        ),
    },
}


def _detect_spatial_fallback(ff_desc_full: str, shot: dict) -> dict | None:
    """检测 prompt 是否需要 doodle 区域编辑 fallback.

    Returns dict {phrase, bbox_ratio, refined_prompt} 或 None.
    """
    override = shot.get("doodle_refine")
    if override:
        return {
            "phrase": "<override>",
            "bbox_ratio": tuple(override["bbox_ratio"]),
            "refined_prompt": override["refined_prompt"],
        }
    for phrase, cfg in _SPATIAL_FALLBACK_DEFAULTS.items():
        if phrase in ff_desc_full:
            return {"phrase": phrase, **cfg}
    return None


async def _doodle_refine_first_frame(
    image_adapter,
    base_image_path: str,
    bbox_ratio: tuple[float, float, float, float],
    refined_prompt: str,
    output_path: str,
    shot_index: int,
) -> str | None:
    """在 base_image 上画红框区域，调用 i2i 用 refined_prompt 改写该区域.

    1) 读 base_image → 画 bbox 红色矩形 → 保存为 annotated
    2) i2i 调用（单参考图 = annotated）
    3) 写入 output_path

    失败（异常/输出缺失/未生成）→ 返回 None，调用方走 graceful degradation 保留原图。
    """
    try:
        from PIL import Image, ImageDraw
        base = Path(base_image_path)
        annotated = base.parent / f"{base.stem}_doodle.png"

        img = Image.open(base_image_path).convert("RGB")
        w, h = img.size
        x1, y1, x2, y2 = (int(c * d) for c, d in zip(bbox_ratio, (w, h, w, h)))
        # 画 bbox：宽线 8px 红色（Seedream 视觉标注会优先改写该区域）
        draw = ImageDraw.Draw(img)
        draw.rectangle((x1, y1, x2, y2), outline="red", width=8)
        img.save(annotated)

        # i2i 调用（单参考图 = 我们的 annotated doodle；force_multi_ref=False 走单图分支）
        # v6.61（09-05 hook 根因 5）：显式传 save_path=output_path——provider
        # _download 在 save_path 为空时按 frame_desc 落默认名（"doodle_refine"
        # 不匹配首帧/尾帧/关键帧 → fallback "kf" → 存成 shot_N_kf.png 冒充
        # 关键帧槽位，15:08 hook 实证）。显式落盘到调用方指定的临时文件，
        # provider 中间产物不再污染关键帧命名空间。
        img_out = await image_adapter.generate_single_image(
            prompt=refined_prompt,
            reference_image_paths=[str(annotated)],
            shot_index=shot_index,
            frame_desc="doodle_refine",
            force_multi_ref=False,
            save_path=str(output_path),
        )
        if img_out is None:
            return None
        if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
            img_out.data.save(output_path)
        elif hasattr(img_out, "save"):
            img_out.save(output_path)
        return output_path if Path(output_path).exists() else None
    except Exception as e:
        _bridge_log(f"  [DOODLE] 区域编辑失败: {e}")
        return None


async def _gen_first_frame_async(
    image_adapter, shots_dir: Path, shot: dict, force: bool,
    script_mtime: float | None = None,
    reference_paths: list | None = None,
    max_retries: int = 1,
    empty_scene: bool = False,
    characters: list | None = None,
):
    """为镜头生成 first_frame.png，prompt 用 ff_desc.

    生成后自动校验文件大小；不达标时自动重试（最多 max_retries 次）.
    empty_scene=True 时在 prompt 中显式禁止人物（与视频 prompt 的空镜约束
    保持一致，防止首帧凭空长出人物后被 image-to-video 固化成片头画面）.
    characters: 用于动作镜头判定——动作镜头改用 NO_LAYOUT 锁图（解除"场景
    布局不变"），否则动作描述被锁图压制、人物画不全（code-audit 2026-08-07）.
    """
    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    ff_path = shot_dir / FIRST_FRAME_FILENAME
    ff_hash_path = shot_dir / "frame_hash.txt"

    # 日志输出到 step02.log，不污染控制台
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    _log_dir = chapter_dir()
    logger = get_logger(_log_dir / "step02.log", console_level="INFO")

    ff_desc = shot.get("ff_desc", "").strip()
    if not ff_desc:
        # ff_Desc 缺失时，从 location/scene + narration 构建静态首帧
        location = shot.get("location", "") or shot.get("scene", "")
        scene = shot.get("scene", "") or location
        narration = shot.get("narration_segment", "")
        main_subj = shot.get("main_subject", "")
        ff_desc = f"{location}，{main_subj}，静态画面。"
        if narration:
            ff_desc += f" {narration[:50]}。" if len(narration) > 50 else f" {narration}。"
    else:
        # ff_Desc 存在时，检查是否是纯相机运动描述（无静态画面）
        motion_keywords = ["上移", "下移", "左移", "右移", "推进", "拉远", "跃起", "冲出",
                          "穿越", "飞", "跑", "快速", "缓慢", "跟随", "追"]
        is_pure_motion = any(kw in ff_desc for kw in motion_keywords)
        if is_pure_motion or len(ff_desc) < 20:
            # ff_Desc 几乎是纯相机运动，补充静态画面
            location = shot.get("location", "") or shot.get("scene", "")
            main_subj = shot.get("main_subject", "")
            ff_desc = f"{location}，{main_subj}，静态画面。{ff_desc}"
    if empty_scene:
        ff_desc = f"{ff_desc}\n空镜头，画面中严禁出现任何人物、身影、人影、轮廓或人形。只拍摄自然景物特写。"

    # v6.18：ff_desc 归一化 + 回写（多动作/进行态堆叠 → 单一静态瞬间）。
    # 必须在哈希计算之前——_get_frame_content_hash 哈希的是 shot["ff_desc"]，
    # 不回写则归一化后缓存永不失效。回写保证帧 prompt、step02a 落盘、
    # step02b 的【视频起点】三处一致。
    if ff_desc and not empty_scene:
        from shared.style_prefix import normalize_ff_desc
        norm = normalize_ff_desc(ff_desc, shot, characters)
        if norm != ff_desc:
            shot["ff_desc"] = ff_desc = norm
            _bridge_log(f"  [bridge] 镜头{idx}: ff_desc 归一化（多动作→静态瞬间）")

    # 计算当前哈希值（核心意图 + 空镜约束 + 参考图 + 锁类型，任一变化都重新生成）
    # 动作镜头用 NO_LAYOUT 锁图（prompt 语义变化），锁类型纳入哈希键而非魔法后缀。
    current_hash = _get_frame_content_hash(
        shot, empty_scene, reference_paths,
        lock_kind="no_layout" if _is_action_shot(shot, characters) else "layout",
    )

    # v1.1：挂实际生成最终 prompt 到 shot（QC 语义核对用）。缓存命中/生成两条
    # 路径都在此补算——缓存命中时 ff_desc_full 未计算，但 shot["ff_desc"] 已被
    # normalize 回写，用 _build_final_frame_prompt 补算得到与图片模型一致的文本。
    try:
        shot["ff_desc_final"] = _build_final_frame_prompt(shot, ff_desc, "first_frame", characters)
    except Exception:
        pass
    # v6.50（08-28）矛盾自检器：提交前拦「匕首在手+没有武器」式对称矛盾——
    # 放在 try 外，fail-fast 异常向上传播让该帧当场失败（不再烧额度画必错的图）
    _assert_ff = shot.get("ff_desc_final")
    if _assert_ff:
        from shared.prompt_contradiction import assert_no_contradictions
        assert_no_contradictions(_assert_ff, f"shot{idx} 首帧")

    # v6.53 A.4：QC_MAX_ATTEMPTS=重抽总尝试次数（含首次），默认沿用调用方
    try:
        max_retries = max(0, int(os.getenv("QC_MAX_ATTEMPTS", str(max_retries + 1))) - 1)
    except (TypeError, ValueError):
        pass

    # === 2026-08-29 重试膨胀修复：prompt 组装移出重试循环 ===
    # 旧实现：组装段（costume/size/tone/锚定/prop 追加 + sanitize + strict_constraints）
    # 在 for attempt 内 → 每次重试都跑一遍，部分 helper 若非严格幂等会叠加
    # （实测多镜头 prompt 在 attempt=2 时比 attempt=1 长 25-40%）。现在
    # **只算一次** ff_desc_full 与 gen_kwargs，重试画的是同一份 prompt。
    # QC 复检、矛盾门、哈希、doodle 配置都基于这份不变 prompt 派生。
    refs = reference_paths if reference_paths else []
    is_action = _is_action_shot(shot, characters)  # 循环不变量
    # 动作镜头：解除"场景布局不变"锁（普通镜头不传 → 适配器默认完整锁）。
    # 注意不能传 None——适配器 kwargs.get("lock_prompt", DEFAULT) 会把 "None，" 拼进 prompt。
    action_lock = IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT if is_action else None
    # ---- prompt 组装（只算一次，重试复用） ----
    _ff_pre = preprocess_prompt_for_image(ff_desc)
    _ff_pre = _inject_frame_spec_and_closeup(_ff_pre, shot, characters, frame_kind="first_frame")
    # 场景物体尺寸锚定注入（船/车等物体比例防漂移，code-audit 08-06 钩子船太小）
    _ff_pre = _inject_object_size_anchor(shot, _ff_pre)
    # 场景色调锚定注入（color_tone：夜景冷蓝/暖金烛光等，防模型自造色调漂色）
    _ff_pre = _inject_tone_anchor(shot, _ff_pre)
    # 角色姿态/位置/朝向锚定块（代码级兜底——首帧若 LLM 漏写刺客朝向/角色姿态，
    # 从 shot 全文提取补上；water_state=False 首帧是动作前一刻，不补船/水面状态）
    # v6.61：frame_kind="first_frame" → spec 驱动出镜表（ff 威胁源潜伏水下
    # on_screen 判定，不再走合并 scan 的 _threat_visible_in）
    _ff_anchor = _extract_char_layout(shot, characters,
                                      role_scan_text=shot.get("ff_desc", ""),
                                      water_state=False,
                                      frame_kind="first_frame")
    if _ff_anchor:
        _ff_pre = f"{_ff_pre}。{_ff_anchor}"
    # A3 修复：道具空间方位锚定块（提取同框手持道具的落点，防自由摆放重叠）
    _ff_prop = _extract_prop_layout(shot, characters,
                                    role_scan_text=shot.get("ff_desc", ""))
    if _ff_prop:
        _ff_pre = f"{_ff_pre}。{_ff_prop}"
    ff_desc_full = sanitize_prompt(ensure_prefix(_ff_pre, STYLE_PREFIX), verbose=False)
    ff_desc_full = add_image_strict_constraints(ff_desc_full, "首帧", shot=shot, characters=characters)
    # ---- prompt 组装结束 ----
    gen_kwargs = dict(
        prompt=ff_desc_full,
        reference_image_paths=refs,
        shot_index=idx,
        frame_desc="首帧",
        force_multi_ref=True,
    )
    if action_lock is not None:
        gen_kwargs["lock_prompt"] = action_lock

    for attempt in range(max_retries + 1):
        if ff_path.exists() and not force and attempt == 0:
            # 检查哈希文件是否存在且匹配
            if ff_hash_path.exists():
                saved_hash = ff_hash_path.read_text(encoding="utf-8").strip()
                if saved_hash == current_hash and _validate_frame(str(ff_path)):
                    # B7：缓存命中但 QC critical → 删帧+删哈希，下次重生成
                    # v6.x.x P1 修：continue 跳过 attempt=0 的 fall-through 生成，
                    # 让 attempt 走到 1 触发 variation[1]——缓存的图本身就是原
                    # prompt 画的"已失败一次"，再用 variation[0] 画没意义。
                    if await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene):
                        _del_frame_and_hash(ff_path, ff_hash_path, "首帧", idx, logger)
                        continue
                    else:
                        return str(ff_path)
                else:
                    logger.verbose(f"  [FRAME] 首帧 shot_{idx} 哈希不匹配或缓存无效，重新生成")
            elif script_mtime is not None and ff_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(ff_path)):
                    if await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene):
                        _del_frame_and_hash(ff_path, ff_hash_path, "首帧", idx, logger)
                        continue
                    else:
                        return str(ff_path)
            elif script_mtime is None and _validate_frame(str(ff_path)):
                if await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene):
                    _del_frame_and_hash(ff_path, ff_hash_path, "首帧", idx, logger)
                    continue
                else:
                    return str(ff_path)

        logger.verbose(f"  [FRAME] 生成首帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        # 2026-08-29：gen_kwargs 已在循环外组装，prompt 复用同一份；不再重新追加锚块。
        # v6.x：per-attempt prompt 微扰动（仅局部变量，不回写 shot / 不影响哈希 / 不影响 QC 断言源）。
        attempt_variation = _ATTEMPT_VARIATIONS[attempt % len(_ATTEMPT_VARIATIONS)]
        attempt_kwargs = dict(gen_kwargs, prompt=ff_desc_full + attempt_variation)
        try:
            img_out = await image_adapter.generate_single_image(**attempt_kwargs)
            if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                img_out.data.save(str(ff_path))
            elif hasattr(img_out, "save"):
                img_out.save(str(ff_path))
        except Exception as e:
            _bridge_log(f"  [FRAME] 首帧 shot_{idx} 生成异常: {e}")
            if attempt < max_retries:
                continue
            return None

        if _validate_frame(str(ff_path)):
            # 保存哈希值，供下次缓存检测使用
            ff_hash_path.write_text(current_hash, encoding="utf-8")
            # B7 修复：首帧 QC critical → unlink 坏帧 + 删哈希，重试循环重生成
            # v6.54.1：先修后抽——局部可修的 critical 走 bbox i2i 编辑（保已通过
            # 的构图/人脸），编辑失败或全局失败才删帧重抽
            qc_critical = await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene,
                                                 variation=attempt_variation)
            if qc_critical:
                if await _try_qc_repair(image_adapter, shot, str(ff_path), "first_frame",
                                        characters, empty_scene, idx):
                    pass  # 修复成功：哈希未删（下次缓存命中即修好的图），继续 spatial fallback
                elif attempt < max_retries:
                    # 还有下一次 attempt → 删帧重抽（B7 修复）
                    logger.verbose(f"  [FRAME] 首帧 shot_{idx} QC critical，删帧重生成")
                    try:
                        Path(ff_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    try:
                        Path(ff_hash_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                else:
                    # 末次尝试仍 critical → 保留最不差一版（attempts_exhausted 留痕），
                    # sidecar 已带 critical 记录，留痕供人工复核；无图比坏图更伤链路
                    _bridge_log(f"  [QC] shot_{idx} {attempt+1} 次尝试均 critical，"
                                "保留末版留痕（attempts_exhausted）")
                    return str(ff_path)
            # v6.30：doodle 区域编辑 fallback — 精确空间约束 prompt 调不到位时
            # 走 i2i + 红框二修；QC critical 之后才做（不在已知坏帧上浪费 i2i）。
            # 失败保留原图（graceful degradation）。哈希键不变（基于 prompt，
            # 不是 image），同 prompt 重跑会再调一次 doodle（2x 成本但可控）。
            doodle_cfg = _detect_spatial_fallback(ff_desc_full, shot)
            if doodle_cfg:
                doodle_tmp = ff_path.parent / f"{ff_path.stem}_doodle_tmp.png"
                refined = await _doodle_refine_first_frame(
                    image_adapter, str(ff_path),
                    doodle_cfg["bbox_ratio"], doodle_cfg["refined_prompt"],
                    str(doodle_tmp), idx,
                )
                if refined and _validate_frame(refined):
                    Path(refined).replace(ff_path)
                    _bridge_log(f"  [DOODLE] shot{idx} 区域编辑成功覆盖首帧 (phrase={doodle_cfg['phrase']})")
                else:
                    _bridge_log(f"  [DOODLE] shot{idx} 区域编辑失败，保留原图")
            return str(ff_path)
        if attempt < max_retries:
            logger.verbose(f"  [FRAME] 首帧 shot_{idx} 第{attempt+1}次不达标，重试...")

    return str(ff_path) if ff_path.exists() else None


async def _gen_last_frame_async(
    image_adapter, shots_dir: Path, shot: dict, force: bool,
    script_mtime: float | None = None,
    reference_paths: list | None = None,
    max_retries: int = 1,
    empty_scene: bool = False,
    characters: list | None = None,
):
    """为 medium/large 变幅镜头生成 last_frame.png，prompt 用 lf_desc.

    生成后自动校验文件大小；不达标时自动重试.
    empty_scene=True 时与首帧一致，显式禁止人物出现.
    """
    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    lf_path = shot_dir / LAST_FRAME_FILENAME
    # 尾帧独立哈希文件：首帧/尾帧的参考图不同 → 哈希值不同，
    # 共用一个文件会互相覆盖，导致每次运行都误判失效、双双重做
    lf_hash_path = shot_dir / "last_frame_hash.txt"

    # 日志输出到 step02.log，不污染控制台
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    _log_dir = chapter_dir()
    logger = get_logger(_log_dir / "step02.log", console_level="INFO")

    lf_desc = shot.get("lf_desc", "").strip()
    if not lf_desc:
        # lf_desc 缺失时回退逻辑：
        # 1. 先尝试 ff_desc（如果是静态画面描述）
        # 2. 如果 ff_desc 也是动作描述（如"快速上移"、"跃起"），则用 scene 或 narration 生成静态结尾
        ff_desc = shot.get("ff_desc", "").strip()
        motion_keywords = ["上移", "下移", "左移", "右移", "推进", "拉远", "跃起", "冲出",
                          "穿越", "飞", "跑", "跳", "蹦", "滑", "冲", "快速", "缓慢"]
        is_motion_desc = any(kw in ff_desc for kw in motion_keywords)

        if ff_desc and not is_motion_desc:
            # ff_Desc 是静态画面，可以用作尾帧
            lf_desc = f"{ff_desc}（镜头收尾画面，场景延续，构图自然）"
        else:
            # ff_Desc 是动作描述，生成安全的静态结尾画面
            # 从 scene + narration 提取关键元素构建静态尾帧
            scene = shot.get("scene", "")
            narration = shot.get("narration_segment", "")
            # 提取主体静态画面
            main_subj = shot.get("main_subject", "")
            lf_desc = f"{scene}，{main_subj}，静态画面，夜晚冷蓝月光，水面平静。"
            if narration:
                # 截取narration描述的最后一个动作结果作为尾帧
                lf_desc += f" {narration}。"
    if empty_scene:
        lf_desc = f"{lf_desc}\n空镜头，画面中严禁出现任何人物、身影、人影、轮廓或人形。只拍摄自然景物特写。"

    # v6.29：尾帧终态归一化兜底（与 step01 post_fix 对称）——lf_desc 中间态在
    # 帧生成前也清洗一次，保证 step02a 直接跑时尾帧不画中间态；回写保证哈希正确失效。
    if lf_desc:
        from shared.style_prefix import normalize_lf_desc
        _norm_lf = normalize_lf_desc(lf_desc, shot, characters)
        if _norm_lf != lf_desc:
            shot["lf_desc"] = lf_desc = _norm_lf

    # 计算当前哈希值（核心意图 + 空镜约束 + 参考图 + 锁类型）
    # 尾帧用 NO_LAYOUT 锁图（构图随动作演变），锁类型纳入哈希键（审计 S4）。
    current_hash = _get_frame_content_hash(
        shot, empty_scene, reference_paths, lock_kind="no_layout")

    # v1.1：挂实际生成最终 prompt（QC 语义核对用），与首帧同理
    try:
        shot["lf_desc_final"] = _build_final_frame_prompt(shot, lf_desc, "last_frame", characters)
    except Exception:
        pass
    # v6.50（08-28）矛盾自检器：与首帧同理。
    # 2026-08-29：lf 软跳过补丁——包 try-except，矛盾时**跳过该帧并日志留痕**
    # （返回 None），不再让 lf 矛盾 fail-fast 整条 shot。ff 仍 fail-fast 保留
    # 主防线（ff 失败 = 整 shot 视觉基础崩，必须重抽；lf 失败可降级为缺尾帧）。
    # 老版 raise PromptContradictionError 会让单帧矛盾 kill 整条链路。

    # v6.53 A.4：QC_MAX_ATTEMPTS=重抽总尝试次数（含首次）
    try:
        max_retries = max(0, int(os.getenv("QC_MAX_ATTEMPTS", str(max_retries + 1))) - 1)
    except (TypeError, ValueError):
        pass

    # === 2026-08-29 重试膨胀修复：lf_desc_full 组装移出重试循环（与 ff 同策略）===
    # 2026-08-29 软跳过补丁：lf 矛盾门包 try-except，矛盾时**跳过该帧并日志留痕**
    # （返回 None），不再让 lf 矛盾 fail-fast 整条 shot——ff 仍 fail-fast 保留主防线
    # （ff 失败 = 整 shot 视觉基础崩，必须重抽；lf 失败可降级为缺尾帧）。
    from shared.prompt_contradiction import (
        assert_no_contradictions,
        PromptContradictionError,
    )
    _assert_lf = shot.get("lf_desc_final")
    if _assert_lf:
        try:
            assert_no_contradictions(_assert_lf, f"shot{idx} 尾帧")
        except PromptContradictionError as e:
            _bridge_log(f"  [LF-SKIP] shot{idx} 尾帧 prompt 矛盾，软跳过（不阻塞整批）: {e}")
            return None

    refs = reference_paths if reference_paths else []
    # ---- lf prompt 组装（只算一次，重试复用） ----
    _lf_pre = preprocess_prompt_for_image(lf_desc)
    # v1.1：注入出镜角色服装特征（与首帧一致）
    _lf_pre = _inject_frame_spec_and_closeup(_lf_pre, shot, characters, frame_kind="last_frame")
    # v1.1：尾帧目光修复（错别字 + 看向动作结果对象——听香看被刺倒的杨旭）
    _lf_pre = fix_lf_desc_gaze(_lf_pre, shot, characters)
    # 场景物体尺寸锚定注入（船/车等物体比例防漂移）
    _lf_pre = _inject_object_size_anchor(shot, _lf_pre)
    # 场景色调锚定注入（color_tone：夜景冷蓝/暖金烛光等，防模型自造色调漂色）
    _lf_pre = _inject_tone_anchor(shot, _lf_pre)
    # 角色姿态/位置/朝向锚定块（代码级兜底——lf_desc 是动作结果态，从 shot
    # 全文提取出镜角色的终态姿态/位置，防"浪花消失/角色站位漂移"；
    # water_state=True 尾帧也补船完整+水面状态）
    # v6.61：frame_kind="last_frame" → spec 驱动出镜表（lf 刺客 on_screen=false
    # → 不再进锚定块，治 09-05 hook lf 站立刺客根因）
    _lf_anchor = _extract_char_layout(shot, characters,
                                      role_scan_text=shot.get("lf_desc", ""),
                                      water_state=True,
                                      frame_kind="last_frame")
    if _lf_anchor:
        _lf_pre = f"{_lf_pre}。{_lf_anchor}"
    # A3 修复：道具空间方位锚定块（尾帧同框道具落点，防自由摆放重叠）
    _lf_prop = _extract_prop_layout(shot, characters,
                                    role_scan_text=shot.get("lf_desc", ""))
    if _lf_prop:
        _lf_pre = f"{_lf_pre}。{_lf_prop}"
    # 尾帧注入场景上下文 + 显式"重构图"指令：
    # 参考图（首帧）只作人物/风格锚定，构图必须按 lf_desc 重新布局，
    # 否则参考图会把动作结果帧也锁死在首帧构图上，三帧雷同、动作无法推进。
    scene = shot.get("scene", "") or ""
    location = shot.get("location", "") or ""
    ctx = f"（场景：{location or scene}）" if (location or scene) else ""
    lf_desc_full = (
        f"{_lf_pre}{ctx}"
        f"，构图需按尾帧描述重新布局，"
        f"可与首帧/参考图的景别、机位、人物位置不同，表现动作演变的最终状态。"
    )
    lf_desc_full = sanitize_prompt(ensure_prefix(lf_desc_full, STYLE_PREFIX), verbose=False)
    lf_desc_full = add_image_strict_constraints(lf_desc_full, "尾帧", shot=shot, characters=characters)
    # ---- lf prompt 组装结束 ----

    for attempt in range(max_retries + 1):
        if lf_path.exists() and not force and attempt == 0:
            # 检查哈希文件是否存在且匹配
            if lf_hash_path.exists():
                saved_hash = lf_hash_path.read_text(encoding="utf-8").strip()
                if saved_hash == current_hash and _validate_frame(str(lf_path)):
                    # B7：缓存命中但 QC critical → 删帧+删哈希，下次重生成
                    # v6.x.x P1 修：continue 让 attempt 走到 1 触发 variation[1]
                    if await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene):
                        _del_frame_and_hash(lf_path, lf_hash_path, "尾帧", idx, logger)
                        continue
                    else:
                        return str(lf_path)
                else:
                    logger.verbose(f"  [FRAME] 尾帧 shot_{idx} 哈希不匹配或缓存无效，重新生成")
            elif script_mtime is not None and lf_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(lf_path)):
                    if await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene):
                        _del_frame_and_hash(lf_path, lf_hash_path, "尾帧", idx, logger)
                        continue
                    else:
                        return str(lf_path)
            elif script_mtime is None and _validate_frame(str(lf_path)):
                if _validate_frame(str(lf_path)):
                    if await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene):
                        _del_frame_and_hash(lf_path, lf_hash_path, "尾帧", idx, logger)
                        continue
                    else:
                        return str(lf_path)

        logger.verbose(f"  [FRAME] 生成尾帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        # 2026-08-29：lf_desc_full 已在循环外组装，prompt 复用同一份；不再重新追加锚块。
        # v6.x：per-attempt prompt 微扰动（仅局部变量，不回写 shot / 不影响哈希 / 不影响 QC 断言源）。
        attempt_variation = _ATTEMPT_VARIATIONS[attempt % len(_ATTEMPT_VARIATIONS)]
        try:
            img_out = await image_adapter.generate_single_image(
                prompt=lf_desc_full + attempt_variation,
                reference_image_paths=refs,
                shot_index=idx,
                frame_desc="尾帧",
                # 尾帧：解除"场景布局不变"锁，允许构图随动作演变
                lock_prompt=image_lock_prompt(layout_lock=False),
                force_multi_ref=True,
            )
            if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                img_out.data.save(str(lf_path))
            elif hasattr(img_out, "save"):
                img_out.save(str(lf_path))
        except Exception as e:
            _bridge_log(f"  [FRAME] 尾帧 shot_{idx} 生成异常: {e}")
            if attempt < max_retries:
                continue
            return None

        if _validate_frame(str(lf_path)):
            # 保存哈希值，供下次缓存检测使用
            lf_hash_path.write_text(current_hash, encoding="utf-8")
            # B7 修复：尾帧 QC critical → unlink 坏帧 + 删哈希，重试循环重生成
            qc_critical = await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene,
                                                 variation=attempt_variation)
            if qc_critical:
                # v6.54.1：先修后抽（与首帧同策略）
                if await _try_qc_repair(image_adapter, shot, str(lf_path), "last_frame",
                                        characters, empty_scene, idx):
                    return str(lf_path)
                elif attempt < max_retries:
                    # 还有下一次 attempt → 删帧重抽（B7 修复）
                    logger.verbose(f"  [FRAME] 尾帧 shot_{idx} QC critical，删帧重生成")
                    try:
                        Path(lf_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    try:
                        Path(lf_hash_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                else:
                    # 末次尝试仍 critical → 保留最不差一版（attempts_exhausted 留痕），
                    # sidecar 已带 critical 记录，留痕供人工复核；无图比坏图更伤链路
                    _bridge_log(f"  [QC] shot_{idx} {attempt+1} 次尝试均 critical，"
                                "保留末版留痕（attempts_exhausted）")
                    return str(lf_path)
            return str(lf_path)
        if attempt < max_retries:
            logger.verbose(f"  [FRAME] 尾帧 shot_{idx} 第{attempt+1}次不达标，重试...")

    return str(lf_path) if lf_path.exists() else None


def _dump_json(path: Path, model) -> None:
    """写 pydantic 模型或模型列表为 JSON（ViMax 用 model_validate_json 读回）."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(model, list):
        data = [m.model_dump() for m in model]
    else:
        data = model.model_dump()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)




# ---------------------------------------------------------------------------
# 中间关键帧生成（多关键帧叙事模式）
# ---------------------------------------------------------------------------

# ff 整图锚条件化阈值 / 阶段豁免：单一事实源 = shared.split_similarity
# （与 step01 constraints/kf_frame_redundancy 共用，禁止在此重定义——本地副本会
# 被下方 import 静默覆盖，改本地值不生效。校准数据见 split_similarity.KF_FF_*）。
from shared.split_similarity import (
    KF_FF_SIM_THRESHOLD as _KF_FF_SIM_THRESHOLD,
    KF_FF_ANCHOR_EXEMPT_STAGES as _KF_FF_ANCHOR_EXEMPT_STAGES,
    text_sim_ratio as _text_sim_ratio,
)


def _compose_kf_refs(base_refs: list, *, kf_desc_raw: str, ff_desc: str,
                     kf_stage: str | None = None,
                     ff_image_path: str | None = None,
                     scene_alt_refs: list | None = None,
                     prop_ref_paths: list | None = None,
                     prop_scan: str = "",
                     portrait_ref_paths: list | None = None,
                     portrait_replacements: list | None = None,
                     lf_image_path: str | None = None,
                     closeup: bool = False,
                     shot: dict | None = None,
                     characters: list | None = None,
                     ref_limit: int | None = None,
                     logger=None) -> list:
    """逐帧组装 kf 参考图（09-13 悬空匕首 / ff-kf0 同源双根因修复）.

    (a) 微动作帧去 ff 整图锚：mid 阶段（approach/climax/react）kf desc 与
        ff_desc 高相似时，ff 整图会把 kf 构图锁死成 ff 复刻——i2i 连 ff 里
        的无主悬浮物都原样抄入。摘掉 ff 整图改补场景资产图（环境锚仍在：
        船向/月亮/背景不漂移），构图还给本帧 desc。pre/exit/after 豁免
        （≈ff/≈lf 是阶段语义设计）；stage 缺失视为 mid。⚠️ ff 整图锚本身
        是 2a 治「kf 每帧翻船」的修复（见 gen_shot_frames 注释），不能全删，
        只在高相似 mid 帧条件化替换。
     (b) 道具参图 per-frame 化：kf_refs 组装按 ff+lf+motion 合并文本匹配道具
         （打耳光镜漏进刺杀戏「神秘人匕首」prop.png），改为本帧 desc+spec
         逐帧匹配。

    closeup: 本帧是否特写（调用方经 _frame_spec_closeup 判定后传入，避免在此
        重复实现「特写」文本扫描）。特写帧不补场景资产图——广角图会把特写
        构图拉回全景，与 ff 整图锚同害（09-13 shot_4 lf 手部特写根因）。

    base_refs 不变时原样返回（保持哈希/缓存语义不变）。
    """
    refs = list(base_refs) if base_refs else []
    if not refs or (not ff_image_path and not prop_ref_paths and not portrait_ref_paths):
        return refs
    if ref_limit is None:
        from shared.ref_compose import active_image_ref_limit
        ref_limit = active_image_ref_limit()

    # (c) 肖像逐帧替换（09-13「用到才拼」）：portrait_replacements 非 None 时
    # 把 per-shot 组装的肖像整组换成本帧 on_screen 名单过滤后的结果——
    # 名单外的缺席角色不拼图（extra_person 根因）。替换组保持肖像优先序
    # （名额裁剪从尾部道具开始，不挤肖像）。
    if portrait_ref_paths and portrait_replacements is not None:
        base = [r for r in refs if r not in portrait_ref_paths]
        new_refs = ([p for p in portrait_replacements if p not in base]
                    + base)[:ref_limit]
        if new_refs != refs:
            refs = new_refs
            if logger:
                logger.verbose("  [KEYFRAME] 肖像参图按本帧 on_screen 名单重配（per-frame）")

    # (d) after/exit 帧环境锚换 lf 整图（09-13 shot_0 kf3 实测）：after/exit≈lf
    # 是阶段语义设计，但环境锚仍挂 ff 整图——ff 里的刺客被 i2i 抄进「刺客已
    # 遁水」的 after 帧（extra_person 3 vs 2）。lf 已生成时换 lf 作环境锚
    # （既保环境连续性，又不带回已离场角色）。
    stage_l = (kf_stage or "").strip().lower()
    if (stage_l in ("after", "exit") and lf_image_path and ff_image_path
            and ff_image_path in refs and Path(str(lf_image_path)).exists()):
        refs = [str(lf_image_path) if r == ff_image_path else r for r in refs]
        if logger:
            logger.verbose("  [KEYFRAME] after/exit 帧环境锚：ff 整图→lf 整图"
                           "（防已离场角色经图像通道回流）")

    mid_action = stage_l not in _KF_FF_ANCHOR_EXEMPT_STAGES
    if (mid_action and ff_image_path and ff_image_path in refs
            and _text_sim_ratio(kf_desc_raw, ff_desc) >= _KF_FF_SIM_THRESHOLD):
        kept = [r for r in refs if r != ff_image_path]
        # 特写帧不补场景资产图（广角图会把特写构图拉回全景，与 ff 锚同害）；
        # closeup 由调用方判定传入，不在此扫描 prop_scan 文本（口径唯一）。
        if not closeup:
            for sr in (scene_alt_refs or []):
                if len(kept) >= ref_limit:
                    break
                if sr and str(sr) not in kept and Path(str(sr)).exists():
                    kept.append(str(sr))
        if kept:  # 防御：替换后不得为空（空则保留原 refs）
            refs = kept
            if logger:
                logger.verbose("  [KEYFRAME] kf 与 ff 高相似：ff 整图锚→场景资产图"
                               "（防复刻锁死）")

    if prop_ref_paths:
        # 有意只减不加（09-13 审查确认）：prop_ref_paths 为空（合并文本无道具
        # 命中）时整个分支跳过——即使本帧 desc 独有道具词也不补参图。防误加
        # 优先（08-16 钓竿幻觉教训：道具参图本身会诱导模型画道具）；漏补的
        # 代价仅是本帧道具外观与 bank 略漂移，可接受。
        base = [r for r in refs if r not in prop_ref_paths]
        per_kf = _collect_prop_asset_refs(
            shot or {}, limit=max(0, ref_limit - len(base)),
            frame_desc=prop_scan, characters=characters) or []
        new_refs = base + [p for p in per_kf if p not in base]
        if new_refs != refs:
            refs = new_refs
            if logger:
                logger.verbose("  [KEYFRAME] 道具参图按本帧 desc+spec 重配（per-frame）")
    return refs


async def _gen_keyframes_async(
    image_adapter, shots_dir: Path, shot: dict, force: bool,
    script_mtime: float | None = None,
    reference_paths: list | None = None,
    max_retries: int = 1,
    empty_scene: bool = False,
    characters: list | None = None,
    only_indices: set[int] | None = None,
    ff_image_path: str | None = None,
    scene_alt_refs: list | None = None,
    prop_ref_paths: list | None = None,
    portrait_ref_paths: list | None = None,
    lf_image_path: str | None = None,
) -> list[str]:
    """为镜头生成中间关键帧图片（多关键帧叙事模式）。

    从 shot['keyframes'] 数组读取关键帧描述，生成对应的图片。
    返回关键帧图片路径列表（不包括首帧和尾帧）。

    Args:
        keyframes: 如 [{"desc": "...", "time_ratio": 0.3}, ...]
        only_indices: 调试逐图选择——只生成指定序号的中间关键帧
            （元素为中间帧序号 i，即 keyframe_{i}.png 的 i；None=全部）

    Returns:
        关键帧图片路径列表
    """
    import asyncio
    from pathlib import Path as P

    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)

    # 日志
    from shared.tee_logger import get_logger
    from shared.paths import chapter_dir
    _kf_logger = get_logger(chapter_dir() / "step02.log", console_level="INFO")

    keyframes_data = shot.get("keyframes", [])
    if not keyframes_data:
        return []

    # v6.34 kf 字段迁移：time_ratio → stage + t_sec。
    # 必须走 shared.kf_time.is_middle_kf()（单一事实源），不能用 kf.get("time_ratio")。
    # 旧实现 `0 < time_ratio < 1` 在 kf 只有 t_sec 字段时永远 default=0，
    # 全部关键帧被过滤掉 → 关键帧从不生成。
    from shared.kf_time import is_middle_kf, get_kf_t_sec
    intermediate_kfs = [
        kf for kf in keyframes_data
        if isinstance(kf, dict) and is_middle_kf(kf, shot)
    ]

    if not intermediate_kfs:
        return []

    kf_paths = []
    for i, kf in enumerate(intermediate_kfs):
        # 调试逐图选择：只生成指定序号的中间关键帧（i 保持原始序号，
        # 与 keyframe_{i}.png 文件名一致；跳过不生成的不改文件名编号）
        if only_indices is not None and i not in only_indices:
            continue
        kf_desc = kf.get("desc", "").strip()
        if not kf_desc:
            continue
        kf_desc_raw = kf_desc  # 09-13：锚定注入前的原始 desc（per-frame 道具匹配/ff 相似度判定用）

        time_ratio = get_kf_t_sec(kf, shot) or 0.5
        t_sec = time_ratio  # v6.34 迁移后变量值是绝对秒数（不是 ratio），rename 给下游用
        kf_path = shot_dir / f"keyframe_{i}.png"
        kf_hash_path = shot_dir / f"keyframe_{i}_hash.txt"

        if empty_scene:
            kf_desc = kf_desc + "\n空镜头， 画面中严禁出现任何人物、身影、人影、轮廓或人形。只拍摄自然景物特写。"
        
        # 复用首帧哈希逻辑：基于 shot 内容派生 + 锁类型（关键帧用 NO_LAYOUT 锁）。
        # v2（08-10）：哈希含 kf_desc + time_ratio → 每个中间帧独立哈希，改 kf desc
        # 能触发该帧重出（原复用 shot 哈希，所有中间帧同值且改 desc 缓存不失效）。
        current_hash = _get_frame_content_hash(
            shot, empty_scene, reference_paths, lock_kind="no_layout",
            extra_fields={"kf_desc": kf_desc, "kf_t": time_ratio})
        
        for attempt in range(max_retries + 1):
            if kf_path.exists() and not force and attempt == 0:
                if kf_hash_path.exists():
                    saved_hash = kf_hash_path.read_text(encoding="utf-8").strip()
                    if saved_hash == current_hash and _validate_frame(str(kf_path)):
                        # B7：缓存命中但 QC critical → 删帧+删哈希，走下方重生成
                        # v6.x.x P1 修：continue 让 attempt 走到 1 触发 variation[1]
                        if await _maybe_qc_frame(shot, str(kf_path), "keyframe",
                                                 characters, empty_scene,
                                                 prompt=shot.get(f"kf_{i}_desc_final"),
                                                 kf_index=i):
                            _del_frame_and_hash(kf_path, kf_hash_path, f"关键帧{i}", idx, _kf_logger)
                            continue
                        else:
                            kf_paths.append(str(kf_path))
                            break
                    else:
                        _kf_logger.verbose(f"  [KEYFRAME] shot_{idx} keyframe_{i} 哈希不匹配或缓存无效，重新生成")
                elif script_mtime is not None and kf_path.stat().st_mtime >= script_mtime:
                    if _validate_frame(str(kf_path)):
                        if await _maybe_qc_frame(shot, str(kf_path), "keyframe",
                                                 characters, empty_scene,
                                                 prompt=shot.get(f"kf_{i}_desc_final"),
                                                 kf_index=i):
                            _del_frame_and_hash(kf_path, kf_hash_path, f"关键帧{i}", idx, _kf_logger)
                            continue
                        else:
                            kf_paths.append(str(kf_path))
                            break
                elif script_mtime is None and _validate_frame(str(kf_path)):
                    if await _maybe_qc_frame(shot, str(kf_path), "keyframe",
                                             characters, empty_scene,
                                             prompt=shot.get(f"kf_{i}_desc_final"),
                                             kf_index=i):
                        _del_frame_and_hash(kf_path, kf_hash_path, f"关键帧{i}", idx, _kf_logger)
                        continue
                    else:
                        kf_paths.append(str(kf_path))
                        break
            
            _kf_logger.verbose(f"  [KEYFRAME] 生成 shot_{idx} keyframe_{i} (t={time_ratio:.1f})...")
            # 关键帧注入场景上下文 + 显式"重构图"指令：
            # 关键帧是动作进行中的瞬间状态，构图需与首帧不同（如"推至中景"），
            # 若沿用首帧整图参考 + 布局不变锁，会锁死在首帧构图上。
            scene = shot.get("scene", "") or ""
            location = shot.get("location", "") or ""
            ctx = f"（场景：{location or scene}）" if (location or scene) else ""
            # 关键帧预处理：替换 AI 可能误解的词汇（与首帧/尾帧一致）
            kf_desc = preprocess_prompt_for_image(kf_desc)
            # 注入出镜角色服装特征（与首帧/尾帧一致）。kf_desc 常只含动作主体
            # （如"刺客破水而出"），杨旭/听香等配角名不在其中，需合并 shot 全文
            # 判定出镜角色——否则关键帧画出的角色服装/姿态与首尾帧不一致
            # （code-audit 08-10 hook keyframe 杨旭站立/听香撑伞/刺客消失）。
            role_scan = " ".join(filter(None, [
                shot.get("ff_desc", ""), shot.get("lf_desc", ""),
                shot.get("motion_desc", "") or shot.get("prompt", ""),
                kf_desc,
            ]))
            # v6.57+ spec 驱动：kf 用 frame_layouts.kfs[i] 各查各的 spec 角色表
            # 取代「role_scan_text 合并全文」启发式（治 kf 角色消失 / 角色错位）
            _kf_idx_in_shot = None
            try:
                _all_kfs = shot.get("keyframes") or []
                if kf in _all_kfs:
                    _kf_idx_in_shot = _all_kfs.index(kf)
            except Exception:
                pass
            kf_desc = _inject_frame_spec_and_closeup(
                kf_desc, shot, characters,
                frame_kind="keyframe", kf_index=_kf_idx_in_shot,
            )
            # 船/车等空间物体尺寸锚定（防关键帧船画小/画断——08-10 hook keyframe_0
            # 船少一截导致听香站水面）
            kf_desc = _inject_object_size_anchor(shot, kf_desc)
            # 场景色调锚定注入（color_tone：夜景冷蓝/暖金烛光等，防模型自造色调漂色）
            kf_desc = _inject_tone_anchor(shot, kf_desc)
            # 角色姿态/位置/朝向锚定块（代码级兜底——kf_desc 常只一行动作描述，
            # 从 shot 全文提取出镜角色的姿态/位置/朝向，防"杨旭站立后倒/听香撑伞/
            # 刺客消失/船断"（code-audit 08-10 hook 关键帧质量）。首帧也注入。
            # v6.61：frame_kind="keyframe"+kf_index → spec 驱动出镜表（kfs[i]
            # 自己的 on_screen + position/pose 直进锚定块，治「神秘人潜伏水面
            # （ff_desc 残留）vs 单膝跪船舷（kf1 desc）」三重矛盾根因）。
            kf_anchor = _extract_char_layout(shot, characters, role_scan_text=role_scan,
                                             frame_kind="keyframe",
                                             kf_index=_kf_idx_in_shot)
            # 08-15（PR-2）：脚本层 post_fix 已注入「【角色锚定】」骨架时跳过，防重复
            # 追加（幂等）。生成时仍由 _extract_char_layout 兜底补漏。
            if kf_anchor and "【角色锚定】" not in kf_desc:
                kf_desc = f"{kf_desc}。{kf_anchor}"
            # A3 修复：道具空间方位锚定块（关键帧同框道具落点，防自由摆放重叠）
            # 09-13：改 per-frame 文本（本帧 raw desc + 本帧 spec），不再用
            # ff+lf+motion 合并全文——合并文本让 ff 的武器词经 props_bank 别名
            # 匹配漏进无关帧（hook kf1「神秘人匕首单独摆放」脏锚定根因；spec
            # 是 v6.57 per-frame 事实源，道具在不在本帧以它+本帧 desc 为准）。
            _kf_prop_scan = _build_kf_prop_scan(shot, _kf_idx_in_shot, kf_desc_raw)
            kf_prop = _extract_prop_layout(shot, characters, role_scan_text=_kf_prop_scan)  # _kf_prop_scan 恒非空（kf_desc_raw 前置非空保证）；无 fallback——回合并全文=污染回归
            if kf_prop:
                kf_desc = f"{kf_desc}。{kf_prop}"
            # v6.61（09-05 hook kf 全部站位错根因之一）：删「环境构图与首帧保持
            # 一致」恒定尾巴——kfs[i] 各帧站位不同（破水→跪舷→下压→倒纵），
            # 硬说"构图与首帧一致"与 spec 差异化站位直接冲突，模型随机调和。
            # 保留场景连续性语义但改为环境层（船/月/背景），不再约束人物布局；
            # 人物布局由本帧 spec + desc 决定。
            kf_desc_full = (
                f"{kf_desc}{ctx}"
                f"，环境延续首帧：船的朝向、月亮位置、背景建筑方位、"
                f"水面荷叶分布不变（仅环境连续性约束，人物站位/动作以本帧描述为准），"
                f"表现动作过程中的瞬间状态。"
            )
            kf_desc_full = sanitize_prompt(ensure_prefix(kf_desc_full, STYLE_PREFIX), verbose=False)
            kf_desc_full = add_image_strict_constraints(kf_desc_full, "关键帧", shot=shot, characters=characters)
            refs = reference_paths if reference_paths else []
            # 09-13：逐帧重配参考图（ff 整图锚条件化 + 道具/肖像参图 per-frame）。
            # 零 prompt 改动，只动参考图；base_refs 不变时原样返回（缓存语义不变）。
            if refs and (ff_image_path or prop_ref_paths or portrait_ref_paths):
                # 肖像逐帧名单（spec on_screen）：本帧缺席角色的肖像不拼图——
                # 全量肖像会拉模型画全员（shot_0 lf extra_person 3 vs 1 根因）。
                # roster=None（spec 缺失）→ 不换，保留 per-shot 组装结果。
                _kf_portraits = None
                if portrait_ref_paths is not None:
                    _kf_roster = _frame_spec_roster(shot, "keyframe", _kf_idx_in_shot)
                    if _kf_roster is not None:
                        from shared.ref_compose import active_image_ref_limit as _arl
                        _kf_portraits = _collect_portrait_refs_for_shot(
                            shot, characters, limit=max(1, _arl() - 2),
                            frame_desc=_kf_prop_scan, roster=_kf_roster)
                refs = _compose_kf_refs(
                    refs, kf_desc_raw=kf_desc_raw, ff_desc=shot.get("ff_desc", ""),
                    kf_stage=kf.get("stage"),
                    ff_image_path=ff_image_path, scene_alt_refs=scene_alt_refs,
                    prop_ref_paths=prop_ref_paths, prop_scan=_kf_prop_scan,
                    portrait_ref_paths=portrait_ref_paths,
                    portrait_replacements=_kf_portraits,
                    lf_image_path=lf_image_path,
                    closeup=_frame_spec_closeup(shot, "keyframe", _kf_idx_in_shot),
                    shot=shot, characters=characters, logger=_kf_logger)

            try:
                # v6.x：per-attempt prompt 微扰动（仅局部变量，不回写 shot / 不影响哈希）。
                attempt_variation = _ATTEMPT_VARIATIONS[attempt % len(_ATTEMPT_VARIATIONS)]
                img_out = await image_adapter.generate_single_image(
                    prompt=kf_desc_full + attempt_variation,
                    reference_image_paths=refs,
                    shot_index=idx,
                    frame_desc=f"关键帧{i}(t={time_ratio:.1f})",
                    # 关键帧：解除"场景布局不变"锁，允许构图随动作演变
                    lock_prompt=image_lock_prompt(layout_lock=False),
                    force_multi_ref=True,
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(kf_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(kf_path))
            except Exception as e:
                _kf_logger.verbose(f"  [KEYFRAME] shot_{idx} keyframe_{i} 生成异常: {e}")
                if attempt < max_retries:
                    continue
                break
            
            if _validate_frame(str(kf_path)):
                # 挂实际最终 prompt（QC 语义核对用）+ 关键帧视觉质检（软失败不阻断）
                shot[f"kf_{i}_desc_final"] = kf_desc_full
                qc_critical = await _maybe_qc_frame(
                    shot, str(kf_path), "keyframe", characters,
                    empty_scene, prompt=kf_desc_full,
                    variation=attempt_variation,
                    kf_index=i)
                # B7 修复：关键帧 QC critical → 删帧 + 删哈希，下次重生成。
                # v6.x 末次兜底：与 ff/lf 对齐——坏图比没图好，保留末版留痕。
                # v6.x.x P5 修：关键帧也接 _try_qc_repair（v6.54.2 落地）——
                # 局部可修的 critical（红痕/睁眼/篷/发光/刀接触等）走 i2i bbox 编辑，
                # 复检通过直接保留，不消耗重抽额度。修复失败再走删帧/保留末版路径。
                if qc_critical:
                    if await _try_qc_repair(image_adapter, shot, str(kf_path), "keyframe",
                                            characters, empty_scene, idx, kf_index=i):
                        # 修复成功：哈希未删（下次缓存命中即修好的图）
                        kf_paths.append(str(kf_path))
                        break
                    if attempt < max_retries:
                        _kf_logger.verbose(f"  [KEYFRAME] shot_{idx} keyframe_{i} QC critical，删帧重生成")
                        try:
                            Path(kf_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                        try:
                            Path(kf_hash_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                        continue
                    # 末次仍 critical → 保留（与 ff/lf 对齐：坏图比没图好）
                    _kf_logger.verbose(f"  [QC] shot_{idx} keyframe_{i} {attempt+1} 次尝试均 critical，"
                                       "保留末版留痕（attempts_exhausted）")
                    kf_paths.append(str(kf_path))
                    break
                # v6.30：doodle 区域编辑 fallback（关键帧同首帧——精确空间约束
                # prompt 调不到位时走 i2i+红框二修）。失败保留原图（graceful degradation）。
                # 哈希键不变（基于 prompt，不是 image），同 prompt 重跑会再调一次 doodle。
                kf_doodle_cfg = _detect_spatial_fallback(kf_desc_full, shot)
                if kf_doodle_cfg:
                    doodle_tmp = kf_path.parent / f"{kf_path.stem}_doodle_tmp.png"
                    refined = await _doodle_refine_first_frame(
                        image_adapter, str(kf_path),
                        kf_doodle_cfg["bbox_ratio"], kf_doodle_cfg["refined_prompt"],
                        str(doodle_tmp), idx,
                    )
                    if refined and _validate_frame(refined):
                        Path(refined).replace(kf_path)
                        _kf_logger.verbose(f"  [DOODLE] shot{idx} keyframe_{i} 区域编辑成功覆盖 (phrase={kf_doodle_cfg['phrase']})")
                    else:
                        _kf_logger.verbose(f"  [DOODLE] shot{idx} keyframe_{i} 区域编辑失败，保留原图")
                kf_paths.append(str(kf_path))
                break
            if attempt < max_retries:
                _kf_logger.verbose(f"  [KEYFRAME] shot_{idx} keyframe_{i} 第{attempt+1}次不达标，重试...")
    
    if kf_paths:
        _bridge_log(f"  [bridge] 镜头{idx}: 生成 {len(kf_paths)} 个中间关键帧")
    
    return kf_paths

# ---------------------------------------------------------------------------
# 单独镜头帧生成（供 submit_ poll. py 调用）
# ---------------------------------------------------------------------------

async def gen_shot_frames(
    shot: dict,
    shots_dir: Path,
    image_adapter,
    script_mtime: float | None,
    force: bool = False,
    characters: list | None = None,
    skip_first_frame: bool = False,
    frames: set | None = None,
) -> dict:
    """为单个镜头生成/检查首帧、尾帧和中间关键帧。

    用于 submit_poll.py 中 skip_frames=False 时的逐镜帧生成。
    characters: script['characters']，用于空镜检测（空镜首帧禁止人物）。
    skip_first_frame: 模式B时跳过首帧生成（用上一镜尾帧代替），仅生成尾帧用于本镜风格锚定。
    frames: 调试逐图选择——帧类型 token 集合（ff/lf/kf/kf0/kf1...，见
        shared.shot_selector.parse_frame_selector）。提供时**精确控制**生成哪些图片，
        覆盖 Mode A/B 的默认跳过逻辑（如 idx>=2 的 Mode B 镜头也可单独生成首帧或
        全部关键帧，方便整镜视觉检查；代价是浪费额度，缓存仍生效——已生成的类型
        无改动会被跳过）。None=默认行为（Mode A 全量 / Mode B 只尾帧）。

    Returns:
        dict: {"ff_path": str | None, "lf_path": str | None, "keyframes_paths": list[str]}
    """
    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)

    ff_ = {}
    ff_path = shot_dir / FIRST_FRAME_FILENAME
    empty_scene = is_empty_scene(shot, characters or [])

    # 首帧参考图：场景资产图 + 出镜角色肖像 + 出镜道具资产图（图生图锚定）。
    # 此前 reference_paths=None 纯文生图，复杂多人构图（钩子"刺客+杨旭+听香"三人物）
    # 被图片模型简化成单人，首帧人物/构图全错，且场景/道具与资产库不一致。
    # 空镜只注入场景资产图（禁止角色/道具肖像，防人物渗入）。
    #
    # 修改2（2026-08-07）：动作镜头首帧不注入场景资产图——场景图被硬约束"禁止
    # 人物"，与多角色参考图矛盾时场景图占上风、人物被压制（钩子首帧几乎无人）。
    # 尾帧/关键帧仍保留场景参考图（环境锚定，NO_LAYOUT 锁下无碍）。
    # 08-16：尾帧与关键帧拆开收集——尾帧传 lf_desc（威胁源没入水中时不注入刺客
    # 肖像），关键帧传合并全文（动作中间态，威胁源以动作可见→保留肖像供
    # kf0「刺客半身探出举匕首」使用）。
    frame_refs_ff = _collect_frame_refs_for_shot(shot, characters, limit=5, for_frame="首帧")
    frame_refs_lf = _collect_frame_refs_for_shot(shot, characters, limit=5, for_frame="尾帧")
    frame_refs_kf = _collect_frame_refs_for_shot(shot, characters, limit=5, for_frame="关键帧")

    # ---- 帧选择解析：frames 提供时精确控制（调试），None 沿用 Mode A/B 默认 ----
    if frames is None:
        want_ff = not skip_first_frame
        want_lf = True
        want_kf = not skip_first_frame
        kf_only = None
    else:
        want_ff = "ff" in frames
        want_lf = "lf" in frames
        kf_specific = {int(t[2:]) for t in frames
                       if t.startswith("kf") and t[2:].isdigit()}
        want_kf = "kf" in frames or bool(kf_specific)
        # "kf"=全部关键帧，忽略具体序号；只有 kfN 时只生成对应序号
        kf_only = None if "kf" in frames else (kf_specific or None)

    if want_ff:
        # 模式A：首帧 generation (使用 _gen_first_frame_async 的缓存逻辑)
        ff_path_str = await _gen_first_frame_async(
            image_adapter, shots_dir, shot,
            force=force, script_mtime=script_mtime,
            reference_paths=frame_refs_ff or None, max_retries=1,
            empty_scene=empty_scene, characters=characters,
        )
    else:
        # 模式B（或 --frames 未选 ff）：跳过首帧生成（尾帧仍生成用于本镜风格锚定）
        ff_path_str = None
    ff_["ff_path"] = ff_path_str

    if want_lf:
        # 尾帧 generation（全镜头覆盖：首尾双关键帧钳制风格漂移，
        # 不再按 variation_type 过滤——LLM 常输出空值导致全部镜头无尾帧）
        # 尾帧参考图：场景/角色/道具资产（锚定人物形象与环境一致）。
        # 不再把首帧整图作为尾帧参考图——首帧是动作起点，直接抄首帧构图会让
        # 尾帧几乎复刻首帧（刺客同位置、听香位置错乱、两帧雷同），
        # 动作演变只能靠 lf_desc 文字描述驱动。首帧只通过 frame_refs_full 的
        # 场景资产图隐式锚定环境/光线，不用整图锁死尾帧构图。
        lf_refs = frame_refs_lf or None
        lf_path_str = await _gen_last_frame_async(
            image_adapter, shots_dir, shot,
            force=force, script_mtime=script_mtime,
            reference_paths=lf_refs,
            max_retries=1,
            empty_scene=empty_scene,
            characters=characters,
        )
    else:
        lf_path_str = None
    ff_["lf_path"] = lf_path_str

    # 中间关键帧 generation（多关键帧叙事模式）
    # 模式B时跳过中间关键帧生成（已用尾帧接续替代关键帧锚定）
    if want_kf:
        # 关键帧参考图策略（环境锚定制衡版，2a）：
        #   [角色肖像拼图] + [首帧整图] + [道具资产拼图]
        # 首帧整图 = 环境锚（船向/月亮/背景/机位），解决 kf 每帧翻船问题。
        # 角色肖像 = 人物一致性锚。道具资产 = 道具外观锚。
        # 场景资产图被首帧整图替代（首帧里已经有完整场景了）。
        # 人物动作变化通过 prompt "环境不变、仅人物动作与景别变化" 引导（2b）。
        from shared.ref_compose import active_image_ref_limit
        ref_limit = active_image_ref_limit()
        kf_refs: list[str] = []

        # 优先级 1：角色肖像（人物一致性最关键）
        portrait_refs = _collect_portrait_refs_for_shot(
            shot, characters, limit=max(1, ref_limit - 2),
            frame_desc=" ".join(filter(None, [
                shot.get("ff_desc", ""), shot.get("lf_desc", ""),
                shot.get("motion_desc", "") or shot.get("prompt", ""),
            ])),
        )
        kf_refs.extend(portrait_refs)

        # 优先级 2：首帧整图（环境锚定）—— 仅当首帧已生成
        if ff_path_str and Path(ff_path_str).exists() and len(kf_refs) < ref_limit:
            kf_refs.append(str(ff_path_str))

        # 优先级 3：道具资产图（有剩余额度再加）
        # 09-13：prop_refs_for_kf 留作 per-frame 重配的对照组（按合并文本匹配的
        # 道具参图会在 _compose_kf_refs 里被本帧匹配结果替换）
        prop_refs_for_kf: list[str] = []
        if len(kf_refs) < ref_limit:
            prop_refs_for_kf = _collect_prop_asset_refs(
                shot, limit=ref_limit - len(kf_refs),
                frame_desc=" ".join(filter(None, [
                    shot.get("ff_desc", ""), shot.get("lf_desc", ""),
                    shot.get("motion_desc", "") or shot.get("prompt", ""),
                ])),
                characters=characters,
            )
            kf_refs.extend(prop_refs_for_kf[:ref_limit - len(kf_refs)])

        # 兜底：如果上面组装失败（如空镜），回退原始 frame_refs_kf
        if not kf_refs and frame_refs_kf:
            kf_refs = list(frame_refs_kf)
        kf_refs = kf_refs or None
        kf_paths = await _gen_keyframes_async(
            image_adapter, shots_dir, shot,
            force=force, script_mtime=script_mtime,
            reference_paths=kf_refs,
            max_retries=1,
            empty_scene=empty_scene,
            characters=characters,
            only_indices=kf_only,
            ff_image_path=(str(ff_path_str)
                           if ff_path_str and Path(ff_path_str).exists() else None),
            scene_alt_refs=_collect_scene_asset_refs(shot),
            prop_ref_paths=prop_refs_for_kf or None,
            portrait_ref_paths=portrait_refs or None,
            lf_image_path=(str(lf_path_str)
                           if lf_path_str and Path(lf_path_str).exists() else None),
        )
    else:
        kf_paths = []
    ff_["keyframes_paths"] = kf_paths

    return ff_
