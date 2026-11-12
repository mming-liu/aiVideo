# -*- coding: utf-8 -*-
"""U6 零调用补验：一次审计 = 至多一次实际模型发送.

只桩 `audit.create_service`（逻辑调用出口），不触碰任何真实 provider/网络。
同时验证：传输重试预算、解析重采样预算、A3 补全预算、发送守卫的第二次发送拒绝。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ["TEXT_TRANSPORT_RETRIES"] = "0"
os.environ["TEXT_TRANSPORT_CONNECT_RETRIES"] = "0"
os.environ["AUDIT_PARSE_RETRIES"] = "0"

from _text_gen import retry_policy as rp  # noqa: E402
from _text_gen.models import TextGenerationResult  # noqa: E402
import step01_script_gen.audit as audit  # noqa: E402


BLOCKING_NO_TARGETS = json.dumps({
    "issues": [{
        "code": "STATE_CHAIN_CONFLICT",
        "severity": "blocking",
        "shot_id": "8",
        "frame_id": "ff",
        "entity_id": "听香",
        "field_path": "frame_layouts.ff",
        "evidence": "堤岸",
        "impact": "画外人物出现在岸上",
        "minimal_fix": "删除 ff 中的听香条目",
    }]
}, ensure_ascii=False)


class FakeService:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def generate(self, req):
        self.calls.append(req)
        return TextGenerationResult(success=True, text=self.text, truncated=False)


def main() -> int:
    result = {
        "env": {k: os.getenv(k) for k in
                ("TEXT_TRANSPORT_RETRIES", "TEXT_TRANSPORT_CONNECT_RETRIES",
                 "AUDIT_PARSE_RETRIES")},
        "submit_attempts": {fc.value: rp.submit_attempts(fc)
                            for fc in rp.FailureClass},
        "provider_retry": {
            "deepseek_sdk_max_retries": 0,
            "agent_plan_guarded_second_attempt": (
                "provider 在 attempt>1 且绑定守卫时直接返回失败，不再发送"),
        },
    }

    fake = FakeService(BLOCKING_NO_TARGETS)
    audit.create_service = lambda *a, **k: fake
    audit_result = audit.run_same_model_audit_with_status(
        "原文", {}, {"shots": []}, model="stub-model")
    result["audit_with_blocking_missing_targets"] = {
        "logical_calls": len(fake.calls),
        "attempts": audit_result.attempts,
        "status": audit_result.status,
        "issues": len(audit_result.issues),
        "completion": audit_result.completion,
        "note": ("AUDIT_PARSE_RETRIES=0 ⇒ 首调后 remaining_calls=0，"
                 "A3 补全未发起；blocking 保留原样。"),
    }

    # 发送守卫：同一授权第二次 before_send 必须被拒（不进入 SDK）。
    from shared.audit_authorization import AuditAuthorization, SendNotPermitted
    with tempfile.TemporaryDirectory(prefix="u6_auth_") as tmp:
        guard = AuditAuthorization(authorization_id="u6probe01",
                                   binding={"schema": "u6.probe/1"},
                                   state_dir=Path(tmp), run_id="u6probe")
        first = guard.before_send(provider="stub", model="stub",
                                  endpoint="stub", call_id="call-1")
        second_error = ""
        try:
            guard.before_send(provider="stub", model="stub",
                              endpoint="stub", call_id="call-2")
        except SendNotPermitted as exc:
            second_error = str(exc)
        result["send_guard"] = {
            "first_attempt_id": first,
            "second_send_refused": bool(second_error),
            "second_error": second_error,
            "max_sends": 1,
        }

    out = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110" / "u6_single_send_controls.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
