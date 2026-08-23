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

import os
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


async def _qc_asset_image(asset_path: Path, ident: str, desc: str,
                          empty_scene: bool = False) -> bool:
    """资产图内容质检（B8 修复 08-11）.

    复用 shared/vimax_bridge._maybe_qc_frame 的视觉质检（ds-vision-skill）：
    - 场景 wide/near 图（禁止人物）：empty_scene=True → QC 检查空镜出人
    - 道具 prop 图（白底棚拍）：empty_scene=False → QC 检查多余物/水印/手
    质检通过（ok=True 且无 critical）返回 False（不需要重生成）；检出 critical
    返回 True（调用方应删图重生成）。软失败（router 缺失/超时）返回 False
    （不阻断，保持资产图可用的既有行为）。

    shot: 最小 shot dict（含 index/ident/描述），供 build_qc_prompt 构建质检 prompt。
    """
    try:
        from shared.vimax_bridge import _maybe_qc_frame
        shot = {
            "index": ident,
            "scene": ident,
            "main_subject": ident,
            "core_keywords": [desc[:40]] if desc else [],
        }
        critical = await _maybe_qc_frame(
            shot, str(asset_path), "first_frame",
            characters=[], empty_scene=empty_scene,
            prompt=desc,
        )
        if critical:
            print(f"[ASSET] {ident} 资产图 QC critical，将删图重生成")
            try:
                asset_path.unlink(missing_ok=True)
            except Exception:
                pass
            return True
        return False
    except Exception as e:
        print(f"[ASSET] {ident} 资产图质检异常（不阻断）: {e}")
        return False


async def gen_scene_assets(image_adapter, force: bool = False, hallucinated_ids: set | None = None) -> int:
    """为场景资产库中缺图的场景生成大场景图 + 近景功能场景图.

    Args:
        script: script_result.json（含 scene_assets 描述，但 bank 才是权威源）
        image_adapter: 图片生成适配器（横屏专用，见调用方）
        force: 忽略缓存强制重生成
        hallucinated_ids: audit 识别的幻觉 prop/scene 集合 → 这些直接跳过不调 4.5

    Returns:
        int: 成功生成/复用的场景资产数
    """
    hallucinated_ids = hallucinated_ids or set()
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
        # v6.40 08-22：audit 识别的幻觉 scene 直接跳过（不调 4.5 重生成）
        if ident in hallucinated_ids:
            logger.info(f"[ASSET] 场景 {ident}: 幻觉条目，跳过不补生成")
            continue
        entry = bank.get_scene(ident)
        if not entry:
            continue
        scene = scene_assets_from_bank(entry)
        scene_dir = bank.get_asset_dir(ident)
        scene_dir.mkdir(parents=True, exist_ok=True)
        wide_path = scene_dir / "wide.png"

        if not force and wide_path.exists() and wide_path.stat().st_size > 5 * 1024:
            # 已入库大场景图直接复用（生成时已过内容质检），不再每轮复检——
            # 省掉每轮对全部场景资产跑视觉 QC 的 API 开销，也避免 QC 抖动导致
            # 已接受的图被反复判 critical、每轮删图重生成烧额度。
            # 需要重审时用 --force 或删掉 wide.png 再跑。
            bank.set_assets(ident, {
                "wide": {"path": str(wide_path.resolve()),
                         "description": f"{ident} 大场景建立镜头参考图"},
            })
            total += 1
            logger.info(f"[ASSET] 场景 {ident}: 大场景图已存在，复用")
            continue

        # 生成大场景建立镜头图
        # v6.40 08-23：跳过视觉 QC——专用 wide prompt 已 4 次强约束"不得出现任何
        # 人物/身影/人影/轮廓或人形"，且专用横屏适配器关闭了幻觉锁图。ds-vision-skill
        # 视觉模型对 2560x1440 大场景空镜（远景屋脊/炊烟/水面倒影/树丛阴影）
        # 误判率高，每个新场景首生成浪费 1 张 3.69M 像素档位的 wide.png。
        # 风险：完全依赖 prompt 强约束，已与 line 128-139 复用分支保持一致。
        try:
            img_out = await gen.generate_wide(scene, save_path=str(wide_path))
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
                img_out = await gen.generate_near(scene, str(wide_path), save_path=str(near_path))
                if img_out is not None and _save_image(img_out, near_path):
                    assets["near"] = {"path": str(near_path.resolve()),
                                      "description": f"{ident} 近景功能场景参考图"}
                    logger.info(f"[ASSET] 场景 {ident} 近景功能场景图 -> {near_path}")
            except Exception as e:
                logger.warn(f"[ASSET] 场景 {ident} 近景图生成异常: {e}（保留大场景图）")
        elif near_path.exists():
            assets["near"] = {"path": str(near_path.resolve()),
                              "description": f"{ident} 近景功能场景参考图"}

        # v6.23：侧面全景图（可选，从大场景派生，多视图）
        side_path = scene_dir / "side.png"
        if SCENE_NEAR_SHOT_ENABLED and (not side_path.exists() or force):
            try:
                img_out = await gen.generate_side(scene, str(wide_path), save_path=str(side_path))
                if img_out is not None and _save_image(img_out, side_path):
                    assets["side"] = {"path": str(side_path.resolve()),
                                      "description": f"{ident} 侧面全景参考图"}
                    logger.info(f"[ASSET] 场景 {ident} 侧面全景图 -> {side_path}")
            except Exception as e:
                logger.warn(f"[ASSET] 场景 {ident} 侧面全景图生成异常: {e}（保留大场景图）")
        elif side_path.exists():
            assets["side"] = {"path": str(side_path.resolve()),
                              "description": f"{ident} 侧面全景参考图"}

        bank.set_assets(ident, assets)
        bank.save()
        total += 1

    return total


async def gen_prop_assets(image_adapter, force: bool = False, hallucinated_ids: set | None = None) -> int:
    """为道具资产库中缺图的道具生成白底资产图.

    Args:
        script: script_result.json（含 prop_assets 描述，但 bank 才是权威源）
        image_adapter: 图片生成适配器（横屏专用，见调用方）
        force: 忽略缓存强制重生成
        hallucinated_ids: audit 识别的幻觉 prop/scene 集合 → 这些直接跳过不调 4.5

    Returns:
        int: 成功生成/复用的道具资产数
    """
    hallucinated_ids = hallucinated_ids or set()
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
        # v6.40 08-22：audit 识别的幻觉 prop 直接跳过（不调 4.5 重生成）
        if ident in hallucinated_ids:
            logger.info(f"[ASSET] 道具 {ident}: 幻觉条目，跳过不补生成")
            continue
        entry = bank.get_prop(ident)
        if not entry:
            continue
        prop = prop_from_bank(entry)
        prop_dir = bank.get_asset_dir(ident)
        prop_dir.mkdir(parents=True, exist_ok=True)
        prop_path = prop_dir / "prop.png"

        if not force and prop_path.exists() and prop_path.stat().st_size > 5 * 1024:
            # 已入库道具图直接复用（生成时已过内容质检），不再每轮复检——
            # 省掉每轮对全部道具跑视觉 QC 的 API 开销，也避免 QC 抖动导致
            # 已接受的图（如活虾）被反复判 critical、每轮删图重生成烧额度。
            # 需要重审时用 --force 或删掉 prop.png 再跑。
            bank.set_asset(ident, {"path": str(prop_path.resolve()),
                                   "description": f"{ident} 道具资产参考图"})
            total += 1
            logger.info(f"[ASSET] 道具 {ident}: 资产图已存在，复用")
            continue

        # 生成白底资产图（B8 修复：QC critical 时重试最多 2 次）
        prop_ok = False
        for _attempt in range(1, 3):
            logger.info(f"[ASSET] 生成道具 {ident} 白底资产图"
                        f"{f'（重试 {_attempt}/2）' if _attempt > 1 else ''} ...")
            try:
                img_out = await gen.generate_prop(prop, save_path=str(prop_path))
                if not _save_image(img_out, prop_path):
                    logger.warn(f"[ASSET] 道具 {ident} 资产图生成失败，跳过")
                    break
                # B8：内容质检（白底道具图带手/多余物），critical → 删图重试
                _critical = await _qc_asset_image(
                    prop_path, ident, (entry.get("appearance") or "")[:120],
                    empty_scene=False,
                )
                if _critical:
                    continue
                prop_ok = True
                break
            except Exception as e:
                logger.warn(f"[ASSET] 道具 {ident} 资产图生成异常: {e}，跳过")
                break
        if not prop_ok:
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

    v6.40 08-22：先调 audit_orphan_props 拿 hallucinated_now 集合，
    传给 gen_scene_assets/gen_prop_assets → 幻觉 scene/prop 直接跳过
    （不调 4.5 重生成，节省额度）。已存在则跳过的逻辑保留。
    """
    if not ASSET_GEN_ENABLED:
        return {"scenes": 0, "props": 0}

    # 跑 audit 拿 hallucinated_now（需先有 script）
    _hallucinated = set()
    try:
        from shared.assets_bank import PropAssetsBank
        from step01_script_gen.asset_extractor import audit_orphan_props
        from shared.paths import script_file
        import json as _json
        _script_path = script_file()
        if _script_path.exists():
            with open(_script_path, "r", encoding="utf-8") as _f:
                _script = _json.load(_f)
            _bank = PropAssetsBank()
            _bank.load()
            _chapter = int(os.getenv("NOVEL_CHAPTER", "1"))
            _audit = audit_orphan_props(_bank, current_chapter=_chapter,
                                        current_script=_script, orphan_window=3)
            _hallucinated = set(_audit.get("hallucinated_now", []))
            from shared.paths import chapter_dir
            from shared.tee_logger import get_logger
            logger = get_logger(chapter_dir() / "step02a.log", console_level="INFO")
            logger.info(f"[ASSET] audit 识别 hallucinated_now={len(_hallucinated)} 个: "
                        f"{sorted(_hallucinated)[:8]}{'...' if len(_hallucinated) > 8 else ''}")
    except Exception as _e:
        # v6.40 08-23：失败原因必须可见（之前 pass 静默吞 NameError 导致幻觉 prop 漏拦活虾等 8 章）
        from shared.paths import chapter_dir
        from shared.tee_logger import get_logger
        try:
            logger = get_logger(chapter_dir() / "step02a.log", console_level="INFO")
            logger.warn(f"[ASSET] audit_orphan_props 失败，幻觉 prop 拦截失效: {_e!r}")
        except Exception:
            print(f"[ASSET][WARN] audit_orphan_props 失败，幻觉 prop 拦截失效: {_e!r}")

    from shared.vimax_adapters import ImageGeneratorAiVideoAdapter

    # 资产图专用适配器：16:9 横屏、不加风格前缀、不替换敏感词、不注入幻觉锁图
    # （资产图禁止人物，无需面部/服装锁定；锁图词"統一五官"对无人物场景无意义）
    # 2026-08-22 升级 2560x1440（= 3,686,400 像素）满足 Seedream 4.5 像素下限 3686400
    asset_adapter = ImageGeneratorAiVideoAdapter(
        size="2560x1440",
        skip_prefix=True,
        skip_sanitize=True,
        enable_hallucination_lock=False,
    )

    n_scenes = await gen_scene_assets(asset_adapter, force=force, hallucinated_ids=_hallucinated)
    n_props = await gen_prop_assets(asset_adapter, force=force, hallucinated_ids=_hallucinated)

    from shared.paths import chapter_dir
    from shared.tee_logger import get_logger
    logger = get_logger(chapter_dir() / "step02a.log", console_level="INFO")
    logger.info(f"[ASSET] 资产图生成完成：场景 {n_scenes} 个 / 道具 {n_props} 个")
    return {"scenes": n_scenes, "props": n_props}
