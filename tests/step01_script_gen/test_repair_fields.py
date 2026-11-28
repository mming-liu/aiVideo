# -*- coding: utf-8 -*-
"""修复授权纯函数（二次审核发现 2）.

`allowed_repair_fields` 从 warning/audit issue 推出「本轮允许改哪些字段」；
`check_replacement_authorized` 用 canonical 路径 diff 判越权。两者都是纯函数，
这里单独锁住它们的边界行为（端到端拒绝路径见 `test_repair_shot_replacements.py`）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.repair_fields import (  # noqa: E402
    DEFAULT_LOCKED_FIELDS, DERIVED_FIELDS, allowed_repair_fields,
    canonical_path_diff, check_replacement_authorized, path_allowed,
)


def test_default_locked_fields_cover_time_domain():
    """迁移期默认锁定：时间语义字段不得因为「没被点名」而放开."""
    for f in ("temporal_plan", "motion_desc", "keyframes", "duration"):
        assert f in DEFAULT_LOCKED_FIELDS


def test_warning_text_maps_to_fields():
    assert allowed_repair_fields(["跨镜 LF 重复率 70%"]) == {"ff_desc", "lf_desc"}
    assert "keyframes.*.t_sec" in allowed_repair_fields(["同刻堆叠，保留原值"])
    assert "prompt" in allowed_repair_fields(["镜头1 prompt 长度超预算"])


def test_warning_without_field_path_yields_empty_set():
    """推不出字段 → 空集（调用方据此拒绝该目标的定向修复）."""
    assert allowed_repair_fields(["「小舟」锚定位缺失"]) == set()
    assert allowed_repair_fields([]) == set()


def test_ascii_keywords_match_whole_tokens_only():
    """`FF` 必须整词匹配——不能因为文本里出现 `OFFER` 就放开 ff_desc."""
    assert allowed_repair_fields(["OFFER 这个词里没有字段名"]) == set()


def test_issue_field_path_is_used_verbatim():
    class _I:
        code = "some_new_code"
        field_path = "ff_desc"
        message = "无关键词的告警"

    assert allowed_repair_fields([], [_I()]) == {"ff_desc"}


def test_path_allowed_supports_wildcards_and_parents():
    allowed = {"keyframes.*.t_sec", "prompt"}
    assert path_allowed("keyframes[0].t_sec", allowed)
    assert path_allowed("keyframes[2].t_sec", allowed)
    assert path_allowed("prompt", allowed)
    assert not path_allowed("keyframes[0].desc", allowed)
    assert not path_allowed("motion_desc", allowed)
    # 父字段放开 → 子路径全部放开
    assert path_allowed("frame_layouts.ff.0.name", {"frame_layouts"})


def test_derived_fields_never_count_as_diff():
    a = {"duration": 4.0}
    b = {"duration": 4.0, "duration_sec": 4.0,
         "timecode": {"start": 0.0, "end": 4.0}}
    assert canonical_path_diff(a, b) == []
    assert DERIVED_FIELDS == ("duration_sec", "timecode")


def test_float_tolerance_absorbs_frame_rounding():
    a = {"duration": 4.0}
    b = {"duration": 4.001}
    assert canonical_path_diff(a, b) == []
    assert canonical_path_diff(a, {"duration": 4.5}) == ["duration"]


def test_list_length_change_is_a_diff():
    a = {"keyframes": [{"desc": "x", "t_sec": 1.0}]}
    b = {"keyframes": [{"desc": "x", "t_sec": 1.0}, {"desc": "y", "t_sec": 3.0}]}
    assert "keyframes.__len__" in canonical_path_diff(a, b)


def test_authorization_rejects_any_unauthorized_change():
    a = {"prompt": "p", "motion_desc": "m", "duration": 4.0}
    b = {"prompt": "p2", "motion_desc": "m2", "duration": 4.0}
    assert "越权改动" in check_replacement_authorized(a, b, {"prompt"})
    assert check_replacement_authorized(a, b, {"prompt", "motion_desc"}) == ""


def test_empty_allowed_rejects_everything_but_derived():
    a = {"prompt": "p"}
    b = {"prompt": "p", "duration_sec": 4.0}
    assert check_replacement_authorized(a, b, set()) == ""
    assert "越权改动" in check_replacement_authorized(a, {"prompt": "p2"}, set())


# ------------------- v6.91（用户决定）：未授权路径**强制回填**

def test_merge_authorized_keeps_only_authorized_edits():
    """授权路径用模型的，其余一律取原镜原值.

    2026-09-22 07:39 实测：replacement 契约要求返回**完整**镜头对象（24 字段全必填），
    而授权只覆盖少数几个路径——模型把未授权字段的措辞写得略有不同就整条被拒，
    3/3 目标因此被拒、修复轮产出 0。回填后「未授权路径 = 原样」由**构造**保证。
    """
    from step01_script_gen.repair_fields import merge_authorized

    target = {"index": 1, "prompt": "原 prompt", "ff_desc": "原 ff",
              "core_keywords": ["船头"], "audio_cue": "原音",
              "keyframes": [{"t_sec": 1.0, "desc": "原 desc", "stage": "pre"}],
              "frame_layouts": {"ff": [{"pose": "直立"}]}}
    new = {"index": 1, "prompt": "新 prompt（越权）", "ff_desc": "新 ff（授权）",
           "core_keywords": ["船头", "新词"], "audio_cue": "新音",
           "keyframes": [{"t_sec": 2.0, "desc": "新 desc（授权）", "stage": "pre"}],
           "frame_layouts": {"ff": [{"pose": "转身"}]}}

    merged, echoed = merge_authorized(target, new, {"ff_desc", "keyframes.*.desc"})

    assert merged["ff_desc"] == "新 ff（授权）"
    assert merged["keyframes"][0]["desc"] == "新 desc（授权）"
    assert merged["prompt"] == "原 prompt"
    assert merged["core_keywords"] == ["船头"]
    assert merged["audio_cue"] == "原音"
    assert merged["frame_layouts"]["ff"][0]["pose"] == "直立"
    assert merged["keyframes"][0]["t_sec"] == 1.0, "未授权的 t_sec 必须回填"
    # 只记**确实被改过**的路径（`index` 这类恒定字段不得塞满日志）
    assert "index" not in echoed and "prompt" in echoed


def test_merge_authorized_drops_unauthorized_added_keys():
    """模型新加的未授权键 → 丢弃（否则等于绕过授权新增字段）."""
    from step01_script_gen.repair_fields import merge_authorized

    merged, _ = merge_authorized({"index": 1}, {"index": 1, "偷偷加的键": "x"},
                                 {"prompt"})
    assert "偷偷加的键" not in merged


def test_merge_authorized_list_length_change_needs_authorization():
    """整表授权才允许增删列表元素；否则回填原表.

    「为修 kf 缺段而增删关键帧」要靠整表授权（`keyframes`）放行——只授权叶子
    （`keyframes.*.desc`）时长度变化属结构改动，回填原值。
    """
    from step01_script_gen.repair_fields import merge_authorized

    target = {"keyframes": [{"t_sec": 1.0, "desc": "a"}]}
    longer = {"keyframes": [{"t_sec": 1.0, "desc": "a"}, {"t_sec": 2.0, "desc": "b"}]}

    merged, _ = merge_authorized(target, longer, {"keyframes.*.desc"})
    assert len(merged["keyframes"]) == 1, "叶子授权不得改变列表长度"

    merged2, _ = merge_authorized(target, longer, {"keyframes"})
    assert len(merged2["keyframes"]) == 2, "整表授权应放行增删"


def test_merge_authorized_is_idempotent_and_noop_safe():
    """已是授权内的改动 → 合并后不变；值本来就相同 → 不记回填."""
    from step01_script_gen.repair_fields import merge_authorized

    target = {"index": 5, "prompt": "同值", "ff_desc": "原"}
    merged, echoed = merge_authorized(target, {"index": 5, "prompt": "同值",
                                              "ff_desc": "新"}, {"ff_desc"})
    assert merged == {"index": 5, "prompt": "同值", "ff_desc": "新"}
    assert echoed == [], echoed


# ---------------------------------------------------------------------------
# v6.113（第三十次真实重跑）：审计路径含 props → 布局侧同样授权
# ---------------------------------------------------------------------------
class _PropsIssue:
    code = "model_audit_issue"
    entity_id = ""
    message = "ff_desc 提到钓杆/酒壶，但布局未列出"

    def __init__(self, field_path, frame_id=""):
        self.field_path = field_path
        self.frame_id = frame_id


def test_audit_props_path_authorizes_both_sides():
    """`ff_desc.props` 这种组合路径不在 schema 里——必须映射到真实的布局 props，
    否则授权集是一条**死路径**，模型改哪里都被回填（审计每轮重报同一条）."""
    allowed = allowed_repair_fields([], [_PropsIssue("ff_desc.props", "ff")])
    assert "ff_desc" in allowed
    assert path_allowed("frame_layouts.ff.0.props", allowed)
    # 死路径本身不再留着（留着会让 `_narrow` 把真正的 ff_desc 当宽前缀丢掉）
    assert "ff_desc.props" not in allowed


def test_audit_props_path_maps_each_frame():
    assert path_allowed("frame_layouts.lf.0.props",
                        allowed_repair_fields([], [_PropsIssue("lf_desc.props", "lf")]))
    assert path_allowed("frame_layouts.kfs.0.0.props",
                        allowed_repair_fields([], [_PropsIssue("kf1.desc.props", "kf1")]))
    # 帧未知 → 三处都放开（收窄会重演死路径）
    unknown = allowed_repair_fields([], [_PropsIssue("props", "")])
    for p in ("frame_layouts.ff.0.props", "frame_layouts.lf.0.props",
              "frame_layouts.kfs.0.0.props"):
        assert path_allowed(p, unknown), p


def test_deterministic_props_path_still_covered():
    """确定性检测器给的真实路径（`frame_layouts.ff[0].props`）仍在授权内."""
    allowed = allowed_repair_fields([], [_PropsIssue("frame_layouts.ff[0].props", "ff")])
    assert path_allowed("frame_layouts.ff.0.props", allowed)
