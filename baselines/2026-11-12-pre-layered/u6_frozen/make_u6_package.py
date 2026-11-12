# -*- coding: utf-8 -*-
"""生成 U6 冻结候选审计包（零调用：只读身份 + 写包文件，不发送）.

包内 budget.remaining=0 / new_authorization 空 ⇒ `-Execute` 必然被
`budget_exhausted` 拒收；只有用户确认费用并填入一次性授权后才可发送。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110" / "u6_package"
PKG_DIR.mkdir(parents=True, exist_ok=True)

CAND = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110" / "candidate_d1a" / "candidate.json"
NOVEL = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110" / "u6_inputs" / "novel_chapter_001.txt"
QUALITY = (ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110" / "iso" / "output"
           / "锦衣夜行" / "part_01_杀青州" / "chapter_001" / "step01_quality.json")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def main() -> int:
    candidate = json.loads(CAND.read_text(encoding="utf-8-sig"))
    quality = json.loads(QUALITY.read_text(encoding="utf-8"))
    shots = candidate.get("shots") or []
    body = round(sum(float(s.get("duration_sec") or 0) for s in shots), 2)
    total = candidate.get("total_duration")
    total = round(float(total), 2) if total is not None else body
    pkg = {
        "schema": "step01_audit_package.v1",
        "created_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "u6_isolated_audit_pending_user_authorization",
        "object": {
            "candidate": rel(CAND),
            "candidate_sha256": sha256(CAND),
            "novel": rel(NOVEL),
            "novel_sha256": sha256(NOVEL),
            "quality_report": rel(QUALITY),
            "quality_sha256": sha256(QUALITY),
            "quality_status": str(quality.get("status") or ""),
            "revision": str(quality.get("script_revision") or ""),
            "shot_count": len(shots),
            "main_duration_sec": body,
            "body_duration_sec": body,
            "total_duration_sec": total,
            "note": ("D1-A 冻结候选：仅 shot 1 画面字段与 shot 8 画外在场条目变更；"
                     "revision 由生产 project_for_commit 复算。质量报告来自隔离章同一投影，"
                     "PASS / fingerprint=593deea03c7cddc6。"),
        },
        "effective_config": {
            "HOOK_ENABLED": "0",
            "DURATION_MIN_SEC": "60",
            "DURATION_MAX_SEC": "90",
            "DURATION_TARGET_MIN_SEC": "70",
            "DURATION_TARGET_MAX_SEC": "80",
            "VOICE_ADAPTATION_ENABLED": "0",
        },
        "single_send_controls": {
            "TEXT_TRANSPORT_RETRIES": "0",
            "TEXT_TRANSPORT_CONNECT_RETRIES": "0",
            "AUDIT_PARSE_RETRIES": "0",
            "REPAIR_ROUNDS_ENABLED": "0",
            "STEP01_AUDIT_ONLY": "1",
        },
        "audit_channel": {
            "provider": "agent_plan",
            "model": "deepseek-v4.1-flash",
            "note": ("实际通道来自 .env.content：TEXT_PROVIDER=agent_plan、"
                     "AUDIT_TEXT_PROVIDER=agent_plan、AUDIT_TEXT_MODEL=deepseek-v4.1-flash；"
                     "入口会按生效配置复核端点。"),
        },
        "audit_contract": {
            "audit_prompt_version": "a5929a9f6abc",
            "rule_source_fingerprint": "593deea03c7cddc6",
            "duration_band": {"min": 60, "max": 90, "target_min": 70, "target_max": 80},
            "audit_scope": "final_after_deterministic_fixes",
            "note": ("规则身份来自当前源码 + 生效配置；旧审计指纹 72dcb353990a2071 "
                     "已失效，不得复用。"),
        },
        "budget": {
            "authorized_total": 1,
            "used": 0,
            "remaining": 0,
            "unlimited": False,
            "new_authorization": {
                "schema": "step01.audit_authorization/1",
                "authorization_id": "",
                "granted_by": "",
                "granted_at": "",
                "max_sends": 1,
                "binding_digest": "",
                "note": ("待用户确认费用后填写：authorization_id（稳定、跨运行可识别）、"
                         "granted_by、granted_at，并把 remaining 改为 1；"
                         "binding_digest 由 --print-binding-digest 计算后逐字保留。"),
            },
        },
        "launch": {
            "entry": "tools/audit_frozen_candidate.py",
            "dry_run": ("python tools/audit_frozen_candidate.py --package "
                        ".pytest_tmp/u1_u5_closeout_20261110/u6_package/audit_package.json "
                        "--dry-run --out-root .pytest_tmp/u1_u5_closeout_20261110/u6_package/dryrun"),
            "execute": ("python tools/audit_frozen_candidate.py --package "
                        ".pytest_tmp/u1_u5_closeout_20261110/u6_package/audit_package.json "
                        "--out-root .pytest_tmp/u1_u5_closeout_20261110/u6_package/run"),
            "requires": ("默认只跑 dry-run（零发送）；真实调用需用户确认费用、填好一次性授权，"
                         "并显式设置 STEP01_AUDIT_AUTHORIZED=1。"),
            "out_root_must_not_exist": True,
        },
        "scope": {"novel": "锦衣夜行", "book_index": 1,
                  "book_name": "杀青州", "chapter": 1},
        "expected_readings": {
            "sends": 1,
            "repair_calls": 0,
            "second_audit": 0,
            "audit_only_on_blocking": True,
            "commit_on_pass": True,
            "note": ("sends 取 budget_receipt.json 的 attempts（守卫记录的 SDK 发送边界），"
                     "不是调用台账行数。"),
        },
    }
    path = PKG_DIR / "audit_package.json"
    path.write_text(json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
