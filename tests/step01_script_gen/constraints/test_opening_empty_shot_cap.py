"""F15. OpeningEmptyShotCapConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make_empty_shot(index, main_subject="空镜·水波/树影", duration=4.0, prompt="环境空镜，无人无角色"):
    from tests.step01_script_gen.conftest import make_shot
    return make_shot(
        index=index,
        main_subject=main_subject,
        duration_sec=duration,
        prompt=prompt,
        ff_desc="环境空镜，无人无角色，暖金阳光斜照。",
        lf_desc="环境空镜，无人无角色，仍空无一人。",
        narration_segment="",
        subtitle="",
        core_keywords=["空镜", "环境"],
    )


def _make_char_shot(index, name="杨旭", duration=5.0):
    from tests.step01_script_gen.conftest import make_shot
    return make_shot(
        index=index,
        main_subject=name,
        duration_sec=duration,
        prompt="中景，杨旭站立船头，白袍随风。",
        ff_desc="杨旭正面半身，白袍金纹，手握折扇。",
        narration_segment=name + "：何事惊慌？",
        subtitle=name + "：何事惊慌？",
    )


def test_three_empty_shots_merges_to_one():
    """前3镜全是空镜 → 合并为1个，保留信息最多的."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.opening_empty_shot_cap import OpeningEmptyShotCapConstraint

    script = make_script([
        _make_empty_shot(1, "空镜·烈日", 3.0, "烈日高悬，河面波光粼粼。"),
        _make_empty_shot(2, "空镜·柳荫", 3.0, "岸边柳荫婆娑，蝉鸣阵阵。"),
        _make_empty_shot(3, "空镜·荷湾", 4.0, "河湾荷花盛开，蜻蜓点水。" * 3),  # 信息最多
        _make_char_shot(4),
    ])
    ctx = make_ctx(script)
    repairs = OpeningEmptyShotCapConstraint().apply(ctx)

    assert len(repairs) == 1, f"expected 1 repair, got {len(repairs)}"
    # 合并后前3镜变成1镜 + 第4镜人物镜 = 共3镜（不对：原12镜，删2个，剩10镜——等等，只有4镜）
    # 原4镜，删2个空镜，剩2镜
    assert len(script["shots"]) == 2, f"expected 2 shots, got {len(script['shots'])}"
    # 第一镜应是空镜（保留的那个）
    assert "空镜" in script["shots"][0]["main_subject"]
    # 第二镜应是人物镜
    assert script["shots"][1]["main_subject"] == "杨旭"
    # 时长合并（v6.63.1：钳到单镜硬上限 8s——3+3+4=10s 超限会被 timecode 拆回 2 镜）
    assert abs(script["shots"][0]["duration_sec"] - 8.0) < 0.01, \
        f"expected 8.0s (clamped), got {script['shots'][0]['duration_sec']}"
    # 重新编号正确
    assert script["shots"][0]["index"] == 1
    assert script["shots"][1]["index"] == 2


def test_two_empty_shots_merges_to_one():
    """前3镜中有2个空镜 → 合并为1个."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.opening_empty_shot_cap import OpeningEmptyShotCapConstraint

    script = make_script([
        _make_empty_shot(1, "空镜·烈日", 3.0),
        _make_char_shot(2, duration=5.0),
        _make_empty_shot(3, "空镜·荷湾", 4.0),
    ])
    ctx = make_ctx(script)
    repairs = OpeningEmptyShotCapConstraint().apply(ctx)

    assert len(repairs) == 1
    # 删1个空镜，剩2镜
    assert len(script["shots"]) == 2


def test_one_empty_shot_no_change():
    """前3镜只有1个空镜 → 不修改."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.opening_empty_shot_cap import OpeningEmptyShotCapConstraint

    script = make_script([
        _make_empty_shot(1, "空镜·烈日", 3.0),
        _make_char_shot(2),
        _make_char_shot(3, "张十三"),
    ])
    original_count = len(script["shots"])
    ctx = make_ctx(script)
    repairs = OpeningEmptyShotCapConstraint().apply(ctx)

    assert len(repairs) == 0
    assert len(script["shots"]) == original_count


def test_action_long_shot_not_counted_as_empty():
    """含动作事件的远景镜不算空镜."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.opening_empty_shot_cap import OpeningEmptyShotCapConstraint

    script = make_script([
        _make_empty_shot(1, "空镜·烈日", 3.0),
        _make_empty_shot(2, "远景·刺客跃出", 4.0),  # 这个含"跃出"事件词，不算空镜
        _make_char_shot(3),
    ])
    # 手动把第2镜改成远景动作镜
    script["shots"][1]["prompt"] = "远景，刺客破水跃出，匕首寒光一闪。"
    script["shots"][1]["ff_desc"] = "远景水面，一道黑影破水而出，身体腾空。"
    script["shots"][1]["narration_segment"] = ""
    script["shots"][1]["subtitle"] = ""
    ctx = make_ctx(script)
    repairs = OpeningEmptyShotCapConstraint().apply(ctx)

    # 只有1个真正的空镜，不触发合并
    assert len(repairs) == 0
    assert len(script["shots"]) == 3


def test_dialogue_shot_not_empty():
    """有台词的镜头不算空镜."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.opening_empty_shot_cap import OpeningEmptyShotCapConstraint

    script = make_script([
        _make_empty_shot(1, "空镜·烈日", 3.0),
        _make_empty_shot(2, "空镜·柳荫", 3.0),
        _make_char_shot(3),  # 有台词
    ])
    # 把第2镜加上台词
    script["shots"][1]["narration_segment"] = "画外音：公子，有人来了。"
    script["shots"][1]["subtitle"] = "画外音：公子，有人来了。"
    ctx = make_ctx(script)
    repairs = OpeningEmptyShotCapConstraint().apply(ctx)

    # 只有第1镜是空镜 + 第2镜有台词不算空镜 = 1个空镜，不触发
    assert len(repairs) == 0


def test_idempotent_second_pass():
    """幂等性：连跑2次，第二次无新增."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.opening_empty_shot_cap import OpeningEmptyShotCapConstraint

    script = make_script([
        _make_empty_shot(1, "空镜·烈日", 3.0),
        _make_empty_shot(2, "空镜·柳荫", 3.0),
        _make_empty_shot(3, "空镜·荷湾", 4.0),
        _make_char_shot(4),
    ])
    ctx = make_ctx(script)
    c = OpeningEmptyShotCapConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)
    assert len(r1) == 1
    assert len(r2) == 0


def test_merged_duration_clamped_to_timecode_cap(monkeypatch):
    """v6.63.1：合并时长钳到单镜硬上限（timecode.MAX_SHOT_DURATION）.

    3+3+4=10s 在 8s 规则下超限——超限镜会被 timecode pre-pass 拆回多镜，
    合并白做；钳制后保持单镜合法。此处把 cap 调成 6s 验证钳制生效。
    """
    import step01_script_gen.timecode as tc
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.opening_empty_shot_cap import OpeningEmptyShotCapConstraint

    monkeypatch.setattr(tc, "MAX_SHOT_DURATION", 6.0)
    script = make_script([
        _make_empty_shot(1, "空镜·烈日", 3.0),
        _make_empty_shot(2, "空镜·柳荫", 3.0),
        _make_empty_shot(3, "空镜·荷湾", 4.0),
        _make_char_shot(4),
    ])
    ctx = make_ctx(script)
    OpeningEmptyShotCapConstraint().apply(ctx)

    assert abs(script["shots"][0]["duration_sec"] - 6.0) < 0.01, \
        f"expected 6.0s (clamped), got {script['shots'][0]['duration_sec']}"


def test_constraint_attributes():
    from step01_script_gen.constraints.opening_empty_shot_cap import OpeningEmptyShotCapConstraint
    from step01_script_gen.constraints.types import Severity
    c = OpeningEmptyShotCapConstraint()
    assert c.name == "opening_empty_shot_cap"
    assert c.severity == Severity.WARN
    assert c.skip_hook is True


def test_merged_keywords_in_main_subject():
    """合并后 main_subject 应包含所有被合并镜的关键词."""
    from tests.step01_script_gen.conftest import make_ctx, make_script
    from step01_script_gen.constraints.opening_empty_shot_cap import OpeningEmptyShotCapConstraint

    script = make_script([
        _make_empty_shot(1, "空镜·烈日", 3.0),
        _make_empty_shot(2, "空镜·柳荫", 3.0),
        _make_empty_shot(3, "空镜·荷湾", 4.0),
        _make_char_shot(4),
    ])
    ctx = make_ctx(script)
    OpeningEmptyShotCapConstraint().apply(ctx)

    ms = script["shots"][0]["main_subject"]
    assert "烈日" in ms
    assert "柳荫" in ms
    assert "荷湾" in ms