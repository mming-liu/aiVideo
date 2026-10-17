"""step01 单元测试共用 fixture."""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录可导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 旧协议历史回放（工作包 1 第 3 条 / 工作包 4 第 1 条）
# ---------------------------------------------------------------------------
# `plan_revision.v2` 之后**新请求一律走 v2**；`replan_ops` 与受限 `full_plan` 保留为
# **冻结响应回放/兼容适配器**，只能由 `outline.with_legacy_request_protocol(...)` 显式进入。
#
# 本目录绝大多数历史用例断言的是**旧协议自身**的行为（守卫 / 能力盘点表 / 反馈文案 /
# 应用器）——它们**隐含**依赖生产路由器按能力表挑出 ops/full_plan，那正是"按内容猜接口"
# 的成因（复核实测 `KeyError: 'view'`）。这里把它们**显式**标成回放：测的东西不变，
# 只是不再假装那是生产路由。真正走生产 v2 的用例标 `@pytest.mark.v2_request`。
#
# ⚠️ 这**不是**把生产路由改回旧协议：开关只在本 fixture 的作用域内有效，出作用域即
# 恢复 v2（`test_old_protocols_only_via_the_explicit_replay_switch` 钉住这一点）。
import pytest                                                      # noqa: E402

from step01_script_gen import outline as _outline                  # noqa: E402


@pytest.fixture(autouse=True)
def _legacy_protocol_replay(request):
    """默认按**旧协议回放**；标了 ``v2_request`` 的用例走生产路径."""
    if request.node.get_closest_marker("v2_request") is not None:
        yield
        return
    with _outline.with_legacy_request_protocol(_outline.ROUTE_AUTO):
        yield


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "v2_request: 该用例走**新请求**路径（生产路由恒为 v2，不受旧协议回放开关影响）")


def make_shot(
    index: int = 1,
    prompt: str = "[中景] 镜头描述",
    ff_desc: str = "",
    lf_desc: str = "",
    motion_desc: str = "",
    duration_sec: float = 4.0,
    location: str = "小舟",
    keyframes: list | None = None,
    char_layout: list | None = None,
    narration_segment: str = "",
    subtitle: str = "",
    main_subject: str = "",
    **extra,
) -> dict:
    """构造一个最小 shot dict 用于测试.

    所有 kwargs 都是可选的——只传你关心的字段。defaults 给的是"古风小舟"场景
    的典型值，方便写 1-2 个测试。
    """
    shot = {
        "index": index,
        "prompt": prompt,
        "ff_desc": ff_desc,
        "lf_desc": lf_desc,
        "motion_desc": motion_desc,
        "duration_sec": duration_sec,
        "location": location,
        "keyframes": keyframes or [],
        "char_layout": char_layout or [],
        "narration_segment": narration_segment,
        "subtitle": subtitle,
    }
    if main_subject:
        shot["main_subject"] = main_subject
    shot.update(extra)
    return shot


def make_script(
    shots: list | None = None,
    *,
    hook: dict | None = None,
    characters: list | None = None,
) -> dict:
    """构造一个最小 script dict.

    Args:
        shots: shots 列表（默认空）
        hook: hook 分镜 dict（默认 None）
        characters: characters 列表（默认空）
    """
    return {
        "shots": shots or [],
        "hook": hook,
        "characters": characters or [],
    }


def make_ctx(
    script: dict,
    *,
    characters_bank: dict | None = None,
    props_bank: dict | None = None,
    novel_text: str = "",
    is_in_repair_loop: bool = False,
    is_final_pass: bool = False,
):
    """构造一个 ConstraintContext 用于测试.

    Args:
        script: script dict（必填，make_script 构造）
        characters_bank: 角色库 dict
        props_bank: 道具资产库 dict
        novel_text: 小说原文
        is_in_repair_loop: True=repair round 末尾
        is_final_pass: True=落盘前 final pass
    """
    from step01_script_gen.constraints import ConstraintContext

    return ConstraintContext.from_script(
        script,
        characters_bank=characters_bank or {},
        props_bank=props_bank or {},
        novel_text=novel_text,
        is_in_repair_loop=is_in_repair_loop,
        is_final_pass=is_final_pass,
    )
