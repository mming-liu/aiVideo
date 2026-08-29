# -*- coding: utf-8 -*-
"""prompt_repetition 悬空角色名碎片清理（v6.50 追加）测试."""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot


def test_strips_dangling_char_name_fragment():
    """「云鬓半偏，杨旭；」悬空角色名碎片 → 清除（08-28 hook ff 实测残留）."""
    script = make_script([
        make_shot(
            index=1,
            ff_desc="月光下杨旭袒腹仰卧船头，神情怡然；听香跪坐船梢云鬓半偏，杨旭；画右水面微澜",
            char_layout=[{"name": "杨旭", "position": "船头", "facing": "面向画右"},
                         {"name": "听香", "position": "船梢", "facing": "面向杨旭"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint

    PromptRepetitionConstraint().apply(ctx)

    out = script["shots"][0]["ff_desc"]
    assert "，杨旭；" not in out
    assert "画右水面微澜" in out
    # 句首/句中主语位置的名字不动
    assert "杨旭袒腹仰卧船头" in out


def test_normal_name_usage_not_stripped():
    """正常句式（名字后有谓语/作主语）不受影响."""
    script = make_script([
        make_shot(
            index=1,
            prompt="张十三站在岸边，手指向画左水边，张十三面色凝重",
            char_layout=[{"name": "张十三", "position": "岸边", "facing": "面向水面"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.prompt_repetition import PromptRepetitionConstraint

    PromptRepetitionConstraint().apply(ctx)

    out = script["shots"][0]["prompt"]
    assert out.count("张十三") == 2
