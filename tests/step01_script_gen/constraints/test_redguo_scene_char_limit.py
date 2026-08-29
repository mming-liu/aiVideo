"""F5. RedguoSceneCharLimitConstraint 单元测试（v6.49, 08-28）.

08-28 chapter_001 跑批实测 2 处红果超限：场景 6>3 + 人物 5>4.
修法：场景合并到前 3 高频 + 配角 alias 为群演.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_scene_merge_to_top_3():
    """6 场景 > 3 → 后 3 个 alias 到 top 1."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, scene="照月湾", location="照月湾", main_subject="杨旭"),
        make_shot(index=2, duration_sec=6.0, scene="杨府别庄", location="杨府别庄", main_subject="张十三"),
        make_shot(index=3, duration_sec=6.0, scene="照月湾", location="照月湾", main_subject="杨旭"),
        make_shot(index=4, duration_sec=6.0, scene="小舟", location="小舟", main_subject="刺客"),
        make_shot(index=5, duration_sec=6.0, scene="岸边", location="岸边", main_subject="老汉"),
        make_shot(index=6, duration_sec=6.0, scene="杨府大门", location="杨府大门", main_subject="张十三"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.redguo_scene_char_limit import RedguoSceneCharLimitConstraint

    repairs = RedguoSceneCharLimitConstraint().apply(ctx)

    # 至少 2 处修复（top 3 之外的场景被 alias；Counter.most_common 留 3 个，
    # 多 tie 时保留前 2 个高频 + 第 3 个 tie-break 留下的，余下 drop）
    assert len(repairs) >= 2
    # 全部 shot scene 应该是 top 3 之一
    final_scenes = {s.get("scene") for s in script["shots"]}
    assert len(final_scenes) <= 3, f"final scenes {final_scenes} > 3"


def test_char_alias_to_extras():
    """5 人物 > 4 → 第 5 个 alias 为群演/船夫/村人."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, scene="照月湾", location="照月湾",
                  main_subject="杨旭", char_layout=[{"name": "杨旭"}, {"name": "老汉"}]),
        make_shot(index=2, duration_sec=6.0, scene="照月湾", location="照月湾",
                  main_subject="听香", char_layout=[{"name": "听香"}]),
        make_shot(index=3, duration_sec=6.0, scene="照月湾", location="照月湾",
                  main_subject="刺客", char_layout=[{"name": "刺客"}]),
        make_shot(index=4, duration_sec=6.0, scene="杨府别庄", location="杨府别庄",
                  main_subject="张十三", char_layout=[{"name": "张十三"}]),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.redguo_scene_char_limit import RedguoSceneCharLimitConstraint

    RedguoSceneCharLimitConstraint().apply(ctx)

    # 老汉 应被 alias 为 村人/船夫/群演
    char_layout_1 = script["shots"][0]["char_layout"]
    names = {item.get("name") for item in char_layout_1 if isinstance(item, dict)}
    assert "老汉" not in names, f"老汉 should be aliased, got {names}"


def test_within_limit_no_repair():
    """3 场景 + 4 人物 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, scene="照月湾", location="照月湾", main_subject="杨旭"),
        make_shot(index=2, duration_sec=6.0, scene="杨府别庄", location="杨府别庄", main_subject="张十三"),
        make_shot(index=3, duration_sec=6.0, scene="照月湾", location="照月湾", main_subject="听香"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.redguo_scene_char_limit import RedguoSceneCharLimitConstraint

    repairs = RedguoSceneCharLimitConstraint().apply(ctx)

    assert len(repairs) == 0


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.redguo_scene_char_limit import RedguoSceneCharLimitConstraint
    from step01_script_gen.constraints.types import Severity

    c = RedguoSceneCharLimitConstraint()
    assert c.name == "redguo_scene_char_limit"
    assert c.severity == Severity.HARD
