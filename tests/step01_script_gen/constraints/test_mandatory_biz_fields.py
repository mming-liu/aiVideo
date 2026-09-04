"""B1. MandatoryBizFieldsConstraint 单元测试（2026-09-04，doubao 适配）.

测试 5 类场景：
  1. 4 字段全部缺失 → 全部兜底填入
  2. 部分字段缺失 → 只补缺失的
  3. 幂等：连跑 2 次 = 第 2 次 0 改动
  4. context_bridge 兜底承接前镜 lf_desc
  5. core_keywords 兜底从 main_subject + ff_desc 提取
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_fills_all_four_missing_fields():
    """4 字段全部缺失 → 全部兜底填入（4 个 RepairRecord）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 用 main_subject="空镜" 强制走 defaults 分支（不让 LLM 提取干扰断言）
    script = make_script([
        make_shot(
            index=1,
            prompt="无",
            ff_desc="无",  # 无中文词可提取
            lf_desc="无",
            main_subject="空镜",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.mandatory_biz_fields import (
        MandatoryBizFieldsConstraint,
    )

    repairs = MandatoryBizFieldsConstraint().apply(ctx)
    s = script["shots"][0]

    assert len(repairs) == 4
    # main_subject="空镜" 走 fallback defaults
    assert s["core_keywords"] != "" and "古风" in s["core_keywords"]
    assert s["fx_effect"] == "无"
    assert s["clip_transition"] == "硬切"
    assert "承接" in s["context_bridge"]


def test_fills_only_missing_fields():
    """部分字段已有 → 不重填；只补缺失的."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="杨旭在画中",
            ff_desc="杨旭盘膝坐船头",
            lf_desc="杨旭被刺",
            main_subject="杨旭",
            core_keywords="杨旭,船头,静坐",  # 已有
            clip_transition="淡入淡出",        # 已有
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.mandatory_biz_fields import (
        MandatoryBizFieldsConstraint,
    )

    repairs = MandatoryBizFieldsConstraint().apply(ctx)

    assert len(repairs) == 2  # 只补 fx_effect + context_bridge
    s = script["shots"][0]
    assert s["core_keywords"] == "杨旭,船头,静坐"  # 保留原值
    assert s["clip_transition"] == "淡入淡出"     # 保留原值
    assert s["fx_effect"] == "无"                 # 兜底
    assert "承接" in s["context_bridge"]          # 兜底


def test_idempotent_second_run_no_change():
    """幂等：连跑 2 次 = 第 2 次 0 改动（repair_marker 命中）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="x", ff_desc="x", lf_desc="x"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.mandatory_biz_fields import (
        MandatoryBizFieldsConstraint,
    )

    c = MandatoryBizFieldsConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)  # 同一 ctx 二次跑

    assert len(repairs1) == 4
    assert len(repairs2) == 0  # 全部已兜底，幂等


def test_context_bridge_inherits_from_prev_lf_desc():
    """context_bridge 兜底应从前镜 lf_desc 截取尾句，承接到本镜开头."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="杨旭坐船头",
            ff_desc="杨旭盘膝坐船头",
            lf_desc="杨旭被刺客刺中心口仰卧船头。",  # 末句
        ),
        make_shot(
            index=2,
            prompt="听香尖叫",
            ff_desc="听香坐船舷尖叫",
            lf_desc="听香蜷缩",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.mandatory_biz_fields import (
        MandatoryBizFieldsConstraint,
    )

    repairs = MandatoryBizFieldsConstraint().apply(ctx)
    s2 = script["shots"][1]
    # shot 2 的 context_bridge 应包含 shot 1 的 lf_desc 末句
    assert "承接" in s2["context_bridge"]
    # shot 2 的 context_bridge 应包含「仰卧船头」之类关键词
    assert "仰卧船头" in s2["context_bridge"] or "刺中" in s2["context_bridge"]


def test_core_keywords_extracts_from_main_subject_and_ff_desc():
    """core_keywords 兜底从 main_subject + ff_desc 提取（不走 LLM）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="x",
            ff_desc="刺客半身破水右手持匕首",  # 含「刺客」「半身破水」「右手持匕首」
            lf_desc="x",
            main_subject="刺客",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.mandatory_biz_fields import (
        MandatoryBizFieldsConstraint,
    )

    repairs = MandatoryBizFieldsConstraint().apply(ctx)
    s = script["shots"][0]
    # main_subject "刺客" 应在 keywords 里
    assert "刺客" in s["core_keywords"]


def test_empty_shots_returns_no_repairs():
    """空 shots 列表 → 0 repairs，不抛错."""
    from tests.step01_script_gen.conftest import make_ctx, make_script

    script = make_script([])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.mandatory_biz_fields import (
        MandatoryBizFieldsConstraint,
    )

    repairs = MandatoryBizFieldsConstraint().apply(ctx)
    assert repairs == []


def test_treats_null_string_as_missing():
    """字段值是字符串 'null' 或 'None' 视为缺失，触发兜底."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="x", ff_desc="x", lf_desc="x",
            core_keywords="null",  # 字符串 "null" 视为缺失
            fx_effect="None",      # 字符串 "None" 视为缺失
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.mandatory_biz_fields import (
        MandatoryBizFieldsConstraint,
    )

    repairs = MandatoryBizFieldsConstraint().apply(ctx)
    s = script["shots"][0]
    # core_keywords / fx_effect 都被兜底
    assert s["core_keywords"] != "null" and "古风" in s["core_keywords"]
    assert s["fx_effect"] == "无"
    # clip_transition / context_bridge 仍要兜底
    assert s["clip_transition"] == "硬切"
    assert "承接" in s["context_bridge"]
