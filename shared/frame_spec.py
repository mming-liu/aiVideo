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


def is_on_screen(entry: dict, default: bool = True) -> bool:
    """spec 条目是否在场（on_screen 缺省判定，单一事实源）.

    缺省 True（保守估计在场）——与本站「缺失字段 on_screen=True」的降级原则
    一致（frame_spec_repair 也把缺失补 True）。**所有读 on_screen 的代码必须
    走本函数**：历史上 vimax_bridge._frame_spec_roster 用 `get("on_screen", True)`
    而 QC/spec_on_screen_names 用 falsy 判定，同一缺省字段一侧「在场」一侧
    「不在场」→ 参考图拼了肖像、QC 期望人数却不算，触发删帧死循环（09-13 审查）。
    """
    if not isinstance(entry, dict):
        return False
    return bool(entry.get("on_screen", default))


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


# ---------------------------------------------------------------------------
# gaze 状态豁免（09-13 hook ff 视线矛盾根因）
#
# schema 强制 spec 每角色必填 gaze_target，LLM 对「双目紧闭/昏迷」角色也只能
# 编一个视线目标 → QC 拿编出来的锚点问 VLM 必判 no → 删帧重抽 3 次耗尽
# （hook ff 杨旭双目紧闭 vs 「视线看向画右刺客」实测）。「无视线」是合法状态
# （gaze_target='无'本就豁免出题），此处提供状态判定，供 QC 出题跳过与
# step01 校验共用（单一事实源）。
# ---------------------------------------------------------------------------

GAZE_BLOCKED_STATES = (
    "双目紧闭", "双眼紧闭", "紧闭双眼", "紧闭", "闭眼", "闭目",
    "昏迷", "不省人事", "失去意识", "昏死", "晕厥",
)


def _find_all(text: str, sub: str) -> list[int]:
    out, p = [], text.find(sub)
    while p >= 0:
        out.append(p)
        p = text.find(sub, p + 1)
    return out


def gaze_blocked_names(frame_text: str, names: list) -> set:
    """帧文本中处于无视线状态的角色集合（最近名归属法）.

    每个状态词归属**离它最近的角色名**（前后 ≤16 字）——对称窗口会把旁边
    的健康角色一起误豁免（实测：「杨旭…双目紧闭…听香跪坐中舱」±12 窗口
    把听香也盖住；最近名归属下只有杨旭被豁免）。
    两个归属细节（实测校准）：
      1. 状态词区间先合并重叠——「双目紧闭」含子串「紧闭」，短词锚点右移
         会把状态错归给后一个角色；
      2. 等距时优先取状态词**之前**的名字（中文「主体…状态」语序，状态的
         主语在前）。
    """
    blocked: set = set()
    text = frame_text or ""
    cand = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not text or not cand:
        return blocked
    spans: list[tuple[int, int]] = []
    for w in GAZE_BLOCKED_STATES:
        for p in _find_all(text, w):
            spans.append((p, p + len(w)))
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in merged:
        best, best_d, best_before = None, 17, False
        for n in cand:
            for q in _find_all(text, n):
                if q + len(n) <= a:            # 名在状态词前
                    d, before = a - (q + len(n)), True
                elif q >= b:                   # 名在状态词后
                    d, before = q - b, False
                else:                           # 名与状态词重叠（罕见）
                    d, before = 0, True
                if d < best_d or (d == best_d and before and not best_before):
                    best, best_d, best_before = n, d, before
        if best is not None:
            blocked.add(best)
    return blocked


def gaze_blocked_for(name: str, spec_entry: dict | None = None,
                     frame_text: str = "", names: list | None = None) -> bool:
    """角色在本帧是否处于「无视线」状态（双目紧闭/昏迷等）.

    判定两路（任一命中即豁免）：
      1. spec 条目自身的 pose/position 含状态词；
      2. 帧描述文本：names 提供时走最近名归属（gaze_blocked_names，精确）；
         未提供时退回 ±12 字窗口共现（粗粒度，兼容旧调用）。
    """
    if not name:
        return False
    if spec_entry:
        own = " ".join(str(spec_entry.get(k) or "") for k in ("pose", "position"))
        if any(w in own for w in GAZE_BLOCKED_STATES):
            return True
    text = frame_text or ""
    if not text:
        return False
    if names is not None:
        return name in gaze_blocked_names(text, names)
    for w in GAZE_BLOCKED_STATES:
        p = text.find(w)
        while p >= 0:
            if name in text[max(0, p - 12):p + len(w) + 12]:
                return True
            p = text.find(w, p + 1)
    return False


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
    on_screen_items = [s for s in ff if is_on_screen(s)]
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
        if is_on_screen(s) and s.get("name")
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
        if not is_on_screen(s):
            continue
        name = s.get("name", "")
        if any(kw in name for kw in threat_keywords):
            return True
    return False


def spec_roster_or_none(
    shot: dict,
    frame_kind: str,
    kf_index: Optional[int] = None,
) -> Optional[set[str]]:
    """本帧出镜名单（on_screen=True 的 name 集合），保留「缺失 vs 明确无人」区分.

    与 spec_on_screen_names 的区别：本函数额外区分
      - None  = spec 缺失/无条目（frame_layouts 无该帧 / kfs 越界 / 该帧为空表）
                → 调用方应回退文本匹配（旧行为）
      - set() = spec 存在且**明确本帧无人出镜**（全 on_screen=False）
                → 调用方必须照空名单处理，禁止回退文本

    专供 vimax_bridge 参考图/拼图「用到才拼」的 None 语义（get_frame_spec 的
    [] 无法区分这两种情况）。on_screen 缺省走 is_on_screen（True）。
    """
    fl = shot.get("frame_layouts")
    if not isinstance(fl, dict):
        return None
    if frame_kind == "keyframe":
        kfs = fl.get("kfs") or []
        if kf_index is None or kf_index < 0 or kf_index >= len(kfs):
            return None
        entries = kfs[kf_index]
    else:
        entries = fl.get(frame_kind)
    if not entries:
        return None
    roster: set[str] = set()
    for e in entries:
        if not is_on_screen(e):
            continue
        name = str(e.get("name", "")).strip()
        if name:
            roster.add(name)
    return roster


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
