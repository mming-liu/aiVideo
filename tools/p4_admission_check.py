# -*- coding: utf-8 -*-
"""P4 提示澄清的**离线准入核对**（0 次模型调用，0 次网络）.

对冻结的原始核验响应做**机械变体**，只走生产同源的准入入口：
`VerificationController(validate_record=record_protocol_problems, require_coherence=True)`
的注入式 `record_for`（无 service、无 requests）。回答三件事：

1. 原响应是否如报告所述被拒（问题码可复现）；
2. 只把 `obligation_id` 改成代码给定的 id（正文不动）时，哪些仍因**正文**被拒；
3. 同时把 id 与 text 改成代码给定的值后，是否能通过完整准入；
   另外两个单变量反例（错 id / 错正文）仍必须被拒。

变体（每个 run 各跑一遍）：

- `original`：原样；
- `id_fixed`：只改 `obligation_id`（按 `frozen_obligations` 顺序）；
- `id_text_fixed`：`obligation_id` 与 `text` 都改成代码给定的值；
- `wrong_id`：id 固定写 `1`（正文正确）；
- `wrong_text`：id 正确、正文尾部多一个句号。

卡口：进程内把 `outline.create_service` 换成抛异常；任何真实调用都会立即失败。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_RUN_DIR = (ROOT / ".p1a_exp" / "p4_stability_20261025T104334")
DEFAULT_PLAN = (ROOT / "output" / "step01_ab" / "20261018T135622" / "d11_run"
                / "real_slot_f08" / "candidate_plan.json")
DEFAULT_NOVEL = (ROOT / "output" / "step01_ab" / "20261018T135622"
                 / "d9_feasibility" / "input" / "novel.txt")


def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _raw_last(archive: Path) -> str:
    lines = archive.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        obj = json.loads(line)
        if isinstance(obj.get("raw"), str) and obj["raw"].strip():
            return obj["raw"]
    return ""


def _variants(payload: dict, basis: dict) -> dict:
    _out = {"original": copy.deepcopy(payload)}
    _id = copy.deepcopy(payload)
    _idtext = copy.deepcopy(payload)
    _wid = copy.deepcopy(payload)
    _wtext = copy.deepcopy(payload)
    for row in _id.get("facts") or []:
        _want = basis.get(str(row.get("fact_uid"))) or []
        for _i, _o in enumerate(row.get("obligations") or []):
            if _i < len(_want):
                _o["obligation_id"] = _want[_i]["obligation_id"]
    for row in _idtext.get("facts") or []:
        _want = basis.get(str(row.get("fact_uid"))) or []
        for _i, _o in enumerate(row.get("obligations") or []):
            if _i < len(_want):
                _o["obligation_id"] = _want[_i]["obligation_id"]
                _o["text"] = _want[_i]["text"]
    for row in _wid.get("facts") or []:
        _want = basis.get(str(row.get("fact_uid"))) or []
        for _i, _o in enumerate(row.get("obligations") or []):
            _o["obligation_id"] = "1"
            if _i < len(_want):
                _o["text"] = _want[_i]["text"]
    for row in _wtext.get("facts") or []:
        _want = basis.get(str(row.get("fact_uid"))) or []
        for _i, _o in enumerate(row.get("obligations") or []):
            if _i < len(_want):
                _o["obligation_id"] = _want[_i]["obligation_id"]
            # 用**实质改写**做反例；只加标点会被 `_norm` 归一（不算改写）。
            _o["text"] = "被改写的主张：与冻结原文无关的另一句话"
    _out.update({"id_fixed": _id, "id_text_fixed": _idtext,
                 "wrong_id": _wid, "wrong_text": _wtext})
    return _out


def check(*, run_dir: Path, plan_path: Path, novel_path: Path) -> dict:
    import shared.content_verification as cv
    from shared import presentation_contract as pc
    from step01_script_gen import content_verify as cverify
    from step01_script_gen import outline as o

    # **零真实调用**的机械卡口：任何 create_service 都直接失败。
    def _boom(*a, **k):
        raise AssertionError("本核对不允许创建 service（0 次模型调用）")
    _orig_service = o.create_service
    o.create_service = _boom
    try:
        run_dir = Path(run_dir)
        cand = run_dir / "candidate_content.json"
        plan = json.loads((cand if cand.is_file() else plan_path)
                          .read_text(encoding="utf-8"))
        novel = Path(novel_path).read_text(encoding="utf-8")
        view, vp = pc.view_from_plan(plan)
        if view is None or vp:
            raise SystemExit("视图不可读：%s" % vp)
        digest = cv.plan_digest_of(plan)
        ndig = _sha256(novel)
        basis = cverify.frozen_obligations(view)
        from step01_script_gen.source_references import catalog_for
        catalog = catalog_for(novel)
        report = {"schema": "p4.admission_check.v1", "run_dir": str(run_dir),
                  "plan_digest": digest, "novel_digest": ndig,
                  "facts": [str(f.get("uid")) for f in view.get("frozen_facts") or []],
                  "runs": []}
        for run in sorted(run_dir.glob("runs/run_*")):
            archive = run / "verify_archive.jsonl"
            if not archive.is_file():
                continue
            raw = _raw_last(archive)
            # 与生产同一条解析出口：`src_` 编号在这里展开成原文片段，schema/status 补齐。
            payload, bad = cverify.parse_verification_response(raw,
                                                              source_catalog=catalog)
            if payload is None:
                report["runs"].append({"run": run.name, "raw_error": bad})
                continue
            row = {"run": run.name, "raw_sha256": _sha256(raw),
                   "variants": {}}
            for name, body in _variants(payload, basis).items():
                ctl = cv.VerificationController(
                    verify=lambda _v, _d, _b=body: copy.deepcopy(_b),
                    require_coherence=True,
                    validate_record=lambda r, v, d: cv.record_protocol_problems(
                        r, v, plan_digest=d, novel_text=novel, novel_digest=ndig),
                    max_verifications=1, novel_digest=ndig,
                    rule_version=cv.DEFAULT_RULE_VERSION)
                rec = ctl.record_for(view, digest, allow_correction=False)
                codes = sorted({str(p.get("code") or "")
                                for p in ((rec or {}).get("problems") or [])
                                if isinstance(p, dict)})
                row["variants"][name] = {
                    "status": str((rec or {}).get("status") or ""),
                    "admitted": str((rec or {}).get("status") or "") == "ok",
                    "problems": codes,
                    "fact_verdicts": {str(x.get("fact_uid")): str(x.get("verdict") or "")
                                      for x in ((rec or {}).get("facts") or [])
                                      if isinstance(x, dict)}}
            report["runs"].append(row)
    finally:
        o.create_service = _orig_service
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--novel", default=str(DEFAULT_NOVEL))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    report = check(run_dir=Path(args.run_dir), plan_path=Path(args.plan),
                   novel_path=Path(args.novel))
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
