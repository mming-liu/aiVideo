# -*- coding: utf-8 -*-
"""镜头运动冲突报告（离线、零模型调用）.

S01 规格：
- 先判"无冲突"：不同主体、不同时段、或不互斥 → 不产生告警；
- definite：同一主体 + 同一时段 + 明确肯定 + 互斥 + 证据充分；
- suspected：存在冲突迹象，但否定/引用/条件/分段/主体关系无法可靠解析；
- signal：仅相似度、段数等提示，**不是冲突结论**；
- 关系字段分开记录：subject_relation / time_relation ∈ same|different|unknown；
- 不靠词命中直接下结论；三类记录都不自动成为生产硬门。
"""
from __future__ import annotations

import difflib
import re

CAMERA_MOVE_TERMS = (
    "镜头掠过", "镜头横移", "镜头推近", "镜头拉远", "镜头跟拍", "镜头环绕",
    "镜头摇", "镜头升", "镜头降", "横移", "推近", "推镜", "拉远", "拉镜",
    "跟拍", "跟随", "环绕", "摇镜", "摇摄", "升降", "俯冲", "拉升",
)

FIXED_CAMERA_VALUES = ("固定机位", "固定", "static", "fixed", "locked")

DISPLACE_TERMS = (
    "滑出", "驶出", "走出", "跑出", "进入画面", "离开画面", "穿过", "越过",
    "靠近", "远离", "腾空", "起身", "转身", "回头", "倒下", "站起", "坐下",
)

_QUOTE_PAIRS = (("“", "”"), ("「", "」"), ('"', '"'))
_NEGATION = re.compile(r"(?:不|别|勿|禁止|避免|不要)[^，。；]{0,6}")
_CONDITIONAL = re.compile(r"(?:若|如果|要是|倘若)[^，。；]{0,10}")
#: 分段运镜：前N秒/随后/然后/接着/之后/先…再…
_SEGMENT = re.compile(r"(?:前\s*\d+(?:\.\d+)?\s*秒|后\s*\d+(?:\.\d+)?\s*秒|第\s*\d+(?:\.\d+)?\s*秒|随后|然后|接着|之后|先[^，。；]{0,6}再)")
#: 通用运镜词需要“镜头/机位/画面”作主语，避免把“小孩跟随母亲”当成运镜
_CAMERA_CONTEXT = ("镜头", "机位", "画面")
_GENERIC_TERMS = ("横移", "推近", "推镜", "拉远", "拉镜", "跟拍", "跟随",
                  "环绕", "摇镜", "摇摄", "升降", "俯冲", "拉升")


def _is_fixed(camera: str) -> bool:
    low = camera.strip().lower()
    if "非固定" in low or "不固定" in low:
        return False
    return any(v in low for v in FIXED_CAMERA_VALUES)


def _camera_subject_ok(motion: str, start: int) -> bool:
    """通用运镜词前的主语必须是相机（允许少量副词填充），不能只是邻近出现“画面”."""
    prefix = motion[max(0, start - 12):start]
    return bool(re.search(
        r"(?:镜头|机位|画面)[，,、]?(?:缓缓|慢慢|开始|继续|向前|向后|向左|向右|快速|缓慢|平稳|随即|然后|随后|地){0,3}$",
        prefix))


def _quoted(text: str, start: int, end: int) -> bool:
    for left, right in _QUOTE_PAIRS:
        l = text.rfind(left, 0, start)
        r = text.find(right, end)
        if l != -1 and r != -1 and l < start < r:
            return True
    return False


def _classify_phrase(motion: str, term: str, start: int, end: int):
    """返回 (class, reason, time_relation)；无法确认是相机指令时返回 None."""
    if term in _GENERIC_TERMS and not _camera_subject_ok(motion, start):
        return None  # “画面中小孩跟随母亲”这类普通动词，不是运镜
    if _quoted(motion, start, end):
        return "suspected", "运镜词位于引号内（引用/转述），不能确认是镜头指令", "unknown"
    prefix = motion[max(0, start - 12):start]
    if _NEGATION.search(prefix):
        return "suspected", "运镜词前有否定（不要/禁止等），是否定指令而非肯定运镜", "unknown"
    if _CONDITIONAL.search(prefix):
        return "suspected", "运镜词处于条件句（若/如果），不是无条件指令", "unknown"
    if _SEGMENT.search(prefix) or _SEGMENT.search(motion[:start]):
        return "suspected", "运镜词处于分段运镜（前/后N秒、随后、然后），不能按整镜固定机位判定", "unknown"
    return "definite", "同一镜头、同一主体（相机）、肯定且互斥的运镜指令", "same"


def motion_report(shot: dict) -> list[dict]:
    """结构化运动报告；空列表 = no_conflict."""
    records: list[dict] = []
    camera = str(shot.get("camera_movement") or "").strip()
    motion = str(shot.get("motion_desc") or "")
    ff = str(shot.get("ff_desc") or "").strip()
    lf = str(shot.get("lf_desc") or "").strip()

    if _is_fixed(camera):
        for term in CAMERA_MOVE_TERMS:
            start = motion.find(term)
            if start == -1:
                continue
            classified = _classify_phrase(motion, term, start, start + len(term))
            if classified is None:
                continue
            cls, reason, time_relation = classified
            records.append({
                "code": "camera_motion_conflict",
                "class": cls,
                "field_paths": ["camera_movement", "motion_desc"],
                "subject": "camera",
                "subject_relation": "same",
                "time_relation": time_relation,
                "time_scope": "shot",
                "snippet": motion[max(0, start - 10):start + len(term) + 10],
                "evidence": {"camera_movement": camera, "term": term},
                "reason": reason,
                "message": "camera_movement=%r 与 motion_desc 的运镜词 %r 存在冲突迹象（%s）"
                           % (camera, term, reason),
                "needed_change": "交内容修订：统一 camera_movement 与 motion_desc 的运镜，或明确分段",
            })

    if ff and lf and any(t in motion for t in DISPLACE_TERMS):
        ratio = difflib.SequenceMatcher(None, ff, lf).ratio()
        if ratio >= 0.85:
            records.append({
                "code": "frame_displacement_missing",
                "class": "signal",
                "field_paths": ["ff_desc", "lf_desc"],
                "subject": "scene",
                "subject_relation": "same",
                "time_relation": "different",
                "time_scope": "start_vs_end",
                "snippet": "",
                "evidence": {"similarity": round(ratio, 3)},
                "reason": "首尾帧文本相似度 %.2f：仅作待校准提示，不证明位移不可见" % ratio,
                "message": "首尾帧文本相似度 %.2f（提示信号，非冲突结论）" % ratio,
                "needed_change": "人工核对首尾帧是否表达终态差异",
            })

    segments = [x for x in motion.split("；") if x.strip()]
    duration = float(shot.get("duration_sec") or 0)
    if len(segments) >= 3 and duration and duration <= 6.5:
        records.append({
            "code": "motion_density_high",
            "class": "signal",
            "field_paths": ["motion_desc", "duration_sec"],
            "subject": "scene",
            "subject_relation": "unknown",
            "time_relation": "same",
            "time_scope": "shot",
            "snippet": "",
            "evidence": {"segments": len(segments), "duration_sec": duration},
            "reason": "单镜动作段较多（%d 段 / %.2fs），仅作提示" % (len(segments), duration),
            "message": "motion_desc 有 %d 段动作、时长 %.2fs（提示信号，非冲突结论）"
                       % (len(segments), duration),
            "needed_change": "人工判断是否需要拆镜",
        })
    return records


def motion_warnings(shot: dict) -> list[dict]:
    """兼容入口：只返回 definite/suspected（signal 不算告警）."""
    return [r for r in motion_report(shot) if r["class"] in ("definite", "suspected")]


def motion_outcome(shot: dict) -> str:
    """no_conflict | definite | suspected（signal 不影响结论）."""
    classes = {r["class"] for r in motion_report(shot)}
    if "definite" in classes:
        return "definite"
    if "suspected" in classes:
        return "suspected"
    return "no_conflict"
