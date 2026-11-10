# -*- coding: utf-8 -*-
"""运镜与状态语义（M2.2 修订，U2）.

目的：把「谁在做动作」「是不是相机指令」「是不是否定」「什么时候发生」从原始
文本里**显式提取成内部记录**，再据记录判冲突——不再用整段子串扫描。

三类此前的误判都是因为缺少这层记录：

  1. 人物/载具动作被当成相机动作（「他起身」里的「起」不是升镜；「人物不动」
     不是固定机位）；
  2. 否定被读成肯定（「推近，禁止拉远」两边都命中 → 假冲突）；
  3. 分段标记过宽（motion_desc 里人物动作的「先/再」把整镜的相机冲突豁免）。

本模块只做纯计算，不读写任何字段；schema 不新增生产字段（记录只存在于检测期）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 词表：相机动作（方向轴 + 规范方向）
# ---------------------------------------------------------------------------
#: (axis, direction, 词表, 是否必须显式相机主体)
#: 「需显式主体」的写法在 motion_desc 里单看可能指人/物（水位上升、船靠近），
#: 只有同分句出现镜头/机位等相机名词时才按相机动作计。
CAMERA_TERMS: tuple = (
    ("zoom", "in", ("推近", "推进", "推镜", "向前推", "前推", "推入", "拉近"), False),
    ("zoom", "in", ("靠近", "逼近"), True),
    ("zoom", "out", ("拉远", "拉后", "拉镜", "向后拉", "后拉", "拉出", "退远"), False),
    ("pan", "left", ("左摇", "向左摇", "左移", "向左移", "向左横移", "左侧平移"), False),
    ("pan", "right", ("右摇", "向右摇", "右移", "向右移", "向右横移", "右侧平移"), False),
    ("vertical", "up", ("上升", "升镜", "升高", "向上摇", "上摇", "升起"), True),
    ("vertical", "up", ("升镜", "拉升"), False),
    ("vertical", "down", ("下降", "降镜", "降低", "向下摇", "下摇"), True),
    ("vertical", "down", ("降镜",), False),
    ("orbit", "cw", ("顺时针环绕", "顺旋", "顺时针旋转"), False),
    ("orbit", "ccw", ("逆时针环绕", "逆旋", "逆时针旋转"), False),
    ("track", "track", ("跟拍", "跟随拍摄", "镜头追随", "追踪拍摄"), False),
    ("fixed", "fixed", ("固定机位", "固定镜头", "机位固定", "镜头固定", "静止镜头",
                        "镜头不动", "机位不动", "固定不动"), False),
)

#: 不带相机名词也可能指人的「类相机」写法——只在 camera_movement 字段或显式
#: 相机主体下才采信。
AMBIGUOUS_ONLY = {"靠近", "逼近", "上升", "升高", "上升", "下降", "降低", "升起",
                  "推近", "推进", "推入"}

CAMERA_NOUNS = ("镜头", "机位", "摄影机", "摄像机", "运镜", "景别", "焦距",
                "画面推", "画面拉", "镜头语言")

#: 人物/载具/物体主体标记。出现在运动词之前时，该分句描述的不是相机。
ACTOR_MARKERS = ("他", "她", "它", "他们", "她们", "众人", "人物", "男子", "女子",
                 "少年", "少女", "老者", "身影", "对方", "大家", "二人", "两人",
                 "一人", "有人", "战士", "侍从", "船夫", "车夫")
OBJECT_SUBJECTS = ("船", "舟", "车", "轿", "马", "载具", "门", "窗", "碗", "杯",
                   "伞", "刀", "剑", "箫", "笔", "书", "箱", "袋", "灯", "旗",
                   "水面", "水位", "潮水", "人群")

NEGATORS = ("不再", "没有", "并没", "并未", "并不", "不能", "不会", "不得", "不要",
            "禁止", "避免", "无需", "勿", "别", "未", "不")

#: 顺序/分段标记（出现在分句开头，或两个相机陈述之间）。
SEQ_START = ("先", "首先", "起初", "开场", "起始", "随后", "然后", "接着", "之后",
             "随即", "紧接着", "片刻后", "最后", "末段", "转而", "再")
_TIME_START_RE = re.compile(
    r"^(?:第[一二三四五六七八九十]段|前段|后段|"
    r"(?:前|后|第)?\s*\d+(?:\.\d+)?\s*(?:-|~|—|至)?\s*\d*(?:\.\d+)?\s*秒?)")

_CLAUSE_SPLIT_RE = re.compile(r"[，。；！？,;!?\n]+")


@dataclass(frozen=True)
class CameraStatement:
    """一条相机陈述（检测期内部记录，不落盘）."""

    field: str            # camera_movement / motion_desc
    clause: str
    term: str
    axis: str
    direction: str
    polarity: str         # affirm / negated
    subject: str          # camera / other / unclear
    segment: int
    offset: int           # 在分句内的字符位置
    evidence: str = field(default="")

    @property
    def affirmative(self) -> bool:
        return self.polarity == "affirm"

    @property
    def is_camera(self) -> bool:
        return self.subject == "camera"


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(text or "") if c.strip()]


def _iter_terms(clause: str):
    """按出现位置返回 (term, axis, direction, needs_subject, offset)，长词优先."""
    found = []
    taken: list[tuple[int, int]] = []
    flat = []
    for axis, direction, words, needs in CAMERA_TERMS:
        for w in words:
            flat.append((w, axis, direction, needs))
    for term, axis, direction, needs in sorted(flat, key=lambda x: -len(x[0])):
        start = 0
        while True:
            pos = clause.find(term, start)
            if pos < 0:
                break
            end = pos + len(term)
            if not any(not (end <= s or pos >= e) for s, e in taken):
                taken.append((pos, end))
                found.append((term, axis, direction, needs, pos))
            start = pos + 1
    found.sort(key=lambda x: x[4])
    return found


def _negated(clause: str, pos: int) -> bool:
    window = clause[max(0, pos - 7):pos]
    return any(neg in window for neg in NEGATORS)


def _has_camera_noun(clause: str) -> bool:
    return any(n in clause for n in CAMERA_NOUNS)


def _has_actor_before(clause: str, pos: int) -> bool:
    head = clause[:pos]
    return any(a in head for a in ACTOR_MARKERS + OBJECT_SUBJECTS)


def _starts_with_seq_marker(clause: str) -> bool:
    text = clause.lstrip()
    if _TIME_START_RE.match(text):
        return True
    for marker in sorted(SEQ_START, key=len, reverse=True):
        if not text.startswith(marker):
            continue
        if marker == "再" and text.startswith("不再"):
            continue
        return True
    return False


def _seq_between(clause: str, left: int, right: int) -> bool:
    middle = clause[left:right]
    return any(m in middle for m in ("随后", "然后", "接着", "之后", "随即",
                                     "紧接着", "片刻后", "转而", "再", "又"))


def extract_camera_statements(shot: dict) -> list[CameraStatement]:
    """从 camera_movement / motion_desc 提取**有归属**的相机陈述.

    subject 判定顺序：
      1. 显式相机名词（镜头/机位/…）→ camera；
      2. 字段就是 camera_movement 且没有人物/物体主体 → camera；
      3. 人物/物体主体出现在运动词之前 → other（人物动作，不是运镜）；
      4. motion_desc 里的省略主语 → 同字段上一条明确相机陈述且中间无新主体 →
         继承 camera（合法省略主语只在同一相机指令链内继承）；
      5. 其余 → unclear（不硬判）。
    """
    statements: list[CameraStatement] = []
    last_camera_clause = {"camera_movement": False, "motion_desc": False}
    for field_name in ("camera_movement", "motion_desc"):
        text = str(shot.get(field_name) or "")
        if not text:
            continue
        segment = 0
        for clause_index, clause in enumerate(_clauses(text)):
            if clause_index and _starts_with_seq_marker(clause):
                # 句首分段标记开启新段；第一句的「先…」仍属第 0 段
                segment += 1
            terms = _iter_terms(clause)
            prev_pos = 0
            camera_noun_here = _has_camera_noun(clause)
            for term, axis, direction, needs_subject, pos in terms:
                if pos and _seq_between(clause, prev_pos, pos):
                    segment += 1
                prev_pos = pos + len(term)
                if _has_actor_before(clause, pos) or any(o in clause[:pos] for o in OBJECT_SUBJECTS):
                    subject = "other"
                elif field_name == "camera_movement":
                    # 该字段本身就是相机指令来源；只有明确写了人物/物体主体才让位。
                    subject = "camera"
                elif camera_noun_here:
                    subject = "camera"
                elif last_camera_clause[field_name] and not any(
                        a in clause for a in ACTOR_MARKERS + OBJECT_SUBJECTS):
                    # 同一相机指令链内的合法省略主语
                    subject = "camera"
                else:
                    # motion_desc 里的裸「推进/推近/拉远」可能是人物或载具在推进
                    # （「让出小径推进」「抬脚跨过浅沟推进」），主体未写明就是
                    # 证据不足——不硬判冲突，也不把它算作相机陈述。
                    subject = "unclear"
                statements.append(CameraStatement(
                    field=field_name, clause=clause, term=term, axis=axis,
                    direction=direction,
                    polarity="negated" if _negated(clause, pos) else "affirm",
                    subject=subject, segment=segment, offset=pos,
                    evidence=clause,
                ))
            last_camera_clause[field_name] = camera_noun_here or any(
                s.subject == "camera" for s in statements if s.clause == clause)
    return statements


def _direction_label(axis: str, direction: str) -> str:
    return {
        ("zoom", "in"): "推近", ("zoom", "out"): "拉远",
        ("pan", "left"): "左摇", ("pan", "right"): "右摇",
        ("vertical", "up"): "上升", ("vertical", "down"): "下降",
        ("orbit", "cw"): "顺时针环绕", ("orbit", "ccw"): "逆时针环绕",
        ("track", "track"): "跟拍", ("fixed", "fixed"): "固定机位",
    }.get((axis, direction), direction)


def camera_conflicts(shot: dict) -> list[dict]:
    """返回运镜冲突记录：[{kind, severity, message, field, evidence, ...}]."""
    statements = extract_camera_statements(shot)
    if not statements:
        return []
    camera = [s for s in statements if s.is_camera]

    # 某个字段是否显式分段：决定「固定机位」是该字段的整镜状态还是某一段状态。
    # 只在 motion_desc 里分段（人物动作/后段推近）不改变 camera_movement 的
    # 整镜含义——「固定机位」仍与后段推近冲突。
    segmented_fields = {s.field for s in camera if s.segment > 0}
    out: list[dict] = []

    def _add(kind: str, severity: str, message: str, field_name: str,
             evidence: str, statement: CameraStatement | None = None) -> None:
        out.append({"kind": kind, "severity": severity, "message": message,
                    "field": field_name, "evidence": evidence,
                    "statement": statement})

    # 1) 同段互斥方向（推/拉、左/右、升/降、顺/逆）
    by_seg: dict[tuple, list[CameraStatement]] = {}
    for s in camera:
        if s.axis == "fixed" or not s.affirmative:
            continue
        by_seg.setdefault((s.axis, s.segment), []).append(s)
    for (axis, seg), items in by_seg.items():
        directions = {s.direction for s in items}
        if len(directions) < 2:
            continue
        picked = []
        for s in items:
            if s.direction not in [p.direction for p in picked]:
                picked.append(s)
        left, right = picked[0], picked[1]
        _add("opposites", "visual_risk",
             "同一段相机指令内出现互斥运镜：「%s」与「%s」同时要求（无先后分段）"
             % (_direction_label(axis, left.direction), _direction_label(axis, right.direction)),
             left.field if left.field == right.field else left.field,
             (left.field + "：" + left.clause + " / " + right.field + "：" + right.clause),
             left)

    # 2) 固定机位 vs 同段/整镜运动
    fixed = [s for s in camera if s.axis == "fixed" and s.affirmative]
    moves = [s for s in camera if s.axis != "fixed" and s.affirmative]
    for f in fixed:
        scope_all = f.field not in segmented_fields
        hits = [m for m in moves if scope_all or m.segment == f.segment]
        if not hits:
            continue
        m = hits[0]
        _add("fixed_vs_move", "visual_risk",
             "camera_movement 为「%s」，但要求「%s」；固定机位与运动镜头互斥"
             % (f.term, _direction_label(m.axis, m.direction)),
             f.field, f.field + "：" + f.clause + " / " + m.field + "：" + m.clause, f)

    # 3) 证据不足：出现互斥方向但主体/否定归属不明 → 只提示，不阻断
    if not out:
        unclear = [s for s in statements if s.subject == "unclear"]
        # 只有在**同一段**里真的出现互斥方向、且有归属不明的陈述时才提示；
        # 显式分段的「先推近再拉远」不在同一段，不产生噪声。
        seg_dirs: dict[tuple, set] = {}
        for s in statements:
            if s.axis != "fixed" and s.affirmative:
                seg_dirs.setdefault((s.axis, s.segment), set()).add(s.direction)
        if unclear and any(len(v) > 1 for v in seg_dirs.values()):
            u = unclear[0]
            _add("ambiguous", "advisory",
                 "检测到可能的运镜互斥（%s），但相机主体/否定归属不明确；"
                 "不按硬冲突处理，请人工核对" % u.clause,
                 u.field, u.clause, u)
    return out


# ---------------------------------------------------------------------------
# 跨镜状态：实体级匹配
# ---------------------------------------------------------------------------
#: 状态族：(族名, 前态词, 后态词)。前态出现在上镜 LF、后态出现在下镜 FF 且
#: 同一实体、没有过渡依据时才提示。
STATE_FAMILIES: tuple = (
    ("stance", ("倒地", "倒伏", "仰倒", "僵卧", "伏地", "倒地不起", "瘫倒"),
     ("站立", "站起", "起身", "端坐", "跪坐", "跪立", "跃起", "站定")),
    ("vitality", ("已无神", "双目无神", "气绝", "身亡", "死去", "毙命"),
     ("睁眼", "睁目", "开口", "说话", "站起", "起身", "呼吸")),
    ("wetness", ("浑身湿透", "湿透", "浸透", "衣袍滴水"), ("干爽", "衣袍干燥", "滴水未沾")),
    ("hands", ("空手", "手中无物", "赤手空拳", "两手空空"),
     ("持刀", "握刀", "持剑", "握剑", "手持", "提着", "攥着")),
)

#: 合法的状态过渡依据（出现在**同一实体**的下一镜描述里才放行）。
TRANSITION_MARKERS = ("已起身", "被扶起", "整理后", "换过", "重新站起", "伤已包扎",
                      "卸下", "放下", "交出", "丢弃", "方才起身", "这才站起",
                      "醒来", "苏醒", "包扎", "换衣", "更衣", "拾起", "接过")

TIME_JUMP_MARKERS = ("回忆", "闪回", "倒叙", "数日前", "数年前", "次日", "翌日",
                      "第二天", "隔日", "多年后", "三日后", "半个月后", "一个月后",
                      "时间流逝", "与此同时", "同一时间", "另一处", "转场")


def _entry_text(entry: dict) -> str:
    return " ".join(filter(None, [
        str(entry.get("pose") or ""),
        str(entry.get("position") or ""),
        " ".join(str(p) for p in (entry.get("props") or [])),
        " ".join(str(s) for s in (entry.get("states") or []) if isinstance(entry.get("states"), list)),
        str(entry.get("gaze_target") or ""),
    ]))


def _families(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for family, before, after in STATE_FAMILIES:
        hit = {w for w in before if w in text} | {w for w in after if w in text}
        if hit:
            out[family] = hit
    return out


def _before_hits(families: dict[str, set[str]]) -> dict[str, str]:
    out = {}
    for family, before, _after in STATE_FAMILIES:
        hit = next((w for w in before if w in families.get(family, set())), "")
        if hit:
            out[family] = hit
    return out


def _after_hits(families: dict[str, set[str]]) -> dict[str, str]:
    out = {}
    for family, _before, after in STATE_FAMILIES:
        hit = next((w for w in after if w in families.get(family, set())), "")
        if hit:
            out[family] = hit
    return out


def _names_in(text: str, known: tuple) -> list[str]:
    return [n for n in known if n and n in (text or "")]


#: 代词主体（无已知角色名时的显式归属）。长词在前，避免「他们」被「他」命中。
_PRONOUNS = ("他们", "她们", "它们", "他", "她", "它")


def _subject_of(clause: str, known: tuple) -> str:
    """该分句的**单一**显式主体：恰好一个已知角色名，或恰好一个人称代词.

    两个及以上主体 → 返回空串（归属不明，不硬判）。
    """
    names = {n for n in _names_in(clause, known) if n}
    if len(names) == 1:
        return next(iter(names))
    if names:
        return ""
    text = clause or ""
    for pronoun in _PRONOUNS:
        if pronoun in text:
            # 「他们」命中后不再把「他」当成另一个主体
            return pronoun
    return ""


def state_break_candidates(prev: dict, curr: dict, names: tuple) -> list[dict]:
    """相邻镜「上镜 LF 终态 → 下镜 FF 初态」的实体级断裂候选.

    优先读两帧的 frame_layouts（同一实体名匹配）；布局缺失时才用文本，且只在
    **同一实体被显式点名**的分句里比较——不把甲的终态和乙的初态相比。
    """
    from step01_script_gen.cross_field_checks import frame_entries

    out: list[dict] = []
    lf_text = str(prev.get("lf_desc") or "")
    ff_text = str(curr.get("ff_desc") or "")
    if not lf_text or not ff_text:
        return out
    if any(mark in ff_text for mark in TIME_JUMP_MARKERS):
        return out

    prev_entries = {str(e.get("name") or "").strip(): e
                    for e in frame_entries(prev, "lf") if e.get("name")}
    curr_entries = {str(e.get("name") or "").strip(): e
                    for e in frame_entries(curr, "ff") if e.get("name")}

    compared = 0
    for name in sorted(set(prev_entries) & set(curr_entries)):
        before = _before_hits(_families(_entry_text(prev_entries[name])))
        after = _after_hits(_families(_entry_text(curr_entries[name])))
        compared += 1
        if not before or not after:
            continue
        curr_text = _entry_text(curr_entries[name])
        if any(mark in curr_text for mark in TRANSITION_MARKERS):
            continue
        family = next(f for f in before if f in after)
        out.append({"entity": name, "family": family,
                    "before": before[family], "after": after[family],
                    "evidence": "%s → %s" % (_entry_text(prev_entries[name]),
                                             _entry_text(curr_entries[name])),
                    "basis": "frame_layouts"})

    if out or compared:
        return out

    # 文本路径：只接受「同一实体被显式点名」的成对分句。
    prev_clauses = _clauses(lf_text)
    curr_clauses = _clauses(ff_text)
    ambiguous = 0
    for p_clause in prev_clauses:
        p_before = _before_hits(_families(p_clause))
        if not p_before:
            continue
        name = _subject_of(p_clause, names)
        if not name:
            ambiguous += 1
            continue
        for c_clause in curr_clauses:
            if _subject_of(c_clause, names) != name:
                continue
            c_after = _after_hits(_families(c_clause))
            if not c_after:
                continue
            if any(mark in c_clause for mark in TRANSITION_MARKERS):
                continue
            family = next(f for f in p_before if f in c_after)
            out.append({"entity": name, "family": family,
                        "before": p_before[family], "after": c_after[family],
                        "evidence": p_clause + " → " + c_clause, "basis": "text"})
            break
    if not out and ambiguous:
        out.append({"entity": "", "family": "", "before": "", "after": "",
                    "evidence": "文本出现状态词但主体归属不明", "basis": "ambiguous"})
    return out


__all__ = ["CameraStatement", "TIME_JUMP_MARKERS", "camera_conflicts",
           "extract_camera_statements", "state_break_candidates"]
