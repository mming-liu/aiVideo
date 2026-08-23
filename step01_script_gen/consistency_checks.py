"""v6.34.6 4 字段×3 角色×3 维度统一一致性检查（B 选项）.

覆盖：
1. shot 内 prompt ↔ lf_desc 同角色姿态/服装
2. shot 内 kf 内部姿态回退
3. shot 间 shot0.lf ↔ shot1.ff 姿态跳变
4. shot 间 shot0.lf ↔ shot1.ff 服装漂色

通用化：所有角色从 characters 读，不写死。
"""
from __future__ import annotations

from shared.subject_state import (
    char_present,
    extract_clothing,
    extract_pose,
    get_relevant_chars,
)


_POSE_FORWARD_RULES: dict[str, set[str]] = {
    "仰卧":  {"仰卧", "仰倒", "躺", "已躺下", "倒", "卧", "趴", "俯卧"},
    "仰倒":  {"仰卧", "仰倒", "躺", "已躺下", "倒", "卧"},
    "躺":    {"仰卧", "仰倒", "躺", "已躺下", "倒", "卧", "趴", "俯卧"},
    "已躺下":{"仰卧", "仰倒", "躺", "已躺下", "倒", "卧"},
    "倒":    {"仰卧", "仰倒", "躺", "已躺下", "倒", "卧", "趴"},
    "卧":    {"仰卧", "仰倒", "躺", "已躺下", "倒", "卧", "趴", "俯卧"},
    "盘坐":  {"盘坐", "盘膝", "端坐", "坐", "跪坐", "立", "站", "站起", "起身", "起", "走动", "走"},
    "盘膝":  {"盘坐", "盘膝", "端坐", "坐", "跪坐", "立", "站", "站起", "起身"},
    "端坐":  {"盘坐", "盘膝", "端坐", "坐", "立", "站"},
    "坐":    {"盘坐", "盘膝", "端坐", "坐", "跪坐", "立", "站", "站起", "起身", "起"},
    "跪坐":  {"跪坐", "坐", "起", "起身", "立", "站", "跪"},
    "跪":    {"跪", "跪坐", "坐", "起", "起身", "立", "站"},
    "立":    {"立", "站", "站起", "起身", "坐", "走动", "走"},
    "站":    {"立", "站", "站起", "起身", "走", "走动", "坐"},
    "踞立":  {"立", "站", "踞立", "踞蹲", "蹲", "坐", "走"},
    "踞蹲":  {"踞蹲", "蹲", "立", "站", "踞立", "潜伏", "没入", "入水", "退入", "离场", "出水", "破水", "半身探出", "半身出水"},
    "蹲":    {"蹲", "踞蹲", "立", "站", "踞立", "走", "潜伏", "没入", "入水", "退入", "离场", "出水", "破水", "半身探出", "半身出水"},
    "半身探出": {"半身探出", "半身出水", "出水", "破水", "浮出", "入水", "没入", "倒纵入水", "离场", "退入"},
    "半身出水": {"半身探出", "半身出水", "出水", "破水", "浮出", "入水", "没入", "倒纵入水", "离场"},
    "潜伏": {"潜伏", "破水", "出水", "半身探出", "半身出水", "入水", "没入", "倒纵入水", "离场", "立", "站", "蹲", "踞蹲"},
    "破水": {"破水", "出水", "半身探出", "半身出水", "入水", "没入", "倒纵入水", "立", "站"},
    "出水": {"出水", "半身探出", "半身出水", "入水", "没入", "倒纵入水"},
}


def _is_pose_forward(prev: str, cur: str) -> bool:
    if not prev or not cur or prev == cur:
        return True
    allowed = _POSE_FORWARD_RULES.get(prev)
    if allowed is None:
        return True
    return cur in allowed


_CLOTHING_COMPATIBLE: dict[str, set[str]] = {
    "素白": {"素白", "白衣", "白袍", "白衫", "白", "月白", "雪白"},
    "白衣": {"素白", "白衣", "白袍", "白衫", "白", "月白", "雪白"},
    "白袍": {"素白", "白衣", "白袍", "白衫", "白", "月白", "雪白"},
    "玄色": {"玄色", "玄衣", "黑衣", "黑衫", "黑色", "漆黑", "鸦青", "墨绿"},
    "玄衣": {"玄色", "玄衣", "黑衣", "黑衫", "黑色", "漆黑"},
    "黑衣": {"玄色", "玄衣", "黑衣", "黑衫", "黑色"},
    "青衣": {"青衣", "青衫", "青色", "鸦青", "蓝衣", "蓝色", "靛蓝"},
    "青衫": {"青衣", "青衫", "青色", "鸦青", "蓝衫"},
    "春衫": {"春衫", "襦裙", "衫", "衣"},
    "劲装": {"劲装", "劲衣", "氅衣", "轻装", "披风", "氅"},
}


def _clothing_compatible(a: str, b: str) -> bool:
    if a == b:
        return True
    s = _CLOTHING_COMPATIBLE.get(a)
    if s is not None and b in s:
        return True
    s = _CLOTHING_COMPATIBLE.get(b)
    if s is not None and a in s:
        return True
    return False


def _check_shot_internal_field_consistency(shots, characters):
    warnings = []
    for s in shots:
        idx = s.get("index", "?")
        prompt = s.get("prompt", "") or ""
        lf = s.get("lf_desc", "") or ""
        if not prompt or not lf:
            continue
        rel = get_relevant_chars(s, characters)
        for char in rel:
            if not (char_present(prompt, char, characters) and char_present(lf, char, characters)):
                continue
            p_p = extract_pose(prompt, char, characters)
            l_p = extract_pose(lf, char, characters)
            p_c = extract_clothing(prompt, char, characters)
            l_c = extract_clothing(lf, char, characters)
            if p_p and l_p and not _is_pose_forward(p_p, l_p):
                warnings.append(f"shot{idx} {char} prompt→lf 姿态倒退：prompt「{p_p}」→ lf「{l_p}」")
            if p_c and l_c and p_c != l_c and not _clothing_compatible(p_c, l_c):
                warnings.append(f"shot{idx} {char} prompt→lf 服装漂色：prompt「{p_c}」→ lf「{l_c}」")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_shot_internal_kf_pose_progression(shots, characters):
    warnings = []
    for s in shots:
        idx = s.get("index", "?")
        kfs = sorted(
            [k for k in (s.get("keyframes") or []) if isinstance(k, dict)],
            key=lambda k: k.get("t_sec") or 0,
        )
        rel = get_relevant_chars(s, characters)
        for i in range(1, len(kfs)):
            d_p = kfs[i - 1].get("desc") or ""
            d_c = kfs[i].get("desc") or ""
            t_p = kfs[i - 1].get("t_sec")
            t_c = kfs[i].get("t_sec")
            for char in rel:
                if not (char_present(d_p, char, characters) and char_present(d_c, char, characters)):
                    continue
                p_p = extract_pose(d_p, char, characters)
                p_c = extract_pose(d_c, char, characters)
                if p_p and p_c and not _is_pose_forward(p_p, p_c):
                    warnings.append(f"shot{idx} t={t_p}→{t_c} {char} kf 姿态回退：「{p_p}」→「{p_c}」")
    return {"pass": True, "issues": [], "warnings": warnings}


def _check_shot_to_shot_lf_to_ff_consistency(shots, characters):
    warnings = []
    for i in range(len(shots) - 1):
        curr, nxt = shots[i], shots[i + 1]
        c_idx, n_idx = curr.get("index", "?"), nxt.get("index", "?")
        c_loc = (curr.get("location", "") or curr.get("scene", "")).strip()
        n_loc = (nxt.get("location", "") or nxt.get("scene", "")).strip()
        if not c_loc or c_loc != n_loc:
            continue
        lf = curr.get("lf_desc", "") or ""
        nff = nxt.get("ff_desc", "") or ""
        if not lf or not nff:
            continue
        for char in characters:
            ident = char.get("identifier", "")
            if not ident:
                continue
            if not (char_present(lf, ident, characters) and char_present(nff, ident, characters)):
                continue
            p_l = extract_pose(lf, ident, characters)
            p_f = extract_pose(nff, ident, characters)
            c_l = extract_clothing(lf, ident, characters)
            c_f = extract_clothing(nff, ident, characters)
            if p_l and p_f and not _is_pose_forward(p_l, p_f):
                warnings.append(f"shot{c_idx}→shot{n_idx} {ident} 姿态跳变：lf「{p_l}」→ ff「{p_f}」")
            if c_l and c_f and c_l != c_f and not _clothing_compatible(c_l, c_f):
                warnings.append(f"shot{c_idx}→shot{n_idx} {ident} 服装漂色：lf「{c_l}」→ ff「{c_f}」")
    return {"pass": True, "issues": [], "warnings": warnings}


def run_consistency_checks(shots, characters):
    return {
        "shot_internal_field_consistency": _check_shot_internal_field_consistency(shots, characters),
        "shot_internal_kf_pose_progression": _check_shot_internal_kf_pose_progression(shots, characters),
        "shot_to_shot_lf_to_ff_consistency": _check_shot_to_shot_lf_to_ff_consistency(shots, characters),
    }
