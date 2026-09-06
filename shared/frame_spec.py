"""v6.57+ per-frame spec accessor（事实源唯一化第一步）.

LLM 输出 frame_layouts（ff/lf/kfs 三桶，per-frame 出镜表）作为 QC + prompt 组装
的事实源，替代散在 char_layout / 散文 desc / 组装期注入锚块三源的旧范式。

下游（validation/constraints/vimax_bridge/image_qc）在 M2/M3 切到 accessor。
M1 阶段：accessor 提供双路径，derive_char_layout 给老读者临时兼容，
不直接修改 shot['char_layout']（避免数据回写副作用）。

设计原则：
- **唯一读取入口** — 所有读 frame_layouts 的代码都走 get_frame_spec，别处再读
  shot['frame_layouts'] 一律视为违规（M4 删残留）
- **降级而非报错** — 老章只有 char_layout，按「全镜出镜表」派生 spec，on_screen
  保守填 True（旧数据无此信息，宁可误报在场也不漏掉）
- **kfs 长度不匹配留痕** — 越界返回 [] 并 warn（M2 由 Constraint 升级为违例）
"""
from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger(__name__)

# char_layout 派生时填的占位字段（保持旧 reader 不崩）
_FALLBACK_SURFACE = "未指定"
_FALLBACK_FACING = ""


def get_frame_spec(
    shot: dict,
    frame_kind: str,
    kf_index: Optional[int] = None,
) -> list[dict]:
    """返回当帧出镜表 spec.

    Args:
        shot: 分镜 dict（必有 frame_layouts，新章；可无，老章降级）
        frame_kind: "first_frame" | "last_frame" | "keyframe"
        kf_index: 当 frame_kind="keyframe" 时取 shot['frame_layouts']['kfs'][kf_index]；
                  越界/类型不匹配时返回 [] 并 warn

    Returns:
        当帧出镜表（每项是 _FRAME_SPEC_ITEM 结构的 dict）。
        - 优先读 frame_layouts.ff / lf / kfs
        - 缺失时降级读 char_layout 派生

    降级路径：旧章节只存 char_layout，按「全镜出镜表」派生 spec 兜底，
    缺失字段 on_screen=True（保守估计在场）；pose=''；gaze_target='无'；props=[]。
    """
    fl = shot.get("frame_layouts")
    if isinstance(fl, dict):
        if frame_kind == "first_frame":
            return fl.get("ff") or []
        if frame_kind == "last_frame":
            return fl.get("lf") or []
        if frame_kind == "keyframe":
            kfs = fl.get("kfs") or []
            if kf_index is None or kf_index < 0 or kf_index >= len(kfs):
                _log.warning(
                    "get_frame_spec: kf_index=%s 越界（kfs 长度 %s，shot index=%s）",
                    kf_index, len(kfs), shot.get("index"),
                )
                return []
            return kfs[kf_index] or []
        _log.warning("get_frame_spec: 未知 frame_kind=%r", frame_kind)
        return []
    # 降级路径：老章只存 char_layout → 派生 spec
    return _legacy_spec_from_char_layout(shot)


def _legacy_spec_from_char_layout(shot: dict) -> list[dict]:
    """旧章节只有 char_layout，按全镜出镜表派生 spec 兜底."""
    cl = shot.get("char_layout") or []
    if not cl:
        return []
    return [
        {
            "name": item.get("name", ""),
            "on_screen": True,  # 旧数据无此信息，保守
            "position": item.get("position", "画中"),
            "pose": "",
            "gaze_target": "无",
            "props": [],
        }
        for item in cl
        if isinstance(item, dict)
    ]


def derive_char_layout(shot: dict) -> list[dict]:
    """frame_layouts.ff → 旧 char_layout 视图（供未迁移读者临时使用，M4 删除）.

    字段映射：
      - name → name
      - position → position
      - pose → facing（兼容旧 reader；pose 是描述性状态，facing 也是描述性空间关系）
      - on_screen=False → 跳过（不出场就不写进 char_layout）
      - role: 旧 reader 关心主/次 → 第 1 个 on_screen 角色 role='主'，其余 '次'
      - surface: 新 spec 无此字段，填空串（v6.62 起不再填 _FALLBACK_SURFACE
        占位符——"未指定" 是 truthy，曾被 surface 注入当真数据拼进锚定块）

    旧章（无 frame_layouts）→ 原样返回 char_layout。
    """
    fl = shot.get("frame_layouts")
    if not isinstance(fl, dict):
        # 已是 char_layout（无 frame_layouts）→ 原样返回
        return [item for item in (shot.get("char_layout") or []) if isinstance(item, dict)]
    ff = fl.get("ff") or []
    on_screen_items = [s for s in ff if isinstance(s, dict) and s.get("on_screen")]
    if not on_screen_items:
        return []
    out: list[dict] = []
    for i, item in enumerate(on_screen_items):
        out.append({
            "name": item.get("name", ""),
            "position": item.get("position", "画中"),
            "facing": item.get("pose", _FALLBACK_FACING),
            "role": "主" if i == 0 else "次",
            # v6.62（09-06）：surface 填空串而非 _FALLBACK_SURFACE——"未指定"
            # 是 truthy 占位符，曾被 vimax_bridge surface 注入当真数据拼进
            # 锚定块（13/13 镜「在未指定上」垃圾文本实测）。检查器对空串
            # 同样报「缺 surface」，占位符失去存在价值。
            "surface": "",
        })
    return out


def spec_on_screen_names(shot: dict, frame_kind: str, kf_index: Optional[int] = None) -> list[str]:
    """当帧在场角色名列表（on_screen=True 的 name）.

    替代 `_expected_persons` 的散文解析路径——人数 = spec on_screen 计数，
    互斥不可能（M3 QC 切到本函数）。
    """
    spec = get_frame_spec(shot, frame_kind, kf_index)
    return [
        s.get("name", "")
        for s in spec
        if isinstance(s, dict) and s.get("on_screen") and s.get("name")
    ]


def spec_threat_visible(
    shot: dict,
    threat_keywords: tuple,
    frame_kind: str,
    kf_index: Optional[int] = None,
) -> bool:
    """当帧威胁源是否在场（spec on_screen 判定，替代 _threat_visible_in 窗口匹配）.

    threat_keywords: 威胁源关键词元组（如 ("刺客", "神秘人", ...)）。
    返回 True 当且仅当 spec 里有 on_screen=True 且 name 匹配任一关键词。
    """
    spec = get_frame_spec(shot, frame_kind, kf_index)
    for s in spec:
        if not isinstance(s, dict) or not s.get("on_screen"):
            continue
        name = s.get("name", "")
        if any(kw in name for kw in threat_keywords):
            return True
    return False


# 【角色锚定】注入块标记（生成端：shared/vimax_bridge.py _extract_char_layout；
# validation post_fix / frame_spec_repair 坏块自救以该标记识别块边界）
CHAR_ANCHOR_BLOCK_MARKER = "【角色锚定】"


def strip_polluted_char_anchor_block(desc: str) -> str:
    """剥掉被「未指定」占位符污染的【角色锚定】坏块，保留块前正文（历史坏数据自救）.

    09-05/06 存盘 kf desc 的锚块内含 `_FALLBACK_SURFACE="未指定"` 占位符
    （root-fix：vimax_bridge surface 空值守卫 + derive_char_layout 改填空串）。
    块存在但未污染（无「未指定」）或块不存在时**原样返回**——调用方以
    「返回值 != 入参」判定是否发生过剥离；剥掉的坏块由下游锚定注入
    （validation post_fix / step02a / vimax_bridge 组装）用干净 surface 重建。

    实现单源：validation.py post_fix 与 constraints/frame_spec_repair.py 共用本函数。
    """
    if not desc:
        return desc
    if CHAR_ANCHOR_BLOCK_MARKER not in desc or "未指定" not in desc:
        return desc
    return desc.split(CHAR_ANCHOR_BLOCK_MARKER, 1)[0].rstrip("。！？，,;；")
