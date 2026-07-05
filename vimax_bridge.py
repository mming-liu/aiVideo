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

    print(f"  [bridge] camera_tree: {len(cameras)} 台摄像机 "
          f"{[c.idx for c in cameras]}")
    for c in cameras:
        if c.parent_cam_idx:
            print(f"    cam_{c.idx} -> parent=cam_{c.parent_cam_idx}"
                  f" 镜头{c.parent_shot_idx}")
    return cameras


async def build_working_dir(
    script_path: Path,
    working_dir: Path,
    image_adapter,
    force_first_frame: bool = False,
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
            print(f"  [bridge] characters.json: {len(char_objects)} 个角色")
    elif not characters_data:
        if not chars_path.exists():
            with open(chars_path, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

    # ---- 2. character_portraits_registry.json ----
    registry_path = working_dir / "character_portraits_registry.json"
    if registry and (not registry_path.exists() or force_first_frame):
        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
        print(f"  [bridge] character_portraits_registry.json: {len(registry)} 个角色")

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

    # ---- 6. 首帧 + 尾帧生成（摄像机首帧锚定 / 摄像机组间并行）----
    script_mtime = script_path.stat().st_mtime if script_path.exists() else None

    # 摄像机首帧锚定：同一摄像机内，镜头 N 的 first_frame 引用该摄像机第一个镜头的 first_frame
    camera_first_frames: dict[int, str] = {}  # {cam_idx: first_frame_path}

    # 按摄像机分组
    cam_groups: dict[int, list[dict]] = OrderedDict()
    for shot in shots:
        cam = shot.get("cam_idx", 1)
        cam_groups.setdefault(cam, []).append(shot)

    # 风格锚点：只在 Ch1 首次锁定
    style_anchor_locked = False

    async def _process_camera(cam_idx: int, cam_shots: list[dict]):
        """单个摄像机内串行处理所有镜头的首帧 + 尾帧.

        每个摄像机独立创建 adapter 实例，确保多摄像机间真正并行（无共享锁）.
        """
        cam_adapter = type(image_adapter)(
            size=image_adapter.size,
            negative_prompt=image_adapter.negative_prompt,
            skip_prefix=image_adapter._skip_prefix,
            skip_sanitize=image_adapter._skip_sanitize,
        )
        nonlocal style_anchor_locked
        for i, shot in enumerate(cam_shots):
            is_first_in_cam = (i == 0)
            idx = shot["index"]
            shot_dir = shots_dir / str(idx)

            # -- 首帧生成 --
            ff_path = shot_dir / "first_frame.png"
            refs = _build_reference_list(
                shot, bank, style_bank, characters_data, camera_first_frames,
                cam_idx=cam_idx, is_first_in_cam=is_first_in_cam,
            )
            ff_result = await _gen_first_frame_async(
                cam_adapter, shots_dir, shot, force_first_frame, script_mtime,
                reference_paths=refs,
            )
            if is_first_in_cam and ff_result:
                camera_first_frames[cam_idx] = ff_result
                # 风格锚点锁定（全局首帧 = 首个摄像机的首个镜头首帧）
                if not style_anchor_locked and chapter_idx == 1 \
                        and not style_bank.is_locked() and cam_idx == min(cam_groups.keys()):
                    style_bank.lock(ff_result, chapter_idx)
                    style_bank.save()
                    style_anchor_locked = True

            # -- 尾帧生成（medium/large 变幅）--
            vt = shot.get("variation_type", "small").strip() or "small"
            if vt in ("medium", "large"):
                lf_desc = shot.get("lf_desc", "").strip()
                if lf_desc:
                    lf_refs = [str(ff_path)] if ff_path and ff_path.exists() else []
                    # 追加角色参考（lf_desc 中提到的角色）
                    char_ref = _select_best_reference(shot, bank, style_bank, characters_data)
                    if char_ref and char_ref not in lf_refs:
                        lf_refs.append(char_ref)
                    await _gen_last_frame_async(
                        cam_adapter, shots_dir, shot, force_first_frame, script_mtime,
                        reference_paths=lf_refs,
                    )
                else:
                    print(f"  [bridge] 镜头{idx}: variation_type={vt} 但 lf_desc 为空，跳尾帧")

    # 摄像机组间并行执行（组内保持串行，保证锚定顺序）
    await asyncio.gather(*[
        _process_camera(cam_idx, cam_shots)
        for cam_idx, cam_shots in cam_groups.items()
    ])

    print(f"[bridge] working_dir 就绪: {working_dir} ({n} shots, {len(cam_groups)} 摄像机)")


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
    """
    refs: list[str] = []

    # 非首镜头：引用本摄像机首帧作为锚定
    if not is_first_in_cam and cam_idx in camera_first_frames:
        anchor = camera_first_frames[cam_idx]
        if anchor and Path(anchor).exists():
            refs.append(anchor)
            print(f"  [bridge] 镜头{shot['index']}: 参考图=摄像机{cam_idx}首帧锚定")

    # 角色正面肖像
    char_ref = _select_best_reference(shot, bank, style_bank, characters_data)
    if char_ref and char_ref not in refs:
        refs.append(char_ref)

    # 风格锚点（最后回退）
    if not refs:
        anchor = style_bank.get_anchor_path()
        if anchor and Path(anchor).exists():
            refs.append(anchor)
            print(f"  [bridge] 镜头{shot['index']}: 参考图=风格锚点")

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
    from vimax_adapters import ImageGeneratorAiVideoAdapter

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

        # 已有肖像 → 直接复用
        if bank.has_character(identifier):
            entry = bank.get_character(identifier)
            if entry and entry.get("portraits"):
                portraits = entry["portraits"]
                registry[identifier] = portraits
                print(f"  [bank] 复用角色 {identifier} 的已有肖像")
                bank.update_character_chapter(identifier, chapter_idx)
                bank.update_dynamic_features(identifier, char.get("dynamic_features", ""))
                continue

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
            print(f"  [bank] 生成 {identifier} 正面肖像 ...")
            try:
                img_out = await portrait_gen.generate_front_portrait(
                    # 只传角色特征作为 style，不加 STYLE_PREFIX（portrait_adapter 已 skip_prefix）
                    char_in_scene, style="2D anime character portrait, full-body, white background, 16:9 wide canvas",
                )
                # 保存：ImageOutput 是 PIL Image
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(front_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(front_path))
                else:
                    # 如果返回的是路径
                    print(f"  [bank] {identifier} 正面肖像: 未知返回类型 {type(img_out)}")
                    continue
                print(f"  [bank] {identifier} 正面肖像 -> {front_path}")
            except Exception as e:
                print(f"  [bank] {identifier} 正面肖像生成失败: {e}")
                continue

        # 侧面肖像
        side_path = portrait_dir / "side.png"
        if not side_path.exists() and front_path.exists():
            print(f"  [bank] 生成 {identifier} 侧面肖像 ...")
            try:
                img_out = await portrait_gen.generate_side_portrait(
                    char_in_scene, front_image_path=str(front_path),
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(side_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(side_path))
                print(f"  [bank] {identifier} 侧面肖像 -> {side_path}")
            except Exception as e:
                print(f"  [bank] {identifier} 侧面肖像生成失败: {e}")

        # 背面肖像
        back_path = portrait_dir / "back.png"
        if not back_path.exists() and front_path.exists():
            print(f"  [bank] 生成 {identifier} 背面肖像 ...")
            try:
                img_out = await portrait_gen.generate_back_portrait(
                    char_in_scene, front_image_path=str(front_path),
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(back_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(back_path))
                print(f"  [bank] {identifier} 背面肖像 -> {back_path}")
            except Exception as e:
                print(f"  [bank] {identifier} 背面肖像生成失败: {e}")

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
    1. 同时搜索 ff_desc、prompt、context_bridge 三个字段（不是只搜 prompt）
    2. 根据描述中的视角词（侧身/背面/正面）选择最合适的角色肖像视图
    3. 视角不存在时按 front → side → back 降级

    优先级:
    1. ff_desc/prompt/context_bridge 中提到某角色 + 有匹配视角的肖像 → 用该视角
    2. 风格锚点（Ch2+ 或 Ch1 已锁定）
    3. None（text-to-image）
    """
    ff_desc = shot.get("ff_desc", "").strip() or ""
    context = shot.get("context_bridge", "").strip() or ""
    prompt = shot.get("prompt", "")
    search_text = f"{ff_desc} {context} {prompt}"

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
                        print(f"  [bridge] 镜头{shot['index']}: 参考图=角色 {identifier} {view}肖像{view_tag}")
                        return p
                # 极端情况：portraits dict 里没有任何视图
                break

    # 回退：风格锚点
    anchor = style_bank.get_anchor_path()
    if anchor and Path(anchor).exists():
        print(f"  [bridge] 镜头{shot['index']}: 参考图=风格锚点")
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
        print(f"  [bridge] 帧图片过小: {p} ({size_kb:.1f}KB < {min_kb}KB)，判定无效")
        return False
    return True


async def _gen_first_frame_async(
    image_adapter, shots_dir: Path, shot: dict, force: bool,
    script_mtime: float | None = None,
    reference_paths: list | None = None,
    max_retries: int = 1,
):
    """为镜头生成 first_frame.png，prompt 用 ff_desc.

    生成后自动校验文件大小；不达标时自动重试（最多 max_retries 次）.
    """
    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    ff_path = shot_dir / "first_frame.png"
    ff_desc = shot.get("ff_desc", "").strip() or shot.get("prompt", "")

    for attempt in range(max_retries + 1):
        if ff_path.exists() and not force and attempt == 0:
            if script_mtime is not None and ff_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(ff_path)):
                    return str(ff_path)
                else:
                    print(f"  [FRAME] 首帧 shot_{idx} 缓存无效，重新生成")
            elif script_mtime is None and _validate_frame(str(ff_path)):
                return str(ff_path)
            elif script_mtime is not None:
                print(f"  [FRAME] 首帧 shot_{idx} 早于 script_result.json，重新生成")

        print(f"  [FRAME] 生成首帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        refs = reference_paths if reference_paths else []
        try:
            img_out = await image_adapter.generate_single_image(
                prompt=ff_desc,
                reference_image_paths=refs,
                shot_index=idx,
            )
            if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                img_out.data.save(str(ff_path))
            elif hasattr(img_out, "save"):
                img_out.save(str(ff_path))
        except Exception as e:
            print(f"  [FRAME] 首帧 shot_{idx} 生成异常: {e}")
            if attempt < max_retries:
                continue
            return None

        if _validate_frame(str(ff_path)):
            return str(ff_path)
        if attempt < max_retries:
            print(f"  [FRAME] 首帧 shot_{idx} 第{attempt+1}次不达标，重试...")

    return str(ff_path) if ff_path.exists() else None


async def _gen_last_frame_async(
    image_adapter, shots_dir: Path, shot: dict, force: bool,
    script_mtime: float | None = None,
    reference_paths: list | None = None,
    max_retries: int = 1,
):
    """为 medium/large 变幅镜头生成 last_frame.png，prompt 用 lf_desc.

    生成后自动校验文件大小；不达标时自动重试.
    """
    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    lf_path = shot_dir / "last_frame.png"
    lf_desc = shot.get("lf_desc", "").strip()
    if not lf_desc:
        print(f"  [FRAME] 尾帧 shot_{idx}: lf_desc 为空，跳过")
        return None

    for attempt in range(max_retries + 1):
        if lf_path.exists() and not force and attempt == 0:
            if script_mtime is not None and lf_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(lf_path)):
                    return str(lf_path)
                else:
                    print(f"  [FRAME] 尾帧 shot_{idx} 缓存无效，重新生成")
            elif script_mtime is None and _validate_frame(str(lf_path)):
                return str(lf_path)
            elif script_mtime is not None:
                print(f"  [FRAME] 尾帧 shot_{idx} 早于 script_result.json，重新生成")

        print(f"  [FRAME] 生成尾帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        refs = reference_paths if reference_paths else []
        try:
            img_out = await image_adapter.generate_single_image(
                prompt=lf_desc,
                reference_image_paths=refs,
            )
            if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                img_out.data.save(str(lf_path))
            elif hasattr(img_out, "save"):
                img_out.save(str(lf_path))
        except Exception as e:
            print(f"  [FRAME] 尾帧 shot_{idx} 生成异常: {e}")
            if attempt < max_retries:
                continue
            return None

        if _validate_frame(str(lf_path)):
            return str(lf_path)
        if attempt < max_retries:
            print(f"  [FRAME] 尾帧 shot_{idx} 第{attempt+1}次不达标，重试...")

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
