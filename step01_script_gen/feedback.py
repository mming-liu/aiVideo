"""feedback — 重生成 LLM 的本地校验反馈生成器（v6.60.1 自 validation.py 抽出）.

build_regen_feedback 重跑 _run_prompt_checklist 把告警按类型聚合成
「本地校验反馈」文本，供 over-limit / repair 轮注入 LLM prompt。
与 check 函数族解耦：改反馈文案/聚合策略不再动 validation.py 本体。

对 validation 内部符号的引用全部走函数内延迟 import（避免循环导入：
validation 模块级 re-export 本函数）。
"""
from __future__ import annotations


def build_regen_feedback(script: dict, novel_text: str = "",
                         max_categories: int = 99, per_line_chars: int = 300) -> str:
    """把分镜检查表告警按类型聚合，生成给重生成 LLM 的「本地校验反馈」.

    v6.33（08-12）：checklist 的 WARN 此前只 print 不返回，重生成 LLM
    看不到内容密度/景别跳跃/致命伤等真实缺陷 → 重生成还是老问题。本函数重跑
    _run_prompt_checklist（抑制 stdout 避免日志刷屏），把告警按类型聚合：
    每类 1 行「- 类别 N处：首例」，取告警数最多的前 max_categories 类，
    排除需人工判断/结构性的类别（_REGEN_FEEDBACK_EXCLUDE）。返回 "" 表示无反馈。

    v6.36（08-15）：默认放开——max_categories=99（全部非排除类别，不再只取 top-5）
    + per_line_chars=300（首例几乎完整，不再 72 字截断），重生成 LLM 看全量分析、
    能修更多。若需控制 prompt 体积可显式传小值。

    Args:
        script: 当前待重生成的分镜脚本（重生成前已 parse_response，含完整字段）
        novel_text: 小说原文（供原文锚点类检查；默认空则跳过）
        max_categories: 最多反馈几类（默认 99=全部非排除类别）
        per_line_chars: 每行首例截断长度（默认 300，几乎不截）
    """
    from step01_script_gen.validation import (
        _REGEN_FEEDBACK_EXCLUDE,
        _REGEN_FEEDBACK_LABELS,
        _run_prompt_checklist,
        _set_novel_text,
    )
    try:
        if novel_text:
            _set_novel_text(novel_text)
        import io as _io
        import contextlib as _ctx
        _buf = _io.StringIO()
        with _ctx.redirect_stdout(_buf):  # 抑制 刷屏
            results = _run_prompt_checklist(script).get("results", {})
    except Exception:
        return ""
    grouped = {}
    for key, res in results.items():
        if key in _REGEN_FEEDBACK_EXCLUDE or not isinstance(res, dict):
            continue
        warns = list(res.get("warnings") or []) + list(res.get("issues") or [])
        if warns:
            grouped[key] = warns
    if not grouped:
        return ""
    top = sorted(grouped.items(), key=lambda kv: -len(kv[1]))[:max_categories]
    lines = []
    for key, warns in top:
        label = _REGEN_FEEDBACK_LABELS.get(key, key)
        # 08-20（v6.37）：改为每类全量例举（之前只首例，信息不足）。
        # 提取每条 warning 的镜头号前缀，列出所有命中镜头号；再附首例完整文本（截断）。
        from step01_script_gen.quality_issues import parse_shot_ref
        shot_ids = set()
        for w in warns:
            sid, related = parse_shot_ref(w)
            for value in ([sid] if sid is not None else []) + related:
                shot_ids.add("钩子" if str(value) in ("0", "hook") else f"镜头{value}")
        shot_str = ",".join(sorted(shot_ids)) or "全局"
        first = warns[0].strip()
        if len(first) > per_line_chars:
            first = first[:per_line_chars] + "…"
        lines.append(f"- {label} {len(warns)}处（{shot_str}）：{first}")
    body = "\n".join(lines)
    return (
        "\n\n# 本地校验反馈（来自分镜检查表，请针对性修正；「N处」=命中镜头数，括号内=镜头号）\n"
        + body + "\n"
    )
