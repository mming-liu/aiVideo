# -*- coding: utf-8 -*-
"""v6.52 帧图修复单元测试：参考图防泄漏 / 前景体型锚 / 船宽矛盾 / 人数锚.

hook ff 实测三问题：前景神秘人巨人化、船尾多出第 4 个蒙面人（参考图全身
站姿立绘照抄）、船宽 prompt 自相矛盾（desc 内嵌 1.4米 vs 注入 1.6米）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PIL import Image

from shared.portrait_stitch import crop_head_portrait, _HEAD_BOX
from shared.ref_compose import _SHEET_LABELS, describe_ref
from shared.vimax_bridge import _extract_char_layout, _inject_object_size_anchor


# ── B. 前景体型锚（per-character）───────────────────────────────────

_CHARS = [{"identifier": "杨旭"}, {"identifier": "听香"}, {"identifier": "刺客"}]


def _hook_shot() -> dict:
    return {
        "ff_desc": "神秘人半身探出水面直指杨旭，听香在旁惊愕",
        "char_layout": [
            {"name": "刺客", "position": "画左前景", "surface": "船头水面交界处",
             "facing": "面朝画右的杨旭"},
            {"name": "杨旭", "position": "画右", "surface": "船头甲板", "facing": "仰卧"},
            {"name": "听香", "position": "画右中景", "surface": "中舱边缘", "facing": "面向船头"},
        ],
    }


def test_scale_anchor_foreground_entry_only():
    """前景条目带体型锚，中景条目不带（per-character 精确性）."""
    shot = _hook_shot()
    block = _extract_char_layout(shot, _CHARS, role_scan_text=shot["ff_desc"])
    assert "非巨人" in block
    i_k = block.find("刺客")
    i_y = block.find("杨旭")
    seg_k = block[i_k:i_y if i_y > i_k else len(block)]
    assert "体型与画面其他人物相当" in seg_k
    i_t = block.find("听香")
    seg_t = block[i_t:i_t + 60] if i_t >= 0 else ""
    assert "体型" not in seg_t


def test_scale_anchor_dynamic_verb_not_in_pose_table():
    """跃上/扑出/破水不在 _KF_POSE_STATES，但前景+这些动词必须触发体型锚."""
    shot = {
        "ff_desc": "张十三跃上小舟，俯身看向杨旭",
        "char_layout": [
            {"name": "张十三", "position": "画右前景", "surface": "船舷", "facing": "俯身"},
        ],
    }
    block = _extract_char_layout(
        shot, [{"identifier": "张十三"}, {"identifier": "杨旭"}],
        role_scan_text=shot["ff_desc"])
    assert "非巨人" in block


def test_scale_anchor_not_on_midshot():
    """无前景/无动态词 → 不注入体型锚（不污染常规镜头）."""
    shot = {
        "ff_desc": "听香站在船中",
        "char_layout": [{"name": "听香", "position": "画右中景", "facing": "面向画左"}],
    }
    block = _extract_char_layout(
        shot, [{"identifier": "听香"}], role_scan_text=shot["ff_desc"])
    assert "非巨人" not in block


def test_scale_anchor_idempotent():
    """同输入两次结果一致."""
    shot = _hook_shot()
    b1 = _extract_char_layout(shot, _CHARS, role_scan_text=shot["ff_desc"])
    b2 = _extract_char_layout(shot, _CHARS, role_scan_text=shot["ff_desc"])
    assert b1 == b2


# ── C1. 内嵌尺寸剥离 + 船宽相对锚 ──────────────────────────────────

def test_embedded_size_strip_both_formats():
    """两种实测内嵌格式整段剥离：「（宽1.4米，可容3人）」「（长6米宽1.4米无篷舴艋舟）」."""
    import re
    strip = lambda s: re.sub(r"[（(][^（）()]*\d+(?:\.\d+)?米[^（）()]*[）)]", "", s)
    t1 = strip("杨旭躺船上，无篷舴艋舟（宽1.4米，可容3人）停在水面")
    t2 = strip("（长6米宽1.4米无篷舴艋舟）船头，特写匕首")
    assert "1.4米" not in t1 and "可容3人" not in t1
    assert "6米" not in t2 and "1.4米" not in t2
    assert "杨旭躺船上" in t1 and "停在水面" in t1


def test_embedded_size_strip_idempotent():
    """同文本跑两次结果一致."""
    import re
    strip = lambda s: re.sub(r"[（(][^（）()]*\d+(?:\.\d+)?米[^（）()]*[）)]", "", s)
    t = "舴艋舟（宽1.4米，可容3人）靠岸"
    assert strip(strip(t)) == strip(t)


def test_boat_relative_width_anchor(tmp_path, monkeypatch):
    """category=船 的物体注入相对宽度锚（肩宽三倍），不依赖绝对米数."""
    import json
    chapter = tmp_path / "chapter_x"
    chapter.mkdir()
    script = {"scene_objects": {"照月湾小舟": {
        "category": "船", "subtype": "窄身无篷舴艋舟",
        "length_m": 7.0, "width_m": 1.6}}}
    (chapter / "script_result.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8")
    # 兼容带参调用（script_file() 会以 chapter_dir(chapter) 形式间接调用）
    monkeypatch.setattr("shared.paths.chapter_dir", lambda *a, **k: chapter)
    shot = {"location": "小舟上", "ff_desc": "杨旭仰卧船头"}
    out = _inject_object_size_anchor(shot, "杨旭仰卧船头")
    assert "窄身无篷舴艋舟" in out
    assert "肩宽的三倍" in out
    assert "船舷两侧仍有空档" in out


# ── A1/A3. 肖像裁切 + 参考表标签 ───────────────────────────────────

def test_crop_head_portrait_window_and_aspect():
    """裁切窗口 = _HEAD_BOX；crop 不拉伸（像素宽高比 = 源图对应区域比例）."""
    img = Image.new("RGB", (1338, 768), (180, 180, 180))
    c = crop_head_portrait(img)
    l, t, r, b = _HEAD_BOX
    expect = (int(1338 * r) - int(1338 * l), int(768 * b) - int(768 * t))
    assert c.size == expect
    # crop 语义保证不变形：裁后像素比 == 源图该区域像素比（int 取整容差 <1%）
    src_ratio = img.width * (r - l) / (img.height * (b - t))
    out_ratio = c.width / c.height
    assert abs(src_ratio - out_ratio) < 0.01


def test_chars_sheet_label_semantics():
    """chars 标签：保留项（面部/发型/上装）与禁止项（站姿/持物/人数）并存."""
    label = _SHEET_LABELS["chars"]
    for keep in ("面部", "发型", "上装"):
        assert keep in label, f"缺少保留项 {keep}"
    for ban in ("站姿", "持物", "人数", "以文字描述为准"):
        assert ban in label, f"缺少禁止项 {ban}"


def test_describe_ref_recognizes_crop_cache():
    """portrait_crops 裁像缓存有专属用途标签（拼图与单图路径一致）."""
    p = str(Path("vimax_working") / "portrait_crops" / "刺客_front.png")
    label = describe_ref(p)
    assert "头肩" in label and "禁止" in label


# ── D. 人数锚两层补强 ──────────────────────────────────────────────

def test_count_anchor_reuse_ban_when_threat_visible():
    """威胁可见：人数限制含「复制/复用」禁令（第 4 蒙面人=别名复用漏网）."""
    from style_prefix import add_image_strict_constraints
    shot = {
        "location": "小舟上",
        "ff_desc": "神秘人半身探出水面直指杨旭，听香在旁惊愕",
        "lf_desc": "", "motion_desc": "",
        "char_layout": _hook_shot()["char_layout"],
    }
    out = add_image_strict_constraints("神秘人半身探出水面", "首帧", shot=shot,
                                       characters=_CHARS)
    assert "复制/复用" in out and "只出现一次" in out


def test_count_anchor_invisible_threat_shows_entity_count():
    """威胁仅黑影：人数只数实体角色，黑影不计入且防「第二个黑影」.

    2026-08-29：措辞从「禁止…第二个黑影、第二个{名单}」改为「黑影只允许出现一个；
    禁止出现任何其他人物或第二名{名单}角色」——把「黑影」从禁止从句移出，
    避免命中 `禁止[^\n。]{0,24}?(黑影|人影)` 自检器（与剧情伏笔「水下黑影轮廓」自触）。
    """
    from style_prefix import add_image_strict_constraints
    shot = {
        "location": "小舟上",
        # 含威胁名（has_threat）但黑影/水下非实体词压过可见性 → invisible 分支
        "ff_desc": "神秘人化作水下黑影一闪而过，杨旭仰卧未觉，听香在旁未察",
        "lf_desc": "", "motion_desc": "",
    }
    out = add_image_strict_constraints("杨旭仰卧，水下黑影", "首帧", shot=shot,
                                       characters=_CHARS)
    assert "不计入实体人数" in out
    # 新措辞：正向陈述「黑影只允许出现一个」+ 禁止「第二名{名单}角色」
    assert "黑影只允许出现一个" in out
    # 「第二名杨旭、听香角色」是连续子串；分别断言前 2 字 + 完整名单
    assert "第二名杨旭" in out and "听香角色" in out
    # 旧措辞「第二个黑影、第二个{名单}」必须废除（避免自触）
    assert "第二个黑影" not in out


def test_invisible_threat_wording_no_self_contradiction():
    """2026-08-29：新增 — 新措辞代入最终 prompt 跑矛盾自检器，必须 0 报矛盾.

    旧措辞「禁止…第二个黑影」+ 剧情「水下黑影轮廓」会触发对称矛盾
    （禁止[^\n。]{0,24}?(黑影|人影) 命中 24 字内的「黑影」），导致 shot
    fail-fast 整条链路崩。新措辞把「黑影」从禁止从句移出，正向陈述防第二黑影。

    走 invisible_only 分支：ff_desc 只让威胁以"水下黑影"形式出现（无完整
    刺客站姿/可见实体特征），触发 _threat_invisible_only=True → 应用新措辞。
    """
    from style_prefix import add_image_strict_constraints
    from shared.prompt_contradiction import find_contradictions
    shot = {
        "location": "小舟上",
        "ff_desc": "神秘人化作水下黑影一闪而过，杨旭仰卧未觉，听香在旁未察",
        "lf_desc": "", "motion_desc": "",
    }
    base = "杨旭仰卧，水下黑影，"
    tail = add_image_strict_constraints(base, "首帧", shot=shot, characters=_CHARS)
    full = base + tail
    issues = find_contradictions(full)
    # 必须不报「对称矛盾：…禁止条款又禁掉它」
    assert not any("对称矛盾" in i for i in issues), \
        f"新措辞不应触发黑影自触矛盾，实测 issues={issues}"


def test_hard_count_anchor_from_char_layout():
    """char_layout ≥3 条 → 人数硬锚带名单与清点指令（防线2）."""
    from style_prefix import add_image_strict_constraints
    shot = _hook_shot()
    out = add_image_strict_constraints("神秘人半身探出水面", "首帧", shot=shot,
                                       characters=_CHARS)
    assert "【画面人数硬锚】" in out and "逐个清点" in out


# ── 防线1. QC 人数断言 ─────────────────────────────────────────────

def test_qc_prompt_person_count_assertion():
    """QC prompt 含具体人数断言（名单 N 人、多于 N+tolerance → critical）.

    v6.53 A.0：容差默认 0（try1 的 4 人图 vs 名单 3 人曾因 +1 容差漏放）.
    """
    from shared.image_qc import build_qc_prompt
    shot = {"scene": "照月湾", "main_subject": "神秘人",
            "ff_desc": "神秘人半身探出水面", "char_layout": _hook_shot()["char_layout"]}
    prompt = build_qc_prompt(shot, "first_frame", _CHARS)
    assert "extra_person" in prompt
    assert "多于 3" in prompt  # 3 人名单 + 容差 0
    assert "critical" in prompt


def test_qc_extra_person_category_in_whitelist():
    """extra_person 在类别白名单，模型返回可归一化不被丢弃."""
    from shared.image_qc import _map_category, _VALID_CATEGORIES
    assert "extra_person" in _VALID_CATEGORIES
    assert _map_category("extra_person") == "extra_person"
