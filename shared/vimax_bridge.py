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
from collections import OrderedDict
from pathlib import Path
from typing import List

from vimax.interfaces import (
    Camera,
    CharacterInScene,
    ShotBriefDescription,
    ShotDescription,
)

from character_bank import CharacterBank, StyleBank

import hashlib

# 静默日志: 桥接日志写入 step02.log，不污染控制台
def _bridge_log(msg: str):
    try:
        from shared.tee_logger import get_logger
        from shared.paths import chapter_dir
        log = get_logger(chapter_dir() / "step02.log", console_level="INFO")
        log.verbose(msg)
    except Exception:
        _bridge_log(msg)

# 首帧/尾帧生成需要风格前缀（与视频生成保持一致）
from style_prefix import STYLE_PREFIX, sanitize_prompt, ensure_prefix, is_empty_scene


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

    # 非首镜头：引用本摄像机首帧作为锚定
    if not is_first_in_cam and cam_idx in camera_first_frames:
        anchor = camera_first_frames[cam_idx]
        if anchor and Path(anchor).exists():
            refs.append(anchor)
            _bridge_log(f"  [bridge] 镜头{shot['index']}: 参考图=摄像机{cam_idx}首帧锚定")

    # 角色正面肖像
    char_ref = _select_best_reference(shot, bank, style_bank, characters_data)
    if char_ref and char_ref not in refs:
        refs.append(char_ref)

    # 只在 prompt 明确提到角色时才传参考图（不回退到风格锚点）

    return refs[:1]  # 当前 provider 只支持单参考图，取最优第一个


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

        # 正面肖像（英文 prompt，不拼风格前缀，横屏白底）
        front_path = portrait_dir / "front.png"
        if not front_path.exists():
            _bridge_log(f"  [bank] 生成 {identifier} 正面肖像 ...")
            try:
                img_out = await portrait_gen.generate_front_portrait(
                    # 只传角色特征作为 style，不加 STYLE_PREFIX（portrait_adapter 已 skip_prefix）
                    char_in_scene, style="photorealistic Chinese ancient costume character portrait, full-body, white background, 16:9 wide canvas, realistic skin texture, natural lighting, film photography",
                )
                # 保存：ImageOutput 是 PIL Image
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(front_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(front_path))
                else:
                    # 如果返回的是路径
                    _bridge_log(f"  [bank] {identifier} 正面肖像: 未知返回类型 {type(img_out)}")
                    continue
                _bridge_log(f"  [bank] {identifier} 正面肖像 -> {front_path}")
            except Exception as e:
                _bridge_log(f"  [bank] {identifier} 正面肖像生成失败: {e}")
                continue

        # 侧面肖像
        side_path = portrait_dir / "side.png"
        if not side_path.exists() and front_path.exists():
            _bridge_log(f"  [bank] 生成 {identifier} 侧面肖像 ...")
            try:
                img_out = await portrait_gen.generate_side_portrait(
                    char_in_scene, front_image_path=str(front_path),
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(side_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(side_path))
                _bridge_log(f"  [bank] {identifier} 侧面肖像 -> {side_path}")
            except Exception as e:
                _bridge_log(f"  [bank] {identifier} 侧面肖像生成失败: {e}")

        # 背面肖像
        back_path = portrait_dir / "back.png"
        if not back_path.exists() and front_path.exists():
            _bridge_log(f"  [bank] 生成 {identifier} 背面肖像 ...")
            try:
                img_out = await portrait_gen.generate_back_portrait(
                    char_in_scene, front_image_path=str(front_path),
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(back_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(back_path))
                _bridge_log(f"  [bank] {identifier} 背面肖像 -> {back_path}")
            except Exception as e:
                _bridge_log(f"  [bank] {identifier} 背面肖像生成失败: {e}")

        # 构建 portraits dict
        portraits = {}
        if front_path.exists():
            portraits["front"] = {
                "path": str(front_path.resolve()),
                "description": f"{identifier} 正面全身肖像",
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
                # 按优先级尝试：检测到的视角 → front → side → back
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
        shot.get("scene", ""),              # 场景地点
    ]
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


async def _gen_first_frame_async(
    image_adapter, shots_dir: Path, shot: dict, force: bool,
    script_mtime: float | None = None,
    reference_paths: list | None = None,
    max_retries: int = 1,
    empty_scene: bool = False,
):
    """为镜头生成 first_frame.png，prompt 用 ff_desc.

    生成后自动校验文件大小；不达标时自动重试（最多 max_retries 次）.
    empty_scene=True 时在 prompt 中显式禁止人物（与视频 prompt 的空镜约束
    保持一致，防止首帧凭空长出人物后被 image-to-video 固化成片头画面）.
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
        # ff_Desc 缺失时， 从 scene/ narration 构建静态首帧
        scene = shot.get("scene", "")
        narration = shot.get("narration_segment", "")
        main_subj = shot.get("main_subject", "")
        ff_desc = f"{scene}，{main_subj}，静态画面。"
        if narration:
            ff_desc += f" {narration[:50]}。" if len(narration) > 50 else f" {narration}。"
    else:
        # ff_Desc 存在时，检查是否是纯相机运动描述（无静态画面）
        motion_keywords = ["上移", "下移", "左移", "右移", "推进", "拉远", "跃起", "冲出",
                          "穿越", "飞", "跑", "快速", "缓慢", "跟随", "追"]
        is_pure_motion = all(kw in ff_desc for kw in motion_keywords) if motion_keywords else False
        if is_pure_motion or len(ff_desc) < 20:
            # ff_Desc 几乎是纯相机运动，补充静态画面
            scene = shot.get("scene", "")
            main_subj = shot.get("main_subject", "")
            ff_desc = f"{scene}，{main_subj}，静态画面。{ff_desc}"
    if empty_scene:
        ff_desc = f"{ff_desc}\n空镜头，画面中严禁出现任何人物、身影、人影、轮廓或人形。只拍摄自然景物特写。"

    # 计算当前哈希值（核心意图 + 空镜约束 + 参考图，任一变化都重新生成）
    current_hash = _get_frame_content_hash(shot, empty_scene, reference_paths)

    for attempt in range(max_retries + 1):
        if ff_path.exists() and not force and attempt == 0:
            # 检查哈希文件是否存在且匹配
            if ff_hash_path.exists():
                saved_hash = ff_hash_path.read_text(encoding="utf-8").strip()
                if saved_hash == current_hash and _validate_frame(str(ff_path)):
                    return str(ff_path)
                else:
                    logger.verbose(f"  [FRAME] 首帧 shot_{idx} 哈希不匹配或缓存无效，重新生成")
            elif script_mtime is not None and ff_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(ff_path)):
                    return str(ff_path)
            elif script_mtime is None and _validate_frame(str(ff_path)):
                return str(ff_path)

        logger.verbose(f"  [FRAME] 生成首帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        # 添加风格前缀（与视频生成保持一致，确保风格统一）
        ff_desc_full = sanitize_prompt(ensure_prefix(ff_desc, STYLE_PREFIX), verbose=False)
        refs = reference_paths if reference_paths else []
        try:
            img_out = await image_adapter.generate_single_image(
                prompt=ff_desc_full,
                reference_image_paths=refs,
                shot_index=idx,
                frame_desc="首帧",
            )
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
    current_hash = _get_frame_content_hash(shot, empty_scene, reference_paths)

    for attempt in range(max_retries + 1):
        if lf_path.exists() and not force and attempt == 0:
            # 检查哈希文件是否存在且匹配
            if lf_hash_path.exists():
                saved_hash = lf_hash_path.read_text(encoding="utf-8").strip()
                if saved_hash == current_hash and _validate_frame(str(lf_path)):
                    return str(lf_path)
                else:
                    logger.verbose(f"  [FRAME] 尾帧 shot_{idx} 哈希不匹配或缓存无效，重新生成")
            elif script_mtime is not None and lf_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(lf_path)):
                    return str(lf_path)
            elif script_mtime is None and _validate_frame(str(lf_path)):
                return str(lf_path)

        logger.verbose(f"  [FRAME] 生成尾帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        # 添加风格前缀（与视频生成保持一致，确保持风格统一）
        lf_desc_full = sanitize_prompt(ensure_prefix(lf_desc, STYLE_PREFIX), verbose=False)
        refs = reference_paths if reference_paths else []
        try:
            img_out = await image_adapter.generate_single_image(
                prompt=lf_desc_full,
                reference_image_paths=refs,
                shot_index=idx,
                frame_desc="尾帧",
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
        current_hash = _get_frame_content_hash(shot, empty_scene, reference_paths) + f"_kf_{i}"
        
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
            kf_desc_full = sanitize_prompt(ensure_prefix(kf_desc, STYLE_PREFIX), verbose=False)
            refs = reference_paths if reference_paths else []
            
            try:
                img_out = await image_adapter.generate_single_image(
                    prompt=kf_desc_full,
                    reference_image_paths=refs,
                    shot_index=idx,
                    frame_desc=f"中间关键帧(t={time_ratio:.1f})",
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

    if skip_first_frame:
        # 模式B：跳过首帧生成，尾帧仍生成（用于本镜风格锚定）
        ff_path_str = None
    else:
        # 模式A：首帧 generation (使用 _gen_first_frame_async 的缓存逻辑)
        ff_path_str = await _gen_first_frame_async(
            image_adapter, shots_dir, shot,
            force=force, script_mtime=script_mtime,
            reference_paths=None, max_retries=1,
            empty_scene=empty_scene,
        )
    ff_["ff_path"] = ff_path_str

    # 尾帧 generation（全镜头覆盖：首尾双关键帧钳制风格漂移，
    # 不再按 variation_type 过滤——LLM 常输出空值导致全部镜头无尾帧）
    # 模式B时尾帧参考图回退到空（首帧已跳过）
    lf_refs = [ff_path_str] if ff_path_str else None
    lf_path_str = await _gen_last_frame_async(
        image_adapter, shots_dir, shot,
        force=force, script_mtime=script_mtime,
        reference_paths=lf_refs,
        max_retries=1,
        empty_scene=empty_scene,
    )
    ff_["lf_path"] = lf_path_str

    # 中间关键帧 generation（多关键帧叙事模式）
    # 模式B时跳过中间关键帧生成（已用尾帧接续替代关键帧锚定）
    if skip_first_frame:
        kf_paths = []
    else:
        kf_refs = [ff_path_str] if ff_path_str else None
        kf_paths = await _gen_keyframes_async(
            image_adapter, shots_dir, shot,
            force=force, script_mtime=script_mtime,
            reference_paths=kf_refs,
            max_retries=1,
            empty_scene=empty_scene,
        )
    ff_["keyframes_paths"] = kf_paths

    return ff_
