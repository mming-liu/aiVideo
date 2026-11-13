# -*- coding: utf-8 -*-
"""S06（G7）决策样本：等价派生 / 目标丢失 / 保护违规的**具体结果**.

2026-11-13 复核要求：G7 的裁决材料不能只说「按残余接门会拒收等价派生」。
本工具用三个匿名、确定性样本，分别算出：

- `postprocess_reverted`（`main._authorized_changes_lost`，**真实**记账函数）；
- 目标是否达成（`main._audit_issue_resolved`，**真实**判据）；
- 保护字段是否被改（`main._locked_repair_signature`，**真实**轮级锁定契约）；

再对四种候选门给出**实际裁决**：

| 候选 | 规则 |
|---|---|
| 现状：只记账 | 不因 `postprocess_reverted` 拒绝 |
| R1：任何残余都拒 | `postprocess_reverted` 非空 → 拒 |
| R2：仅目标丢失拒 | 合并后目标已达成、后处理后未达成 → 拒 |
| R2 + 既有保护门 | R2 或保护字段变化 → 拒 |

只读、离线、零模型调用；样本是内存快照，不读写生产产物。

用法：
    python tools/build_s06_g7_samples.py [--out docs/step01-layered-execution/artifacts/s06]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.constraints.v6_43_prompt_enforcement import _fix_vague_anchor
from step01_script_gen.main import (
    _audit_issue_resolved,
    _authorized_changes_lost,
    _locked_repair_signature,
)
from step01_script_gen.quality_issues import Issue


def _shot(**overrides) -> dict:
    shot = {
        "index": 1,
        "duration_sec": 4.0,
        "location": "渡口",
        "scene": "渡口",
        "main_subject": "甲",
        "prompt": "",
        "ff_desc": "甲立在渡口。",
        "lf_desc": "甲停下。",
        "motion_desc": "前2秒：走；后2秒：停。",
        "narration_segment": "甲走过石阶。",
        "subtitle": "",
        "variation_type": "",
    }
    shot.update(overrides)
    return shot


def _script(shot: dict) -> dict:
    return {"shots": [copy.deepcopy(shot)], "hook": None}


def _issue(field_path: str, evidence: str) -> Issue:
    return Issue(
        code="UNSOURCED_CONTENT",
        severity="hard",
        message="后处理不得编造/恢复无来源内容",
        shot_id=1,
        field_path=field_path,
        evidence=evidence,
    )


def _equivalent_derivation() -> dict:
    """A. 等价派生：模型删掉无来源的「约 2cm」，后处理只补否定说明.

    后处理改的是**同一授权路径**（`prompt`），但目标（删掉 2cm 编造）仍然达成。
    """
    merged = _shot(prompt="刺客潜伏水下，仅露双眼。")
    after = copy.deepcopy(merged)
    _fix_vague_anchor(after)  # 真实后处理：补「（不是上半身出水）」
    return {
        "id": "A_equivalent_derivation",
        "label": "等价派生：授权路径被后处理改写，但修复目标仍达成",
        "producer": "step01_script_gen.constraints.v6_43_prompt_enforcement._fix_vague_anchor",
        "issue": _issue("prompt", "仅头顶约 2cm 露出水面"),
        "allowed_by_target": {"1": {"prompt"}},
        "merged": _script(merged),
        "after": _script(after),
        "expected": "accept",
    }


def _target_loss() -> dict:
    """B. 目标丢失：模型删掉「窄身」，后处理把修前值写回.

    `after` 是**受控样本**（修前快照 = 被回填的值），用于验证「目标丢失」判据；
    生产者标为 synthetic，不冒充某条具体约束。
    """
    merged = _shot(ff_desc="机甲立在废墟。")
    after = _shot(ff_desc="窄身机甲立在废墟。")
    return {
        "id": "B_target_loss",
        "label": "目标丢失：授权删除被后处理改回修前值",
        "producer": "synthetic revert（修前快照回填；用于验证目标丢失判据）",
        "issue": _issue("ff_desc", "窄身机甲立在废墟"),
        "allowed_by_target": {"1": {"ff_desc"}},
        "merged": _script(merged),
        "after": _script(after),
        "expected": "reject",
    }


def _protection_violation() -> dict:
    """C. 保护违规：授权路径没变，后处理改了锁定的 `narration_segment`."""
    merged = _shot(prompt="甲快步走过石阶。", narration_segment="甲走过石阶。")
    after = _shot(prompt="甲快步走过石阶。", narration_segment="甲走过长阶。")
    return {
        "id": "C_protection_violation",
        "label": "保护违规：后处理改了锁定字段（旁白），授权路径未变",
        "producer": "synthetic locked-field change（用于验证保护门；正式链路由 locked_changed 拦）",
        "issue": _issue("prompt", "甲慢慢走过长石阶"),
        "allowed_by_target": {"1": {"prompt"}},
        "merged": _script(merged),
        "after": _script(after),
        "expected": "reject",
    }


_SAMPLES = (_equivalent_derivation, _target_loss, _protection_violation)


def build() -> dict:
    rows = []
    for factory in _SAMPLES:
        sample = factory()
        record = _authorized_changes_lost(
            sample["merged"], sample["after"], sample["allowed_by_target"])
        merged_ok = _audit_issue_resolved(sample["merged"], sample["issue"])
        after_ok = _audit_issue_resolved(sample["after"], sample["issue"])
        locked_changed = (
            _locked_repair_signature(sample["merged"])
            != _locked_repair_signature(sample["after"]))
        target_lost = bool(merged_ok and not after_ok)
        verdicts = {
            "current_record_only": False,
            "R1_any_residual": bool(record),
            "R2_target_loss": target_lost,
            "R2_plus_protection": bool(target_lost or locked_changed),
        }
        rows.append({
            "id": sample["id"],
            "label": sample["label"],
            "producer": sample["producer"],
            "expected": sample["expected"],
            "merged_text": {
                k: sample["merged"]["shots"][0].get(k)
                for k in ("prompt", "ff_desc", "narration_segment")
            },
            "after_text": {
                k: sample["after"]["shots"][0].get(k)
                for k in ("prompt", "ff_desc", "narration_segment")
            },
            "postprocess_reverted": record,
            "target_achieved_in_merged": merged_ok,
            "target_achieved_after": after_ok,
            "target_lost": target_lost,
            "locked_field_changed": locked_changed,
            "verdicts": verdicts,
        })
    return {
        "schema": "s06.g7_decision_samples.v1",
        "note": "三个匿名受控样本；记账/目标/保护三类判据均为真实函数；不读盘、不调模型。",
        "rows": rows,
        "candidate_rules": {
            "current_record_only": "不因 postprocess_reverted 拒绝（现状）",
            "R1_any_residual": "postprocess_reverted 非空 → 拒绝",
            "R2_target_loss": "合并后目标已达成、后处理后未达成 → 拒绝",
            "R2_plus_protection": "R2 或 _locked_repair_signature 变化 → 拒绝",
        },
    }


def _markdown(report: dict) -> str:
    lines = [
        "# S06（G7）决策样本：等价派生 / 目标丢失 / 保护违规",
        "",
        "> 生成：`python tools/build_s06_g7_samples.py`（离线、零模型调用）",
        "> 记账=`main._authorized_changes_lost`；目标=`main._audit_issue_resolved`；",
        "> 保护=`main._locked_repair_signature`。三个样本均为匿名受控快照。",
        "",
        "| 样本 | postprocess_reverted | 目标丢失 | 保护字段变化 | 现状 | R1 任何残余 | R2 目标丢失 | R2+保护 | 应然 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        rec = row["postprocess_reverted"]
        rec_txt = "、".join(f"{tid}:{','.join(paths)}" for tid, paths in rec.items()) or "空"
        v = row["verdicts"]

        def _cell(flag: bool) -> str:
            return "拒" if flag else "过"

        lines.append(
            f"| {row['id']} | {rec_txt} | {_cell(row['target_lost'])} | "
            f"{_cell(row['locked_field_changed'])} | {_cell(v['current_record_only'])} | "
            f"{_cell(v['R1_any_residual'])} | {_cell(v['R2_target_loss'])} | "
            f"{_cell(v['R2_plus_protection'])} | {row['expected']} |")
    lines += [
        "",
        "## 逐样本结果",
        "",
    ]
    for row in report["rows"]:
        lines += [
            f"### {row['id']}",
            "",
            f"- 说明：{row['label']}",
            f"- 生产者：{row['producer']}",
            f"- 合并后：`prompt={row['merged_text']['prompt']!r}`，"
            f"`ff_desc={row['merged_text']['ff_desc']!r}`，"
            f"`narration_segment={row['merged_text']['narration_segment']!r}`",
            f"- 后处理后：`prompt={row['after_text']['prompt']!r}`，"
            f"`ff_desc={row['after_text']['ff_desc']!r}`，"
            f"`narration_segment={row['after_text']['narration_segment']!r}`",
            f"- 记账：`postprocess_reverted={json.dumps(row['postprocess_reverted'], ensure_ascii=False)}`",
            f"- 目标：合并后达成={row['target_achieved_in_merged']}，"
            f"后处理后达成={row['target_achieved_after']} → 丢失={row['target_lost']}",
            f"- 保护字段变化={row['locked_field_changed']}",
            f"- 应然：{row['expected']}",
            "",
        ]
    lines += [
        "## 结论（供裁决）",
        "",
        "- **现状（只记账）**：B 的目标丢失不会被拒收——记账存在，但不影响准入。",
        "- **R1（任何残余都拒）**：A 的等价派生被误拒——授权路径被后处理改写不等于目标丢失。",
        "- **R2（仅目标丢失拒）**：A 过、B 拒；但 C 的保护违规不在授权路径上，"
        "`postprocess_reverted` 为空，需要既有 `locked_changed` 保护门单独拦截。",
        "- **R2 + 既有保护门**：三个样本均符合应然结果。",
        "",
        "因此 G7 的裁决对象应写成「**按目标丢失接门，并保留既有保护门**」，"
        "而不是「按任何残余接门」。",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="docs/step01-layered-execution/artifacts/s06",
        help="输出目录（默认 docs/.../artifacts/s06）")
    args = parser.parse_args(argv)
    report = build()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "g7_decision_samples.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "g7_decision_samples.md").write_text(_markdown(report), encoding="utf-8")
    ok = all(
        (row["verdicts"]["R2_plus_protection"] is (row["expected"] == "reject"))
        for row in report["rows"])
    print(json.dumps({
        "rows": [{"id": r["id"], "record": r["postprocess_reverted"],
                  "target_lost": r["target_lost"],
                  "locked_changed": r["locked_field_changed"],
                  "verdicts": r["verdicts"], "expected": r["expected"]}
                 for r in report["rows"]],
        "R2_plus_protection_matches_expected": ok,
        "out": str(out),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
