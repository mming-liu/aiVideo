"""step01_script_gen.outline - 章节大纲生成（ShortVideoDirector director-outline 吸收）.

在分镜生成前，先用 LLM 生成本章大纲（主要事件/角色出场/情绪弧线/集尾钩子），
注入分镜 prompt 作为「大纲锚点」，让分镜生成参考大纲，保证冲突/高潮分布。
"""
from __future__ import annotations

import json
import hashlib
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
  "key_scenes": ["关键场景1", "场景2", ...],
  "required_facts": ["事实A｜原文证据：...", "事实B｜原文证据：..."],
  "causal_chain": ["起因→行动→结果→悬念"],
  "character_motives": ["角色：想做什么；为什么；原文证据：..."],
  "shot_beats": ["节拍：...｜本镜信息目的：...｜原文依据：..."],
  "time_budget": "对白约Xs；动作约Ys；反应约Zs"
}}

# 规则
- main_events: 按时间顺序列出 3-6 个主要事件，覆盖全章
- required_facts: 不得省略的关键事实，必须附原文证据
- causal_chain: 每个关键转折必须具备起因→行动→结果→悬念
- character_motives: 说明角色为什么这样做，并附原文证据
- shot_beats: 覆盖每条 main_events，按原文顺序排列；每个节拍只承载一条主要信息，标出信息目的和证据
- time_budget: 给对白、动作、反应预留可执行时间，不能靠加速朗读掩盖
- emotional_arc: 一句话概括情绪走向
- climax: 全章最震撼/冲突最强的事件
- end_hook: 本章结尾已有的悬念，必须有原文依据；不得为了另拍钩子视频虚构事件
- key_scenes: 需要重点刻画的关键场景（动作/情感高潮）
"""


def generate_outline(novel_text: str, model: str | None = None,
                     cache_path: Path | None = None) -> dict | None:
    """生成章节大纲；同章文本、模型和规则未变时复用磁盘缓存。"""
    if not novel_text:
        return None
    from shared.config import PROMPT_MAX_CHARS
    prompt = OUTLINE_PROMPT.format(novel_text=novel_text[:PROMPT_MAX_CHARS])
    system_prompt = "你是短视频编剧，擅长从小说章节提炼叙事大纲，供分镜参考。只输出纯JSON。"
    # v6.125.2（用户 2026-09-25 决定）：道具状态**候选锚点**随大纲一起携带。
    # 它由**确定性扫描**从原文得出（不经 LLM），只取 `motion_away` / `marker_after`
    # 两种形状，带原文位置与 `status="待核实"`；生成/GAP 重生用**独立短提示**注入
    # （不能并进 `outline_anchor`——agent_plan 会把大纲锚点压到约 200 字，
    # 除【高潮事件】外的行会被裁掉）。
    from step01_script_gen.prop_state_anchor import outline_prop_anchors
    _prop_anchors = outline_prop_anchors(novel_text)
    cache_key = None
    if cache_path is not None:
        from _text_gen.config import TextGenCfg
        cache_path = Path(cache_path)
        cache_source = {
            "novel_text": novel_text,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "schema": OUTLINE_SCHEMA,
            "provider": TextGenCfg.PROVIDER,
            "model": model or TextGenCfg.default_text_model(),
            "temperature": 0.3,
            "max_tokens": 4000,
            # v6.125.2：候选锚点进缓存键——**提取规则变化**（同一段原文得出不同
            # 候选）必须让缓存失效，否则旧锚点会被当成新规则的结果复用。
            "prop_anchors": _prop_anchors,
        }
        cache_key = hashlib.sha256(json.dumps(
            cache_source, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")).hexdigest()
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (isinstance(cached, dict) and cached.get("source_hash") == cache_key
                    and _validate_outline(cached.get("outline"))):
                print(f"[CACHE] 复用章节大纲: {cache_path}")
                # 缓存里若缺该字段（旧缓存但键相同——理论上不会，键已含锚点），
                # 用本次算出的补上，避免调用方拿到 None 而丢掉独立短提示
                if isinstance(cached["outline"], dict):
                    cached["outline"].setdefault("prop_state_candidates", _prop_anchors)
                return cached["outline"]
        except (OSError, ValueError, TypeError):
            pass
    service = create_service()
    req = TextGenerationRequest(
        system_prompt=system_prompt,
        user_prompt=prompt,
        max_tokens=4000,
        temperature=0.3,
        json_schema=OUTLINE_SCHEMA,
        model=model,
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
    outline = _validate_outline(data)
    if outline:
        # 候选锚点是**确定性**产物（不经 LLM），挂在大纲 dict 上随稿流动；
        # `_validate_outline` 只做字段净化、不重建 dict，所以不会把它丢掉。
        outline["prop_state_candidates"] = _prop_anchors
    if outline and cache_path is not None:
        try:
            from shared.json_io import atomic_dump_json
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_dump_json(cache_path, {"source_hash": cache_key, "outline": outline},
                             ensure_ascii=False, indent=2)
        except OSError as exc:
            print(f"[WARN] 章节大纲缓存写入失败（本次继续使用）: {exc}")
    return outline


def _validate_outline(outline: dict | None) -> dict | None:
    """大纲合法性校验（C2 修复 08-11）.

    校验项：
    1. main_events 有 3-6 条非空事件（空列表/全空串 → 不合格）
    2. climax 非空（高潮事件是分镜分布的锚点，空则锚点失效）
    3. required_facts / causal_chain 非空，shot_beats 至少覆盖每条事件
    4. 净化：列表元素 strip、过滤空串
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
    if not 3 <= len(cleaned_events) <= 6:
        print("[WARN] 大纲校验失败：main_events 须有 3-6 条（不注入不完整大纲）")
        return None
    for field in ("required_facts", "causal_chain", "shot_beats"):
        values = outline.get(field)
        cleaned = ([item.strip() for item in values
                    if isinstance(item, str) and item.strip()]
                   if isinstance(values, list) else [])
        if not cleaned:
            print(f"[WARN] 大纲校验失败：{field} 为空（不注入不完整大纲）")
            return None
        outline[field] = cleaned
    if len(outline["shot_beats"]) < len(cleaned_events):
        print("[WARN] 大纲校验失败：shot_beats 未覆盖全部主要事件")
        return None
    climax = str(outline.get("climax") or "").strip()
    if not climax:
        print("[WARN] 大纲校验失败：climax（高潮事件）为空（不注入空大纲锚点）")
        return None
    outline["climax"] = climax
    return outline


# 与 prompts.py「总时长硬约束」同源的规划目标（合法区间 60-90s，规划目标 70-80s）
_TARGET_MIN_SEC = 70
_TARGET_MAX_SEC = 80
# 匹配「30s」「30秒」「30 s」
_BUDGET_NUM_RE = __import__("re").compile(r"(\d+(?:\.\d+)?)(\s*[sS秒])")


def _calibrate_time_budget(budget: str) -> str:
    """把大纲的时间预算**校准到目标区间**（v6.116，审核）.

    实测缓存大纲写「对白约30s；动作约60s；反应约30s」≈ **120s**，而生成约束的规划
    目标是 **70-80s**——两个信号互相矛盾（`_validate_outline` 此前不检查这一项），
    模型会照着大纲超配时长。校准按**原比例**缩放到目标中点，其余文字不变；解析不出
    数字时原样返回（自由文本没有矛盾数字，注入无害）。
    """
    text = str(budget or "").strip()
    if not text:
        return ""
    nums = [float(m.group(1)) for m in _BUDGET_NUM_RE.finditer(text)]
    total = sum(nums)
    if not nums or (_TARGET_MIN_SEC <= total <= _TARGET_MAX_SEC):
        return text
    target = (_TARGET_MIN_SEC + _TARGET_MAX_SEC) / 2.0
    scaled = iter(round(n * target / total, 1) for n in nums)

    def _rep(m):
        try:
            v = next(scaled)
        except StopIteration:
            return m.group(0)
        return ("%g" % v) + m.group(2)

    return _BUDGET_NUM_RE.sub(_rep, text) + (
        "（已按目标 %d-%d 秒校准）" % (_TARGET_MIN_SEC, _TARGET_MAX_SEC))


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
    if outline.get("required_facts"):
        parts.append("【必须保留事实】" + "；".join(outline["required_facts"]))
    if outline.get("causal_chain"):
        parts.append("【因果链】" + "；".join(outline["causal_chain"]))
    if outline.get("character_motives"):
        parts.append("【角色动机】" + "；".join(outline["character_motives"]))
    if outline.get("shot_beats"):
        parts.append("【镜头节拍】" + "；".join(outline["shot_beats"]))
    # v6.116：预算先校准——大纲写 120s 而生成目标 70-80s 会让模型超配时长
    _budget = _calibrate_time_budget(outline.get("time_budget"))
    if _budget:
        parts.append(f"【时间预算】{_budget}")
    return "\n".join(parts)
