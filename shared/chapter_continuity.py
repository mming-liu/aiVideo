# -*- coding: utf-8 -*-
"""跨章连续性：章末状态提取与承接告警（2026-11-28 O9）.

**只读、零模型调用**：从已提交的 `script_result.json` 末镜确定性提取「章末状态」
（在场角色与外观锚点、随身道具、场景/时间/色温、终态画面），供下一章规划时作为
只读参考事实；并对照前章状态对新章开场做**可审计告警**（不阻断生成）。

设计边界：
- 只做**事实提取 + 告警**，不改写剧本、不注入提示词（提示词注入需要真实 Step01
  回归，属另一包）；
- 告警一律 advisory：断裂判断是启发式的，不能冒充硬门；
- 提取结果完全由输入决定（无时间戳、无随机），同一份剧本两次提取逐字节相同。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA = "chapter_end_state/1"
FILENAME = "chapter_end_state.json"

_APPEARANCE_RE = re.compile(r"\[([^\[\]:：]{1,24})[:：]\s*([^\[\]]*)\]")
#: 时间词 → 归一桶（越具体越先匹配；同一桶避免「夜/夜色」这类同义写法误报跳变）
_TIME_TOKENS = (("凌晨", "凌晨"), ("深夜", "夜"), ("夜色", "夜"), ("夜晚", "夜"),
                ("正午", "正午"), ("午后", "午后"), ("清晨", "晨"), ("黎明", "晨"),
                ("黄昏", "昏"), ("傍晚", "昏"), ("白天", "白天"), ("夜", "夜"),
                ("晨", "晨"), ("昏", "昏"))
_PUNCT_RE = re.compile(r"[\s，。、；：！？,.!?;:·…—\-—]+")


def _text(value) -> str:
    return str(value or "").strip()


def _first(mapping: dict, *keys: str) -> str:
    for key in keys:
        value = _text(mapping.get(key))
        if value:
            return value
    return ""


def _appearance_map(shot: dict) -> dict[str, str]:
    """从镜头 prompt 的 `[角色: 外观描述]` 片段提取外观锚点（确定性）."""
    prompt = _text(shot.get("prompt"))
    out: dict[str, str] = {}
    for name, desc in _APPEARANCE_RE.findall(prompt):
        name = _text(name)
        if name and name not in out:
            out[name] = _text(desc)
    return out


def _time_of_day(shot: dict) -> str:
    """归一后的时刻桶（夜/晨/昏/正午/午后/白天/凌晨）；识别不到返回空串."""
    text = " ".join(_text(shot.get(key)) for key in
                    ("prompt", "lf_desc", "ff_desc", "context_bridge"))
    best = None
    for token, bucket in _TIME_TOKENS:
        index = text.find(token)
        if index >= 0 and (best is None or index < best[0]
                           or (index == best[0] and len(token) > len(best[1]))):
            best = (index, token, bucket)
    return best[2] if best else ""


def _on_screen(shot: dict) -> tuple[list[str], list[str]]:
    """返回末镜在场角色名（按出场顺序）与道具名（去重）."""
    names: list[str] = []
    props: list[str] = []
    layouts = shot.get("frame_layouts") or {}
    for role in ("ff", "lf"):
        for entry in (layouts.get(role) or []):
            if not isinstance(entry, dict):
                continue
            if entry.get("on_screen") is False:
                continue
            name = _text(entry.get("name"))
            if name and name not in names:
                names.append(name)
            for prop in (entry.get("props") or []):
                prop = _text(prop)
                if prop and prop not in props:
                    props.append(prop)
    subject = _text(shot.get("main_subject"))
    if subject and subject not in names:
        names.insert(0, subject)
    return names, props


def extract_end_state(script: dict, *, chapter: int | None = None) -> dict:
    """从剧本末镜提取章末状态（纯函数，确定性）."""
    if not isinstance(script, dict):
        raise ValueError("extract_end_state 需要 script_result 字典")
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    last = shots[-1] if shots else {}
    names, props = _on_screen(last)
    appearance = _appearance_map(last)
    chapter_value = chapter if chapter is not None else script.get("chapter_index")
    try:
        chapter_value = int(chapter_value or 0)
    except (TypeError, ValueError):
        chapter_value = 0
    return {
        "schema": SCHEMA,
        "chapter": chapter_value,
        "book_name": _text(script.get("book_name")),
        "script_revision": _text(script.get("script_revision")),
        "last_shot_index": last.get("index"),
        "scene": _first(last, "scene", "location"),
        "location": _text(last.get("location")),
        "time_of_day": _time_of_day(last),
        "color_tone": _text(last.get("color_tone")),
        "final_frame": _text(last.get("lf_desc")),
        "characters_on_screen": [
            {"name": name, "appearance": appearance.get(name, "")}
            for name in names],
        "props": props,
        "source": {"shot_count": len(shots), "kind": "script_result.last_shot"},
    }


def _first_shot(script: dict) -> dict:
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    return shots[0] if shots else {}


def _norm(text: str) -> str:
    return _PUNCT_RE.sub("", _text(text))


def compare_continuity(prev_state: dict, script: dict) -> list[dict]:
    """对照前章终态与新章开场，返回 advisory 告警列表（不阻断）."""
    if not isinstance(prev_state, dict) or prev_state.get("schema") != SCHEMA:
        raise ValueError("prev_state 必须是 %s 格式（用 extract_end_state 生成）" % SCHEMA)
    shot = _first_shot(script)
    names, props = _on_screen(shot)
    appearance = _appearance_map(shot)
    problems: list[dict] = []

    def add(code: str, message: str, **fields) -> None:
        problems.append({"code": code, "severity": "advisory",
                         "message": message, **fields})

    for entry in (prev_state.get("characters_on_screen") or []):
        name = _text((entry or {}).get("name"))
        if not name:
            continue
        if name not in names:
            add("character_absent_at_start",
                "上章末镜在场角色 %r 未出现在本章开场镜头（可能已离场/切视角）" % name,
                character=name)
            continue
        before = _norm((entry or {}).get("appearance"))
        after = _norm(appearance.get(name))
        if before and after and before != after:
            add("appearance_changed",
                "角色 %r 的外观锚点在本章开场与上章末镜不一致（换了服装/伤痕/状态？）" % name,
                character=name, previous=(entry or {}).get("appearance"),
                current=appearance.get(name))

    prev_scene = _text(prev_state.get("scene"))
    scene = _first(shot, "scene", "location")
    if prev_scene and scene and _norm(prev_scene) != _norm(scene):
        add("scene_hard_jump",
            "本章开场场景 %r 与上章末镜 %r 不同（确认是转场还是断裂）"
            % (scene, prev_scene), previous=prev_scene, current=scene)

    prev_time = _text(prev_state.get("time_of_day"))
    time_now = _time_of_day(shot)
    if prev_time and time_now and prev_time != time_now:
        add("time_of_day_jump",
            "本章开场时间 %r 与上章末镜 %r 不同" % (time_now, prev_time),
            previous=prev_time, current=time_now)

    prev_props = [_text(p) for p in (prev_state.get("props") or []) if _text(p)]
    missing = [p for p in prev_props if p not in props]
    if missing:
        add("props_missing_at_start",
            "上章末镜的随身道具未在本章开场出现：%s" % "、".join(missing),
            props=missing)
    return problems


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
def write_end_state(script: dict, path: Path, *, chapter: int | None = None) -> dict:
    """提取并原子写入 `chapter_end_state.json`；返回写入的状态."""
    from shared.json_io import atomic_dump_json

    state = extract_end_state(script, chapter=chapter)
    atomic_dump_json(Path(path), state, ensure_ascii=False, indent=2)
    return state


def load_end_state(path: Path) -> dict:
    """读取并校验章末状态；schema 不符/不可读时显式失败."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("章末状态不可读: %s（%s）" % (path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise ValueError("章末状态不是合法 JSON: %s（%s）" % (path, exc)) from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("章末状态 schema 不符（期望 %s）: %s" % (SCHEMA, path))
    return data


__all__ = ["FILENAME", "SCHEMA", "compare_continuity", "extract_end_state",
           "load_end_state", "write_end_state"]
