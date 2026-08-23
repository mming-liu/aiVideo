"""step01 单元测试共用 fixture."""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录可导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def make_shot(
    index: int = 1,
    prompt: str = "[中景] 镜头描述",
    ff_desc: str = "",
    lf_desc: str = "",
    motion_desc: str = "",
    duration_sec: float = 4.0,
    location: str = "小舟",
    keyframes: list | None = None,
    char_layout: list | None = None,
    narration_segment: str = "",
    subtitle: str = "",
    main_subject: str = "",
    **extra,
) -> dict:
    """构造一个最小 shot dict 用于测试.

    所有 kwargs 都是可选的——只传你关心的字段。defaults 给的是"古风小舟"场景
    的典型值，方便写 1-2 个测试。
    """
    shot = {
        "index": index,
        "prompt": prompt,
        "ff_desc": ff_desc,
        "lf_desc": lf_desc,
        "motion_desc": motion_desc,
        "duration_sec": duration_sec,
        "location": location,
        "keyframes": keyframes or [],
        "char_layout": char_layout or [],
        "narration_segment": narration_segment,
        "subtitle": subtitle,
    }
    if main_subject:
        shot["main_subject"] = main_subject
    shot.update(extra)
    return shot


def make_script(
    shots: list | None = None,
    *,
    hook: dict | None = None,
    characters: list | None = None,
) -> dict:
    """构造一个最小 script dict.

    Args:
        shots: shots 列表（默认空）
        hook: hook 分镜 dict（默认 None）
        characters: characters 列表（默认空）
    """
    return {
        "shots": shots or [],
        "hook": hook,
        "characters": characters or [],
    }


def make_ctx(
    script: dict,
    *,
    characters_bank: dict | None = None,
    props_bank: dict | None = None,
    novel_text: str = "",
    is_in_repair_loop: bool = False,
    is_final_pass: bool = False,
):
    """构造一个 ConstraintContext 用于测试.

    Args:
        script: script dict（必填，make_script 构造）
        characters_bank: 角色库 dict
        props_bank: 道具资产库 dict
        novel_text: 小说原文
        is_in_repair_loop: True=repair round 末尾
        is_final_pass: True=落盘前 final pass
    """
    from step01_script_gen.constraints import ConstraintContext

    return ConstraintContext.from_script(
        script,
        characters_bank=characters_bank or {},
        props_bank=props_bank or {},
        novel_text=novel_text,
        is_in_repair_loop=is_in_repair_loop,
        is_final_pass=is_final_pass,
    )
