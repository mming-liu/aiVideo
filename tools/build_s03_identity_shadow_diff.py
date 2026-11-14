# -*- coding: utf-8 -*-
"""S03 身份生产与旧稿差异的影子记录（离线、零模型调用）.

2026-11-14 复核要求：旧稿没有单位身份时，检查器应报告「该项未完成语义判定」，
其**实际准入影响**进入影子差异，按既定决策处理（本工具不改变默认门禁）。

同一份匿名剧本构造三种计划版本，跑真实 `check_script_semantics`：

1. `old_no_identity`：causal_chain 无单位身份（旧稿）；
2. `new_identity`：causal_chain 带 `（Bk）` 身份（本轮生成端要求）；
3. `new_identity_plus_bird`：在第 2 种基础上插入真实节拍 B3「鸟从城头飞过」
   （镜头引用 B3、两个不同时刻关键帧），验证无关证据不进入关系范围。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.execution_semantics import (  # noqa: E402
    check_script_semantics,
    codes_of,
)


def _shot(index, *, ff, lf, motion, refs, keyframes=None):
    return {
        "index": index,
        "duration_sec": 5.0,
        "ff_desc": ff,
        "lf_desc": lf,
        "motion_desc": motion,
        "prompt": ff,
        "narration_segment": "",
        "context_bridge": "",
        "plan_refs": {"beats": list(refs), "lines": []},
        "keyframes": list(keyframes or []),
    }


def _script(chain, shots, beats, events=None):
    return {
        "chapter_outline": {
            "causal_chain": list(chain),
            "shot_beats": list(beats),
            "dialogue_lines": [],
            "main_events": list(events or []),
            "required_facts": [],
            "fact_uids": [],
        },
        "shots": shots,
        "total_duration": sum(s["duration_sec"] for s in shots),
    }


def _sample_old() -> dict:
    beats = ["节拍：守卫封锁城门", "节拍：旅人进入城门"]
    shots = [_shot(1, ff="城门前，守卫横矛封锁", lf="旅人已经进入城门",
                   motion="守卫横矛", refs=[1, 2])]
    # 旧稿：因果条目是改写过的概述，既不与节拍正文完全相等，也没有编号引用。
    return _script(["起因：守卫封锁了城门", "结果：旅人进了城门"],
                   shots, beats, ["守卫封锁城门", "旅人进入城门"])


def _sample_new() -> dict:
    beats = ["节拍：守卫封锁城门", "节拍：旅人进入城门"]
    shots = [_shot(1, ff="城门前，守卫横矛封锁", lf="旅人已经进入城门",
                   motion="守卫横矛", refs=[1, 2])]
    return _script(["起因：守卫封锁城门（B1）", "结果：旅人进入城门（B2）"],
                   shots, beats, ["守卫封锁城门", "旅人进入城门"])


def _sample_new_plus_bird() -> dict:
    beats = ["节拍：守卫封锁城门", "节拍：旅人进入城门",
             "节拍：鸟从城头飞过"]
    shots = [
        _shot(1, ff="城门前，守卫横矛封锁", lf="旅人已经进入城门",
              motion="守卫横矛", refs=[1, 2]),
        _shot(2, ff="城头上方，鸟展翅", lf="鸟飞远",
              motion="鸟展翅飞走", refs=[3],
              keyframes=[{"time_sec": 1.0, "desc": "鸟展翅"},
                         {"time_sec": 3.0, "desc": "鸟飞远"}]),
    ]
    return _script(["起因：守卫封锁城门（B1）", "结果：旅人进入城门（B2）"],
                   shots, beats, ["守卫封锁城门", "旅人进入城门"])


def _verdict(sample: dict) -> dict:
    findings = check_script_semantics(sample)
    gap = next((f for f in findings if f.get("code") == "causal_gap"), {})
    return {
        "codes": codes_of(findings),
        "relation_status": gap.get("relation_status"),
        "relation_note": gap.get("relation_note"),
        "relation_units": gap.get("relation_units"),
        "relation_carriers": gap.get("relation_carriers"),
        "carrier_evidence": gap.get("carrier_evidence"),
        "needs_semantic_review": gap.get("needs_semantic_review"),
    }


def build_report() -> dict:
    old = _verdict(_sample_old())
    new = _verdict(_sample_new())
    bird = _verdict(_sample_new_plus_bird())
    return {
        "schema": "s03.identity_shadow_diff.v1",
        "purpose": ("旧稿无身份 vs 新稿带身份：准入差异只记录，不改变默认门禁；"
                    "正式采纳影响按既定裁决处理"),
        "samples": {
            "old_no_identity": old,
            "new_identity": new,
            "new_identity_plus_bird": bird,
        },
        "admission_delta": {
            "old_no_identity_time_ellipsis_lost": (
                "time_ellipsis_lost" in old["codes"]),
            "new_identity_time_ellipsis_lost": (
                "time_ellipsis_lost" in new["codes"]),
            "old_plan_formal_admission": (
                "不变（只出 causal_gap advisory；time_ellipsis_lost 未触发，"
                "不新增拒收）"),
            "bird_beat_changes_verdict": bird["codes"] != new["codes"],
            "bird_in_relation_carriers": (
                "shot:2" in (bird.get("relation_carriers") or [])),
        },
    }


def _markdown(report: dict) -> str:
    lines = [
        "# S03 身份影子差异（旧稿 vs 新稿）",
        "",
        "> 生成：`python tools/build_s03_identity_shadow_diff.py`（离线、零模型调用）",
        "> 检查器：`shared.execution_semantics.check_script_semantics`（真实函数）",
        "> 本记录**不改变**默认门禁；旧稿的实际准入影响按既定裁决处理。",
        "",
        "| 版本 | codes | relation_status | relation_note | 关系承载 | 过程证据 | needs_semantic_review |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, item in report["samples"].items():
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s |" % (
                name, "、".join(item["codes"]) or "（空）",
                item.get("relation_status") or "-",
                item.get("relation_note") or "-",
                "、".join(item.get("relation_carriers") or []) or "-",
                "、".join(item.get("carrier_evidence") or []) or "无",
                item.get("needs_semantic_review")))
    delta = report["admission_delta"]
    lines += [
        "",
        "## 准入差异（记录，不自动改变门禁）",
        "",
        "- 旧稿（无身份）：`time_ellipsis_lost`=%s；只出 `causal_gap` advisory，"
        "**不新增拒收**——该项未完成语义判定。" % delta[
            "old_no_identity_time_ellipsis_lost"],
        "- 新稿（带身份）：`time_ellipsis_lost`=%s——过程/时间证据缺失时按关系范围拒收。"
        % delta["new_identity_time_ellipsis_lost"],
        "- 鸟节拍（真实 B3 + 两关键帧）：改变裁决=%s；鸟镜进入关系承载=%s。"
        % (delta["bird_beat_changes_verdict"],
           delta["bird_in_relation_carriers"]),
        "",
        "## 结论",
        "",
        "身份生产把「无法定位关系」与「关系已定位但过程/时间缺失」分开：前者进入"
        "影子差异并标 `needs_semantic_review`，后者才触发 `time_ellipsis_lost`。"
        "旧稿在正式门禁上的最终影响仍需按既定决策在提交/影子评估中处理，"
        "本文件只提供可复核读数。",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="docs/step01-layered-execution/artifacts/s03",
        help="输出目录（默认 docs/.../artifacts/s03）")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report()
    (out_dir / "identity_shadow_diff.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "identity_shadow_diff.md").write_text(
        _markdown(report), encoding="utf-8")
    print(json.dumps(report["admission_delta"], ensure_ascii=False, indent=2))
    print("out:", str(out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
