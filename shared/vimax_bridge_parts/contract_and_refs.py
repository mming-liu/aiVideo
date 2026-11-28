# -*- coding: utf-8 -*-
"""`shared.vimax_bridge` 拆分出的 contract_and_refs 部分（2026-11-28 O11）.

自动迁移：符号级依赖闭包已核；父模块 100% re-export 保持
`shared.vimax_bridge.<name>` 调用与 monkeypatch 语义不变。
"""
from __future__ import annotations

# 从父模块整体复制的导入表：迁移代码的自由名全部覆盖；未用到的导入
# 不参与 lint 计数（拆分不改行为，也不引入新的 F401 噪声）。
# ruff: noqa: F401
import asyncio
import fnmatch
import json
import os
import re
from collections import OrderedDict
from pathlib import Path
from vimax.interfaces import (
    Camera,
    CharacterInScene,
    ShotBriefDescription,
    ShotDescription,
)
from shared.character_bank import CharacterBank, StyleBank
from shared.paths import (
    shot_work_dir,
    FIRST_FRAME_FILENAME,
    LAST_FRAME_FILENAME,
    KEYFRAME_FILENAME_FMT,
)
import hashlib
from shared.style_prefix import (style_prefix_for_canvas, frame_canvas_ratio, sanitize_prompt,
                                 ensure_prefix, is_empty_scene, add_image_strict_constraints,
                                 preprocess_prompt_for_image)
from shared.style_prefix import IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT, image_lock_prompt
from step01_script_gen.prompt_semantic import (
    THREAT_WORDS_ENLARGED as _THREAT_WORDS_ENLARGED,
    THREAT_VISIBILITY_KEYWORDS as _THREAT_VISIBILITY_KEYWORDS,
    THREAT_CORE_IDENTIFIERS as _THREAT_CORE_IDENTIFIERS,
)
from step01_script_gen.prompt_semantic import WARDROBE_WORDS as _COSTUME_WORDS
from step01_script_gen.prompt_semantic import (
    HAIR_STATE_WORDS as _HAIR_STATE_WORDS,
    HAIR_STATIC_PHRASES as _HAIR_STATIC_PHRASES,
    ANIMAL_FACE_KEYWORDS as _ANIMAL_FACE_KEYWORDS,
    NO_ANIMAL_FEATURES_GUARD as _NO_ANIMAL_FEATURES_GUARD,
    ANIMAL_METAPHOR_FACE_REPLACEMENTS as _ANIMAL_METAPHOR_FACE_REPLACEMENTS,
)
from shared.content_safety import (
    WEAPON_WORDS,
    strip_action_result as _safety_strip_action_result,
)
from shared.split_similarity import (
    KF_FF_SIM_THRESHOLD as _KF_FF_SIM_THRESHOLD,
    KF_FF_ANCHOR_EXEMPT_STAGES as _KF_FF_ANCHOR_EXEMPT_STAGES,
    text_sim_ratio as _text_sim_ratio,
)


# monkeypatch 兼容转发（见 _forward.py）
from shared.vimax_bridge_parts._forward import forward as _forward

# monkeypatch 兼容：本文件内部调用也走父模块属性（定义本身仍导出）
_proxy__bridge_log = _forward("shared.vimax_bridge", '_bridge_log')  # noqa: F811
is_empty_scene = _forward('shared.vimax_bridge', 'is_empty_scene')  # noqa: F811
shot_work_dir = _forward('shared.vimax_bridge', 'shot_work_dir')  # noqa: F811

__all__ = [
    'PORTRAIT_VIEW_ROLES',
    'PROMPT_TEMPLATE_VERSION',
    '_ACTION_STRONG_VERBS',
    '_ATTEMPT_VARIATIONS',
    '_BODY_POSITION_WORDS',
    '_CURRENT_FRAME_COLOR_WORDS',
    '_EXPLICIT_VEHICLE_MOTION',
    '_INERT_STATE_WORDS',
    '_LATER_STATE_WORDS',
    '_LF_GAZE_LOST_OBJECT',
    '_LF_VICTIM_HINTS',
    '_PROP_POSITION_WINDOW',
    '_PROP_POSITION_WORDS',
    '_PROP_SYNONYM_GROUPS',
    '_SPATIAL_FALLBACK_DEFAULTS',
    '_UNAPPROVED_PORTRAIT_STATUSES',
    '_bridge_log',
    '_build_camera_tree',
    '_build_char_anchor',
    '_build_kf_prop_scan',
    '_build_reference_list',
    '_characters_in_units',
    '_collect_scene_asset_refs',
    '_collect_space_object_refs',
    '_compose_frame_refs_by_category',
    '_dedupe_char_anchor_guard',
    '_dedupe_prompt_clauses',
    '_detect_action_subject',
    '_detect_spatial_fallback',
    '_detect_view_preference',
    '_dump_json',
    '_fixed_camera_shift_anchor',
    '_frame_spec_closeup',
    '_frame_spec_roster',
    '_inject_object_size_anchor',
    '_inject_tone_anchor',
    '_is_action_shot',
    '_is_image_file_readable_readonly',
    '_is_space_object_prop',
    '_load_portrait_stores_readonly',
    '_norm_portrait_char_name',
    '_portrait_approved_ok',
    '_portrait_identity_ok',
    '_prop_terms',
    '_resolve_missing_lf_desc',
    '_select_all_character_refs',
    '_select_best_reference',
    '_select_states_by_contract',
    '_state_present_for_char',
    '_strip_frame_action_result',
    '_strip_later_timeline_clauses',
    '_units_for_indices',
]


# 静默日志: 桥接日志写入 step02.log，不污染控制台
def _bridge_log(msg: str):
    """写入文件日志.

    2026-11-14（U6-FIN WP1b）：落点必须走 :func:`shared.log_utils.resolve_log_file`。
    旧写法硬编码 `chapter_dir()/step02.log`，于是 `step02b --plan-only` 一碰哈希
    计算就往**正式章目录**建了 step02.log，被 production snapshot 判成
    `production_write_detected`（exit 3）——plan-only 的"零写入"其实只是没写产物。
    """
    from shared.tee_logger import get_logger
    try:
        from shared.log_utils import resolve_log_file
        log = get_logger(resolve_log_file("step02.log"), console_level="INFO")
        log.verbose(msg)
    except Exception as _degrade_exc:
        from shared.degrade import note_degraded as _note_degraded
        _note_degraded("shared/vimax_bridge.py:_bridge_log", _degrade_exc)

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

    _proxy__bridge_log(f"  [bridge] camera_tree: {len(cameras)} 台摄像机 "
          f"{[c.idx for c in cameras]}")
    for c in cameras:
        if c.parent_cam_idx:
            _proxy__bridge_log(f"    cam_{c.idx} -> parent=cam_{c.parent_cam_idx}"
                  f" 镜头{c.parent_shot_idx}")
    return cameras

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
                _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 空镜，参考图=摄像机{cam_idx}首帧锚定（背景）")
        # 回退到风格锚点（Ch1 锁定的全局风格）
        if not refs and style_bank.is_locked():
            style_anchor = style_bank.get_anchor_path()
            if style_anchor and Path(style_anchor).exists():
                refs.append(style_anchor)
                _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 空镜，参考图=风格锚点（背景）")
        if not refs:
            _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 空镜，无可用背景参考图，降级为 text-to-image")
        return refs[:1]

    # 非首镜头：引用本摄像机首帧作为锚定（保证场景连贯，优先级最高）
    if not is_first_in_cam and cam_idx in camera_first_frames:
        anchor = camera_first_frames[cam_idx]
        if anchor and Path(anchor).exists():
            refs.append(anchor)
            _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 参考图=摄像机{cam_idx}首帧锚定")

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

def _units_for_indices(script: dict, only_indices) -> list[dict]:
    """按 --shots 选出目标单元（hook 的 index=0 + 指定 shot）."""
    wanted = {int(i) for i in only_indices}
    units: list[dict] = []
    hook = script.get("hook")
    if isinstance(hook, dict) and 0 in wanted:
        units.append(hook)
    for shot in script.get("shots") or []:
        try:
            if int(shot.get("index", -1)) in wanted:
                units.append(shot)
        except (TypeError, ValueError):
            continue
    return units

def _characters_in_units(characters: list[dict], units: list[dict]) -> list[dict]:
    """目标单元里实际出场的角色（spec 出镜名单优先，退回全文匹配）.

    spec 名单走 `_frame_spec_roster`（per-frame 事实源）；没有 spec 时用角色名
    是否出现在单元文本里兜底。宁可少生成（后续全量跑会补齐），不连带全章。
    """
    if not units:
        return []
    names: set = set()
    for unit in units:
        for frame_kind in ("ff", "lf"):
            try:
                names |= set(_frame_spec_roster(unit, frame_kind) or ())
            except Exception:
                pass
        kfs = (unit.get("frame_layouts") or {}).get("kfs") or []
        for i in range(len(kfs)):
            try:
                names |= set(_frame_spec_roster(unit, "keyframe", i) or ())
            except Exception:
                pass
        for entry in unit.get("char_layout") or []:
            if isinstance(entry, dict):
                for key in ("name", "identifier"):
                    if entry.get(key):
                        names.add(str(entry[key]))
    text = json.dumps(units, ensure_ascii=False)
    out = []
    for char in characters:
        ident = str(char.get("identifier") or "")
        if ident and (ident in names or ident in text):
            out.append(char)
    return out

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

def _style_resolved_portraits(bank, identifier: str) -> tuple[dict, str]:
    """按 Bank 的统一画风判据取可用肖像（只读，不建目录）.

    2026-11-28 S02：参考图收集不得直接消费 `get_character()["portraits"]`；
    Bank 支持 `resolve_portraits()` 时以它为准（跨画风/历史资产按同一判据
    拒收），不支持时退回旧行为（仅供测试替身）。
    """
    if bank is not None and hasattr(bank, "resolve_portraits"):
        try:
            resolved = bank.resolve_portraits(identifier)
        except Exception:
            resolved = None
        if isinstance(resolved, dict):
            return (dict(resolved.get("portraits") or {}),
                    str(resolved.get("reason") or ""))
    entry = (bank.get_character(identifier)
             if bank is not None and hasattr(bank, "get_character") else None)
    return (dict((entry or {}).get("portraits") or {}), "")


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
        _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 空镜，不注入参考图")
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
            portraits, _style_reason = _style_resolved_portraits(bank, identifier)
            if portraits:
                # 特写镜头：face 面部特写优先（锁五官防变脸，组合参考图法）；
                # 否则按优先级尝试：检测到的视角 → front → side → back
                is_closeup = any(kw in search_text for kw in ("大特写", "特写", "面部", "表情", "脸部"))
                if is_closeup:
                    face_p = portraits.get("face", {}).get("path")
                    if face_p and Path(face_p).exists():
                        _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 参考图=角色 {identifier} 面部特写")
                        return face_p
                for view in [view_pref, "front", "side", "back"]:
                    p = portraits.get(view, {}).get("path")
                    if p and Path(p).exists():
                        view_tag = f"（视角={view}" + (" 自动匹配)" if view == view_pref else " 降级)")
                        _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 参考图=角色 {identifier} {view}肖像{view_tag}")
                        return p
                # 极端情况：portraits dict 里没有任何视图
                break

    # 回退：风格锚点
    anchor = style_bank.get_anchor_path()
    if anchor and Path(anchor).exists():
        _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 参考图=风格锚点")
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
        portraits, _style_reason = _style_resolved_portraits(bank, identifier)
        if not portraits:
            continue
        # 特写镜头：face 面部特写优先（锁五官防变脸）
        if is_closeup:
            face_p = portraits.get("face", {}).get("path")
            if face_p and Path(face_p).exists():
                refs.append(face_p)
                _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 多角色参考=角色 {identifier} 面部特写")
                if len(refs) >= limit:
                    break
                continue
        # 按优先级尝试：检测到的视角 → front → side → back
        for view in [view_pref, "front", "side", "back"]:
            p = portraits.get(view, {}).get("path")
            if p and Path(p).exists():
                refs.append(p)
                _proxy__bridge_log(f"  [bridge] 镜头{shot['index']}: 多角色参考=角色 {identifier} {view}肖像")
                break
        if len(refs) >= limit:
            break

    return refs

def _collect_scene_asset_refs(shot: dict) -> list[str]:
    """为本镜头匹配场景资产图（scenes_bank wide/near）作环境参考.

    用于 step02a 首帧图生图：注入与 r2v 同源的场景资产图，保证首帧的
    场景/环境/光线/船型与全局资产库一致，避免图生图自由发挥导致场景漂移。

    时间段匹配（重要）：资产库的 wide（大场景建立镜头）和 near（近景功能场景）
    常带不同时段/光线——如「照月湾小舟」wide=黄昏暖金、near=夜晚冷月。
    首帧的时间由 ff_desc 决定，若注入时段冲突的场景图（黄昏 wide 配夜晚
    首帧），会把时间带偏。故：画面描述含夜晚/月光/夜色 → 优先 near；
    含黄昏/夕阳/落日 → 优先 wide；无法判断时近景 near 兜底（含正确船型）。
    场景身份按 shared.scene_identity 解析：显式 scene_asset_id/scene_id 优先，
    否则只认完整 identifier/别名唯一命中；歧义与无命中只写诊断并跳过，
    不再按 bank 顺序或公共地名猜（2026-11-08 WP7-Prep 复核）。
    注意：step02b 的 r2v 参考链仍用旧的模糊匹配，属后续收敛项。
    """
    try:
        from shared.assets_bank import SceneAssetsBank
        from shared.scene_identity import resolve_scene_identity
        sbank = SceneAssetsBank()
        sbank.load()
        entries = {}
        for sid in sbank.get_all_identifiers():
            if sid:
                entries[str(sid)] = sbank.get_scene(sid) or {}
        # 2026-11-08 WP7-Prep：显式 id -> 完整 identifier/别名唯一命中；
        # 歧义/无命中只诊断、不按 bank 顺序猜（照月湾荷湾/荷塘静默选图根因）。
        match = resolve_scene_identity(
            entries,
            explicit_id=shot.get("scene_asset_id") or shot.get("scene_id"),
            scene_name=shot.get("scene"),
            location=shot.get("location"),
        )
        try:
            shot["_scene_match"] = match.as_dict()
        except Exception:
            pass
        if not match.ok:
            _proxy__bridge_log(
                "  [bridge] 镜头%s 场景参考跳过（%s）: %s"
                % (shot.get("index", "?"), match.reason,
                   "; ".join(match.diagnostics)))
            return []
        sid = match.scene_id
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
            _proxy__bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 首帧参考图=场景 {sid} {Path(picked).name}"
                        f"（{'夜→near' if is_night else '昏→wide' if is_dusk else '近景兜底'}）")
            return [picked]
        return []
    except Exception as e:
        _proxy__bridge_log(f"  [bridge] 场景资产参考图收集异常: {e}")
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
                _proxy__bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 首帧参考图=空间物体 {pid} {Path(img).name}")
        return refs
    except Exception as e:
        _proxy__bridge_log(f"  [bridge] 空间物体参考图收集异常: {e}")
        return []

_PROP_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("钓杆", "钓竿", "鱼竿"),
)

def _prop_terms(name: str) -> list[str]:
    """Return canonical orthographic synonyms for a prop name without duplicating."""
    t = str(name or "").strip()
    if not t:
        return []
    out = [t]
    for grp in _PROP_SYNONYM_GROUPS:
        if t in grp:
            for item in grp:
                if item not in out:
                    out.append(item)
    return out

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

    max_refs = active_image_ref_limit()
    buckets = [
        ("scene", list(scene_refs) + list(space_refs)),
        ("chars", list(portrait_refs)),
        ("props", list(prop_refs)),
    ]
    n_total = len({p for _, items in buckets for p in (items or []) if p and Path(p).exists()})
    frame_tag = {"首帧": "ff", "尾帧": "lf"}.get(for_frame, "kf")
    shot_dir = shot_work_dir(shot.get("index", 0))
    composed = fit_refs(buckets, max_refs, shot_dir, frame_tag)
    if n_total > max_refs:
        _proxy__bridge_log(
            f"  [bridge] 镜头{shot.get('index', '?')} {for_frame}: 参考图 {n_total} 张超上限 "
            f"{max_refs}，按类别无损压缩为 {len(composed)} 张"
        )
    return composed

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

PORTRAIT_VIEW_ROLES = {
    "front": "primary_full_body",
    "base": "primary_full_body",
    "side": "side_full_body",
    "back": "back_full_body",
    "face": "closeup_identity",
}

_UNAPPROVED_PORTRAIT_STATUSES = frozenset({
    "unapproved", "rejected", "pending", "draft", "stale", "failed",
    "qc_rejected", "qc_call_failed",
})

def _norm_portrait_char_name(name: str) -> str:
    from shared.character_bank import normalize_identifier
    return normalize_identifier(str(name or "").strip())

def _is_image_file_readable_readonly(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        from PIL import Image as _PILImage
        with _PILImage.open(path) as im:
            im.load()
        return True
    except Exception:
        return False

def _portrait_identity_ok(
    char_id: str,
    aliases: list[str],
    entry: dict | None,
    img_path: Path,
    bank_dir: Path | None,
) -> bool:
    norm_id = _norm_portrait_char_name(char_id)
    allowed_norms = {_norm_portrait_char_name(a) for a in ([char_id] + list(aliases or [])) if a}

    if isinstance(entry, dict):
        for k in ("character_id", "identifier", "owner", "subject"):
            val = str(entry.get(k) or "").strip()
            if val and _norm_portrait_char_name(val) not in allowed_norms:
                return False

    # Check if asset sits under a conflicting character directory in characters_bank
    try:
        resolved_img = img_path.resolve()
        if bank_dir is not None:
            resolved_bank = Path(bank_dir).resolve()
            try:
                rel = resolved_img.relative_to(resolved_bank)
                if len(rel.parts) >= 2:
                    folder_owner = _norm_portrait_char_name(rel.parts[0])
                    if folder_owner and folder_owner not in allowed_norms:
                        return False
            except ValueError:
                pass
    except OSError:
        pass

    # Check optional adjacent metadata sidecar (<stem>.json or meta.json)
    for meta_candidate in (img_path.with_suffix(".json"), img_path.parent / "meta.json"):
        if meta_candidate.is_file():
            try:
                md = json.loads(meta_candidate.read_text(encoding="utf-8"))
                if isinstance(md, dict):
                    for k in ("character_id", "identifier", "owner", "subject"):
                        val = str(md.get(k) or "").strip()
                        if val and _norm_portrait_char_name(val) not in allowed_norms:
                            return False
            except Exception:
                pass
    return bool(norm_id)

def _portrait_approved_ok(entry: dict | None, bank_entry: dict | None, img_path: Path) -> bool:
    for rec in (entry, bank_entry):
        if not isinstance(rec, dict):
            continue
        if rec.get("approved") is False:
            return False
        st = str(rec.get("status") or "").strip().lower()
        if st in _UNAPPROVED_PORTRAIT_STATUSES:
            return False
    qc_sc = img_path.with_name(f"{img_path.stem}_hallucination.json")
    if qc_sc.is_file():
        try:
            sc_data = json.loads(qc_sc.read_text(encoding="utf-8"))
            if str(sc_data.get("status") or "").strip().lower() in ("qc_rejected", "qc_call_failed"):
                return False
        except Exception:
            pass
    return True

def _load_portrait_stores_readonly() -> tuple[dict, object | None, Path | None]:
    reg: dict = {}
    bank = None
    bank_dir: Path | None = None
    try:
        from shared.paths import characters_bank_dir, vimax_working_dir
        bank_dir = characters_bank_dir()
        registry_path = vimax_working_dir() / "character_portraits_registry.json"
        if registry_path.is_file():
            try:
                loaded = json.loads(registry_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    reg = loaded
            except Exception:
                reg = {}
    except Exception:
        pass
    try:
        from shared.character_bank import CharacterBank, BankLoadError
        bank = CharacterBank()
        bank.load(read_only=True)   # S02：查询路径不建目录；损坏/非法结构抛结构化异常
        if not reg:
            reg = bank.get_all_portraits_as_registry() or {}
    except BankLoadError:
        # 损坏/非法结构必须显式：调用方（门禁/收集）据此报告，不静默当成“无肖像”。
        raise
    except Exception:
        bank = None
    return reg, bank, bank_dir

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
                        _proxy__bridge_log(
                            f"  [bridge] 场景物体 {name} 长宽比 {ratio:.1f} 离谱，"
                            f"仅注入长度，跳过宽度（B5 比例后验）"
                        )
                        width = None
                except (TypeError, ValueError):
                    width = None
            if not length and not width:
                continue
            # v6.52：剥离 desc 内嵌的 LLM 尺寸残留——「（宽1.4米，可容3人）」「（长6米
            # 宽1.4米无篷舴艋舟）」与 scene_objects 注入的尺寸自相矛盾（16:34 脚本
            # 实测 6 处 1.4米 vs 注入 1.6米），模型两侧都不信、船宽随机抽奖。
            # 尺寸一律以本函数注入的 scene_objects 锚为准。幂等：剥完再跑无变化。
            # 2026-11-13（S04 复核）：只在**确实有来源尺寸要注入**时才剥离——旧实现
            # 在匹配前无条件剥，未命中 scene_objects 时尺寸被删且无替代（内容净丢失）。
            text = re.sub(r"[（(][^（）()]*\d+(?:\.\d+)?米[^（）()]*[）)]", "", text)
            # 只注入尺寸/称呼，不注入容量结构（避免与画面描述冲突）。
            # S25：正向 prompt 不出现“无篷”二字（扩散模型对“篷/舟”名词
            # 激活强于“无”），展示名统一改成“敞口”。
            _display_name = str(name).replace("无船篷", "敞口").replace("无篷", "敞口")
            _display_subtype = str(subtype).replace("无船篷", "敞口").replace("无篷", "敞口")
            size_desc = f"该{_display_name}为{_display_subtype}{'，长' + str(length) + '米' if length else ''}{'、宽' + str(width) + '米' if width else ''}"
            # 2026-11-13（S04 复核）：这里只保留**有来源**的属性（名称/形制/长宽）。
            # 旧实现还追加过两段无依据假设，已删除：
            #   ① 「船宽约为人物肩宽的三倍，…仍有落脚空档」——肩宽比例没有输入来源，
            #      且与绝对米数并存时会给出两套互相打架的尺度；
            #   ② 命中「近景/中景/中近景」就「只画船头/船舷局部，船体向画外延伸」——
            #      裁切与构图是输入/计划的决定，不能由下游按景别词补造。
            # 无篷形制反向锚仍有来源（subtype/name 里的「无篷」），保留：
            # 「舴艋舟」易被联想成乌篷船（hook ff 实测画出大竹篷遮挡舱面）。
            if "无篷" in subtype or "无篷" in name:
                size_desc += "，敞口无遮挡，舱面完全敞开可见"
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

# v6.45：角色惰性/失能状态词——本镜呈躺/卧/倒/昏迷/闭目/中刀等状态时，
# 不可能手持任何物件。chapter 级 dynamic_features 的"手持钓杆"等跨镜手部物件
# 不得注入该角色（08-26 hook kf0 杨旭"手持钓杆"污染根因）。
_INERT_STATE_WORDS = (
    "仰卧", "倒地", "倒下", "躺", "卧", "瘫坐",
    "昏迷", "晕倒", "闭目", "双目紧闭", "已死", "身亡", "毙命",
    "中刀", "中剑", "胸口插", "心口插", "被刺",
)

def _dedupe_char_anchor_guard(additions: list[str]) -> tuple[list[str], str]:
    """Strip duplicated head/face guard from character strings and return standalone frame-level sentence."""
    guard = str(_NO_ANIMAL_FEATURES_GUARD or "")
    if not guard or not additions:
        return additions, ""
    seen_guard = False
    cleaned: list[str] = []
    for item in additions:
        if guard in item:
            seen_guard = True
            cleaned.append(item.replace(guard, ""))
        else:
            cleaned.append(item)
    if not seen_guard:
        return cleaned, ""
    return cleaned, f"全员{guard}。"

_CURRENT_FRAME_COLOR_WORDS = (
    "素白", "白色", "白衣", "玄色", "黑衣", "青色", "青衣", "藕荷", "春衫",
    "红衣", "血色", "暗红", "黑", "白", "青", "红", "蓝", "灰", "褐", "金", "银",
)

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

_LATER_STATE_WORDS = ("落水", "散发", "凌乱", "湿透", "受伤", "昏迷", "倒地",
                     "血迹", "血污")

def _state_present_for_char(text: str, state: str) -> bool:
    """当前角色文本里是否**肯定**出现该状态（排除“未/无/没有/不”否定）."""
    import re as _re
    for m in _re.finditer(re.escape(state), str(text or "")):
        pre = str(text)[max(0, m.start() - 3):m.start()]
        if not any(neg in pre for neg in ("未", "无", "没有", "不", "非")):
            return True
    return False

def _select_states_by_contract(dynamic: str, allowed_states) -> str:
    """显式状态合同：只保留 allowed_states 里的状态分句。

    这是通用入口；调用方必须给出本帧该角色有效状态（可含继承状态）。
    不在 allowed 里的状态分句被丢弃；静态服装/头饰分句保留。
    """
    text = str(dynamic or "")
    allowed = {str(s).strip() for s in (allowed_states or []) if str(s).strip()}
    if not text:
        return text
    import re as _re
    kept = []
    for clause in (c.strip() for c in _re.split(r"[，、,；;]", text) if c.strip()):
        states = [w for w in _LATER_STATE_WORDS if w in clause]
        if (not states or any(w in allowed for w in states)
                or any(a in clause for a in allowed)):
            kept.append(clause)
    return "，".join(kept)

def _strip_later_timeline_clauses(dynamic: str, frame_desc: str,
                                  current_text: str | None = None) -> str:
    """按本帧角色文本过滤 dynamic_features 中的状态分句.

    旧实现用“本帧提到任意状态词”做全局开关，且“，后”后整段截断；这里改为
    逐分句、逐角色：只保留本帧该角色文本里**肯定出现**的状态分句；静态服装/
    头饰分句始终保留。当前文本写“未受伤/没有散发”不视为状态成立。
    """
    text = str(dynamic or "")
    current = str(current_text if current_text is not None else frame_desc or "")
    if not text:
        return text
    import re as _re
    clauses = [c.strip() for c in _re.split(r"[，、,；;]", text) if c.strip()]
    kept = []
    for clause in clauses:
        states = [w for w in _LATER_STATE_WORDS if w in clause]
        if not states:
            kept.append(clause)          # 服装/头饰/静态特征
            continue
        if any(_state_present_for_char(current, w) for w in states):
            kept.append(clause)          # 本帧该角色确实处于此状态
        # 否则只丢这个状态分句，不按“后”字砍掉后续所有静态信息
    return "，".join(kept)

def _strip_frame_action_result(dynamic: str) -> str:
    """从 dynamic_features 中剥离首帧不应出现的动作结果/武器词（复用 shared 单一实现）."""
    return _safety_strip_action_result(dynamic)

# 目光看向"不在场/消失对象"的模式（触发改为看动作结果对象）
_LF_GAZE_LOST_OBJECT = (
    "消失之处", "消失处", "水面刺客", "刺客消失", "画右水面", "画左水面",
    "水中刺客", "刺客没入之处", "没入之处",
)

# 动作结果对象出现（场景含被刺倒的同伴）→ 才触发目光改写
_LF_VICTIM_HINTS = ("倒下", "仰面倒", "仰面躺", "中刀", "被刺", "心口", "洇开", "倒地", "受伤")

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
    if os.getenv("DOODLE_REFINE_ENABLED", "0").strip() != "1":
        return None

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

_EXPLICIT_VEHICLE_MOTION = (
    (r"(?:船|舟|车|轿|载具)[^，。；]{0,20}(?:向|朝|沿)画面(?:上方|上)", "up"),
    (r"(?:船|舟|车|轿|载具)[^，。；]{0,20}(?:向|朝|沿)画面(?:下方|下)", "down"),
    (r"(?:船|舟|车|轿|载具)[^，。；]{0,20}(?:向画面左|向画面右|横向)", "horizontal"),
    (r"(?:船|舟|车|轿|载具)[^，。；]{0,20}(?:迎面|向镜头|驶近|靠近镜头)", "approach"),
)

def _fixed_camera_shift_anchor(desc: str) -> str:
    """只消费输入明确写出的载具屏幕方向；不凭“深处/前行”猜 Y 轴位移."""
    import re as _re
    text = str(desc or "")
    if "纵向" in text or "近大远小" in text or "横向移动" in text:
        return ""
    for pat, direction in _EXPLICIT_VEHICLE_MOTION:
        if not _re.search(pat, text):
            continue
        if direction == "up":
            return ("；该载具与乘员沿画面纵向向上移动并随距离略微缩小（近大远小），"
                    "不得用擦除背景代替位移")
        if direction == "down":
            return ("；该载具与乘员沿画面纵向向下移动并略微放大（近大远小），"
                    "不得用擦除背景代替位移")
        if direction == "horizontal":
            return "；该载具与乘员沿画面横向移动，不改变船体尺度，不得用擦除背景代替位移"
        return "；该载具与乘员向镜头靠近并略微放大，不得用擦除背景代替位移"
    return ""

def _dedupe_prompt_clauses(text: str) -> str:
    """去掉**完全重复**的分句（同一句被多个注入器追加时只留第一处）.

    2026-11-08（WP2）：13 镜图片 prompt 体检里的重复短语，大多来自
    _inject_frame_spec_and_closeup / _extract_char_layout /
    add_image_strict_constraints 各自把同一句描述再写一遍。
    这里只做**确定性去重**（归一化后完全相同的分句），不改写、不删信息。
    """
    import re as _re
    parts = _re.split(r"(?<=[。；;])", str(text or ""))
    seen = set()
    out = []
    for part in parts:
        # 只把**原文相同**（仅去首尾空白）的分句视为重复：标点参与语义，
        # 「不，放行」与「不放行」不是同一句（复核 2026-11-08 已复现误删）。
        key = part.strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return "".join(out) or str(text or "")

PROMPT_TEMPLATE_VERSION = "step02a.prompt/v2"

def _resolve_missing_lf_desc(shot: dict) -> str | None:
    """Return a lossless static fallback for a missing ``lf_desc``.

    Only the same shot's static ``ff_desc`` may be reused.  Deriving a new
    last-frame scene from ``scene`` / ``main_subject`` / narration would create
    semantic facts after the step01 audit, so that path is deliberately refused.
    """
    ff_desc = str((shot or {}).get("ff_desc") or "").strip()
    if not ff_desc:
        return None
    motion_keywords = (
        "上移", "下移", "左移", "右移", "推进", "拉远", "跃起", "冲出",
        "穿越", "飞", "跑", "跳", "蹦", "滑", "冲", "快速", "缓慢",
    )
    if any(kw in ff_desc for kw in motion_keywords):
        return None
    return ff_desc

def _dump_json(path: Path, model) -> None:
    """写 pydantic 模型或模型列表为 JSON（ViMax 用 model_validate_json 读回）."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(model, list):
        data = [m.model_dump() for m in model]
    else:
        data = model.model_dump()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

