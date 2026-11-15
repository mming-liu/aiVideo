# -*- coding: utf-8 -*-
"""M01：授权路径四阶段归因的**单元入口**验收（零模型 / 零网络）.

对应 run j 的确定性缺口：诊断时点在整轮回退之后，导致 36 个字段被笼统记为
「后处理 writer 回退」。这里逐阶段构造稿子，验证模型写入 / writer 改写 /
事务回滚能被分开，并且不互相掩盖。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import main as _main                       # noqa: E402
from step01_script_gen.repair_attribution import (                # noqa: E402
    attribute_field_loss, authorized_path_snapshot, changed_paths,
    flatten_exact_paths, value_fingerprint)
from step01_script_gen.repair_effects import RepairEffectLedger   # noqa: E402

_ALLOWED = {"shot_5": {"ff_desc", "prompt", "frame_layouts.ff.0.pose"}}


def _script(ff="原始首帧", prompt="原始提示", pose="站立"):
    return {
        "shots": [{
            "index": "5", "duration": 8.0,
            "ff_desc": ff, "prompt": prompt,
            "frame_layouts": {"ff": [{"pose": pose}], "lf": [], "kfs": []},
            "subtitle": "字幕原文",
        }],
    }


def _snap(script):
    return authorized_path_snapshot(script, _ALLOWED)


# ---------------------------------------------------------------------------
# 口径与快照
# ---------------------------------------------------------------------------
def test_flatten_matches_main_implementation():
    obj = {"a": {"b": 1}, "c": [{"d": 2}, 3], "e": None}
    assert flatten_exact_paths(obj) == _main._flatten_exact_paths(obj)


def test_snapshot_only_covers_authorized_existing_paths():
    snap = _snap(_script())
    assert set(snap["shot_5"]) == {
        "ff_desc", "prompt", "frame_layouts.ff.0.pose"}
    assert "subtitle" not in snap["shot_5"]


def test_missing_path_is_distinguished_from_empty_value():
    a = _script()
    b = _script()
    del b["shots"][0]["prompt"]
    diff = changed_paths(_snap(a), _snap(b))
    assert diff == {"shot_5": ["prompt"]}
    assert value_fingerprint(None) != value_fingerprint("<missing>")


def test_ledger_nodes_carry_authorized_paths_and_view_keeps_them():
    led = RepairEffectLedger(round_id="1", source_revision="r",
                             frame_targets={"shot_5"},
                             allowed_by_target=_ALLOWED)
    led.freeze_candidate(_script(), "r")
    led.freeze_merged(_script(ff="模型改写"), [])
    n = led.observe_writer("W1", _script(ff="模型改写"))
    assert n["authorized_paths"], "writer 节点必须冻结授权路径"
    view = led.filtered_view({"shot_5"})
    assert view.allowed_by_target == _ALLOWED
    assert view.nodes[-1]["authorized_paths"] == n["authorized_paths"]


# ---------------------------------------------------------------------------
# 四阶段归因
# ---------------------------------------------------------------------------
def _attribute(base, merged, post, final, *, post_rollback=None, writers=()):
    return attribute_field_loss(snapshots={
        "baseline": _snap(base), "merged": _snap(merged),
        "postprocess": _snap(post),
        "post_rollback": _snap(post_rollback or post),
        "final": _snap(final),
    }, writer_nodes=writers)


def test_writer_revert_is_attributed_to_first_changing_writer():
    base = _script()
    merged = _script(ff="模型改写")
    post = _script()                      # writer 改回基线
    writers = [
        {"node": "writer", "writer": "W1",
         "authorized_paths": _snap(merged)},   # 没改
        {"node": "writer", "writer": "W2",
         "authorized_paths": _snap(post)},     # 首次改掉
        {"node": "reverify", "writer": "",
         "authorized_paths": _snap(post)},     # 不能算成首次
    ]
    out = _attribute(base, merged, post, post, writers=writers)
    rec = out["fields"]["shot_5"]["ff_desc"]
    assert rec["stage"] == "writer_reverted"
    assert rec["model_written"] is True
    assert rec["writer_changed"] is True
    assert rec["first_writer"] == "W2"
    assert rec["transaction_rolled_back"] is False
    assert out["summary"]["writer_reverted"] == 1


def test_transaction_rollback_is_not_reported_as_postprocess_revert():
    """run j 的核心反例：writer 没动，整轮回退把模型改动抹掉."""
    base = _script()
    merged = _script(ff="模型改写", prompt="模型新提示")
    post = _script(ff="模型改写", prompt="模型新提示")   # 后处理完全没改
    final = _script()                                    # 整轮回退 → 基线
    out = _attribute(base, merged, post, final)
    fields = out["fields"]["shot_5"]
    assert set(path for path, rec in fields.items()
               if rec["stage"] == "transaction_rollback") == {
                   "ff_desc", "prompt"}
    for rec in fields.values():
        assert rec["writer_changed"] is False
        assert rec["first_writer"] is None
    assert out["summary"]["writer_changed"] == 0
    assert out["summary"]["transaction_rolled_back"] == 2
    # A→B 无差异，因此修正后的 postprocess_reverted 必须为空
    assert changed_paths(_snap(merged), _snap(post)) == {}
    # B→D 有差异，单独由 transaction_rollback 承载
    assert changed_paths(_snap(post), _snap(final)) == {
        "shot_5": ["ff_desc", "prompt"]}


def test_writer_revert_and_rollback_are_both_recorded_when_both_happen():
    base = _script()
    merged = _script(ff="模型改写")
    post = _script()                    # writer 改回
    final = _script()                   # 又整轮回退到同一份基线
    # 注意：post 与 final 同为基线 ⇒ 值相同 ⇒ 事务回滚在该字段上无额外差异，
    # 只应记 writer 改写（不能凭「accepted=false」硬造一个回滚阶段）。
    out = _attribute(base, merged, post, final)
    rec = out["fields"]["shot_5"]["ff_desc"]
    assert rec["stage"] == "writer_reverted"
    assert rec["transaction_rolled_back"] is False


def test_kept_model_change_is_not_counted_as_loss():
    base = _script()
    merged = _script(ff="模型改写")
    out = _attribute(base, merged, merged, merged)
    rec = out["fields"]["shot_5"]["ff_desc"]
    assert rec["stage"] == "kept"
    assert rec["model_written"] is True
    assert rec["writer_changed"] is False
    assert rec["transaction_rolled_back"] is False
    assert out["summary"]["kept"] == 1


def test_postprocess_only_change_without_model_write_is_visible():
    base = _script()
    post = _script(prompt="writer 自己写的")
    out = _attribute(base, base, post, post)
    rec = out["fields"]["shot_5"]["prompt"]
    assert rec["stage"] == "postprocess_only"
    assert rec["model_written"] is False
    assert rec["writer_changed"] is True


def test_preview_values_are_bounded_and_do_not_affect_stage():
    base = _script()
    merged = _script(ff="甲" * 400)
    out = attribute_field_loss(snapshots={
        "baseline": _snap(base), "merged": _snap(merged),
        "postprocess": _snap(merged), "final": _snap(base),
    }, preview_scripts={"baseline": base, "merged": merged,
                        "postprocess": merged, "final": base})
    rec = out["fields"]["shot_5"]["ff_desc"]
    assert rec["stage"] == "transaction_rollback"
    assert len(rec["values"]["merged"]) <= 123
