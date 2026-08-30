# -*- coding: utf-8 -*-
"""F10. FfLfTemporalConstraint 单元测试（v6.50，08-28）.

样本源自 08-26/08-28 hook 实拍图验证：ff 被写成刺杀结束态 → 首尾帧
无动作差 → 图生视频丢失「破水刺杀」。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot


def _hook_shot(ff_desc: str) -> dict:
    return make_shot(
        index=1,
        motion_desc="神秘人破水而出，右手匕首刺中杨旭心口，随即倒纵入水",
        ff_desc=ff_desc,
        lf_desc="神秘人已没入水中，杨旭仰卧船头心口暗红洇痕",
        main_subject="刺杀",
    )


def test_ff_post_state_flagged():
    """本镜有攻击 + ff 含「匕首依旧/洇痕扩大」→ 违例."""
    from step01_script_gen.constraints.ff_lf_temporal import FfLfTemporalConstraint

    script = make_script([
        _hook_shot("杨旭仍仰卧不动，胸口匕首依旧，白袍上暗红洇痕扩大"),
    ])
    ctx = make_ctx(script)
    repairs = FfLfTemporalConstraint().apply(ctx)

    assert len(repairs) == 1
    assert ctx.drain_violations(), "应记录违例"


def test_ff_pre_state_ok():
    """ff 是动作起始态（黑影蛰伏）→ 通过."""
    from step01_script_gen.constraints.ff_lf_temporal import FfLfTemporalConstraint

    script = make_script([
        _hook_shot("杨旭盘坐船头手握钓竿，画右水中一道黑影上浮，激起细微波纹"),
    ])
    ctx = make_ctx(script)
    repairs = FfLfTemporalConstraint().apply(ctx)

    assert repairs == []
    assert ctx.drain_violations() == []


def test_no_attack_motion_not_flagged():
    """本镜无攻击动作（如战后对峙镜）→ ff 含洇痕合法，不报."""
    from step01_script_gen.constraints.ff_lf_temporal import FfLfTemporalConstraint

    script = make_script([
        make_shot(
            index=2,
            motion_desc="张十三蹲在听香面前压低声音威胁，听香抽噎点头",
            ff_desc="杨旭仰卧船头，心口暗红洇痕清晰，听香跪坐画左",
            main_subject="对峙",
        ),
    ])
    ctx = make_ctx(script)
    repairs = FfLfTemporalConstraint().apply(ctx)

    assert repairs == []


# ── v6.x.x（08-30 实抽）：补 4 个「伤锚完整句」形态词 ──

def test_ff_injury_anchor_tail_full_sentence_flagged():
    """v6.x.x：ff 含 INJURY_ANCHOR_TAIL 完整句（伤处白袍+血迹洇开）→ 违例.

    08-30 实抽：hook.ff_desc 末尾被 Constraint 版 _INJURY_ANCHOR_TAIL 注入
    "伤处白袍心口一片暗红血迹洇开"，但旧 _POST_STATE_WORDS 只覆盖
    "洇痕扩大/已刺入/匕首依旧"等过渡态词，对"伤锚完整句"形态漏判。
    4 个新增词：「一片暗红血迹洇开」「血痕清晰可见」「伤处白袍」「暗红痕迹洇开」。
    """
    from step01_script_gen.constraints.ff_lf_temporal import FfLfTemporalConstraint
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # ff 文本 → 期望是否被 flag
    cases = [
        ("杨旭仰卧船头，伤处白袍心口一片暗红血迹洇开", True),   # 新增 1
        ("杨旭仰卧船头，心口洇开大片暗红痕迹洇开", True),       # 新增 2
        ("杨旭仰卧船头，心口血痕清晰可见", True),               # 新增 3
        ("杨旭仰卧船头，胸口已刺入，匕首依旧", True),           # 旧词回归
        ("杨旭仰卧船头手握钓竿，画右水中一道黑影上浮", False),  # 前态不报
    ]
    for ff, expect_flag in cases:
        shot = make_shot(
            index=1,
            motion_desc="神秘人破水而出，右手匕首刺中杨旭心口，随即倒纵入水",
            ff_desc=ff,
            lf_desc="神秘人已没入水中",
            main_subject="刺杀",
        )
        ctx = make_ctx(make_script([shot]))
        repairs = FfLfTemporalConstraint().apply(ctx)
        if expect_flag:
            assert len(repairs) == 1, f"ff={ff!r} 应判违例，实测 {len(repairs)} 修复"
        else:
            assert repairs == [], f"ff={ff!r} 不该报，实测 {len(repairs)} 修复"
