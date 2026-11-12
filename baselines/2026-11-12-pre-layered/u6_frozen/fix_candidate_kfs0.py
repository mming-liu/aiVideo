# -*- coding: utf-8 -*-
"""方案 A：零调用删除 shot 8 kfs[0] 听香残留，重算投影/质量/哈希并扫描同类残留.

不调用任何模型；只读候选/原文，写隔离目录下的新候选、投影稿、质量报告和证据。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110"
WORK = BASE / "u6_kfs0fix"
WORK.mkdir(parents=True, exist_ok=True)
OUTPUT = WORK / "output"
LOG = WORK / "step01.log"

os.environ.update({
    "OUTPUT_BASE": str(OUTPUT),
    "STEP01_LOG_PATH": str(LOG),
    "HOOK_ENABLED": "0",
    "VOICE_ADAPTATION_ENABLED": "0",
    "DURATION_MIN_SEC": "60",
    "DURATION_MAX_SEC": "90",
    "DURATION_TARGET_MIN_SEC": "70",
    "DURATION_TARGET_MAX_SEC": "80",
    "NOVEL_NAME": "锦衣夜行",
    "BOOK_INDEX": "1",
    "BOOK_NAME": "杀青州",
    "NOVEL_CHAPTER": "1",
})
sys.path.insert(0, str(ROOT))

from step01_script_gen import main as m  # noqa: E402
from shared.quality_gate import compute_script_revision  # noqa: E402
from shared.vimax_bridge import _get_frame_content_hash  # noqa: E402

CAND_OLD = BASE / "candidate_d1a" / "candidate.json"
CAND_NEW = BASE / "candidate_d1a" / "candidate_kfs0fix.json"
NOVEL = BASE / "u6_inputs" / "novel_chapter_001.txt"
SHOT2_REFS = [
    str(ROOT / "output" / "锦衣夜行" / "global" / "scenes_bank" / "照月湾荷塘" / "near.png"),
    str(ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001"
        / "vimax_working" / "shots" / "2"
        / "ref_sheet_ff_chars_杨旭+听香_a3f68e05f200118b8e1a.png"),
    str(ROOT / "output" / "锦衣夜行" / "global" / "props_bank" / "油纸伞" / "prop.png"),
]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def layout_rows(shot: dict, frame: str) -> list:
    fl = shot.get("frame_layouts") or {}
    return [e for e in fl.get(frame) or [] if isinstance(e, dict)]


def kf_rows(shot: dict, idx: int) -> list:
    fl = shot.get("frame_layouts") or {}
    kfs = fl.get("kfs") or []
    return [e for e in (kfs[idx] if idx < len(kfs) else []) if isinstance(e, dict)]


def brief(rows: list) -> list:
    return [{k: e.get(k) for k in ("name", "on_screen", "position", "pose", "support")}
            for e in rows]


def main() -> int:
    old = load(CAND_OLD)
    new = copy.deepcopy(old)
    shot8_old = [s for s in old["shots"] if s.get("index") == 8][0]
    shot8_new = [s for s in new["shots"] if s.get("index") == 8][0]
    before = brief(kf_rows(shot8_new, 0))
    kept = [e for e in kf_rows(shot8_new, 0)
            if not (str(e.get("name") or "").strip() == "听香")]
    removed = len(kf_rows(shot8_new, 0)) - len(kept)
    shot8_new["frame_layouts"]["kfs"][0] = kept
    CAND_NEW.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")

    novel = NOVEL.read_text(encoding="utf-8")
    projected, errors = m.project_for_commit(new, novel, label="kfs0fix")
    revision = compute_script_revision(projected)
    quality = m._build_step01_quality_gate(projected, novel)
    proj_shot8 = [s for s in projected["shots"] if s.get("index") == 8][0]
    proj_shot2 = [s for s in projected["shots"] if s.get("index") == 2][0]
    old_proj, old_errors = m.project_for_commit(old, novel, label="kfs0fix-old")

    # 回读投影后的 shot 8 全部布局槽位
    after = {
        "ff": brief(layout_rows(proj_shot8, "ff")),
        "lf": brief(layout_rows(proj_shot8, "lf")),
        "kfs": [brief(kf_rows(proj_shot8, i))
                for i in range(len((proj_shot8.get("frame_layouts") or {}).get("kfs") or []))],
    }
    readback_clean = all(
        not any(str(e.get("name") or "").strip() == "听香" for e in rows)
        for rows in [layout_rows(proj_shot8, "ff"), layout_rows(proj_shot8, "lf")]
        + [kf_rows(proj_shot8, i)
           for i in range(len((proj_shot8.get("frame_layouts") or {}).get("kfs") or []))]
    )

    # shot 2 首帧哈希（与 U5 同一 refs / lock / empty）
    shot2_hash = _get_frame_content_hash(
        proj_shot2, False, SHOT2_REFS, lock_kind="layout",
        characters=projected.get("characters") or [], frame_kind="first_frame")
    saved_hash = (ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001"
                  / "vimax_working" / "shots" / "2" / "frame_hash.txt").read_text(
                      encoding="utf-8").strip()

    # 同类残留扫描：on_screen=true 且名字在文本里挨着“画外/幕后/传来/尖叫/声音/喊/呼”
    cues = ("画外", "画面外", "幕后", "传来", "尖叫", "声音", "喊", "呼")
    text_fields = ("ff_desc", "lf_desc", "motion_desc", "prompt",
                   "narration_segment", "subtitle")
    scan = []
    for shot in projected.get("shots") or []:
        idx = shot.get("index")
        texts = {f: str(shot.get(f) or "") for f in text_fields}
        for frame in ("ff", "lf"):
            for e in layout_rows(shot, frame):
                name = str(e.get("name") or "").strip()
                if not name or e.get("on_screen") is not True:
                    continue
                for f, text in texts.items():
                    pos = text.find(name)
                    if pos < 0:
                        continue
                    window = text[max(0, pos - 12): pos + len(name) + 12]
                    if any(c in window for c in cues):
                        scan.append({"shot": idx, "frame": frame, "name": name,
                                     "field": f, "window": window})
                        break
        for i, kf in enumerate((shot.get("frame_layouts") or {}).get("kfs") or []):
            for e in kf or []:
                name = str(e.get("name") or "").strip()
                if not name or e.get("on_screen") is not True:
                    continue
                for f, text in texts.items():
                    pos = text.find(name)
                    if pos < 0:
                        continue
                    window = text[max(0, pos - 12): pos + len(name) + 12]
                    if any(c in window for c in cues):
                        scan.append({"shot": idx, "frame": "kfs[%d]" % i, "name": name,
                                     "field": f, "window": window})
                        break
    # 位置/支撑面明显矛盾的条目（人工复核清单）
    inconsistent = []
    for shot in projected.get("shots") or []:
        idx = shot.get("index")
        for frame in ("ff", "lf"):
            for e in layout_rows(shot, frame):
                pos = str(e.get("position") or "")
                sup = str((e.get("support") or {}).get("id") or "")
                if ("水面" in pos and sup and "堤" in sup) or ("堤" in pos and "船" in sup):
                    inconsistent.append({"shot": idx, "frame": frame,
                                         "name": e.get("name"), "position": pos,
                                         "support": sup})
        for i, kf in enumerate((shot.get("frame_layouts") or {}).get("kfs") or []):
            for e in kf or []:
                pos = str(e.get("position") or "")
                sup = str((e.get("support") or {}).get("id") or "")
                if ("水面" in pos and sup and "堤" in sup) or ("堤" in pos and "船" in sup):
                    inconsistent.append({"shot": idx, "frame": "kfs[%d]" % i,
                                         "name": e.get("name"), "position": pos,
                                         "support": sup})

    # 历史审计中带降级标记/可比较的条目
    rounds = load(ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001"
                  / "step01_audit_rounds.json")
    downgraded = []
    for row in rounds:
        for issue in row.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            if issue.get("out_of_delta_scope") or issue.get("_downgraded_locked_only") \
                    or issue.get("_downgraded_stage_enum"):
                downgraded.append({"round": row.get("round"),
                                   "recorded_at": row.get("recorded_at"),
                                   "shot_id": issue.get("shot_id"),
                                   "severity": issue.get("severity"),
                                   "code": issue.get("code"),
                                   "field_path": issue.get("field_path")})

    evidence = {
        "candidate_old": {"path": str(CAND_OLD), "sha256": sha256_file(CAND_OLD)},
        "candidate_new": {"path": str(CAND_NEW), "sha256": sha256_file(CAND_NEW)},
        "shot8_kfs0_before": before,
        "shot8_kfs0_removed": removed,
        "projection": {
            "errors": errors,
            "revision": revision,
            "old_projection_errors": old_errors,
            "old_revision": compute_script_revision(old_proj),
            "shot_count": len(projected.get("shots") or []),
            "shot8_after": after,
            "readback_clean": readback_clean,
            "note": ("投影后回读 ff/lf/全部 kfs：听香不得以 on_screen=true 回填。"
                     "readback_clean=true 表示所有槽位都不再有听香条目。"),
        },
        "quality": {
            "status": quality.get("status"),
            "script_revision": quality.get("script_revision"),
            "rule_source_fingerprint": quality.get("rule_source_fingerprint"),
            "hard_count": quality.get("hard_count"),
            "visual_risk_count": quality.get("visual_risk_count"),
            "advisory_count": quality.get("advisory_count"),
        },
        "shot2_hash": {
            "saved": saved_hash,
            "recomputed": shot2_hash,
            "match": shot2_hash == saved_hash,
            "refs": SHOT2_REFS,
            "lock": "layout",
        },
        "same_class_scan": {
            "cues": list(cues),
            "hits": scan,
            "note": ("命中 = on_screen=true 的角色名在文本里出现在画外/幕后/传来/"
                     "尖叫/声音/喊/呼的邻近窗口；需人工复核，不等于缺陷。"),
        },
        "position_support_inconsistent": inconsistent,
        "historical_downgraded": downgraded,
    }
    (WORK / "step01_quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    (WORK / "script_projected.json").write_text(
        json.dumps(projected, ensure_ascii=False, indent=2), encoding="utf-8")
    out = BASE / "u6_shot8_layout_fixed.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in
                      ("candidate_new", "projection", "quality", "shot2_hash")},
                     ensure_ascii=False, indent=1))
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
