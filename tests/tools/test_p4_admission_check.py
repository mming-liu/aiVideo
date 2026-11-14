# -*- coding: utf-8 -*-
"""`tools/p4_admission_check.py` 的回归（夹具驱动，零调用、零网络）.

这个脚本回答的是「P4 报告里那句『原响应被拒』到底为什么被拒」——它把冻结的
核验响应做机械变体（原样 / 只改 id / 改 id+正文 / 错 id / 错正文），再走与生产
同源的 `VerificationController(validate_record=record_protocol_problems)` 准入。
结论错一格，要么把"正文改写"当成"编号问题"放过，要么把能通过的回炉重做。

两道必须钉住的边界：

1. **零真实调用**：脚本在进程内把 `outline.create_service` 换成抛异常的函数，
   本文件断言它跑完会**恢复**原函数，且全程未创建 service；
2. **四个变体的裁定**：`id_text_fixed` 必须通过，`wrong_id` / `wrong_text` 必须被拒。

数据在 `fixtures/p4_admission/packs.json`（人物用「某人/另一人」占位），
机制在 `p4_admission_builder.py`，本文件只做断言。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import p4_admission_builder as builder                    # noqa: E402
import p4_admission_check as tool                         # noqa: E402

VARIANTS = ("original", "id_fixed", "id_text_fixed", "wrong_id", "wrong_text")


def _packs() -> dict:
    return builder.load_packs()


def _run(pack_id: str, tmp_path: Path) -> dict:
    pack = _packs()[pack_id]
    paths = builder.materialize(pack, tmp_path)
    return tool.check(run_dir=paths["run_dir"], plan_path=paths["plan_path"],
                      novel_path=paths["novel_path"])


# ── ① 变体裁定矩阵 ────────────────────────────────────────────────────────

def test_all_five_variants_are_produced(tmp_path):
    report = _run("obligation_id_and_text", tmp_path)
    assert [r["run"] for r in report["runs"]] == ["run_a"]
    assert set(report["runs"][0]["variants"]) == set(VARIANTS)


def test_each_variant_matches_the_declared_verdict(tmp_path):
    pack = _packs()["obligation_id_and_text"]
    variants = _run("obligation_id_and_text", tmp_path)["runs"][0]["variants"]
    for name, expect in pack["expect"].items():
        got = variants[name]
        assert got["admitted"] is expect["admitted"], (name, got)
        for code in expect["problems_include"]:
            assert code in got["problems"], (name, code, got["problems"])


def test_only_the_fully_corrected_variant_is_admitted(tmp_path):
    variants = _run("obligation_id_and_text", tmp_path)["runs"][0]["variants"]
    admitted = [name for name in VARIANTS if variants[name]["admitted"]]
    assert admitted == ["id_text_fixed"]


def test_id_fix_alone_does_not_rescue_a_rewritten_claim(tmp_path):
    """id 改对了但正文是改写 ⇒ 仍被拒。这条区分「编号问题」与「正文问题」。"""
    variants = _run("obligation_id_and_text", tmp_path)["runs"][0]["variants"]
    assert variants["id_fixed"]["admitted"] is False
    assert "verify_obligation_text_substituted" in variants["id_fixed"]["problems"]


def test_report_carries_identities(tmp_path):
    pack = _packs()["obligation_id_and_text"]
    report = _run("obligation_id_and_text", tmp_path)
    assert report["schema"] == "p4.admission_check.v1"
    assert report["facts"] == [pack["fact"]["uid"]]
    assert len(report["plan_digest"]) == 64 and len(report["novel_digest"]) == 64


# ── ② 解析期拒绝：引用编号冒充原文 ────────────────────────────────────────

def test_raw_text_as_source_span_is_rejected_at_parse_time(tmp_path):
    """来源引用必须是目录编号。写成原文句子 ⇒ 该 run 在解析阶段就拒，不出变体。"""
    pack = _packs()["source_span_not_in_catalog"]
    row = _run("source_span_not_in_catalog", tmp_path)["runs"][0]
    codes = [p["code"] for p in row.get("raw_error") or []]
    for code in pack["expect"]["run_error_includes"]:
        assert code in codes, codes
    assert "variants" not in row or not row["variants"]


# ── ③ 零调用卡口 ──────────────────────────────────────────────────────────

def test_create_service_is_guarded_and_restored(tmp_path, monkeypatch):
    from step01_script_gen import outline as o

    calls = []

    def _should_not_run(*a, **k):
        calls.append((a, k))
        raise AssertionError("准入核对不允许创建 service")

    monkeypatch.setattr(o, "create_service", _should_not_run)
    _run("obligation_id_and_text", tmp_path)
    assert calls == []
    # 脚本在 finally 里恢复原函数：恢复的是**我们塞进去的那个**，不是生产实现，
    # 这正说明它确实存了旧值再写回（而不是把 monkeypatch 的桩留成生产实现）。
    assert o.create_service is _should_not_run


def test_blank_run_dir_without_archive_reports_no_runs(tmp_path):
    pack = _packs()["obligation_id_and_text"]
    paths = builder.materialize(pack, tmp_path)
    (paths["run_dir"] / "runs" / "run_a" / "verify_archive.jsonl").unlink()
    report = tool.check(run_dir=paths["run_dir"], plan_path=paths["plan_path"],
                        novel_path=paths["novel_path"])
    assert report["runs"] == []


def test_last_raw_line_wins(tmp_path):
    """同一 run 里有多条 raw 时取**最后一条非空**——重试后的结论才算数。"""
    pack = _packs()["obligation_id_and_text"]
    paths = builder.materialize(pack, tmp_path)
    archive = paths["run_dir"] / "runs" / "run_a" / "verify_archive.jsonl"
    good = json.dumps(builder.build_record(pack), ensure_ascii=False)
    broken = json.dumps({"facts": [], "source_support": [], "voice_units": []},
                        ensure_ascii=False)
    archive.write_text(
        "\n".join([json.dumps({"raw": broken}), json.dumps({"raw": good}),
                   json.dumps({"raw": "   "})]) + "\n", encoding="utf-8")
    row = tool.check(run_dir=paths["run_dir"], plan_path=paths["plan_path"],
                     novel_path=paths["novel_path"])["runs"][0]
    assert "raw_error" not in row and row["variants"]["id_text_fixed"]["admitted"] is True


# ── ④ CLI ────────────────────────────────────────────────────────────────

def test_cli_writes_report_to_out(tmp_path, capsys):
    pack = _packs()["obligation_id_and_text"]
    paths = builder.materialize(pack, tmp_path)
    out = tmp_path / "reports" / "p4.json"
    old = sys.argv
    sys.argv = ["p4_admission_check.py", "--run-dir", str(paths["run_dir"]),
                "--plan", str(paths["plan_path"]), "--novel", str(paths["novel_path"]),
                "--out", str(out)]
    try:
        tool.main()
    finally:
        sys.argv = old
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["runs"][0]["variants"]["id_text_fixed"]["admitted"] is True
    assert capsys.readouterr().out.strip().startswith("{")
