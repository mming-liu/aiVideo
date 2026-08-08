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

import hashlib

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

# 首帧/尾帧生成需要风格前缀（与视频生成保持一致）
from style_prefix import STYLE_PREFIX, sanitize_prompt, ensure_prefix, is_empty_scene, add_image_strict_constraints, preprocess_prompt_for_image
from style_prefix import IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT


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
        with open(chars_path, "w", encoding="utf-8") as f:
            json.dump(char_objects, f, ensure_ascii=False, indent=2)
        if char_objects:
            _bridge_log(f"  [bridge] characters.json: {len(char_objects)} 个角色")
    elif not characters_data:
        if not chars_path.exists():
            with open(chars_path, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

    # ---- 2. character_portraits_registry.json ----
    # 每次都重写：registry 由 bank 实时导出，旧文件可能残留迁移前的失效路径
    registry_path = working_dir / "character_portraits_registry.json"
    if registry:
        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
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
        ff_path = shot_dir / "first_frame.png"
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
            )

        bg_tasks.append(asyncio.create_task(_gen_rest_frames(shot, ff_path, idx)))

    # 等待所有后台尾帧/关键帧任务完成
    if bg_tasks:
        await asyncio.gather(*bg_tasks)


# ---------------------------------------------------------------------------
# 参考图列表构建（摄像机锚定）
# ---------------------------------------------------------------------------

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

    # 角色肖像：根据 IMAGE_MULTI_REF 决定单张还是多张
    # 1（默认）= 单张最优肖像（向后兼容）；2+ = 锚定帧优先 + 多角色肖像填充剩余名额
    from shared.config import IMAGE_MULTI_REF
    if IMAGE_MULTI_REF >= 2:
        # 多参考图：剩余名额分给出镜角色（同框多角色各自传入肖像）
        remaining = IMAGE_MULTI_REF - len(refs)
        char_refs = _select_all_character_refs(shot, bank, characters_data, limit=remaining)
        for cr in char_refs:
            if cr not in refs:
                refs.append(cr)
        # 一个角色都没匹配到时，回退到单张最优参考图（含风格锚点）
        if not char_refs:
            char_ref = _select_best_reference(shot, bank, style_bank, characters_data)
            if char_ref and char_ref not in refs:
                refs.append(char_ref)
        return refs[:IMAGE_MULTI_REF]

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
    portrait_adapter = ImageGeneratorAiVideoAdapter(
        size="1600x900",
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
                          prompt: str | None = None):
    """帧生成后执行视觉质检（软失败，不阻断流水线）.

    通过 shared/image_qc.py 调用 ds-vision-skill 的 vision-router.ps1 对帧图做内容质检；
    QC 成功（ok=True）即写 sidecar（检出与否都写，has_hallucination=false 表示"已检过无问题"，
    供 should_run_qc 靠 sidecar 新鲜度跳过重复质检）。默认封顶 warning，不触发 step02b 自动重生成。
    asyncio.to_thread 避免阻塞事件循环（单次质检 2-4 分钟）。

    prompt: v1.1 语义核对用——实际生成图片的最终 prompt。不传时从
    shot["ff_desc_final"]/["lf_desc_final"] 取（_gen_first_frame_async 挂载）。
    """
    if not frame_path:
        return
    # 未显式传 prompt 时，从 shot 挂载的最终 prompt 取（QC 语义核对用）
    if prompt is None and isinstance(shot, dict):
        if frame_kind == "last_frame":
            prompt = shot.get("lf_desc_final") or shot.get("ff_desc_final")
        else:
            prompt = shot.get("ff_desc_final") or shot.get("lf_desc_final")
    try:
        from shared.image_qc import maybe_qc_sync, sidecar_path, write_sidecar
        result = await asyncio.to_thread(
            maybe_qc_sync, frame_path, shot, frame_kind, characters, empty_scene, prompt,
        )
        if not result.ok:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 质检失败跳过（未写 sidecar）: {result.error}")
            return
        # QC 成功 → 写 sidecar（检出与否都写），供下次跳过与 step02b 消费
        try:
            write_sidecar(Path(frame_path), result)
        except Exception as e:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} sidecar 写入失败: {e}")
        if result.has_hallucination:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 检出问题: "
                        + "; ".join(f"[{i.severity}]{i.category} {i.description}" for i in result.issues))
        else:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 质检通过")
    except Exception as e:
        _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} 质检异常（不阻断）: {e}")


def _get_frame_content_hash(shot: dict, empty_scene: bool = False,
                            reference_paths: list | None = None) -> str:
    """计算首帧/尾帧的稳定哈希值 v4.

    v4: 新增 _prompt_sig() 对实际图片 prompt (ff_desc/lf_desc) 做规范化哈希，
    解决分镜词（ff_desc/lf_desc/motion_desc）变更后图片缓存不失效的问题。
    规范化（strip + 统一空白字符）避免 LLM 微小格式差异导致永不命中。

    v3: 使用稳定字段（main_subject/scene/core_keywords）代替完整 prompt，
    避免 LLM 生成的 prompt 文本有微小差异（换行/空格/词汇顺序）导致
    哈希永远不匹配，缓存永远失效的问题。
    reference_paths 由 shot 内容派生，内容不变则路径语义不变，不纳入哈希。
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
    ]
    key_parts.extend(stable_fields)

    # v4: 规范化后的实际图片 prompt 文本 — 分镜词变了图片就该重新生成
    for field_name in ("ff_desc", "lf_desc", "motion_desc"):
        val = shot.get(field_name, "")
        if val:
            key_parts.append(f"{field_name}={_normalize(val)}")

    if empty_scene:
        key_parts.append("empty_scene")    # 空镜判定翻转时帧应重做

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


def _collect_prop_asset_refs(shot: dict, limit: int = 2) -> list[str]:
    """为本镜头匹配道具资产图（props_bank prop.png）作道具参考.

    与 r2v 同源匹配：道具名/consistent_terms 出现在本镜 ff_desc/motion_desc/
    lf_desc 或 location 中。注入道具资产图保证首帧出现的道具外观/材质/尺寸
    与全局道具库一致（如钓杆、弯刀），避免图生图自由发挥画错道具。
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
            match_sources = [pid] + list(entry.get("consistent_terms", []) or [])
            if any(src and (src in search_text or src in loc) for src in match_sources):
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
    if empty_scene:
        return scene_refs[:limit]

    portrait_refs = _collect_portrait_refs_for_shot(shot, characters, limit=max(1, limit - len(scene_refs)))
    prop_refs = _collect_prop_asset_refs(shot, limit=max(1, limit - len(scene_refs) - len(portrait_refs)))

    refs: list[str] = []
    for p in scene_refs + portrait_refs + prop_refs:
        if p not in refs:
            refs.append(p)
        if len(refs) >= limit:
            break
    return refs


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


def _collect_portrait_refs_for_shot(shot: dict, characters: list | None = None,
                                    limit: int = 3) -> list[str]:
    """从 character_portraits_registry.json 为本镜头出镜角色收集肖像参考图.

    用于 step02a 首帧/尾帧图生图：给图片模型注入本镜出镜角色的肖像作参考，
    复杂多人构图（如钩子"刺客半身踞船舷+杨旭仰倒+听香画右尖叫"）才能画出
    正确的人物形象与站位，避免纯文生图把三人构图简化成单人。

    判定"出镜角色"与 _collect_r2v_references 同源：角色名出现在本镜
    ff_desc/motion_desc/lf_desc 中。视角匹配：画面描述含背面/侧面词则优先
    对应视角，否则默认 front。空镜/无角色匹配返回空列表（调用方降级纯文生图）。

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
                from style_prefix import ORIENTATION_KW
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
            if ident not in search_text:
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

    shot: 镜头 dict（含 location/scene）
    text: 已组装的帧 prompt
    """
    loc = shot.get("location", "") or shot.get("scene", "") or ""
    if not loc:
        return text
    try:
        from shared.paths import chapter_dir
        import json as _json
        script_path = chapter_dir() / "script_result.json"
        if not script_path.exists():
            return text
        script = _json.load(open(script_path, encoding="utf-8"))
        objs = script.get("scene_objects", {})
        if not isinstance(objs, dict) or not objs:
            return text
        for name, obj in objs.items():
            if not isinstance(obj, dict):
                continue
            # 匹配：物体名/一致称呼出现在镜头地点或画面描述中
            match_sources = [name] + list(obj.get("consistent_terms", []) or [])
            if obj.get("category"):
                match_sources.append(obj["category"])
            if not any(src and src in (loc + text) for src in match_sources if src):
                continue
            length = obj.get("length_m")
            width = obj.get("width_m")
            subtype = obj.get("subtype", "")
            if not length and not width:
                return text
            # 只注入尺寸/称呼，不注入容量结构（避免与画面描述冲突）
            size_desc = f"该{name}为{subtype}{'，长' + str(length) + '米' if length else ''}{'、宽' + str(width) + '米' if width else ''}"
            if size_desc not in text:
                return f"{text}。{size_desc}"
        return text
    except Exception:
        return text


# 服装颜色/服装词提示（幂等判定：desc 已含服装描述则不重复注入）
_COSTUME_COLOR_HINTS = ("白", "素", "青", "蓝", "红", "黑", "绿", "紫", "褐", "金", "灰", "粉", "墨")
_COSTUME_WORDS = ("袍", "衫", "衣", "裳", "裙", "袄", "裰", "服", "袈", "氅")


def _inject_character_costume(desc: str, characters: list | None,
                              shot: dict | None = None) -> str:
    """给首帧/尾帧 prompt 注入出镜角色的服装特征（dynamic_features）.

    出镜角色 = desc 中出现的角色名。按角色名周围窗口检测该角色是否已有服装
    描述（颜色词+服装词，或服装词）——已有则跳过（幂等），否则追加
    「{角色}身着{dynamic}」，保证图片模型知道角色服装颜色——否则「夜色深沉」
    背景下模型自由发挥成深色（code-audit 2026-08-08 首帧杨旭深色衣 vs 角色
    特征「素白色轻袍」）。"黑衣刺客"里的"衣"只算刺客的服装，不影响杨旭。

    空镜（empty_scene）不注入（禁人物）。dynamic_features 缺失时跳过该角色。
    """
    if not desc or not characters or not shot:
        return desc
    try:
        if is_empty_scene(shot, characters):
            return desc
        additions = []
        for c in characters:
            ident = c.get("identifier", "") or ""
            if not ident or ident not in desc:
                continue
            dynamic = (c.get("dynamic_features") or "").strip()
            if not dynamic:
                continue
            # 按角色名周围窗口检测该角色是否已有服装（幂等，避免只补一个角色）
            idx = desc.find(ident)
            window = desc[max(0, idx - 3):idx + 30]
            if any(w in window for w in _COSTUME_WORDS):
                continue  # 该角色已有服装词（如"黑衣刺客"）
            additions.append(f"{ident}身着{dynamic}")
        if additions:
            return f"{desc}。{('，'.join(additions))}"
        return desc
    except Exception:
        return desc


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
        from style_prefix import (
            preprocess_prompt_for_image, sanitize_prompt, ensure_prefix,
            add_image_strict_constraints,
        )
        text = preprocess_prompt_for_image(desc)
        # v1.1：注入出镜角色服装特征（与 _gen_first_frame_async 生成链一致）
        text = _inject_character_costume(text, characters, shot)
        # v1.1：尾帧目光修复（与 _gen_last_frame_async 一致，QC 看到修复后 prompt）
        if frame_kind == "last_frame":
            text = fix_lf_desc_gaze(text, shot, characters)
        text = _inject_object_size_anchor(shot, text)
        text = sanitize_prompt(ensure_prefix(text, STYLE_PREFIX), verbose=False)
        text = add_image_strict_constraints(text, "首帧" if frame_kind == "first_frame" else "尾帧", shot=shot)
        return text
    except Exception:
        return desc


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
    ff_path = shot_dir / "first_frame.png"
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
        from style_prefix import normalize_ff_desc
        norm = normalize_ff_desc(ff_desc, shot, characters)
        if norm != ff_desc:
            shot["ff_desc"] = ff_desc = norm
            _bridge_log(f"  [bridge] 镜头{idx}: ff_desc 归一化（多动作→静态瞬间）")

    # 计算当前哈希值（核心意图 + 空镜约束 + 参考图，任一变化都重新生成）
    current_hash = _get_frame_content_hash(shot, empty_scene, reference_paths)
    # 动作镜头首帧改用 NO_LAYOUT 锁图（解除"场景布局不变"），prompt 语义变化，
    # 追加 _ff_nl 后缀强制旧缓存（无人的首帧）失效重新生成。
    if _is_action_shot(shot, characters):
        current_hash += "_ff_nl"

    # v1.1：挂实际生成最终 prompt 到 shot（QC 语义核对用）。缓存命中/生成两条
    # 路径都在此补算——缓存命中时 ff_desc_full 未计算，但 shot["ff_desc"] 已被
    # normalize 回写，用 _build_final_frame_prompt 补算得到与图片模型一致的文本。
    try:
        shot["ff_desc_final"] = _build_final_frame_prompt(shot, ff_desc, "first_frame", characters)
    except Exception:
        pass

    for attempt in range(max_retries + 1):
        if ff_path.exists() and not force and attempt == 0:
            # 检查哈希文件是否存在且匹配
            if ff_hash_path.exists():
                saved_hash = ff_hash_path.read_text(encoding="utf-8").strip()
                if saved_hash == current_hash and _validate_frame(str(ff_path)):
                    await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene)
                    return str(ff_path)
                else:
                    logger.verbose(f"  [FRAME] 首帧 shot_{idx} 哈希不匹配或缓存无效，重新生成")
            elif script_mtime is not None and ff_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(ff_path)):
                    await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene)
                    return str(ff_path)
            elif script_mtime is None and _validate_frame(str(ff_path)):
                await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene)
                return str(ff_path)

        logger.verbose(f"  [FRAME] 生成首帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        # 添加风格前缀 + 严格约束（与视频生成保持一致，确保风格统一）
        # 先预处理：替换 AI 可能误解的词汇
        ff_desc = preprocess_prompt_for_image(ff_desc)
        # v1.1：注入出镜角色服装特征（防"夜色深沉"背景下模型自由发挥深色衣）
        ff_desc = _inject_character_costume(ff_desc, characters, shot)
        # 场景物体尺寸锚定注入（船/车等物体比例防漂移，code-audit 08-06 钩子船太小）
        ff_desc = _inject_object_size_anchor(shot, ff_desc)
        ff_desc_full = sanitize_prompt(ensure_prefix(ff_desc, STYLE_PREFIX), verbose=False)
        ff_desc_full = add_image_strict_constraints(ff_desc_full, "首帧", shot=shot)
        refs = reference_paths if reference_paths else []
        # 动作镜头首帧：解除"场景布局不变"锁，允许人物动作/构图随描述演变
        # （普通镜头不传 lock_prompt，保持适配器默认完整锁）。
        # 注意不能传 None——适配器 kwargs.get("lock_prompt", DEFAULT) 会把 "None，" 拼进 prompt。
        is_action = _is_action_shot(shot, characters)
        gen_kwargs = dict(
            prompt=ff_desc_full,
            reference_image_paths=refs,
            shot_index=idx,
            frame_desc="首帧",
            force_multi_ref=True,
        )
        if is_action:
            gen_kwargs["lock_prompt"] = IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT
        try:
            img_out = await image_adapter.generate_single_image(**gen_kwargs)
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
            await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene)
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
    lf_path = shot_dir / "last_frame.png"
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

    # 计算当前哈希值（核心意图 + 空镜约束 + 参考图，任一变化都重新生成）
    # 追加 _nl 后缀：解除布局锁后 prompt 语义变化，强制旧缓存失效重新生成
    current_hash = _get_frame_content_hash(shot, empty_scene, reference_paths) + "_lf_nl"

    # v1.1：挂实际生成最终 prompt（QC 语义核对用），与首帧同理
    try:
        shot["lf_desc_final"] = _build_final_frame_prompt(shot, lf_desc, "last_frame", characters)
    except Exception:
        pass

    for attempt in range(max_retries + 1):
        if lf_path.exists() and not force and attempt == 0:
            # 检查哈希文件是否存在且匹配
            if lf_hash_path.exists():
                saved_hash = lf_hash_path.read_text(encoding="utf-8").strip()
                if saved_hash == current_hash and _validate_frame(str(lf_path)):
                    await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene)
                    return str(lf_path)
                else:
                    logger.verbose(f"  [FRAME] 尾帧 shot_{idx} 哈希不匹配或缓存无效，重新生成")
            elif script_mtime is not None and lf_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(lf_path)):
                    await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene)
                    return str(lf_path)
            elif script_mtime is None and _validate_frame(str(lf_path)):
                await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene)
                return str(lf_path)

        logger.verbose(f"  [FRAME] 生成尾帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        # 添加风格前缀 + 严格约束（与视频生成保持一致，确保风格统一）
        # 先预处理：替换 AI 可能误解的词汇
        lf_desc = preprocess_prompt_for_image(lf_desc)
        # v1.1：注入出镜角色服装特征（与首帧一致）
        lf_desc = _inject_character_costume(lf_desc, characters, shot)
        # v1.1：尾帧目光修复（错别字 + 看向动作结果对象——听香看被刺倒的杨旭）
        lf_desc = fix_lf_desc_gaze(lf_desc, shot, characters)
        # 场景物体尺寸锚定注入（船/车等物体比例防漂移）
        lf_desc = _inject_object_size_anchor(shot, lf_desc)
        # 尾帧注入场景上下文 + 显式"重构图"指令：
        # 参考图（首帧）只作人物/风格锚定，构图必须按 lf_desc 重新布局，
        # 否则参考图会把动作结果帧也锁死在首帧构图上，三帧雷同、动作无法推进。
        scene = shot.get("scene", "") or ""
        location = shot.get("location", "") or ""
        ctx = f"（场景：{location or scene}）" if (location or scene) else ""
        lf_desc_full = (
            f"{lf_desc}{ctx}"
            f"，构图需按尾帧描述重新布局，"
            f"可与首帧/参考图的景别、机位、人物位置不同，表现动作演变的最终状态。"
        )
        lf_desc_full = sanitize_prompt(ensure_prefix(lf_desc_full, STYLE_PREFIX), verbose=False)
        lf_desc_full = add_image_strict_constraints(lf_desc_full, "尾帧", shot=shot)
        refs = reference_paths if reference_paths else []
        try:
            img_out = await image_adapter.generate_single_image(
                prompt=lf_desc_full,
                reference_image_paths=refs,
                shot_index=idx,
                frame_desc="尾帧",
                # 尾帧：解除"场景布局不变"锁，允许构图随动作演变
                lock_prompt=IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT,
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
            await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene)
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

async def _gen_keyframes_async(
    image_adapter, shots_dir: Path, shot: dict, force: bool,
    script_mtime: float | None = None,
    reference_paths: list | None = None,
    max_retries: int = 1,
    empty_scene: bool = False,
) -> list[str]:
    """为镜头生成中间关键帧图片（多关键帧叙事模式）。
    
    从 shot['keyframes'] 数组读取关键帧描述，生成对应的图片。
    返回关键帧图片路径列表（不包括首帧和尾帧）。
    
    Args:
        keyframes: 如 [{"desc": "...", "time_ratio": 0.3}, ...]
    
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
    
    # 过滤掉 time_ratio=0（首帧）和 time_ratio=1（尾帧），只生成中间帧
    intermediate_kfs = [
        kf for kf in keyframes_data 
        if isinstance(kf, dict) and 0 < float(kf.get("time_ratio", 0)) < 1
    ]
    
    if not intermediate_kfs:
        return []
    
    kf_paths = []
    for i, kf in enumerate(intermediate_kfs):
        kf_desc = kf.get("desc", "").strip()
        if not kf_desc:
            continue
        
        time_ratio = float(kf.get("time_ratio", 0.5))
        kf_path = shot_dir / f"keyframe_{i}.png"
        kf_hash_path = shot_dir / f"keyframe_{i}_hash.txt"
        
        if empty_scene:
            kf_desc = kf_desc + "\n空镜头， 画面中严禁出现任何人物、身影、人影、轮廓或人形。只拍摄自然景物特写。"
        
        # 复用首帧哈希逻辑：基于 shot 内容派生，内容不变则路径语义不变
        # 追加 _nl 后缀：解除布局锁后 prompt 语义变化，强制旧缓存失效重新生成
        current_hash = _get_frame_content_hash(shot, empty_scene, reference_paths) + f"_kf_{i}_nl"
        
        for attempt in range(max_retries + 1):
            if kf_path.exists() and not force and attempt == 0:
                if kf_hash_path.exists():
                    saved_hash = kf_hash_path.read_text(encoding="utf-8").strip()
                    if saved_hash == current_hash and _validate_frame(str(kf_path)):
                        kf_paths.append(str(kf_path))
                        break
                elif script_mtime is not None and kf_path.stat().st_mtime >= script_mtime:
                    if _validate_frame(str(kf_path)):
                        kf_paths.append(str(kf_path))
                        break
                elif script_mtime is None and _validate_frame(str(kf_path)):
                    kf_paths.append(str(kf_path))
                    break
            
            _kf_logger.verbose(f"  [KEYFRAME] 生成 shot_{idx} keyframe_{i} (t={time_ratio:.1f})...")
            # 关键帧注入场景上下文 + 显式"重构图"指令：
            # 关键帧是动作进行中的瞬间状态，构图需与首帧不同（如"推至中景"），
            # 若沿用首帧整图参考 + 布局不变锁，会锁死在首帧构图上。
            scene = shot.get("scene", "") or ""
            location = shot.get("location", "") or ""
            ctx = f"（场景：{location or scene}）" if (location or scene) else ""
            kf_desc_full = (
                f"{kf_desc}{ctx}"
                f"，构图需按关键帧描述重新布局，"
                f"可与首帧/参考图的景别、机位、人物位置不同，表现动作过程中的瞬间状态。"
            )
            kf_desc_full = sanitize_prompt(ensure_prefix(kf_desc_full, STYLE_PREFIX), verbose=False)
            kf_desc_full = add_image_strict_constraints(kf_desc_full, "关键帧", shot=shot)
            refs = reference_paths if reference_paths else []

            try:
                img_out = await image_adapter.generate_single_image(
                    prompt=kf_desc_full,
                    reference_image_paths=refs,
                    shot_index=idx,
                    frame_desc=f"中间关键帧(t={time_ratio:.1f})",
                    # 关键帧：解除"场景布局不变"锁，允许构图随动作演变
                    lock_prompt=IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT,
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
                kf_hash_path.write_text(current_hash, encoding="utf-8")
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
) -> dict:
    """为单个镜头生成/检查首帧、尾帧和中间关键帧。

    用于 submit_poll.py 中 skip_frames=False 时的逐镜帧生成。
    characters: script['characters']，用于空镜检测（空镜首帧禁止人物）。
    skip_first_frame: 模式B时跳过首帧生成（用上一镜尾帧代替），仅生成尾帧用于本镜风格锚定。

    Returns:
        dict: {"ff_path": str | None, "lf_path": str | None, "keyframes_paths": list[str]}
    """
    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)

    ff_ = {}
    ff_path = shot_dir / "first_frame.png"
    empty_scene = is_empty_scene(shot, characters or [])

    # 首帧参考图：场景资产图 + 出镜角色肖像 + 出镜道具资产图（图生图锚定）。
    # 此前 reference_paths=None 纯文生图，复杂多人构图（钩子"刺客+杨旭+听香"三人物）
    # 被图片模型简化成单人，首帧人物/构图全错，且场景/道具与资产库不一致。
    # 空镜只注入场景资产图（禁止角色/道具肖像，防人物渗入）。
    #
    # 修改2（2026-08-07）：动作镜头首帧不注入场景资产图——场景图被硬约束"禁止
    # 人物"，与多角色参考图矛盾时场景图占上风、人物被压制（钩子首帧几乎无人）。
    # 尾帧/关键帧仍保留场景参考图（环境锚定，NO_LAYOUT 锁下无碍）。
    frame_refs_ff = _collect_frame_refs_for_shot(shot, characters, limit=5, for_frame="首帧")
    frame_refs_full = _collect_frame_refs_for_shot(shot, characters, limit=5, for_frame="尾帧")

    if skip_first_frame:
        # 模式B：跳过首帧生成，尾帧仍生成（用于本镜风格锚定）
        ff_path_str = None
    else:
        # 模式A：首帧 generation (使用 _gen_first_frame_async 的缓存逻辑)
        ff_path_str = await _gen_first_frame_async(
            image_adapter, shots_dir, shot,
            force=force, script_mtime=script_mtime,
            reference_paths=frame_refs_ff or None, max_retries=1,
            empty_scene=empty_scene, characters=characters,
        )
    ff_["ff_path"] = ff_path_str

    # 尾帧 generation（全镜头覆盖：首尾双关键帧钳制风格漂移，
    # 不再按 variation_type 过滤——LLM 常输出空值导致全部镜头无尾帧）
    # 尾帧参考图：场景/角色/道具资产（锚定人物形象与环境一致）。
    # 不再把首帧整图作为尾帧参考图——首帧是动作起点，直接抄首帧构图会让
    # 尾帧几乎复刻首帧（刺客同位置、听香位置错乱、两帧雷同），
    # 动作演变只能靠 lf_desc 文字描述驱动。首帧只通过 frame_refs_full 的
    # 场景资产图隐式锚定环境/光线，不用整图锁死尾帧构图。
    lf_refs = frame_refs_full or None
    lf_path_str = await _gen_last_frame_async(
        image_adapter, shots_dir, shot,
        force=force, script_mtime=script_mtime,
        reference_paths=lf_refs,
        max_retries=1,
        empty_scene=empty_scene,
        characters=characters,
    )
    ff_["lf_path"] = lf_path_str

    # 中间关键帧 generation（多关键帧叙事模式）
    # 模式B时跳过中间关键帧生成（已用尾帧接续替代关键帧锚定）
    if skip_first_frame:
        kf_paths = []
    else:
        # 关键帧参考图：场景/角色/道具资产（锚定一致），不用首帧整图锁死构图
        # （与尾帧同理——首帧整图会把关键帧的中间动作态锁死在起点构图上）。
        kf_refs = frame_refs_full or None
        kf_paths = await _gen_keyframes_async(
            image_adapter, shots_dir, shot,
            force=force, script_mtime=script_mtime,
            reference_paths=kf_refs,
            max_retries=1,
            empty_scene=empty_scene,
        )
    ff_["keyframes_paths"] = kf_paths

    return ff_
