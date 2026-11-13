# -*- coding: utf-8 -*-
"""S06（G1/G2/G6）决策样本：关闭证据 / 景别跨度 / 声音两态的行为影响.

只读、离线、零模型调用。样本都用**真实函数**：

- G1：`step01_script_gen.issue_ledger.build_issue_ledger`
- G2：`step01_script_gen.constraints.framing_jump.FramingJumpConstraint`
  与 `step01_script_gen.validation._check_shot_size_jump`
- G6：`shared.voice_preparation.build_voice_preparation` 与
  `shared.execution_semantics.check_script_semantics`

用法：
    python tools/build_s06_decision_samples.py [--out docs/step01-layered-execution/artifacts/s06]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.execution_semantics import check_script_semantics, codes_of
from shared.voice_preparation import build_voice_preparation
from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.framing_jump import FramingJumpConstraint
from step01_script_gen.issue_ledger import build_issue_ledger
from step01_script_gen.validation import _check_shot_size_jump


# ---------------------------------------------------------------------------
# G1：已确认问题「本轮未报出」不等于「有证据关闭」
# ---------------------------------------------------------------------------
def g1_samples() -> dict:
    confirmed = [{
        "id": "shot1.ff_desc.unsourced",
        "shot_id": 1,
        "field_path": "ff_desc",
        "code": "UNSOURCED_CONTENT",
        "severity": "hard",
        "evidence": "窄身机甲",
        "disposition": "confirmed",
    }]
    silent = build_issue_ledger(
        confirmed, [], candidate_revision="rev-silent")
    with_evidence = build_issue_ledger(
        confirmed, [], candidate_revision="rev-evidence",
        fixed_evidence={"shot1.ff_desc.unsourced": {
            "kind": "deterministic_probe",
            "detail": "窄身机甲 已不在 ff_desc",
            "candidate_revision": "rev-evidence",
        }})

    def _row(ledger: dict) -> dict:
        row = ledger["rows"][0]
        return {
            "status": row["status"],
            "closed": row["closed"],
            "close_basis": row.get("close_basis"),
            "note": row.get("note", ""),
            "summary": ledger["summary"],
        }

    silent_row = _row(silent)
    evidence_row = _row(with_evidence)
    # 现行提交门：resolved = before_keys - after_keys（main.py:6672）。
    # 「本轮没报出」⇒ 该 key 从 after 消失 ⇒ 现行门把它算作 1 条 resolved。
    # ⚠️ 证据层级：下面两列是**按规则推演**（台账状态 + 关闭规则），**没有**走真实
    # 提交入口；真实提交点的最终结果仍需在提交链上验证。生成器不把推演写成实测。
    return {
        "id": "G1_issue_closed",
        "question": "issue_ledger.closed 是否接进提交门（阻塞问题必须有证据关闭）",
        "evidence_level": {
            "ledger": "actual_function:build_issue_ledger",
            "gate_verdicts": "deduced_from_rule:未走真实提交入口",
        },
        "samples": [
            {
                "id": "G1_a_silent",
                "label": "本轮审计没有再次报出（模型沉默）",
                "ledger": silent_row,
                "current_gate_resolved": 1,
                "G1_gate_resolved": 1 if silent_row["closed"] else 0,
                "current_gate_verdict": "推演 accept（把沉默读成已解决）",
                "G1_gate_verdict": "推演 reject（无关闭证据）",
            },
            {
                "id": "G1_b_evidence",
                "label": "有确定性复核证据关闭",
                "ledger": evidence_row,
                "current_gate_resolved": 1,
                "G1_gate_resolved": 1 if evidence_row["closed"] else 0,
                "current_gate_verdict": "推演 accept",
                "G1_gate_verdict": "推演 accept",
            },
        ],
        "recommendation": (
            "只对**阻塞级**确认问题要求 closed=True；非阻塞条目继续只记账。"
            "样本 G1_a 是唯一行为差异：按规则推演为现行门接受、G1 门拒绝；"
            "真实提交入口的最终结果尚未验证（见 evidence_level）。"),
    }


# ---------------------------------------------------------------------------
# G2：景别跨度 > 2 级时，后处理改写前镜景别 vs 只报 warning
# ---------------------------------------------------------------------------
def _shot(index: int, prompt: str, location: str = "门内") -> dict:
    return {
        "index": index,
        "duration_sec": 3.0,
        "location": location,
        "scene": location,
        "main_subject": "甲",
        "prompt": prompt,
        "ff_desc": prompt,
        "lf_desc": prompt,
        "motion_desc": "前1秒：静止；后1秒：静止。",
        "narration_segment": "",
    }


def g2_samples() -> dict:
    script = {"shots": [
        _shot(1, "[大特写] 甲的手指按在门闩上"),
        _shot(2, "[远景] 山谷与长河"),
    ], "characters": []}
    # warning 判据要看**改写前**的稿件（改写后跨度已被抹平，不能再证明它报过）。
    warnings = _check_shot_size_jump(
        [dict(s) for s in script["shots"]])["warnings"]
    before = script["shots"][0]["prompt"]
    repairs = FramingJumpConstraint().apply(ConstraintContext.from_script(script))
    after = script["shots"][0]["prompt"]
    return {
        "id": "G2_framing_jump",
        "question": "景别跨度 > 2 级时，后处理是否继续自动改写前镜景别",
        "sample": {
            "shots": [
                {"index": 1, "prompt_before": before, "prompt_after": after},
                {"index": 2, "prompt": script["shots"][1]["prompt"]},
            ],
            "constraint_repairs": [
                {"shot": r.shot_index, "before": r.before, "after": r.after}
                for r in repairs
            ],
            "validation_warnings": warnings,
        },
        "options": [
            {
                "id": "G2_a_rewrite",
                "label": "保留自动改写（现状）",
                "behavior": "前镜「大特写」被改成「全景」；audit 不再收到这条跨度提示",
                "risk": "用户/审计刻意选择的近景被后处理改掉；与修复轮已授权镜头冲突",
            },
            {
                "id": "G2_b_warn_only",
                "label": "只报 warning，不改写",
                "behavior": "前镜保持「大特写」；validation 仍报 6 级跨度，交审计/人工决定",
                "risk": "跨度可能留在成片里；需要审计或人工接住这条 warning",
            },
        ],
        "recommendation": (
            "G2_b：`_check_shot_size_jump` 已经产出同一条 warning，"
            "后处理不再二次改写；先做影子对照再决定是否保留自动改写。"),
    }


def build() -> dict:
    return {
        "schema": "s06.decision_samples.v1",
        "note": "G1/G2/G6 各真实函数样本；不读盘、不调模型。",
        "g1": g1_samples(),
        "g2": g2_samples(),
        "g6": g6_samples(),
    }


def g6_samples() -> dict:
    """G6：声音「准备态/视频后确认态」未拆分时的实际消费结果.

    样本：一条独立对白声明 5.0s，落在 4.0s 的镜头里；**没有音轨**（未实测）。
    `build_voice_preparation` 把声明值写进 `actual_sec`、`verified=False`；
    `check_script_semantics` 只看 `actual_sec`，于是按「实测超窗」拒收。
    """
    script = {
        "chapter_index": 1,
        "characters": [{"identifier": "甲"}],
        "shots": [{
            "index": 1, "duration_sec": 4.0, "location": "渡口", "scene": "渡口",
            "main_subject": "甲", "prompt": "甲站在渡口。", "ff_desc": "甲站在渡口。",
            "lf_desc": "甲停下。", "motion_desc": "前2秒：走；后2秒：停。",
            "narration_segment": "",
        }],
        "chapter_outline": {
            "dialogue_lines": ["走吧。"],
            "narration_lines": [],
            "voice_units": [{
                "unit_id": "v1", "kind": "tts_dialogue", "speaker": "甲",
                "text": "走吧。", "shot_index": 1, "actual_sec": 5.0,
                "window_sec": 4.0,
            }],
        },
    }
    prep = build_voice_preparation(script, scope={"chapter": 1})
    unit = prep["units"][0]
    findings = check_script_semantics(script)
    return {
        "id": "G6_voice_two_states",
        "question": "声音是否拆「准备态（声明）/ 视频后确认态（实测）」两态",
        "sample": {
            "declared_sec": unit.get("declared_sec"),
            "actual_sec": unit.get("actual_sec"),
            "verified": unit.get("verified"),
            "measurement": unit.get("measurement"),
            "shot_window_sec": unit.get("shot_audio_window_sec"),
            "semantic_codes": codes_of(findings),
            "semantic_findings": [
                {"code": f.get("code"), "severity": f.get("severity"),
                 "message": f.get("message")}
                for f in findings
            ],
        },
        "options": [
            {
                "id": "G6_a_single_state",
                "label": "维持单态（现状）",
                "behavior": "声明值写进 actual_sec；消费方若不看 verified，会把声明当实测"
                            "（本样本直接产出 audio_window_overflow/reject）",
                "risk": "未实测的内容按实测拒收；准备阶段的超窗与成片超窗混在一起",
            },
            {
                "id": "G6_b_two_states",
                "label": "拆准备态/确认态",
                "behavior": "准备态只写 declared_sec 与窗口预算；确认态只在 verified=True 时"
                            "写 actual_sec，消费方按状态路由",
                "risk": "需要改 audio_policy / voice_preparation / execution_semantics 的消费方",
            },
        ],
        "recommendation": (
            "G6_b：准备态允许「计划超窗」作为规划问题报告，确认态才按实测时长拒收；"
            "拆分后 `execution_semantics` 只消费 verified=True 的 actual_sec。"),
    }


def _markdown(report: dict) -> str:
    g1 = report["g1"]
    g2 = report["g2"]
    lines = [
        "# S06（G1/G2）决策样本",
        "",
        "> 生成：`python tools/build_s06_g1_g2_samples.py`（离线、零模型调用）",
        "",
        "## G1：`issue_ledger.closed` 接提交门",
        "",
        f"裁决问题：{g1['question']}",
        "",
        f"证据层级：{json.dumps(g1.get('evidence_level') or {}, ensure_ascii=False)}",
        "",
        "| 样本 | 台账状态 | closed | 现行门 resolved（推演） | G1 门 resolved（推演） | 现行门（推演） | G1 门（推演） |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in g1["samples"]:
        lines.append(
            f"| {s['label']} | {s['ledger']['status']} | {s['ledger']['closed']} | "
            f"{s['current_gate_resolved']} | {s['G1_gate_resolved']} | "
            f"{s['current_gate_verdict']} | {s['G1_gate_verdict']} |")
    lines += [
        "",
        f"建议：{g1['recommendation']}",
        "",
        "## G2：景别跨度 > 2 级的自动改写",
        "",
        f"裁决问题：{g2['question']}",
        "",
        f"- 样本：镜 1 `{g2['sample']['shots'][0]['prompt_before']}` → "
        f"`{g2['sample']['shots'][0]['prompt_after']}`；"
        f"镜 2 `{g2['sample']['shots'][1]['prompt']}`",
        f"- 约束改写：{json.dumps(g2['sample']['constraint_repairs'], ensure_ascii=False)}",
        f"- 既有 warning：{json.dumps(g2['sample']['validation_warnings'], ensure_ascii=False)}",
        "",
        "| 选项 | 行为 | 风险 |",
        "|---|---|---|",
    ]
    for o in g2["options"]:
        lines.append(f"| {o['label']} | {o['behavior']} | {o['risk']} |")
    lines += ["", f"建议：{g2['recommendation']}", ""]
    g6 = report["g6"]
    sample = g6["sample"]
    lines += [
        "## G6：声音「准备态 / 视频后确认态」",
        "",
        f"裁决问题：{g6['question']}",
        "",
        f"- 样本：一条独立对白声明 `{sample['declared_sec']}s`，镜头窗口 "
        f"`{sample['shot_window_sec']}s`，**没有音轨**（未实测）。",
        f"- `build_voice_preparation` 产出：`actual_sec={sample['actual_sec']}`，"
        f"`verified={sample['verified']}`，`measurement={sample['measurement']!r}`。",
        f"- `check_script_semantics` 产出：`{sample['semantic_codes']}`"
        f"（{'；'.join(f['message'] for f in sample['semantic_findings'])}）。",
        "",
        "| 选项 | 行为 | 风险 |",
        "|---|---|---|",
    ]
    for o in g6["options"]:
        lines.append(f"| {o['label']} | {o['behavior']} | {o['risk']} |")
    lines += ["", f"建议：{g6['recommendation']}", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="docs/step01-layered-execution/artifacts/s06")
    args = parser.parse_args(argv)
    report = build()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "g1_g2_g6_decision_samples.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "g1_g2_g6_decision_samples.md").write_text(
        _markdown(report), encoding="utf-8")
    print(json.dumps({
        "g1": [(s["ledger"]["status"], s["ledger"]["closed"],
                s["current_gate_resolved"], s["G1_gate_resolved"])
               for s in report["g1"]["samples"]],
        "g2_before": report["g2"]["sample"]["shots"][0]["prompt_before"],
        "g2_after": report["g2"]["sample"]["shots"][0]["prompt_after"],
        "g2_warnings": len(report["g2"]["sample"]["validation_warnings"]),
        "g6_codes": report["g6"]["sample"]["semantic_codes"],
        "g6_actual_sec": report["g6"]["sample"]["actual_sec"],
        "g6_verified": report["g6"]["sample"]["verified"],
        "out": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
