"""step02a_frame_gen.asset_gen - 场景/道具资产图生成编排.

在帧生成之前调用：从 SceneAssetsBank / PropAssetsBank 读取已提取的资产描述，
为缺失资产图的条目生成大场景图 + 近景功能场景图 + 道具资产图，并回写 bank。

缓存策略（镜像角色肖像 _generate_missing_portraits）：
- 已有资产图且文件存在 → 直接复用，只更新 last_chapter
- 大场景图 wide.png 生成失败 → 跳过该场景（近景依赖大场景，一起跳过）
- near_desc 缺失 → 只生成大场景图（近景跳过）
- 资产图由 bank 权威存储，asset_gen 只负责生成 + 回写

资产图是横屏参考资产（供 r2v 场景/道具一致性参考），与竖屏视频帧不同：
专用横屏适配器 + 跳过风格前缀（资产图不做 9:16 竖屏约束）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from shared.assets_bank import SceneAssetsBank, PropAssetsBank
from shared.config import (
    ASSET_GEN_ENABLED, SCENE_NEAR_SHOT_ENABLED,
)


def _save_image(img_out, path: Path) -> bool:
    """保存 ImageOutput / PIL 图片到 path，返回是否成功."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
            img_out.data.save(str(path))
        elif hasattr(img_out, "save"):
            img_out.save(str(path))
        else:
            print(f"[ASSET] 未知返回类型 {type(img_out)}，保存失败")
            return False
    except Exception as e:
        print(f"[ASSET] 保存失败 {path}: {e}")
        return False
    if not path.exists() or path.stat().st_size < 5 * 1024:
        print(f"[ASSET] 资产图过小或缺失: {path}")
        return False
    return True


async def gen_scene_assets(image_adapter, force: bool = False) -> int:
    """为场景资产库中缺图的场景生成大场景图 + 近景功能场景图.

    Args:
        script: script_result.json（含 scene_assets 描述，但 bank 才是权威源）
        image_adapter: 图片生成适配器（横屏专用，见调用方）
        force: 忽略缓存强制重生成

    Returns:
        int: 成功生成/复用的场景资产数
    """
    if not ASSET_GEN_ENABLED:
        return 0

    from shared.paths import chapter_dir
    from shared.tee_logger import get_logger
    logger = get_logger(chapter_dir() / "step02a.log", console_level="INFO")

    bank = SceneAssetsBank()
    bank.load()

    from vimax.agents.scene_assets_generator import SceneAssetsGenerator, scene_assets_from_bank
    gen = SceneAssetsGenerator(image_generator=image_adapter)

    total = 0
    for ident in bank.get_all_identifiers():
        entry = bank.get_scene(ident)
        if not entry:
            continue
        scene = scene_assets_from_bank(entry)
        scene_dir = bank.get_asset_dir(ident)
        scene_dir.mkdir(parents=True, exist_ok=True)
        wide_path = scene_dir / "wide.png"

        if not force and wide_path.exists() and wide_path.stat().st_size > 5 * 1024:
            # 已有大场景图：复用（无需重生成）
            bank.set_assets(ident, {
                "wide": {"path": str(wide_path.resolve()),
                         "description": f"{ident} 大场景建立镜头参考图"},
            })
            total += 1
            logger.info(f"[ASSET] 场景 {ident}: 大场景图已存在，复用")
            continue

        # 生成大场景建立镜头图
        logger.info(f"[ASSET] 生成场景 {ident} 大场景建立镜头图 ...")
        try:
            img_out = await gen.generate_wide(scene)
            if not _save_image(img_out, wide_path):
                logger.warn(f"[ASSET] 场景 {ident} 大场景图生成失败，跳过该场景")
                continue
            logger.info(f"[ASSET] 场景 {ident} 大场景图 -> {wide_path}")
        except Exception as e:
            logger.warn(f"[ASSET] 场景 {ident} 大场景图生成异常: {e}，跳过")
            continue

        assets = {
            "wide": {"path": str(wide_path.resolve()),
                     "description": f"{ident} 大场景建立镜头参考图"},
        }

        # 近景功能场景图（可选，从大场景派生）
        near_path = scene_dir / "near.png"
        if SCENE_NEAR_SHOT_ENABLED and (not near_path.exists() or force):
            try:
                img_out = await gen.generate_near(scene, str(wide_path))
                if img_out is not None and _save_image(img_out, near_path):
                    assets["near"] = {"path": str(near_path.resolve()),
                                      "description": f"{ident} 近景功能场景参考图"}
                    logger.info(f"[ASSET] 场景 {ident} 近景功能场景图 -> {near_path}")
            except Exception as e:
                logger.warn(f"[ASSET] 场景 {ident} 近景图生成异常: {e}（保留大场景图）")
        elif near_path.exists():
            assets["near"] = {"path": str(near_path.resolve()),
                              "description": f"{ident} 近景功能场景参考图"}

        bank.set_assets(ident, assets)
        bank.save()
        total += 1

    return total


async def gen_prop_assets(image_adapter, force: bool = False) -> int:
    """为道具资产库中缺图的道具生成白底资产图.

    Args:
        script: script_result.json（含 prop_assets 描述，但 bank 才是权威源）
        image_adapter: 图片生成适配器（横屏专用，见调用方）

    Returns:
        int: 成功生成/复用的道具资产数
    """
    if not ASSET_GEN_ENABLED:
        return 0

    from shared.paths import chapter_dir
    from shared.tee_logger import get_logger
    logger = get_logger(chapter_dir() / "step02a.log", console_level="INFO")

    bank = PropAssetsBank()
    bank.load()

    from vimax.agents.prop_assets_generator import PropAssetsGenerator, prop_from_bank
    gen = PropAssetsGenerator(image_generator=image_adapter)

    total = 0
    for ident in bank.get_all_identifiers():
        entry = bank.get_prop(ident)
        if not entry:
            continue
        prop = prop_from_bank(entry)
        prop_dir = bank.get_asset_dir(ident)
        prop_dir.mkdir(parents=True, exist_ok=True)
        prop_path = prop_dir / "prop.png"

        if not force and prop_path.exists() and prop_path.stat().st_size > 5 * 1024:
            bank.set_asset(ident, {"path": str(prop_path.resolve()),
                                   "description": f"{ident} 道具资产参考图"})
            total += 1
            logger.info(f"[ASSET] 道具 {ident}: 资产图已存在，复用")
            continue

        logger.info(f"[ASSET] 生成道具 {ident} 白底资产图 ...")
        try:
            img_out = await gen.generate_prop(prop)
            if not _save_image(img_out, prop_path):
                logger.warn(f"[ASSET] 道具 {ident} 资产图生成失败，跳过")
                continue
        except Exception as e:
            logger.warn(f"[ASSET] 道具 {ident} 资产图生成异常: {e}，跳过")
            continue

        bank.set_asset(ident, {"path": str(prop_path.resolve()),
                               "description": f"{ident} 道具资产参考图"})
        bank.save()
        total += 1

    return total


async def gen_all_assets(force: bool = False) -> dict:
    """step02a 入口：生成全部场景/道具资产图.

    使用专用横屏适配器（资产图是横屏参考，不加风格前缀，不做合规清洗——
    与角色肖像 _generate_missing_portraits 一致）。
    """
    if not ASSET_GEN_ENABLED:
        return {"scenes": 0, "props": 0}

    from shared.vimax_adapters import ImageGeneratorAiVideoAdapter

    # 资产图专用适配器：16:9 横屏、不加风格前缀、不替换敏感词（英文/纯描述 prompt）
    asset_adapter = ImageGeneratorAiVideoAdapter(
        size="1600x900",
        skip_prefix=True,
        skip_sanitize=True,
    )

    n_scenes = await gen_scene_assets(asset_adapter, force=force)
    n_props = await gen_prop_assets(asset_adapter, force=force)

    from shared.paths import chapter_dir
    from shared.tee_logger import get_logger
    logger = get_logger(chapter_dir() / "step02a.log", console_level="INFO")
    logger.info(f"[ASSET] 资产图生成完成：场景 {n_scenes} 个 / 道具 {n_props} 个")
    return {"scenes": n_scenes, "props": n_props}
