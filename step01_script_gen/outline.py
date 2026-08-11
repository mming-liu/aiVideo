"""step01_script_gen.outline - 章节大纲生成（ShortVideoDirector director-outline 吸收）.

在分镜生成前，先用 LLM 生成本章大纲（主要事件/角色出场/情绪弧线/集尾钩子），
注入分镜 prompt 作为「大纲锚点」，让分镜生成参考大纲，保证冲突/高潮分布。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from _text_gen import create_service, TextGenerationRequest
from _text_gen.deepseek_schema import OUTLINE_SCHEMA


OUTLINE_PROMPT = """
阅读以下小说原文，生成章节大纲，供后续分镜生成参考。

# 小说原文
{novel_text}

# 输出格式
只输出纯JSON，格式如下：
{{
  "main_events": ["主要事件1", "主要事件2", ...],
  "characters": ["出场角色1", "角色2", ...],
  "emotional_arc": "本章情绪弧线（如：平静→惊疑→爆发→余波）",
  "climax": "本章高潮事件（冲突/转折点）",
  "end_hook": "结尾钩子（吸引观众看下一集）",
  "key_scenes": ["关键场景1", "场景2", ...]
}}

# 规则
- main_events: 按时间顺序列出 3-6 个主要事件，覆盖全章
- emotional_arc: 一句话概括情绪走向
- climax: 全章最震撼/冲突最强的事件
- end_hook: 结尾留的悬念/钩子
- key_scenes: 需要重点刻画的关键场景（动作/情感高潮）
"""


def generate_outline(novel_text: str) -> dict | None:
    """调用 LLM 生成章节大纲，返回 dict；失败返回 None（不阻断分镜生成）."""
    if not novel_text:
        return None
    prompt = OUTLINE_PROMPT.format(novel_text=novel_text[:8000])
    service = create_service()
    req = TextGenerationRequest(
        system_prompt="你是短视频编剧，擅长从小说章节提炼叙事大纲，供分镜参考。只输出纯JSON。",
        user_prompt=prompt,
        max_tokens=4000,
        temperature=0.3,
        json_schema=OUTLINE_SCHEMA,
    )
    result = service.generate(req)
    if not result.success:
        return None

    raw = result.text.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(raw[start:end + 1])
        else:
            return None
    # C2 修复（08-11）：大纲零校验——此前 main_events/climax 非空零校验，
    # 空大纲静默注入分镜 prompt（大纲锚点是一堆空标签，LLM 拿不到任何约束）。
    # 补两条校验：① 关键字段非空（main_events 至少1条、climax 非空）；
    # ② main_events 元素非空字符串。不合格时返回 None（调用方按"大纲生成失败"
    # 处理，不注入空锚点）。
    return _validate_outline(data)


def _validate_outline(outline: dict | None) -> dict | None:
    """大纲合法性校验（C2 修复 08-11）.

    校验项：
    1. main_events 至少 1 条非空事件（空列表/全空串 → 不合格）
    2. climax 非空（高潮事件是分镜分布的锚点，空则锚点失效）
    3. 净化：main_events 元素 strip、过滤空串
    不合格返回 None（调用方不注入空锚点，避免「【本章主要事件】」空标签进 prompt）。
    """
    if not isinstance(outline, dict) or not outline:
        return None
    events = outline.get("main_events") or []
    if isinstance(events, list):
        cleaned_events = [str(e).strip() for e in events if str(e).strip()]
        outline["main_events"] = cleaned_events
    else:
        cleaned_events = []
        outline["main_events"] = []
    if not cleaned_events:
        print("[WARN] 大纲校验失败：main_events 为空（不注入空大纲锚点）")
        return None
    climax = str(outline.get("climax") or "").strip()
    if not climax:
        print("[WARN] 大纲校验失败：climax（高潮事件）为空（不注入空大纲锚点）")
        return None
    outline["climax"] = climax
    return outline


def outline_to_anchor(outline: dict | None) -> str:
    """把大纲 dict 转成可注入分镜 prompt 的锚点文本.

    C2 修复：内部先过 _validate_outline——无效大纲（空 main_events/空 climax）
    返回空串，不注入空标签。
    """
    outline = _validate_outline(outline)
    if not outline:
        return ""
    parts = []
    if outline.get("main_events"):
        parts.append("【本章主要事件】" + "；".join(outline["main_events"]))
    if outline.get("emotional_arc"):
        parts.append(f"【情绪弧线】{outline['emotional_arc']}")
    if outline.get("climax"):
        parts.append(f"【高潮事件】{outline['climax']}")
    if outline.get("end_hook"):
        parts.append(f"【结尾钩子】{outline['end_hook']}")
    if outline.get("characters"):
        parts.append("【出场角色】" + "、".join(outline["characters"]))
    return "\n".join(parts)
