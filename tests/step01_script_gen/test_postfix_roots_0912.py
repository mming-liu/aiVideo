"""post_fix 根因修复行为锁定（2026-09-12）.

锁定 4 项确定性修复 + 回写陷阱重构后的不变量：
  1. motion「N-M三」分段残渣 → 「N-M秒：」
  2. prompt 句读终端不变量（截断残片 ≤30 字剔除）
  3. 打斗反馈按水体上下文选词（水下→水花，非水下→火星）
  4. 回写陷阱不变量：循环内任何修改立即写回 shot[]，无尾部批量回写
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _postfix(shots: list[dict]) -> list[dict]:
    from step01_script_gen.validation import post_fix_shot_prompts
    script = {"shots": shots, "hook": None, "characters": [], "scene_objects": {}}
    post_fix_shot_prompts(script)
    return script["shots"]


def test_motion_segment_residue_normalized():
    """「2-5三上前一步」→「2-5秒：上前一步」（分段派生全链的上游净化）."""
    shots = _postfix([{
        "index": 8, "location": "湾堤", "prompt": "张十三举灯呼喊",
        "motion_desc": "前2秒：张十三站在湾堤上；2-5三上前一步，开口呼喊",
    }])
    m = shots[0]["motion_desc"]
    assert "2-5三" not in m
    assert "2-5秒：上前一步" in m


def test_prompt_tail_garbage_known_signature_trimmed():
    """已知残片形态（氛围手指/截断参数）→ 剔除."""
    shots = _postfix([{
        "index": 1, "location": "小舟",
        "prompt": "张十三立在船头厉声喝止，冷蓝暗调。水面泛着银辉，氛围手指",
    }])
    p = shots[0]["prompt"]
    assert "氛围手指" not in p
    assert "水面泛着银辉" in p  # 残片之前的合法内容保留

    shots2 = _postfix([{
        "index": 2, "location": "小舟",
        "prompt": "杨旭仰卧船头。电影质感，高清1080P。淡入淡出转场时长0",
    }])
    assert "转场时长0" not in shots2[0]["prompt"]


def test_prompt_tail_legit_short_sentence_preserved():
    """P1 回归锁：合法短句不得被当残片误删（审查实测两组误删样本）."""
    shots = _postfix([{
        "index": 1, "location": "小舟",
        "prompt": "张十三站在船头。他看向远方",
    }])
    assert shots[0]["prompt"].endswith("他看向远方")

    shots2 = _postfix([{
        "index": 2, "location": "小舟",
        "prompt": "张三拔刀。刀光一闪",
    }])
    assert shots2[0]["prompt"].endswith("刀光一闪")


def test_none_prompt_normalized():
    """None prompt → 规范化为空串（无尾部批量回写可覆盖）."""
    shots = _postfix([{
        "index": 2, "location": "小舟", "prompt": None,
        "motion_desc": None,
    }])
    assert shots[0]["prompt"] == ""
    assert shots[0]["motion_desc"] == ""


def test_combat_fx_water_context_uses_splash():
    """水下打斗镜的打击反馈是水花系，不是火星（物理矛盾根修）."""
    shots = _postfix([{
        "index": 5, "location": "照月湾水下",
        "prompt": "神秘人贴在船底外侧，双手握短刃",
        "ff_desc": "神秘人黑衣蒙面，浑身是水，贴在船底外侧",
        "motion_desc": "前2秒：神秘人贴在船底外侧静止不动；2-5秒：双腿蹬水跃出，短刃刺向船板",
    }])
    m = shots[0]["motion_desc"]
    assert "火星" not in m
    assert "水花四溅" in m and "衣袂翻飞" in m


def test_combat_fx_land_context_keeps_sparks():
    """陆上打斗镜维持火星系（不误伤）."""
    shots = _postfix([{
        "index": 20, "location": "杨府别庄前厅",
        "prompt": "两人在前厅对刀，刀光相击",
        "motion_desc": "前2秒：两人对峙；2-5秒：挥刀砍向对方门户",
    }])
    m = shots[0]["motion_desc"]
    assert "火星四溅" in m


