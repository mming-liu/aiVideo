"""Fix A/B 单元测试（v6.45, 08-26）——vimax_bridge 帧组装兜底.

- Fix A: _extract_prop_layout 威胁武器入体方位兜底——「匕首插在杨旭胸口」产出
  【道具锚定】匕首在胸口（修复 kf 无武器锚、匕首消失根因）。
- Fix B: _inject_character_costume 惰性角色剥离手持物——仰卧/死亡角色不注入
  chapter 级 dynamic_features 的「手持钓杆」（修复 hook kf 杨旭手持钓杆+起身根因）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make_chapter_with_props(tmp_path, prop_assets: dict):
    """写一个 script_result.json 到 tmp 目录并 patch chapter_dir 指向它."""
    (tmp_path / "script_result.json").write_text(
        json.dumps({"prop_assets": prop_assets}, ensure_ascii=False),
        encoding="utf-8",
    )
    return mock.patch("shared.paths.chapter_dir", return_value=tmp_path)


# ---------------------------------------------------------------------------
# Fix A：威胁武器入体方位兜底
# ---------------------------------------------------------------------------


def test_threat_weapon_in_chest_gets_body_position(tmp_path):
    """匕首插在杨旭胸口 → 产出【道具锚定】匕首在胸口（标准方位词/角色窗口都未命中时兜底）."""
    from shared.vimax_bridge import _extract_prop_layout

    prop_assets = {
        "刺客匕首": {"consistent_terms": ["匕首", "寒光"], "kind": "weapon"},
    }
    shot = {
        "ff_desc": "弦月当空，杨旭仰卧船头，素白轻袍，胸口赫然插着一把匕首，刀柄直竖，双目紧闭",
        "lf_desc": "杨旭仍仰卧不动，胸口匕首依旧",
        "motion_desc": "听香捂住嘴，泪水夺眶而出",
        "prompt": "夜刺场景",
        "main_subject": "夜刺",
    }
    # 角色名距匕首超 12 字窗口、无标准方位词 → 旧逻辑返回空
    characters = [{"identifier": "杨旭"}, {"identifier": "听香"}]
    role_scan = shot["ff_desc"] + shot["lf_desc"] + shot["motion_desc"]
    with _make_chapter_with_props(tmp_path, prop_assets):
        block = _extract_prop_layout(shot, characters, role_scan_text=role_scan)
    assert block
    assert "刺客匕首在胸口" in block


def test_non_weapon_prop_not_anchored_to_chest(tmp_path):
    """普通道具（酒杯）不触发入体方位兜底——防「酒杯在胸口」误锚."""
    from shared.vimax_bridge import _extract_prop_layout

    prop_assets = {
        "酒杯": {"consistent_terms": ["酒杯", "杯"], "kind": "prop"},
    }
    shot = {
        "ff_desc": "听香捂住胸口，桌上摆着酒杯",
        "lf_desc": "",
        "motion_desc": "",
        "prompt": "夜刺场景",
        "main_subject": "夜刺",
    }
    role_scan = shot["ff_desc"]
    with _make_chapter_with_props(tmp_path, prop_assets):
        block = _extract_prop_layout(shot, [{"identifier": "听香"}], role_scan_text=role_scan)
    # 酒杯有「桌上」标准方位词，命中也只报桌上；即使走兜底也不该锚到胸口
    if block:
        assert "胸口" not in block


# ---------------------------------------------------------------------------
# Fix B：惰性角色剥离手持物
# ---------------------------------------------------------------------------


def test_inert_char_strips_hand_object():
    """仰卧/双目紧闭的杨旭 → 不注入「手持钓杆」."""
    from shared.vimax_bridge import _inject_character_costume

    characters = [
        {
            "identifier": "杨旭",
            "static_features": "国字脸，剑眉星目，黑发束冠，身姿挺拔，弱冠之年",
            "dynamic_features": "素白色轻袍，头戴平定四方巾，赤脚，手持钓杆",
        },
    ]
    shot = {
        "ff_desc": "杨旭仰卧船头，胸口插着匕首，双目紧闭",
        "lf_desc": "杨旭仍仰卧不动，胸口匕首依旧",
    }
    kf_desc = "中近景构图：静态定格。目光凝望杨旭。镜头固定。"
    role_scan = shot["ff_desc"] + shot["lf_desc"] + kf_desc

    out = _inject_character_costume(kf_desc, characters, shot, role_scan_text=role_scan)

    assert "手持钓杆" not in out
    assert "素白色轻袍" in out


def test_active_char_keeps_hand_object():
    """站立/盘膝（非惰性）的杨旭 → 保留「手持钓杆」不被误剥."""
    from shared.vimax_bridge import _inject_character_costume

    characters = [
        {
            "identifier": "杨旭",
            "static_features": "国字脸，剑眉星目",
            "dynamic_features": "素白色轻袍，赤脚，手持钓杆",
        },
    ]
    shot = {
        "ff_desc": "杨旭盘膝坐于船头，持箫欲吹",
        "lf_desc": "杨旭吹箫，箫音悠扬",
    }
    kf_desc = "杨旭将箫凑近唇边"
    role_scan = shot["ff_desc"] + shot["lf_desc"] + kf_desc

    out = _inject_character_costume(kf_desc, characters, shot, role_scan_text=role_scan)

    assert "手持钓杆" in out
