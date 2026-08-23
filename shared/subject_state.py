"""shared.subject_state - 角色状态一致性检查的轻量辅助.

v6.34.6（08-22，B 选项 4 字段×3 角色×3 维度一致性检查的共享层）.

设计原则：
- 服装色/姿态词表与 vimax_bridge 通用化复用，不写死
- 角色判定按 _char_aliases 别名合并
- 服装色提取窗口 = 角色名后 12 字符（与 vimax_bridge._extract_kf_pose 窗口一致），
  避免跨角色污染
"""
from __future__ import annotations

from shared.vimax_bridge import (
    _char_aliases,
    _extract_kf_pose,
    _KF_POSE_STATES,
)


# 服装色 + 款式词表——去掉了「甲」「衣」「衫」「袍」等单字易误命中项。
# 保留至少 2 字以上的复合词。
_CLOTHING_KEYWORDS: tuple[str, ...] = (
    "素白", "玄色", "玄衣", "青衣", "青衫", "黑衣", "黑衫", "白衣",
    "白袍", "白衫", "灰衣", "灰色", "红衣", "蓝色", "黄衣",
    "绿衣", "紫衣", "粉衣", "茶衣",
    "深蓝", "深灰", "墨绿", "银灰", "雪白", "漆黑", "月白", "鸦青", "檀色",
    "绛红", "赭石", "枯黄", "酱色", "靛蓝", "黛色",
    "劲装", "劲衣", "轻袍", "轻装", "襦裙", "氅衣", "披风", "斗篷",
    "春衫", "夏衫", "秋衫", "冬衫",
    "小帽", "纱帽", "斗笠", "高冠",
)


def char_present(text: str, char_id: str, characters: list | None = None) -> bool:
    if not text or not char_id:
        return False
    for alias in _char_aliases(char_id, characters):
        if alias and alias in text:
            return True
    return False


def extract_pose(text: str, char_id: str, characters: list | None = None) -> str:
    if not text or not char_id:
        return ""
    return _extract_kf_pose(text, char_id)


def extract_clothing(text: str, char_id: str, characters: list | None = None) -> str:
    """角色名后 12 字符窗口内找服装色/款式词（与 _extract_kf_pose 窗口一致）.

    为防止跨角色污染，按标点「，。；；」断句优先：若角色名后 12 字符内
    含标点，取第一段（角色名 + 短句）；无标点时取全 12 字符。
    """
    if not text or not char_id:
        return ""
    occ: set[int] = set()
    for alias in _char_aliases(char_id, characters):
        idx = text.find(alias)
        while idx >= 0:
            occ.add(idx)
            idx = text.find(alias, idx + len(alias))
    for pos0 in sorted(occ):
        # 12 字符窗口与 _extract_kf_pose 一致
        raw_window = text[pos0:pos0 + 12]
        # 按标点截断：取第一段（角色名后第一个标点前）
        cut = -1
        for p in ("，", "。", "；", "、", "；"):
            i = raw_window.find(p)
            if i >= 0 and (cut < 0 or i < cut):
                cut = i
        window = raw_window if cut < 0 else raw_window[:cut + 1]
        for kw in sorted(_CLOTHING_KEYWORDS, key=len, reverse=True):
            if kw in window:
                return kw
    return ""


def get_relevant_chars(shot: dict, characters: list | None) -> list[str]:
    if not shot:
        return []
    scan = " ".join(filter(None, [
        shot.get("ff_desc", "") or "",
        shot.get("lf_desc", "") or "",
        shot.get("motion_desc", "") or "",
    ]))
    if not scan or not characters:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for c in characters:
        ident = c.get("identifier", "")
        if not ident or ident in seen:
            continue
        if char_present(scan, ident, characters):
            seen.add(ident)
            out.append(ident)
    return out
