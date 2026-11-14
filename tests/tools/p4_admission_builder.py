# -*- coding: utf-8 -*-
"""P4 准入核对的夹具构造器（数据在 `fixtures/p4_admission/packs.json`）.

职责：把一个 pack 展开成 `tools/p4_admission_check.py` 真正要吃的东西——

    <run_dir>/candidate_content.json          计划（由 pack 的 view 经生产
                                              `plan_from_view` 生成，不手写）
    <run_dir>/runs/run_a/verify_archive.jsonl  冻结的核验原始响应（每行一条 `{"raw": ...}`）
    <novel.txt>                                原文

**为什么计划由代码生成**：计划结构由 `shared/presentation_contract` 定义，手写
JSON 会随 schema 演进静默失效；`plan_from_view` 是生产同源入口，夹具跟着它走。

**来源引用编号**：`source_support[].evidence_spans` 只接受原文目录里的编号，
由 `catalog_for(novel)` 现算（编号里带原文摘要），所以不能写死在 pack 里。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "p4_admission"
PACKS_PATH = FIXTURES / "packs.json"
SCHEMA = "p4_admission_pack.v1"


def load_packs() -> dict:
    doc = json.loads(PACKS_PATH.read_text(encoding="utf-8"))
    assert doc.get("schema") == SCHEMA, doc.get("schema")
    packs = {p["id"]: p for p in doc["packs"]}
    assert len(packs) == len(doc["packs"]), "pack id 重复"
    return packs


def build_view(pack: dict) -> dict:
    """pack → 最小新模式视图（事实 1 条、载体 1 个、事件 1 个）."""
    from shared import presentation_contract as pc

    fact = pack["fact"]
    carrier = pack["carrier"]
    beat = pack["beat"]
    return {
        "schema": pc.PRESENTATION_SCHEMA,
        "mode": pc.MODE_HYBRID,
        "event_binding": "declared",
        "frozen_facts": [{"uid": fact["uid"], "claim": fact["claim"],
                          "evidence": fact["evidence"]}],
        "frozen_events": ["甲"],
        "beats": [{"uid": beat["uid"], "event_uids": ["甲"], "text": beat["text"],
                   "purpose": beat["purpose"], "evidence": fact["evidence"],
                   "source": {},
                   "voice_ref": {"kind": pc.KIND_NARRATION, "uid": carrier["uid"]}}],
        "dialogue_units": [],
        "narration_units": [{"uid": carrier["uid"], "text": fact["evidence"],
                             "source": {"text": fact["evidence"]}}],
        "screen_cards": [],
        "fact_bindings": [{"fact_uid": fact["uid"],
                           "carriers": [{"kind": carrier["kind"],
                                         "uid": carrier["uid"]}]}],
        "hook_bindings": {"spoken_uids": [], "carriers": []},
        "evidence_updates": [],
        "plan_basis": {},
    }


def build_plan(pack: dict) -> dict:
    from shared import presentation_contract as pc
    return pc.plan_from_view(build_view(pack))


def source_span(pack: dict) -> str:
    from step01_script_gen.source_references import catalog_for
    catalog = catalog_for(pack["novel"])
    return next(iter(catalog))


def build_record(pack: dict) -> dict:
    """pack → 核验原始响应（模型本该返回的形状）."""
    fact = pack["fact"]
    spec = pack["record"]
    obligation = spec["obligation"]
    span = fact["evidence"]
    return {
        "source_support": [{
            "fact_uid": fact["uid"],
            "verdict": spec["source_verdict"],
            "evidence_spans": ([span] if spec.get("source_spans_use_raw_text")
                               else [source_span(pack)]),
            "reason": "",
        }],
        "facts": [{
            "fact_uid": fact["uid"],
            "verdict": spec["fact_verdict"],
            "support_spans": [span],
            "missing": "",
            "suggested_carriers": [],
            "obligations": [{
                "obligation_id": obligation["obligation_id"],
                "text": obligation["text"],
                "verdict": obligation["verdict"],
                "evidence": [{"carrier_uid": pack["carrier"]["uid"],
                              "exhibitor_span": span}],
                "missing": "",
                "reason": obligation.get("reason", ""),
            }],
        }],
        "voice_units": [{
            "unit_uid": pack["carrier"]["uid"],
            "verdict": "supported",
            "source_kind": spec["voice"]["source_kind"],
            "speaker": spec["voice"]["speaker"],
            "echo_text": span,
            "reason": "",
        }],
    }


def materialize(pack: dict, root: Path) -> dict:
    """把 pack 落成 run 目录；返回 `check()` 需要的三个路径."""
    root = Path(root)
    run_dir = root / "run_p4"
    (run_dir / "runs" / "run_a").mkdir(parents=True, exist_ok=True)
    (run_dir / "candidate_content.json").write_text(
        json.dumps(build_plan(pack), ensure_ascii=False), encoding="utf-8")
    (run_dir / "runs" / "run_a" / "verify_archive.jsonl").write_text(
        json.dumps({"raw": json.dumps(build_record(pack), ensure_ascii=False)},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    novel = root / "novel.txt"
    novel.write_text(pack["novel"], encoding="utf-8")
    return {"run_dir": run_dir,
            "plan_path": run_dir / "candidate_content.json",
            "novel_path": novel}
