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

# 2026-11-28 O11：结构拆分后从 parts/ 显式回导，保持模块路径与
# monkeypatch 语义不变（父模块仍是唯一对外入口）。
from shared.vimax_bridge_parts.contract_and_refs import (  # noqa: F401
    PORTRAIT_VIEW_ROLES,
    PROMPT_TEMPLATE_VERSION,
    _ACTION_STRONG_VERBS,
    _ATTEMPT_VARIATIONS,
    _BODY_POSITION_WORDS,
    _CURRENT_FRAME_COLOR_WORDS,
    _EXPLICIT_VEHICLE_MOTION,
    _INERT_STATE_WORDS,
    _LATER_STATE_WORDS,
    _LF_GAZE_LOST_OBJECT,
    _LF_VICTIM_HINTS,
    _PROP_POSITION_WINDOW,
    _PROP_POSITION_WORDS,
    _PROP_SYNONYM_GROUPS,
    _SPATIAL_FALLBACK_DEFAULTS,
    _UNAPPROVED_PORTRAIT_STATUSES,
    _bridge_log,
    _build_camera_tree,
    _build_char_anchor,
    _build_kf_prop_scan,
    _build_reference_list,
    _characters_in_units,
    _collect_scene_asset_refs,
    _collect_space_object_refs,
    _compose_frame_refs_by_category,
    _dedupe_char_anchor_guard,
    _dedupe_prompt_clauses,
    _detect_action_subject,
    _detect_spatial_fallback,
    _detect_view_preference,
    _dump_json,
    _fixed_camera_shift_anchor,
    _frame_spec_closeup,
    _frame_spec_roster,
    _inject_object_size_anchor,
    _inject_tone_anchor,
    _is_action_shot,
    _is_image_file_readable_readonly,
    _is_space_object_prop,
    _load_portrait_stores_readonly,
    _norm_portrait_char_name,
    _portrait_approved_ok,
    _portrait_identity_ok,
    _prop_terms,
    _resolve_missing_lf_desc,
    _select_all_character_refs,
    _select_best_reference,
    _select_states_by_contract,
    _state_present_for_char,
    _strip_frame_action_result,
    _strip_later_timeline_clauses,
    _units_for_indices,
)
from shared.vimax_bridge_parts.ledger_and_errors import (  # noqa: F401
    FAILURE_INFRA_UNAVAILABLE,
    FAILURE_MISSING_ASSET,
    FAILURE_QC_DISPUTE,
    FAILURE_REQUEST_CONFLICT,
    FAILURE_STOCHASTIC_MISS,
    _NON_RETRYABLE_FAILURES,
    _check_frame_contract_preflight,
    _classify_generation_failure,
    _resolve_ledger_qc_verdict,
)
from shared.vimax_bridge_parts.paths_and_cache import (  # noqa: F401
    _PROTECTED_FRAME_MARKER,
    _accept_doodle_refinement,
    _adapter_resolved_model_id,
    _cleanup_doodle_candidate,
    _closeup_directive,
    _configured_image_provider_model,
    _del_frame_and_hash,
    _del_frame_unless_protected,
    _doodle_refine_first_frame,
    _emit_stage4_ledger,
    _frame_is_protected,
    _generate_missing_portraits,
    _get_frame_content_hash,
    _head_portrait_path,
    _keep_protected_frame,
    _maybe_qc_frame,
    _no_auto_repair,
    _persist_frame_request_snapshot,
    _protected_frames_env,
    _resolve_style_anchor_path,
    _shot_complexity_label,
    _try_qc_repair,
    _validate_frame,
)

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
    # F5 统一只读肖像资产解析
    "PORTRAIT_VIEW_ROLES",
    "resolve_character_portrait_assets",
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
    # 2026-11-28 O11：拆分后这些名字的调用点已移入 parts/，但它们仍是本模块的
    # 公开命名空间（外部 import / 测试 monkeypatch 依赖），显式保留。
    "Camera",
    "_NO_ANIMAL_FEATURES_GUARD",
    "_HAIR_STATIC_PHRASES",
    "_HAIR_STATE_WORDS",
    "_ANIMAL_FACE_KEYWORDS",
    "_ANIMAL_METAPHOR_FACE_REPLACEMENTS",
    "shot_work_dir",
    "_safety_strip_action_result",
    "fnmatch",
    "hashlib",
]



# 首帧/尾帧生成需要风格前缀（与视频生成保持一致）
from shared.style_prefix import (style_prefix_for_canvas, frame_canvas_ratio, sanitize_prompt,
                                 ensure_prefix, is_empty_scene, add_image_strict_constraints,
                                 preprocess_prompt_for_image)
from shared.style_prefix import IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT, image_lock_prompt


# 注意: 视频级缓存哈希 (prompt_hash.txt) 的唯一读写实现在
# step02_video_gen/submit_poll.py 的 _get_shot_hash / _poll_video 中，
# 此处不再保留第二套实现，避免读写算法分歧导致缓存永不命中。


# ---------------------------------------------------------------------------
# 摄像机树构建（算法驱动，无 LLM）
# ---------------------------------------------------------------------------



async def build_working_dir(
    script_path: Path,
    working_dir: Path,
    image_adapter,
    force_first_frame: bool = False,
    skip_frames: bool = False,
    only_indices: "set | None" = None,
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
    # 2026-10-31 S13B：--shots 子集运行时只补**目标镜头实际出场**角色的肖像，
    # 避免单镜试跑连带为全章未出场角色生成 4 视图（实测 3 角色 × 4 = 12 张）。
    registry = {}
    if characters_data:
        portrait_characters = characters_data
        if only_indices is not None:
            units = _units_for_indices(script, only_indices)
            portrait_characters = _characters_in_units(characters_data, units)
        if portrait_characters:
            registry = await _generate_missing_portraits(
                portrait_characters, bank, image_adapter, chapter_idx,
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
        _payload = registry
        if only_indices is not None and registry_path.is_file():
            # 子集运行只补目标角色；**不得**用子集 registry 覆盖全章注册表
            # （否则未出场角色会从 registry 里消失）。合并写回。
            try:
                _old = json.loads(registry_path.read_text(encoding="utf-8"))
                if isinstance(_old, dict):
                    _old.update(registry)
                    _payload = _old
            except Exception:
                _payload = registry
        _adj(registry_path, _payload, ensure_ascii=False, indent=2)
        _bridge_log(f"  [bridge] character_portraits_registry.json: {len(_payload)} 个角色")

    # ---- 3. storyboard.json（cam_idx 从 script 读取）----
    # 子集运行**不重写**全章分镜表/摄像机树：它们描述整章结构，用子集覆盖会
    # 让未参与本次运行的镜头从缓存里消失。
    storyboard_path = working_dir / "storyboard.json"
    storyboard = [] if only_indices is not None else [
        ShotBriefDescription(
            idx=shot["index"],
            is_last=(i == n),
            cam_idx=shot.get("cam_idx", 1),          # ← 不再硬编码
            visual_desc=shot.get("prompt", ""),
            audio_desc=shot.get("subtitle", ""),
        )
        for i, shot in enumerate(shots, 1)
    ]
    if only_indices is None:
        _dump_json(storyboard_path, storyboard)
    else:
        _bridge_log("  [bridge] 子集运行：保留全章 storyboard.json 不重写")

    # ---- 4. camera_tree.json（算法构建多摄像机树）----
    camera_tree_path = working_dir / "camera_tree.json"
    if only_indices is None:
        camera_tree = _build_camera_tree(shots)
        _dump_json(camera_tree_path, camera_tree)
    else:
        _bridge_log("  [bridge] 子集运行：保留全章 camera_tree.json 不重写")

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
        if only_indices is not None and int(idx) not in only_indices:
            continue
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
                    _anchor_empty = is_empty_scene(shot, characters_data)
                    style_bank.lock(
                        ff_result, chapter_idx,
                        anchor_kind=("empty_scene" if _anchor_empty else "character_scene"),
                        allow_characters=not _anchor_empty,
                        expected_persons=0 if _anchor_empty else None,
                    )
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





# ---------------------------------------------------------------------------
# 角色肖像生成
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# 参考图选择（增强版：视角检测 + 多字段搜索 + 摄像机优先）
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# 首帧/尾帧生成（带校验重试）
# ---------------------------------------------------------------------------



































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
            raw_terms = [pid] + list(entry.get("consistent_terms", []) or [])
            match_sources: list[str] = []
            for rt in raw_terms:
                for syn in _prop_terms(rt):
                    if syn not in match_sources:
                        match_sources.append(syn)
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






def _collect_frame_refs_for_shot(shot: dict, characters: list | None = None,
                                 limit: int = 5, for_frame: str = "首帧",
                                 return_buckets: bool = False,
                                 kf_index: int | None = None,
                                 apply_head_crop: bool | None = None,
                                 create_head_crop: bool = True):
    """为 step02a 首帧/尾帧/关键帧图生图收集参考图（场景 → 角色 → 道具）.

    for_frame: "首帧"/"尾帧"/"关键帧"；当 for_frame=="关键帧" 且传入 kf_index 时，
    按该关键帧自己的 frame_layouts.kfs[kf_index] 判定特写门控、出镜名单与描述。
    """
    empty_scene = is_empty_scene(shot, characters or [])
    scene_refs = _collect_scene_asset_refs(shot)
    omissions: list[dict] = []
    if scene_refs and for_frame in ("首帧", "尾帧", "关键帧"):
        if for_frame == "首帧":
            _is_cu = _frame_spec_closeup(shot, "ff")
        elif for_frame == "尾帧":
            _is_cu = _frame_spec_closeup(shot, "lf")
        else:
            _is_cu = (
                _frame_spec_closeup(shot, "keyframe", kf_index)
                if kf_index is not None else False
            )
        if _is_cu:
            omissions.append({
                "kind": "scene",
                "reason": "closeup_frame",
                "detail": "spec 含特写，广角场景资产图会把构图拉回全景",
                "reviewed": False,
            })
            scene_refs = []
            _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 特写帧跳过场景资产图（防广角拉构图）")
    if empty_scene:
        style_anchor_used = False
        if not scene_refs:
            _sa = _resolve_style_anchor_path()
            if _sa and _sa not in scene_refs:
                scene_refs.append(_sa)
                style_anchor_used = True
        if return_buckets:
            return {"refs": scene_refs[:limit],
                    "buckets": [("scene", scene_refs[:limit])],
                    "omissions": omissions,
                    "missing": [],
                    "identity_reference": "empty_scene",
                    "style_anchor_used": style_anchor_used}
        return scene_refs[:limit]

    space_refs = _collect_space_object_refs(shot)
    if for_frame == "首帧":
        frame_desc = shot.get("ff_desc", "") or ""
    elif for_frame == "尾帧":
        frame_desc = shot.get("lf_desc", "") or ""
    elif for_frame == "关键帧" and kf_index is not None:
        kfs = shot.get("keyframes") or []
        kf_item = kfs[kf_index] if 0 <= kf_index < len(kfs) and isinstance(kfs[kf_index], dict) else {}
        frame_desc = str(kf_item.get("desc") or "").strip() or (
            shot.get("motion_desc", "") or shot.get("prompt", ""))
    else:
        frame_desc = " ".join(filter(None, [
            shot.get("ff_desc", ""),
            shot.get("lf_desc", ""),
            shot.get("motion_desc", "") or shot.get("prompt", ""),
        ]))
    _roster = None
    _closeup = False
    if for_frame == "首帧":
        _roster = _frame_spec_roster(shot, "ff")
        _closeup = _frame_spec_closeup(shot, "ff")
    elif for_frame == "尾帧":
        _roster = _frame_spec_roster(shot, "lf")
        _closeup = _frame_spec_closeup(shot, "lf")
    elif for_frame == "关键帧" and kf_index is not None:
        _roster = _frame_spec_roster(shot, "keyframe", kf_index)
        _closeup = _frame_spec_closeup(shot, "keyframe", kf_index)
    _do_crop = (not return_buckets) if apply_head_crop is None else bool(apply_head_crop)
    portrait_details = _collect_portrait_refs_for_shot(
        shot, characters, limit=max(2, limit - 1), frame_desc=frame_desc,
        roster=_roster, return_details=True, closeup=_closeup,
        apply_head_crop=_do_crop,
        create_head_crop=create_head_crop,
    )
    if isinstance(portrait_details, dict):
        portrait_refs = list(portrait_details.get("refs") or [])
        omissions.extend(portrait_details.get("omissions") or [])
        missing_refs = list(portrait_details.get("missing") or [])
        id_ref_state = str(portrait_details.get("identity_reference") or "verified")
    else:
        # Test monkeypatches returning list[str]
        portrait_refs = list(portrait_details or [])
        missing_refs = []
        id_ref_state = "verified" if portrait_refs else "unverified"
    prop_refs = _collect_prop_asset_refs(shot, limit=3, frame_desc=frame_desc, characters=characters)

    # G3: inject global style_anchor.png when portraits are missing/unregistered
    # so cross-chapter text_only renders keep the book's locked visual style.
    style_anchor_used = False
    if missing_refs or not portrait_refs:
        _sa = _resolve_style_anchor_path()
        if _sa and _sa not in scene_refs:
            scene_refs = list(scene_refs) + [_sa]
            style_anchor_used = True

    bucket_list = [("scene", list(scene_refs) + list(space_refs)),
                   ("chars", list(portrait_refs)),
                   ("props", list(prop_refs))]
    if return_buckets:
        return {
            "refs": None,
            "buckets": bucket_list,
            "omissions": omissions,
            "missing": missing_refs,
            "identity_reference": id_ref_state,
            "style_anchor_used": style_anchor_used,
        }
    return _compose_frame_refs_by_category(
        shot, for_frame, scene_refs, space_refs, portrait_refs, prop_refs, limit=limit,
    )






















def resolve_character_portrait_assets(
    role: str,
    *,
    characters: list | None = None,
    reg: dict | None = None,
    bank=None,
    bank_dir: Path | None = None,
    view_pref: str = "front",
    closeup: bool = False,
    allow_face_fallback: bool = False,
) -> dict:
    """Pure, side-effect-free, read-only portrait resolver shared by Gate & Collector.

    Never creates directories, crops images, stitches sheets, or mutates disk state.
    Returns:
        {character_id, resolved_views, selected_view, selected_path,
         readable, approved, identity_match, missing_reason}
    """
    raw_role = str(role or "").strip()
    if not raw_role:
        return {
            "character_id": "",
            "resolved_views": {},
            "selected_view": "",
            "selected_path": "",
            "readable": False,
            "approved": False,
            "identity_match": False,
            "missing_reason": "portrait_not_registered",
        }

    if reg is None and bank is None and bank_dir is None:
        reg, bank, bank_dir = _load_portrait_stores_readonly()
    elif bank_dir is None:
        try:
            from shared.paths import characters_bank_dir
            bank_dir = characters_bank_dir()
        except Exception:
            bank_dir = None

    reg_dict = reg if isinstance(reg, dict) else {}

    # Resolve canonical character_id from explicit characters list / registry keys
    known_ids: list[str] = []
    seen_ids: set[str] = set()
    for c in (characters or []):
        if isinstance(c, dict):
            cid = str(c.get("identifier") or c.get("name") or "").strip()
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                known_ids.append(cid)
    for k in reg_dict:
        ks = str(k or "").strip()
        if ks and ks not in seen_ids:
            seen_ids.add(ks)
            known_ids.append(ks)

    alias_holders: dict[str, list[str]] = {}
    for cid in known_ids:
        for a in _char_aliases(cid, characters):
            alias_holders.setdefault(a, [])
            if cid not in alias_holders[a]:
                alias_holders[a].append(cid)

    if raw_role in seen_ids:
        char_id = raw_role
    else:
        holders = alias_holders.get(raw_role) or []
        if len(holders) > 1:
            return {
                "character_id": raw_role,
                "resolved_views": {},
                "selected_view": "",
                "selected_path": "",
                "readable": False,
                "approved": False,
                "identity_match": False,
                "missing_reason": "ambiguous_character_alias",
            }
        char_id = holders[0] if len(holders) == 1 else _norm_portrait_char_name(raw_role)

    aliases = _char_aliases(char_id, characters)
    bank_entry = bank.get_character(char_id) if bank is not None and hasattr(bank, "get_character") else None

    # Gather view metadata entries from registry first, or bank_dir if unregistered in reg
    views_raw = reg_dict.get(char_id)
    if views_raw is None:
        norm_cid = _norm_portrait_char_name(char_id)
        for rk, rv in reg_dict.items():
            if _norm_portrait_char_name(str(rk or "")) == norm_cid:
                views_raw = rv
                break
    if views_raw is None and isinstance(bank_entry, dict) and isinstance(bank_entry.get("portraits"), dict):
        views_raw = bank_entry.get("portraits")

    view_entries: dict[str, dict] = {}
    if isinstance(views_raw, dict):
        for vk in ("front", "base", "side", "back", "face"):
            ev = views_raw.get(vk)
            if isinstance(ev, dict) and str(ev.get("path") or "").strip():
                view_entries[vk] = dict(ev)
            elif isinstance(ev, str) and ev.strip():
                view_entries[vk] = {"path": ev.strip()}
    if not view_entries and bank_dir is not None:
        cdir = Path(bank_dir) / char_id
        if cdir.is_dir():
            for vk in ("front", "base", "side", "back", "face"):
                cand = cdir / f"{vk}.png"
                if cand.is_file():
                    view_entries[vk] = {"path": str(cand)}

    if not view_entries:
        return {
            "character_id": char_id,
            "resolved_views": {},
            "selected_view": "",
            "selected_path": "",
            "readable": False,
            "approved": False,
            "identity_match": False,
            "missing_reason": "portrait_not_registered",
        }

    # Explicitly enforce view contract priority
    if closeup:
        order = ["face", "front", "base", "side", "back"]
    elif view_pref in ("side", "back"):
        order = [view_pref, "front", "base", "side", "back"]
    elif allow_face_fallback:
        order = ["front", "base", "side", "back", "face"]
    else:
        order = ["front", "base", "side", "back"]

    # Deduplicate priority order preserving order
    ordered_views: list[str] = []
    for vk in order:
        if vk not in ordered_views:
            ordered_views.append(vk)

    resolved_views: dict[str, str] = {}
    for vk, ev in view_entries.items():
        p = Path(str(ev.get("path") or ""))
        if p.is_file():
            resolved_views[vk] = str(p)

    # Whole-character bank disapproval blocks immediately
    if isinstance(bank_entry, dict) and (
        bank_entry.get("approved") is False
        or str(bank_entry.get("status") or "").strip().lower() in _UNAPPROVED_PORTRAIT_STATUSES
    ):
        return {
            "character_id": char_id,
            "resolved_views": resolved_views,
            "selected_view": "",
            "selected_path": "",
            "readable": any(_is_image_file_readable_readonly(Path(p)) for p in resolved_views.values()),
            "approved": False,
            "identity_match": True,
            "missing_reason": "portrait_unapproved",
        }

    saw_missing = False
    saw_unreadable = False
    saw_identity_mismatch = False
    saw_unapproved = False

    for vk in ordered_views:
        ev = view_entries.get(vk)
        if not ev:
            continue
        p = Path(str(ev.get("path") or ""))
        if not p.is_file():
            saw_missing = True
            continue
        if not _portrait_identity_ok(char_id, aliases, ev, p, bank_dir):
            saw_identity_mismatch = True
            continue
        if not _portrait_approved_ok(ev, bank_entry, p):
            saw_unapproved = True
            continue
        if not _is_image_file_readable_readonly(p):
            saw_unreadable = True
            continue
        return {
            "character_id": char_id,
            "resolved_views": resolved_views,
            "selected_view": vk,
            "selected_path": str(p),
            "readable": True,
            "approved": True,
            "identity_match": True,
            "missing_reason": "",
        }

    # Check if only face exists while non-closeup primary view was required
    only_face_present = (
        "face" in view_entries
        and not any(k in view_entries for k in ("front", "base", "side", "back"))
        and "face" not in ordered_views
    )
    if saw_identity_mismatch:
        reason = "portrait_identity_mismatch"
    elif saw_unapproved:
        reason = "portrait_unapproved"
    elif saw_unreadable:
        reason = "portrait_unreadable"
    elif only_face_present:
        reason = "portrait_role_insufficient"
    elif saw_missing or not resolved_views:
        reason = "portrait_file_missing"
    else:
        reason = "portrait_file_missing"

    return {
        "character_id": char_id,
        "resolved_views": resolved_views,
        "selected_view": "",
        "selected_path": "",
        "readable": False if saw_unreadable else bool(resolved_views),
        "approved": not saw_unapproved,
        "identity_match": not saw_identity_mismatch,
        "missing_reason": reason,
    }


def _collect_portrait_refs_for_shot(shot: dict, characters: list | None = None,
                                    limit: int = 3,
                                    frame_desc: str | None = None,
                                    roster: set[str] | None = None,
                                    return_details: bool = False,
                                    closeup: bool = False,
                                    apply_head_crop: bool = True,
                                    create_head_crop: bool = True):
    """为本镜头出镜角色收集肖像参考图（与 pre-shot 门禁共享 resolve_character_portrait_assets）.

    S04/F5：当 roster 非 None 时，按结构化名册与唯一别名解析，统一消费只读
    resolve_character_portrait_assets() 完成视图角色、可读性、身份与审批检查。
    """
    def _pack(refs_out: list[str], *, omissions=None, missing=None,
              identity_reference: str = "verified"):
        if return_details:
            return {
                "refs": refs_out,
                "omissions": list(omissions or []),
                "missing": list(missing or []),
                "identity_reference": identity_reference,
            }
        return refs_out

    try:
        reg, bank, bank_dir = _load_portrait_stores_readonly()
        if not reg and (bank_dir is None or not Path(bank_dir).is_dir() or not any(Path(bank_dir).iterdir())):
            return _pack([], identity_reference="unverified")

        if roster is not None and len(roster) == 0:
            return _pack(
                [],
                omissions=[{"kind": "chars", "reason": "all_offscreen"}],
                identity_reference="empty_roster",
            )

        search_text = " ".join([
            shot.get("ff_desc", "").strip() or "",
            shot.get("motion_desc", "").strip() or shot.get("prompt", ""),
            shot.get("lf_desc", "").strip() or "",
        ])
        if roster is None and not search_text:
            return _pack([], identity_reference="unverified")

        view_pref = "front"
        for kw in ("背面", "背影", "背向", "背对", "背身", "后背", "背朝"):
            if kw in search_text:
                view_pref = "back"
                break
        for kw in ("侧身", "侧面", "侧脸", "侧目", "半侧", "侧对", "侧立"):
            if kw in search_text:
                view_pref = "side"
                break

        action_subject = _detect_action_subject(shot, characters)
        subject_view = None
        if action_subject:
            _s_idx = search_text.find(action_subject)
            if _s_idx >= 0:
                _tail = search_text[_s_idx:_s_idx + 30]
                from shared.style_prefix import ORIENTATION_KW
                if any(kw in _tail for kw in ORIENTATION_KW):
                    subject_view = "side"

        char_names: list[str] = []
        seen = set()
        for c in (characters or []):
            ident = c.get("identifier", "")
            if ident and ident not in seen:
                seen.add(ident)
                char_names.append(ident)
        for ident in (reg or {}):
            if ident not in seen:
                seen.add(ident)
                char_names.append(ident)

        if action_subject and action_subject in char_names:
            char_names.remove(action_subject)
            char_names.insert(0, action_subject)

        alias_holders: dict[str, list[str]] = {}
        for ident in char_names:
            for a in _char_aliases(ident, characters):
                alias_holders.setdefault(a, [])
                if ident not in alias_holders[a]:
                    alias_holders[a].append(ident)

        refs: list[str] = []
        omissions: list[dict] = []
        missing: list[dict] = []
        if roster is not None:
            for rname in sorted(roster):
                holders = alias_holders.get(rname) or []
                if rname not in holders and len(holders) > 1:
                    missing.append({
                        "entity": rname,
                        "reason": "ambiguous_character_alias",
                        "candidates": list(holders),
                    })

        for ident in char_names:
            aliases = _char_aliases(ident, characters)
            if roster is not None:
                matched_roster = [
                    a for a in aliases
                    if a in roster and (a == ident or len(alias_holders.get(a) or []) <= 1)
                ]
                if not matched_roster:
                    omissions.append({"kind": "chars", "entity": ident, "reason": "off_screen"})
                    continue
            else:
                if not any(a in search_text for a in aliases):
                    continue
                if frame_desc is not None and ident in _THREAT_IDS \
                        and _threat_visible_in(frame_desc, characters) is not True:
                    omissions.append({"kind": "chars", "entity": ident, "reason": "threat_not_visible"})
                    continue

            _ident_view = subject_view if ident == action_subject else view_pref
            resolved = resolve_character_portrait_assets(
                ident,
                characters=characters,
                reg=reg,
                bank=bank,
                bank_dir=bank_dir,
                view_pref=_ident_view or "front",
                closeup=closeup,
            )
            picked = str(resolved.get("selected_path") or "")
            if not picked:
                missing.append({
                    "entity": ident,
                    "reason": str(resolved.get("missing_reason") or "portrait_file_missing"),
                })
                continue
            if picked and ident in _THREAT_IDS:
                _nw = Path(picked).parent / f"{Path(picked).stem}_no_weapon.png"
                if _nw.exists() and _is_image_file_readable_readonly(_nw):
                    picked = str(_nw)
                    _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: {ident} 使用无武器版立绘 {_nw.name}")
            if picked and apply_head_crop:
                if create_head_crop:
                    picked = _head_portrait_path(picked)
                else:
                    try:
                        picked = _head_portrait_path(picked, create_if_missing=False)
                    except TypeError:
                        picked = _head_portrait_path(picked)
            if picked and picked not in refs:
                if len(refs) >= limit:
                    omissions.append({"kind": "chars", "entity": ident, "reason": "portrait_limit"})
                    continue
                refs.append(picked)
                _bridge_log(f"  [bridge] 镜头{shot.get('index','?')}: 首帧/尾帧参考图=角色 {ident} {resolved.get('selected_view') or view_pref}肖像")
        id_state = "verified" if refs else ("missing" if missing else "unverified")
        return _pack(refs, omissions=omissions, missing=missing, identity_reference=id_state)
    except Exception as e:
        _bridge_log(f"  [bridge] 肖像参考图收集异常: {e}")
        return _pack([], missing=[{"reason": "portrait_collect_error", "detail": str(e)}],
                     identity_reference="error")


# ---------------------------------------------------------------------------
# 动作镜头判定
#
# v6.19 注：action_ref 动作姿态参考图已废弃（code-audit 2026-08-08）——
# 前一刻改造后首帧是静态画面（潜伏水下/水面微澜），静态肖像作参考图锚定
# 身份即可；「破水而出」等动作描述无法翻译成可画的静态姿势，生成的
# action_ref 姿态怪异（半只脚出水/双手伸开），且与首帧/尾帧状态冲突。
# 参考图直接用角色肖像 + 场景/道具资产（gen_shot_frames）。
# ---------------------------------------------------------------------------









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
    THREAT_VISIBILITY_KEYWORDS as _THREAT_VISIBILITY_KEYWORDS,
    THREAT_CORE_IDENTIFIERS as _THREAT_CORE_IDENTIFIERS,
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
    for c in (characters or []):
        if isinstance(c, dict) and str(c.get("identifier") or "").strip() == char_id:
            for a in (c.get("aliases") or []):
                a = str(a or "").strip()
                if a:
                    aliases.append(a)
            break
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
_THREAT_IDS = _THREAT_CORE_IDENTIFIERS  # 单源 registry.THREAT_CORE_IDENTIFIERS（2026-10 裁决）

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
    if not shot:
        return ""
    if not characters and not (frame_kind and isinstance(shot.get("frame_layouts"), dict)):
        return ""
    characters = characters or []
    try:
        if is_empty_scene(shot, characters):
            return ""
        # 角色判定文本：role_scan_text 优先（关键帧合并全文），否则合并 shot 字段
        scan = role_scan_text or " ".join(filter(None, [
            shot.get("ff_desc", ""), shot.get("lf_desc", ""),
            shot.get("motion_desc", "") or shot.get("prompt", ""),
        ]))
        if not scan and not (frame_kind and isinstance(shot.get("frame_layouts"), dict)):
            return ""

        # ── v6.61 spec 驱动出镜表（新章；与 _inject_frame_spec/QC 同源）──
        # onstage/on_screen 决定谁进锚定块；威胁源可见性由 spec 判定，
        # 不再走合并 scan 的 _threat_visible_in（旧路径误判 lf 可见）。
        _fl = shot.get("frame_layouts")
        _spec_items: list | None = None
        if frame_kind and isinstance(_fl, dict):
            try:
                from shared.frame_spec import get_frame_spec, is_on_screen
                _spec_items = [
                    s for s in get_frame_spec(shot, frame_kind, kf_index)
                    if is_on_screen(s)
                ]
            except Exception:
                _spec_items = None
        _spec_extra_items: list[dict] = []
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
            # S23-4：spec 里 on_screen=True 但不在 characters 的非注册角色
            # （远景农人/群演）也要进【角色锚定】，否则正文有、锚定块漏。
            _registered_aliases: set[str] = set()
            for _c in characters:
                _cid = str(_c.get("identifier", "")).strip()
                if _cid:
                    _registered_aliases.update(_char_aliases(_cid, characters))
            _spec_extra_items = [
                _si for _si in _spec_items
                if str(_si.get("name", "")).strip()
                and str(_si.get("name", "")).strip() not in _registered_aliases
            ]
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
        if not onstage and not _spec_extra_items:
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
        # v6.63：船上容量锚——不依赖 scene_objects/location 是否带「船」字。
        # frame spec 已写明谁在船板/船头/船尾；同船 ≥2 人时，船宽必须按
        # 实际人数和坐/站姿态锚定，否则图片模型会把船画小，站者被挤到船外。
        _BOAT_SURFACE_WORDS = ("船板", "甲板", "船舱", "舱内", "船头", "船尾", "船舷", "船侧")
        _BOAT_WATER_WORDS = ("水面", "水中", "水下", "半身", "潜")
        _boat_bound: list[dict] = []
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
                # spec 模式下 pose 是唯一事实源。spec pose 为空时也必须清空
                # scan 提取值，否则合并全文里的其他帧姿态会串进本帧。
                pose = str(_spec_item.get("pose", "") or "").strip()
            seg = ident
            if pose:
                seg += f"，{pose}"
            if layout_item:
                pos = str(layout_item.get("position", "")).strip()
                # S25：facing 只读显式字段/gaze_target，绝不再把 pose 拷进
                # facing（旧代码造成“盘坐持箫（盘坐持箫）”重复与无标点粘连）。
                facing = str(layout_item.get("facing", "")).strip()
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
                    seg += f"，{surface}"
                if surface and f"在{surface}" not in seg:
                    seg += f"（在{surface}上）"
                if pos:
                    seg += f"，位于{pos}"
                if facing:
                    seg += f"（{facing}）"
            else:
                pos = _extract_kf_position(scan, ident)
                if pos:
                    seg += (f"，位于{pos}" if pose else pos)
                # 朝向：extract_orientation 有(主体,目标) → 面向{目标}
                from shared.style_prefix import extract_orientation
                try:
                    orient = extract_orientation(scan, characters)
                    for subject, target in orient:
                        if subject == ident:
                            seg += f"（面向{target}）"
                            break
                except Exception as _degrade_exc:
                    from shared.degrade import note_degraded as _note_degraded
                    _note_degraded("shared/vimax_bridge.py:_extract_char_layout", _degrade_exc)
            # v6.52 前景体型锚：前景机位 + 动态入场姿态（探出/跃上/扑出/破水…）
            # 或威胁源时，图片模型按前景透视把该角色画成巨人（hook ff 神秘人
            # 手臂比船宽还长实测）。相对体型锚压回正常人体比例。只挂本角色
            # seg 末尾，中景/背景条目不受影响。动态词查 scan 全文而非姿态表——
            # 跃上/扑出/破水这类动词不在 _KF_POSE_STATES 里，但恰是巨人化重灾。
            if ("前景" in (pos or "")) and (
                    any(w in scan for w in _FG_DYNAMIC_WORDS) or ident in _THREAT_IDS):
                seg += "（体型与画面其他人物相当，透视正常，非巨人）"
            parts.append(seg)
            _boat_probe = f"{pose} {pos}"
            if (any(w in _boat_probe for w in _BOAT_SURFACE_WORDS)
                    and not any(w in _boat_probe for w in _BOAT_WATER_WORDS)):
                _boat_bound.append({"name": ident, "pose": pose, "position": pos})

        # S23-4：非注册但本帧 on_screen 的 spec 条目（远景农人/群演）补进
        # 【角色锚定】，否则正文有、锚定块漏。只拼该条目自己的 pose/position/
        # gaze，不套 char_layout 的 surface 逻辑。
        for _si in _spec_extra_items:
            _name = str(_si.get("name", "")).strip()
            if not _name:
                continue
            _pose = str(_si.get("pose", "") or "").strip()
            _pos = str(_si.get("position", "") or "").strip()
            _gaze = str(_si.get("gaze_target", "") or "").strip()
            _seg = _name
            if _pose:
                _seg += f"，{_pose}"
            if _pos:
                _seg += f"，位于{_pos}"
            if _gaze and _gaze not in ("无", "无视线", ""):
                _seg += f"（目光看向{_gaze}）"
            parts.append(_seg)
            _extra_probe = f"{_pose} {_pos}"
            if (any(w in _extra_probe for w in _BOAT_SURFACE_WORDS)
                    and not any(w in _extra_probe for w in _BOAT_WATER_WORDS)):
                _boat_bound.append({"name": _name, "pose": _pose, "position": _pos})

        if not parts:
            return ""
        block = "【角色锚定】" + "，".join(parts)

        # S25：先读显式 support 合同；它非空时，下面的 boat 兼容分支必须让位。
        _support_rows = []
        for _si in (_spec_items or []):
            _sup = _si.get("support")
            if isinstance(_sup, dict):
                _sid = str(_sup.get("id", "") or "").strip()
                _skind = str(_sup.get("kind", "") or "").strip()
                _ssurf = str(_sup.get("surface", "") or "").strip()
            else:
                _sid = str(_si.get("support_id", "") or "").strip()
                _skind = str(_si.get("support_kind", "") or "").strip()
                _ssurf = str(_si.get("support_surface", "") or "").strip()
            _sname = str(_si.get("name", "") or "").strip()
            if _sname and (_sid or _skind or _ssurf):
                _support_rows.append((_sname, _sid, _skind, _ssurf))

        # S25：船上约束只绑定本帧 spec 明确在船上的角色，不再用“所有角色/
        # 其余角色”概括；容量也不从人数直接推“并排”或“同一甲板平面”。
        _boat_names = [str(x.get("name") or "").strip()
                       for x in _boat_bound if str(x.get("name") or "").strip()]
        _boat_names = list(dict.fromkeys(_boat_names))
        if not _support_rows and _boat_names and "各自所在船" not in block:
            # 不按人数推“同船/并排”：多船、前后排列、不同甲板都合法。
            block += (f"。⚠️ 船上角色{'、'.join(_boat_names)}各自所在船的船板宽度"
                      "至少为单人居坐宽度的2.5倍；不同船分别计算，不合并；"
                      "按前后/并排实际站位留出落脚空档，不要求并排，"
                      "也不强制同一甲板平面；各自鞋底/裙摆在本船船板内侧")

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

        # v6.53.1：收集「水面角色」（surface/pose 表明身体在水里）。S25 已
        # 去掉“所有角色必须在船上”，改为船上/岸上/水中分句，避免与
        # desc「神秘人半身探出水面」或岸上老农直接矛盾。
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

        # S25：船上/岸上/水中角色分句，绑定具体角色，不写“所有/其余”。
        _all_stage_names = [str(c.get("identifier", "")).strip() for c in onstage]
        _all_stage_names += [str(_si.get("name", "")).strip() for _si in _spec_extra_items]
        _all_stage_names = [n for n in dict.fromkeys(_all_stage_names) if n]
        _shore_names = [n for n in _all_stage_names
                        if n not in _boat_names and n not in _water_names]

        def _boat_scope_clause() -> str:
            parts = []
            if _boat_names:
                parts.append(
                    "船上角色" + "、".join(_boat_names)
                    + "各自落在本船船板/甲板内侧，鞋底与裙摆不越出船体轮廓，"
                      "禁止站在船外水面或荷叶中")
            if _shore_names:
                parts.append(
                    "岸上/远景角色" + "、".join(_shore_names)
                    + "保持原位，不得被拉上船或改到水面")
            if _water_names:
                parts.append(
                    "水中角色" + "、".join(_water_names)
                    + "保持水中/原有承载面，不得改成站在船上")
            if not parts:
                return ""
            return "。⚠️ 各船船头船尾齐全不缺失；" + "；".join(parts)

        # S25：船体完整性只补“完整小舟”；角色约束由 _boat_scope_clause
        # 按本帧船上/岸上/水中名单分别绑定，不再写“所有角色/其余角色”。
        _boat_scan = " ".join(filter(None, [
            str(shot.get("location", "") or ""), str(shot.get("scene", "") or ""),
            str(shot.get("main_subject", "") or ""),
            str(shot.get("ff_desc", "") or ""), str(shot.get("lf_desc", "") or ""),
            str(shot.get("motion_desc", "") or shot.get("prompt", "") or ""),
        ]))
        _on_boat_scene = any(w in _boat_scan for w in ("船", "舟", "舱", "甲板"))
        if (not _support_rows and _on_boat_scene
                and (_boat_bound or _water_names)
                and "各船船头船尾" not in block):
            block += _boat_scope_clause()

        # S25 通用实体—承载面合同：由 spec 显式 support 字段驱动，不限于船。
        # 不按人数推并排/宽度/共面；只陈述“谁在哪个承载实体的哪个面”。
        if _support_rows and "承载关系" not in block:
            from collections import Counter as _Counter
            _sid_counts = _Counter(_sid for _, _sid, _, _ in _support_rows if _sid)
            _support_parts = []
            for _sname, _sid, _skind, _ssurf in _support_rows:
                _label = _skind or "承载面"
                # 不把内部 ASCII id 拼进生图 Prompt：同一承载实体 ≥2 人时
                # 用“同一{_label}”表达共享关系；单人只写承载面类别。
                if _sid and _sid_counts[_sid] >= 2:
                    _ref = f"同一{_label}"
                else:
                    _ref = _label
                _support_parts.append(
                    f"{_sname}位于{_ref}{('的' + _ssurf) if _ssurf else ''}")
            block += ("。⚠️ 承载关系：" + "；".join(_support_parts)
                      + "（各自双脚/坐姿与衣摆底端均处于对应承载面边界内侧，"
                        "不落入承载面外侧）")
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




def _extract_prop_layout(
    shot: dict,
    characters: list | None = None,
    role_scan_text: str | None = None,
    frame_kind: str | None = None,
    kf_index: int | None = None,
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
        # 幂等：desc 已含"【道具锚定】" → 返回 ""（防多轮组装堆叠）。
        # 必须放在 spec 直读之前，否则 spec 路径会绕过它重复加标题。
        if "【道具锚定】" in scan:
            return ""

        # S25：frame_layouts.{ff/lf}[].props 是“谁持有什么”的结构化事实源，
        # 优先直读，不依赖正式目录 script_file() 是否存在。
        if frame_kind:
            try:
                from shared.prompt_facts import project_frame_facts
                _proj_props = project_frame_facts(shot, frame_kind, characters, kf_index=kf_index)
                _layout_parts = []
                for _ent in (_proj_props.get("entities") or []):
                    _name = str(_ent.get("name") or "").strip()
                    if not _name:
                        continue
                    for _prop in ((_ent.get("current") or {}).get("props") or []):
                        _prop = str(_prop).strip()
                        if not _prop:
                            continue
                        _hand = ""
                        _pp = -1
                        for _pt in _prop_terms(_prop):
                            _pp = scan.find(_pt)
                            if _pp >= 0:
                                break
                        if _pp >= 0:
                            _pre = scan[max(0, _pp - 8):_pp]
                            for _h in ("左手", "右手", "双手"):
                                if _h in _pre:
                                    _hand = _h
                                    break
                        _layout_parts.append(
                            f"{_prop}在{_name}{_hand}" if _hand else f"{_name}手持{_prop}")
                if _layout_parts:
                    return "【道具锚定】" + "，".join(dict.fromkeys(_layout_parts))
            except Exception:
                pass

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
        if not isinstance(prop_assets, dict):
            prop_assets = {}

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
            # S25：后向窗口遇到逗号/句号/分号立即截断——旧实现让
            # “左手持钓杆，右手提酒壶”中“钓杆”跨过逗号绑到“右手”。
            _post_limit = min(next_prop_pos, end_pos + 8)
            for _punct in ("，", ",", "。", "；", ";", "！", "？"):
                _pp = scan.find(_punct, end_pos, _post_limit)
                if _pp >= 0:
                    _post_limit = _pp
            for w in _PROP_POSITION_WORDS:
                pre_idx = scan.rfind(w, max(0, hit_pos - 12), hit_pos)
                if pre_idx >= 0:
                    # 距离按“方位词词尾 → 道具词首”算，且方位词前紧邻
                    # 站/立/坐/跪等姿态动词时，该词是角色站位，不是道具落点。
                    _pre_gap = scan[pre_idx + len(w):hit_pos]
                    _pre_ctx = scan[max(0, pre_idx - 4):pre_idx]
                    if any(v in (_pre_gap + _pre_ctx) for v in
                           ("站", "立", "坐", "跪", "躺", "卧", "伏")):
                        continue
                    d = hit_pos - (pre_idx + len(w))
                    if d < best_dist:
                        best_dist, pos_word = d, w
                # 后向只找到下一个道具名、+8 或标点为止（不跨句/跨道具捞方位）
                post_idx = scan.find(w, end_pos, _post_limit)
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
                        f"禁止护手/刀把/刀身露出水面；{_tip}必须正对画左目标角色方向")
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
            # Current-frame explicit costume/state wins over the cross-chapter
            # bank description.  Stable facial features still come from static.
            if dynamic and _current_frame_has_costume(desc, ident, characters):
                dynamic = ""
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
            additions, guard_sentence = _dedupe_char_anchor_guard(additions)
            # v6.50：desc 尾部句号 + 本处固定 "。" → 双句号（矛盾门会拦），先剥再拼；
            # F6：共享头面守卫作为独立帧级全局句子放在角色列表之外，不粘在最后一个角色末尾。
            out = f"{desc.rstrip('。')}。{('，'.join(additions))}"
            if guard_sentence:
                out = f"{out.rstrip('。')}。{guard_sentence}"
            return out
        return desc
    except Exception:
        return desc




def _current_frame_has_costume(desc: str, ident: str, characters: list | None) -> bool:
    """Whether this frame already specifies this character's costume/state."""
    if not desc or not ident:
        return False
    import re as _re_costume
    for alias in _char_aliases(ident, characters):
        for match in _re_costume.finditer(re.escape(alias), desc):
            window = desc[max(0, match.start() - 20):match.end() + 50]
            has_color = any(w in window for w in _CURRENT_FRAME_COLOR_WORDS)
            has_garment = (
                any(w in window for w in _COSTUME_WORDS)
                or any(w in window for w in ("劲装", "铠甲", "女装", "男装", "扮相"))
            )
            if has_color and has_garment:
                return True
            if any(w in window for w in ("伪装", "换上", "披头散发", "血染", "湿透")):
                return True
    return False














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
    if not desc or not shot:
        return desc
    characters = characters or []
    try:
        if is_empty_scene(shot, characters):
            return desc
        from shared.prompt_facts import project_frame_facts
        proj = project_frame_facts(shot, frame_kind, characters, kf_index=kf_index)
        # 老章降级（spec 为空——无 frame_layouts / 无 char_layout）
        if not proj.get("spec_present"):
            return _inject_character_costume(
                desc, characters, shot,
                frame_kind=frame_kind,
            )
        entities = proj.get("entities") or []
        if not entities:
            return desc
        _non_violent_frame = (
            _threat_visible_in(desc, characters) is None
            and not any(w in desc for w in WEAPON_WORDS)
            and not any(w in desc for w in _INERT_STATE_WORDS)
        )
        additions = []
        for ent in entities:
            ident = str(ent.get("name") or "").strip()
            if not ident:
                continue
            current = ent.get("current") or {}
            static = str(ent.get("identity") or "").strip()
            bank_dynamic = str(ent.get("dynamic_bank") or "").strip()
            explicit_states = current.get("states")
            _current_state_text = " ".join(filter(None, [
                str(current.get("pose") or ""),
                str(current.get("position") or ""),
                " ".join(str(p) for p in (current.get("props") or [])),
            ])) or desc

            if isinstance(explicit_states, list):
                bank_costume = _select_states_by_contract(bank_dynamic, explicit_states)
                if bank_costume and _current_frame_has_costume(desc, ident, characters):
                    bank_costume = ""
                if bank_costume and (
                    frame_kind == "first_frame"
                    or _non_violent_frame
                    or _char_inert_in_shot(ident, desc, characters)
                ):
                    bank_costume = _strip_frame_action_result(bank_costume)
                extra_states = [
                    str(st).strip() for st in explicit_states
                    if str(st or "").strip()
                    and str(st).strip() not in bank_costume
                    and str(st).strip() not in desc
                ]
                if not static and not bank_costume and not extra_states:
                    continue
                if static or bank_costume:
                    combined = _build_char_anchor(ident, static, bank_costume)
                    if extra_states:
                        combined = f"{combined}，{'，'.join(extra_states)}"
                else:
                    combined = f"{ident}，{'，'.join(extra_states)}"
            else:
                if not static and not bank_dynamic:
                    continue
                dynamic = _strip_later_timeline_clauses(bank_dynamic, desc, _current_state_text)
                if dynamic and _current_frame_has_costume(desc, ident, characters):
                    dynamic = ""
                if frame_kind == "first_frame" or _non_violent_frame \
                        or _char_inert_in_shot(ident, desc, characters):
                    dynamic = _strip_frame_action_result(dynamic)
                    if not dynamic and not static:
                        continue
                combined = _build_char_anchor(ident, static, dynamic)

            if combined in desc:
                continue
            additions.append(combined)
        if additions:
            additions, guard_sentence = _dedupe_char_anchor_guard(additions)
            out = f"{desc.rstrip('。')}。{('，'.join(additions))}"
            if guard_sentence:
                out = f"{out.rstrip('。')}。{guard_sentence}"
            return out
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
from shared.content_safety import (
    WEAPON_WORDS,
    strip_action_result as _safety_strip_action_result,
)




# 尾帧目光确定性修复：错别字 + 看向动作结果对象
_LF_GAZE_TYPO = (
    ("倒倒", "紧紧"), ("倒到", "紧紧"),
    ("望望", "望着"), ("看看", "看着"),
)


def fix_lf_desc_gaze(lf_desc: str, shot: dict | None = None,
                     characters: list | None = None) -> str:
    """Normalize known gaze typos without changing the gaze target.

    The previous implementation changed a character's gaze to the wounded
    victim based on scene heuristics.  That is a semantic rewrite after the
    step01 audit and is intentionally not performed here.  Gaze-target
    conflicts are reported by validation and must be repaired by the LLM from
    the original story evidence.  Only typo correction is lossless.
    """
    if not lf_desc:
        return lf_desc
    # 1. 错别字修复（叠字错写）
    text = lf_desc
    for old, new in _LF_GAZE_TYPO:
        text = text.replace(old, new)
    return text


def _build_final_frame_prompt(shot: dict, desc: str, frame_kind: str,
                              characters: list | None = None) -> str:
    """QC 与生成、预检使用同一提示组装入口，包括逐帧承载关系。"""
    return compose_frame_prompt(shot, characters, frame_kind, desc=desc)















def compose_frame_prompt(shot: dict, characters: list | None, frame_kind: str,
                         *, desc: str | None = None,
                         kf_index: int | None = None) -> str:
    """帧 prompt 唯一组装入口：生成链与 --plan-only 预检共用同一份逻辑.

    S04：预检不得另拼一份 prompt，否则“看到的”和“发出去的”会漂移。
    """
    chars = characters or []
    if frame_kind == "first_frame":
        base = desc if desc is not None else (shot.get("ff_desc") or shot.get("prompt") or "")
        text = preprocess_prompt_for_image(base)
        text = _inject_frame_spec_and_closeup(text, shot, chars, frame_kind="first_frame")
        text = _inject_object_size_anchor(shot, text)
        text = _inject_tone_anchor(shot, text)
        anchor = _extract_char_layout(shot, chars,
                                      role_scan_text=shot.get("ff_desc", ""),
                                      water_state=False, frame_kind="first_frame")
        if anchor:
            text = f"{text}。{anchor}"
        prop = _extract_prop_layout(shot, chars, role_scan_text=shot.get("ff_desc", ""),
                                    frame_kind="first_frame")
        if prop:
            text = f"{text}。{prop}"
        text = sanitize_prompt(ensure_prefix(text, style_prefix_for_canvas(frame_canvas_ratio(), orient=True)), verbose=False)
        return _dedupe_prompt_clauses(
            add_image_strict_constraints(text, "首帧", shot=shot, characters=chars))

    if frame_kind == "last_frame":
        base = desc if desc is not None else (shot.get("lf_desc") or shot.get("prompt") or "")
        text = preprocess_prompt_for_image(base)
        _ff_core = str(shot.get("ff_desc") or "").strip()
        _lf_core = str(shot.get("lf_desc") or "").strip()
        if desc is None and _ff_core and _lf_core and _ff_core != _lf_core:
            # 正文 base 已经是 lf_desc；这里只加状态差量指令，避免整段 lf_desc
            # 重复注入触发 prompt 矛盾/重复检测。
            text = ("【尾帧终态（须与首帧起始态区分）】画面按本尾帧终态演变，"
                    "禁止复刻首帧姿态。\n%s" % text)
        text = _inject_frame_spec_and_closeup(text, shot, chars, frame_kind="last_frame")
        text = fix_lf_desc_gaze(text, shot, chars)
        text = _inject_object_size_anchor(shot, text)
        text = _inject_tone_anchor(shot, text)
        anchor = _extract_char_layout(shot, chars,
                                      role_scan_text=shot.get("lf_desc", ""),
                                      water_state=True, frame_kind="last_frame")
        if anchor:
            text = f"{text}。{anchor}"
        prop = _extract_prop_layout(shot, chars, role_scan_text=shot.get("lf_desc", ""),
                                    frame_kind="last_frame")
        if prop:
            text = f"{text}。{prop}"
        scene = shot.get("scene", "") or ""
        location = shot.get("location", "") or ""
        from shared.style_prefix import visual_location_hint
        vis_loc = visual_location_hint(location or scene, shot)
        ctx = f"（环境：{vis_loc}）" if vis_loc else ""
        camera = str(shot.get("camera_movement") or "")
        from shared.shot_motion_checks import is_fixed_camera
        if is_fixed_camera(camera):
            # 固定机位只约束相机：沿用首帧机位/景别、相机不移动；人物、道具与
            # 环境的位置按本镜尾帧描述演进。
            text = (f"{text}{ctx}"
                    f"，机位固定：沿用首帧的同一机位与景别，相机不移动；"
                    f"人物、道具与环境的位置按尾帧描述的动作终态演进"
                    f"{_fixed_camera_shift_anchor(base)}。")
        else:
            text = (f"{text}{ctx}"
                    f"，构图需按尾帧描述重新布局，"
                    f"可与首帧/参考图的景别、机位、人物位置不同，表现动作演变的最终状态。")
        text = sanitize_prompt(ensure_prefix(text, style_prefix_for_canvas(frame_canvas_ratio(), orient=True)), verbose=False)
        return _dedupe_prompt_clauses(
            add_image_strict_constraints(text, "尾帧", shot=shot, characters=chars))

    if frame_kind == "keyframe":
        kfs = shot.get("keyframes") or []
        kf_raw = ""
        if desc is not None:
            base = str(desc)
            kf_raw = base
        elif kf_index is not None and 0 <= kf_index < len(kfs) and isinstance(kfs[kf_index], dict):
            base = str(kfs[kf_index].get("desc") or "").strip()
            kf_raw = base
        else:
            base = str(shot.get("prompt") or "").strip()
            kf_raw = base
        text = preprocess_prompt_for_image(base)
        text = _inject_frame_spec_and_closeup(
            text, shot, chars, frame_kind="keyframe", kf_index=kf_index)
        text = _inject_object_size_anchor(shot, text)
        text = _inject_tone_anchor(shot, text)
        role_scan = " ".join(filter(None, [
            shot.get("ff_desc", ""), shot.get("lf_desc", ""),
            shot.get("motion_desc", "") or shot.get("prompt", ""),
            kf_raw,
        ]))
        anchor = _extract_char_layout(
            shot, chars, role_scan_text=role_scan,
            frame_kind="keyframe", kf_index=kf_index)
        if anchor and "【角色锚定】" not in text:
            text = f"{text}。{anchor}"
        prop_scan = _build_kf_prop_scan(shot, kf_index, kf_raw)
        prop = _extract_prop_layout(
            shot, chars, role_scan_text=prop_scan,
            frame_kind="keyframe", kf_index=kf_index)
        if prop:
            text = f"{text}。{prop}"
        scene = shot.get("scene", "") or ""
        location = shot.get("location", "") or ""
        from shared.style_prefix import visual_location_hint
        vis_loc = visual_location_hint(location or scene, shot)
        ctx = f"（环境：{vis_loc}）" if vis_loc else ""
        text = (
            f"{text}{ctx}"
            f"，环境延续首帧：船的朝向、月亮位置、背景建筑方位、"
            f"水面荷叶分布不变（仅环境连续性约束，人物站位/动作以本帧描述为准），"
            f"表现动作过程中的瞬间状态。"
        )
        text = sanitize_prompt(ensure_prefix(text, style_prefix_for_canvas(frame_canvas_ratio(), orient=True)), verbose=False)
        return _dedupe_prompt_clauses(
            add_image_strict_constraints(
                text, "关键帧", shot=shot, characters=chars, kf_index=kf_index))

    raise ValueError("frame_kind 必须是 first_frame / last_frame / keyframe")






















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

    # 日志输出到 step02.log，不污染控制台（候选上下文优先，见 log_utils.resolve_log_file）
    from shared.tee_logger import get_logger
    from shared.log_utils import resolve_log_file
    logger = get_logger(resolve_log_file("step02.log"), console_level="INFO")

    ff_desc = shot.get("ff_desc", "").strip()
    if not ff_desc:
        _bridge_log(f"  [FRAME] 首帧 shot_{idx} 缺少 ff_desc，拒绝凭 scene/main_subject 虚构画面")
        return None
    # 2026-11-22（M04）：删除「ff_desc 短于 20 字或含运动词就前置『地点，主体，静态画面。』」
    # 的补写。它只在生成器路径执行、视觉计划不做，于是生成器收到的提示与计划单元不一致
    # （S05 内容链用例：ff「小舟靠岸，丁把缆绳系在石桩上。」15 字被补成「快艇，丁，静态画面。…」）；
    # 补写内容也是凭 location/main_subject 拼出来的，与下方「桥接层不得改写帧事实」冲突。
    if empty_scene:
        ff_desc = f"{ff_desc}\n空镜头，画面中严禁出现任何人物、身影、人影、轮廓或人形。只拍摄自然景物特写。"

    # 描述已在 step01 语义审计后冻结；桥接层不得回写或改写帧事实。

    # 计算当前哈希值（核心意图 + 空镜约束 + 参考图 + 锁类型，任一变化都重新生成）
    # 动作镜头用 NO_LAYOUT 锁图（prompt 语义变化），锁类型纳入哈希键而非魔法后缀。
    current_hash = _get_frame_content_hash(
        shot, empty_scene, reference_paths,
        lock_kind="no_layout" if _is_action_shot(shot, characters) else "layout",
        characters=characters, frame_kind="first_frame",
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
        from shared.prompt_contradiction import PromptContradictionError, assert_no_contradictions
        try:
            assert_no_contradictions(_assert_ff, f"shot{idx} 首帧")
        except PromptContradictionError as _pc_err:
            shot["_last_failure_class"] = FAILURE_REQUEST_CONFLICT
            _emit_stage4_ledger(
                shot, "first_frame", 0,
                prompt_text=_assert_ff, refs=list(reference_paths or []),
                action_type="blocked_preflight", defect_class=FAILURE_REQUEST_CONFLICT,
                qc_verdict="not_run", detail=str(_pc_err),
            )
            raise

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
    # S04：生成与 --plan-only 预检共用同一组装入口。
    ff_desc_full = compose_frame_prompt(shot, characters, "first_frame", desc=ff_desc)
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

    # P1-5: validate structured frame contract before entering retry/POST loop (0 POST on conflict).
    _preflight_err = _check_frame_contract_preflight(shot, "first_frame", characters)
    if _preflight_err:
        shot["_last_failure_class"] = FAILURE_REQUEST_CONFLICT
        _emit_stage4_ledger(
            shot, "first_frame", 0,
            prompt_text=ff_desc_full, refs=refs,
            action_type="blocked_preflight", defect_class=FAILURE_REQUEST_CONFLICT,
            qc_verdict="not_run", detail=str(_preflight_err["issues"][:2]),
        )
        _bridge_log(f"  [CONTRACT-STOP] shot_{idx} 首帧契约冲突，拒绝发包: {_preflight_err['issues'][:2]}")
        return None

    # U7 收口（2026-11-15）：锁保帧防覆写——已验收原图不因哈希失配/--force 被重画。
    if _keep_protected_frame(shot, ff_path, "first_frame", logger, "首帧", idx):
        return str(ff_path)

    for attempt in range(max_retries + 1):
        if ff_path.exists() and not force and attempt == 0:
            # 检查哈希文件是否存在且匹配
            if ff_hash_path.exists():
                saved_hash = ff_hash_path.read_text(encoding="utf-8").strip()
                if saved_hash == current_hash and _validate_frame(str(ff_path)):
                    if await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene):
                        if _del_frame_unless_protected(
                                shot, ff_path, ff_hash_path, "首帧", idx, logger,
                                "first_frame"):
                            continue
                        return str(ff_path)
                    else:
                        return str(ff_path)
                else:
                    logger.verbose(f"  [FRAME] 首帧 shot_{idx} 哈希不匹配或缓存无效，重新生成")
            elif script_mtime is not None and ff_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(ff_path)):
                    if await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene):
                        if _del_frame_unless_protected(
                                shot, ff_path, ff_hash_path, "首帧", idx, logger,
                                "first_frame"):
                            continue
                        return str(ff_path)
                    else:
                        return str(ff_path)
            elif script_mtime is None and _validate_frame(str(ff_path)):
                if await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene):
                    if _del_frame_unless_protected(
                            shot, ff_path, ff_hash_path, "首帧", idx, logger,
                            "first_frame"):
                        continue
                    return str(ff_path)
                else:
                    return str(ff_path)

        logger.verbose(f"  [FRAME] 生成首帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        from shared.request_events import new_request_id
        _op_id = new_request_id()
        attempt_variation = _ATTEMPT_VARIATIONS[attempt % len(_ATTEMPT_VARIATIONS)]
        attempt_prompt = ff_desc_full + attempt_variation
        attempt_kwargs = dict(gen_kwargs, prompt=attempt_prompt, operation_id=_op_id)
        img_out = None
        try:
            img_out = await image_adapter.generate_single_image(**attempt_kwargs)
            if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                img_out.data.save(str(ff_path))
            elif hasattr(img_out, "save"):
                img_out.save(str(ff_path))
        except Exception as e:
            fail_cls = _classify_generation_failure(exc=e)
            shot["_last_failure_class"] = fail_cls
            _emit_stage4_ledger(
                shot, "first_frame", attempt + 1,
                prompt_text=attempt_prompt, refs=refs,
                action_type="generate", defect_class=fail_cls, detail=str(e),
                operation_id=_op_id,
                model_id=_adapter_resolved_model_id(image_adapter, img_out),
                characters=characters,
            )
            _bridge_log(f"  [FRAME] 首帧 shot_{idx} 生成异常 ({fail_cls}): {e}")
            if fail_cls in _NON_RETRYABLE_FAILURES:
                return None
            if attempt < max_retries:
                continue
            return None

        if _validate_frame(str(ff_path)):
            try:
                _snap_info = _persist_frame_request_snapshot(
                    ff_path,
                    shot=shot,
                    frame_kind="first_frame",
                    prompt_text=attempt_prompt,
                    refs=refs,
                    image_adapter=image_adapter,
                    img_out=img_out,
                    operation_id=_op_id,
                )
            except Exception as _snap_exc:
                shot["_last_failure_class"] = FAILURE_INFRA_UNAVAILABLE
                ff_hash_path.unlink(missing_ok=True)
                _emit_stage4_ledger(
                    shot, "first_frame", attempt + 1,
                    prompt_text=attempt_prompt, refs=refs,
                    action_type="generate", defect_class=FAILURE_INFRA_UNAVAILABLE,
                    qc_verdict="snapshot_write_failed", detail=str(_snap_exc),
                    operation_id=_op_id,
                    model_id=_adapter_resolved_model_id(image_adapter, img_out),
                    characters=characters,
                )
                _bridge_log(f"  [SNAPSHOT] shot_{idx} 首帧请求快照写入失败，阻断 QC 放行: {_snap_exc}")
                return None
            ff_hash_path.write_text(current_hash, encoding="utf-8")
            _cx = _shot_complexity_label(shot, characters, "first_frame")
            qc_critical = await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene,
                                                 variation=attempt_variation)
            _qc_last = shot.get("_qc_last")
            _df_cls, _qc_verdict, _qc_detail = _resolve_ledger_qc_verdict(
                ff_path, shot, "first_frame", characters,
                qc_critical=qc_critical, qc_last=_qc_last,
            )
            if _df_cls == FAILURE_QC_DISPUTE:
                shot["_last_failure_class"] = FAILURE_QC_DISPUTE
                _emit_stage4_ledger(
                    shot, "first_frame", attempt + 1,
                    prompt_text=attempt_prompt, refs=refs,
                    action_type="generate", defect_class=FAILURE_QC_DISPUTE,
                    qc_verdict="qc_call_failed", detail=_qc_detail,
                    operation_id=_snap_info["operation_id"],
                    model_id=_snap_info["model_id"],
                    request_digest=_snap_info["request_digest"],
                    image_sha256=_snap_info["image_sha256"],
                    shot_complexity=_cx,
                )
                _bridge_log(f"  [QC-DISPUTE] shot_{idx} 首帧 QC 调用异常，保留图片停止重抽（0 额外 POST）")
                return str(ff_path)
            if qc_critical:
                shot["_last_failure_class"] = FAILURE_STOCHASTIC_MISS
                _emit_stage4_ledger(
                    shot, "first_frame", attempt + 1,
                    prompt_text=attempt_prompt, refs=refs,
                    action_type="generate", defect_class=FAILURE_STOCHASTIC_MISS,
                    qc_verdict=_qc_verdict, detail=_qc_detail,
                    operation_id=_snap_info["operation_id"],
                    model_id=_snap_info["model_id"],
                    request_digest=_snap_info["request_digest"],
                    image_sha256=_snap_info["image_sha256"],
                    shot_complexity=_cx,
                )
                if await _try_qc_repair(image_adapter, shot, str(ff_path), "first_frame",
                                        characters, empty_scene, idx):
                    _, _rep_v, _rep_d = _resolve_ledger_qc_verdict(
                        ff_path, shot, "first_frame", characters,
                        qc_critical=False, qc_last=shot.get("_qc_last"),
                    )
                    from shared.image_qc import frame_identity as _fi
                    _emit_stage4_ledger(
                        shot, "first_frame", attempt + 1,
                        prompt_text=attempt_prompt, refs=refs,
                        action_type="local_edit", defect_class="none",
                        qc_verdict=_rep_v, detail=_rep_d,
                        operation_id=_snap_info["operation_id"],
                        model_id=_adapter_resolved_model_id(image_adapter),
                        request_digest=_snap_info["request_digest"],
                        image_sha256=str(_fi(ff_path).get("sha256") or ""),
                        shot_complexity=_cx,
                    )
                elif attempt < max_retries:
                    if not _del_frame_unless_protected(
                            shot, ff_path, ff_hash_path, "首帧", idx, logger,
                            "first_frame"):
                        return str(ff_path)
                    continue
                else:
                    _bridge_log(f"  [QC] shot_{idx} {attempt+1} 次尝试均 critical，"
                                "保留末版留痕（attempts_exhausted）")
                    return str(ff_path)
            else:
                shot["_last_failure_class"] = _df_cls
                _emit_stage4_ledger(
                    shot, "first_frame", attempt + 1,
                    prompt_text=attempt_prompt, refs=refs,
                    action_type="generate", defect_class=_df_cls,
                    qc_verdict=_qc_verdict, detail=_qc_detail,
                    operation_id=_snap_info["operation_id"],
                    model_id=_snap_info["model_id"],
                    request_digest=_snap_info["request_digest"],
                    image_sha256=_snap_info["image_sha256"],
                    shot_complexity=_cx,
                )
            # v6.30：doodle 区域编辑 fallback — 精确空间约束 prompt 调不到位时
            # 走 i2i + 红框二修；QC critical 之后才做（不在已知坏帧上浪费 i2i）。
            # 失败保留原图（graceful degradation）。哈希在二修前已落盘，重跑命中
            # 缓存直接复用当前图（成功=修好的图，失败=原图），不重复扣费。
            doodle_cfg = None if _no_auto_repair() else _detect_spatial_fallback(ff_desc_full, shot)
            if doodle_cfg:
                doodle_tmp = ff_path.parent / f"{ff_path.stem}_doodle_tmp.png"
                refined = await _doodle_refine_first_frame(
                    image_adapter, str(ff_path),
                    doodle_cfg["bbox_ratio"], doodle_cfg["refined_prompt"],
                    str(doodle_tmp), idx,
                )
                if await _accept_doodle_refinement(refined, ff_path, shot, "first_frame",
                                                  characters, empty_scene, ff_desc_full):
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

    # 日志输出到 step02.log，不污染控制台（候选上下文优先，见 log_utils.resolve_log_file）
    from shared.tee_logger import get_logger
    from shared.log_utils import resolve_log_file
    logger = get_logger(resolve_log_file("step02.log"), console_level="INFO")

    lf_desc = shot.get("lf_desc", "").strip()
    if not lf_desc:
        # 只允许无损复用同一镜头的静态 ff_desc；不得从 scene /
        # main_subject / narration 推断新的尾帧事实。
        lf_desc = _resolve_missing_lf_desc(shot) or ""
        if not lf_desc:
            logger.warn(
                f"  [FRAME] 尾帧 shot_{idx} 缺少 lf_desc，且 ff_desc 不是可复用静态画面；"
                "拒绝凭 scene/main_subject 虚构尾帧，交由上游审计/LLM 修复"
            )
            return None
    if empty_scene:
        lf_desc = f"{lf_desc}\n空镜头，画面中严禁出现任何人物、身影、人影、轮廓或人形。只拍摄自然景物特写。"

    # 描述已在 step01 语义审计后冻结；桥接层不得把中间态改写成结果态。

    # 计算当前哈希值（核心意图 + 空镜约束 + 参考图 + 锁类型）
    # 尾帧用 NO_LAYOUT 锁图（构图随动作演变），锁类型纳入哈希键（审计 S4）。
    current_hash = _get_frame_content_hash(
        shot, empty_scene, reference_paths, lock_kind="no_layout",
        characters=characters, frame_kind="last_frame")

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
            shot["_last_failure_class"] = FAILURE_REQUEST_CONFLICT
            _emit_stage4_ledger(
                shot, "last_frame", 0,
                prompt_text=_assert_lf, refs=list(reference_paths or []),
                action_type="blocked_preflight", defect_class=FAILURE_REQUEST_CONFLICT,
                qc_verdict="not_run", detail=str(e),
            )
            _bridge_log(f"  [LF-SKIP] shot{idx} 尾帧 prompt 矛盾，软跳过（不阻塞整批）: {e}")
            return None

    refs = reference_paths if reference_paths else []
    # ---- lf prompt 组装（只算一次，重试复用） ----
    # S04：生成与 --plan-only 预检共用同一组装入口。
    lf_desc_full = compose_frame_prompt(shot, characters, "last_frame", desc=lf_desc)
    # ---- lf prompt 组装结束 ----

    # P1-5: validate structured frame contract before entering retry/POST loop (0 POST on conflict).
    _preflight_err = _check_frame_contract_preflight(shot, "last_frame", characters)
    if _preflight_err:
        shot["_last_failure_class"] = FAILURE_REQUEST_CONFLICT
        _emit_stage4_ledger(
            shot, "last_frame", 0,
            prompt_text=lf_desc_full, refs=refs,
            action_type="blocked_preflight", defect_class=FAILURE_REQUEST_CONFLICT,
            qc_verdict="not_run", detail=str(_preflight_err["issues"][:2]),
        )
        _bridge_log(f"  [CONTRACT-STOP] shot_{idx} 尾帧契约冲突，拒绝发包: {_preflight_err['issues'][:2]}")
        return None

    # U7 收口（2026-11-15）：锁保帧防覆写——已验收原图不因哈希失配/--force 被重画。
    if _keep_protected_frame(shot, lf_path, "last_frame", logger, "尾帧", idx):
        return str(lf_path)

    for attempt in range(max_retries + 1):
        if lf_path.exists() and not force and attempt == 0:
            # 检查哈希文件是否存在且匹配
            if lf_hash_path.exists():
                saved_hash = lf_hash_path.read_text(encoding="utf-8").strip()
                if saved_hash == current_hash and _validate_frame(str(lf_path)):
                    if await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene):
                        if _del_frame_unless_protected(
                                shot, lf_path, lf_hash_path, "尾帧", idx, logger,
                                "last_frame"):
                            continue
                        return str(lf_path)
                    else:
                        return str(lf_path)
                else:
                    logger.verbose(f"  [FRAME] 尾帧 shot_{idx} 哈希不匹配或缓存无效，重新生成")
            elif script_mtime is not None and lf_path.stat().st_mtime >= script_mtime:
                if _validate_frame(str(lf_path)):
                    if await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene):
                        if _del_frame_unless_protected(
                                shot, lf_path, lf_hash_path, "尾帧", idx, logger,
                                "last_frame"):
                            continue
                        return str(lf_path)
                    else:
                        return str(lf_path)
            elif script_mtime is None and _validate_frame(str(lf_path)):
                if _validate_frame(str(lf_path)):
                    if await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene):
                        if _del_frame_unless_protected(
                                shot, lf_path, lf_hash_path, "尾帧", idx, logger,
                                "last_frame"):
                            continue
                        return str(lf_path)
                    else:
                        return str(lf_path)

        logger.verbose(f"  [FRAME] 生成尾帧 shot_{idx} {f'(重试 {attempt}/{max_retries})' if attempt > 0 else ''}...")
        from shared.request_events import new_request_id
        _op_id = new_request_id()
        attempt_variation = _ATTEMPT_VARIATIONS[attempt % len(_ATTEMPT_VARIATIONS)]
        attempt_prompt = lf_desc_full + attempt_variation
        img_out = None
        try:
            img_out = await image_adapter.generate_single_image(
                prompt=attempt_prompt,
                reference_image_paths=refs,
                shot_index=idx,
                frame_desc="尾帧",
                lock_prompt=image_lock_prompt(layout_lock=False),
                force_multi_ref=True,
                operation_id=_op_id,
            )
            if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                img_out.data.save(str(lf_path))
            elif hasattr(img_out, "save"):
                img_out.save(str(lf_path))
        except Exception as e:
            fail_cls = _classify_generation_failure(exc=e)
            shot["_last_failure_class"] = fail_cls
            _emit_stage4_ledger(
                shot, "last_frame", attempt + 1,
                prompt_text=attempt_prompt, refs=refs,
                action_type="generate", defect_class=fail_cls, detail=str(e),
                operation_id=_op_id,
                model_id=_adapter_resolved_model_id(image_adapter, img_out),
                characters=characters,
            )
            _bridge_log(f"  [FRAME] 尾帧 shot_{idx} 生成异常 ({fail_cls}): {e}")
            if fail_cls in _NON_RETRYABLE_FAILURES:
                return None
            if attempt < max_retries:
                continue
            return None

        if _validate_frame(str(lf_path)):
            try:
                _snap_info = _persist_frame_request_snapshot(
                    lf_path,
                    shot=shot,
                    frame_kind="last_frame",
                    prompt_text=attempt_prompt,
                    refs=refs,
                    image_adapter=image_adapter,
                    img_out=img_out,
                    operation_id=_op_id,
                )
            except Exception as _snap_exc:
                shot["_last_failure_class"] = FAILURE_INFRA_UNAVAILABLE
                lf_hash_path.unlink(missing_ok=True)
                _emit_stage4_ledger(
                    shot, "last_frame", attempt + 1,
                    prompt_text=attempt_prompt, refs=refs,
                    action_type="generate", defect_class=FAILURE_INFRA_UNAVAILABLE,
                    qc_verdict="snapshot_write_failed", detail=str(_snap_exc),
                    operation_id=_op_id,
                    model_id=_adapter_resolved_model_id(image_adapter, img_out),
                    characters=characters,
                )
                _bridge_log(f"  [SNAPSHOT] shot_{idx} 尾帧请求快照写入失败，阻断 QC 放行: {_snap_exc}")
                return None
            lf_hash_path.write_text(current_hash, encoding="utf-8")
            _cx = _shot_complexity_label(shot, characters, "last_frame")
            qc_critical = await _maybe_qc_frame(shot, str(lf_path), "last_frame", characters, empty_scene,
                                                 variation=attempt_variation)
            _qc_last = shot.get("_qc_last")
            _df_cls, _qc_verdict, _qc_detail = _resolve_ledger_qc_verdict(
                lf_path, shot, "last_frame", characters,
                qc_critical=qc_critical, qc_last=_qc_last,
            )
            if _df_cls == FAILURE_QC_DISPUTE:
                shot["_last_failure_class"] = FAILURE_QC_DISPUTE
                _emit_stage4_ledger(
                    shot, "last_frame", attempt + 1,
                    prompt_text=attempt_prompt, refs=refs,
                    action_type="generate", defect_class=FAILURE_QC_DISPUTE,
                    qc_verdict="qc_call_failed", detail=_qc_detail,
                    operation_id=_snap_info["operation_id"],
                    model_id=_snap_info["model_id"],
                    request_digest=_snap_info["request_digest"],
                    image_sha256=_snap_info["image_sha256"],
                    shot_complexity=_cx,
                )
                _bridge_log(f"  [QC-DISPUTE] shot_{idx} 尾帧 QC 调用异常，保留图片停止重抽（0 额外 POST）")
                return str(lf_path)
            if qc_critical:
                shot["_last_failure_class"] = FAILURE_STOCHASTIC_MISS
                _emit_stage4_ledger(
                    shot, "last_frame", attempt + 1,
                    prompt_text=attempt_prompt, refs=refs,
                    action_type="generate", defect_class=FAILURE_STOCHASTIC_MISS,
                    qc_verdict=_qc_verdict, detail=_qc_detail,
                    operation_id=_snap_info["operation_id"],
                    model_id=_snap_info["model_id"],
                    request_digest=_snap_info["request_digest"],
                    image_sha256=_snap_info["image_sha256"],
                    shot_complexity=_cx,
                )
                if await _try_qc_repair(image_adapter, shot, str(lf_path), "last_frame",
                                        characters, empty_scene, idx):
                    _, _rep_v, _rep_d = _resolve_ledger_qc_verdict(
                        lf_path, shot, "last_frame", characters,
                        qc_critical=False, qc_last=shot.get("_qc_last"),
                    )
                    from shared.image_qc import frame_identity as _fi
                    _emit_stage4_ledger(
                        shot, "last_frame", attempt + 1,
                        prompt_text=attempt_prompt, refs=refs,
                        action_type="local_edit", defect_class="none",
                        qc_verdict=_rep_v, detail=_rep_d,
                        operation_id=_snap_info["operation_id"],
                        model_id=_adapter_resolved_model_id(image_adapter),
                        request_digest=_snap_info["request_digest"],
                        image_sha256=str(_fi(lf_path).get("sha256") or ""),
                        shot_complexity=_cx,
                    )
                    return str(lf_path)
                elif attempt < max_retries:
                    if not _del_frame_unless_protected(
                            shot, lf_path, lf_hash_path, "尾帧", idx, logger,
                            "last_frame"):
                        return str(lf_path)
                    continue
                else:
                    _bridge_log(f"  [QC] shot_{idx} {attempt+1} 次尝试均 critical，"
                                "保留末版留痕（attempts_exhausted）")
                    return str(lf_path)
            shot["_last_failure_class"] = _df_cls
            _emit_stage4_ledger(
                shot, "last_frame", attempt + 1,
                prompt_text=attempt_prompt, refs=refs,
                action_type="generate", defect_class=_df_cls,
                qc_verdict=_qc_verdict, detail=_qc_detail,
                operation_id=_snap_info["operation_id"],
                model_id=_snap_info["model_id"],
                request_digest=_snap_info["request_digest"],
                image_sha256=_snap_info["image_sha256"],
                shot_complexity=_cx,
            )
            return str(lf_path)
        if attempt < max_retries:
            logger.verbose(f"  [FRAME] 尾帧 shot_{idx} 第{attempt+1}次不达标，重试...")

    return str(lf_path) if lf_path.exists() else None






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

    idx = shot["index"]
    shot_dir = shots_dir / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)

    # 日志（候选上下文优先，见 log_utils.resolve_log_file）
    from shared.tee_logger import get_logger
    from shared.log_utils import resolve_log_file
    _kf_logger = get_logger(resolve_log_file("step02.log"), console_level="INFO")

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
        kf_path = shot_dir / KEYFRAME_FILENAME_FMT.format(i)
        kf_hash_path = shot_dir / f"keyframe_{i}_hash.txt"

        if empty_scene:
            kf_desc = kf_desc + "\n空镜头， 画面中严禁出现任何人物、身影、人影、轮廓或人形。只拍摄自然景物特写。"
        
        # 复用首帧哈希逻辑：基于 shot 内容派生 + 锁类型（关键帧用 NO_LAYOUT 锁）。
        # v2（08-10）：哈希含 kf_desc + time_ratio → 每个中间帧独立哈希，改 kf desc
        # 能触发该帧重出（原复用 shot 哈希，所有中间帧同值且改 desc 缓存不失效）。
        current_hash = _get_frame_content_hash(
            shot, empty_scene, reference_paths, lock_kind="no_layout",
            extra_fields={"kf_desc": kf_desc, "kf_t": time_ratio},
            characters=characters, frame_kind="keyframe")

        # U7 收口（2026-11-15）：锁保帧防覆写——已验收原图不因哈希失配/--force 被重画。
        if _keep_protected_frame(shot, kf_path, "keyframe", _kf_logger,
                                 f"关键帧{i}", idx, kf_index=i):
            kf_paths.append(str(kf_path))
            continue

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
                            if _del_frame_unless_protected(
                                    shot, kf_path, kf_hash_path, f"关键帧{i}", idx,
                                    _kf_logger, "keyframe", i):
                                continue
                            kf_paths.append(str(kf_path))
                            break
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
                            if _del_frame_unless_protected(
                                    shot, kf_path, kf_hash_path, f"关键帧{i}", idx,
                                    _kf_logger, "keyframe", i):
                                continue
                            kf_paths.append(str(kf_path))
                            break
                        else:
                            kf_paths.append(str(kf_path))
                            break
                elif script_mtime is None and _validate_frame(str(kf_path)):
                    if await _maybe_qc_frame(shot, str(kf_path), "keyframe",
                                             characters, empty_scene,
                                             prompt=shot.get(f"kf_{i}_desc_final"),
                                             kf_index=i):
                        if _del_frame_unless_protected(
                                shot, kf_path, kf_hash_path, f"关键帧{i}", idx,
                                _kf_logger, "keyframe", i):
                            continue
                        kf_paths.append(str(kf_path))
                        break
                    else:
                        kf_paths.append(str(kf_path))
                        break
            
            _kf_logger.verbose(f"  [KEYFRAME] 生成 shot_{idx} keyframe_{i} (t={time_ratio:.1f})...")
            # 关键帧注入场景上下文 + 显式"重构图"指令：
            # 关键帧是动作进行中的瞬间状态，构图需与首帧不同（如"推至中景"），
            # 若沿用首帧整图参考 + 布局不变锁，会锁死在首帧构图上。
            _kf_idx_in_shot = None
            try:
                _all_kfs = shot.get("keyframes") or []
                if kf in _all_kfs:
                    _kf_idx_in_shot = _all_kfs.index(kf)
            except Exception:
                pass
            kf_desc_full = compose_frame_prompt(
                shot, characters, "keyframe",
                desc=kf_desc, kf_index=_kf_idx_in_shot,
            )
            _kf_prop_scan = _build_kf_prop_scan(shot, _kf_idx_in_shot, kf_desc_raw)
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

            _kf_preflight = _check_frame_contract_preflight(
                shot, "keyframe", characters, kf_index=_kf_idx_in_shot,
            )
            if _kf_preflight:
                shot["_last_failure_class"] = FAILURE_REQUEST_CONFLICT
                _emit_stage4_ledger(
                    shot, f"keyframe_{i}", 0,
                    kf_index=i,
                    prompt_text=kf_desc_full, refs=refs,
                    action_type="blocked_preflight", defect_class=FAILURE_REQUEST_CONFLICT,
                    qc_verdict="not_run", detail=str(_kf_preflight["issues"][:2]),
                )
                break

            from shared.prompt_contradiction import PromptContradictionError, assert_no_contradictions
            try:
                assert_no_contradictions(kf_desc_full, f"shot{idx} 关键帧{i}")
            except PromptContradictionError as _kf_pc_err:
                shot["_last_failure_class"] = FAILURE_REQUEST_CONFLICT
                _emit_stage4_ledger(
                    shot, f"keyframe_{i}", 0,
                    kf_index=i,
                    prompt_text=kf_desc_full, refs=refs,
                    action_type="blocked_preflight", defect_class=FAILURE_REQUEST_CONFLICT,
                    qc_verdict="not_run", detail=str(_kf_pc_err),
                )
                break

            from shared.request_events import new_request_id
            _op_id = new_request_id()
            attempt_variation = _ATTEMPT_VARIATIONS[attempt % len(_ATTEMPT_VARIATIONS)]
            attempt_prompt = kf_desc_full + attempt_variation
            img_out = None
            try:
                img_out = await image_adapter.generate_single_image(
                    prompt=attempt_prompt,
                    reference_image_paths=refs,
                    shot_index=idx,
                    frame_desc=f"关键帧{i}(t={time_ratio:.1f})",
                    lock_prompt=image_lock_prompt(layout_lock=False),
                    force_multi_ref=True,
                    operation_id=_op_id,
                )
                if hasattr(img_out, "data") and hasattr(img_out.data, "save"):
                    img_out.data.save(str(kf_path))
                elif hasattr(img_out, "save"):
                    img_out.save(str(kf_path))
            except Exception as e:
                fail_cls = _classify_generation_failure(exc=e)
                shot["_last_failure_class"] = fail_cls
                _emit_stage4_ledger(
                    shot, f"keyframe_{i}", attempt + 1,
                    kf_index=i,
                    prompt_text=attempt_prompt, refs=refs,
                    action_type="generate", defect_class=fail_cls, detail=str(e),
                    operation_id=_op_id,
                    model_id=_adapter_resolved_model_id(image_adapter, img_out),
                    characters=characters,
                )
                _kf_logger.verbose(f"  [KEYFRAME] shot_{idx} keyframe_{i} 生成异常 ({fail_cls}): {e}")
                if fail_cls in _NON_RETRYABLE_FAILURES:
                    break
                if attempt < max_retries:
                    continue
                break

            if _validate_frame(str(kf_path)):
                try:
                    _snap_info = _persist_frame_request_snapshot(
                        kf_path,
                        shot=shot,
                        frame_kind="keyframe",
                        prompt_text=attempt_prompt,
                        refs=refs,
                        image_adapter=image_adapter,
                        img_out=img_out,
                        operation_id=_op_id,
                        kf_index=i,
                    )
                except Exception as _snap_exc:
                    shot["_last_failure_class"] = FAILURE_INFRA_UNAVAILABLE
                    kf_hash_path.unlink(missing_ok=True)
                    _emit_stage4_ledger(
                        shot, f"keyframe_{i}", attempt + 1,
                        kf_index=i,
                        prompt_text=attempt_prompt, refs=refs,
                        action_type="generate", defect_class=FAILURE_INFRA_UNAVAILABLE,
                        qc_verdict="snapshot_write_failed", detail=str(_snap_exc),
                        operation_id=_op_id,
                        model_id=_adapter_resolved_model_id(image_adapter, img_out),
                        characters=characters,
                    )
                    _kf_logger.verbose(f"  [SNAPSHOT] shot_{idx} keyframe_{i} 请求快照写入失败，阻断 QC 放行: {_snap_exc}")
                    break
                shot[f"kf_{i}_desc_final"] = kf_desc_full
                _cx = _shot_complexity_label(shot, characters, "keyframe", kf_index=i)
                qc_critical = await _maybe_qc_frame(
                    shot, str(kf_path), "keyframe", characters,
                    empty_scene, prompt=kf_desc_full,
                    variation=attempt_variation,
                    kf_index=i)
                _qc_last = shot.get("_qc_last")
                _df_cls, _qc_verdict, _qc_detail = _resolve_ledger_qc_verdict(
                    kf_path, shot, "keyframe", characters,
                    qc_critical=qc_critical, qc_last=_qc_last, kf_index=i,
                )
                if _df_cls == FAILURE_QC_DISPUTE:
                    shot["_last_failure_class"] = FAILURE_QC_DISPUTE
                    _emit_stage4_ledger(
                        shot, f"keyframe_{i}", attempt + 1,
                        kf_index=i,
                        prompt_text=attempt_prompt, refs=refs,
                        action_type="generate", defect_class=FAILURE_QC_DISPUTE,
                        qc_verdict="qc_call_failed", detail=_qc_detail,
                        operation_id=_snap_info["operation_id"],
                        model_id=_snap_info["model_id"],
                        request_digest=_snap_info["request_digest"],
                        image_sha256=_snap_info["image_sha256"],
                        shot_complexity=_cx,
                    )
                    kf_paths.append(str(kf_path))
                    break
                if qc_critical:
                    shot["_last_failure_class"] = FAILURE_STOCHASTIC_MISS
                    _emit_stage4_ledger(
                        shot, f"keyframe_{i}", attempt + 1,
                        kf_index=i,
                        prompt_text=attempt_prompt, refs=refs,
                        action_type="generate", defect_class=FAILURE_STOCHASTIC_MISS,
                        qc_verdict=_qc_verdict, detail=_qc_detail,
                        operation_id=_snap_info["operation_id"],
                        model_id=_snap_info["model_id"],
                        request_digest=_snap_info["request_digest"],
                        image_sha256=_snap_info["image_sha256"],
                        shot_complexity=_cx,
                    )
                    if await _try_qc_repair(image_adapter, shot, str(kf_path), "keyframe",
                                            characters, empty_scene, idx, kf_index=i):
                        _, _rep_v, _rep_d = _resolve_ledger_qc_verdict(
                            kf_path, shot, "keyframe", characters,
                            qc_critical=False, qc_last=shot.get("_qc_last"), kf_index=i,
                        )
                        from shared.image_qc import frame_identity as _fi
                        _emit_stage4_ledger(
                            shot, f"keyframe_{i}", attempt + 1,
                            kf_index=i,
                            prompt_text=attempt_prompt, refs=refs,
                            action_type="local_edit", defect_class="none",
                            qc_verdict=_rep_v, detail=_rep_d,
                            operation_id=_snap_info["operation_id"],
                            model_id=_adapter_resolved_model_id(image_adapter),
                            request_digest=_snap_info["request_digest"],
                            image_sha256=str(_fi(kf_path).get("sha256") or ""),
                            shot_complexity=_cx,
                        )
                        kf_paths.append(str(kf_path))
                        break
                    if attempt < max_retries:
                        if not _del_frame_unless_protected(
                                shot, kf_path, kf_hash_path, f"关键帧{i}", idx,
                                _kf_logger, "keyframe", i):
                            kf_paths.append(str(kf_path))
                            break
                        continue
                    _kf_logger.verbose(f"  [QC] shot_{idx} keyframe_{i} {attempt+1} 次尝试均 critical，"
                                       "保留末版留痕（attempts_exhausted）")
                    kf_paths.append(str(kf_path))
                    break
                shot["_last_failure_class"] = _df_cls
                _emit_stage4_ledger(
                    shot, f"keyframe_{i}", attempt + 1,
                    kf_index=i,
                    prompt_text=attempt_prompt, refs=refs,
                    action_type="generate", defect_class=_df_cls,
                    qc_verdict=_qc_verdict, detail=_qc_detail,
                    operation_id=_snap_info["operation_id"],
                    model_id=_snap_info["model_id"],
                    request_digest=_snap_info["request_digest"],
                    image_sha256=_snap_info["image_sha256"],
                    shot_complexity=_cx,
                )
                # v6.30：doodle 区域编辑 fallback（关键帧同首帧——精确空间约束
                # prompt 调不到位时走 i2i+红框二修）。失败保留原图（graceful degradation）。
                # 哈希在二修前已落盘：重跑命中缓存复用当前图，不重复扣费。
                kf_doodle_cfg = None if _no_auto_repair() else _detect_spatial_fallback(kf_desc_full, shot)
                if kf_doodle_cfg:
                    doodle_tmp = kf_path.parent / f"{kf_path.stem}_doodle_tmp.png"
                    refined = await _doodle_refine_first_frame(
                        image_adapter, str(kf_path),
                        kf_doodle_cfg["bbox_ratio"], kf_doodle_cfg["refined_prompt"],
                        str(doodle_tmp), idx,
                    )
                    if await _accept_doodle_refinement(refined, kf_path, shot, "keyframe",
                                                      characters, empty_scene, kf_desc_full, i):
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
    # 2026-10-31 P0-5：首帧与尾帧共用同一套场景资产图（同镜环境必须同源）。
    # 旧实现让动作镜头首帧跳过场景图、尾帧注入，同一镜头 ff/lf 背景/船型/水面
    # 光线分裂，I2V 只能靠画面变形硬接。人数由 spec 名单 + NO_LAYOUT 锁约束，
    # 不再靠"首帧不给场景图"回避。
    # 08-16：尾帧与关键帧拆开收集——尾帧传 lf_desc（威胁源没入水中时不注入刺客
    # 肖像），关键帧传合并全文（动作中间态，威胁源以动作可见→保留肖像供
    # kf0「刺客半身探出举匕首」使用）。
    frame_refs_ff = _collect_frame_refs_for_shot(shot, characters, limit=5, for_frame="首帧")
    # 尾帧不在这里预算：它要把「首帧整图」并入同一组类别后**一次**压缩，
    # 预算会白跑一次拼图并让类别信息丢失。
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
        # 尾帧参考图 = 本镜首帧整图（基底）+ 场景/角色/道具资产。
        # 2026-10-31 P0-5：旧实现完全不参考本镜刚生成的首帧，ff/lf 各画一套
        # 环境。现在首帧整图排第一张作人物/服装/环境基底，构图漂移由 NO_LAYOUT
        # 锁 + prompt 里的「按尾帧描述重新布局」指令约束，不再用"不给首帧"回避
        # 构图雷同。首帧文件缺失时直接失败（禁止尾帧脱离首帧单独成图）。
        if not ff_path.is_file() or (want_ff and not ff_path_str):
            raise FileNotFoundError(f"shot {idx}: last frame requires a valid first frame")
        from shared.ref_compose import fit_refs, active_image_ref_limit
        # 尾帧参考图按**类别**组合：首帧整图（环境/服饰基底）+ 场景 + 人物 + 道具，
        # 一次压缩到上限；每张拼合表都带 refmap（成员区域），身份不再靠文件名。
        _lf = _collect_frame_refs_for_shot(
            shot, characters, limit=5, for_frame="尾帧",
            return_buckets=True, apply_head_crop=True)
        lf_buckets = [("frame", [str(ff_path)])] + list(_lf["buckets"])
        lf_refs = fit_refs(lf_buckets, active_image_ref_limit(), shot_dir,
                           f"shot{idx}_lf", omissions=_lf.get("omissions"))
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
