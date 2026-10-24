# -*- coding: utf-8 -*-
"""仅核验冻结候选：显式清单 → 输入身份校验 → 控制器 → 一次发送.

清单格式（`step01.verify_frozen.v1`）：

{
  "schema": "step01.verify_frozen.v1",
  "candidate": {"path": "...", "plan_digest": "..."},
  "novel": {"path": "...", "format": "sample|novel_text", "digest": "..."},
  "revision_run": "..."
}

- `sample` 只提取 `sample["novel"]` 字符串；`novel_text` 只读纯正文。
- 所有路径相对清单目录；身份不匹配在创建 service 前退出。
- 本入口不初始化、不修订、不覆盖候选。
"""
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

MANIFEST_SCHEMA = "step01.verify_frozen.v1"
SAMPLE_SCHEMA = "step01.sample.v1"


class InputIdentityError(RuntimeError):
    pass


def _resolve(base: Path, value) -> Path:
    p = Path(str(value or ""))
    return p if p.is_absolute() else (base / p)


def extract_novel(spec: dict, base: Path) -> tuple:
    """返回 `(novel_text, source_path, format)`；格式错误直接拒收，不猜 JSON."""
    _fmt = str(spec.get("format") or "")
    _src = _resolve(base, spec.get("path"))
    if _fmt == "sample":
        data = json.loads(_src.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != SAMPLE_SCHEMA:
            raise InputIdentityError("样本包 schema 不是 %s" % SAMPLE_SCHEMA)
        _novel = data.get("novel")
        if not isinstance(_novel, str) or not _novel.strip():
            raise InputIdentityError("样本包缺少非空字符串 novel")
        return _novel, _src, _fmt
    if _fmt == "novel_text":
        _novel = _src.read_text(encoding="utf-8")
        if not _novel.strip():
            raise InputIdentityError("纯正文文件为空")
        return _novel, _src, _fmt
    raise InputIdentityError("novel.format 必须是 sample 或 novel_text")


def preflight(manifest_path: Path) -> dict:
    """只做读取与身份校验；不创建 service、不发送."""
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
        raise InputIdentityError("清单 schema 不是 %s" % MANIFEST_SCHEMA)
    base = manifest_path.parent
    _cand = data.get("candidate") or {}
    _novel = data.get("novel") or {}
    if not isinstance(_cand, dict) or not isinstance(_novel, dict):
        raise InputIdentityError("candidate/novel 必须是对象")
    cand_path = _resolve(base, _cand.get("path"))
    plan = json.loads(cand_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise InputIdentityError("候选不是 JSON 对象")
    plan_digest = cv.plan_digest_of(plan)
    if not str(_cand.get("plan_digest") or "") or plan_digest != str(_cand["plan_digest"]):
        raise InputIdentityError("候选 plan_digest 不匹配：实际 %s，清单 %r"
                                 % (plan_digest, _cand.get("plan_digest")))
    novel_text, novel_path, novel_fmt = extract_novel(_novel, base)
    novel_digest = hashlib.sha256(novel_text.encode("utf-8")).hexdigest()
    if not str(_novel.get("digest") or "") or novel_digest != str(_novel["digest"]):
        raise InputIdentityError("原文 novel_digest 不匹配：实际 %s，清单 %r"
                                 % (novel_digest, _novel.get("digest")))
    _run = _resolve(base, data.get("revision_run"))
    if not _run.is_dir():
        raise InputIdentityError("revision_run 不存在：%s" % _run)
    _run_cand = _run / "candidate_plan.json"
    if not _run_cand.is_file():
        raise InputIdentityError("revision_run 缺少 candidate_plan.json：%s" % _run)
    _run_plan = json.loads(_run_cand.read_text(encoding="utf-8"))
    if cv.plan_digest_of(_run_plan) != plan_digest:
        raise InputIdentityError("revision_run 的候选与本次候选身份不一致")
    view, vp = pc.view_from_plan(plan)
    if not view or vp:
        raise InputIdentityError("候选视图不可读：%r" % (vp,))
    return {"manifest": data, "manifest_path": manifest_path,
            "candidate_path": cand_path, "plan": plan, "plan_digest": plan_digest,
            "novel_path": novel_path, "novel_format": novel_fmt,
            "novel_text": novel_text, "novel_digest": novel_digest,
            "revision_run": _run, "view": view}


def _previous_text(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("raw_response_text"), str):
        return data["raw_response_text"]
    if isinstance(data, str):
        return data
    raise InputIdentityError("previous_response 不是带 raw_response_text 的 JSON 或文本")


def _load_correction(path: Path, info: dict, previous_path: Path) -> dict:
    rec = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rec, dict):
        raise InputIdentityError("纠正记录不是 JSON 对象")
    if str(rec.get("plan_digest") or "") != info["plan_digest"]:
        raise InputIdentityError("纠正记录的 plan_digest 与冻结候选不一致")
    _rec_novel = str(rec.get("novel_digest") or "")
    if _rec_novel:
        if _rec_novel != info["novel_digest"]:
            raise InputIdentityError("纠正记录的 novel_digest 与冻结原文不一致")
    else:
        # 失败记录可能没有盖原文摘要；从同一运行的 input_identity 核对来源身份。
        _ident = Path(path).parent / "input_identity.json"
        if not _ident.is_file():
            raise InputIdentityError("纠正记录缺 novel_digest，且无同源 input_identity")
        _id = json.loads(_ident.read_text(encoding="utf-8"))
        if str(_id.get("novel_digest") or "") != info["novel_digest"]:
            raise InputIdentityError("纠正记录所在运行的原文身份与冻结原文不一致")
    if str(rec.get("status") or "") == "ok":
        raise InputIdentityError("纠正记录 status=ok，不是失败记录")
    problems = rec.get("problems")
    if not isinstance(problems, list) or not problems:
        raise InputIdentityError("纠正记录没有具体协议问题")
    return {"record": rec, "previous_response": _previous_text(Path(previous_path)),
            "problems": problems, "error": str(rec.get("error") or "")}


def run(manifest_path: Path, *, root=None, dry=False,
        correction_record=None, previous_response=None,
        service_verify=None) -> dict:
    """先 preflight（零发送），再创建运行目录、落盘冻结输入、核验一次."""
    info = preflight(Path(manifest_path))
    _corr = None
    if correction_record:
        if not previous_response:
            raise InputIdentityError("纠正模式必须同时提供 previous_response")
        _corr = _load_correction(Path(correction_record), info,
                                 Path(previous_response))
    if root is None:
        raw_root = os.environ.get("DSH_EXP_ROOT", "").strip()
        if not raw_root:
            raise InputIdentityError("DSH_EXP_ROOT 未设置：0 次发送，中止")
        root = Path(raw_root)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / ("verify_" + time.strftime("%Y%m%dT%H%M%S")
                      + "_%d" % (time.time_ns() % 1000000))
    run_dir.mkdir(parents=True, exist_ok=False)
    m._write(run_dir / "frozen_manifest.json", info["manifest"])
    (run_dir / "extracted_novel.txt").write_text(info["novel_text"],
                                                 encoding="utf-8")
    m._write(run_dir / "input_identity.json", {
        "candidate": str(info["candidate_path"]),
        "candidate_digest": info["plan_digest"],
        "novel_source": str(info["novel_path"]),
        "novel_format": info["novel_format"],
        "novel_digest": info["novel_digest"],
        "revision_run": str(info["revision_run"]),
    })
    if _corr:
        m._write(run_dir / "correction_input.json", {
            "record": _corr["record"], "problems": _corr["problems"],
            "error": _corr["error"],
            "previous_response": _corr["previous_response"]})
    if dry:
        return {"dry": True, "run_dir": str(run_dir), "sends": 0,
                "candidate_digest": info["plan_digest"],
                "novel_digest": info["novel_digest"],
                "correction": bool(_corr)}
    m.SENDS.update({"cap": 1, "logical": 0, "attempts": 0,
                    "abort": False, "calls": [], "sends": []})
    m._install_send_budget(run_dir)
    m._cap_service(run_dir)
    from _text_gen.config import config
    if config.PROVIDER != "agent_plan":
        raise InputIdentityError("有效通道不是 OpenCode Go：0 次发送，停止")
    os.environ["DSH_CAPTURE_PAYLOAD"] = str(run_dir / "payloads")
    _send = service_verify or verify_view_with_service

    def _normal(_v, _d):
        return _send(_v, _d, novel_text=info["novel_text"],
                     novel_digest=info["novel_digest"])

    def _repair(_v, _d, failure, previous):
        return _send(_v, _d, novel_text=info["novel_text"],
                     novel_digest=info["novel_digest"],
                     correction={"problems": failure.get("problems", []),
                                 "error": failure.get("error", ""),
                                 "previous_response": previous})

    def _validate(rec, _v, _d):
        return cv.record_protocol_problems(
            rec, _v, plan_digest=_d, novel_text=info["novel_text"],
            novel_digest=info["novel_digest"])

    _ctl = cv.VerificationController(
        verify=_normal, repair=_repair, validate_record=_validate,
        max_verifications=1, novel_digest=info["novel_digest"],
        rule_version=cv.DEFAULT_RULE_VERSION)
    if _corr:
        rec = _ctl._correct_record(
            info["view"], info["plan_digest"], _corr["record"],
            previous_response=_corr["previous_response"])
    else:
        rec = _ctl.record_for(info["view"], info["plan_digest"])
    m._write(run_dir / "record.json", rec)
    attached = dict(info["plan"])
    attached["plan_verification"] = rec
    probs = o.plan_acceptance_problems(attached, info["novel_text"])
    m._write(run_dir / "acceptance_problems.json", probs)
    result = {
        "candidate": str(info["candidate_path"]),
        "candidate_digest": info["plan_digest"],
        "novel_digest": info["novel_digest"],
        "novel_format": info["novel_format"],
        "sends": m.SENDS["attempts"],
        "record_status": (rec.get("status") if isinstance(rec, dict) else None),
        "correction": bool(_corr),
        "corrections": len(getattr(_ctl, "corrections", []) or []),
        "voice_units": ([str(x.get("unit_uid")) for x in
                         (rec.get("voice_units") or []) if isinstance(x, dict)]
                        if isinstance(rec, dict) else []),
        "acceptance_codes": [str(x.get("code")) for x in probs],
    }
    m._write(run_dir / "result.json", result)
    print("verify done:", json.dumps(result, ensure_ascii=False))
    print("run:", run_dir)
    return {"dry": False, "run_dir": str(run_dir), **result}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dry", action="store_true",
                    help="只校验输入身份并落盘，不创建 service/不发送")
    ap.add_argument("--correction-record", default="",
                    help="指定失败核验记录；进入纠正模式")
    ap.add_argument("--previous-response", default="",
                    help="失败记录对应的原始响应文件（含 raw_response_text）")
    args = ap.parse_args()
    try:
        run(Path(args.manifest), dry=args.dry,
            correction_record=(args.correction_record or None),
            previous_response=(args.previous_response or None))
    except InputIdentityError as exc:
        raise SystemExit("输入身份失败：0 次发送；%s" % exc)


if __name__ == "__main__":
    main()
