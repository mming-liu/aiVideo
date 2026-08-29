"""W16. kf 弱模板占位约束（v6.45，08-26）.

LLM 常把 kf desc 写成占位模板：「静态定格。X 姿态细节已在前一刻画面定格
（不重复本镜 ff_desc 描述）…」「lf 静态定格后的环境留痕…」。kf 独立生图，
图片模型看不到 ff/lf——占位模板让关键武器/姿态/伤处全丢（08-26 hook kf0：
匕首没了、杨旭起身看镜头根因）。

修法：剥离"不重复本镜 ff_desc"占位指令 + "前一刻画面定格"措辞后，pre/approach
承袭 ff_desc 静态态、react/after 承袭 lf_desc，追加「（本帧画面即首帧/尾帧
静态状态：…）」锚，使 kf 自描述画面内容。

幂等：desc 已含「本帧画面」注入锚跳过；无占位标记或 ff/lf 均缺省时跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity

# 弱模板占位标记：命中任一 → 需要重写
# 08-27 实测新变体：LLM 直写「姿态细节已在画面定格，听香姿态细节已在画面定格。
# 前景…，中景角色姿态定格，背景…。镜头固定，等待动作启动。」——同族第二个变体
# （08-26 是「姿态细节已在前一刻画面定格（不重复本镜 ff_desc 描述）」）。
_WEAK_MARKERS = (
    "不重复本镜", "前一刻画面定格", "lf 静态定格后", "环境留痕",
    "姿态细节已在画面定格", "姿态细节已在前一刻画面定格",
    "中景角色姿态定格", "等待动作启动",
)
# 继承源：post 阶段承袭 lf_desc，其余承袭 ff_desc
_POST_STAGES = ("react", "after", "exit")
# 幂等锚：desc 已含「本帧画面」注入锚视为已修
_REPAIR_MARKER = "本帧画面"


def _strip_template_preamble(desc: str) -> str:
    """剥离弱模板占位前奏，保留 LLM 真实写的首句（若有）.

    占位模板（08-26/08-27 两版实测）统一以「构图前缀 / 静态定格 / 镜头指令 /
    环境留痕」开头——前奏整段是给 LLM 看的占位指令，图片模型看不到 ff/lf，
    占位即丢姿态/武器/伤处。W16 修复锚（本帧画面=首帧/尾帧静态状态）才是真实
    画面内容。若 LLM 在占位前写了真实动作句（如「刺客破水而出，姿态…」），
    保留该句；标记嵌在句中（无分句边界）说明前奏不完整，整段剥离更安全。
    """
    import re as _re
    s = desc.strip()
    # 1) 以构图/静态定格/镜头固定/环境留痕 开头 = 纯模板前奏 → 整段剥离
    if _re.match(r"^[^。]*?(构图[：:]|静态定格|镜头固定|环境留痕)", s):
        return ""
    # 2) 有真实内容：取首个占位标记前的整句（标记须在分句边界后，防截断人名）
    for ph in _WEAK_MARKERS:
        idx = s.find(ph)
        if idx > 0:
            if s[idx - 1] in "，,。；;！？":
                head = s[:idx].rstrip("，,。；; ")
                return head + ("。" if head else "")
            return ""
    return ""


def _kf_index(kfs: list, kf: dict) -> int:
    """返回 kf 在 kfs 列表中的索引（用于 RepairRecord.target 路径）."""
    try:
        return kfs.index(kf)
    except ValueError:
        return -1


class KfWeakTemplateConstraint(Constraint):
    """W16. kf 弱模板占位：剥离占位指令 + 承袭 ff/lf 静态状态锚.

    severity=WARN: 已有确定性重写，无需 regen.
    幂等通过「desc 已含『本帧画面』」保证.
    """

    name = "kf_weak_template"
    severity = Severity.WARN
    fields = ()  # 不扫文本字段，处理的是 keyframes 列表

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list) or not kfs:
                continue
            ff = (shot.get("ff_desc") or "").strip()
            lf = (shot.get("lf_desc") or "").strip()
            if not ff and not lf:
                continue
            for kf in kfs:
                if not isinstance(kf, dict):
                    continue
                desc = kf.get("desc") or ""
                if not desc or _REPAIR_MARKER in desc:
                    continue
                if not any(m in desc for m in _WEAK_MARKERS):
                    continue
                stage = (kf.get("stage") or "")
                is_post = stage in _POST_STAGES
                src = lf if is_post else ff
                if not src:
                    continue
                # 剥离完整弱模板前奏（两版变体：姿态占位/镜头指令/构图前缀），
                # 只保留 LLM 真实写的首句 → 追加本镜静态状态锚（kf 独立生图自描述）
                head = _strip_template_preamble(desc)
                tag = "尾帧" if is_post else "首帧"
                after = f"{head}（{_REPAIR_MARKER}即{tag}静态状态：{src}）"
                kf["desc"] = after
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="rewrite",
                        target=f"keyframes[{_kf_index(kfs, kf)}].desc",
                        before=desc,
                        after=after,
                    )
                )
        return repairs
