"""F16. DialogueGroundingConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


SAMPLE_NOVEL = """
杨旭缓步走上船头，河面波光粼粼，两岸柳荫婆娑。
忽听身后有人说道：「公子，请留步。」
杨旭回身，见一黑衣男子立在船梢，面色冷峻，手握匕首。
「你是何人？」杨旭皱眉问道，手中折扇微动。
「张某，特来取公子性命。」黑衣人说罢，踏步上前。
杨旭大惊，向后急退。「你敢！」他厉声喝道。
寒光一闪，匕首已刺至胸前。杨旭闷哼一声，白袍瞬间洇出暗红。
刺客抽刀后撤，冷冷道：「对不住了，公子。」
杨旭捂住伤口，缓缓倒下，口中喃喃：「是你……」
船板上血痕蜿蜒，汇入河中，随波散去。
"""


def test_grounded_dialogue_no_change():
    """台词在原文中有出处 → 不修改."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint

    script = make_script([
        make_shot(
            index=1,
            main_subject="杨旭",
            narration_segment="杨旭：你是何人？",
            subtitle="杨旭：你是何人？",
            prompt="杨旭皱眉问道，手中折扇微动。",
            core_keywords=["杨旭", "折扇", "质问"],
        ),
    ])
    ctx = make_ctx(script, novel_text=SAMPLE_NOVEL)
    repairs = DialogueGroundingConstraint().apply(ctx)

    assert len(repairs) == 0
    assert script["shots"][0]["narration_segment"] == "杨旭：你是何人？"


def test_ungrounded_dialogue_replaced():
    """台词是编造的 → 用原文中最接近的台词替换."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint

    script = make_script([
        make_shot(
            index=1,
            main_subject="神秘人",
            narration_segment="神秘人：受死吧！",
            subtitle="神秘人：受死吧！",
            prompt="黑衣人抽出匕首，寒光一闪，刺向杨旭胸口。",
            core_keywords=["黑衣人", "匕首", "刺杀"],
        ),
    ])
    ctx = make_ctx(script, novel_text=SAMPLE_NOVEL)
    repairs = DialogueGroundingConstraint().apply(ctx)

    assert len(repairs) == 1
    new_narr = script["shots"][0]["narration_segment"]
    assert "神秘人：" in new_narr  # 角色名前缀保留
    assert "受死吧" not in new_narr  # 编造的内容被替换
    assert len(new_narr) > 5  # 替换成了有效台词
    # subtitle 同步更新
    assert script["shots"][0]["subtitle"] == new_narr


def test_no_novel_text_skips():
    """没有原文 → 不修改."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint

    script = make_script([
        make_shot(
            index=1,
            narration_segment="神秘人：受死吧！",
            subtitle="神秘人：受死吧！",
        ),
    ])
    ctx = make_ctx(script, novel_text="")
    repairs = DialogueGroundingConstraint().apply(ctx)
    assert len(repairs) == 0


def test_empty_dialogue_skipped():
    """空台词 → 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint

    script = make_script([
        make_shot(
            index=1,
            narration_segment="",
            subtitle="",
            prompt="空镜，河面波光粼粼。",
            core_keywords=["河面"],
        ),
    ])
    ctx = make_ctx(script, novel_text=SAMPLE_NOVEL)
    repairs = DialogueGroundingConstraint().apply(ctx)
    assert len(repairs) == 0


def test_no_candidate_clears_dialogue():
    """找不到合适的原文台词 → 清空（不编造）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint

    # 用一段完全没有引号台词的"原文"
    no_dialogue_novel = """
    杨旭走在船头。河面上很平静。风轻轻吹过。
    远处有山。近处有水。一切都很安静。
    """
    script = make_script([
        make_shot(
            index=1,
            narration_segment="杨旭：今天天气真好。",
            subtitle="杨旭：今天天气真好。",
            prompt="杨旭站在船头看风景。",
            core_keywords=["杨旭", "风景"],
        ),
    ])
    ctx = make_ctx(script, novel_text=no_dialogue_novel)
    repairs = DialogueGroundingConstraint().apply(ctx)

    assert len(repairs) == 1
    assert script["shots"][0]["narration_segment"] == ""
    assert script["shots"][0]["subtitle"] == ""


def test_constraint_attributes():
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint
    from step01_script_gen.constraints.types import Severity
    c = DialogueGroundingConstraint()
    assert c.name == "dialogue_grounding"
    assert c.severity == Severity.WARN


def test_idempotent_after_repair():
    """修复后再跑一次：已替换的台词在原文中有出处，不重复修."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint

    script = make_script([
        make_shot(
            index=1,
            main_subject="神秘人",
            narration_segment="神秘人：受死吧！",
            subtitle="神秘人：受死吧！",
            prompt="黑衣人踏步上前，匕首寒光闪烁。",
            core_keywords=["黑衣人", "匕首", "上前"],
        ),
    ])
    ctx = make_ctx(script, novel_text=SAMPLE_NOVEL)
    c = DialogueGroundingConstraint()
    r1 = c.apply(ctx)
    assert len(r1) == 1

    # 第二次运行：替换后的台词应该能在原文中找到出处（或已被清空）
    r2 = c.apply(ctx)
    # 要么 0 次（找到了出处），要么 0 次（已清空）
    assert len(r2) == 0, f"second pass should be idempotent, got {len(r2)} repairs"


def test_preserves_role_prefix():
    """替换后保留「角色名：」前缀."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint

    script = make_script([
        make_shot(
            index=1,
            main_subject="杨旭",
            narration_segment="杨旭：你这恶贼！",
            subtitle="杨旭：你这恶贼！",
            prompt="杨旭厉声喝道，目光如刀。",
            core_keywords=["杨旭", "厉声", "喝道"],
        ),
    ])
    ctx = make_ctx(script, novel_text=SAMPLE_NOVEL)
    DialogueGroundingConstraint().apply(ctx)

    new_narr = script["shots"][0]["narration_segment"]
    # 角色名前缀保留
    assert new_narr.startswith("杨旭：")


# ---------------------------------------------------------------------------
# v6.63.1 回归：真实小说用中文弯引号“”（锦衣夜行 31110 处 “”、0 处「」）。
# 旧引号正则缺 “” → 抽不到候选 → 失锚台词全被清空而非替换。
# ---------------------------------------------------------------------------

CURLY_NOVEL = """
杨旭缓步走上船头，河面波光粼粼。
忽听身后有人说道：“公子，请留步。”
黑衣男子立在船梢，面色冷峻，手握匕首。
“你是何人？”杨旭皱眉问道。
“张某，特来取公子性命。”黑衣人踏步上前。
"""


def test_curly_quote_novel_replaces_ungrounded():
    """弯引号原文：失锚台词应被替换为原文台词，而不是被清空."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint

    script = make_script([
        make_shot(
            index=1,
            main_subject="神秘人",
            narration_segment="神秘人：受死吧！",
            subtitle="神秘人：受死吧！",
            prompt="黑衣人抽出匕首，寒光一闪，刺向杨旭胸口。",
            core_keywords=["黑衣人", "匕首", "刺杀"],
        ),
    ])
    ctx = make_ctx(script, novel_text=CURLY_NOVEL)
    repairs = DialogueGroundingConstraint().apply(ctx)

    assert len(repairs) == 1
    new_narr = script["shots"][0]["narration_segment"]
    # 替换成了弯引号原文里的台词，而不是清空
    assert new_narr != "", "curly-quote novel should yield candidates, not clear"
    assert "受死吧" not in new_narr
    assert "神秘人：" in new_narr
    assert script["shots"][0]["subtitle"] == new_narr


def test_curly_quote_grounded_no_change():
    """弯引号原文：台词有出处（含全角冒号台词前缀）→ 不修改."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.dialogue_grounding import DialogueGroundingConstraint

    script = make_script([
        make_shot(
            index=1,
            main_subject="杨旭",
            narration_segment="杨旭：你是何人？",
            subtitle="杨旭：你是何人？",
            prompt="杨旭皱眉问道。",
            core_keywords=["杨旭", "质问"],
        ),
    ])
    ctx = make_ctx(script, novel_text=CURLY_NOVEL)
    repairs = DialogueGroundingConstraint().apply(ctx)

    assert len(repairs) == 0
    assert script["shots"][0]["narration_segment"] == "杨旭：你是何人？"


def test_speaker_matched_candidate_preferred():
    """v6.63.1：候选台词优先选说话人匹配的（原文「XX道：」标注）.

    缓解角色名错位：不再把张三的台词安进李四嘴里。
    （注：不走 constraint 集成路径——bad_line 的角色名本身会命中原文短语
    被 _locate_narration_in_novel 判为「有出处」，到不了替换环节。）
    """
    from step01_script_gen.constraints.dialogue_grounding import (
        _extract_dialogue_in_range,
        _pick_best_dialogue,
    )

    speaker_novel = (
        "杨旭站在船头。张十三道：“公子，船上只有你一人。”\n"
        "听香道：“公子离奇遇刺，我怎会不知。”\n"
        "黑衣人踏步上前，匕首寒光闪烁。\n"
    )
    # 说话人抽取：引号前「XX道：」识别
    extracted = _extract_dialogue_in_range(speaker_novel)
    speakers = {line: sp for line, sp in extracted}
    assert speakers.get("公子，船上只有你一人。") == "张十三"
    assert speakers.get("公子离奇遇刺，我怎会不知。") == "听香"

    # 选词：同说话人强优先——选张十三那句，而不是听香的
    candidates = list(speakers.items())
    best = _pick_best_dialogue(candidates, "张十三：受死吧！")
    assert best == "公子，船上只有你一人。"