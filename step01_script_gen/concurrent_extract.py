"""step01_script_gen.concurrent_extract - 并发预提取 helper.

v6.43（08-25）优化：3 个 LLM 预提取（角色/场景物体/大纲）无依赖关系，
原本按顺序执行 = 3 × LLM 延迟（~16s 总耗时）；改用 ThreadPoolExecutor
并发执行后 = max(3) ≈ 5s，节省 ~10s。

合并 schema 是替代方案但改动大（要改 deepseek_schema.py + 3 个 extractor），
并发更简单且不影响 3 个模块的独立 schema（保留各自优化空间）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any


def extract_all_concurrent(
    novel_text: str,
    character_bank: Any,
    scene_objects_bank: Any,
    *,
    enable_characters: bool = True,
    enable_scene_objects: bool = True,
    enable_outline: bool = True,
    log_func=None,
) -> dict[str, Any]:
    """并发跑 3 个 LLM 预提取（角色/场景物体/大纲），返回 dict 各项.

    Args:
        novel_text: 小说原文
        character_bank: CharacterBank 实例（角色提取写入）
        scene_objects_bank: SceneObjectsBank 实例（场景物体写入）
        enable_*: 各项独立开关（用 .env 推断）
        log_func: 进度日志函数（默认 print 兜底）

    Returns:
        {"characters": [...], "scene_objects": [...], "outline": {...}}
        任一失败则对应字段为 None，调用方按现状继续（不阻断）。
    """
    _log = log_func or (lambda msg: print(msg))

    def _do_characters() -> list[dict] | None:
        from step01_script_gen.character_extractor import extract_characters
        return extract_characters(novel_text, character_bank)

    def _do_scene_objects() -> list[dict] | None:
        from step01_script_gen.object_extractor import extract_scene_objects
        return extract_scene_objects(novel_text, scene_objects_bank)

    def _do_outline() -> dict | None:
        from step01_script_gen.outline import generate_outline
        return generate_outline(novel_text)

    # 提交 3 个任务
    futures = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="pre_extract") as ex:
        if enable_characters:
            futures[ex.submit(_do_characters)] = "characters"
        if enable_scene_objects:
            futures[ex.submit(_do_scene_objects)] = "scene_objects"
        if enable_outline:
            futures[ex.submit(_do_outline)] = "outline"

        results: dict[str, Any] = {key: None for key in futures.values()}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception as e:
                _log(f"[WARN] 预提取 {key} 失败: {e}")
                results[key] = None
    return results


def extract_assets_concurrent(
    script: dict,
    scene_assets_bank: Any,
    prop_assets_bank: Any,
    *,
    enable_scene_assets: bool = True,
    enable_props: bool = True,
    log_func=None,
) -> dict[str, Any]:
    """并发跑 2 个 LLM 资产提取（场景/道具），返回 dict 各项.

    v6.43（08-25）优化：2 个 LLM 提取都基于同一份 script_json，无依赖关系，
    并发执行可节省 ~5s wall-clock（同 #29 思路）。
    """
    _log = log_func or (lambda msg: print(msg))

    def _do_scene_assets() -> list[dict] | None:
        from step01_script_gen.asset_extractor import extract_scene_assets
        return extract_scene_assets(script, scene_assets_bank)

    def _do_props() -> list[dict] | None:
        from step01_script_gen.asset_extractor import extract_props
        return extract_props(script, prop_assets_bank)

    futures = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="asset_extract") as ex:
        if enable_scene_assets:
            futures[ex.submit(_do_scene_assets)] = "scene_assets"
        if enable_props:
            futures[ex.submit(_do_props)] = "prop_assets"

        results: dict[str, Any] = {key: None for key in futures.values()}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception as e:
                _log(f"[WARN] 资产提取 {key} 失败: {e}")
                results[key] = None
    return results
