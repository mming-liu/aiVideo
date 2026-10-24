# -*- coding: utf-8 -*-
"""仅核验冻结候选（一次实际发送）：不初始化、不修订、不覆盖候选."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import content_verification as cv
from shared import presentation_contract as pc
from step01_script_gen import outline as o
from step01_script_gen.content_verify import verify_view_with_service
from tools import minimal_real_run as m

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--novel", required=True)
    args = ap.parse_args()
    raw_root = os.environ.get("DSH_EXP_ROOT", "").strip()
    if not raw_root:
        raise SystemExit("DSH_EXP_ROOT 未设置：0 次发送，中止")
    root = Path(raw_root)
    root.mkdir(parents=True, exist_ok=True)
    run = root / ("verify_" + time.strftime("%Y%m%dT%H%M%S")
                  + "_%d" % (time.time_ns() % 1000000))
    run.mkdir(parents=True, exist_ok=False)
    probe = run / "_preflight.json"
    try:
        probe.write_text(json.dumps({"schema": "verify.preflight.v1"}),
                         encoding="utf-8")
        json.loads(probe.read_text(encoding="utf-8"))
    except Exception as exc:                                      # noqa: BLE001
        raise SystemExit("预检失败：0 次发送；%r" % (exc,))
    plan = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    novel = Path(args.novel).read_text(encoding="utf-8")
    if not isinstance(plan, dict) or not novel.strip():
        raise SystemExit("候选/原文不可读：0 次发送")
    view, vp = pc.view_from_plan(plan)
    if not view or vp:
        raise SystemExit("候选视图不可读：0 次发送；%r" % (vp,))
    digest = cv.plan_digest_of(plan)
    nd = hashlib.sha256(novel.encode("utf-8")).hexdigest()
    m.SENDS.update({"cap": 1, "logical": 0, "attempts": 0,
                    "abort": False, "calls": [], "sends": []})
    m._install_send_budget(run)
    m._cap_service(run)
    from _text_gen.config import config
    if config.PROVIDER != "agent_plan":
        raise SystemExit("有效通道不是 OpenCode Go：0 次发送，停止")
    os.environ["DSH_CAPTURE_PAYLOAD"] = str(run / "payloads")
    # 必须经控制器：由它盖章 rule_version / plan_digest / novel_digest /
    # obligation_basis；直接调 verify_view_with_service 会得到缺元数据的记录。
    _ctl = cv.VerificationController(
        verify=lambda _v, _d: verify_view_with_service(
            _v, _d, novel_text=novel, novel_digest=nd),
        max_verifications=1,
        novel_digest=nd, rule_version=cv.DEFAULT_RULE_VERSION)
    rec = _ctl.record_for(view, digest)
    m._write(run / "record.json", rec)
    attached = dict(plan)
    attached["plan_verification"] = rec
    probs = o.plan_acceptance_problems(attached, novel)
    m._write(run / "acceptance_problems.json", probs)
    m._write(run / "result.json", {
        "candidate": str(Path(args.candidate).resolve()),
        "candidate_digest": digest, "novel_digest": nd,
        "sends": m.SENDS["attempts"],
        "record_status": (rec.get("status") if isinstance(rec, dict) else None),
        "voice_units": ([str(x.get("unit_uid")) for x in
                         (rec.get("voice_units") or []) if isinstance(x, dict)]
                        if isinstance(rec, dict) else []),
        "acceptance_codes": [str(x.get("code")) for x in probs],
    })
    print("verify done:", json.dumps({
        "sends": m.SENDS["attempts"],
        "record_status": (rec.get("status") if isinstance(rec, dict) else None),
        "voice_units": ([str(x.get("unit_uid")) for x in
                         (rec.get("voice_units") or []) if isinstance(x, dict)]
                        if isinstance(rec, dict) else []),
        "acceptance_codes": [str(x.get("code")) for x in probs]},
        ensure_ascii=False))
    print("run:", run)


if __name__ == "__main__":
    main()
