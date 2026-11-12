"""S07. 脚本级确定性一致性检查（2026-11-12）.

背景：S06 有限对照证明，把「重复文本 / 景别矛盾 / 道具与状态链」交给模型审计时，
同一份稿的 blocking 会随轮次漂移，而且模型漏报过已确认缺陷。本模块把这三类里
**代码能证明的部分**转成确定性检查，减少对模型严重度的依赖。

检查项（finding code）：

  · ``DUPLICATE_TEXT``      同一字段内相邻重复的括号说明 / 分句；
  · ``FRAMING_CONFLICT``    同一镜内**静态**景别声明互相矛盾（无运镜依据）；
  · ``PROP_STATE_CHAIN``    道具在上一帧已脱手 / 放下，后一帧又回到手中，
                            中间没有取回动作、也没有时间跳跃依据；
  · ``STATE_CHAIN_CONFLICT`` 发型等成对状态在相邻帧之间翻转，中间没有过渡依据；
                            角色静态形象与首见状态相反、且此前没有任何过渡，也算；
  · ``STATE_CONFLICT``      同一字段同时声明一对状态的两侧（如 prompt 既写挽髻
                            又写披头散发）；
  · ``STATE_RESULT_LEAK``   首帧已经呈现本镜动作之后的结果态；
  · ``LF_PROCESS_STATE``    尾帧仍停留在显式未收束的过程态（与 ``STATE_RESULT_LEAK``
                            互补：后者是首帧提前呈现结果态）；
  · ``ADDED_DIALOGUE``      脚本台词在**调用方提供的对照源**里找不到逐字依据
                            （新增台词/画外音）。未传对照源时该码**不产出**。

## 边界（与正式门禁的关系）

* 本模块**只读**：不修改 script、不产生修复动作、不改变任何 issue 的 severity，
  也不参与 blocking 计数；
* 找不到双方逐字依据时**不出 finding**；宁缺勿滥，未覆盖的部分仍由语义审计负责；
* 时间跳跃 / 场景变化 / 取回动作 / 运镜轨迹 / 明说动作延续到镜外是**反证**：
  命中即不出 finding；
* 产物由 ``audit_adjudication.build_shadow_report`` 以 ``gate_effect=shadow_only``
  落盘，接入正式门禁属于准入语义变更，不在本模块内做；
* ``ADDED_DIALOGUE`` 的「新增」必须有对照源才能判定：``scan_script`` 只在调用方
  显式传入 ``source_text`` 时检查，未传入时宁缺勿滥、不产出该码；
* 正文 `shots` 与 `hook` 分开：重复/景别扫 `shots`，跨镜道具/状态链只连正文，
  不把独立开场钩子接到第 1 镜（hook 的检查留给后续工作包）。

## 内容无关性

角色、道具与状态词都从本次输入取得或来自通用视觉词表（景别梯子、脱手/持有动词、
发型/衣着状态词），不绑定任何具体作品、章节或人物。
"""
from __future__ import annotations

import re

from shared.kf_time import get_kf_t_sec
from step01_script_gen.split_visuals import FRAMING_LADDER

# ---------------------------------------------------------------------------
# 通用词表
# ---------------------------------------------------------------------------

#: 括号说明（中英文括号）；内容长度 >= 2 才可能作为重复单元。
_PAREN_RE = re.compile(r"[（(]([^（）()]{2,160})[）)]")

#: 相邻重复分句的最小长度：太短的分句可能是合法的强调/节奏（「看着，看着」）。
_DUP_MIN_CLAUSE = 6

#: 景别的非梯子别名 → 梯子上的标准名（长词优先匹配）。
_FRAMING_ALIASES = {
    "大全景": "全景",
    "中远景": "中景",
    "鸟瞰远景": "大远景",
    "极特写": "大特写",
}
_FRAMING_TERMS = sorted(set(FRAMING_LADDER) | set(_FRAMING_ALIASES),
                       key=len, reverse=True)
_FRAMING_RE = re.compile("|".join(re.escape(t) for t in _FRAMING_TERMS))

#: 运镜/景别轨迹的显式声明。只用来**排除**静态景别冲突，不用来判缺陷。
_TRAJECTORY_MARKERS = (
    "推近", "推至", "推向", "推成", "推镜", "拉远", "拉开", "拉至",
    "摇到", "摇至", "移到", "移至", "变焦", "由远及近", "由近及远",
    "由远到近", "由近到远", "切到", "切至", "切换到", "转为", "过渡到",
    "镜头推", "镜头从",
)

#: 道具脱手 / 放下的**显式**标记。放在/搁在等位置动词也算「已离手」。
_RELEASE_MARKERS = (
    "脱手", "松手", "撒手", "脱开", "放下", "搁在", "搁下", "放在", "置于",
    "丢下", "丢了", "丢掉", "摔在", "摔落", "滑落", "掉落", "坠地", "落地",
)

#: 持有标记（两字以上，避免单字「提/拿」在「提前/拿手」里误命中）。
_HOLD_MARKERS = (
    "握着", "手持", "持着", "提着", "拿着", "攥着", "紧握", "反握", "正握",
    "举着", "端着", "抱着", "抓住", "持刀", "持剑", "持匕", "持箫", "持杆",
)

#: 身体部位 + 道具的连写（「右手酒壶」「手中灯盏」）也视为持有。
_HAND_RE = re.compile(r"(?:右手|左手|双手|手中|手里|指间|掌心|臂弯|怀中|怀里)")

#: 取回动作：出现即说明状态变化有依据。
_ACQUIRE_MARKERS = (
    "捡起", "拾起", "拿起", "拿回", "取回", "接过", "夺回", "抢过", "捞起",
    "抄起", "抓起", "掏出", "取出", "摸出", "端起", "重新拿", "重新提起",
    "重新握住",
)

#: 否定词：取回动词**之前**出现即表示该动作被否定（「茶盏未再被拿起」不是取回）。
#: 只做同分句前缀判断，不跨分句、不猜语义；长词在前仅为可读性，匹配用子串包含。
_NEGATION_MARKERS = (
    "未曾", "没有", "并未", "仍未", "不再", "不曾", "不可",
    "未", "没", "无", "不", "莫", "勿",
)

#: 显式时间跳跃（不含「淡入淡出/叠化」——它们在当前稿里也用于连续动作的过渡，
#: 单凭转场类型不能证明时间过去了；只有文字依据才算）。
_TIME_JUMP_MARKERS = (
    "次日", "翌日", "第二天", "隔日", "数日后", "三日后", "三天后",
    "一柱香", "一炷香", "半个时辰", "一个时辰", "片刻之后", "许久之后",
    "多年后", "多年以后", "回忆", "闪回", "时间流逝", "天亮之后", "入夜之后",
)

#: 尾帧「动作延续到镜外」的显式反证：写明本镜不要求动作收束时才豁免。
#: 只收**明说**的延续/切走，不把普通多段动作当成故意留白。
_LF_OPEN_END_MARKERS = (
    "动作未完", "动作未收", "未及收势", "未收势",
    "持续到镜末", "持续到镜头结束", "延续到下一镜", "延续至下一镜",
    "留待下一镜", "切在动作中", "镜头在动作中切",
)

#: 尾帧同一分句已明说收束/停住：过程标记只是过渡描述，不是「停在过程态」。
_LF_CLOSURE_MARKERS = (
    "停住", "停下", "停手", "收势", "收手", "完成", "结束",
    "落定", "定格", "静止", "已停", "不再动", "终了",
)

#: 台词引号对（成对或同字符）；只用于从播出文本里提取**显式台词**。
_QUOTE_PAIRS = (
    ("「", "」"), ("『", "』"), ("“", "”"), ("‘", "’"),
    ('"', '"'), ("'", "'"),
)

#: 成对状态：只放**可观察、成对、且方向明确**的状态词。
_STATE_PAIRS = (
    {
        "name": "hair",
        "label": "发型",
        "up_label": "挽起",
        "down_label": "散开",
        # 挽起 / 束起（只放明确的发式词；「挽起袖子」「盘起腿」不算）
        "up": ("挽髻", "盘发", "束发", "束髻", "束冠", "美人髻", "发髻"),
        # 散开（不单收「散发」：它也可能是「散发着酒气」）
        "down": ("披头散发", "披散", "长发散落", "秀发披落", "发丝散落",
                 "青丝散落", "头发散落", "披落"),
        # 状态变化过程（必须与头发/发饰同句才成立）
        "transitions": ("散落", "披落", "碰落", "打散", "扯散", "削落", "断落",
                        "落水", "松开", "解开", "扯开", "滑落", "散开"),
        "terms": ("发髻", "青丝", "秀发", "头发", "发丝", "长发", "鬓", "髻", "步摇"),
    },
)

_FINDING_ORDER = {
    "DUPLICATE_TEXT": 0,
    "FRAMING_CONFLICT": 1,
    "PROP_STATE_CHAIN": 2,
    "STATE_CHAIN_CONFLICT": 3,
    "STATE_CONFLICT": 4,
    "STATE_RESULT_LEAK": 5,
    "LF_PROCESS_STATE": 6,
    "ADDED_DIALOGUE": 7,
}
_FIELD_ORDER = {
    "prompt": 0,
    "ff_desc": 1,
    "keyframes": 2,
    "lf_desc": 3,
    "motion_desc": 4,
    "narration_segment": 5,
}


# ---------------------------------------------------------------------------
# 基础读取
# ---------------------------------------------------------------------------

def _shots(script: dict) -> list[dict]:
    shots = (script or {}).get("shots") if isinstance(script, dict) else None
    return [s for s in shots if isinstance(s, dict)] if isinstance(shots, list) else []


def _shot_id(shot: dict, pos: int) -> str:
    sid = shot.get("index", pos + 1)
    return str(sid) if sid is not None and str(sid).strip() else str(pos + 1)


def _clauses(text) -> list[str]:
    return [c.strip() for c in re.split(r"[，。；！？\n]", str(text or "")) if c.strip()]


def _field_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
        return "\n".join(value)
    return ""


def _character_names(script: dict) -> list[str]:
    names: list[str] = []
    for item in (script or {}).get("characters") or []:
        if isinstance(item, dict):
            name = str(item.get("identifier") or item.get("name") or "").strip()
            if name:
                names.append(name)
    for shot in _shots(script):
        layouts = shot.get("frame_layouts") if isinstance(shot.get("frame_layouts"), dict) else {}
        for bucket in ("ff", "lf"):
            for entry in layouts.get(bucket) or []:
                if isinstance(entry, dict) and str(entry.get("name") or "").strip():
                    names.append(str(entry.get("name")).strip())
        for frame in layouts.get("kfs") or []:
            for entry in frame or []:
                if isinstance(entry, dict) and str(entry.get("name") or "").strip():
                    names.append(str(entry.get("name")).strip())
        for entry in shot.get("char_layout") or []:
            if isinstance(entry, dict) and str(entry.get("name") or "").strip():
                names.append(str(entry.get("name")).strip())
    return sorted({n for n in names if n}, key=len, reverse=True)


def _explicit_time_jump(shot: dict) -> bool:
    if not isinstance(shot, dict):
        return False
    if shot.get("time_jump"):
        return True
    bridge = str(shot.get("context_bridge") or "")
    if any(m in bridge for m in _TIME_JUMP_MARKERS):
        return True
    transition = str(shot.get("clip_transition") or "")
    return ("闪回" in transition) or ("回忆" in transition)


def _scene_changed(a: dict, b: dict) -> bool:
    """两镜的 scene/location 是否明确改变（双方都非空才比较）."""
    sa = str(a.get("scene") or "").strip()
    sb = str(b.get("scene") or "").strip()
    if sa and sb and sa != sb:
        return True
    la = str(a.get("location") or "").strip()
    lb = str(b.get("location") or "").strip()
    return bool(la and lb and la != lb)


def _frames(script: dict) -> list[dict]:
    """全稿帧序列：ff → 按 t_sec 排序的 kf → lf（跳过 hook）.

    每帧保留**原始** keyframes 下标（`keyframes[N]` 的 N），排序只影响扫描顺序。
    """
    out: list[dict] = []
    for pos, shot in enumerate(_shots(script)):
        sid = _shot_id(shot, pos)
        try:
            duration = float(shot.get("duration_sec") or shot.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        ff = _field_text(shot.get("ff_desc"))
        if ff:
            out.append({"shot": shot, "shot_id": sid, "shot_pos": pos,
                        "frame_id": "ff", "field_path": "ff_desc",
                        "text": ff, "t_sec": 0.0, "order": 0})
        kfs = shot.get("keyframes") if isinstance(shot.get("keyframes"), list) else []
        kf_entries: list[dict] = []
        for i, kf in enumerate(kfs):
            if not isinstance(kf, dict):
                continue
            desc = _field_text(kf.get("desc"))
            if not desc:
                continue
            try:
                t_sec = get_kf_t_sec(kf, shot)
            except Exception:                                    # noqa: BLE001 - 缺时间不猜
                t_sec = None
            kf_entries.append({"shot": shot, "shot_id": sid, "shot_pos": pos,
                               "frame_id": "kf%d" % (i + 1),
                               "field_path": "keyframes[%d].desc" % i,
                               "text": desc, "t_sec": t_sec, "order": i + 1})
        kf_entries.sort(key=lambda e: (e["t_sec"] is None,
                                       e["t_sec"] if e["t_sec"] is not None else 0.0,
                                       e["order"]))
        out.extend(kf_entries)
        lf = _field_text(shot.get("lf_desc"))
        if lf:
            out.append({"shot": shot, "shot_id": sid, "shot_pos": pos,
                        "frame_id": "lf", "field_path": "lf_desc",
                        "text": lf, "t_sec": duration, "order": 10 ** 6})
    return out


def _frame_index_of(frames: list[dict], frame: dict) -> int:
    for i, item in enumerate(frames):
        if item is frame:
            return i
    return -1


def _shot_texts(script: dict) -> dict[int, str]:
    """shot_pos → 该镜所有可读文本（用于过渡/取回动作的证据搜索）."""
    out: dict[int, str] = {}
    for pos, shot in enumerate(_shots(script)):
        parts = [str(shot.get(k) or "") for k in
                 ("prompt", "ff_desc", "motion_desc", "lf_desc", "context_bridge")]
        out[pos] = "\n".join(p for p in parts if p)
    return out


# ---------------------------------------------------------------------------
# 1. 重复文本
# ---------------------------------------------------------------------------

def _duplicate_findings(script: dict) -> list[dict]:
    findings: list[dict] = []

    def add(shot_id: str, field_path: str, frame_id: str, evidence: str, detail: str):
        evidence = evidence.strip()
        if len(evidence) < 2:
            return
        findings.append({
            "code": "DUPLICATE_TEXT",
            "family": "duplicate",
            "shot_id": shot_id,
            "frame_id": frame_id,
            "field_path": field_path,
            "evidence": evidence,
            "detail": detail,
        })

    for pos, shot in enumerate(_shots(script)):
        sid = _shot_id(shot, pos)
        fields: list[tuple[str, str, str]] = [
            ("prompt", _field_text(shot.get("prompt")), ""),
            ("ff_desc", _field_text(shot.get("ff_desc")), "ff"),
            ("lf_desc", _field_text(shot.get("lf_desc")), "lf"),
            ("motion_desc", _field_text(shot.get("motion_desc")), ""),
            ("narration_segment", _field_text(shot.get("narration_segment")), ""),
        ]
        for i, kf in enumerate(shot.get("keyframes") or []):
            if isinstance(kf, dict):
                fields.append(("keyframes[%d].desc" % i,
                               _field_text(kf.get("desc")), "kf%d" % (i + 1)))
        for field_path, text, frame_id in fields:
            if not text:
                continue
            # ① 相邻括号说明（「（A）（A）」）；中英文括号都算，中间只允许空白。
            parens = list(_PAREN_RE.finditer(text))
            for first, second in zip(parens, parens[1:]):
                between = text[first.end():second.start()]
                if between.strip():
                    continue
                a = first.group(1).strip()
                b = second.group(1).strip()
                if a and a == b:
                    add(sid, field_path, frame_id, first.group(0),
                        "同一括号说明相邻出现两次")
            # ② 相邻重复分句（「甲转身。甲转身。」）；只收长度 >= 6 的完整分句。
            units = re.findall(r"[^，。；！？\n]+[，。；！？]?", text)
            previous = ""
            for unit in units:
                norm = unit.strip().rstrip("，。；！？").strip()
                if norm and norm == previous and len(norm) >= _DUP_MIN_CLAUSE:
                    add(sid, field_path, frame_id, norm, "同一分句相邻出现两次")
                previous = norm
            # ③ 整段就是同一单元的两次连写（无标点分隔时也能抓）。
            stripped = text.strip()
            half = len(stripped) // 2
            if (half >= _DUP_MIN_CLAUSE and len(stripped) % 2 == 0
                    and stripped[:half] == stripped[half:]):
                add(sid, field_path, frame_id, stripped[:half], "整段文本是同一单元的两次连写")
    return findings


# ---------------------------------------------------------------------------
# 2. 景别矛盾
# ---------------------------------------------------------------------------

def _framing_hits(text: str) -> list[tuple[str, str, int]]:
    """文本里的景别词 → [(原词, 梯子标准名, 梯子下标)]；长词优先."""
    hits: list[tuple[str, str, int]] = []
    for match in _FRAMING_RE.finditer(text or ""):
        term = match.group(0)
        canonical = _FRAMING_ALIASES.get(term, term)
        if canonical in FRAMING_LADDER:
            hits.append((term, canonical, FRAMING_LADDER.index(canonical)))
    return hits


def _single_framing(text: str):
    """字段只声明**一个**景别时返回 `(原词, 标准名, 下标)`；否则 None."""
    hits = _framing_hits(text)
    canonical = {h[1] for h in hits}
    if len(canonical) != 1:
        return None
    return hits[0]


def _has_trajectory(text: str) -> bool:
    return any(marker in (text or "") for marker in _TRAJECTORY_MARKERS)


def analyze_framing(shot: dict) -> dict:
    """单镜景别事实：各字段声明、是否声明运镜、静态冲突字段（只读）."""
    prompt = _field_text(shot.get("prompt"))
    fields: list[tuple[str, str, str]] = [("prompt", "", prompt)]
    ff = _field_text(shot.get("ff_desc"))
    if ff:
        fields.append(("ff_desc", "ff", ff))
    for i, kf in enumerate(shot.get("keyframes") or []):
        if isinstance(kf, dict):
            desc = _field_text(kf.get("desc"))
            if desc:
                fields.append(("keyframes[%d].desc" % i, "kf%d" % (i + 1), desc))
    lf = _field_text(shot.get("lf_desc"))
    if lf:
        fields.append(("lf_desc", "lf", lf))
    motion = _field_text(shot.get("motion_desc"))

    levels: dict[str, tuple] = {}
    multi_level_fields: list[str] = []
    for path, _frame_id, text in fields:
        info = _single_framing(text)
        if info is None:
            if len({h[1] for h in _framing_hits(text)}) > 1:
                multi_level_fields.append(path)
            continue
        levels[path] = info
    trajectory = bool(multi_level_fields) or _has_trajectory(motion)
    return {
        "shot_id": str(shot.get("index") or ""),
        "levels": levels,
        "multi_level_fields": multi_level_fields,
        "trajectory": trajectory,
        "motion": motion,
        "fields": {path: text for path, _fid, text in fields},
    }


def _frame_id_of_path(field_path: str) -> str:
    if field_path == "ff_desc":
        return "ff"
    if field_path == "lf_desc":
        return "lf"
    match = re.match(r"^keyframes\[(\d+)\]", field_path or "")
    return ("kf%d" % (int(match.group(1)) + 1)) if match else ""


def _framing_findings(script: dict) -> list[dict]:
    findings: list[dict] = []
    for pos, shot in enumerate(_shots(script)):
        sid = _shot_id(shot, pos)
        info = analyze_framing(shot)
        levels = info["levels"]
        prompt_info = levels.get("prompt")
        frame_levels = {p: v for p, v in levels.items() if p != "prompt"}
        motion = info["motion"]

        # A. prompt 的景别与**所有**帧景别都相距 >= 3 级：prompt 是静态孤证。
        #    运镜本身不豁免——只有 motion 里明确点名了 prompt 的词（说明它是轨迹
        #    端点）才不报，避免把「镜头推近」当成任何景别都成立。
        if prompt_info and frame_levels:
            distances = [abs(prompt_info[2] - v[2]) for v in frame_levels.values()]
            if distances and min(distances) >= 3:
                prompt_explained = (prompt_info[0] in motion and _has_trajectory(motion))
                if not prompt_explained:
                    related = [
                        {"field_path": path, "frame_id": _frame_id_of_path(path),
                         "evidence": val[0], "level": val[1]}
                        for path, val in frame_levels.items()
                    ]
                    findings.append({
                        "code": "FRAMING_CONFLICT",
                        "family": "framing",
                        "shot_id": sid,
                        "frame_id": "",
                        "field_path": "prompt",
                        "evidence": prompt_info[0],
                        "detail": ("prompt 声明「%s」，同镜帧描述均为「%s」，"
                                   "且运镜未点名该景别" % (
                                       prompt_info[0],
                                       "/".join(sorted({v[1] for v in frame_levels.values()})))),
                        "related": related,
                    })

        # B. ff 与 lf 都是**单一静态**景别且相距 >= 3 级，motion/字段又没有轨迹声明。
        ff_info = levels.get("ff_desc")
        lf_info = levels.get("lf_desc")
        if ff_info and lf_info and abs(ff_info[2] - lf_info[2]) >= 3:
            if not info["trajectory"]:
                target = "lf_desc"
                if prompt_info:
                    # 指向与 prompt 不一致的那一侧；prompt 也解释不了时指向 lf。
                    if abs(prompt_info[2] - ff_info[2]) < abs(prompt_info[2] - lf_info[2]):
                        target = "lf_desc"
                    elif abs(prompt_info[2] - lf_info[2]) < abs(prompt_info[2] - ff_info[2]):
                        target = "ff_desc"
                target_info = lf_info if target == "lf_desc" else ff_info
                other = ff_info if target == "lf_desc" else lf_info
                findings.append({
                    "code": "FRAMING_CONFLICT",
                    "family": "framing",
                    "shot_id": sid,
                    "frame_id": ("lf" if target == "lf_desc" else "ff"),
                    "field_path": target,
                    "evidence": target_info[0],
                    "detail": ("首帧「%s」→ 尾帧「%s」相距 %d 级，"
                               "但 motion/字段没有运镜轨迹声明"
                               % (ff_info[0], lf_info[0],
                                  abs(ff_info[2] - lf_info[2]))),
                    "related": [
                        {"field_path": ("ff_desc" if target == "lf_desc" else "lf_desc"),
                         "frame_id": ("ff" if target == "lf_desc" else "lf"),
                         "evidence": other[0], "level": other[1]},
                    ],
                })
    return findings


# ---------------------------------------------------------------------------
# 3a. 道具状态链
# ---------------------------------------------------------------------------

def _prop_state(text: str, prop: str) -> "str | None":
    """该帧文本对 `prop` 的显式状态：`release` / `hold` / None（不可判）.

    同一句既有释放又有持有：出现取回动作时不可判（先放后拿 / 先拿后放），
    否则以显式释放为准（「手中灯盏已脱手」是释放，不是持有）。
    """
    states: set[str] = set()
    for clause in _clauses(text):
        if prop not in clause:
            continue
        release = any(marker in clause for marker in _RELEASE_MARKERS)
        hold = any(marker in clause for marker in _HOLD_MARKERS)
        if not hold and _HAND_RE.search(clause):
            hand = _HAND_RE.search(clause)
            tail = clause[hand.end():]
            hold = prop in tail[:6]
        if release and hold:
            # 同一句既写释放又写持有：只有「放下又拿起 / 拿起又放下」这类复合动作
            # 不可判；其余以显式释放为准（「手中灯盏已脱手」是释放，不是持有）。
            if any(marker in clause for marker in _ACQUIRE_MARKERS):
                return None
            states.add("release")
            continue
        if release:
            states.add("release")
        elif hold:
            states.add("hold")
    return states.pop() if len(states) == 1 else None


def _acquire_in(text: str, prop: str) -> bool:
    """文本里是否有对 `prop` 的**肯定**取回动作.

    否定式（「茶盏未再被拿起」「甲没有拿起茶盏」）里的取回动词不是状态变化的依据：
    取同分句内**最早出现**的取回动词，只有它之前没有否定词时才算取回；否则跳过
    该分句，继续检查其他分句里的真实取回动作。
    """
    for clause in _clauses(text):
        if prop not in clause:
            continue
        positions = [clause.find(marker) for marker in _ACQUIRE_MARKERS
                     if marker in clause]
        if not positions:
            continue
        earliest = min(positions)
        if any(neg in clause[:earliest] for neg in _NEGATION_MARKERS):
            continue
        return True
    return False


def has_acquisition(text: str, prop: str) -> bool:
    """文本里是否有「取回某道具」的显式动作（供裁决层做反证）."""
    return _acquire_in(text, prop)


def has_explicit_time_jump(shot: dict) -> bool:
    """本镜是否声明了显式时间跳跃（供裁决层做反证）."""
    return _explicit_time_jump(shot)


def _prop_alias_map(script: dict, frames: list[dict]) -> dict[str, set[str]]:
    """结构化 props → 稳定的短名（「青瓷酒壶」→「酒壶」）.

    只从本稿 `frame_layouts` 的 props 取候选，再取在文本里真实出现的最短后缀作为
    扫描名；同一短名下的多个全名合并，避免同一件道具报两遍。
    """
    names: set[str] = set()
    for shot in _shots(script):
        layouts = shot.get("frame_layouts") if isinstance(shot.get("frame_layouts"), dict) else {}
        buckets = [layouts.get("ff") or [], layouts.get("lf") or []]
        buckets += [frame or [] for frame in (layouts.get("kfs") or [])]
        for bucket in buckets:
            for entry in bucket:
                if not isinstance(entry, dict):
                    continue
                for prop in entry.get("props") or []:
                    text = str(prop or "").strip()
                    if text:
                        names.add(text)
    frame_texts = "\n".join(f["text"] for f in frames)
    aliases: dict[str, set[str]] = {}
    for name in names:
        candidates = {name}
        if len(name) >= 3:
            candidates.add(name[-2:])
        if len(name) >= 4:
            candidates.add(name[-3:])
        present = [c for c in candidates if len(c) >= 2 and c in frame_texts]
        if not present:
            continue
        alias = min(present, key=lambda x: (len(x), x))
        aliases.setdefault(alias, set()).add(name)
    return aliases


def _scenes_continuous(script: dict, from_pos: int, to_pos: int) -> bool:
    """跨镜时间依据：显式时间跳跃或场景/地点改变 → 不判连续性."""
    shots = _shots(script)
    if from_pos == to_pos:
        return True
    for pos in range(from_pos + 1, min(to_pos, len(shots) - 1) + 1):
        shot = shots[pos]
        prev = shots[pos - 1]
        if _explicit_time_jump(shot):
            return False
        if _scene_changed(prev, shot):
            return False
    return True


def _prop_chain_findings(script: dict) -> list[dict]:
    frames = _frames(script)
    if not frames:
        return []
    aliases = _prop_alias_map(script, frames)
    if not aliases:
        return []
    shot_texts = _shot_texts(script)
    findings: list[dict] = []

    for alias in sorted(aliases):
        state: "str | None" = None
        released: "dict | None" = None
        for frame in frames:
            current = _prop_state(frame["text"], alias)
            if current == "release":
                state = "release"
                released = frame
                continue
            if current != "hold":
                continue
            if state == "release" and released is not None:
                # 取回动作（含中间镜头 motion）是反证。
                acquire = False
                for candidate in frames:
                    if (candidate["shot_pos"], candidate["order"]) < (
                            released["shot_pos"], released["order"]):
                        continue
                    if (candidate["shot_pos"], candidate["order"]) > (
                            frame["shot_pos"], frame["order"]):
                        continue
                    if _acquire_in(candidate["text"], alias):
                        acquire = True
                        break
                if not acquire:
                    for pos in range(released["shot_pos"], frame["shot_pos"] + 1):
                        if _acquire_in(shot_texts.get(pos, ""), alias):
                            acquire = True
                            break
                legal = (acquire or not _scenes_continuous(
                    script, released["shot_pos"], frame["shot_pos"]))
                if not legal:
                    findings.append({
                        "code": "PROP_STATE_CHAIN",
                        "family": "prop_state",
                        "shot_id": frame["shot_id"],
                        "frame_id": frame["frame_id"],
                        "field_path": frame["field_path"],
                        "evidence": _state_clause(frame["text"], alias, "hold"),
                        "prop": alias,
                        "detail": ("道具「%s」在 shot%s/%s 已脱手/放下，"
                                   "本帧又回到手中，中间没有取回动作或时间跳跃依据"
                                   % (alias, released["shot_id"],
                                      released["field_path"])),
                        "related": [{
                            "shot_id": released["shot_id"],
                            "frame_id": released["frame_id"],
                            "field_path": released["field_path"],
                            "evidence": _state_clause(released["text"], alias, "release"),
                            "state": "release",
                        }],
                    })
            state = "hold"
            released = None
    return findings


def _state_clause(text: str, prop: str, kind: str) -> str:
    """从文本里取含 prop 的状态子句（证据必须逐字）."""
    markers = _RELEASE_MARKERS if kind == "release" else _HOLD_MARKERS
    for clause in _clauses(text):
        if prop not in clause:
            continue
        if any(m in clause for m in markers):
            return clause
        if kind == "hold" and _HAND_RE.search(clause):
            return clause
    return next((c for c in _clauses(text) if prop in c), "")


# ---------------------------------------------------------------------------
# 3b. 成对状态链（发型等）
# ---------------------------------------------------------------------------

def _pair_of_text(text: str, pair: dict) -> "str | None":
    up = any(term in text for term in pair["up"])
    down = any(term in text for term in pair["down"])
    if up and not down:
        return "up"
    if down and not up:
        return "down"
    return None


def _state_label(pair: dict, state: str) -> str:
    return str(pair.get("%s_label" % state) or state)


def _state_events(text: str, names: list[str], pair: dict) -> list[dict]:
    """按子句抽取状态事件：只认**单一**命名主体；无名字时沿用上一个主体."""
    events: list[dict] = []
    last_subject = ""
    for clause in _clauses(text):
        found = [name for name in names if name in clause]
        if len(found) == 1:
            subject = found[0]
        elif len(found) > 1:
            subject = ""
        else:
            subject = last_subject
        if subject:
            last_subject = subject
        if not subject:
            continue
        up_terms = [t for t in pair["up"] if t in clause]
        down_terms = [t for t in pair["down"] if t in clause]
        if up_terms and down_terms:
            events.append({"subject": subject, "state": "ambiguous",
                           "clause": clause})
        elif up_terms:
            events.append({"subject": subject, "state": "up", "clause": clause,
                           "term": up_terms[0]})
        elif down_terms:
            events.append({"subject": subject, "state": "down", "clause": clause,
                           "term": down_terms[0]})
    return events


def _transition_in(text: str, pair: dict) -> bool:
    """状态变化过程是否在本句被明说（必须与状态载体同句）."""
    for clause in _clauses(text):
        if not any(term in clause for term in pair["terms"]):
            continue
        if any(marker in clause for marker in pair["transitions"]):
            return True
    return False


def _state_texts_between(frames: list[dict], script: dict,
                         first: dict, last: dict) -> list[str]:
    """两帧之间（含端点）的所有帧文本 + 所涉镜头的 motion/prompt 文本."""
    out: list[str] = []
    start = _frame_index_of(frames, first)
    end = _frame_index_of(frames, last)
    if start < 0 or end < start:
        return out
    for frame in frames[start:end + 1]:
        out.append(frame["text"])
    texts = _shot_texts(script)
    for pos in range(first["shot_pos"], last["shot_pos"] + 1):
        out.append(texts.get(pos, ""))
    return out


def _state_findings(script: dict) -> list[dict]:
    frames = _frames(script)
    names = _character_names(script)
    if not names:
        return []
    findings: list[dict] = []

    # A. 同一字段同时声明一对状态的两侧（如 prompt 既写挽髻又写披头散发）。
    for pos, shot in enumerate(_shots(script)):
        sid = _shot_id(shot, pos)
        fields: list[tuple[str, str, str]] = [
            ("prompt", "", _field_text(shot.get("prompt"))),
            ("ff_desc", "ff", _field_text(shot.get("ff_desc"))),
            ("lf_desc", "lf", _field_text(shot.get("lf_desc"))),
        ]
        for i, kf in enumerate(shot.get("keyframes") or []):
            if isinstance(kf, dict):
                fields.append(("keyframes[%d].desc" % i, "kf%d" % (i + 1),
                               _field_text(kf.get("desc"))))
        for field_path, frame_id, text in fields:
            if not text:
                continue
            for pair in _STATE_PAIRS:
                # 必须按**主体**分组：同一字段里张十三写着束髻、听香写着披头散发，
                # 是两个人的正常状态，不是同一个人的自相矛盾。
                by_subject: dict[str, dict[str, dict]] = {}
                for event in _state_events(text, names, pair):
                    if event["state"] in ("up", "down"):
                        by_subject.setdefault(event["subject"], {}).setdefault(
                            event["state"], event)
                for subject, states in by_subject.items():
                    if "up" not in states or "down" not in states:
                        continue
                    if _transition_in(text, pair):
                        continue
                    later = states["down"] if text.find(states["down"]["clause"]) > \
                        text.find(states["up"]["clause"]) else states["up"]
                    findings.append({
                        "code": "STATE_CONFLICT",
                        "family": "state",
                        "shot_id": sid,
                        "frame_id": frame_id,
                        "field_path": field_path,
                        "evidence": later["clause"],
                        "subject": subject,
                        "state_pair": pair["name"],
                        "detail": ("同一字段同时声明「%s」与「%s」，且没有过渡过程"
                                   % (states["up"].get("term", "up"),
                                      states["down"].get("term", "down"))),
                        "related": [{
                            "field_path": field_path,
                            "evidence": (states["up"] if later is states["down"]
                                         else states["down"])["clause"],
                        }],
                    })

    # B. 相邻帧之间的状态翻转（无过渡/无时间跳跃依据才算）。
    for pair in _STATE_PAIRS:
        last: dict[tuple[str, str], dict] = {}
        previous_frame: dict[tuple[str, str], dict] = {}
        for frame in frames:
            events = _state_events(frame["text"], names, pair)
            for event in events:
                if event["state"] not in ("up", "down"):
                    continue
                key = (event["subject"], pair["name"])
                prior = last.get(key)
                if prior is not None and prior["state"] != event["state"]:
                    prior_frame = previous_frame.get(key, frame)
                    between = _state_texts_between(frames, script, prior_frame, frame)
                    if not any(_transition_in(text, pair) for text in between) \
                            and _scenes_continuous(script, prior_frame["shot_pos"],
                                                   frame["shot_pos"]):
                        findings.append({
                            "code": "STATE_CHAIN_CONFLICT",
                            "family": "state",
                            "shot_id": frame["shot_id"],
                            "frame_id": frame["frame_id"],
                            "field_path": frame["field_path"],
                            "evidence": event["clause"],
                            "subject": event["subject"],
                            "state_pair": pair["name"],
                            "detail": ("%s 的%s从「%s」变为「%s」，"
                                       "中间没有过渡过程或时间跳跃依据"
                                       % (event["subject"], pair.get("label", pair["name"]),
                                          _state_label(pair, prior["state"]),
                                          _state_label(pair, event["state"]))),
                            "related": [{
                                "shot_id": prior_frame["shot_id"],
                                "frame_id": prior_frame["frame_id"],
                                "field_path": prior_frame["field_path"],
                                "evidence": prior["clause"],
                                "state": prior["state"],
                            }],
                        })
                last[key] = {"state": event["state"], "clause": event["clause"]}
                previous_frame[key] = frame

    # C. 角色静态形象 vs 首次出现的相反状态（首见前没有任何过渡依据）。
    #    这条比 B 宽：它不要求相邻帧显式提到状态，只要求**静态形象**给了初始值。
    #    时间依据只认显式文字跳跃，不把 scene 变化本身当时间流逝（否则连续动作的
    #    跨空间切镜会把真实的状态泄漏漏掉）。
    characters = (script or {}).get("characters") or []
    shot_texts = _shot_texts(script)
    for character in characters:
        if not isinstance(character, dict):
            continue
        name = str(character.get("identifier") or character.get("name") or "").strip()
        if not name:
            continue
        static_text = _field_text(character.get("static_features"))
        if not static_text:
            continue
        for pair in _STATE_PAIRS:
            initial = _pair_of_text(static_text, pair)
            if initial not in ("up", "down"):
                continue
            for pos, frame in enumerate(frames):
                events = [e for e in _state_events(frame["text"], names, pair)
                          if e["subject"] == name and e["state"] in ("up", "down")]
                if not events:
                    continue
                event = events[0]
                if event["state"] == initial:
                    continue
                # 首次相反状态：检查此前所有文本里有没有过渡过程或显式时间跳跃。
                prior_texts = [f["text"] for f in frames[:pos]]
                prior_texts += list(shot_texts.values())[:frame["shot_pos"] + 1]
                if any(_transition_in(text, pair) for text in prior_texts):
                    break
                if any(_explicit_time_jump(shot)
                       for shot in _shots(script)[:frame["shot_pos"] + 1]):
                    break
                findings.append({
                    "code": "STATE_CHAIN_CONFLICT",
                    "family": "state",
                    "shot_id": frame["shot_id"],
                    "frame_id": frame["frame_id"],
                    "field_path": frame["field_path"],
                    "evidence": event["clause"],
                    "subject": name,
                    "state_pair": pair["name"],
                    "detail": ("角色静态形象为「%s」，本帧已变为「%s」，"
                               "此前没有任何过渡过程或显式时间跳跃依据"
                               % (_state_label(pair, initial),
                                  _state_label(pair, event["state"]))),
                    "related": [{"field_path": "characters.static_features",
                                 "evidence": static_text[:80], "state": initial}],
                    "basis": "static_anchor",
                })
                break

    # D. 首帧已是本镜动作之后的结果态（ff 与 lf 都是目标态，motion 里才有过渡）。
    for pos, shot in enumerate(_shots(script)):
        sid = _shot_id(shot, pos)
        ff = _field_text(shot.get("ff_desc"))
        lf = _field_text(shot.get("lf_desc"))
        motion = _field_text(shot.get("motion_desc"))
        if not (ff and lf and motion):
            continue
        for pair in _STATE_PAIRS:
            for name in names:
                ff_events = [e for e in _state_events(ff, names, pair)
                             if e["subject"] == name]
                lf_events = [e for e in _state_events(lf, names, pair)
                             if e["subject"] == name]
                if not ff_events or not lf_events:
                    continue
                target = "down"
                if ff_events[0]["state"] != target or lf_events[0]["state"] != target:
                    continue
                if not _transition_in(motion, pair):
                    continue
                findings.append({
                    "code": "STATE_RESULT_LEAK",
                    "family": "state",
                    "shot_id": sid,
                    "frame_id": "ff",
                    "field_path": "ff_desc",
                    "evidence": ff_events[0]["clause"],
                    "subject": name,
                    "state_pair": pair["name"],
                    "detail": ("首帧已经呈现「%s」结果态，而过渡过程写在 motion 里"
                               % _state_label(pair, target)),
                    "related": [{"field_path": "motion_desc",
                                 "evidence": next(
                                     (c for c in _clauses(motion)
                                      if any(t in c for t in pair["terms"])), "")}],
                })
    return findings


# ---------------------------------------------------------------------------
# 4. 尾帧过程态（与 STATE_RESULT_LEAK 互补）
# ---------------------------------------------------------------------------

def _lf_process_findings(script: dict) -> list[dict]:
    """尾帧仍停在显式未收束的过程态.

    判据（内容无关、可判定）：

    1. 复用 ``shared.style_prefix.detect_lf_midstate`` 的保守闸门——它要求
       「过程标记 + 匹配动词」成对（或武器+水面残留、半身态）才命中；静态的
       「门开着」不触发，避免把合法的持续状态当成过程态；
    2. 本镜没有显式时间跳跃（``_explicit_time_jump``）；
    3. motion / context_bridge / clip_transition 没有明说「动作延续到镜外」
       （``_LF_OPEN_END_MARKERS``）——故意切在动作中的镜头有显式依据时不报；
    4. 命中的尾帧分句本身没有收束词（``_LF_CLOSURE_MARKERS``）。
    """
    try:
        from shared.style_prefix import detect_lf_midstate
    except Exception:                                      # noqa: BLE001 - 缺依赖不猜
        return []
    findings: list[dict] = []
    for pos, shot in enumerate(_shots(script)):
        lf = _field_text(shot.get("lf_desc"))
        if not lf:
            continue
        try:
            problems = detect_lf_midstate(lf)
        except Exception:                                  # noqa: BLE001 - 诊断不改变结论
            continue
        if not problems:
            continue
        if _explicit_time_jump(shot):
            continue
        motion = _field_text(shot.get("motion_desc"))
        bridge = str(shot.get("context_bridge") or "")
        transition = str(shot.get("clip_transition") or "")
        if any(marker in (motion + "\n" + bridge + "\n" + transition)
               for marker in _LF_OPEN_END_MARKERS):
            continue
        open_problems = [
            p for p in problems
            if not any(marker in str(p.get("clause") or "")
                       for marker in _LF_CLOSURE_MARKERS)
        ]
        if not open_problems:
            continue
        first = open_problems[0]
        kinds = sorted({str(p.get("type") or "") for p in open_problems
                        if p.get("type")})
        kind_text = "/".join(kinds)
        detail = ("尾帧仍停留在过程态（%s）；未发现显式时间跳跃或"
                  "「动作延续到镜外」的依据，尾帧应落到动作终态" % kind_text
                  if kind_text else
                  "尾帧仍停留在过程态；尾帧应落到动作终态")
        findings.append({
            "code": "LF_PROCESS_STATE",
            "family": "state",
            "shot_id": _shot_id(shot, pos),
            "frame_id": "lf",
            "field_path": "lf_desc",
            "evidence": str(first.get("clause") or lf).strip(),
            "detail": detail,
            "related": [{"field_path": "motion_desc", "evidence": motion[:80]}]
            if motion else [],
        })
    return findings


# ---------------------------------------------------------------------------
# 5. 新增台词（必须有对照源）
# ---------------------------------------------------------------------------

def _quoted_spans(text: str) -> list[str]:
    """播出文本里的显式引号台词（按出现顺序，去空）."""
    spans: list[str] = []
    i, n = 0, len(text or "")
    while i < n:
        ch = text[i]
        for opener, closer in _QUOTE_PAIRS:
            if ch != opener:
                continue
            end = text.find(closer, i + 1)
            if end > i + 1:
                spans.append(text[i + 1:end].strip())
                i = end + 1
                break
        else:
            i += 1
    return [s for s in spans if s]


def _dialogue_lines(seg: str, characters: list[dict]) -> list[str]:
    """播出文本里的**显式台词**：引号内容优先，其次「说话人+言语动词+冒号」后的正文.

    只收能确定性识别的台词；没有引号、也没有可核说话人前缀的纯叙述/画外音
    不在此判定（避免把旁白改写、合规替换误判成「新增台词」）。
    """
    if not seg:
        return []
    quoted = _quoted_spans(seg)
    if quoted:
        return quoted
    try:
        from shared.dialogue import speaker_prefix
    except Exception:                                      # noqa: BLE001 - 缺依赖不猜
        return []
    speaker = speaker_prefix(seg, characters)
    if not speaker:
        return []
    colon = re.search(r"[:：]", seg)
    if not colon:
        return []
    body = seg[colon.end():].strip()
    return [body] if body else []


def _added_dialogue_findings(script: dict, source_text) -> list[dict]:
    """脚本显式台词在对照源里找不到逐字依据 → ``ADDED_DIALOGUE``.

    **没有对照源就不产出**：纯脚本内无法区分「新增」与「合法改编」。
    """
    if not isinstance(source_text, str) or not source_text.strip():
        return []
    try:
        from step01_script_gen.plan_feasibility import source_contains
    except Exception:                                      # noqa: BLE001 - 缺依赖不猜
        return []
    characters = [c for c in ((script or {}).get("characters") or [])
                  if isinstance(c, dict)]
    findings: list[dict] = []
    for pos, shot in enumerate(_shots(script)):
        sid = _shot_id(shot, pos)
        narration = _field_text(shot.get("narration_segment"))
        subtitle = _field_text(shot.get("subtitle"))
        seg = narration or subtitle
        field_path = "narration_segment" if narration else "subtitle"
        seen: set[str] = set()
        for line in _dialogue_lines(seg, characters):
            line = line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            try:
                grounded = source_contains(source_text, line)
            except Exception:                              # noqa: BLE001 - 诊断不改变结论
                continue
            if grounded:
                continue
            findings.append({
                "code": "ADDED_DIALOGUE",
                "family": "dialogue",
                "shot_id": sid,
                "frame_id": "",
                "field_path": field_path,
                "evidence": line,
                "detail": ("台词在对照源中找不到逐字依据，可能是新增台词/画外音"
                           "（已提供对照源）"),
                "source": "source_text",
            })
    return findings


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def scan_script(script: dict, *, source_text: "str | None" = None) -> list[dict]:
    """全稿确定性检查 → 按镜头/字段/代码排序的 findings（只读，不改脚本）.

    ``source_text`` 是 ``ADDED_DIALOGUE`` 的**对照源**（原文/规划文本）。不传时该码
    不产出——纯脚本内无法区分「新增台词」与「合法改编」，宁缺勿滥。
    """
    if not isinstance(script, dict):
        return []
    findings: list[dict] = []
    findings.extend(_duplicate_findings(script))
    findings.extend(_framing_findings(script))
    findings.extend(_prop_chain_findings(script))
    findings.extend(_state_findings(script))
    findings.extend(_lf_process_findings(script))
    findings.extend(_added_dialogue_findings(script, source_text))

    # 去重：同一 (code, shot, field, evidence) 只留一条。
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in findings:
        key = (item.get("code"), item.get("shot_id"), item.get("field_path"),
               item.get("evidence"), item.get("prop"), item.get("subject"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    shot_order = {}
    for pos, shot in enumerate(_shots(script)):
        shot_order[_shot_id(shot, pos)] = pos

    def sort_key(item: dict):
        field = str(item.get("field_path") or "")
        base = field.split("[")[0].split(".")[0]
        return (shot_order.get(str(item.get("shot_id")), 10 ** 6),
                _FIELD_ORDER.get(base, 9), field,
                _FINDING_ORDER.get(str(item.get("code")), 99),
                str(item.get("evidence") or ""))
    return sorted(unique, key=sort_key)


__all__ = [
    "analyze_framing", "has_acquisition", "has_explicit_time_jump", "scan_script",
]
