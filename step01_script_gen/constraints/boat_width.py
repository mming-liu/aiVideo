"""T1. 船体宽度硬修复约束（v6.42 迁移自 _hard_fix_boat_width）.

v6.40+（08-23）船体宽度硬修复：检测「窄身」「长X米…舴艋舟/小舟」但无宽度
→ 补「宽X米」。

08-23 实测：shot 1 prompt 写「一叶小舟（长6米窄身无篷舴艋舟）」，「窄身」被模型
忠实渲染成"木板"——杨旭和听香坐着就把船占满，无视觉余量。LLM 自改时倾向
把"舴艋舟"删掉换"画舫"，但舴艋舟是原文场景标识不该改。确定性兜底：保留船型，
把**属于船体的**"窄身"替换成有来源的宽度。

宽度来源（v6.60.1 硬编码清理）：本镜 scene_objects 命中的船规格（v6.52 协议，
shared/scene_objects_bank.boat_width_text）动态取值；没有来源就不改写。

改写规则（按字段 prompt/ff_desc/lf_desc/motion_desc/keyframes[*].desc）：
  ① 「长X米窄身」 → 「长X米宽W米」（仅当「窄身」结构上属于船）
  ② 「窄身无篷舴艋舟」/「小舟（长6米窄身无篷）」 → 「宽W米无篷…」（同上）
  ③ 「长X米（…）舴艋舟」无"宽"字 → 同样插入"宽W米"在"无篷"前

2026-11-13（S02 复核）：旧实现只证明「字段里出现过船类词」，随后对**全文**删除
「窄身」——「窄身机甲站在小舟旁」被删成「机甲站在小舟旁」，把别的实体的属性
改掉了。现在属性改写必须**绑定到明确的船实体**（同一名词短语 / 船名词后的括号 /
船名词+长度），无法确定归属时保留描述并记 `action="warn"` 待评阅项。

幂等：已含"宽X米"或"宽身"则跳过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity
from shared.scene_objects_bank import boat_width_text


# ③ 长X米无篷 → 长X米宽W米无篷
_RE_LONG_NO_CANOPY = re.compile(r"(长\d+(?:\.\d+)?米)无篷")
#: 「窄身」可带一个「长X米」前缀（规则 ①）。
_NARROW_RE = re.compile(r"(?P<long>长\d+(?:\.\d+)?米)?窄身")

#: 判定「这段文本说的是船」的名词（只在命中时才可以改写「窄身」等船体形制词）。
_BOAT_NOUNS = ("船", "舟", "艇", "舢板", "舴艋", "筏")
#: 船名词的正则（不含「船长/船夫/船工」等人物称谓）。
_BOAT_NOUN = (r"(?:舴艋舟|小舟|木舟|乌篷船|篷船|渡船|渔船|货船|客船|"
              r"舟|船(?!长|夫|工|家|员|主)|艇|舢板|筏|舫)")
#: 「窄身」与船名词之间允许出现的形制/材质修饰（同一名词短语内部）。
_BOAT_FORM_TAIL = r"(?:的)?(?:无篷|有篷|乌篷|敞口|木|小|轻|扁|旧|破|空|窄)*"
_NARROW_ATTACHED_RE = re.compile(r"窄身" + _BOAT_FORM_TAIL + _BOAT_NOUN)
#: 某个位置之前的船名词（「小舟长6米窄身」「小舟（长6米窄身无篷）」）：
#: 允许中间夹 0-6 个非断句字符。
_BOAT_BEFORE_RE = re.compile(_BOAT_NOUN + r"[^，。、\n]{0,6}$")


def _mentions_boat(text: str) -> bool:
    return any(noun in text for noun in _BOAT_NOUNS)


def _paren_span(text: str, pos: int) -> tuple[int, int] | None:
    """`pos` 所在括号短语的 `(open, close)`；不在成对括号内返回 None."""
    depth = 0
    open_at = -1
    for i in range(pos - 1, -1, -1):
        ch = text[i]
        if ch in "）)":
            depth += 1
        elif ch in "（(":
            if depth == 0:
                open_at = i
                break
            depth -= 1
    if open_at < 0:
        return None
    depth = 0
    for i in range(pos, len(text)):
        ch = text[i]
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            if depth == 0:
                return (open_at, i)
            depth -= 1
    return None


def _narrow_in_boat_parenthetical(text: str, pos: int) -> bool:
    """「窄身」位于括号内，且该括号短语紧跟船名词（「小舟（长6米窄身无篷）」）."""
    span = _paren_span(text, pos)
    if not span:
        return False
    head = text[max(0, span[0] - 8):span[0]]
    return bool(_BOAT_BEFORE_RE.search(head))


def _narrow_bound_to_boat(text: str, match: "re.Match[str]") -> bool:
    """「窄身」是否**结构上属于船**（可证明归属才允许改写）.

    三种可证归属：
      ① 船名词紧挨在「长X米窄身」之前（「小舟长6米窄身」）；
      ② 船名词在同一名词短语内紧随「窄身」（「窄身无篷舴艋舟」）；
      ③ 「窄身」位于紧跟船名词的括号短语内（「小舟（长6米窄身无篷）」）。
    其余（「窄身机甲」「窄身少女」）一律不归属——保留原文。
    """
    long_text = match.group("long") or ""
    narrow_at = match.start() + len(long_text)
    if long_text:
        head = text[max(0, match.start() - 8):match.start()]
        if _BOAT_BEFORE_RE.search(head):
            return True
    if _NARROW_ATTACHED_RE.match(text, narrow_at):
        return True
    return _narrow_in_boat_parenthetical(text, narrow_at)


def _rewrite_narrow(text: str, width_txt: str) -> tuple[str, list[str]]:
    """把**可证明属于船**的「窄身」替换成有来源的宽度；其余保留.

    Returns:
        `(new_text, preserved)`；`preserved` 是归属无法证明、因而原样保留的
        「窄身」片段（调用方按需记 warn，交 audit 评阅）。
    """
    preserved: list[str] = []

    def _sub(match: "re.Match[str]") -> str:
        if not _narrow_bound_to_boat(text, match):
            preserved.append(match.group(0))
            return match.group(0)
        return f"{match.group('long') or ''}{width_txt}"

    return _NARROW_RE.sub(_sub, text), preserved


class BoatWidthConstraint(Constraint):
    """T1. 船体宽度：检测「窄身」/无宽度 → 补「宽W米」（W 取 scene_objects 船规格）.

    severity=WARN: 船宽补全是确定性改写.

    2026-11-13（S02）三条约束：
    ① **没有输入规格就不改写**（`boat_width_text` 返回空串 → 跳过）；
    ② 「窄身」只在**能证明属于本镜船体**时处理——「窄身机甲」不是船，不得删除；
    ③ 归属不明的「窄身」保留原文并记 warn（待评阅），不静默删除。
    """

    name = "boat_width"
    severity = Severity.WARN
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        scene_objects = getattr(ctx, "script", None)
        scene_objects = scene_objects.get("scene_objects") or {} if isinstance(scene_objects, dict) else {}
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            width_txt = boat_width_text(shot, scene_objects)
            # 没有来源的尺寸不写（S02）。
            if not width_txt:
                continue
            # 收集要改写的字段（含 kf.desc——v6.41+ kf 描述漏修）
            fld_text: list[tuple[str, str]] = []
            for fld in self.fields:
                v = shot.get(fld)
                if isinstance(v, str) and v:
                    fld_text.append((fld, v))
            kfs = shot.get("keyframes") or []
            for j, kf in enumerate(kfs):
                if isinstance(kf, dict):
                    d = kf.get("desc")
                    if isinstance(d, str) and d:
                        fld_text.append((f"keyframes[{j}].desc", d))

            for fld, v in fld_text:
                # 幂等：已含宽度或宽身则跳过
                if re.search(r"宽\d", v) or "宽身" in v:
                    continue
                new_v, preserved = _rewrite_narrow(v, width_txt)
                # ③ 含"长X米...无篷"但无"宽"字 → 在"无篷"前插"宽W米"
                if "无篷" in new_v and not re.search(r"宽\d", new_v):
                    new_v = _RE_LONG_NO_CANOPY.sub(
                        lambda m: m.group(1) + width_txt + "无篷", new_v)
                if preserved and _mentions_boat(v):
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="warn",
                            target=fld,
                            before=v,
                            after=new_v,
                            detail=("「窄身」无法证明属于本镜船体（可能是其他实体），"
                                    "保留原文交 audit 评阅，不删除、不借用船宽。"),
                        )
                    )
                if new_v != v:
                    # 写回原字段（kf 的 desc 需要特殊处理）
                    if fld.startswith("keyframes["):
                        m = re.match(r"keyframes\[(\d+)\]\.desc", fld)
                        if m:
                            kfs[int(m.group(1))]["desc"] = new_v
                    else:
                        shot[fld] = new_v
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target=fld,
                            before=v,
                            after=new_v,
                        )
                    )
        return repairs
