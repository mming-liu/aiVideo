# -*- coding: utf-8 -*-
"""step02_video_gen.ref_manifest - G3：参考清单先行 + 「参考图k=身份」绑定校验.

S06 G3 的问题不是「正文里没有参考说明」，而是**顺序**：正文先定稿，参考清单
由 provider `prepare_request` 才最终确定，于是正文写不出「参考图 k 是谁」，也无
法证明这段声明在 prepare（压缩/改写）之后还在。本模块把这件事拆成三步，全部
在**请求组装期**完成，不需要模型：

1. **清单先行**（``collect_manifest``）：按 provider 实际下发顺序
   （首帧 → 尾帧 → 关键帧 → 参考图）枚举每一项的 ``k`` / ``role`` / 身份。
   顺序来源是 ``_make_request`` 已经算好的素材字段，**不重新收集**——重新收集
   会与真正发出去的那份不一致。
2. **绑定段**（``binding_block``）：生成紧凑的 ``参考图k=身份`` 声明并追加到
   正文尾部；正文的其他部分不受影响。
3. **prepare 前后一致性**（``verify_survival``）：记录 5967f7e 校准的三态——
   ``True``=经 prepare 后仍在最终正文、``False``=prepare 后丢失、
   ``None``=provider 无 prepare 能力、**未验证**。机器读取不得把 ``None`` 当
   ``True``（``block_survives_prepare_status`` 给出 ``verified`` /
   ``unverified_provider_no_prepare``）。

只做确定性映射（文件名 / role / 角色注册表），不做语义猜测；映射不出来时如实
写「参考素材（role=…）」，不编造身份。
"""
from __future__ import annotations

import os
from pathlib import Path

SCHEMA = "video_ref_binding.v1"

#: 绑定段标题（与 step02a 的「参考图说明」同一读法，便于人工对照）。
BLOCK_HEADER = "【参考图绑定】"

ROLE_FIRST_FRAME = "first_frame"
ROLE_LAST_FRAME = "last_frame"
ROLE_KEYFRAME = "keyframe"
ROLE_REFERENCE = "reference"

_ROLE_LABEL = {
    ROLE_FIRST_FRAME: "本镜首帧（起始构图）",
    ROLE_LAST_FRAME: "本镜尾帧（终态）",
    ROLE_KEYFRAME: "过程关键帧（中间时刻）",
    ROLE_REFERENCE: "参考素材（角色/场景外观）",
}

#: 「不出图、只为身份」的通用兜底——不编造具体角色名。
_UNKNOWN_REFERENCE = "参考素材"


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _frame_paths(index: int) -> tuple[str, str]:
    try:
        from shared.paths import first_frame_file, last_frame_file
        return _norm(first_frame_file(index)), _norm(last_frame_file(index))
    except Exception:                                        # noqa: BLE001
        return "", ""


def _registry_index(registry: dict | None) -> dict[str, str]:
    """``{规范化路径: 角色标识}``（注册表 → 反向索引，供身份映射用）."""
    out: dict[str, str] = {}
    for ident, views in (registry or {}).items():
        if not ident or not isinstance(views, dict):
            continue
        for view in views.values():
            path = view.get("path") if isinstance(view, dict) else None
            if path:
                out.setdefault(_norm(path), str(ident))
    return out


def _identity_of(path: str, role: str, *, index: int, registry_index: dict[str, str]) -> str:
    """单条参考素材的身份（确定性映射，不做语义猜测）."""
    name = Path(str(path)).name
    norm = _norm(path)
    ff, lf = _frame_paths(index)
    if role == ROLE_FIRST_FRAME or (ff and norm == ff):
        return _ROLE_LABEL[ROLE_FIRST_FRAME]
    if role == ROLE_LAST_FRAME or (lf and norm == lf):
        return _ROLE_LABEL[ROLE_LAST_FRAME]
    if role == ROLE_KEYFRAME or name.startswith("keyframe"):
        return _ROLE_LABEL[ROLE_KEYFRAME]
    ident = registry_index.get(norm)
    if ident:
        return "角色%s外观" % ident
    if "ref_sheet" in name or name.endswith(".refmap.json"):
        return "合并参考表（角色/场景外观）"
    if name in ("wide.png", "near.png"):
        return "场景环境参考"
    if name == "prop.png" or "props_bank" in norm:
        return "道具外观参考"
    if "scenes_bank" in norm:
        return "场景环境参考"
    return _ROLE_LABEL.get(role, _UNKNOWN_REFERENCE)


def collect_manifest(*, shot: dict, image_path: str | None = None,
                     last_frame_image_path: str | None = None,
                     extra_keyframe_paths: list | None = None,
                     reference_paths: list | None = None,
                     registry: dict | None = None, characters: list | None = None
                     ) -> list[dict]:
    """按**实际下发顺序**枚举参考素材及其身份.

    顺序与 provider 收到的素材顺序一致（``step02_video_gen.request_plan.
    collect_references`` 同一套字段），因此 ``k`` 直接就是「参考图k」。
    只为图片建条目；视频参考（``reference_video``）不占图片编号，单独标注。
    """
    try:
        from _video_gen.media_io import media_kind as _media_kind
    except Exception:                                        # noqa: BLE001
        def _media_kind(_p):
            return "image"

    index = 0
    try:
        index = int(shot.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    reg_index = _registry_index(registry)
    items: list[dict] = []

    def _add(role: str, path: str | None) -> None:
        if not path:
            return
        kind = "image" if _media_kind(str(path)) != "video" else "video"
        items.append({
            "k": len([i for i in items if i["kind"] == "image"]) + 1 if kind == "image" else None,
            "role": role,
            "kind": kind,
            "path": str(path),
            "name": Path(str(path)).name,
            "identity": (_identity_of(str(path), role, index=index,
                                      registry_index=reg_index)
                         if kind == "image" else "视频参考（运镜/动作/光影）"),
        })

    _add(ROLE_FIRST_FRAME, image_path)
    _add(ROLE_LAST_FRAME, last_frame_image_path)
    for path in (extra_keyframe_paths or []):
        _add(ROLE_KEYFRAME, path)
    for path in (reference_paths or []):
        _add(ROLE_REFERENCE, path)
    return items


def binding_block(items: list[dict]) -> str:
    """紧凑的 ``参考图k=身份`` 声明（无条目返回空串，不产生空标题）."""
    pairs = []
    for item in items or []:
        if item.get("kind") != "image" or not item.get("k"):
            continue
        pairs.append("参考图%d=%s" % (item["k"], item.get("identity") or _UNKNOWN_REFERENCE))
    if not pairs:
        return ""
    return BLOCK_HEADER + "；".join(pairs)


def apply_binding(prompt: str, items: list[dict]) -> tuple[str, str]:
    """把绑定段追加到正文尾部（幂等）：返回 ``(新正文, 绑定段)``.

    幂等是给重试/重复组装用的：同一份正文不会被追加两次（那会改变
    ``request_key`` 并让缓存永远不命中）。
    """
    block = binding_block(items)
    text = str(prompt or "")
    if not block or block in text:
        return text, block
    return text.rstrip() + "\n\n" + block, block


def verify_survival(*, items: list[dict], request_prompt: str,
                    prepared_prompt: str | None = None,
                    prepared_available: bool = False) -> dict:
    """记录三态：绑定段在 provider prepare 前后是否仍在最终正文.

    ``prepared_available=False``（provider 无 prepare / 离线规划）⇒
    ``block_survives_prepare=None`` + ``unverified_provider_no_prepare``：
    **未验证**，不得写成已验证。
    """
    block = binding_block(items)
    text = str(request_prompt or "")
    in_frozen = bool(block) and block in text
    final = str(prepared_prompt or "")
    if not block:
        return {"schema": SCHEMA, "block": "", "block_in_frozen_prompt": False,
                "block_survives_prepare": None,
                "block_survives_prepare_status": "no_binding",
                "bindings_one_to_one": True, "binding_count": 0,
                "reference_count": len(items or []), "per_item": [],
                "missing_after_prepare": []}
    if not prepared_available:
        survives = None
        status = "unverified_provider_no_prepare"
    else:
        survives = block in final
        status = "verified"
    per_item = []
    for item in items or []:
        if item.get("kind") != "image" or not item.get("k"):
            continue
        marker = "参考图%d=%s" % (item["k"], item.get("identity") or _UNKNOWN_REFERENCE)
        per_item.append({"k": item["k"], "name": item.get("name"),
                         "identity": item.get("identity"),
                         "expected": marker,
                         "in_final_prompt": (marker in final) if prepared_available
                         else (marker in text)})
    return {
        "schema": SCHEMA,
        "block": block,
        "block_in_frozen_prompt": in_frozen,
        "block_survives_prepare": survives,
        "block_survives_prepare_status": status,
        "bindings_one_to_one": bool(per_item) and all(
            i["in_final_prompt"] for i in per_item),
        "binding_count": len(per_item),
        "reference_count": len([i for i in (items or []) if i.get("kind") == "image"]),
        "per_item": per_item,
        "missing_after_prepare": [i["expected"] for i in per_item
                                  if not i["in_final_prompt"]],
    }


__all__ = [
    "SCHEMA", "BLOCK_HEADER", "collect_manifest", "binding_block", "apply_binding",
    "verify_survival",
]
