"""LLM 自检回路约束（v6.42 PoC）.

在 parse_response 后调一次小 LLM，给定"通用检查项"清单，
让 LLM 自审每个 shot 并报告可能的问题。问题作为 Violation 上报
（不自动修复，给 LLM 下一轮 repair feedback 看）。

特点：
  - opt-in: SELF_REVIEW_ENABLED=1 才启用（默认 0，避免生产环境成本）
  - 每次 apply_constraints 只调 1 次 LLM（聚合所有 shot）
  - 用最便宜的 provider（TEXT_PROVIDER）
  - 错误静默：LLM 调用失败不阻断流程
  - 输出解析：每行 "shot N: <issue>" → Violation(shot_index=N)

启用方式：
  SELF_REVIEW_ENABLED=1 python step01_script_gen/main.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation


# ============================================================================
# Prompt 模板
# ============================================================================

_SYSTEM_PROMPT = """你是短视频分镜脚本的"质检员"。请对给定的分镜脚本逐镜检查，识别**可能影响视频生成质量**的硬伤。

只报告以下 7 类问题（其他不要列）：
  1. 时序矛盾：kf[0].desc 写"破水"但 kf[1].desc 写"已躺血"——pre/post 倒装
  2. 锚点缺失：角色名出现但 ff_desc/lf_desc/prompt 都没提及其姿态/位置/朝向
  3. 空间方位缺：prompt 写"看<人名>"但没说目光方向（应"目光紧盯<人名>看画左/画右"）
  4. 致命伤断裂：shot N 出现致命伤，后续 shot 同 location 同角色却无伤处描述
  5. 道具空间歧义：出现"炭炉/酒壶/酒杯"等道具但没说在画 X/船头/甲板等位置
  6. 镜头轴线跳：连续 3+ 镜同一对角色对话但 camera axis 在 180° 间跳变
  7. 抽象词污染：出现"气势/氛围/感受"等抽象词（模型无法渲染）

输出格式（严格遵守）：
  shot N: <问题简述，≤30 字>

每个问题一行，不要任何解释/编号/总评。如果脚本没问题，输出"OK"。

只输出"shot N: ..."或"OK"，不要输出其他内容。
"""


def _build_user_prompt(script: dict) -> str:
    """压缩 script 为 LLM 友好格式."""
    lines: List[str] = []
    hook = script.get("hook") if isinstance(script, dict) else None
    if hook and isinstance(hook, dict):
        h_prompt = (hook.get("prompt") or "").strip()
        h_ff = (hook.get("ff_desc") or "").strip()
        lines.append(f"[HOOK] prompt={h_prompt} | ff={h_ff}")
    shots = script.get("shots", []) if isinstance(script, dict) else []
    for s in shots:
        if not isinstance(s, dict):
            continue
        idx = s.get("index", "?")
        prompt = (s.get("prompt") or "").strip()
        ff = (s.get("ff_desc") or "").strip()
        lf = (s.get("lf_desc") or "").strip()
        motion = (s.get("motion_desc") or "").strip()
        loc = (s.get("location") or "").strip()
        duration = s.get("duration_sec", "")
        kfs = s.get("keyframes", []) or []
        kf_lines = []
        for k in kfs:
            if isinstance(k, dict):
                t = k.get("t_sec", "?")
                d = (k.get("desc") or "").strip()
                kf_lines.append(f"t={t}: {d}")
        lines.append(
            f"[Shot {idx}] loc={loc} dur={duration}s\n"
            f"  prompt: {prompt}\n"
            f"  ff: {ff} | lf: {lf}\n"
            f"  motion: {motion}\n"
            f"  kfs: {' | '.join(kf_lines) if kf_lines else '无'}"
        )
    return "\n".join(lines) if lines else "(空脚本)"


_SHOT_LINE_RE = re.compile(r"^\s*shot\s+(\d+)\s*[:：]\s*(.+?)\s*$", re.IGNORECASE)


def _parse_review_output(text: str) -> List[Violation]:
    """解析 LLM 自检输出，转换为 Violation 列表.

    期望格式：每行 "shot N: <issue>"
    其他行忽略（包括 "OK"）。
    """
    violations: List[Violation] = []
    if not isinstance(text, str):
        return violations
    for line in text.splitlines():
        m = _SHOT_LINE_RE.match(line)
        if not m:
            continue
        try:
            shot_idx = int(m.group(1))
        except (TypeError, ValueError):
            continue
        msg = m.group(2).strip()
        if not msg:
            continue
        violations.append(
            Violation(
                constraint_name="self_review",
                severity=Severity.WARN,
                shot_index=shot_idx,
                field=None,
                message=msg[:80],
                evidence=line.strip(),
            )
        )
    return violations


def _call_llm_review(script: dict) -> Optional[str]:
    """调一次 LLM 做自检. 返回 LLM 原始输出，失败返回 None."""
    try:
        from _text_gen import create_service, TextGenerationRequest
    except ImportError:
        return None
    try:
        service = create_service()
        req = TextGenerationRequest(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(script),
            max_tokens=2000,
            temperature=0.1,
            top_p=0.3,
        )
        result = service.generate(req)
        if result and result.success and result.text:
            return result.text
    except Exception:
        return None
    return None


class SelfReviewConstraint(Constraint):
    """LLM 自检：parse_response 后调一次 LLM 自审每个 shot.

    severity=INFO: 不阻断流程，仅记录违例供 LLM 下一轮修复参考.
    默认 disabled：SELF_REVIEW_ENABLED=1 才启用.
    """

    name = "self_review"
    severity = Severity.INFO
    skip_hook = True  # 只检 shot，不检 hook

    def is_enabled(self) -> bool:
        # v6.43（08-23 审计）：docstring 与代码默认状态不一致——文档说
        # 默认 disabled（opt-in），但代码默认 "1" 会导致跑批时悄悄调 LLM，
        # 既不必要又可能改写. 修齐：默认 "0"（opt-in），要启用显式
        # SELF_REVIEW_ENABLED=1.
        flag = os.getenv("SELF_REVIEW_ENABLED", "0").strip()
        return flag != "0"

    def apply(self, ctx) -> list[RepairRecord]:
        """调 LLM 自检，把结果作为 Violation 上报（不自动 fix）.

        每次 apply_constraints 调用最多 1 次 LLM 调用（用 ctx 上的 flag 防止重入）.
        """
        # 防御：apply() 直接调用时也走 is_enabled 开关
        if not self.is_enabled():
            return []
        if not isinstance(ctx.script, dict):
            return []
        # 防止重入（apply_constraints 跑多遍时只第一次调）
        if getattr(ctx, "_self_review_done", False):
            return []

        text = _call_llm_review(ctx.script)
        # 即使 LLM 失败也标记 done（避免反复重试）
        ctx._self_review_done = True
        if text is None:
            return []  # LLM 失败静默

        violations = _parse_review_output(text)
        for v in violations:
            ctx.record_violation(v)

        # 不返回 repairs（没修，只标）
        return []
