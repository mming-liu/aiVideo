# -*- coding: utf-8 -*-
"""S01a 方案 A 复核：当前 candidate.json 已是 kfs[0] 修正版；重算投影/质量/哈希并扫描同类残留.

零调用。旧候选为 candidate.pre_s01a.json（含听香条目），新候选为 candidate.json。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110"
WORK = BASE / "u6_kfs0fix"
WORK.mkdir(parents=True, exist_ok=True)
OUTPUT = WORK / "output"

os.environ.update({
    "OUTPUT_BASE": str(OUTPUT),
    "STEP01_LOG_PATH": str(WORK / "step01.log"),
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

OLD = BASE / "candidate_d1a" / "candidate.pre_s01a.json"
NEW = BASE / "candidate_d1a" / "candidate.json"
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
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def brief(rows: list) -> list:
    return [{k: e.get(k) for k in ("name", "on_screen", "position", "pose", "support")}
            for e in rows if isinstance(e, dict)]


def diff(a, b, path=""):
    out = []
    if type(a) is not type(b):
        return [(path, "TYPE", type(a).__name__, type(b).__name__)]
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append((path + "/" + k, "ADDED", None, b[k]))
            elif k not in b:
                out.append((path + "/" + k, "REMOVED", a[k], None))
            else:
                out += diff(a[k], b[k], path + "/" + k)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append((path, "LEN", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, path + "[%d]" % i)
    elif a != b:
        out.append((path, "CHANGED", a, b))
    return out


def main() -> int:
    old = load(OLD)
    new = load(NEW)
    novel = NOVEL.read_text(encoding="utf-8")
    changes = diff(old, new)
    projected, errors = m.project_for_commit(new, novel, label="kfs0fix")
    revision = compute_script_revision(projected)
    quality = m._build_step01_quality_gate(projected, novel)
    (WORK / "step01_quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    (WORK / "script_projected.json").write_text(
        json.dumps(projected, ensure_ascii=False, indent=2), encoding="utf-8")

    shot8 = [s for s in projected["shots"] if s.get("index") == 8][0]
    shot2 = [s for s in projected["shots"] if s.get("index") == 2][0]
    shot8_text = {k: shot8.get(k) for k in
                  ("ff_desc", "lf_desc", "motion_desc", "prompt",
                   "narration_segment", "subtitle")}
    shot8_keyframes = [k.get("desc") for k in shot8.get("keyframes") or []]
    fl = shot8.get("frame_layouts") or {}
    after = {
        "ff": brief(fl.get("ff") or []),
        "lf": brief(fl.get("lf") or []),
        "kfs": [brief(kf) for kf in fl.get("kfs") or []],
    }
    all_rows = [fl.get("ff") or [], fl.get("lf") or []] + list(fl.get("kfs") or [])
    readback_clean = all(
        not any(str(e.get("name") or "").strip() == "听香" for e in rows)
        for rows in all_rows
    )

    shot2_hash = _get_frame_content_hash(
        shot2, False, SHOT2_REFS, lock_kind="layout",
        characters=projected.get("characters") or [], frame_kind="first_frame")
    saved_hash = (ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001"
                  / "vimax_working" / "shots" / "2" / "frame_hash.txt").read_text(
                      encoding="utf-8").strip()

    cues = ("画外", "画面外", "幕后", "传来", "尖叫", "声音", "喊", "呼")
    import re as _re

    def clauses(text: str) -> list:
        return [c.strip() for c in _re.split(r"[，。；！？、,.;!?]", text or "") if c.strip()]

    hits = []
    voice_pose = []
    offscreen_position = []
    for shot in projected.get("shots") or []:
        idx = shot.get("index")
        texts = {f: str(shot.get(f) or "") for f in
                 ("ff_desc", "lf_desc", "motion_desc", "prompt",
                  "narration_segment", "subtitle")}
        for _i, _kf in enumerate(shot.get("keyframes") or []):
            if isinstance(_kf, dict):
                texts["keyframes[%d].desc" % _i] = str(_kf.get("desc") or "")
        slots = [("ff", shot.get("frame_layouts", {}).get("ff") or []),
                 ("lf", shot.get("frame_layouts", {}).get("lf") or [])]
        slots += [("kfs[%d]" % i, kf or [])
                  for i, kf in enumerate(shot.get("frame_layouts", {}).get("kfs") or [])]
        for frame, rows in slots:
            for e in rows:
                name = str(e.get("name") or "").strip()
                if not name:
                    continue
                if e.get("on_screen") is not True:
                    continue
                pos = str(e.get("position") or "")
                if any(c in pos for c in ("画外", "画面外", "幕后")):
                    offscreen_position.append({"shot": idx, "frame": frame,
                                               "name": name, "position": pos})
                pose = str(e.get("pose") or "")
                if any(c in pose for c in ("传来", "尖叫", "喊", "呼", "声音")):
                    voice_pose.append({"shot": idx, "frame": frame,
                                       "name": name, "pose": pose})
                for f, text in texts.items():
                    for clause in clauses(text):
                        if name in clause and any(c in clause for c in cues):
                            hits.append({"shot": idx, "frame": frame, "name": name,
                                         "field": f, "clause": clause})
                            break

    inconsistent = []
    for shot in projected.get("shots") or []:
        idx = shot.get("index")
        slots = [("ff", shot.get("frame_layouts", {}).get("ff") or []),
                 ("lf", shot.get("frame_layouts", {}).get("lf") or [])]
        slots += [("kfs[%d]" % i, kf or [])
                  for i, kf in enumerate(shot.get("frame_layouts", {}).get("kfs") or [])]
        for frame, rows in slots:
            for e in rows:
                pos = str(e.get("position") or "")
                sup = str((e.get("support") or {}).get("id") or "")
                if ("水面" in pos and sup and "堤" in sup) or ("堤" in pos and "船" in sup):
                    inconsistent.append({"shot": idx, "frame": frame,
                                         "name": e.get("name"), "position": pos,
                                         "support": sup})

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
    formal = load(ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001"
                  / "step01_audit.json")
    formal_downgraded = []
    for issue in formal.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        if issue.get("out_of_delta_scope") or issue.get("_downgraded_locked_only") \
                or issue.get("_downgraded_stage_enum"):
            formal_downgraded.append({"shot_id": issue.get("shot_id"),
                                      "severity": issue.get("severity"),
                                      "code": issue.get("code"),
                                      "field_path": issue.get("field_path"),
                                      "out_of_delta_scope": issue.get("out_of_delta_scope")})
    historical_issues = []
    _seen = set()
    for row in rounds:
        for issue in row.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            key = (str(issue.get("shot_id")), str(issue.get("code")),
                   str(issue.get("field_path")))
            if key in _seen:
                continue
            _seen.add(key)
            historical_issues.append({
                "shot_id": issue.get("shot_id"),
                "severity": issue.get("severity"),
                "code": issue.get("code"),
                "field_path": issue.get("field_path"),
                "message": str(issue.get("message") or "")[:200],
            })

    evidence = {
        "old_candidate": {"path": str(OLD), "sha256": sha256_file(OLD),
                          "projected_revision": "1d70284e"
                          "d0d241a95eb3842e9028c26711e5dd5248e94f36ae0a78d1d52be116"},
        "new_candidate": {"path": str(NEW), "sha256": sha256_file(NEW),
                          "projected_revision": revision},
        "candidate_diff": {
            "count": len(changes),
            "changes": [{"path": p, "kind": k,
                         "before": str(a)[:200] if k != "LEN" else a,
                         "after": str(b)[:200] if k != "LEN" else b}
                        for p, k, a, b in changes],
            "note": "只允许 script_revision 与 shot 8 kfs[0] 的听香条目删除；其他差异即停止。",
        },
        "projection": {
            "errors": errors,
            "revision": revision,
            "shot_count": len(projected.get("shots") or []),
            "shot8_after": after,
            "readback_clean": readback_clean,
        },
        "shot8_text_consistency": {
            "fields": shot8_text,
            "keyframes": shot8_keyframes,
            "characters_contains_tingxiang": "听香" in [
                str(c.get("identifier") or "") for c in projected.get("characters") or []],
            "note": ("文本里的听香均以“画外”出现（ff_desc/prompt/keyframes[0]）；"
                     "布局 ff/lf/全部 kfs 已无听香条目；characters 是全局角色表，"
                     "不是本镜在场声明。"),
        },
        "quality": {
            "status": quality.get("status"),
            "script_revision": quality.get("script_revision"),
            "rule_source_fingerprint": quality.get("rule_source_fingerprint"),
            "hard_count": quality.get("hard_count"),
            "visual_risk_count": quality.get("visual_risk_count"),
            "advisory_count": quality.get("advisory_count"),
        },
        "shot2_hash": {"saved": saved_hash, "recomputed": shot2_hash,
                       "match": shot2_hash == saved_hash,
                       "refs": SHOT2_REFS, "lock": "layout"},
        "same_class_scan": {
            "cues": list(cues),
            "hits": hits,
            "voice_pose_on_screen": voice_pose,
            "offscreen_position_on_screen": offscreen_position,
            "note": ("clause 级匹配：角色名与画外/幕后/传来等词必须在同一分句；"
                     "pose 含传来/尖叫/喊/呼且 on_screen=true 单列；"
                     "position 含画外/画面外/幕后且 on_screen=true 单列。"),
        },
        "before_scan": {
            "shot8_kfs0": brief(([s for s in old["shots"]
                                  if s.get("index") == 8][0]
                                 .get("frame_layouts", {}).get("kfs") or [[]])[0]),
            "voice_pose_on_screen": [
                {"name": e.get("name"), "pose": e.get("pose")}
                for e in ([s for s in old["shots"] if s.get("index") == 8][0]
                          .get("frame_layouts", {}).get("kfs") or [[]])[0]
                if e.get("on_screen") is True
                and any(c in str(e.get("pose") or "")
                        for c in ("传来", "尖叫", "喊", "呼", "声音"))
            ],
            "note": "修正前 shot 8 kfs[0] 的听香条目；用于证明扫描对已知残留有判别力。",
        },
        "position_support_inconsistent": inconsistent,
        "historical_downgraded": {
            "rounds": downgraded,
            "formal": formal_downgraded,
            "note": ("rounds 是降级前快照，通常无标记；formal 是降级后产物。"
                     "formal 仅 shot 8 一条 out_of_delta_scope，对应旧候选，已由 A 修正；"
                     "其余历史项均为 advisory，不依赖 out_of_delta_scope。"),
        },
        "historical_issues": historical_issues,
    }
    out = BASE / "u6_shot8_layout_fixed.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in
                      ("old_candidate", "new_candidate", "candidate_diff",
                       "projection", "quality", "shot2_hash",
                       "same_class_scan", "position_support_inconsistent",
                       "historical_downgraded")},
                     ensure_ascii=False, indent=1))
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
