import copy
import json

import pytest

from shared.quality_gate import verify_step01_commit_manifest
from step01_script_gen import main as m
from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
from tests.step01_script_gen.test_step01_commit import chapter, _script, _fake_audit, _audit_payload


def test_commit_audits_the_persisted_projection_and_disk_identity(chapter, monkeypatch):
    script = _script()
    script["chapter_outline"] = {"plan_verification": {"state": "accepted", "_call_evidence": {"request": "runtime-only"}}}
    original_plan = copy.deepcopy(script["chapter_outline"])
    seen = []
    def factory(s, n):
        seen.append(copy.deepcopy(s))
        return _audit_payload(s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT)
    _fake_audit(monkeypatch, factory)
    final, summary = m._commit_step01_outputs(script, "")
    assert summary["status"] == "COMMITTED"
    disk = json.loads((chapter / "script_result.json").read_text(encoding="utf8"))
    verify_step01_commit_manifest(chapter, disk)
    assert "_call_evidence" not in seen[-1]["chapter_outline"]["plan_verification"]
    assert disk["script_revision"] == summary["revision"] == final["script_revision"]
    assert original_plan["plan_verification"]["_call_evidence"] == {"request": "runtime-only"}


@pytest.mark.parametrize("phase", ["staged", "formal"])
def test_commit_readback_rejects_serialized_content_drift(chapter, monkeypatch, phase):
    import shared.json_io as io
    real = io.atomic_dump_json
    fired = []
    def write(path, payload, **kwargs):
        real(path, payload, **kwargs)
        is_stage = path.parent.name == "_staged"
        if path.name == "script_result.json" and is_stage == (phase == "staged"):
            altered = json.loads(path.read_text(encoding="utf8"))
            altered["shots"][0]["ff_desc"] += "serialization drift"
            real(path, altered, **kwargs)
            fired.append(path)
    monkeypatch.setattr(io, "atomic_dump_json", write)
    _fake_audit(monkeypatch, lambda s,n: _audit_payload(s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")
    assert fired
    assert summary["status"] == "BLOCK"
    assert ("staged 自验失败" if phase == "staged" else "提交后自验失败") in summary["detail"]
    if phase == "staged":
        assert not (chapter / "script_result.json").exists()


# ---------------------------------------------------------------------------
# 已提交产物字节级只读：step02 派生指标必须落 sidecar（2026-10 复核 P0）
# ---------------------------------------------------------------------------
def test_step02_video_metrics_sidecar_keeps_committed_artifact_readonly(
        chapter, monkeypatch, tmp_path):
    """回归：step02b 的实际时长指标不得写回 `script_result.json`.

    修复前的真实链路：step02b 生成完视频后调 `_update_script_with_actual_durations`，
    它把 `actual_duration` / `duration_deviation_pct` 写回 `script_result.json` ⇒
    该文件 SHA1 与 `step01_commit.json` 里锁定的摘要失配 ⇒ step03 / step04 以及
    任何重跑都被真实门 `verify_step01_commit_manifest` 判
    `artifact changed after commit` 拒收（跨阶段硬阻断）。

    本用例走**真实提交点**写盘、**真实**测量入口、**真实**门校验，只打桩
    ffprobe 测量值（不打桩会启动外部进程）。断言：
      ① 派生指标确实产出到 `step02_video_metrics.json`（数据没丢）；
      ② `script_result.json` 字节与摘要都不变；
      ③ 真实门**仍然通过**——这正是修复前必然失败的那一步。
    """
    import hashlib

    import step01_script_gen.main as m
    from shared.paths import SHOT_VIDEO_FILENAME, STEP02_VIDEO_METRICS_FILENAME
    from step02_video_gen import audio_pregen

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    _final, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "COMMITTED"

    script_path = chapter / "script_result.json"
    before_bytes = script_path.read_bytes()
    before_digest = hashlib.sha1(before_bytes).hexdigest()
    assert before_digest == _sha1_of_manifest(chapter, "script_result.json")

    # 造一个"已生成视频" + 打桩测量值（7.5s vs 期望 5.0s ⇒ 偏差 50%）
    work_dir = tmp_path / "work"
    shot_dir = work_dir / "shots" / "1"
    shot_dir.mkdir(parents=True)
    (shot_dir / SHOT_VIDEO_FILENAME).write_bytes(b"not-a-real-video")
    monkeypatch.setattr(audio_pregen, "_measure_actual_duration", lambda p: 7.5)

    out = audio_pregen.write_actual_durations_metrics(work_dir, script_path)

    # ① 派生数据确实落到了 sidecar，且与产物同目录
    assert out is not None, "有测量结果时必须产出 sidecar"
    assert out.name == STEP02_VIDEO_METRICS_FILENAME
    assert out.parent == script_path.parent, "sidecar 必须与产物同目录"
    metrics = json.loads(out.read_text(encoding="utf-8"))
    assert metrics["shots"]["1"] == {
        "actual_duration": 7.5,
        "expected_duration": 5.0,
        "duration_deviation_pct": 50.0,
    }
    assert metrics["updated_count"] == 1
    assert metrics["total_actual_duration"] == 7.5
    assert metrics["script_revision"] == summary["revision"], "sidecar 要记产物身份"

    # ② 已提交产物字节级只读
    assert script_path.read_bytes() == before_bytes
    assert hashlib.sha1(script_path.read_bytes()).hexdigest() == before_digest

    # ③ 真实门仍然通过（修复前这一步抛 QualityGateError）
    disk = json.loads(script_path.read_text(encoding="utf8"))
    verify_step01_commit_manifest(chapter, disk)


def test_step02_video_metrics_no_measurement_writes_nothing(chapter, monkeypatch, tmp_path):
    """对照：测不到任何实际时长时**不写** sidecar，且不碰已提交产物."""
    import step01_script_gen.main as m
    from shared.paths import STEP02_VIDEO_METRICS_FILENAME
    from step02_video_gen import audio_pregen

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    _final, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "COMMITTED"

    script_path = chapter / "script_result.json"
    before_bytes = script_path.read_bytes()
    empty_work = tmp_path / "empty_work"       # shots/ 不存在
    out = audio_pregen.write_actual_durations_metrics(empty_work, script_path)

    assert out is None
    assert not (chapter / STEP02_VIDEO_METRICS_FILENAME).exists()
    assert script_path.read_bytes() == before_bytes
    verify_step01_commit_manifest(chapter, json.loads(script_path.read_text(encoding="utf8")))


def _sha1_of_manifest(chapter, name: str) -> str:
    """从真实 manifest 里读某个产物的摘要（作为「锁定值」的独立来源）."""
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf8"))
    assert manifest["status"] == "COMMITTED"
    return str(manifest["artifacts"][name]["sha1"]).lower()
