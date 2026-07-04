"""script_result.json → ViMax working_dir 翻译器.

把 aiVideo 01_script_gen 产出的分镜（narration + shots + characters）翻译成 ViMax 的
working_dir 缓存产物，让 Script2VideoPipeline 命中缓存、跳过全部 LLM 规划步骤
（extract_characters / design_storyboard / decompose / construct_camera_tree /
generate_frames_for_single_camera），只跑 generate_video_for_single_shot。

v2: 跨章节角色与风格一致性
  - 从 script_result.json 的 characters 字段填充 characters.json（不再写 []）
  - 用 CharacterPortraitsGenerator 为新角色生成前/侧/背面肖像
  - 肖像存入 output/characters_bank/ 跨章持久化
  - Ch1 锁定风格锚点到 output/style_bank/style_anchor.png
  - 后续章节用风格锚点 + 角色肖像作为 first_frame 参考图
"""

import asyncio
import json
import os
from pathlib import Path
from typing import List

from vimax.interfaces import (
    Camera,
    CharacterInScene,
    ShotBriefDescription,
    ShotDescription,
)

from character_bank import CharacterBank, StyleBank


async def build_working_dir(
    script_path: Path,
    working_dir: Path,
    image_adapter,
    force_first_frame: bool = False,
):
    """读 script_result.json，写 ViMax working_dir 缓存产物 + 预生成 first_frame.

    force_first_frame=True 时即使 first_frame.png 存在也重新生成（prompt 变更后用）.
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
        # 向后兼容：无 characters 字段时写空
        if not chars_path.exists():
            with open(chars_path, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

    # ---- 2. character_portraits_registry.json ----
    registry_path = working_dir / "character_portraits_registry.json"
    if registry and (not registry_path.exists() or force_first_frame):
        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
        print(f"  [bridge] character_portraits_registry.json: {len(registry)} 个角色")

    # ---- 3. storyboard.json ----
    storyboard_path = working_dir / "storyboard.json"
    storyboard = [
        ShotBriefDescription(
            idx=shot["index"],
            is_last=(i == n),
            cam_idx=1,
            visual_desc=shot.get("prompt", ""),
            audio_desc=shot.get("subtitle", ""),
        )
        for i, shot in enumerate(shots, 1)
    ]
    _dump_json(storyboard_path, storyboard)

    # ---- 4. camera_tree.json ----
    camera_tree_path = working_dir / "camera_tree.json"
    camera_tree = [
        Camera(
            idx=1,
            active_shot_idxs=[shot["index"] for shot in shots],
            parent_cam_idx=None,
            parent_shot_idx=None,
        )
    ]
    _dump_json(camera_tree_path, camera_tree)

    # ---- 5. shot_description.json + 找可见角色 ----
    def _find_visible_char_idxs(shot_prompt: str, chars: list[dict]) -> list[int]:
        """在 shot.prompt 中匹配角色名，返回可见角色 idx 列表."""
        idxs = []
        for i, char in enumerate(chars):
            ident = char.get("identifier", "")
            if ident and ident in shot_prompt:
                idxs.append(i)
        return idxs

    for shot in shots:
        idx = shot["index"]
        sd = ShotDescription(
            idx=idx,
            is_last=(idx == shots[-1]["index"]),
            cam_idx=1,
            visual_desc=shot.get("prompt", ""),
            variation_type="small",
            variation_reason="mvp single frame",
            ff_desc=shot.get("prompt", ""),
            ff_vis_char_idxs=_find_visible_char_idxs(shot.get("prompt", ""), characters_data),
            lf_desc="",
            lf_vis_char_idxs=[],
            motion_desc=shot.get("prompt", ""),
            audio_desc=shot.get("subtitle", ""),
        )
        _dump_json(shots_dir / str(idx) / "shot_description.json", sd)
        (shots_dir / str(idx)).mkdir(parents=True, exist_ok=True)

    # ---- 6. 首帧生成（带角色/风格参考图）----
    script_mtime = script_path.stat().st_mtime if script_path.exists() else None

    # Shot 1 先生成锚点首帧
    anchor_frame = None
    if shots:
        ref_for_shot1 = _select_best_reference(
            shots[0], bank, style_bank, characters_data,
        )
        anchor_frame = await _gen_first_frame_async(
            image_adapter, shots_dir, shots[0], force_first_frame, script_mtime,
            reference_paths=[ref_for_shot1] if ref_for_shot1 else [],
        )

    # 风格锚点：Ch1 首次生成后锁定
    if chapter_idx == 1 and not style_bank.is_locked() and anchor_frame:
        style_bank.lock(anchor_frame, chapter_idx)
        style_bank.save()

    # Shot 2-6：用角色参考图或风格锚点
    tasks = []
    for shot in shots[1:]:
        ref = _select_best_reference(shot, bank, style_bank, characters_data)
        refs = []
        if anchor_frame:
            refs.append(anchor_frame)
        if ref and ref not in refs:
            refs.append(ref)
        # 当前 ImageGeneratorAiVideoAdapter 只用 [0]，把最优参考放前面
        tasks.append(_gen_first_frame_async(
            image_adapter, shots_dir, shot, force_first_frame, script_mtime,
            reference_paths=refs[:1],
        ))
    results = await asyncio.gather(*tasks) if tasks else []
    print(f"[bridge] working_dir 就绪: {working_dir} ({n} shots)")


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
# 参考图选择
# ---------------------------------------------------------------------------

def _select_best_reference(
    shot: dict,
    bank: CharacterBank,
    style_bank: StyleBank,
    characters_data: list[dict],
) -> str | None:
    """为镜头选择最优单张参考图.

    优先级:
      1. shot.prompt 中提到某角色 + 有正面肖像 → 用角色正面肖像
      2. 风格锚点（Ch2+ 或 Ch1 已锁定）
      3. None（text-to-image）
    """
    prompt = shot.get("prompt", "")

    # 检查角色名是否出现在 prompt 中
    for char in characters_data:
        identifier = char.get("identifier", "")
        if identifier and identifier in prompt:
            entry = bank.get_character(identifier)
            if entry and entry.get("portraits"):
                front = entry["portraits"].get("front", {}).get("path")
                if front and Path(front).exists():
                    print(f"  [bridge] 镜头{shot['index']}: 参考图=角色 {identifier} 正面肖像")
                    return front

    # 回退：风格锚点
    anchor = style_bank.get_anchor_path()
    if anchor and Path(anchor).exists():
        print(f"  [bridge] 镜头{shot['index']}: 参考图=风格锚点")
        return anchor

    return None


# ---------------------------------------------------------------------------
# 首帧生成（同原版）
# ---------------------------------------------------------------------------

async def _gen_first_frame_async(
    image_adapter, shots_dir: Path, shot: dict, force: bool,
    script_mtime: float | None = None,
    reference_paths: list | None = None,
):
    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    ff_path = shot_dir / "first_frame.png"
    if ff_path.exists() and not force:
        if script_mtime is not None and ff_path.stat().st_mtime >= script_mtime:
            return str(ff_path)
        elif script_mtime is None:
            return str(ff_path)
        else:
            print(f"  [bridge] 首帧 shot_{idx} 早于 script_result.json，重新生成")
    print(f"  [bridge] 生成首帧 shot_{idx} ...")
    refs = reference_paths if reference_paths else []
    img_out = await image_adapter.generate_single_image(
        prompt=shot.get("prompt", ""),
        reference_image_paths=refs,
    )
    if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
        img_out.data.save(str(ff_path))
    elif hasattr(img_out, "save"):
        img_out.save(str(ff_path))
    return str(ff_path)


def _dump_json(path: Path, model) -> None:
    """写 pydantic 模型或模型列表为 JSON（ViMax 用 model_validate_json 读回）."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(model, list):
        data = [m.model_dump() for m in model]
    else:
        data = model.model_dump()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
