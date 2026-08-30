"""A. InjuryContinuityConstraint 单元测试.

测试 3 类场景：
  1. 修复：build_idx 之后同 location 缺 anchor → 追加 _INJURY_ANCHOR_TAIL
  2. 幂等：连跑 2 次 = 第 2 次 0 改动
  3. 隔离：不同 location / 没 victim → 不追加
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_anchor_to_prompt_after_build_idx():
    """build_idx 含 anchor 词，后续同 location shot 缺 anchor → 应追加到 prompt."""
    from step01_script_gen.validation import _INJURY_ANCHOR_TAIL
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # shot 0: build_idx（"杨旭"被刺 + 心口洇痕）
    # shot 1: 同 location (小舟) + 杨旭倒下，缺 anchor → 应被修复
    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客举刀下刺",
            ff_desc="刀尖刺入杨旭心口",
            lf_desc="杨旭心口一片暗红洇痕",
            location="小舟",
            main_subject="杨旭",
            char_layout=[
                {"name": "杨旭", "role": "次"},
                {"name": "刺客", "role": "主"},
            ],
        ),
        make_shot(
            index=2,
            prompt="[中景] 杨旭倒在船头",
            lf_desc="杨旭仰卧",
            location="小舟",
            main_subject="杨旭",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint

    repairs = InjuryContinuityConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert _INJURY_ANCHOR_TAIL in script["shots"][1]["prompt"]
    # lf_desc 也应有 anchor
    assert _INJURY_ANCHOR_TAIL in script["shots"][1]["lf_desc"]


def test_appends_anchor_to_lf_desc_when_prompt_already_has_it():
    """prompt 已有 anchor，lf_desc 缺 anchor → 只追加到 lf_desc."""
    from step01_script_gen.validation import _INJURY_ANCHOR_TAIL
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客举刀下刺",
            ff_desc="刀尖刺入杨旭心口",
            lf_desc="杨旭心口洇痕",
            location="小舟",
            main_subject="杨旭",
            char_layout=[
                {"name": "杨旭", "role": "次"},
                {"name": "刺客", "role": "主"},
            ],
        ),
        make_shot(
            index=2,
            prompt="[中景] 杨旭倒在船头血痕清晰",  # prompt 已有 anchor
            lf_desc="杨旭仰卧",  # lf_desc 缺
            location="小舟",
            main_subject="杨旭",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint

    repairs = InjuryContinuityConstraint().apply(ctx)

    # prompt 不应有 _INJURY_ANCHOR_TAIL（已有 anchor）
    assert _INJURY_ANCHOR_TAIL not in script["shots"][1]["prompt"]
    # lf_desc 应有
    assert _INJURY_ANCHOR_TAIL in script["shots"][1]["lf_desc"]


def test_is_idempotent():
    """连跑 2 次 = 第 2 次 0 改动."""
    from step01_script_gen.validation import _INJURY_ANCHOR_TAIL
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="杨旭心口暗红洇痕",
            ff_desc="",
            lf_desc="",
            location="小舟",
            main_subject="杨旭",
            char_layout=[
                {"name": "杨旭", "role": "次"},
                {"name": "刺客", "role": "主"},
            ],
        ),
        make_shot(
            index=2,
            prompt="杨旭倒在船头",
            lf_desc="杨旭仰卧",
            location="小舟",
            main_subject="杨旭",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint

    c = InjuryContinuityConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) >= 1
    assert len(repairs2) == 0  # 幂等


def test_skips_different_location():
    """build_idx 之后 location 不同 → 不应被修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="杨旭心口洇痕",
            location="小舟",
            main_subject="杨旭",
            char_layout=[
                {"name": "杨旭", "role": "次"},
                {"name": "刺客", "role": "主"},
            ],
        ),
        make_shot(
            index=2,
            prompt="杨旭倒在岸边",  # location 不同
            location="岸边",
            main_subject="杨旭",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint

    repairs = InjuryContinuityConstraint().apply(ctx)

    assert len(repairs) == 0
    # 岸边 shot 不应被污染
    assert "白袍心口一片暗红血迹洇开" not in script["shots"][1]["prompt"]


def test_skips_when_victim_not_in_frame():
    """victim 不在画面里 → 不应被修复（否则空镜也补伤处）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="杨旭心口洇痕",
            location="小舟",
            main_subject="杨旭",
            char_layout=[
                {"name": "杨旭", "role": "次"},
                {"name": "刺客", "role": "主"},
            ],
        ),
        make_shot(
            index=2,
            prompt="[远景] 刺客遁水远去",  # 没 victim
            lf_desc="水面只剩涟漪",
            location="小舟",
            main_subject="刺客",
            char_layout=[{"name": "刺客", "role": "主"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint

    repairs = InjuryContinuityConstraint().apply(ctx)

    # shot 2 缺 victim → 不应被修复
    assert "白袍心口一片暗红血迹洇开" not in script["shots"][1]["prompt"]
    assert "白袍心口一片暗红血迹洇开" not in script["shots"][1]["lf_desc"]


def test_no_build_idx_no_repair():
    """全片无 anchor 词 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="夜宴喝酒", location="府邸", main_subject="杨旭"),
        make_shot(index=2, prompt="杨旭举杯", location="府邸", main_subject="杨旭"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint

    repairs = InjuryContinuityConstraint().apply(ctx)
    assert repairs == []


def test_victim_id_from_char_layout_role_secondary():
    """build_idx 缺 main_subject，victim 应能从 char_layout role=次 推出来."""
    from step01_script_gen.validation import _INJURY_ANCHOR_TAIL
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刀刺入",
            ff_desc="心口暗红洇痕",
            location="小舟",
            char_layout=[
                {"name": "杨旭", "role": "次"},
                {"name": "刺客", "role": "主"},
            ],
            # 无 main_subject，触发来源②
        ),
        make_shot(
            index=2,
            prompt="杨旭倒在船头",
            lf_desc="杨旭仰卧",
            location="小舟",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint

    repairs = InjuryContinuityConstraint().apply(ctx)

    # victim 应被识别为杨旭，shot 2 应被修复
    assert len(repairs) >= 1
    assert _INJURY_ANCHOR_TAIL in script["shots"][1]["prompt"] or _INJURY_ANCHOR_TAIL in script["shots"][1]["lf_desc"]


def test_constraint_attributes():
    """验证 Constraint 类属性正确."""
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint
    from step01_script_gen.constraints.types import Severity

    c = InjuryContinuityConstraint()
    assert c.name == "fatal_wound_continuity"
    assert c.severity == Severity.WARN
    assert c.repair_marker is not None


def test_collective_name_never_victim():
    """泛指名（群演等）不作受害者候选——16:34 run shot4 main_subject='群演'
    曾被采纳为 victim，shot6/7 伤锚延续漏补（实测）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.injury_continuity import (
        InjuryContinuityConstraint, _identify_victim,
    )
    from step01_script_gen.validation import _INJURY_ANCHOR_TAIL

    # build 镜 main_subject=群演 且 char_layout 群演在列 → 应跳过群演，
    # 回落到 role=次 的杨旭
    build = make_shot(
        index=1,
        prompt="特写匕首刺入，杨旭倒地，心口洇痕",
        main_subject="群演",
        char_layout=[
            {"name": "群演", "role": "次"},
            {"name": "杨旭", "role": "次"},
        ],
        location="小舟船头",
    )
    assert _identify_victim(build) == "杨旭"

    script = make_script([
        build,
        make_shot(index=2, prompt="杨旭（衣宽袍）倒在中景", lf_desc="杨旭仰卧不动",
                  ff_desc="杨旭（衣宽袍）倒在中景", location="小舟上，张十三质问"),
    ])
    ctx = make_ctx(script)
    repairs = InjuryContinuityConstraint().apply(ctx)
    assert len(repairs) >= 1
    assert _INJURY_ANCHOR_TAIL in script["shots"][1]["ff_desc"], (
        "location 2字头匹配（小舟船头↔小舟上）+ ff 纳入后应补 ff_desc"
    )


# ── v6.x.x：hook 刺杀前蓄势态不补伤锚（08-30 实抽 hook 污染根因） ──

def test_hook_pre_attack_state_skips_anchor():
    """hook 含 victim + 缺 anchor + 文本无血词/无 post-state 词 → 不补 INJURY_ANCHOR_TAIL.

    旧版只看"含 victim + 缺 anchor"就补，导致 hook.prompt 末尾被注入
    "伤处白袍心口一片暗红血迹洇开"——与杨旭"闭目吟哦/仰卧未觉"自相矛盾。
    修法：复用 BLOOD_STATE_WORDS + POST_STATE_KEYWORDS 反向判定。
    """
    from step01_script_gen.validation import _INJURY_ANCHOR_TAIL
    from tests.step01_script_gen.conftest import make_ctx, make_script

    # hook 是刺杀前蓄势瞬间：杨旭仰卧闭目 + 刺客破水待刺
    hook = {
        "index": 0,
        "prompt": "弦月当空，杨旭素白袍袒腹仰卧船头，仰面朝上双目紧闭，闭目吟哦。"
                  "神秘人湿衣伏于舟侧水下，仅露头部，一手持匕首于水面之下。",
        "ff_desc": "杨旭闭目吟哦，神秘人潜伏水下",
        "lf_desc": "",
    }
    script = make_script(
        shots=[],
        hook=hook,
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint

    repairs = InjuryContinuityConstraint().apply(ctx)

    # 关键断言：前态 hook → 0 修复（不补伤锚）
    assert len(repairs) == 0, f"前态 hook 不该补伤锚，实测 {len(repairs)} 修复"
    assert _INJURY_ANCHOR_TAIL not in hook["prompt"], \
        "前态 hook.prompt 末尾不该被注入 INJURY_ANCHOR_TAIL"


def test_hook_post_attack_state_still_appends_anchor():
    """hook 是刺杀后形态（含血词/含 post-state 词）→ 仍照常补 INJURY_ANCHOR_TAIL.

    修法不能误伤刺杀后 hook（之前依赖此 Constraint 补锚的镜不应丢功能）。
    """
    from step01_script_gen.validation import _INJURY_ANCHOR_TAIL
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 必须有 build_idx 来源 shot（否则 victim=None，整段无修复）
    # shot 0: build_idx（心口洇痕 + 杨旭倒下）
    # hook: 刺杀后态（刺客已没入 + 杨旭血迹）
    hook = {
        "index": 0,
        "prompt": "刺客已没入水中不见踪影。杨旭白衣心口洇开大片暗红，双目圆睁仰卧不动。",
        "ff_desc": "",
        "lf_desc": "刺客已没入水中，水面只剩涟漪",
    }
    script = make_script(
        shots=[
            make_shot(
                index=1,
                prompt="杨旭心口洇痕，仰面朝上",
                location="小舟",
                main_subject="杨旭",
                char_layout=[
                    {"name": "杨旭", "role": "次"},
                    {"name": "刺客", "role": "主"},
                ],
            ),
        ],
        hook=hook,
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.injury_continuity import InjuryContinuityConstraint

    repairs = InjuryContinuityConstraint().apply(ctx)

    # 后态 hook → 至少 1 处修复（prompt 或 lf_desc 补伤锚）
    assert len(repairs) >= 1, f"后态 hook 应照常补伤锚，实测 {len(repairs)} 修复"
    appended = (_INJURY_ANCHOR_TAIL in hook["prompt"]
                or _INJURY_ANCHOR_TAIL in hook["lf_desc"])
    assert appended, f"后态 hook 至少 prompt/lf_desc 之一应被追加 {_INJURY_ANCHOR_TAIL}"
