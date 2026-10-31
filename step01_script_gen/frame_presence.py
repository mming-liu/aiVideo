"""Per-actor frame continuity from input identities, not story role labels.

Transitions must explain **the interval they are attached to**.  An "走出画面" that
starts exactly where the previous gap ends does not explain that gap; a transition
whose own window only partially overlaps a gap leaves the event position unknown.
Missing/invalid/out-of-order frame times and untimed clauses are reported as
insufficient evidence — never replaced by an averaged or guessed time.
"""
from shared.frame_spec import get_frame_spec, is_on_screen
import math
from shared.motion_segments import atom_range, strip_time_label
from step01_script_gen.subject_attribution import leading_named_span
import re


EXIT_WORDS = ("走出", "离开", "退场", "没入", "入水", "潜回", "移出")
ENTRY_WORDS = ("走入", "进入", "入场", "出水", "移入", "显露", "重新出现")
OCCLUSION_WORDS = ("遮挡", "遮住", "被挡")
_NEGATION = re.compile(r"(?:没有|并未|尚未|未曾|不曾|不会|未|不|无).{0,2}$")
_EPS = 1e-6


def _duration(shot):
    try:
        return float(shot.get("duration_sec") or shot.get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _clause_window(clause, duration):
    """`((start, end) 或 None, 正文, 问题码或 None)`；时间语法复用 motion_segments."""
    body = strip_time_label(clause)
    if body == clause:
        return None, clause, None
    label = clause[: len(clause) - len(body)]
    window = atom_range(label, duration) if duration > 0 else None
    if window is None:
        return None, body, "transition_time_unknown"
    start, end = window
    if start < -_EPS or end > duration + _EPS or end - start <= _EPS:
        return None, body, "transition_window_out_of_range"
    return (start, end), body, None


def _frame_times(shot, keyframes):
    """与 `_frames()` 同序的物理时刻（无法确定/非法处为 None）+ 问题清单.

    三类必须分开：
      · **缺失**（无 `t_sec` 且无声明过的 `time_ratio`）→ None + `keyframe_time_missing`；
      · **非法**（非有限数、≤0、≥镜长、违反单调）→ None + 对应诊断，**不得**进入区间判定；
      · **合法** → 数值。
    绝不做等分/排序/夹取：一个推算出来的时刻会让「这段有没有过渡」看起来有答案。
    单调比较只对**上一个合法值**进行（此前 `times[-1] + eps` 遇到 None 会直接
    TypeError，2026-10-28 复核实测）。
    """
    duration = _duration(shot)
    times = [0.0]
    problems = []
    if not math.isfinite(duration) or duration <= 0:
        problems.append({"code": "duration_unknown", "frame": "last_frame"})
        duration = 0.0
    last_valid = 0.0
    for i, kf in enumerate(keyframes):
        raw, ratio = kf.get("t_sec"), kf.get("time_ratio")
        value = None
        try:
            if raw is not None and str(raw).strip() != "":
                value = float(raw)
            elif ratio is not None and str(ratio).strip() != "" and duration > 0:
                value = float(ratio) * duration   # 声明过的比例，不是补造
        except (TypeError, ValueError):
            value = None
        if value is None:
            problems.append({"code": "keyframe_time_missing", "frame": "keyframe", "index": i})
        elif not math.isfinite(value):
            problems.append({"code": "keyframe_time_not_finite", "frame": "keyframe",
                             "index": i, "value": str(value)})
            value = None
        elif value <= _EPS:
            problems.append({"code": "keyframe_time_not_positive", "frame": "keyframe",
                             "index": i, "value": value})
            value = None
        elif duration > 0 and value >= duration - _EPS:
            problems.append({"code": "keyframe_time_at_or_past_end", "frame": "keyframe",
                             "index": i, "value": value})
            value = None
        elif value <= last_valid + _EPS:
            problems.append({"code": "keyframe_time_out_of_order", "frame": "keyframe",
                             "index": i, "value": value})
            value = None
        if value is not None:
            last_valid = value
        times.append(value)
    times.append(duration if duration > 0 else None)
    return times, problems


def _frames(shot, keyframes):
    frames = [(shot.get("ff_desc", ""), get_frame_spec(shot, "first_frame"))]
    frames += [(k.get("desc", ""), get_frame_spec(shot, "keyframe", i))
               for i, k in enumerate(keyframes)]
    frames += [(shot.get("lf_desc", ""), get_frame_spec(shot, "last_frame"))]
    return frames


def _transitions(shot, frames, names, duration):
    """actor -> [(kind, window|None, problem|None)]；只收主语是本人且未被否定的分句."""
    motion = str(shot.get("motion_desc") or "")
    out = {n: [] for n in names}
    for raw in re.split(r"[，。；！？\n]", motion + "。" + "。".join(str(t) for t, _ in frames)):
        clause = raw.strip()
        if not clause:
            continue
        window, body, problem = _clause_window(clause, duration)
        actor, span = leading_named_span(body, names)
        if actor not in out:
            continue
        for kind, words in (("exit", EXIT_WORDS), ("entry", ENTRY_WORDS),
                            ("occlusion", OCCLUSION_WORDS)):
            for word in words:
                for match in re.finditer(re.escape(word), span):
                    prefix = span[max(0, match.start() - 4):match.start()]
                    if _NEGATION.search(prefix):
                        continue
                    out[actor].append((kind, window, problem))
    return out


def _covers(events, kind, lo, hi):
    """`(explained, reason)`：只有**整段落在该区间内**的过渡才算解释."""
    partial = False
    unknown = False
    for event_kind, window, problem in events:
        useful = event_kind == kind or (event_kind == "occlusion" and kind in ("exit", "entry"))
        if not useful:
            continue
        if window is None:
            unknown = True
            continue
        start, end = window
        if start >= lo - _EPS and end <= hi + _EPS:
            return True, None
        if end > lo + _EPS and start < hi - _EPS:
            partial = True
    if partial:
        return False, "transition_position_uncertain"
    if unknown:
        return False, "transition_time_unknown"
    return False, "no_transition"


def presence_assessment(shot):
    """`{warnings, insufficient}`：矛盾与证据不足分开，后者带角色/帧/区间."""
    keyframes = [k for k in shot.get("keyframes") or [] if isinstance(k, dict)]
    frames = _frames(shot, keyframes)
    times, time_problems = _frame_times(shot, keyframes)
    names = sorted({str(e.get("name") or "").strip() for _, entries in frames
                    for e in entries if isinstance(e, dict)} - {""}, key=len, reverse=True)
    transitions = _transitions(shot, frames, names, _duration(shot))
    warnings, insufficient = [], []
    for problem in time_problems:
        insufficient.append(dict(problem, actor=None, frames=["ff", "lf"]))
    if not keyframes:
        return {"warnings": warnings, "insufficient": insufficient}
    for name in names:
        states = []
        for _text, entries in frames:
            matches = [e for e in entries if isinstance(e, dict) and e.get("name") == name]
            states.append(is_on_screen(matches[0]) if len(matches) == 1 else None)
        events = transitions.get(name) or []
        for i in range(len(states) - 1):
            lo, hi = times[i], times[i + 1]
            frames_pair = [i, i + 1]
            gap = None if lo is None or hi is None else "%g–%gs" % (lo, hi)
            a, b = states[i], states[i + 1]
            if lo is None or hi is None:
                insufficient.append({"code": "gap_time_unknown", "actor": name,
                                     "frames": frames_pair, "window": None})
                continue
            if a is True and b is False or a is False and b is True:
                kind = "exit" if a is True else "entry"
                label = "离场" if kind == "exit" else "入场"
                ok, reason = _covers(events, kind, lo, hi)
                if ok:
                    continue
                if reason == "no_transition":
                    warnings.append(name + "在 " + gap + " 显式" + label +
                                    "，但该时段没有" + label + "/遮挡过程（别处的声明不能解释这一段）")
                else:
                    insufficient.append({"code": reason, "actor": name,
                                         "frames": frames_pair, "window": [lo, hi],
                                         "state": label})
            elif (a is True and b is None) or (a is None and b is True):
                insufficient.append({"code": "state_missing", "actor": name,
                                     "frames": frames_pair, "window": [lo, hi]})
    return {"warnings": warnings, "insufficient": insufficient}


def presence_warnings(shot):
    """兼容入口：只返回可判定的矛盾（证据不足走 `presence_assessment`）."""
    return presence_assessment(shot)["warnings"]