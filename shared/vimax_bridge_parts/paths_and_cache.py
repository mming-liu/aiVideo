# -*- coding: utf-8 -*-
"""`shared.vimax_bridge` 拆分出的 paths_and_cache 部分（2026-11-28 O11）.

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

from .contract_and_refs import (  # noqa: F401
    PROMPT_TEMPLATE_VERSION,
    _bridge_log,
)

# monkeypatch 兼容转发（见 _forward.py）
from shared.vimax_bridge_parts._forward import forward as _forward

# monkeypatch 兼容：本文件内部调用也走父模块属性（定义本身仍导出）
_proxy__doodle_refine_first_frame = _forward("shared.vimax_bridge", '_doodle_refine_first_frame')  # noqa: F811
_proxy__maybe_qc_frame = _forward("shared.vimax_bridge", '_maybe_qc_frame')  # noqa: F811
_proxy__resolve_style_anchor_path = _forward("shared.vimax_bridge", '_resolve_style_anchor_path')  # noqa: F811
_proxy__validate_frame = _forward("shared.vimax_bridge", '_validate_frame')  # noqa: F811
# monkeypatch 兼容：本文件内对 `_accept_doodle_refinement` 的调用要走父模块属性，
# 这样 tests/shared 里 `monkeypatch.setattr(vb, "_accept_doodle_refinement", ...)`
# 才能影响到这里（定义本身仍导出给父模块）。
_proxy_accept_doodle = _forward("shared.vimax_bridge",
                                "_accept_doodle_refinement")  # noqa: F811
_bridge_log = _forward('shared.vimax_bridge', '_bridge_log')  # noqa: F811
is_empty_scene = _forward('shared.vimax_bridge', 'is_empty_scene')  # noqa: F811
style_prefix_for_canvas = _forward('shared.vimax_bridge', 'style_prefix_for_canvas')  # noqa: F811

__all__ = [
    '_PROTECTED_FRAME_MARKER',
    '_accept_doodle_refinement',
    '_adapter_resolved_model_id',
    '_cleanup_doodle_candidate',
    '_closeup_directive',
    '_configured_image_provider_model',
    '_del_frame_and_hash',
    '_del_frame_unless_protected',
    '_doodle_refine_first_frame',
    '_emit_stage4_ledger',
    '_frame_is_protected',
    '_generate_missing_portraits',
    '_get_frame_content_hash',
    '_head_portrait_path',
    '_keep_protected_frame',
    '_maybe_qc_frame',
    '_no_auto_repair',
    '_persist_frame_request_snapshot',
    '_protected_frames_env',
    '_resolve_style_anchor_path',
    '_shot_complexity_label',
    '_try_qc_repair',
    '_validate_frame',
]


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

    # 肖像专用适配器：9:16 竖屏全身（与分镜帧同画幅，脸占画面高度更大）、
    # 不加风格前缀（风格由生成器按 VIDEO_STYLE 注入）、不替换敏感词（英文 prompt）。
    # 2026-08-22 升级 1440x2560（= 3,686,400 像素）满足 Seedream 4.5 像素下限 3686400
    portrait_adapter = ImageGeneratorAiVideoAdapter(
        size="1440x2560",
        skip_prefix=True,
        skip_sanitize=True,
    )
    portrait_gen = CharacterPortraitsGenerator(image_generator=portrait_adapter)
    # 按量试跑：一次失败即中止整批（默认关闭，保持历史"逐视角继续"行为）。
    _fail_fast = os.getenv("STEP02A_FAIL_FAST", "0").strip() == "1"
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
        # Step 1: 生成基础全景图（front = base），锁定核心特征并传入无人物画风参考锚点
        base_path = portrait_dir / "base.png"
        if not base_path.exists():
            style_ref = _proxy__resolve_style_anchor_path()
            if style_ref:
                _sa_sc = Path(style_ref).with_name(f"{Path(style_ref).stem}_hallucination.json")
                if _sa_sc.is_file():
                    try:
                        _sc_data = json.loads(_sa_sc.read_text(encoding="utf-8"))
                        if isinstance(_sc_data.get("person_count"), int) and _sc_data["person_count"] > 0:
                            _bridge_log(
                                f"  [bank] 警告：画风锚点 {style_ref} 含 {_sc_data['person_count']} 名人物，"
                                "建议改用纯空镜画风锚点"
                            )
                    except Exception:
                        pass
            _bridge_log(
                f"  [bank] 生成 {identifier} 基础全景图"
                + (f"（画风参考={Path(style_ref).name}）" if style_ref else "")
                + " ..."
            )
            try:
                img_out = await portrait_gen.generate_base_portrait(
                    char_in_scene,
                    style="",
                    save_path=str(base_path),
                    style_reference_path=style_ref,
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
                if _fail_fast:
                    raise
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
                if _fail_fast:
                    raise

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
                if _fail_fast:
                    raise

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
                if _fail_fast:
                    raise

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

    通过 shared/image_qc.py（OpenCode Go 视觉通道）对帧图做内容质检；
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
            prompt = ((shot.get(f"kf_{kf_index}_desc_final")
                       if kf_index is not None else None)
                      or shot.get("kf_0_desc_final") or shot.get("kf_desc_final")
                      or shot.get("ff_desc_final") or shot.get("lf_desc_final"))
        else:
            prompt = shot.get("ff_desc_final") or shot.get("lf_desc_final")
    try:
        from shared.image_qc import (
            build_qc_frame_meta, frame_identity, maybe_qc_sync, qc_provider,
            qc_state, write_sidecar,
        )
        # U4：QC 开始前记录图片摘要——写回时再次核对，期间换图则结果记 qc_stale，
        # 绝不给新图写有效 PASS。
        pre = frame_identity(frame_path)
        pre_digest = pre.get("sha256") or None
        frame_meta = build_qc_frame_meta(
            shot, frame_kind, characters, kf_index=kf_index, frame_path=frame_path,
        )
        result = await asyncio.to_thread(
            maybe_qc_sync, frame_path, shot, frame_kind, characters, empty_scene, prompt,
            kf_index,
        )
        # v6.54.1：完整结果暂存——QC critical 分支据此构建局部修复计划
        try:
            shot["_qc_last"] = result
        except Exception:
            pass
        if result.error == "skipped":
            # S05：依据真实 sidecar 绑定状态记账，不把 settled qc_rejected 伪装成 qc_not_run 或放行。
            try:
                settled = qc_state(frame_path, expected_frame=frame_meta)
            except TypeError:
                settled = qc_state(frame_path)
            settled_status = str(settled.get("status") or "qc_not_run")
            _bridge_log(
                f"  [QC] shot_{shot.get('index','?')} {frame_kind} "
                f"sidecar 已结算 ({settled_status})，跳过重检"
            )
            from shared import request_events
            try:
                evt = settled_status if settled_status in ("qc_passed", "qc_rejected") else "qc_not_run"
                request_events.emit(
                    evt, kind="image_qc",
                    frame={"path": str(frame_path), "sha256": pre_digest or ""},
                    reason=("sidecar_settled" if evt != "qc_not_run"
                            else str(settled.get("reason") or "qc_disabled")),
                )
            except Exception as _degrade_exc:
                from shared.degrade import note_degraded as _note_degraded
                _note_degraded("shared/vimax_bridge.py:_maybe_qc_frame", _degrade_exc)
            return settled_status == "qc_rejected"
        if not result.ok:
            # 调用失败：写结构化 qc_call_failed（含旧结果历史），下次仍会重检。
            try:
                write_sidecar(Path(frame_path), result, pre_digest=pre_digest,
                              frame_meta=frame_meta)
            except Exception as e:
                _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} "
                            f"失败记录写入失败: {e}")
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} "
                        f"质检失败（qc_call_failed）: {result.error}")
            return False
        # QC 成功 → 写 sidecar（检出与否都写），供下次跳过与 step02b 消费
        wrote_status = ""
        try:
            write_sidecar(Path(frame_path), result, pre_digest=pre_digest,
                          frame_meta=frame_meta)
            wrote_status = qc_state(frame_path).get("status", "")
        except Exception as e:
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} sidecar 写入失败: {e}")
        if wrote_status == "qc_stale":
            # 检查期间图片被替换：本次结果不属于新图，不能据此删帧。
            _bridge_log(f"  [QC] shot_{shot.get('index','?')} {frame_kind} "
                        f"检查期间图片已变化，结果记 qc_stale（不删帧）")
            return False
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
                "provider": qc_provider(),
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
            # M0.3：把刚生成、已被质检拒收的图片从 generated_ok 改判为 qc_rejected。
            # 只在账本启用时有副作用；无 generated_ok 可扣时返回 False（不制造负计数）。
            from shared import post_budget
            try:
                post_budget.record_qc_rejected("image")
            except Exception as _degrade_exc:
                from shared.degrade import note_degraded as _note_degraded
                _note_degraded("shared/vimax_bridge.py:_maybe_qc_frame", _degrade_exc)
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

def _no_auto_repair() -> bool:
    """是否禁止自动修图（QC i2i 修复 / doodle 二修）.

    S22-3：修图是额外 i2i 请求，会挤占图片 POST 预算，必须在**发送前**禁止，
    而不是靠预算耗尽。默认**关闭修图**；只有显式 `IMAGE_QC_REPAIR_ENABLED=1`
    且 QC 允许重抽（`QC_MAX_ATTEMPTS > 1`）时才开启。
    """
    if os.getenv("STEP02A_NO_REPAIR", "0").strip() == "1":
        return True
    if os.getenv("IMAGE_QC_REPAIR_ENABLED", "0").strip() != "1":
        return True
    try:
        return int(os.getenv("QC_MAX_ATTEMPTS", "2") or "2") <= 1
    except ValueError:
        return True

def _cleanup_doodle_candidate(frame_path, tmp) -> None:
    """二修未被采纳时清理候选产物，只保留 `*_repair_qc_log.json` 审计留痕.

    失败候选图与 `<stem>_repair_hallucination.json` 没有消费者；留在镜目录里会让
    人工排查把「被拒的图」当成可用帧。红框标注图同理，一并删除。
    """
    from shared.image_qc import sidecar_path

    base = Path(frame_path)
    for path in (Path(tmp), sidecar_path(Path(tmp)),
                 base.parent / f"{base.stem}_doodle.png"):
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            _bridge_log(f"  [REPAIR] 候选清理失败 {path}: {e}")

async def _try_qc_repair(image_adapter, shot: dict, frame_path: str, frame_kind: str,
                         characters: list | None, empty_scene: bool, idx, kf_index: int | None = None) -> bool:
    """QC critical 后先试局部修复（v6.54.1 先修后抽）.

    build_repair_plan 分类：局部可修（睁眼/刀接触/红痕/篷/发光）→ doodle 红框
    i2i 改写 1 次（复用 v6.30 机制，bbox 来自 L1 检测）→ 复检；复检通过 = True
    （帧已替换，哈希不删——下次缓存命中的就是修好的图）。全局失败/编辑失败/
    复检仍 critical → False（调用方走原删帧重抽路径）。
    """
    if _no_auto_repair():
        _bridge_log(f"  [REPAIR] shot_{idx} {frame_kind} 已禁用自动修图（STEP02A_NO_REPAIR=1）")
        return False
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
    edited = await _proxy__doodle_refine_first_frame(
        image_adapter, frame_path, plan["bbox_ratio"], plan["edit_prompt"], str(tmp), idx)
    if not edited or not _proxy__validate_frame(edited):
        _cleanup_doodle_candidate(frame_path, tmp)
        _bridge_log("  [REPAIR] 编辑失败/输出无效，回落重抽")
        return False
    # P0-6：任何二修覆盖原图前都必须复检（含「不得保留红框」断言）；
    # 免检/跳过 QC 不算通过。旧实现先 replace 再复检，且把 error=skipped 当通过。
    if await _proxy_accept_doodle(edited, frame_path, shot, frame_kind, characters,
                                       empty_scene, prompt=None, kf_index=kf_index):
        _bridge_log("  [REPAIR] 修复后复检通过")
        return True
    _cleanup_doodle_candidate(frame_path, tmp)
    _bridge_log("  [REPAIR] 修复后复检未通过/未执行，回落重抽")
    return False

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

#: 2026-11-14 Step D：受保护帧标记（只影响「删不删」，不影响生成与 QC）。
_PROTECTED_FRAME_MARKER = ".lock_frame"

def _protected_frames_env() -> list[str]:
    raw = os.getenv("STEP02A_PROTECTED_FRAMES") or ""
    return [part.strip().lower() for part in re.split(r"[,;]", raw) if part.strip()]

def _frame_is_protected(shot: dict, frame_path, frame_kind: str = "",
                        kf_index: int | None = None) -> bool:
    """该帧是否被显式锁定：受保护帧即使 QC critical 也不得删除原图.

    两种声明方式（都只影响删除动作）：

    1. 镜头目录标记文件（推荐，按镜头/按帧）：
       `.lock_frame`（整镜）或 `.lock_frame_<frame_kind>`
       （`first_frame` / `last_frame` / `keyframe_<i>`）；
    2. 环境变量 `STEP02A_PROTECTED_FRAMES`（逗号/分号分隔）：
       - `<shot_index>:<frame_kind>[:<kf_index>]`，如 `2:first_frame`；
       - 路径 glob（对完整路径与文件名各匹配一次），如 `*first_frame.png`。

    未声明 = 旧行为（QC critical 仍按 B7 删帧重抽）。
    """
    path = Path(frame_path)
    shot_dir = path.parent
    kind = str(frame_kind or "")
    markers = [_PROTECTED_FRAME_MARKER]
    if kind:
        markers.append("%s_%s" % (_PROTECTED_FRAME_MARKER, kind))
    if kind == "keyframe" and kf_index is not None:
        markers.append("%s_keyframe_%d" % (_PROTECTED_FRAME_MARKER, int(kf_index)))
    for name in markers:
        if (shot_dir / name).exists():
            return True

    idx = str((shot or {}).get("index") if isinstance(shot, dict) else "")
    for token in _protected_frames_env():
        if ":" in token:
            parts = token.split(":")
            if parts[0] != idx:
                continue
            if len(parts) >= 2 and parts[1] and parts[1] != kind:
                continue
            if len(parts) >= 3 and parts[2]:
                try:
                    if int(parts[2]) != int(kf_index):
                        continue
                except (TypeError, ValueError):
                    continue
            return True
        if (fnmatch.fnmatch(path.as_posix().lower(), token)
                or fnmatch.fnmatch(path.name.lower(), token)):
            return True
    return False

def _del_frame_unless_protected(shot, frame_path, hash_path, label, idx, logger,
                                frame_kind: str = "",
                                kf_index: int | None = None) -> bool:
    """QC critical 的删帧动作：受保护帧只告警、不删；返回是否真的删除.

    返回 True = 已删除（调用方继续重抽）；False = 受保护，调用方必须保留原图。
    """
    if _frame_is_protected(shot, frame_path, frame_kind, kf_index):
        logger.verbose(
            f"  [FRAME] {label} shot_{idx} 受保护，QC critical 不删原图"
            "（sidecar 已记录，交人工复核）")
        return False
    _del_frame_and_hash(frame_path, hash_path, label, idx, logger)
    return True

def _keep_protected_frame(shot, frame_path, frame_kind: str, logger, label, idx,
                          kf_index: int | None = None) -> bool:
    """受保护帧防覆写：原图存在、锁定且可读 ⇒ 不重新生成（--force 也不覆写）.

    2026-11-15（U7 收口）：``.lock_frame`` 原先只拦「QC critical 删帧」——哈希
    失配（如参考表落点/fit_refs 调整改变哈希键）与 ``--force`` 仍会原位覆写已
    验收原图。锁保语义补全为「**不删、也不覆写**」：确需重生时由人工移除
    标记文件（或解除 STEP02A_PROTECTED_FRAMES）。原图不可读（损坏/截断）
    时不拦，允许照常重生成。
    """
    path = Path(frame_path)
    if not path.exists() or not _frame_is_protected(shot, frame_path, frame_kind, kf_index):
        return False
    if not _proxy__validate_frame(str(path)):
        logger.verbose(f"  [FRAME] {label} shot_{idx} 已锁定但原图不可读，按未锁定处理重新生成")
        return False
    logger.verbose(f"  [FRAME] {label} shot_{idx} 受保护：跳过重新生成，保留原图"
                   "（哈希失配/--force 不覆写锁保帧；确需重生请删除 .lock_frame 标记）")
    return True

def _get_frame_content_hash(shot: dict, empty_scene: bool = False,
                            reference_paths: list | None = None,
                            lock_kind: str = "layout",
                            extra_fields: dict | None = None, characters: list | None = None,
                            *, frame_kind: str | None = None) -> str:
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
    路径及文件字节摘要同时入键，角色静态/动态外观也入键，原位替换不能复用旧帧。
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
        style_prefix_for_canvas(frame_canvas_ratio(), orient=True),          # 风格前缀（含本帧画幅）变化时首帧也应重新生成
        f"lock={lock_kind}",               # 锁类型是 prompt 语义的一部分
    ]
    key_parts.extend(stable_fields)

    # v4: 规范化后的实际图片 prompt 文本 — 分镜词变了图片就该重新生成
    # 2026-11-08（WP2-5）：只把**本帧**用到的描述纳入摘要，否则改尾帧会把首帧
    # 一起判为过期（未受影响的媒体被误废）。frame_kind 缺省时保持旧口径。
    if frame_kind == "first_frame":
        _hash_fields = ("ff_desc", "motion_desc")
    elif frame_kind == "last_frame":
        _hash_fields = ("lf_desc", "motion_desc")
    else:
        _hash_fields = ("ff_desc", "lf_desc", "motion_desc")
    for field_name in _hash_fields:
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

    # 参考图路径语义纳入哈希（审计 S4：此前不入哈希，参考图变了但哈希不变）。
    # 2026-10-31 P1-7：只哈希路径会漏掉「同路径原位替换」（重做 base.png/wide.png
    # 后路径不变、图像已变）→ 旧帧被误判缓存命中继续复用。改为路径 + 文件字节摘要。
    for ref in (reference_paths or []):
        p = Path(ref)
        try:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            digest = "unreadable"   # 读不到就按不可读入键，绝不静默复用旧帧
        key_parts.append(f"ref={_normalize(str(ref))}:sha256={digest}")
    key_parts.append(json.dumps([
        {k: c.get(k) for k in ("identifier", "static_features", "dynamic_features")}
        for c in (characters or []) if isinstance(c, dict)
    ], ensure_ascii=False, sort_keys=True))
    key_parts.append("doodle=" + os.getenv("DOODLE_REFINE_ENABLED", "0"))

    # S23-3：固定机位尾帧的系统文案（只约束相机、放开人物/道具/环境）是
    # prompt 语义的一部分。旧实现未把它纳入哈希，导致 71f6baa 改了尾帧文案后
    # 旧 last_frame.png 仍命中缓存。只在尾帧加入策略标识；首帧/关键帧哈希不变。
    if frame_kind == "last_frame":
        from shared.shot_motion_checks import is_fixed_camera
        if is_fixed_camera(str(shot.get("camera_movement") or "")):
            key_parts.append("lf_camera=fixed_camera_only_v1")

    # 多人同载的固定构图已移除；只使受影响首尾帧的旧缓存过期。
    if frame_kind in ("first_frame", "last_frame"):
        from collections import Counter
        from shared.frame_spec import get_frame_spec, is_on_screen
        support_ids = []
        for item in get_frame_spec(shot, frame_kind):
            if not is_on_screen(item):
                continue
            support = item.get("support")
            sid = (support.get("id") if isinstance(support, dict)
                   else item.get("support_id"))
            if sid:
                support_ids.append(str(sid))
        if any(count >= 2 for count in Counter(support_ids).values()):
            key_parts.append("support_prompt=relations_only_v1")

    key = "|".join(key_parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

def _resolve_style_anchor_path() -> str | None:
    """Read-only lookup for locked global style_anchor.png (returns path or None without creating dirs).

    2026-11-28 S02：与 `StyleBank.get_anchor_for_style()` 用**同一判据**——带标记
    但与当前画风不符（`style_mismatch`）、无标记且纪元已是别的画风
    （`legacy_unstamped`）时一律返回 `None`，不得再回落到裸 JSON 或默认
    `style_anchor.png`。本函数保持只读：不建目录、不盖纪元戳、不改内存。
    """
    try:
        from shared.character_bank import (
            current_video_style, normalize_video_style, resolve_style_state,
            style_strict,
        )
        from shared.paths import style_bank_dir
        sdir = style_bank_dir()
        if not sdir.is_dir():
            return None
        data: dict = {}
        json_path = sdir / "style_bank.json"
        if json_path.is_file():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        path = ""
        img = str(data.get("style_anchor_image") or "").strip()
        if img and Path(img).is_file():
            path = img
        if not path:
            default = sdir / "style_anchor.png"
            if default.is_file():
                path = str(default.resolve())
        if not path:
            return None
        meta = data.get("meta")
        stamp = meta.get("style_stamp") if isinstance(meta, dict) else None
        epoch = normalize_video_style(
            stamp.get("video_style") if isinstance(stamp, dict) else "")
        state = resolve_style_state(data.get("video_style"),
                                    current=current_video_style(), epoch=epoch)
        if state in ("style_mismatch", "legacy_unstamped") and style_strict():
            return None
        return path
    except Exception:
        return None

def _head_portrait_path(src: str, create_if_missing: bool = True) -> str:
    """把角色立绘转换为头肩胸像缓存（v6.52 参考图防姿态/持物泄漏）.

    全身站姿会被图生图照抄成画面构图（hook ff 要「半身探出水面」画出站立
    巨人、船尾多出第二个蒙面人实测）；刺客像里的发光匕首也随参考图泄入
    画面。裁到头肩胸后姿态与持物不再入参考，身份特征（脸/发型/上装）保留。

    裁切缓存于 vimax_working/portrait_crops/（源 mtime 变化才重裁）——
    放在收集层而非拼图层，fit_refs 的「≤limit 原图直传」单图路径与拼图
    路径拿到的是同一份已裁图。失败返回原路径（graceful degradation）。
    """
    face = Path(src).parent / "face.png"
    if Path(src).stem in ("base", "front", "front_no_weapon") and face.is_file():
        return str(face)
    try:
        sp = Path(src)
        if not sp.exists():
            return src
        from shared.paths import vimax_working_dir
        out_dir = vimax_working_dir() / "portrait_crops"
        out = out_dir / f"{sp.parent.name}_{sp.stem}.png"
        if out.exists() and out.stat().st_mtime >= sp.stat().st_mtime:
            return str(out)
        if not create_if_missing:
            return src
        from shared.portrait_stitch import crop_head_portrait
        out_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image as _PILImage
        img = _PILImage.open(sp).convert("RGB")
        crop_head_portrait(img).save(out, "PNG")
        return str(out)
    except Exception:
        return src

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

async def _accept_doodle_refinement(refined, target, shot, frame_kind, characters,
                                    empty_scene, prompt=None, kf_index=None):
    """二修候选的准入：QC 通过才覆盖原图，未知/跳过 QC 一律保留原图.

    P0-6：旧路径把带 8px 红框的候选直接 replace 覆盖已过 QC 的原图，既不复检
    红框残留、也不留退路。现在：
      1) 候选先过 _maybe_qc_frame（prompt 追加「不得保留红框」断言）；
      2) ok=False 或 error=skipped（免检/未真正检过）→ 拒绝覆盖；
      3) 覆盖前备份原图与 sidecar，替换/写 sidecar 失败时回滚。
    prompt=None 时按 _maybe_qc_frame 的同一规则从 shot 的 *_desc_final 取。
    """
    if not refined or not _proxy__validate_frame(refined):
        return False
    if prompt is None:
        if frame_kind == "last_frame":
            prompt = shot.get("lf_desc_final") or shot.get("ff_desc_final") or ""
        elif frame_kind == "keyframe":
            prompt = (shot.get("kf_0_desc_final") or shot.get("kf_desc_final")
                      or shot.get("ff_desc_final") or shot.get("lf_desc_final") or "")
        else:
            prompt = shot.get("ff_desc_final") or shot.get("lf_desc_final") or ""
    from shared.image_qc import (
        build_qc_frame_meta,
        request_snapshot_path,
        sidecar_path,
        write_frame_request_snapshot,
        write_sidecar,
    )
    from uuid import uuid4
    import shutil
    target = Path(target)
    candidate = Path(refined)
    sidecar_path(candidate).unlink(missing_ok=True)
    probe = dict(shot)
    probe.pop("_qc_last", None)
    critical = await _proxy__maybe_qc_frame(
        probe, str(candidate), frame_kind, characters, empty_scene,
        prompt=prompt + "；不得保留红框、标注线或编辑标记", kf_index=kf_index)
    result = probe.get("_qc_last")
    if critical or not getattr(result, "ok", False) or getattr(result, "error", "") == "skipped":
        return False
    if any(getattr(i, "severity", "") == "critical" for i in getattr(result, "issues", [])):
        return False
    backup = target.with_name("." + target.name + "." + uuid4().hex + ".bak")
    sidecar = sidecar_path(target)
    req_file = request_snapshot_path(target)
    old_sidecar = sidecar.read_bytes() if sidecar.exists() else None
    old_req = req_file.read_bytes() if req_file.exists() else None
    shutil.copy2(target, backup)
    try:
        candidate.replace(target)
        write_frame_request_snapshot(
            target,
            shot=shot,
            frame_kind=frame_kind,
            prompt=prompt + "；不得保留红框、标注线或编辑标记",
            refs=[str(target)],
            kf_index=kf_index,
            model_id=_adapter_resolved_model_id(None),
        )
        frame_meta = build_qc_frame_meta(
            shot, frame_kind, characters, kf_index=kf_index, frame_path=target,
        )
        write_sidecar(target, result, frame_meta=frame_meta)
    except Exception:
        backup.replace(target)
        if old_sidecar is None:
            sidecar.unlink(missing_ok=True)
        else:
            sidecar.write_bytes(old_sidecar)
        if old_req is None:
            req_file.unlink(missing_ok=True)
        else:
            req_file.write_bytes(old_req)
        raise
    finally:
        backup.unlink(missing_ok=True)
    return True

def _configured_image_provider_model() -> tuple[str, str]:
    try:
        from shared import config as _cfg
        from shared.model_alias import canonical_image_model_id
        provider = str(getattr(_cfg, "IMAGE_PROVIDER", "") or "")
        model_attr = {
            "volcengine": "VOLCENGINE_T2I_MODEL",
            "bailian": "BAILIAN_T2I_MODEL",
            "agent_plan": "AGENT_PLAN_T2I_MODEL",
            "agnes": "AGNES_IMAGE_MODEL",
            "minimax": "MINIMAX_T2I_MODEL",
        }.get(provider, "")
        raw_model = str(getattr(_cfg, model_attr, "") or "") if model_attr else ""
        model_id = canonical_image_model_id(raw_model) if raw_model else ""
        return provider, model_id
    except Exception:
        return "", ""

def _adapter_resolved_model_id(image_adapter, img_out=None) -> str:
    meta = getattr(img_out, "request_meta", None) if img_out is not None else None
    if isinstance(meta, dict) and str(meta.get("model_id") or "").strip():
        return str(meta["model_id"]).strip()
    meta2 = getattr(image_adapter, "last_request_meta", None) if image_adapter is not None else None
    if isinstance(meta2, dict) and str(meta2.get("model_id") or "").strip():
        return str(meta2["model_id"]).strip()
    _, fallback = _configured_image_provider_model()
    return fallback

def _shot_complexity_label(
    shot: dict | None,
    characters: list | None,
    frame_kind: str,
    kf_index: int | None = None,
) -> str:
    if not isinstance(shot, dict):
        return "unknown"
    try:
        from shared.prompt_facts import project_frame_facts
        proj = project_frame_facts(shot, frame_kind, characters, kf_index=kf_index)
        ents = proj.get("entities") or []
        if is_empty_scene(shot, characters or []) or (proj.get("spec_present") and len(ents) == 0):
            return "empty_scene"
        if len(ents) == 1:
            return "single_person"
        if len(ents) == 2:
            return "two_person"
        if len(ents) >= 3:
            return "multi_person"
    except Exception:
        pass
    return "unknown"

def _persist_frame_request_snapshot(
    frame_path: Path,
    *,
    shot: dict,
    frame_kind: str,
    prompt_text: str,
    refs: list | None,
    image_adapter=None,
    img_out=None,
    operation_id: str = "",
    kf_index: int | None = None,
) -> dict:
    """Write `<frame_stem>_request.json` and return resolved request & artifact identity."""
    from shared.image_qc import frame_identity, write_frame_request_snapshot
    req_meta = (
        getattr(img_out, "request_meta", None)
        or (getattr(image_adapter, "last_request_meta", None) if image_adapter is not None else None)
    )
    if not isinstance(req_meta, dict):
        req_meta = {}
    eff_prompt = str(req_meta.get("effective_prompt") or prompt_text or "")
    eff_refs = list(req_meta.get("effective_refs") or refs or [])
    provider, default_model = _configured_image_provider_model()
    resolved_provider = str(req_meta.get("provider") or provider or "")
    resolved_model = str(req_meta.get("model_id") or default_model or "")
    resolved_op = str(req_meta.get("operation_id") or operation_id or "")
    snap_path = write_frame_request_snapshot(
        frame_path,
        shot=shot,
        frame_kind=frame_kind,
        prompt=eff_prompt,
        refs=eff_refs,
        kf_index=kf_index,
        model_id=resolved_model,
        provider=resolved_provider,
        operation_id=resolved_op,
    )
    img_id = frame_identity(frame_path)
    try:
        snap_data = json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception:
        snap_data = {}
    return {
        "operation_id": resolved_op,
        "provider": resolved_provider,
        "model_id": resolved_model,
        "request_digest": str(snap_data.get("request_digest") or ""),
        "prompt_sha256": str(snap_data.get("prompt_sha256") or ""),
        "image_sha256": str(img_id.get("sha256") or ""),
    }

def _emit_stage4_ledger(
    shot: dict,
    frame_role: str,
    attempt_idx: int,
    *,
    kf_index: int | None = None,
    prompt_text: str = "",
    refs: list[str] | None = None,
    action_type: str = "generate",
    defect_class: str = "none",
    qc_verdict: str = "",
    detail: str = "",
    operation_id: str = "",
    model_id: str = "",
    request_digest: str = "",
    image_sha256: str = "",
    shot_complexity: str = "",
    characters: list | None = None,
) -> None:
    """Emit a Stage 4 decision ledger record into step02_events.jsonl (fail-soft)."""
    import hashlib as _hl
    import re as _re
    try:
        from shared.ref_compose import ref_identity
        from shared.request_events import emit_decision_ledger
        from shared.run_scope import current_scope_identity

        eff_kfi = kf_index
        if eff_kfi is None:
            _km = _re.match(r"^keyframe_(\d+)$", str(frame_role or "").strip())
            if _km:
                eff_kfi = int(_km.group(1))
        cfg_provider, cfg_model = _configured_image_provider_model()
        eff_model_id = str(model_id or cfg_model or "")
        scope = current_scope_identity()
        chapter_scope = f"part_{int(scope.get('part') or 1):02d}/chapter_{int(scope.get('chapter') or 1):03d}"
        work_id = str(scope.get("book_name") or (shot.get("book_name") if isinstance(shot, dict) else "") or "")
        script_rev = str((shot.get("_script_revision") if isinstance(shot, dict) else "") or "")
        req_digest = str(request_digest or (
            f"sha256:{_hl.sha256(prompt_text.encode('utf-8')).hexdigest()[:16]}"
            if prompt_text else ""
        ))
        eff_complexity = str(shot_complexity or "")
        if not eff_complexity and isinstance(shot, dict):
            fk_long = (
                "last_frame" if frame_role == "last_frame"
                else ("keyframe" if str(frame_role).startswith("keyframe") else "first_frame")
            )
            eff_complexity = _shot_complexity_label(shot, characters, fk_long, kf_index=eff_kfi)
        ref_ids = []
        for rp in (refs or []):
            ident = ref_identity(rp)
            ref_ids.append(f"{ident.get('kind') or 'ref'}:{ident.get('entity') or Path(rp).name}")
        emit_decision_ledger(
            work_id=work_id,
            chapter_scope=chapter_scope,
            script_revision=script_rev,
            shot_index=shot.get("index") if isinstance(shot, dict) else None,
            frame_role=frame_role,
            kf_index=eff_kfi,
            shot_complexity=eff_complexity or "unknown",
            provider=cfg_provider,
            model_id=eff_model_id,
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            request_digest=req_digest,
            prompt_version=PROMPT_TEMPLATE_VERSION,
            ref_strategy="multi_ref_contract" if refs else "text_only",
            ref_identities=ref_ids,
            attempt_index=attempt_idx,
            operation_id=operation_id,
            image_sha256=image_sha256,
            action_type=action_type,
            defect_class=defect_class,
            qc_verdict=qc_verdict,
            human_usability="unknown",
            non_target_damage=None,
            estimated_cost_cny=None,
            detail=detail,
        )
    except Exception:
        return

