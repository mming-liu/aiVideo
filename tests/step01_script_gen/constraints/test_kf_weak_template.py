"""W16. KfWeakTemplateConstraint 单元测试.

测试 3 类场景：
  1. 修复：弱模板占位 kf → 剥离占位指令 + 承袭 ff/lf 静态状态（pre 承袭 ff，react 承袭 lf）
  2. 幂等：已含「本帧画面」注入锚 → 跳过
  3. 隔离：未命中占位标记 / ff/lf 均缺省 → 不改写
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_pre_kf_inherits_ff_state():
    """pre 弱模板「不重复本镜 ff_desc」→ 剥离占位 + 追加 ff 静态状态."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 夜刺",
            ff_desc="杨旭仰卧船头，素白轻袍，胸口赫然插着一把匕首，双目紧闭",
            lf_desc="听香捂嘴，杨旭胸口匕首依旧",
            duration_sec=5.0,
            keyframes=[
                {
                    "stage": "pre",
                    "t_sec": 0.5,
                    "desc": "中近景构图：静态定格。杨旭姿态细节已在前一刻画面定格（不重复本镜 ff_desc 描述），镜头固定，等待动作启动。",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_weak_template import KfWeakTemplateConstraint

    repairs = KfWeakTemplateConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf = script["shots"][0]["keyframes"][0]
    # 占位指令被剥离
    assert "不重复本镜" not in kf["desc"]
    assert "前一刻画面定格" not in kf["desc"]
    # 承袭 ff 静态状态：匕首 + 仰卧
    assert "本帧画面即首帧静态状态" in kf["desc"]
    assert "匕首" in kf["desc"]
    assert "仰卧" in kf["desc"]


def test_react_kf_inherits_lf_state():
    """react 弱模板「lf 静态定格后的环境留痕」→ 承袭 lf 静态状态."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 夜刺",
            ff_desc="杨旭仰卧船头",
            lf_desc="杨旭仍仰卧不动，胸口匕首依旧，白袍上暗红洇痕扩大",
            duration_sec=5.0,
            keyframes=[
                {
                    "stage": "react",
                    "t_sec": 4.0,
                    "desc": "远景/中景构图：lf 静态定格后的环境留痕。水面涟漪圈数已标定。等待下一镜切换。",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_weak_template import KfWeakTemplateConstraint

    repairs = KfWeakTemplateConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf = script["shots"][0]["keyframes"][0]
    assert "本帧画面即尾帧静态状态" in kf["desc"]
    assert "匕首" in kf["desc"]


def test_idempotent_when_marker_present():
    """已含「本帧画面」注入锚 → 跳过，不重复追加."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 夜刺",
            ff_desc="杨旭仰卧船头，胸口插匕首",
            lf_desc="",
            duration_sec=5.0,
            keyframes=[
                {
                    "stage": "pre",
                    "t_sec": 0.5,
                    "desc": "静态定格（本帧画面即首帧静态状态：杨旭仰卧船头，胸口插匕首）",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_weak_template import KfWeakTemplateConstraint

    repairs = KfWeakTemplateConstraint().apply(ctx)

    assert repairs == []


def test_skips_when_no_template_marker():
    """内容自描述的 kf（无占位标记）→ 不改写."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 夜刺",
            ff_desc="杨旭仰卧船头，胸口插匕首",
            lf_desc="",
            duration_sec=5.0,
            keyframes=[
                {
                    "stage": "climax",
                    "t_sec": 2.5,
                    "desc": "刺客半身探出，刀尖朝下，杨旭仰卧未察觉",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_weak_template import KfWeakTemplateConstraint

    repairs = KfWeakTemplateConstraint().apply(ctx)

    assert repairs == []


def test_strips_pose_detail_placeholder_variant():
    """08-27 新变体「姿态细节已在画面定格…等待动作启动」→ 占位前奏整段剥离."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 夜刺",
            ff_desc="杨旭仰卧船头，素白轻袍，胸口赫然插着一把匕首，双目紧闭",
            lf_desc="",
            duration_sec=5.0,
            keyframes=[
                {
                    "stage": "pre",
                    "t_sec": 0.5,
                    "desc": "中近景构图：静态定格。杨旭姿态细节已在画面定格，"
                            "听香姿态细节已在画面定格。前景船舷木纹/荷叶虚化，"
                            "中景角色姿态定格，背景冷月/弦月倒影。镜头固定，等待动作启动。",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_weak_template import KfWeakTemplateConstraint

    repairs = KfWeakTemplateConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf = script["shots"][0]["keyframes"][0]
    # 占位前奏被整段剥离
    assert "姿态细节已在画面定格" not in kf["desc"]
    assert "等待动作启动" not in kf["desc"]
    assert "中景角色姿态定格" not in kf["desc"]
    assert "镜头固定" not in kf["desc"]
    # 修复锚承袭 ff 真实状态
    assert "本帧画面即首帧静态状态" in kf["desc"]
    assert "匕首" in kf["desc"]
    assert "仰卧" in kf["desc"]


def test_keeps_real_action_before_placeholder():
    """占位标记前有 LLM 真实动作句 → 保留该句，不整段丢弃."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 夜刺",
            ff_desc="杨旭仰卧船头，胸口插匕首",
            lf_desc="",
            duration_sec=5.0,
            keyframes=[
                {
                    "stage": "approach",
                    "t_sec": 1.0,
                    "desc": "刺客破水而出，姿态细节已在画面定格，等待动作启动。",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_weak_template import KfWeakTemplateConstraint

    repairs = KfWeakTemplateConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf = script["shots"][0]["keyframes"][0]
    # 真实动作句保留
    assert "刺客破水而出" in kf["desc"]
    # 占位标记剥离
    assert "姿态细节已在画面定格" not in kf["desc"]
    assert "等待动作启动" not in kf["desc"]
    # 修复锚仍追加
    assert "本帧画面即首帧静态状态" in kf["desc"]


def test_hook_weak_template_also_fixed():
    """hook 分镜弱模板同样承袭 ff 状态."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script(
        shots=[],
        hook={
            "index": 0,
            "ff_desc": "杨旭仰卧船头，胸口赫然插着一把匕首，双目紧闭",
            "lf_desc": "杨旭仍仰卧不动，胸口匕首依旧",
            "duration_sec": 5.0,
            "keyframes": [
                {
                    "stage": "pre",
                    "t_sec": 0.5,
                    "desc": "静态定格。杨旭姿态细节已在前一刻画面定格（不重复本镜 ff_desc 描述），等待动作启动。",
                },
            ],
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_weak_template import KfWeakTemplateConstraint

    repairs = KfWeakTemplateConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf = script["hook"]["keyframes"][0]
    assert "本帧画面即首帧静态状态" in kf["desc"]
    assert "匕首" in kf["desc"]
