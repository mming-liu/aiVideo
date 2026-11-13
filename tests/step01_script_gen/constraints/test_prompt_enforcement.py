"""test_prompt_enforcement.py — prompt 强化硬修复层单测."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from step01_script_gen.constraints.v6_43_prompt_enforcement import (
    V643PromptEnforcementConstraint,
    _needs_head_fix,
    _append_head_fix,
    _overlap_ratio,
)
# v6.43 重构：Rules 10/11/12 已迁移到 prompt_semantic 包。
# 旧 _fix_duplicate_anchor_phrase / _fix_prop_physical_position / _fix_assassin_underwater_contradiction
# 函数已删除。语义层测试见 tests/step01_script_gen/prompt_semantic/。


def _ctx(shots):
    class _Ctx:
        def __init__(self, shots):
            self.shots = shots
            self.hook = None
    return _Ctx(shots)


def _run(shots):
    return V643PromptEnforcementConstraint().apply(_ctx(shots))


def test_xiaozhou_to_zemeng_basic():
    shot = {"ff_desc": "夜色下，一叶小舟静浮水面", "prompt": "一叶小舟静泊"}
    _run([shot])
    assert "一叶小舟" in shot["ff_desc"]  # S02：无输入规格不替换
    assert "敞口木舟" not in shot["ff_desc"]
    assert "一叶小舟" in shot["prompt"]


def test_xiaozhou_bare_word():
    shot = {"ff_desc": "远处小舟隐约可见"}
    _run([shot])
    assert "小舟" in shot["ff_desc"]  # S02：无输入规格不替换
    assert "敞口木舟" not in shot["ff_desc"]


def test_xiaozhou_idempotent():
    shot = {"ff_desc": "一叶小舟", "prompt": "小舟静浮"}
    c = V643PromptEnforcementConstraint()
    c.apply(_ctx([shot]))
    ff1, p1 = shot["ff_desc"], shot["prompt"]
    c.apply(_ctx([shot]))
    assert shot["ff_desc"] == ff1
    assert shot["prompt"] == p1


def test_muzhou_preserved_without_a_spec():
    """S02：没有输入规格时保留原形制，不替换成「敞口木舟」。"""
    shot = {"ff_desc": "一叶木舟漂在河面"}
    _run([shot])
    assert "一叶木舟" in shot["ff_desc"]
    assert "敞口木舟" not in shot["ff_desc"]


def test_lying_needs_head_fix():
    text = "杨旭（衣宽袍）袒腹仰卧船头"
    assert _needs_head_fix(text) is True
    fixed = _append_head_fix(text)
    assert "仰面朝上双目紧闭" not in fixed
    assert "头颈僵直不侧转" not in fixed


def test_lying_with_head_fix_no_append():
    text = "杨旭仰卧船头，仰面朝上双目紧闭，头颈僵直不侧转"
    assert _needs_head_fix(text) is False


def test_hunmi_needs_head_fix():
    text = "杨旭昏迷在船板"
    assert _needs_head_fix(text) is True
    fixed = _append_head_fix(text)
    assert "仰面朝上双目紧闭" not in fixed


def test_bimu_no_append_when_no_lying_keyword():
    text = "杨旭盘膝坐于船头吹笛"
    assert _needs_head_fix(text) is False


def test_overlap_ratio_high_for_similar_text():
    a = "刺客潜伏水下，仅露双眼于水面，右手持匕首刀尖朝上"
    b = "刺客潜伏水下，仅露双眼于水面，右手持匕首刀尖朝上，蓄势待发"
    assert _overlap_ratio(a, b) > 0.7


def test_overlap_ratio_low_for_different_text():
    a = "夜色冷月，湖面静谧"
    b = "刺客破水而出，水花四溅，刀光一闪"
    assert _overlap_ratio(a, b) < 0.3


def test_kf_pre_high_overlap_is_reported_not_template_rewritten():
    original = "刺客潜伏水下，仅露双眼于水面，右手持匕首刀尖朝上，蓄势待发。杨旭袒腹仰卧船头，听香跪坐其侧。小舟静浮，荷花环绕。"
    shot = {
        "ff_desc": "刺客潜伏水下，仅露双眼于水面，右手持匕首刀尖朝上，蓄势待发。杨旭袒腹仰卧船头，听香跪坐其侧。",
        "lf_desc": "刺客已没入水中",
        "keyframes": [{"desc": original, "stage": "pre", "t_sec": 0.7}],
    }
    repairs = _run([shot])
    assert shot["keyframes"][0]["desc"] == original
    assert any(r.action == "warn" and "重叠" in r.detail for r in repairs)


def test_kf_pre_low_overlap_unchanged():
    original_kf = "中近景构图：刺客双眼微眯冷光一现，前景船舷木纹虚化，背景冷月。"
    shot = {
        "ff_desc": "刺客潜伏水下",
        "lf_desc": "",
        "keyframes": [{"desc": original_kf, "stage": "pre", "t_sec": 0.7}],
    }
    _run([shot])
    assert shot["keyframes"][0]["desc"] == original_kf


def test_kf_climax_not_rewritten():
    original_kf = "刺客破水而出，水花炸开，刀光一闪"
    shot = {
        "ff_desc": "刺客潜伏水下",
        "lf_desc": "",
        "keyframes": [{"desc": original_kf, "stage": "climax", "t_sec": 1.5}],
    }
    _run([shot])
    assert shot["keyframes"][0]["desc"] == original_kf


def test_kf_after_high_overlap_is_reported_not_template_rewritten():
    original = "刺客已没入水中，水面只剩涟漪，摇碎一湾冷月。杨旭胸口暗红。听香跪坐。"
    shot = {
        "ff_desc": "刺客潜伏水下",
        "lf_desc": "刺客已没入水中，水面只剩涟漪，摇碎一湾冷月。杨旭胸口暗红。",
        "keyframes": [{"desc": original, "stage": "after", "t_sec": 3.0}],
    }
    repairs = _run([shot])
    assert shot["keyframes"][0]["desc"] == original
    assert any(r.action == "warn" and "重叠" in r.detail for r in repairs)


def test_tingxiang_qice_to_chuanwei():
    # Rule 4 改用 scene_anchors，需要传 location + char_layout
    shot = {
        "ff_desc": "杨旭仰卧船头，听香跪坐其侧，目光凝望杨旭",
        "location": "舴艋舟",
        "char_layout": [
            {"name": "杨旭", "position": "船头"},
            {"name": "听香", "position": "船尾"},
        ],
    }
    _run([shot])
    assert "其侧" not in shot["ff_desc"]
    # 船场景下，听香应被锚到船尾（anchors[0]=船头, anchors[-1]=船尾）
    assert "听香立于船尾" in shot["ff_desc"]


def test_tingxiang_shenbian_to_chuanwei():
    shot = {
        "ff_desc": "杨旭吹箫，听香立身旁撑伞",
        "location": "舴艋舟",
        "char_layout": [
            {"name": "杨旭", "position": "船头"},
            {"name": "听香", "position": "船尾"},
        ],
    }
    _run([shot])
    assert "听香立于船尾" in shot["ff_desc"]


def test_integration_all_four_rules():
    shot = {
        "ff_desc": "夜色下，一叶小舟静浮水面，杨旭（衣宽袍）袒腹仰卧船头，听香跪坐其侧",
        "lf_desc": "刺客已没入水中",
        "prompt": "夜色下，一叶小舟静浮水面",
        "motion_desc": "0-1秒：水面微澜",
        "location": "舴艋舟",
        "char_layout": [
            {"name": "杨旭", "position": "船头"},
            {"name": "听香", "position": "船尾"},
        ],
        "keyframes": [
            {
                "desc": "夜色下，一叶小舟静浮水面，刺客潜伏水下，仅一双冷厉眼睛露出水面，听香跪坐其侧。",
                "stage": "pre",
                "t_sec": 0.7,
            }
        ],
    }
    kf_original = shot["keyframes"][0]["desc"]
    repairs = _run([shot])
    assert "一叶小舟" in shot["ff_desc"]  # S02：无输入规格不替换
    assert "敞口木舟" not in shot["ff_desc"]
    assert "仰面朝上双目紧闭" not in shot["ff_desc"]
    assert "听香立于船尾" in shot["ff_desc"]
    assert shot["keyframes"][0]["desc"] == kf_original
    assert any(r.action == "warn" for r in repairs)


def test_works_on_hook_via_enforce_all():
    # _enforce_all 新签名需要 char_names + anchors
    c = V643PromptEnforcementConstraint()
    fixed = c._enforce_all(
        "夜色下，一叶小舟静浮水面，杨旭（衣宽袍）袒腹仰卧船头",
        {"ff_desc": "", "lf_desc": "刺客已没入水中"},
        "ff_desc",
        ["杨旭"],  # char_names
        ("船头", "船中", "船尾"),  # anchors（船场景）
    )
    # S02 复核（2026-11-13）：没有输入规格 → 保留原形制，不替换成「敞口木舟」。
    assert "一叶小舟" in fixed
    assert "敞口木舟" not in fixed
    assert "仰面朝上双目紧闭" not in fixed


# ============================================================================


# 集成测试：真实钩子 ff_desc 三类问题一齐修
# ============================================================================

def test_integration_real_hook_fixes_all_three():
    """08-24 实测钩子：酒壶漂浮 + 钓杆悬空 + 刺客头顶匕首 + 露出水面过多.

    v6.43 重构：3 类问题改由 prompt_semantic.PromptSemanticNormalizer 处理。
    本测试只验证 v6_43_prompt_enforcement 仍能调用并修改字段。
    详细的语义测试见 tests/step01_script_gen/prompt_semantic/。
    """
    text = (
        "夜色下，一叶无篷舴艋舟（宽1.4米，可容3人）静浮荷花荡中，"
        "杨旭袒腹仰卧船头仰面朝上双目紧闭，双手空着自然搭在腹上，"
        "酒壶已倾倒脱手，挂在船舷画左边缘，"
        "钓杆已脱手滑落，斜伸出画右船舷身体前倾，即将没入水面，"
        "听香跪坐画左船梢。"
        "画右船舷下方水面，刺客仅头顶约 2cm 露出水面（仅露双眼，不是上半身出水），"
        "刀尖（独立物体）位于刺客头顶正上方约 1cm。"
    )
    shot = {
        "ff_desc": text, "prompt": "", "lf_desc": "", "motion_desc": "",
        "location": "舴艋舟",
        "char_layout": [
            {"name": "杨旭", "position": "船头"},
            {"name": "听香", "position": "船梢"},
            {"name": "刺客", "position": "水面下"},
        ],
        "keyframes": [],
    }
    # 钩子场景：shot 经 ctx.hook 路径，is_hook=True
    class _HookCtx:
        def __init__(self, hook):
            self.shots = []
            self.hook = hook
    n = V643PromptEnforcementConstraint().apply(_HookCtx(shot))
    # v6.43 重构后：每字段 1 次 find/replace 算 1 fix。ff_desc 触发 3 类问题 = 1 fix。
    assert len(n) >= 1
    # Issue 2（酒壶+钓杆）+ Issue 3（刺客+刀尖+头顶+露出水面）已修复
    assert "挂在船舷" not in shot["ff_desc"]
    assert "斜伸出" not in shot["ff_desc"]
    assert "倾倒" in shot["ff_desc"]  # 酒壶/钓杆
    assert "斜倚" in shot["ff_desc"]  # 钓杆
    # Issue 3
    assert "刺客完全潜伏水面下" in shot["ff_desc"]
    assert "头顶正上方" not in shot["ff_desc"]
    # Issue 1: 听香 anchor 块不重复（虽然此例没有 anchor 块，但确认未引入新问题）
    # 听香仅 1 次出现
    assert shot["ff_desc"].count("听香") == 1


# ============================================================================
# v6.50（08-28）：仰卧头部固定追加到「仰卧」主语子句（不再追加到整段末尾）
# ============================================================================

def test_head_fix_attaches_to_lying_subject_clause():
    """多主语句：不从仰卧与伤情推断任何人物的眼态."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            ff_desc="听香跪坐画左，面向杨旭，杨旭仰卧船头，左手钓竿垂入水面，心口暗红洇痕",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.v6_43_prompt_enforcement import V643PromptEnforcementConstraint

    V643PromptEnforcementConstraint().apply(ctx)

    ff = script["shots"][0]["ff_desc"]
    # 原有姿态与持物关系保持，不追加闭眼/僵直
    assert "杨旭仰卧船头，左手钓竿" in ff
    assert "双目紧闭" not in ff
    assert not ff.endswith("头颈僵直不侧转")


def test_head_fix_skips_when_lying_clause_has_eye_open_words():
    """仰卧子句含「双眼圆睁」→ 不追加（避免「双目紧闭」紧邻矛盾），留给修复轮."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    original = "神秘人破水而出；杨旭袒腹仰卧船头，双眼圆睁；听香在画左张嘴欲呼"
    script = make_script([make_shot(index=1, ff_desc=original)])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.v6_43_prompt_enforcement import V643PromptEnforcementConstraint

    V643PromptEnforcementConstraint().apply(ctx)

    assert script["shots"][0]["ff_desc"] == original


def test_head_fix_mid_sentence_boundary():
    """仰卧在句中、句号结尾：原文保持，不补眼态."""
    from step01_script_gen.constraints.v6_43_prompt_enforcement import _append_head_fix, _needs_head_fix

    text = "听香在画左张嘴欲呼。杨旭袒腹仰卧于船头。水面平静"
    assert _needs_head_fix(text)
    out = _append_head_fix(text)
    assert out == text


# ============================================================================
# v6.50（08-29）：shot13.5 变体——lying 词表收窄 + 否定/直立双守卫
# ============================================================================

def test_head_fix_no_trigger_on_broad_words_removed():
    """「失神」已从 LYING 词表移除——俯首者不触发头固定."""
    from step01_script_gen.constraints.v6_43_prompt_enforcement import _needs_head_fix

    assert _needs_head_fix("听香捂着脸含泪俯首，目光凝望张十三，不敢仰视，失神。") is False


def test_head_fix_skips_upright_conflict_sentence():
    """匹配所在句含直立词（俯首）→ 不追加（宁可漏加不误拼）."""
    from step01_script_gen.constraints.v6_43_prompt_enforcement import _append_head_fix, _needs_head_fix

    text = "听香捂着脸含泪俯首，吓得几乎昏迷。"
    assert _needs_head_fix(text)
    assert _append_head_fix(text) == text


def test_head_fix_skips_negation_prefix():
    """「并未昏迷」→ 不追加."""
    from step01_script_gen.constraints.v6_43_prompt_enforcement import _append_head_fix, _needs_head_fix

    text = "杨旭靠在船舷，并未昏迷，呼吸平稳"
    assert _needs_head_fix(text)
    assert _append_head_fix(text) == text


def test_head_fix_multi_sentence_targets_second_match():
    """两个人物的姿态都保持，不为任何一句补造眼态."""
    from step01_script_gen.constraints.v6_43_prompt_enforcement import _append_head_fix

    out = _append_head_fix("听香捂脸俯首，吓得几乎昏迷。杨旭仰卧船头，钓竿垂入水面")
    assert "吓得几乎昏迷，仰面朝上" not in out
    assert out == "听香捂脸俯首，吓得几乎昏迷。杨旭仰卧船头，钓竿垂入水面"


# ---------------------------------------------------------------------------
# v6.62.9（09-10）守卫 3 两处漏判 —— run5 实测 shot6 落盘自相矛盾：
# 「杨旭仍仰卧船头，仰面朝上双目紧闭，头颈僵直不侧转，…，双眼半阖，望向画上方夜空」
#   ① 定长窗 m.end()+12 看不到 20+ 字外的睁眼态
#   ② _EYE_OPEN_WORDS 只收「圆睁/瞪」，漏「半阖」这类半睁态
# ---------------------------------------------------------------------------

def test_half_open_eyes_late_in_sentence_blocks_injection():
    """睁眼态写在同句靠后位置（超出旧定长窗）也必须拦住头部固定注入."""
    from step01_script_gen.constraints.v6_43_prompt_enforcement import _append_head_fix
    text = "杨旭仍仰卧船头，酒壶垂在身侧，壶口朝下，酒液洒在船板上，双眼半阖，望向画上方夜空"
    assert _append_head_fix(text) == text
    assert "双目紧闭" not in _append_head_fix(text)


def test_partial_open_eye_words_are_guarded():
    """半睁/微睁/眼睑微张 与「双目紧闭」冲突，同样要拦."""
    from step01_script_gen.constraints.v6_43_prompt_enforcement import _append_head_fix
    for eye in ("双眼半阖", "双眼半睁", "双目微睁", "眼睑微张"):
        text = f"杨旭仰卧船头，{eye}，望向夜空"
        assert _append_head_fix(text) == text, eye


def test_injury_line_does_not_determine_eye_or_neck_state():
    """伤情与仰卧不能证明闭眼或头颈僵直，原文保持合法."""
    from step01_script_gen.constraints.v6_43_prompt_enforcement import _append_head_fix
    out = _append_head_fix("杨旭仰卧船头，素白轻袍心口一片暗红")
    assert out == "杨旭仰卧船头，素白轻袍心口一片暗红"


def test_other_subject_eye_state_does_not_authorize_injection():
    """另一个人物的眼态不能作为本句人物闭眼的证据."""
    from step01_script_gen.constraints.v6_43_prompt_enforcement import _append_head_fix
    out = _append_head_fix("杨旭仰卧船头，心口暗红洇痕。听香双眼圆睁，扑到船边")
    assert "仰面朝上双目紧闭" not in out


# ============================================================================
# v6.73: head-state context gate + per-field boat spec dedup
# ============================================================================

def test_enforce_all_skips_head_fix_for_living_reclining():
    c = V643PromptEnforcementConstraint()
    shot = {"prompt": "杨旭仰卧船头，手执玉箫，仰望夜空"}
    out = c._enforce_all(
        shot["prompt"], shot, "prompt", ["杨旭"], (),
    )
    assert "双目紧闭" not in out
    assert "头颈僵直" not in out


def test_enforce_all_preserves_injury_without_guessing_head_state():
    c = V643PromptEnforcementConstraint()
    shot = {"lf_desc": "杨旭仰卧船头，心口暗红洇痕"}
    out = c._enforce_all(
        shot["lf_desc"], shot, "lf_desc", ["杨旭"], (),
    )
    assert "仰面朝上双目紧闭" not in out
    assert "头颈僵直不侧转" not in out


def test_boat_spec_injected_only_once_per_field():
    shot = {"ff_desc": "远处小舟隐约可见，近处小舟靠岸"}
    _run([shot])
    assert shot["ff_desc"].count("（宽1.4米") == 0  # S02：无来源不写宽度


def test_enforce_all_removes_invalid_head_state_from_living_reclining():
    c = V643PromptEnforcementConstraint()
    text = "杨旭仰卧船头，仰面朝上双目紧闭，头颈僵直不侧转，手执玉箫"
    shot = {"prompt": text}
    out = c._enforce_all(text, shot, "prompt", ["杨旭"], ())
    assert "双目紧闭" not in out
    assert "头颈僵直" not in out
    assert "手执玉箫" in out


def test_enforce_all_removes_closed_eye_on_open_eye_conflict():
    c = V643PromptEnforcementConstraint()
    text = "杨旭仰卧船头，双目圆睁，仰面朝上双目紧闭，头颈僵直不侧转，心口暗红洇痕"
    shot = {"prompt": text, "lf_desc": "心口暗红洇痕"}
    out = c._enforce_all(text, shot, "prompt", ["杨旭"], ())
    assert "仰面朝上双目紧闭" not in out
    assert "双目圆睁" in out
