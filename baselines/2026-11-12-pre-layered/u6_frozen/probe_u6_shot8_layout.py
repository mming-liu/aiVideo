# -*- coding: utf-8 -*-
"""U6 零调用预检：shot 8 画外听香布局残留（候选修复是否完整）."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110"
CAND = BASE / "candidate_d1a" / "candidate.json"
PROD = ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001" / "script_result.json"
AUDIT = ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001" / "step01_audit.json"


def shot8(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    shot = [s for s in data.get("shots") or [] if s.get("index") == 8][0]
    layouts = shot.get("frame_layouts") or {}
    return {
        "subtitle": shot.get("subtitle"),
        "motion_desc": shot.get("motion_desc"),
        "ff": [{k: e.get(k) for k in ("name", "on_screen", "position", "pose", "support")}
               for e in layouts.get("ff") or []],
        "kfs0": [{k: e.get(k) for k in ("name", "on_screen", "position", "pose", "support")}
                 for e in (layouts.get("kfs") or [[]])[0]],
    }


audit = json.loads(AUDIT.read_text(encoding="utf-8"))
issue = [i for i in audit.get("issues") or []
         if str(i.get("shot_id")) == "8"
         and str(i.get("code")) == "model_audit_STATE_CHAIN_CONFLICT"]
result = {
    "production": shot8(PROD),
    "candidate": shot8(CAND),
    "old_audit_issue": issue[0] if issue else None,
    "finding": (
        "候选只删除了 shot 8 frame_layouts.ff 的听香条目；frame_layouts.kfs[0] 仍有 "
        "name=听香、on_screen=true、position=水面、support=堤岸 的条目。"
        "旧审计的 repair_targets 明确包含 frame_layouts.kfs[0]，消息要求删除 ff 与 "
        "kfs[0] 中的听香条目。候选修复不完整。"),
    "risk": (
        "新审计是全 13 镜、无增量降级；shot 8 的 kfs[0] 残留很可能再次被判 "
        "visual_risk/blocking。STEP01_AUDIT_ONLY=1 下 U6 会停在审计 blocking，"
        "这 1 次审计发送不会产生合法提交。"),
    "recommendation": (
        "在 S02 前做零调用数据修正：删除 shot 8 frame_layouts.kfs[0] 的听香条目"
        "（保留文本/旁白/画外尖叫），重新 project_for_commit、重建质量报告、"
        "重算 revision 与审计包绑定摘要；或明确接受本次审计 blocking 后另开修复轮。"),
}
out = BASE / "u6_shot8_layout_risk.json"
out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print(str(out))
