"""Integration: a frozen candidate through the **real** commit gates, stub audit only.

External dataset (deliberately not part of a clean checkout, per AGENTS.md):

- `STEP01_FROZEN_CANDIDATE` = path to a candidate JSON produced by
  `tools/verify_candidate_delivery.py` (the frozen delivery artifact).
- `STEP01_FROZEN_NOVEL` = optional path to the matching novel text.
- `STEP01_FROZEN_CONFIG` = optional JSON object; defaults to the delivery band the
  frozen candidate was produced under (120 s ceiling, 105-115 s target).

Why a child process: `shared.config` reads the duration band once at import, and the
commit gates read it from there.  To judge the candidate under **its own declared
config** (not this machine's `.env.content` default of 60-90 s) the values must be in
the environment before anything imports `shared.config`.  The child therefore runs the
real `_commit_step01_outputs` with the delivery config; only the text-model audit call
is stubbed.  Its result is written under `<chapter>/_stub_audit/` and marked
`stub: true`; no artifact of this test may be reused as a real audit for M08.

Without the environment variable the module skips with an explicit reason, so the
basic suite stays runnable from a clean checkout.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE = str(os.environ.get("STEP01_FROZEN_CANDIDATE") or "").strip()
_NOVEL = str(os.environ.get("STEP01_FROZEN_NOVEL") or "").strip()
_DEFAULT_CONFIG = {"DURATION_MAX_SEC": "120", "DURATION_TARGET_MIN_SEC": "105",
                   "DURATION_TARGET_MAX_SEC": "115", "VOICE_ADAPTATION_ENABLED": "1"}

pytestmark = pytest.mark.skipif(
    not _CANDIDATE,
    reason="STEP01_FROZEN_CANDIDATE 未设置：本用例需要显式指定的冻结候选（外部数据集）")

_CHILD = r"""
import json, os, sys
from pathlib import Path

sys.path.insert(0, os.environ["SP_ROOT"])
import step01_script_gen.main as m
import step01_script_gen.audit as audit_mod

chap = Path(os.environ["SP_CHAPTER"])
chap.mkdir(parents=True, exist_ok=True)
m.CHAPTER_DIR = chap
m.OUTPUT_FILE = chap / "script_result.json"
m.SCRIPT_FILE = chap / "script_result.json"
m._snapshot_run_artifacts = lambda script=None: None

calls = {"n": 0}

def fake(*args, **kwargs):
    calls["n"] += 1
    result = audit_mod.AuditResult("ok", [], "")
    result.issues = []
    result.raw_text = ""
    return result

audit_mod.run_same_model_audit_with_status = fake
candidate = json.loads(Path(os.environ["SP_CANDIDATE"]).read_text(encoding="utf-8"))
novel = (Path(os.environ["SP_NOVEL"]).read_text(encoding="utf-8")
         if os.environ.get("SP_NOVEL") else "")
plan = json.loads(json.dumps(candidate.get("chapter_outline") or {}))
projected, projection_errors = m.project_for_commit(
    candidate, novel, label="delivery-projection")
projection_revision = str(projected.get("script_revision") or "")
script, summary = m._commit_step01_outputs(candidate, novel, plan=plan)
Path(os.environ["SP_RESULT"]).write_text(json.dumps({
    "status": summary.get("status"),
    "detail": str(summary.get("detail"))[:800],
    "revision": summary.get("revision"),
    "projection_revision": projection_revision,
    "projection_errors": projection_errors,
    "calls": calls["n"],
    "blocking": [b.get("code") for b in (summary.get("blocking") or [])],
    "band": {"min": m.DURATION_MIN_SEC, "max": m.DURATION_MAX_SEC},
}, ensure_ascii=False), encoding="utf-8")
"""


def _run_child(tmp_path: Path) -> dict:
    cfg = dict(_DEFAULT_CONFIG)
    extra = str(os.environ.get("STEP01_FROZEN_CONFIG") or "").strip()
    if extra:
        cfg.update({str(k): str(v) for k, v in json.loads(extra).items()})
    chapter = tmp_path / "chapter"
    result = tmp_path / "commit_result.json"
    log = (tmp_path / "child.log").open("w", encoding="utf-8")
    env = dict(os.environ)
    env.update(cfg)
    env.update({
        "SP_ROOT": str(_PROJECT_ROOT), "SP_CANDIDATE": _CANDIDATE,
        "SP_NOVEL": _NOVEL, "SP_CHAPTER": str(chapter), "SP_RESULT": str(result),
        "OUTPUT_BASE": str(tmp_path / "output"),
        "STEP01_LOG_PATH": str(tmp_path / "step01.log"),
        "TEXT_CALL_LEDGER": str(tmp_path / "calls.jsonl"),
    })
    try:
        subprocess.run([sys.executable, "-c", _CHILD], env=env, cwd=str(tmp_path),
                       stdout=log, stderr=log, check=False, timeout=600)
    finally:
        log.close()
    if not result.exists():
        raise AssertionError("子进程没有写出结果：%s" % (tmp_path / "child.log").read_text(
            encoding="utf-8", errors="replace")[-2000:])
    return {"chapter": chapter, "payload": json.loads(result.read_text(encoding="utf-8"))}


def test_frozen_candidate_commits_through_real_gates(tmp_path):
    from shared.quality_gate import compute_script_revision

    candidate = json.loads(Path(_CANDIDATE).read_text(encoding="utf-8"))
    entry_revision = compute_script_revision(candidate)
    run = _run_child(tmp_path)
    payload, chapter = run["payload"], run["chapter"]

    assert payload["status"] == "COMMITTED", payload
    # 不变量：离线交付工具报出的「提交投影 revision」必须等于提交入口实际写入的
    # revision（两处各算一遍曾产生两个 revision，审计只对后者有效）。
    assert not payload["projection_errors"], payload["projection_errors"]
    assert payload["projection_revision"] == payload["revision"], payload
    assert payload["calls"] == 1, "提交点必须**恰好**发一次（桩）审计"
    assert payload["band"] == {"min": 60.0, "max": 120.0} or payload["band"]["max"] == 120.0

    for name in ("script_result.json", "step01_quality.json",
                 "step01_audit.json", "step01_commit.json"):
        assert (chapter / name).exists(), name
    on_disk = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    quality = json.loads((chapter / "step01_quality.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "COMMITTED"
    assert manifest["script_revision"] == payload["revision"]
    assert compute_script_revision(on_disk) == payload["revision"]
    assert quality["status"] == "PASS", quality.get("status")
    roles = [e.get("voice_role") for e in
             (on_disk.get("chapter_outline") or {}).get("narration_lines") or []
             if isinstance(e, dict) and e.get("voice_role")]
    assert roles, "冻结候选的显式声源必须在落盘稿里保留"
    # 输入候选的 revision 与提交后的 revision 允许不同（投影会做 hook 归一/派生），
    # 但两者都必须存在且可算：用显式断言代替原来恒真的表达式。
    assert entry_revision, "进入提交点的候选必须能算出 revision"
    assert payload["revision"] and payload["revision"] != "", "提交后的 revision 必须非空"

    stub = chapter / "_stub_audit"
    stub.mkdir(exist_ok=True)
    (stub / "marker.json").write_text(json.dumps({
        "stub": True,
        "reason": "test_candidate_commit_real.py: only the audit model call is stubbed",
        "candidate": _CANDIDATE, "entry_revision": entry_revision,
        "revision": payload["revision"], "calls": payload["calls"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    assert (stub / "marker.json").exists()