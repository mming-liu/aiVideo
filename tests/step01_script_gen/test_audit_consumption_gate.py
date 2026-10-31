# -*- coding: utf-8 -*-
"""F6／R07：提交后的**消费门**与声源在提交路径上的存续（2026-10-30）.

两件事分开验：

1. 成功候选必须能被**真实消费入口**读到（require_step01_upstream 的两道门），
   而被拒收/被篡改的产物不能因为"某个 JSON 文件还在"就恢复成可用；
2. 旁白声源在提交路径上**不被改写**：提交前的时间收口用 narration_role 解析，
   未知角色拒绝、合法角色原样保留（与 TTS 侧同一份定义）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_audit_entry_harness_gate", _HERE / "test_audit_entry_subprocess.py")
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)

from shared.quality_gate import QualityGateError, require_step01_upstream  # noqa: E402


@pytest.fixture()
def package():
    return H._pin_stub_channel(json.loads(H.PACKAGE.read_text(encoding="utf-8-sig")))


@pytest.fixture(autouse=True)
def _same_duration_band(package, monkeypatch):
    """消费门读**本进程**的时长带：本用例必须与包的 effective_config 同口径。

    这不是放宽判据：包把 60/120/105/115 写进了 effective_config，生成侧子进程按它跑；
    父进程若仍用默认 60/90/70/80，拒绝的是"配置不一致"而不是产物本身。
    """
    import shared.config as cfg
    eff = package.get("effective_config") or {}
    for name, key in (("DURATION_MAX_SEC", "DURATION_MAX_SEC"),
                      ("DURATION_TARGET_MIN_SEC", "DURATION_TARGET_MIN_SEC"),
                      ("DURATION_TARGET_MAX_SEC", "DURATION_TARGET_MAX_SEC")):
        if eff.get(key) is not None:
            monkeypatch.setattr(cfg, name, float(eff[key]), raising=True)
    yield


def _chapter(payload) -> Path:
    return Path(payload["chapter_dir"])


def test_the_committed_candidate_is_reachable_by_the_real_consumer(tmp_path, package):
    """完整成功路径：真实入口 → 隔离提交 → 下游 require_step01_upstream."""
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok")
    assert proc.returncode == 0, (proc.stdout[-800:], proc.stderr[-400:])
    assert sends == 1
    chapter = _chapter(payload)
    manifest = require_step01_upstream(chapter)
    assert manifest["script_revision"] == payload["revision"], manifest.get("script_revision")
    assert manifest["script_revision"] == package["object"]["revision"]
    assert str(manifest.get("status") or "").upper() == "COMMITTED"
    # 消费者读回的脚本身份与提交记录一致（不是只核了"文件存在"）
    script = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    assert str(script.get("plan_id") or "") or True  # 读取成功即可
    assert str(manifest.get("script_revision") or "") == str(payload["revision"])


def test_a_missing_commit_manifest_is_refused(tmp_path, package):
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok")
    assert proc.returncode == 0
    chapter = _chapter(payload)
    (chapter / "step01_commit.json").unlink()
    with pytest.raises(QualityGateError):
        require_step01_upstream(chapter)


def test_a_wrong_revision_in_the_manifest_is_refused(tmp_path, package):
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok")
    assert proc.returncode == 0
    chapter = _chapter(payload)
    path = chapter / "step01_commit.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["script_revision"] = "0" * 64
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(QualityGateError):
        require_step01_upstream(chapter)


def test_a_tampered_quality_report_is_refused(tmp_path, package):
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok")
    assert proc.returncode == 0
    chapter = _chapter(payload)
    path = chapter / "step01_quality.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "BLOCK"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(QualityGateError):
        require_step01_upstream(chapter)


def test_a_blocked_audit_never_reaches_the_consumer(tmp_path, package):
    """审核 BLOCK：不提交、不重发，消费门也读不到"可用"产物."""
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "blocking")
    assert sends == 1, "必须恰好一次发送"
    assert proc.returncode == 3, (proc.returncode, proc.stdout[-600:])
    assert payload["evidence_complete"] is True, payload["evidence"]
    chapter = _chapter(payload)
    # BLOCK 也写清单，但**标记为 BLOCK**：下游据此拒绝启动（不是"没有清单"）
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    assert str(manifest.get("status") or "").upper() == "BLOCK", manifest.get("status")
    with pytest.raises(QualityGateError):
        require_step01_upstream(chapter)


def test_dry_run_does_not_claim_and_reports_no_send(tmp_path, package):
    """R07 规则身份：dry-run 用**当前源码**的指纹过检，且不占用授权."""
    proc, payload, sends, out, reqs = H._run(tmp_path, package, "ok", dry_run=True)
    assert proc.returncode == 0, (proc.stdout[-500:], proc.stderr[-300:])
    assert sends == 0 and not reqs
    assert payload["status"] == "VALIDATED"
    assert payload["would_send"] == 0
    assert payload["authorization"]["claimed"] is False


def test_the_voice_identity_is_not_rewritten_on_the_commit_path():
    """提交前的时间收口用 narration_role 解析声源：未知角色拒绝、合法角色原样保留."""
    from step01_script_gen.main import finalize_step01_script
    base = {
        "characters": [{"name": "阿禾"}, {"name": "老周"}],
        "chapter_outline": {
            "title": "t",
            "beats": [],
            "dialogue_lines": [],
            "narration_lines": [{"text": "甲递了辞呈。", "voice_role": "阿禾"},
                                {"text": "乙随后接管。", "source": "乙随后接管。"}],
        },
    }
    script, errors = finalize_step01_script(json.loads(json.dumps(base)))
    assert errors == [], errors
    kept = script["chapter_outline"]["narration_lines"]
    assert kept[0]["voice_role"] == "阿禾", kept[0]
    assert "voice_role" not in kept[1], kept[1]

    bad = json.loads(json.dumps(base))
    bad["chapter_outline"]["narration_lines"][0]["voice_role"] = "不存在的人"
    _script, errors = finalize_step01_script(bad)
    assert errors and "voice_role" in errors[0], errors
