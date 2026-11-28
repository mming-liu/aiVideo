# -*- coding: utf-8 -*-
"""2026-09-27 十次连跑根因 P6：BLOCK manifest 的 blocking 清单截断**显式化**.

## 问题

`shared/quality_gate.py` 里清单是裸 `[:20]`：`blocking_count` 是真值，而清单只留
**顺序前 20 条**。10 次连跑里 36 条的 run 也只落 20 条，且「留哪 20 条」取决于排序
——于是「这一轮到底有哪些类缺陷」从清单读不出来，问题报告只能按清单统计出**下限**
（kf 族因此被少算）。

## 本文件锁住

1. 清单 ≤ 20 条时**原样**保留，且 `blocking_truncated=False`；
2. 超 20 条时 `blocking_truncated=True`（读者不必数）；
3. 截断后**每个 code 至少留一条**（旧口径会整类漏掉）；
4. 留下的是**原 record 子集**（不新增、不改写、不伪造）；
5. `blocking_count` 始终是真实总数（不因截断而变）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _rec(code, shot="1"):
    return {"code": code, "severity": "visual_risk", "shot_id": shot,
            "field_path": "ff_desc", "message": f"{code} 的问题"}


def _manifest(blocking):
    from shared.quality_gate import build_step01_commit_manifest

    return build_step01_commit_manifest(
        run_id="r", created_at="t", revision="rev",
        rule_source_fingerprint="fp", rule_version="v",
        audit_scope="final_after_deterministic_fixes",
        chapter_dir=Path("."), status="BLOCK", blocking=blocking, detail="d")


class TestTruncationIsExplicit:
    def test_short_list_is_kept_verbatim_and_not_flagged(self):
        items = [_rec("kf_segment_missing"), _rec("multi_person_staging")]
        m = _manifest(items)
        assert m["blocking"] == items
        assert m["blocking_truncated"] is False
        assert m["blocking_count"] == 2

    def test_oversize_list_declares_truncation_and_keeps_the_true_count(self):
        items = [_rec("c%d" % i, shot=str(i)) for i in range(36)]
        m = _manifest(items)
        assert m["blocking_count"] == 36, "总数必须是真值"
        assert m["blocking_truncated"] is True, (
            "36 条的 run 必须显式声明被截断（此前只能靠「正好 20 条」猜）")
        assert len(m["blocking"]) == 20


class TestEveryCodeSurvivesTheCap:
    def test_a_code_only_appearing_late_is_still_visible(self):
        """旧口径：前 20 条全被 kf 族占满 → 后面的类别整类看不见."""
        items = [_rec("kf_segment_missing", shot=str(i)) for i in range(30)]
        items += [_rec("shot_duration_below_min", shot="31"),
                  _rec("prop_possession_consistency", shot="32")]
        m = _manifest(items)
        codes = {r["code"] for r in m["blocking"]}
        assert "shot_duration_below_min" in codes and \
            "prop_possession_consistency" in codes, (
            "截断不得让某一类缺陷整体消失（旧 `[:20]` 正是这样）", sorted(codes))
        assert len(m["blocking"]) == 20

    def test_kept_items_are_the_original_records(self):
        items = [_rec("a", shot=str(i)) for i in range(25)]
        items.append(_rec("b", shot="99"))
        m = _manifest(items)
        pool = {id(x) for x in items}
        assert all(id(x) in pool for x in m["blocking"]), "不得伪造/改写 record"
        assert m["blocking"][-1] is items[-1] or \
            any(r["code"] == "b" for r in m["blocking"])

    def test_more_codes_than_cap_still_bounded(self):
        items = [_rec("c%d" % i, shot=str(i)) for i in range(30)]
        m = _manifest(items)
        assert len(m["blocking"]) == 20
        assert m["blocking_count"] == 30

    def test_empty_blocking_is_fine(self):
        m = _manifest([])
        assert m["blocking"] == [] and m["blocking_truncated"] is False
