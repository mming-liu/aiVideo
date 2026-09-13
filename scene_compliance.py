"""分镜合规校验模块 — 场景矛盾/敏感内容/画面字幕覆盖率 自动校验与阻断.

功能:
  - 场景室内/室外矛盾检测（字幕和画面同镜矛盾→阻断）
  - 暴力/血腥/裸露敏感内容检测（关键词+语义级→阻断）
  - 字幕核心实词在画面中的覆盖率校验（程序级而非仅靠 LLM 自查）
  - 全 script 批量校验 → 返回合规/不合规报告
  - 提示词字段通用校验（长度、前缀、景别多样性等）
"""

from __future__ import annotations

import json
import re
from typing import Any

# ===========================================================================
# 一、敏感内容词库（分级过滤）
# ===========================================================================

# --- 1a. 暴力动作词 ---
_VIOLENT_ACTION_WORDS = {
    "刺进", "刺穿", "刺入", "捅进", "插进", "扎进",
    "砍断", "斩断", "砍下", "剁下", "劈开",
    "割喉", "割断", "割裂", "切开",
    "穿透身体", "贯穿", "透体而出",
    "折断", "扭断", "掐断",
    "打碎", "砸碎", "碎裂", "捏碎",
    "鲜血飞溅", "血溅", "血流如注", "血流成河", "鲜血淋漓",
    "血肉模糊", "脑浆迸裂", "开膛破肚",
    "杀死", "击杀", "掐死", "勒死", "毒死",
    "尸体堆积", "尸横遍野",
}

_BLOOD_GORE_WORDS = {"脑浆", "内脏", "肠子", "碎肉", "断肢", "断臂", "断头", "残肢", "白骨", "骷髅", "腐肉"}

_SEXUAL_EXPLICIT_WORDS = {
    "裸体", "赤裸", "一丝不挂",
    "酥胸", "玉体", "胴体", "香肩半露",
    "抚摸", "亲吻", "缠绵", "交缠",
    "喘息", "娇喘", "呻吟",
    "挑逗", "诱惑", "勾引",
}

# --- 1b. 场景矛盾词库 ---
_INDOOR_WORDS = {
    "宫殿", "府衙", "签押房", "大堂", "书房", "监牢", "密室",
    "酒楼", "客栈", "后花园", "卧房", "闺房", "厅堂", "殿内",
    "室内", "屋内", "房内", "房中", "窗内",
    "飞檐", "画舫", "烛火", "灯火",
    "回廊", "走廊", "檐下",
}

_OUTDOOR_WORDS = {
    "竹林", "山巅", "云海", "河畔", "月下", "田野", "枯柳",
    "樱花", "庭院", "道场", "长街", "山路", "荒漠", "戈壁",
    "大地", "烈日", "城门", "军营", "古城", "青州",
    "荒野", "古道", "密林", "深潭", "断崖", "瀑布",
    "渡口", "石桥", "沙场", "长亭", "茅舍",
    "室外", "屋外", "野外", "户外",
    "街头", "巷口", "桥上", "树下", "山顶", "岸边",
}

# ===========================================================================
# 二、合规校验核心函数
# ===========================================================================


def _find_matching_words(text: str, keywords: set[str]) -> set[str]:
    """返回 keywords 中出现在 text 字符串内的所有词。"""
    return {w for w in keywords if w in text}


def check_scene_contradiction(subtitle: str, prompt_text: str) -> dict:
    """检测单镜场景矛盾：字幕说室内但画面室外（或反之）。"""
    sub_indoor = _find_matching_words(subtitle, _INDOOR_WORDS)
    sub_outdoor = _find_matching_words(subtitle, _OUTDOOR_WORDS)
    prompt_indoor = _find_matching_words(prompt_text, _INDOOR_WORDS)
    prompt_outdoor = _find_matching_words(prompt_text, _OUTDOOR_WORDS)

    result = {"pass": True, "reason": "", "detail": {}}

    if sub_indoor and prompt_outdoor and not prompt_indoor:
        result["pass"] = False
        result["reason"] = f"场景矛盾：字幕在室内({sorted(sub_indoor)[:3]})，prompt在室外({sorted(prompt_outdoor)[:3]})"
    elif sub_outdoor and prompt_indoor and not prompt_outdoor:
        result["pass"] = False
        result["reason"] = f"场景矛盾：字幕在室外({sorted(sub_outdoor)[:3]})，prompt在室内({sorted(prompt_indoor)[:3]})"

    return result


def check_content_sensitive(subtitle: str, prompt_text: str) -> dict:
    """检测单镜敏感内容（暴力/性/血腥）。"""
    result = {"pass": True, "found_violent": [], "found_sexual": [], "found_gore": []}
    search_text = subtitle + prompt_text

    for pattern in _VIOLENT_ACTION_WORDS:
        if pattern in search_text:
            result["found_violent"].append(pattern)

    for pattern in _SEXUAL_EXPLICIT_WORDS:
        if pattern in search_text:
            result["found_sexual"].append(pattern)

    for pattern in _BLOOD_GORE_WORDS:
        if pattern in search_text:
            result["found_gore"].append(pattern)

    if result["found_violent"] or result["found_sexual"] or result["found_gore"]:
        result["pass"] = False
        # v6.33（08-12）：补 reason——此前返回无 reason，validation.py:178 打印
        # 「镜头X sensitive: 」空内容（日志 08-12 镜头5/8/9）。拼出命中的敏感词。
        parts = []
        if result["found_violent"]:
            parts.append("暴力:" + "、".join(result["found_violent"]))
        if result["found_sexual"]:
            parts.append("性暗示:" + "、".join(result["found_sexual"]))
        if result["found_gore"]:
            parts.append("血腥:" + "、".join(result["found_gore"]))
        result["reason"] = "画面含敏感内容 " + "；".join(parts) + "（将自动合规替换）"

    return result


def _extract_candidates(subtitle: str) -> list[str]:
    """从字幕中提取候选实词（2-6字片段）。"""
    segs = re.split(r"[^一-鿿]+", subtitle)
    candidates: list[str] = []
    for s in segs:
        s = s.strip()
        if len(s) < 2:
            continue
        if len(s) <= 6:
            candidates.append(s)
        else:
            candidates.append(s[:4])
            candidates.append(s[:2])
            candidates.append(s[-2:])
            mid = len(s) // 2
            if mid + 2 <= len(s):
                candidates.append(s[mid - 1 : mid + 1])
    # 去重保持顺序
    seen: set[str] = set()
    uniq: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


_STOP_NOUNS = {
    "一个", "没有", "不是", "什么", "怎么", "可以", "这个", "那个",
    "他们", "你们", "我们", "自己", "起来", "出来", "过来", "进去",
    "已经", "还是", "就是", "只是", "但是", "然而", "因为", "所以",
    "如果", "虽然", "而且", "或者", "之后", "之前", "然后", "最后",
    "不过", "那里", "这里", "那些", "这些", "那么", "为何", "如何",
}

_SUBSTITUTION_MAP = {
    "太阳": {"烈日", "骄阳", "日光", "阳光", "强光"},
    "大地": {"焦土", "土地", "地面", "黄土", "大地", "田野"},
    "火炉": {"烈", "炙", "灼"},
    "烘烤": {"炙烤", "灼烧", "暴晒"},
    "田间": {"田野", "田地", "田间"},
    "冒烟": {"热气", "蒸腾", "升腾", "烟尘"},
    "青州": {"青州"},
}


def check_subtitle_coverage(subtitle: str, prompt_text: str, speakers: frozenset | None = None) -> dict:
    """程序级校验字幕核心实词在画面描述中的覆盖率."""
    # 对白镜特殊处理：本流水线 v4.1 起 narration/subtitle 被强制为「说话人道：台词」，
    # 即字幕内容 = 人物对白。对白文字本身不需要（也不应该）以文字形式出现在画面里，
    # 程序无法逐字匹配它。此时校验语义改为：**说话人是否出现在画面中**（有声必须有人）。
    # 这样既消除「对白镜覆盖率 0%」的批量误报，又保留"喊了话但画面里没人"的真缺陷检测。
    # 说话人从 script 的角色清单里精确匹配（避免把「惊叫/颤声」等动词修饰语误当说话人）。
    speakers = speakers or frozenset()
    if ("：" in subtitle or ":" in subtitle) and speakers:
        for sp in sorted(speakers, key=len, reverse=True):
            if subtitle.startswith(sp):
                in_prompt = sp in prompt_text or any(
                    s in prompt_text for s in _SUBSTITUTION_MAP.get(sp, set())
                )
                return {
                    "pass": bool(in_prompt),
                    "rate": 1.0 if in_prompt else 0.0,
                    "missing": [] if in_prompt else [sp],
                    "covered": [sp] if in_prompt else [],
                    "all_nouns": [sp],
                }
        # 有冒号但说话人不匹配任何已知角色 → 无法判定，降级为普通实词校验，不误报
    # 兜底：从「说话人道：」前缀正则提取（用于无角色清单/纯文本场景）
    m_dialog = re.match(r"^([一-鿿]{2,8}?)(?:道|说|喊|喝|叫|问|答|曰)[:：]", subtitle)
    if m_dialog:
        speaker = m_dialog.group(1)
        in_prompt = speaker in prompt_text or any(
            s in prompt_text for s in _SUBSTITUTION_MAP.get(speaker, set())
        )
        return {
            "pass": bool(in_prompt),
            "rate": 1.0 if in_prompt else 0.0,
            "missing": [] if in_prompt else [speaker],
            "covered": [speaker] if in_prompt else [],
            "all_nouns": [speaker],
        }

    candidates = _extract_candidates(subtitle)
    useful = [c for c in candidates if c not in _STOP_NOUNS][:15]

    covered: list[str] = []
    missing: list[str] = []

    for noun in useful:
        if noun in prompt_text:
            covered.append(noun)
            continue
        subs = _SUBSTITUTION_MAP.get(noun, set())
        if any(s in prompt_text for s in subs):
            covered.append(noun)
            continue
        missing.append(noun)

    total = len(useful)
    rate = (total - len(missing)) / total if total > 0 else 1.0

    return {
        "pass": len(missing) <= 1 or rate >= 0.5,
        "rate": round(rate, 3),
        "missing": missing[:8],
        "covered": covered,
        "all_nouns": useful,
    }


def check_shot_full(shot: dict, speakers: frozenset | None = None) -> dict:
    """单镜头全量校验，返回合规报告。"""
    report: dict = {"pass": True, "index": shot.get("index", "?"), "checks": {}, "warnings": []}

    subtitle = shot.get("subtitle", "").strip()
    search_text = " ".join(filter(None, [shot.get("prompt", ""), shot.get("ff_desc", ""), shot.get("lf_desc", ""), shot.get("motion_desc", "")]))

    if not subtitle or not search_text:
        report["warnings"].append("字幕或画面描述为空")
        return report

    # 1. 场景矛盾
    scene = check_scene_contradiction(subtitle, search_text)
    report["checks"]["scene"] = scene
    if not scene["pass"]:
        report["pass"] = False

    # 2. 敏感内容
    sensitive = check_content_sensitive(subtitle, search_text)
    report["checks"]["sensitive"] = sensitive
    if not sensitive["pass"]:
        report["pass"] = False

    # 3. 字幕实词覆盖率
    coverage = check_subtitle_coverage(subtitle, search_text, speakers)
    report["checks"]["coverage"] = coverage
    if not coverage["pass"] and coverage["rate"] < 0.6:
        report["pass"] = False
        report["warnings"].append(f"实词覆盖率仅 {coverage['rate']:.0%}，缺失词: {coverage['missing']}")

    # 4. 暴力细节检查
    prompt = shot.get("prompt", "")
    for word in _VIOLENT_ACTION_WORDS:
        if word in prompt:
            report["warnings"].append(f"画面含暴力动作描述 {word}")

    return report


def validate_script(script: dict, strict_mode: bool = False) -> dict:
    """全脚本合规校验."""
    # 收集脚本内已定义的角色名，供对白镜的"说话人是否入画"校验使用
    speakers = frozenset(
        c.get("identifier", "") for c in script.get("characters", []) if c.get("identifier")
    )
    shot_reports = [check_shot_full(s, speakers) for s in script.get("shots", [])]
    fail_count = sum(1 for r in shot_reports if not r["pass"])
    warn_count = sum(len(r["warnings"]) for r in shot_reports)

    summary = {"total_shots": len(shot_reports), "fail_count": fail_count, "warn_count": warn_count, "pass": fail_count == 0}
    result: dict = {"pass": summary["pass"], "shot_reports": shot_reports, "summary": summary}

    if strict_mode and not summary["pass"]:
        lines = []
        for r in shot_reports:
            if not r["pass"]:
                lines.append(f"镜头{r['index']}: 未通过")
                for ck, cv in r["checks"].items():
                    if not cv.get("pass", True):
                        lines.append(f"  - {ck}: {cv.get('reason', '违规')}")
                for w in r["warnings"]:
                    lines.append(f"  - {w}")
        raise ValueError("\n".join(lines))

    return result


# ===========================================================================
# 三、自动清理
# ===========================================================================


def auto_sanitize_shot_prompts(script: dict, verbose: bool = True) -> dict:
    """自动修复场景矛盾（敏感词替换 + 矛盾词删除）。"""
    from shared.style_prefix import sanitize_prompt

    for shot in script.get("shots", []):
        subtitle = shot.get("subtitle", "")
        prompt = shot.get("prompt", "")

        for field in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            val = shot.get(field, "")
            if val:
                shot[field] = sanitize_prompt(val, verbose=verbose)

        # 场景矛盾修复
        sub_indoor = _find_matching_words(subtitle, _INDOOR_WORDS)
        sub_outdoor = _find_matching_words(subtitle, _OUTDOOR_WORDS)
        prompt_indoor = _find_matching_words(prompt, _INDOOR_WORDS)

        if sub_outdoor and not sub_indoor and prompt_indoor:
            for word in prompt_indoor:
                if word not in subtitle:
                    prompt = prompt.replace(word, "")
                    if verbose:
                        print(f"  [清理] 删除矛盾场景词 {word}")

        shot["prompt"] = prompt

    return script


# ===========================================================================
# 四、便捷入口
# ===========================================================================


def quick_validate(json_path: str, strict: bool = False) -> dict:
    """对磁盘上的分镜 JSON 文件做合规校验。"""
    with open(json_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    return validate_script(script, strict_mode=strict)
