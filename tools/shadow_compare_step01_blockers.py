# -*- coding: utf-8 -*-
"""S07：同一冻结输入上的「现行 Step01 结果 vs 新责任分配」影子比较.

不执行正式门禁切换、不采纳正式产物、不发起任何模型/媒体调用。输出：

- ``shadow_compare.json``：每条阻断的现行严重度、新责任阶段、下游接收证据与终局；
- ``shadow_compare.md``：可直接评审的对照表。

运行：

    python tools/shadow_compare_step01_blockers.py --out .pytest_tmp/s07
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.execution_semantics import codes_of, check_execution_semantics
from shared.voice_preparation import build_voice_preparation
from step02a_frame_gen.execution_plan import build_visual_plan

FIXTURES = _ROOT / "tests" / "shared" / "fixtures" / "execution_contract"

#: 现行 Step01 阻断（来自 S01 责任表）及新责任分配。severity 是**现行**严重度，
#: 不是本包建议值；本包只产出清单，不切换。
BLOCKERS = [
    {
        "id": "D1",
        "name": "正片总时长低于下限",
        "current": {"owner": "step01", "gate": "commit BLOCK", "code": "duration_budget",
                    "severity": "blocking"},
        "new": {"owner": "S05/S06", "receiver": "voice_preparation / timeline_acceptance",
                "route": "s06_duration_feedback", "step01_block": False},
        "legal_positive": "合法短稿不靠下游补造内容；S06 报差额，内容确需变化才回 Step01",
        "defect_counterexample": "S06 `timeline_duration_short` 报 delta 与受影响 clip",
    },
    {
        "id": "D2",
        "name": "正片总时长超过上限",
        "current": {"owner": "step01", "gate": "commit BLOCK", "code": "duration_budget",
                    "severity": "blocking"},
        "new": {"owner": "S05/S06", "receiver": "timeline_acceptance",
                "route": "s06_duration_feedback", "step01_block": False},
        "legal_positive": "合法长稿由 Step01 决定精简/合并；执行阶段不静默截断",
        "defect_counterexample": "S06 `timeline_duration_long` 报差额与受影响 clip",
    },
    {
        "id": "D3",
        "name": "红果时长标准告警",
        "current": {"owner": "step01", "gate": "advisory warning", "code": "redguo_standard",
                    "severity": "advisory"},
        "new": {"owner": "S06", "receiver": "timeline_acceptance",
                "route": "advisory", "step01_block": False},
        "legal_positive": "只作提示，不与 D1/D2 双算为阻塞",
        "defect_counterexample": "同一时长样本只有一条阻塞权威",
    },
    {
        "id": "S-01",
        "name": "结果缺因果/过程（causal_gap）",
        "current": {"owner": "step01", "gate": "audit blocking（语义审计）",
                    "code": "causal_gap", "severity": "blocking"},
        "new": {"owner": "S03", "receiver": "build_visual_plan / check_execution_semantics",
                "route": "candidate_failed + step01_feedback", "step01_block": True},
        "legal_positive": "合法时间省略 + 过程载体：`temporal_ellipsis` 基线 findings=[]",
        "defect_counterexample": "变异被自动检出 `causal_gap`，候选 blocked 并回 Step01",
    },
    {
        "id": "S-02",
        "name": "时间省略丢失（time_ellipsis_lost）",
        "current": {"owner": "step01", "gate": "audit blocking（语义审计）",
                    "code": "time_ellipsis_lost", "severity": "blocking"},
        "new": {"owner": "S03", "receiver": "check_execution_semantics",
                "route": "candidate_failed + step01_feedback", "step01_block": True},
        "legal_positive": "基线有“三日后”与过程动作；findings=[]",
        "defect_counterexample": "变异同时被检出 `causal_gap` 与 `time_ellipsis_lost`",
    },
    {
        "id": "S-03",
        "name": "画外声音错误出镜（offscreen_speaker_visible）",
        "current": {"owner": "step01", "gate": "无执行级检查（语义审计不可见）",
                    "code": "offscreen_speaker_visible", "severity": "not_detected"},
        "new": {"owner": "S03", "receiver": "build_visual_plan / validate_execution_changes",
                "route": "candidate_failed + step01_feedback", "step01_block": True},
        "legal_positive": "基线画外说话人不在可见布局；findings=[]",
        "defect_counterexample": "变异 `on_screen=true` + 布局出镜被自动检出；"
                                 "`prompt`/`visible_characters` 不再能绕过",
    },
    {
        "id": "S-04",
        "name": "听者反应缺失（listener_reaction_missing）",
        "current": {"owner": "step01", "gate": "无执行级检查",
                    "code": "listener_reaction_missing", "severity": "not_detected"},
        "new": {"owner": "S03", "receiver": "check_execution_semantics",
                "route": "candidate_failed + step01_feedback", "step01_block": True},
        "legal_positive": "基线 ff→lf 布局变化/动作反应；findings=[]",
        "defect_counterexample": "变异移除反应后自动检出",
    },
    {
        "id": "S-05",
        "name": "独立声音超窗口（audio_window_overflow）",
        "current": {"owner": "step01", "gate": "无实测（计划时长不冒充实测）",
                    "code": "audio_window_overflow", "severity": "not_detected"},
        "new": {"owner": "S05/S06", "receiver": "voice_preparation / timeline_acceptance",
                "route": "voice_execution_feedback", "step01_block": False},
        "legal_positive": "基线 1.8+1.2 <= 4.0；findings=[]",
        "defect_counterexample": "变异 3.4+1.2 > 4.0，报 unit_id/shot_index/overflow_sec",
    },
    {
        "id": "S-06",
        "name": "原生对白未核实（native_dialogue_unverified）",
        "current": {"owner": "step01", "gate": "无实测",
                    "code": "native_dialogue_unverified", "severity": "not_detected"},
        "new": {"owner": "S05", "receiver": "voice_preparation",
                "route": "voice_execution_feedback", "step01_block": False},
        "legal_positive": "基线 verified=true 且有实测时长",
        "defect_counterexample": "变异 verified=false，明确记未验证，不凭音轨存在判通过",
    },
]


def _family(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _eval_family(name: str) -> dict:
    family = _family(name)
    out = {"family": name, "scope": family["scope"], "sides": {}}
    for side in ("baseline", "mutation"):
        script = family[side]["script"]
        candidate = family[side]["execution"]["candidate"]
        semantic = check_execution_semantics(script, candidate)
        plan = build_visual_plan(script, scope=family["scope"],
                                 only_indices={s["index"] for s in script["shots"]},
                                 candidate=candidate)
        windows = {str(s["index"]): float(s.get("duration_sec") or 0)
                   for s in script["shots"]}
        voice = build_voice_preparation(script, scope=family["scope"], windows=windows)
        out["sides"][side] = {
            "expected": family[side]["expected"]["verdict"],
            "semantic_codes": codes_of(semantic),
            "visual_plan_status": plan.get("status"),
            "visual_plan_feedback": plan.get("feedback"),
            "voice_status": voice.get("status"),
            "voice_codes": voice.get("codes"),
        }
    return out


def build_report(out: Path) -> dict:
    families = {name: _eval_family(name) for name in
                ("temporal_ellipsis", "offscreen_voice", "voice_timing")}
    rows = []
    for blocker in BLOCKERS:
        received = []
        for family, data in families.items():
            if family == "temporal_ellipsis" and blocker["id"] in ("S-01", "S-02"):
                received.append({"family": family,
                                 "mutation_codes": data["sides"]["mutation"]["semantic_codes"],
                                 "receiver": data["sides"]["mutation"]["visual_plan_status"]})
            if family == "offscreen_voice" and blocker["id"] in ("S-03", "S-04"):
                received.append({"family": family,
                                 "mutation_codes": data["sides"]["mutation"]["semantic_codes"],
                                 "receiver": data["sides"]["mutation"]["visual_plan_status"]})
            if family == "voice_timing" and blocker["id"] in ("S-05", "S-06"):
                received.append({"family": family,
                                 "mutation_codes": data["sides"]["mutation"]["voice_codes"],
                                 "receiver": data["sides"]["mutation"]["voice_status"]})
        row = dict(blocker)
        row["downstream_receipts"] = received
        row["received"] = bool(received) if blocker["id"].startswith("S-") else None
        row["final_disposition"] = (
            "downstream_detected_and_routed" if received
            else "checklist_only_no_synthetic_input" if blocker["id"].startswith("D")
            else "not_received")
        rows.append(row)
    report = {
        "schema": "step01_shadow_compare.v1",
        "note": "同一冻结夹具输入；现行 Step01 结果按 S01 责任表记录，不冒充真实运行；"
                "新责任分配由 S02/S03/S05 的离线检查器计算。",
        "families": families,
        "blockers": rows,
        "summary": {
            # 语义/画面基线三族都必须是绿的；声音检查只对带独立声音单元的
            # voice_timing 族适用（其它族允许原生对白/无独立音轨）。
            "baseline_all_green": all(
                not data["sides"]["baseline"]["semantic_codes"]
                and data["sides"]["baseline"]["visual_plan_status"] == "planned"
                for data in families.values())
            and families["voice_timing"]["sides"]["baseline"]["voice_status"] == "ready",
            "mutation_all_detected": all(
                data["sides"]["mutation"]["visual_plan_status"] == "blocked"
                for name, data in families.items() if name != "voice_timing"),
            "voice_mutation_detected": families["voice_timing"]["sides"]["mutation"][
                "voice_codes"],
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "shadow_compare.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, out / "shadow_compare.md")
    return report


def _write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# S07 影子比较（同一冻结输入）",
        "",
        "> 只产出对照与清单，不执行正式门禁切换、不采纳正式产物。",
        "",
        "| 阻断 | 现行责任/严重度 | 新责任 | 下游接收 | 终局 |",
        "|---|---|---|---|---|",
    ]
    for row in report["blockers"]:
        receipts = ", ".join(
            f"{r['family']}:{r['mutation_codes']}@{r['receiver']}"
            for r in row.get("downstream_receipts") or []) or "—"
        lines.append(
            f"| {row['id']} {row['name']} | {row['current']['owner']} / "
            f"{row['current']['severity']} | {row['new']['owner']} | {receipts} | "
            f"{row['final_disposition']} |")
    lines += [
        "",
        "## 基线与变异读数",
        "",
        "| 样本族 | 基线 | 变异 codes | 变异下游状态 |",
        "|---|---|---|---|",
    ]
    for name, data in report["families"].items():
        lines.append(
            f"| {name} | {data['sides']['baseline']['expected']} / "
            f"{data['sides']['baseline']['semantic_codes']} | "
            f"{data['sides']['mutation']['semantic_codes']} | "
            f"plan={data['sides']['mutation']['visual_plan_status']} "
            f"voice={data['sides']['mutation']['voice_status']} "
            f"codes={data['sides']['mutation']['voice_codes']} |")
    lines += ["", f"基线全绿：`{report['summary']['baseline_all_green']}`；"
                  f"三族变异检测：`{report['summary']['mutation_all_detected']}`。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Step01 阻断影子比较")
    parser.add_argument("--out", default=".pytest_tmp/s07")
    args = parser.parse_args(argv)
    report = build_report(Path(args.out))
    print(json.dumps({"out": str(Path(args.out)),
                      "baseline_all_green": report["summary"]["baseline_all_green"],
                      "mutation_all_detected": report["summary"]["mutation_all_detected"],
                      "blockers": len(report["blockers"])}, ensure_ascii=False))
    return 0 if report["summary"]["baseline_all_green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
