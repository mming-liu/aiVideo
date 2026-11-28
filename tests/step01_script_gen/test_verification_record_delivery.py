"""T2：核验记录落盘与挂载只认 `status == "ok"`。

根因（docs/archive/step01-handoff-2026-10-22-deepseek.md §4 T2）：
`if rec:` 不看 `status`，失败记录被当成合法核验记录挂到计划/写到
`verification_record.json`，下一个消费者把它读成“已有核验记录”。
"""
import json

from step01_script_gen import content_verify as cv
from step01_script_gen import outline
from shared.content_verification import SCHEMA_VERIFICATION
from tests.step01_script_gen.test_legacy_semantic import NOVEL, plan_for_narration


def _record(status, *, code="", schema=SCHEMA_VERIFICATION):
    return {"schema": schema, "status": status, "code": code,
            "facts": [], "voice_units": []}


def test_attach_verification_does_not_mount_failed_record():
    """失败记录不得挂成候选计划上的 `plan_verification`。"""
    seen = _record("bad_response", code="verify_bad_response")

    class Controller:
        require_coherence = False

        def record_for(self, view, digest, verify=None):
            return seen

    checked = cv.attach_verification(plan_for_narration(), Controller())
    assert "plan_verification" not in checked
    # 失败原因不能因此丢失：另挂诊断键，验收仍按它的 code fail-closed。
    assert checked["verification_rejected"]["status"] == "bad_response"
    assert checked["verification_rejected"]["code"] == "verify_bad_response"


def test_acceptance_keeps_the_failure_code_and_does_not_read_it_as_missing():
    """失败记录不挂 `plan_verification`，但验收不能把失败吞成“缺记录”。"""
    seen = _record("bad_response", code="verify_bad_response")

    class Controller:
        require_coherence = False

        def record_for(self, view, digest, verify=None):
            return seen

    checked = cv.attach_verification(plan_for_narration(), Controller())
    codes = [p["code"] for p in outline.plan_acceptance_problems(checked, NOVEL)]
    assert "verify_bad_response" in codes
    assert "verify_record_missing" not in codes


def test_rejected_record_is_persisted_and_run_log_names_status(tmp_path):
    """非 ok 记录另存 `.rejected.json`，并在 `run_log['verify']` 写明失败状态。"""
    log = {}
    result = cv.deliver_verification_record(
        _record("bad_response", code="verify_bad_response"),
        run_dir=tmp_path, log=log, transmitted=1)

    rejected = tmp_path / "verification_record.rejected.json"
    assert rejected.exists()
    assert json.loads(rejected.read_text(encoding="utf-8"))["status"] == "bad_response"
    assert not (tmp_path / "verification_record.json").exists()
    assert result["accepted"] is False
    assert log["verify"]["transmitted"] == 1
    assert log["verify"]["status"] == "bad_response"
    assert log["verify"]["rejected_status"] == "bad_response"
    assert log["verify"]["rejected_code"] == "verify_bad_response"


def test_ok_record_is_persisted_as_the_canonical_record(tmp_path):
    """只有 ok 记录能占用规范文件名；同一目录的旧 rejected 文件被清掉。"""
    rejected = tmp_path / "verification_record.rejected.json"
    rejected.write_text(json.dumps(_record("bad_response")), encoding="utf-8")
    log = {}
    result = cv.deliver_verification_record(
        _record("ok"), run_dir=tmp_path, log=log, transmitted=1)

    canonical = tmp_path / "verification_record.json"
    assert result["accepted"] is True
    assert json.loads(canonical.read_text(encoding="utf-8"))["status"] == "ok"
    assert not rejected.exists()
    assert "rejected_status" not in log["verify"]


def test_rejected_delivery_removes_a_stale_canonical_record(tmp_path):
    """失败轮次不得留下上一轮的规范记录（否则下次装载会读到旧的成功记录）。"""
    canonical = tmp_path / "verification_record.json"
    canonical.write_text(json.dumps(_record("ok")), encoding="utf-8")

    result = cv.deliver_verification_record(
        _record("input_admission_failed", code="verify_input_rejected"),
        run_dir=tmp_path, log={}, transmitted=0)

    assert result["accepted"] is False
    assert not canonical.exists()
    assert (tmp_path / "verification_record.rejected.json").exists()
