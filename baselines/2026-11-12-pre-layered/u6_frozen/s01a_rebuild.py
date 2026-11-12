# -*- coding: utf-8 -*-
"""S01a 零调用重建：删除 shot 8 frame_layouts.kfs[0] 的「听香」残留条目.

只做数据修正与重投影，不发送任何模型请求，不写正式目录。
--apply 才落盘（candidate.json / reading.md / iso 两件 / 审计包）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(r"C:/myFloders/git/aiVideo")
BASE = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110"
OUT = BASE / "s01a"
OUT.mkdir(parents=True, exist_ok=True)

ISO_CH = BASE / "iso" / "output"
os.environ["OUTPUT_BASE"] = str(ISO_CH)
os.environ["HOOK_ENABLED"] = "0"
os.environ["VOICE_ADAPTATION_ENABLED"] = "0"
for _k, _v in (("DURATION_MIN_SEC", "60"), ("DURATION_MAX_SEC", "90"),
               ("DURATION_TARGET_MIN_SEC", "70"), ("DURATION_TARGET_MAX_SEC", "80")):
    os.environ.setdefault(_k, _v)
sys.path.insert(0, str(ROOT))

PROD = ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001" / "script_result.json"
NOVEL = BASE / "u6_inputs" / "novel_chapter_001.txt"
OLD_CAND = BASE / "candidate_d1a" / "candidate.json"
SPEC_PATH = BASE / "candidate_d1a" / "edit_spec.json"

TARGET_NAME = "听香"          # 章节数据输入，仅本批修正使用
APPLY = "--apply" in sys.argv


def diff(a, b, path="", acc=None):
    acc = [] if acc is None else acc
    if type(a) is not type(b):
        acc.append({"path": path, "kind": "type", "old": type(a).__name__, "new": type(b).__name__})
        return acc
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                acc.append({"path": f"{path}/{k}", "kind": "added"})
            elif k not in b:
                acc.append({"path": f"{path}/{k}", "kind": "removed"})
            else:
                diff(a[k], b[k], f"{path}/{k}", acc)
        return acc
    if isinstance(a, list):
        if len(a) != len(b):
            acc.append({"path": path, "kind": "len", "old": len(a), "new": len(b)})
        for i in range(min(len(a), len(b))):
            diff(a[i], b[i], f"{path}[{i}]", acc)
        return acc
    if a != b:
        acc.append({"path": path, "kind": "value", "old": a, "new": b})
    return acc


spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
src = json.loads(PROD.read_text(encoding="utf-8-sig"))
old = json.loads(OLD_CAND.read_text(encoding="utf-8-sig"))
novel_text = NOVEL.read_text(encoding="utf-8")

# ---- S01a: 数据修正（显式、最小、只删布局条目） --------------------------
edit8 = [x for x in spec["shots"] if x["target"]["index"] == 8][0]
layouts = edit8["target"]["frame_layouts"]
kfs = layouts.get("kfs") or []
before = [e.get("name") for e in kfs[0]] if kfs else []
kfs[0] = [e for e in kfs[0] if e.get("name") != TARGET_NAME]
after = [e.get("name") for e in kfs[0]]
ff_before = [e.get("name") for e in layouts.get("ff") or []]
edit8["reason"] = ("shot 8：删除「画外听香」被误登记为 on_screen 的布局条目"
                   "（ff 与 kfs[0] 同源矛盾；正文与旁白明确为画外尖叫，视觉不出现）")

# ---- 重建投影：真实 finalize 两次验幂等 --------------------------------
from step01_script_gen.candidate_edit import (                        # noqa: E402
    build_candidate, finalize_candidate, object_digest, reading_markdown)
from step01_script_gen.main import (                                   # noqa: E402
    _build_step01_quality_gate, _strip_runtime_fields, project_for_commit)

cand, build_errs = build_candidate(src.get("chapter_outline"), src, spec)
if build_errs:
    raise SystemExit("build_errors: " + json.dumps(build_errs, ensure_ascii=False))

final = finalize_candidate(
    cand,
    lambda s: project_for_commit(s, novel_text, label="s01a_rebuild"),
    _strip_runtime_fields,
)

from shared.quality_gate import ensure_script_revision, compute_script_revision  # noqa: E402

revision = ensure_script_revision(final)
quality = _build_step01_quality_gate(final, novel_text)
q_rev = str(quality.get("script_revision") or "")

OLD_Q = ISO_CH / "锦衣夜行" / "part_01_杀青州" / "chapter_001" / "step01_quality.json"


def _issue_keys(payload):
    out = {}
    for bucket in ("hard", "visual_risk", "advisory", "video_advisory"):
        for n, item in enumerate(payload.get(bucket) or []):
            blob = json.dumps(item, ensure_ascii=False, sort_keys=True)
            tag = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]
            key = f"{bucket}|{item.get('code')}|{item.get('shot_id')}|{n}|{tag}"
            out[key] = item
    return out


old_q = json.loads(OLD_Q.read_text(encoding="utf-8")) if OLD_Q.is_file() else {}
advisory_delta = {"resolved": [], "new": []}
if old_q:
    ok, nk = _issue_keys(old_q), _issue_keys(quality)
    advisory_delta = {"resolved": sorted(set(ok) - set(nk)),
                      "new": sorted(set(nk) - set(ok))}

# ---- 校验：改动面必须最小 ------------------------------------------------
d_old = diff(old, final)
shot8 = [s for s in final["shots"] if s.get("index") == 8][0]
l8 = shot8.get("frame_layouts") or {}
report = {
    "schema": "s01a_rebuild_probe.v1",
    "novel_present": NOVEL.is_file(),
    "shot8_layout_before": {"ff": ff_before, "kfs0": before},
    "shot8_layout_after": {"ff": [e.get("name") for e in l8.get("ff") or []],
                           "kfs0": [e.get("name") for e in (l8.get("kfs") or [[]])[0]]},
    "diff_vs_old_candidate": d_old,
    "diff_count": len(d_old),
    "revision_computed": revision,
    "revision_recheck": compute_script_revision(final),
    "quality": {"status": quality.get("status"), "blocking": quality.get("blocking"),
                "hard_count": quality.get("hard_count"),
                "visual_risk_count": quality.get("visual_risk_count"),
                "advisory_count": quality.get("advisory_count"),
                "rule_source_fingerprint": quality.get("rule_source_fingerprint"),
                "script_revision": q_rev},
    "shot_count": len(final.get("shots") or []),
    "duration_sec": round(sum(float(s.get("duration_sec") or 0) for s in final["shots"]), 2),
    "narration_kept": bool(shot8.get("narration_segment")),
    "advisory_delta_vs_old_quality": advisory_delta,
    "applied": APPLY,
}

if APPLY:
    cand_dir = BASE / "candidate_d1a"
    (cand_dir / "edit_spec.pre_s01a.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    spec_body = json.dumps(final, ensure_ascii=False, indent=1) + "\n"
    (cand_dir / "candidate.json").write_text(spec_body, encoding="utf-8")
    (cand_dir / "candidate_d1a_reading.md").write_text(
        reading_markdown(final, revision=revision), encoding="utf-8")
    iso_dir = ISO_CH / "锦衣夜行" / "part_01_杀青州" / "chapter_001"
    iso_dir.mkdir(parents=True, exist_ok=True)
    (iso_dir / "script_result.json").write_text(spec_body, encoding="utf-8")
    (iso_dir / "step01_quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    report["written"] = {
        "candidate_sha256": hashlib.sha256(spec_body.encode("utf-8")).hexdigest(),
        "iso_script_sha256": hashlib.sha256((iso_dir / "script_result.json").read_bytes()).hexdigest(),
        "iso_quality_sha256": hashlib.sha256((iso_dir / "step01_quality.json").read_bytes()).hexdigest(),
    }

path = OUT / ("s01a_apply.json" if APPLY else "s01a_probe.json")
path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k != "diff_vs_old_candidate"},
                 ensure_ascii=False, indent=1))
print("diff paths:")
for d in d_old[:40]:
    print(" ", d["path"], d["kind"], str(d.get("old"))[:60], "->", str(d.get("new"))[:60])
print("report ->", path)
