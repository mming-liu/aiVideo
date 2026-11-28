# -*- coding: utf-8 -*-
"""`tools/replay_plan_revision.py` 的离线单测（S02）。

两层：

1. **匿名最小 fixture**（默认必跑，自足）：测试自己构造匿名 plan / 原文 /
   增量回复，写到 `tmp_path` 的伪语料包里回放——覆盖 `applied`、`no_change`、
   `response_override`、缺失清单/缺失回复/哈希不符/缺原文等失败分支。
   不依赖 `.tmp/`、不依赖生产 `output/`、不写死任何测试小说人名或情节。
2. **外部冻结语料回归**（显式声明数据集）：`test_external_corpus_...` 只在
   `.tmp/step01_plan_corpus_20261121/corpus_manifest.json` 存在时运行；缺失时
   skip 并写明「外部数据集未运行」，不冒充基础测试通过。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import replay_plan_revision as rpr  # noqa: E402
from shared import presentation_contract as pc  # noqa: E402
from step01_script_gen import outline as outline  # noqa: E402
from step01_script_gen.plan_revision import revision_fingerprint  # noqa: E402

#: 外部冻结语料（S02 回归项的数据集身份；不是基础测试依赖）。
EXTERNAL_CORPUS = ROOT / ".tmp" / "step01_plan_corpus_20261121" / "corpus_manifest.json"
EXTERNAL_SKIP_REASON = (
    "外部数据集 .tmp/step01_plan_corpus_20261121/corpus_manifest.json 缺失："
    "S02 真实语料回归**未运行**（不是通过）；基础 fixture 用例仍独立执行")


# ---------------------------------------------------------------------------
# 匿名最小 fixture：plan / 原文 / 增量回复
# ---------------------------------------------------------------------------
def _fixture_novel() -> str:
    """匿名最小原文（与任何测试小说无关，只用于逐字来源核验）。"""
    return ("青石巷口，阿岚推门进来，把账本放在桌上。老陈抬头看了她一眼，没有作声。"
            "窗外的雨还没有停。阿岚说，账目都在这里了。老陈合上账本，起身去关窗。")


def _fixture_plan() -> dict:
    """匿名最小 legacy 计划：三条事件 / 三条节拍 / 一条台词 / 一条旁白。"""
    return {
        "main_events": ["阿岚送来账本", "老陈查看账本", "雨夜关窗收尾"],
        "climax": "老陈合上账本",
        "causal_chain": ["送账本→查看→收尾"],
        "time_budget": "对白约30秒",
        "characters": ["阿岚", "老陈"],
        "emotional_arc": "平静→迟疑",
        "end_hook": "雨声未停",
        "omittable": [],
        "key_scenes": ["青石巷口"],
        "character_motives": ["老陈：核对账目"],
        "shot_beats": [
            "节拍：阿岚推门进来，把账本放在桌上｜本镜信息目的：交代来意｜"
            "原文依据：“青石巷口，阿岚推门进来，把账本放在桌上。”｜载体：台词1",
            "节拍：老陈抬头看了她一眼，没有作声｜本镜信息目的：显露迟疑｜"
            "原文依据：“老陈抬头看了她一眼，没有作声。”｜载体：旁白1",
            "节拍：老陈合上账本，起身去关窗｜本镜信息目的：收尾｜"
            "原文依据：“老陈合上账本，起身去关窗。”｜载体：画面",
        ],
        "dialogue_lines": ["阿岚说，账目都在这里了。"],
        "narration_lines": [{"text": "老陈抬头看了她一眼，没有作声。",
                             "source": "老陈抬头看了她一眼，没有作声。",
                             "fact_refs": [2]}],
        "required_facts": [
            "阿岚送来账本｜原文证据：“青石巷口，阿岚推门进来，把账本放在桌上。”｜承载：B1/台词1",
            "老陈看了她一眼没有作声｜原文证据：“老陈抬头看了她一眼，没有作声。”｜承载：B2/旁白1",
            "老陈合上账本起身关窗｜原文证据：“老陈合上账本，起身去关窗。”｜承载：B3/画面",
        ],
        "hook_spoken": [],
        "hook_carrier": "",
    }


def _fixture_hashes() -> tuple:
    """(view, base_plan_hash, fact_basis_hash)——增量回复必须原样回带这两个摘要。"""
    plan = _fixture_plan()
    view, problems = pc.view_from_plan(plan)
    assert view is not None and not problems, problems
    request = pc.delta_request(view, rule_version=pc.DELTA_RULE_VERSION)
    return view, request["base_plan_hash"], request["fact_basis_hash"]


def _applied_response() -> str:
    """applied：upsert 既有节拍正文（保 uid/承载/集合），合并后视图摘要必变。"""
    view, base_hash, basis_hash = _fixture_hashes()
    beat = dict((view.get("beats") or [])[0])
    beat["text"] = "阿岚推门进来，把账本轻轻放在桌上"
    payload = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
               "base_plan_hash": base_hash, "fact_basis_hash": basis_hash,
               "upsert": {"beats": [beat]}}
    return json.dumps(payload, ensure_ascii=False)


def _no_change_response() -> str:
    """no_change：只回信封（两个基线摘要原样回带），不含任何改动块。"""
    _, base_hash, basis_hash = _fixture_hashes()
    payload = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
               "base_plan_hash": base_hash, "fact_basis_hash": basis_hash}
    return json.dumps(payload, ensure_ascii=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_corpus(tmp_path: Path, *, mutate_manifest=None) -> Path:
    """写一个匿名最小冻结语料包，返回 corpus_manifest.json 路径。"""
    plan = _fixture_plan()
    novel = _fixture_novel()
    applied = _applied_response()
    no_change = _no_change_response()
    root = tmp_path / "fixture_corpus"
    bodies = root / "frozen" / "fixture-run" / "call_bodies"
    bodies.mkdir(parents=True)
    (bodies / "applied.txt").write_text(applied, encoding="utf-8")
    (bodies / "no_change.txt").write_text(no_change, encoding="utf-8")
    fingerprint_in = revision_fingerprint(plan)
    manifest = {
        "schema": rpr.MANIFEST_SCHEMA,
        "created_at": "fixture",
        "source_run_id": "fixture-run",
        "note": "匿名最小 fixture（单测自造，不依赖 .tmp / 生产 output）",
        "files": {},
        "replay_targets": [
            {
                "round": 2,
                "kind_expected": "applied",
                "fingerprint_in_expected": fingerprint_in,
                "plan_in": plan,
                "novel_text": novel,
                "call": {
                    "call_id": "cfixtureapplied",
                    "schema": "plan_revision_delta_v1",
                    "finish_reason": "stop",
                    "response_file": "frozen/fixture-run/call_bodies/applied.txt",
                    "response_sha256": _sha256_text(applied),
                    "response_bytes": len(applied.encode("utf-8")),
                },
            },
            {
                "round": 3,
                "kind_expected": "no_change",
                "fingerprint_in_expected": fingerprint_in,
                "plan_in": plan,
                "novel_text": novel,
                "call": {
                    "call_id": "cfixturenochange",
                    "schema": "plan_revision_delta_v1",
                    "finish_reason": "stop",
                    "response_file": "frozen/fixture-run/call_bodies/no_change.txt",
                    "response_sha256": _sha256_text(no_change),
                    "response_bytes": len(no_change.encode("utf-8")),
                },
            },
        ],
    }
    manifest_path = root / "corpus_manifest.json"
    if mutate_manifest is not None:
        mutate_manifest(manifest, root)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return manifest_path


def _zero_call_probe(monkeypatch) -> dict:
    """把真实工厂换成「不许被调用」的探针（失败分支必须一次都不碰模型面）。"""
    state = {"calls": 0}

    def _must_not_be_called(*args, **kwargs):
        state["calls"] += 1
        raise AssertionError("失败分支不应构造模型服务")

    monkeypatch.setattr(outline, "create_service", _must_not_be_called)
    return state


# ---------------------------------------------------------------------------
# 默认必跑：匿名最小 fixture（applied / no_change / override / 失败分支）
# ---------------------------------------------------------------------------
def test_fixture_applied_round_runs_the_real_revision_entry(tmp_path):
    manifest = _write_corpus(tmp_path)
    report = rpr.replay(manifest, 2, output_base=tmp_path / "out")
    assert report["ok"] is True
    assert report["result"]["kind"] == "applied"
    assert report["result"]["model_calls"] == 1
    assert report["result"]["response_source"] == "frozen"
    assert report["result"]["fingerprint_in"] == report["expected"]["fingerprint_in"]
    assert report["result"]["fingerprint_out"] != report["result"]["fingerprint_in"]
    assert report["result"]["stop_reason"] == "calls_exhausted"  # 单轮回放强制收口
    assert report["conformance"]["kind"] is True
    assert report["conformance"]["fingerprint_in"] is True
    # 无归档请求证据的夹具模式：显式标注「未校验」，不冒充已核对
    assert report["request_evidence"]["checked"] is False
    assert report["request_evidence"]["match"] is None
    assert any("未提供归档请求证据" in w for w in report["warnings"])
    artifact = report["artifacts"]["report"]
    assert Path(artifact).is_file()
    saved = json.loads(Path(artifact).read_text(encoding="utf-8"))
    assert saved["result"]["kind"] == "applied"


def test_fixture_no_change_round_keeps_the_plan_unchanged(tmp_path):
    manifest = _write_corpus(tmp_path)
    report = rpr.replay(manifest, 3, output_base=tmp_path / "out")
    assert report["ok"] is True
    assert report["result"]["kind"] == "no_change"
    assert report["result"]["plan_unchanged"] is True
    assert report["result"]["fingerprint_out"] == report["result"]["fingerprint_in"]
    assert report["conformance"]["plan_unchanged"] is True
    assert report["conformance"]["plan_matches_archive"] is True


def test_response_override_is_used_instead_of_the_frozen_reply(tmp_path):
    manifest = _write_corpus(tmp_path)
    no_change = _no_change_response()
    frozen_applied = _applied_response()
    assert no_change != frozen_applied
    report = rpr.replay(manifest, 2, no_change, output_base=tmp_path / "out")
    # 冻结回复是 applied；用了替换回复 ⇒ no_change，证明替换生效。
    assert report["result"]["kind"] == "no_change"
    assert report["result"]["response_source"] == "override"
    assert report["result"]["response_sha256"] == _sha256_text(no_change)
    assert report["result"]["response_sha256"] != _sha256_text(frozen_applied)
    assert report["result"]["plan_unchanged"] is True
    # 替换回复不属于归档证据 ⇒ 显式不宣称归档一致性，并给出告警。
    assert report["ok"] is False
    assert report["conformance"]["kind"] is False
    assert any("response_override" in w for w in report["warnings"])


def test_missing_frozen_reply_fails_without_any_model_call(tmp_path, monkeypatch):
    manifest_path = _write_corpus(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    (tmp_path / "fixture_corpus" / "frozen" / "fixture-run" / "call_bodies"
     / "applied.txt").unlink()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    probe = _zero_call_probe(monkeypatch)
    with pytest.raises(rpr.ReplayError, match="冻结回复缺失"):
        rpr.replay(manifest_path, 2, output_base=tmp_path / "out")
    assert probe["calls"] == 0


def test_response_sha256_mismatch_fails_without_any_model_call(tmp_path, monkeypatch):
    def _corrupt(manifest, root):
        manifest["replay_targets"][0]["call"]["response_sha256"] = "0" * 64

    manifest_path = _write_corpus(tmp_path, mutate_manifest=_corrupt)
    probe = _zero_call_probe(monkeypatch)
    with pytest.raises(rpr.ReplayError, match="哈希不符"):
        rpr.replay(manifest_path, 2, output_base=tmp_path / "out")
    assert probe["calls"] == 0


def test_missing_manifest_fails(tmp_path):
    with pytest.raises(rpr.ReplayError, match="清单缺失"):
        rpr.replay(tmp_path / "not_there.json", 2)


def test_declared_corpus_file_missing_or_hash_mismatch_fails(tmp_path, monkeypatch):
    def _declare_missing(manifest, root):
        manifest["files"] = {"replan_rounds": {
            "path": "frozen/fixture-run/step01_replan_rounds.jsonl",
            "sha256": "0" * 64}}

    manifest_path = _write_corpus(tmp_path, mutate_manifest=_declare_missing)
    probe = _zero_call_probe(monkeypatch)
    with pytest.raises(rpr.ReplayError, match="冻结语料文件缺失"):
        rpr.replay(manifest_path, 2, output_base=tmp_path / "out")
    assert probe["calls"] == 0

    # 文件存在但内容哈希不符 ⇒ 同样在模型调用之前失败。
    declared = tmp_path / "fixture_corpus" / "frozen" / "fixture-run" / "step01_replan_rounds.jsonl"
    declared.write_text('{"round": 2}\n', encoding="utf-8")
    with pytest.raises(rpr.ReplayError, match="哈希不符"):
        rpr.replay(manifest_path, 2, output_base=tmp_path / "out")
    assert probe["calls"] == 0


def test_missing_novel_input_fails(tmp_path, monkeypatch):
    def _drop_novel(manifest, root):
        for target in manifest["replay_targets"]:
            target.pop("novel_text", None)

    manifest_path = _write_corpus(tmp_path, mutate_manifest=_drop_novel)
    probe = _zero_call_probe(monkeypatch)
    with pytest.raises(rpr.ReplayError, match="缺少原文输入"):
        rpr.replay(manifest_path, 2, output_base=tmp_path / "out")
    assert probe["calls"] == 0


def test_unknown_round_fails(tmp_path):
    manifest_path = _write_corpus(tmp_path)
    with pytest.raises(rpr.ReplayError, match="没有 round"):
        rpr.replay(manifest_path, 99, output_base=tmp_path / "out")


# ---------------------------------------------------------------------------
# 外部冻结语料回归项（显式声明数据集；缺失时 skip 并注明，不冒充通过）
# ---------------------------------------------------------------------------
def _run_replay_cli(manifest: Path, round_id: int, output_base: Path,
                    *, code_ref: str) -> dict:
    """以**钉住冻结语料代码基线**的方式跑 CLI（共享工作树可能有并行包改动）.

    冻结语料是和某个代码版本一起冻结的：清单里的 `code_baseline` 必须是当时能逐字
    复现请求的提交。`--code-source <commit>` 在隔离根物化 `git archive <commit>` 快照
    并在子进程里回放，保证「语料 + 代码」一起固定——否则后续提示/协议改动会让外部
    回归项按预期漂移失败（那不是语料坏了）。
    """
    import subprocess

    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "replay_plan_revision.py"),
         "--manifest", str(manifest), "--round", str(int(round_id)),
         "--code-source", str(code_ref), "--output-base", str(output_base)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), env=_replay_env())
    assert done.returncode == 0, (done.returncode, done.stdout[-2000:], done.stderr[-2000:])
    return json.loads(done.stdout)


def _replay_env() -> dict:
    """回放子进程只继承工具链环境，不继承前序用例留下的站点配置.

    冻结语料自带 `code_baseline` 的 `.env.content`；若把 `STEP01_*` / `BOOK_*` /
    `NOVEL_*` / `DURATION_*` 泄漏进子进程，`override=False` 会让现场值盖过快照值，
    归档请求与快照代码的提示边界随之漂移（实测：先跑 resume 用例再跑本文件必红）。
    """
    import os

    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("STEP01_", "BOOK_", "NOVEL_", "DURATION_")):
            env.pop(key, None)
    return env


def _external_code_baseline():
    """外部语料的代码基线（缺失时返回 None ⇒ 该回归项**未运行**，不冒充通过）."""
    try:
        _m = json.loads(EXTERNAL_CORPUS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    _ref = str(_m.get("code_baseline") or "").strip()
    return _ref or None


@pytest.mark.skipif(not EXTERNAL_CORPUS.is_file(), reason=EXTERNAL_SKIP_REASON)
def test_external_corpus_replays_round2_and_round4(tmp_path):
    """外部数据集：11-21 真实运行 round 2（applied）/ round 4（no_change）回放."""
    _ref = _external_code_baseline()
    if not _ref:
        pytest.skip("外部语料未声明 code_baseline：无法钉住代码版本，回放**未运行**"
                    "（不是通过）")
    round2 = _run_replay_cli(EXTERNAL_CORPUS, 2, tmp_path / "external",
                             code_ref=_ref)
    assert round2["ok"] is True, round2["conformance"]
    assert round2["code_source"]["mode"] == _ref
    assert all(round2["code_identity"].values())
    assert round2["result"]["kind"] == "applied"
    assert round2["result"]["fingerprint_in"] == "76231a28244d5280427f8dc8659ba1afda919ec6"
    assert round2["result"]["fingerprint_out"] == "b1653b43671f3f09275e7f94852e1f00f8167cf2"
    assert round2["result"]["model_calls"] == 1
    assert round2["request_evidence"]["checked"] is True
    assert round2["request_evidence"]["identity_match"] is True
    assert round2["request_evidence"]["diagnostic_match"] is True
    assert round2["request_evidence"]["rebuilt"]["request_chars"] == 37652
    assert round2["request_evidence"]["rebuilt"]["user_prompt_sha256"] == (
        "47a99584a9637cb978791caa257950a75c39275458d6c34e3e684549734d5901")
    assert round2["conformance"]["plan_matches_archive"] is True
    assert round2["conformance"]["residuals"] is True

    round4 = _run_replay_cli(EXTERNAL_CORPUS, 4, tmp_path / "external",
                             code_ref=_ref)
    assert round4["ok"] is True, round4["conformance"]
    assert round4["code_source"]["mode"] == _ref
    assert round4["result"]["kind"] == "no_change"
    assert round4["result"]["fingerprint_in"] == "ecc6b6e4647230d21d7a1017fbddf6a7116fc73f"
    assert round4["result"]["plan_unchanged"] is True
    assert round4["request_evidence"]["identity_match"] is True
    assert round4["request_evidence"]["diagnostic_match"] is True
    assert round4["request_evidence"]["rebuilt"]["request_chars"] == 36249
    assert round4["request_evidence"]["rebuilt"]["user_prompt_sha256"] == (
        "ab51032c378f60e7a09d14b35ccd8247cbbfae26139be8a4e6c5250925b1d5c7")
    for report in (round2, round4):
        isolation = report["isolation"]
        assert Path(isolation["output_base"]).is_dir()
        assert Path(report["artifacts"]["report"]).is_file()
