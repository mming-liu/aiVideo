"""v6.58 A7 拆分镜内容重复检测的测试.

覆盖：
  1. helper: jaccard_bigram
  2. helper: is_split_pair（含 8.0 边界，float==int 陷阱）
  3. helper: split_pair_similarity
  4. constraint: 高重复被检测 → WARN
  5. constraint: 正常拆镜对不报警
  6. constraint: 主镜（无 _split_from）不参与
  7. constraint: 找不到原镜不崩溃
  8. constraint: 跨段多副镜都检测

遵循现有 test 风格：函数式 + 局部 import（避免 pytest collect 时 sys.path
未就绪导致 module 找不到）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# helper 单元测试
# ---------------------------------------------------------------------------

def test_jaccard_identical():
    from shared.split_similarity import jaccard_bigram
    s = "杨旭拔剑出鞘"
    assert jaccard_bigram(s, s) == 1.0


def test_jaccard_2_char_diff():
    """实测差 2 字 ≈ 0.71."""
    from shared.split_similarity import jaccard_bigram
    a = "杨旭拔剑出鞘，刺向对方胸口"
    b = "杨旭拔剑出鞘，刺向对方肩部"
    sim = jaccard_bigram(a, b)
    assert 0.6 < sim < 0.85


def test_jaccard_completely_different():
    from shared.split_similarity import jaccard_bigram
    assert jaccard_bigram("杨旭拔剑出鞘", "李寻欢放下酒杯") == 0.0


def test_jaccard_empty():
    from shared.split_similarity import jaccard_bigram
    assert jaccard_bigram("", "") == 0.0


def test_jaccard_short_text():
    """< 2 字符 → 0（避免空集合除零）."""
    from shared.split_similarity import jaccard_bigram
    assert jaccard_bigram("X", "X") == 0.0


def test_is_split_pair_7_5():
    from shared.split_similarity import is_split_pair
    assert is_split_pair({"index": 7.5, "_split_from": 7})


def test_is_split_pair_8_0():
    """关键：8.0 是 float，8.0 == 8 在 Python 是 True——必须用 str '.' 判定."""
    from shared.split_similarity import is_split_pair
    assert is_split_pair({"index": 8.0, "_split_from": 7.5})


def test_is_split_pair_8_25():
    from shared.split_similarity import is_split_pair
    assert is_split_pair({"index": 8.25, "_split_from": 8})


def test_is_split_pair_main_shot():
    from shared.split_similarity import is_split_pair
    assert not is_split_pair({"index": 7})


def test_is_split_pair_no_split_from():
    from shared.split_similarity import is_split_pair
    assert not is_split_pair({"index": 7.5})  # 缺 _split_from


def test_split_pair_similarity_high():
    """同动作差 1 字 → sim 应明显高于低重复对照."""
    from shared.split_similarity import split_pair_similarity
    shot_a = {
        "motion_desc": "杨旭拔剑出鞘",
        "prompt": "杨旭拔剑",
        "lf_desc": "拔剑完成",
    }
    shot_b = {
        "motion_desc": "杨旭拔剑出鞘",
        "prompt": "杨旭拔剑",
        "lf_desc": "拔剑到位",
    }
    sim = split_pair_similarity(shot_a, shot_b)
    # 实测 0.636——同动作变体，明显 > 完全不同（< 0.3）
    assert sim >= 0.5


def test_split_pair_similarity_low():
    from shared.split_similarity import split_pair_similarity
    shot_a = {
        "motion_desc": "杨旭拔剑出鞘",
        "prompt": "杨旭拔剑",
        "lf_desc": "拔剑完成",
    }
    shot_b = {
        "motion_desc": "李寻欢放下酒杯",
        "prompt": "李寻欢转身",
        "lf_desc": "缓步离开",
    }
    sim = split_pair_similarity(shot_a, shot_b)
    assert sim < 0.3


# ---------------------------------------------------------------------------
# constraint 行为测试
# ---------------------------------------------------------------------------

def test_constraint_high_similarity_pair_warns():
    """典型 shot10/10.5 重复场景——长 prompt + 仅 lf 差 1 字."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.split_content_similarity import (
        SplitContentSimilarityConstraint,
    )

    # 长 prompt 拉开 bigram 集合，1 字差异占比变小 → sim 趋近 0.85+
    long_a = (
        "杨旭拔剑出鞘，刀锋寒光一闪，握紧刀柄蓄势待发，"
        "目光紧盯对方胸口，脚步前移寻找破绽"
    )
    long_b = (
        "杨旭拔剑出鞘，刀锋寒光一闪，握紧刀柄蓄势待发，"
        "目光紧盯对方胸口，脚步前移寻找破绽"
    )
    script = make_script([
        make_shot(
            index=10,
            motion_desc=long_a,
            prompt=long_a,
            lf_desc="刀尖已抵对方胸前",
        ),
        make_shot(
            index=10.5,
            _split_from=10,
            motion_desc=long_a,
            prompt=long_b,
            lf_desc="刀尖已抵对方胸前",  # 几乎相同
        ),
    ])
    ctx = make_ctx(script)
    target = SplitContentSimilarityConstraint()
    target.apply(ctx)
    violations = ctx.drain_violations()

    sim_violations = [v for v in violations
                      if v.constraint_name == "split_content_similarity"]
    assert len(sim_violations) >= 1
    # Severity.WARN 枚举（value=2）
    from step01_script_gen.constraints.types import Severity
    assert sim_violations[0].severity == Severity.WARN


def test_constraint_normal_split_pair_passes():
    """正常拆镜：shot_a 拔剑、shot_b 收剑（不同动作）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.split_content_similarity import (
        SplitContentSimilarityConstraint,
    )

    script = make_script([
        make_shot(
            index=11,
            motion_desc="杨旭拔剑出鞘",
            prompt="杨旭拔剑",
            lf_desc="刀锋寒光一闪",
        ),
        make_shot(
            index=11.5,
            _split_from=11,
            motion_desc="刺客格挡后撤",
            prompt="刺客举刀格挡",
            lf_desc="双方僵持",
        ),
    ])
    ctx = make_ctx(script)
    target = SplitContentSimilarityConstraint()
    target.apply(ctx)
    violations = ctx.drain_violations()

    sim_violations = [v for v in violations
                      if v.constraint_name == "split_content_similarity"]
    assert sim_violations == []


def test_constraint_no_split_from_no_check():
    """主镜之间不参与（即使文本相似）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.split_content_similarity import (
        SplitContentSimilarityConstraint,
    )

    script = make_script([
        make_shot(
            index=5,
            motion_desc="杨旭拔剑",
            prompt="杨旭拔剑",
            lf_desc="刀出鞘",
        ),
        make_shot(
            index=6,
            motion_desc="杨旭拔剑",  # 文本相似
            prompt="杨旭拔剑",
            lf_desc="刀出鞘",
        ),
    ])
    ctx = make_ctx(script)
    target = SplitContentSimilarityConstraint()
    target.apply(ctx)
    violations = ctx.drain_violations()

    sim_violations = [v for v in violations
                      if v.constraint_name == "split_content_similarity"]
    assert sim_violations == []


def test_constraint_missing_original_shot_no_crash():
    """原镜不在 shots 列表（异常）时不崩溃."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.split_content_similarity import (
        SplitContentSimilarityConstraint,
    )

    script = make_script([
        make_shot(
            index=12.5,
            _split_from=999,  # 不存在的原镜
            motion_desc="杨旭拔剑",
            prompt="杨旭拔剑",
            lf_desc="刀出鞘",
        ),
    ])
    ctx = make_ctx(script)
    target = SplitContentSimilarityConstraint()
    # 不应抛异常
    target.apply(ctx)
    violations = ctx.drain_violations()

    sim_violations = [v for v in violations
                      if v.constraint_name == "split_content_similarity"]
    assert sim_violations == []


def test_constraint_multiple_split_pairs_all_detected():
    """多个拆镜对：10.5/11.5 重复（长文本），12.5 不重复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.split_content_similarity import (
        SplitContentSimilarityConstraint,
    )

    long_text = (
        "杨旭拔剑出鞘，刀锋寒光一闪，握紧刀柄蓄势待发，"
        "目光紧盯对方胸口，脚步前移寻找破绽"
    )
    script = make_script([
        make_shot(index=10, motion_desc=long_text, prompt=long_text, lf_desc="刀出鞘寒光"),
        make_shot(index=10.5, _split_from=10, motion_desc=long_text, prompt=long_text, lf_desc="刀出鞘寒光闪"),
        make_shot(index=11, motion_desc=long_text, prompt=long_text, lf_desc="刀出鞘寒光"),
        make_shot(index=11.5, _split_from=11, motion_desc=long_text, prompt=long_text, lf_desc="刀出鞘寒光闪"),
        make_shot(index=12, motion_desc="门吱呀响", prompt="门响", lf_desc="门开"),
        make_shot(index=12.5, _split_from=12, motion_desc="风吹草动", prompt="风吹", lf_desc="草动"),
    ])
    ctx = make_ctx(script)
    target = SplitContentSimilarityConstraint()
    target.apply(ctx)
    violations = ctx.drain_violations()

    sim_violations = [v for v in violations
                      if v.constraint_name == "split_content_similarity"]
    # 至少 1 个（10.5 + 11.5 都重复，12.5 不会）
    assert len(sim_violations) >= 1
    # 12.5 不应被警告（"门吱呀响" vs "风吹草动" 完全不重叠）
    violation_indices = {v.shot_index for v in sim_violations}
    assert 12.5 not in violation_indices
