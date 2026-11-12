"""S06 阶段二分析：修订前后逐条核对 7 条确认缺陷 + 终局审计对照."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PHASE2 = ROOT / ".pytest_tmp/s06/phase2"

CHECKS = [
    ("#2 shot7 lf 酒壶在右手", 7, "lf_desc", "右手酒壶倾倒"),
    ("#4 shot11 ff 已竖指", 11, "ff_desc", "右手食指竖于唇前"),
    ("#5 shot1 prompt 大特写", 1, "prompt", "大特写"),
    ("#6 shot6 prompt 重复说明", 6, "prompt", "（寒光是独立物体，不是从角色身上长出来的）"),
    ("#8 shot10 ff 披头散发", 10, "ff_desc", "披头散发"),
    ("#9 shot4 ff 右手提壶", 4, "ff_desc", "右手提青瓷酒壶"),
    ("#10 shot7 ff 反握短刃", 7, "ff_desc", "反握带血短刃"),
]


def _shots(script: dict) -> dict:
    return {str(s.get("index")): s for s in script.get("shots") or []}


def _field(script: dict, idx: int, field: str) -> str:
    return str((_shots(script).get(str(idx)) or {}).get(field) or "")


def main() -> int:
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(
        [d for d in PHASE2.iterdir() if (d / "summary.json").is_file()])[-1]
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    before = json.loads((ROOT / ".pytest_tmp/u1_u5_closeout_20261110/candidate_d1a/"
                         "candidate.json").read_text(encoding="utf-8"))
    after = json.loads((run / "repaired_script.json").read_text(encoding="utf-8"))
    final = json.loads((run / "final_audit.json").read_text(encoding="utf-8"))
    diag = json.loads((run / "repair_diag.json").read_text(encoding="utf-8"))

    print("# S06 阶段二  run=%s" % run.name)
    print("repair=%ss audit=%ss  final_status=%s issues=%d blocking=%d"
          % (summary["repair_sec"], summary["audit_sec"], summary["final_status"],
             summary["final_issues"], summary["final_blocking"]))
    print("revision: %s -> %s" % (str(summary["candidate_revision"])[:12],
                                  str(summary["repaired_revision"])[:12]))
    print()
    print("| 确认缺陷 | 修前 | 修后 | 已消除 |")
    print("|---|---|---|---|")
    for label, idx, field, needle in CHECKS:
        b = _field(before, idx, field)
        a = _field(after, idx, field)
        nb = b.count(needle)
        na = a.count(needle)
        print("| %s | %d | %d | %s |" % (label, nb, na, "是" if (nb and not na) else "否"))
    print()
    print("## 修订事务")
    for row in diag.get("diag") or []:
        print("   round=%s accepted=%s resolved=%s reason=%s targets=%s"
              % (row.get("repair_round"), row.get("accepted"), row.get("resolved"),
                 row.get("reason"), row.get("targets")))
        rb = row.get("related_readback") or {}
        if rb:
            print("     readback: checked=%s changed=%s reverted=%s half=%s residual=%s"
                  % (rb.get("checked"), rb.get("changed"), rb.get("reverted"),
                     len(rb.get("half_repaired") or []),
                     (rb.get("residual_after_projection") or {}).get("changed")))
            for t in rb.get("targets") or []:
                if t.get("requested") or t.get("reverted"):
                    print("     - %s requested=%s applied=%s reverted=%s"
                          % (t.get("target"), t.get("requested"), t.get("applied"),
                             t.get("reverted")))
    if diag.get("block"):
        print("   block_reasons: %s" % diag["block"][:3])
    print()
    print("## 终局审计（新契约）")
    for i in final.get("issues") or []:
        print("   - [%s] %s @shot%s %s | %s"
              % (i.get("severity"), i.get("code"), i.get("shot_id"),
                 i.get("field_path"), str(i.get("message") or "")[:100]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
