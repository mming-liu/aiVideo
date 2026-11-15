# -*- coding: utf-8 -*-
"""**H 阶段验收矩阵**（复核 2026-10-02 之七）：完整调用留档 + 逐轮证据可重放 + 留档故障可见.

这一份只管**证据链**（调用 → 响应正文 → 轮次记录 → 重放）与自己暴露故障的方式；
bank 来源语义在 `test_run_provenance.py`，发布截止守卫在 `test_plan_phase_g_*.py`。

判据只用**真入口**（`create_service` 工厂代理、`generate_outline_result`、控制器），
模型调用由本地桩替代；台账与正文都落在临时目录，不发网络请求。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:                                                # 复用 G 的夹具
    sys.path.insert(0, str(_HERE))

from shared import call_ledger as cl                                          # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402

#: 规划额度单一来源（2026-11-15 批准 4→6）
_N = o.DEFAULT_REVISION_MAX_CALLS
from test_plan_phase_g_rejection_feedback import (                            # noqa: E402
    _LINE, _LINE2, _SRC, _Svc, _broken_carrier_plan, _legal_plan, _patch,
)


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """把台账与正文落进临时目录（生产里由 main 指向章节目录）."""
    _p = tmp_path / "step01_calls.jsonl"
    cl.set_ledger_path(_p)
    yield _p
    cl.set_ledger_path(None)


# ---------------------------------------------------------------------------
# H01/H02 调用身份：同轮多次、并发调用都能各自定位
# ---------------------------------------------------------------------------
def test_h01_every_call_in_one_run_has_its_own_id_and_body(ledger):
    """同一次运行里的多次调用：各自 `call_id`、各自正文文件、摘要可回读核验."""
    from _text_gen.models import TextGenerationRequest, TextGenerationResult
    _ids = []
    for _i in range(3):
        _r = TextGenerationResult(success=True, text=json.dumps({"i": _i}),
                                  finish_reason="stop")
        _ids.append(cl.record_call(
            "agent_plan", TextGenerationRequest(system_prompt="s", user_prompt="u",
                                                json_schema={"name": "chapter_outline"}),
            _r, elapsed_ms=1.0 * _i))
    assert len(set(_ids)) == 3, _ids
    _rows = cl.read_calls(ledger)
    assert [r["call_id"] for r in _rows] == _ids, "台账顺序 = 调用顺序"
    for _i, _r in enumerate(_rows):
        assert cl.verify_body(_r), "摘要必须与盘上文件一致"
        assert json.loads(cl.read_call_body(_r["call_id"])) == {"i": _i}
        assert _r["response_bytes"] == len(json.dumps({"i": _i}))


def test_h02_concurrent_calls_never_overwrite_each_other(ledger):
    """并发调用：`call_id` 不重复、正文不互相覆盖（工厂代理在**调用前**生成 id）."""
    from _text_gen.models import TextGenerationRequest, TextGenerationResult
    _seen = []
    _lock = threading.Lock()

    def _one(i):
        _r = TextGenerationResult(success=True, text="body-%d" % i, finish_reason="stop")
        _cid = cl.record_call("agent_plan",
                              TextGenerationRequest(system_prompt="s", user_prompt="u"),
                              _r, elapsed_ms=0.0)
        with _lock:
            _seen.append((_cid, i))

    _ts = [threading.Thread(target=_one, args=(i,)) for i in range(12)]
    [_t.start() for _t in _ts]
    [_t.join() for _t in _ts]
    assert len({c for c, _ in _seen}) == 12, "并发下 call_id 不许撞"
    for _cid, _i in _seen:
        assert cl.read_call_body(_cid) == "body-%d" % _i, "正文不许被别的调用覆盖"
    _rows = cl.read_calls(ledger)
    assert len(_rows) == 12 and all(cl.verify_body(r) for r in _rows)


# ---------------------------------------------------------------------------
# H03 非法与截断的响应：完整正文 + finish_reason 都要留档
# ---------------------------------------------------------------------------
def test_h03_a_truncated_response_keeps_its_verbatim_body_and_finish_reason(ledger):
    """截断（`success=False`）也保留**原始正文**与 `finish_reason`——留档才判得了截点."""
    from _text_gen.models import TextGenerationRequest, TextGenerationResult, truncated_result

    _cut = '{"shot_beats": ["节拍：甲", "节拍：乙'
    _res = truncated_result(text=_cut, usage=None, allow_truncated=False,
                            error="Agent Plan 输出被截断", finish_reason="length")
    assert _res.success is False and _res.text == _cut, "失败结果也要带着原文（供留档）"
    cl.record_call("agent_plan", TextGenerationRequest(system_prompt="s", user_prompt="u"),
                   _res, elapsed_ms=3.0)
    _row = cl.read_calls(ledger)[0]
    assert _row["ok"] is False and _row["truncated"] is True
    assert _row["finish_reason"] == "length"
    assert cl.read_call_body(_row["call_id"]) == _cut, "截断正文必须**逐字**保留"
    assert cl.verify_body(_row)


# ---------------------------------------------------------------------------
# H04 留档故障可见：不假装留过档，也不影响生成
# ---------------------------------------------------------------------------
def test_h04_an_archive_failure_is_visible_and_never_claimed_as_archived(ledger, tmp_path,
                                                                        monkeypatch, capsys):
    """正文写不进去 ⇒ **告警 + 行里标证据缺失**；裁决与生成结果**不受影响**，不假装留过档.

    两种故障各一条：① 归档函数自己抛错；② 归档目录建不出来（盘满/只读的形态）。
    """
    from _text_gen.models import TextGenerationRequest, TextGenerationResult

    def _call():
        return cl.record_call(
            "agent_plan", TextGenerationRequest(system_prompt="s", user_prompt="u"),
            TextGenerationResult(success=True, text="正文", finish_reason="stop"),
            elapsed_ms=1.0)

    # ① 归档函数抛错：**行照写**，并标明没归档
    monkeypatch.setattr(cl, "_archive_body",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full（注入）")))
    _call()
    monkeypatch.undo()
    cl.set_ledger_path(ledger)
    # ② 归档目录建不出来：路径落在一个**文件**下面
    _blocker = tmp_path / "blocker"
    _blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cl, "bodies_dir", lambda *a, **k: _blocker / "sub")
    _call()
    monkeypatch.undo()
    cl.set_ledger_path(ledger)

    _rows = cl.read_calls(ledger)
    assert len(_rows) == 2, _rows
    for _row in _rows:
        assert _row["archived"] is False and _row["archive_error"], _row
        assert cl.read_call_body(_row["call_id"]) is None, "没归档就是读不到，不许假装留过档"
    assert _rows[1]["response_chars"] == 2, "正文长度照记（知道丢了多少）"
    _out = capsys.readouterr().out
    assert _out.count("留档失败") >= 2 and "证据缺失" in _out, _out


# ---------------------------------------------------------------------------
# H05/H06 逐轮证据：关联 call_id / 指纹 / 拒收 / 完整验收，且**可重放**
# ---------------------------------------------------------------------------
def _patch_ledgered(monkeypatch, payloads, error="stub: 没有更多响应"):
    """把桩装成**真正的 provider**，让 `o.create_service` 走工厂代理（才有 call_id/台账）.

    直接替换 `o.create_service` 会**绕过代理**——那样测不到调用身份与留档。这里把桩注册进
    provider 注册表，用真工厂创建 ⇒ `_LedgeredService` 包在外面，行为与生产一致。
    """
    from _text_gen import service as _svc_mod
    from _text_gen.service import TextGenerationService

    class _Inner(TextGenerationService):
        _model = "stub-model"

        def __init__(self):
            self.svc = _Svc(payloads, error=error)

        def generate(self, request):
            return self.svc.generate(request)

    monkeypatch.setitem(_svc_mod._REGISTRY, "h_stub", _Inner)
    monkeypatch.setattr(o, "create_service",
                        lambda *a, **k: _svc_mod.create_service("h_stub"))
    # 2026-10（核验协议迁移）：本夹具的 `payloads` 只按**规划**调用配量；核验协议演化后
    # 核验也要发请求，若它也走 `create_service` 就会把顺序回放列表耗光 ⇒
    # `核验调用失败：stub: 没有更多响应` ⇒ `executable=False` ⇒ 归档里只剩首版记录，
    # "留档 ID/指纹/完整结果"与"归档响应回放真实应用器"两条都无从谈起。
    # 与 `test_plan_phase_g_rejection_feedback._patch` 同源：经 `verify=` 覆盖缝注入**合法**
    # 核验记录（`verification_helpers.legal_record_for`，四轴齐全、身份随 digest 绑定）。
    from tests import verification_helpers as vh
    _gor = o.generate_outline_result

    def _gor_with_verify(*a, **k):
        # 2026-10：原文取**本次调用**传进去的那一份——用模块常量猜会在
        # "本文件传 `_WIDE_NOVEL`、常量里却先命中 `_SRC`"这类情形下把记录
        # 绑到**另一份原文**上 ⇒ `核验记录违约` ⇒ "核验记录无法复核"。
        _novel = (a[0] if a else None) or k.get("novel_text") or _SRC
        k.setdefault("verify",
                     lambda view, digest: vh.legal_record_for(view, _novel,
                                                              plan_digest=digest))
        return _gor(*a, **k)

    monkeypatch.setattr(o, "generate_outline_result", _gor_with_verify)
    return _Inner().svc


def _rejected_rounds(path):
    _out = []
    if not Path(path).exists():
        return _out
    for _line in Path(path).read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line:
            _out.append(json.loads(_line))
    return _out


def _run_one_revision(tmp_path, monkeypatch, payloads):
    _ledger = tmp_path / "step01_calls.jsonl"
    cl.set_ledger_path(_ledger)
    try:
        _svc = _patch_ledgered(monkeypatch, payloads)
        _r = o.generate_outline_result(_SRC)
    finally:
        cl.set_ledger_path(None)
    return _r, _svc, _rejected_rounds(tmp_path / "step01_replan_rounds.jsonl")


def test_h05_the_round_archive_records_ids_fingerprints_and_full_results(tmp_path, monkeypatch):
    """每轮记录：`call_id`、输入计划指纹、拒收记录、候选指纹、**完整验收结果**."""
    _r, _svc, _rounds = _run_one_revision(
        tmp_path, monkeypatch,
        [_broken_carrier_plan()] + [_legal_plan()] * _N)
    assert _r["executable"] is False
    # 首版的**成功**尝试也留档（复核之八 P2）⇒ 轮次档 = 1 条首版 + 3 条修订
    _rev_rounds = [x for x in _rounds if x.get("phase") != "first_version"]
    assert len(_rounds) == _N + 1, _rounds
    assert [x for x in _rounds if x.get("phase") == "first_version"][0]["kind"] == "ok"
    assert len(_rev_rounds) == _N, _rounds
    for _rec in _rev_rounds:
        assert _rec["call_id"], "每轮必须关联到具体的调用"
        assert _rec["fingerprint_in"], "输入计划指纹"
        assert _rec["problems_out"] is None or isinstance(_rec["problems_out"], list)
        assert isinstance(_rec["problems_in"], list) and _rec["problems_in"], \
            "本轮输入的**完整验收结果**要留档（不是计数）"
    _rej = [x for x in _rounds if x.get("rejection")]
    assert _rej and _rej[0]["rejection"]["kind"] == "illegal", _rej
    # `call_id` 必须能在台账里找到（同一份证据链），且正文可回读
    _ids = {r["call_id"] for r in cl.read_calls(tmp_path / "step01_calls.jsonl")}
    assert {x["call_id"] for x in _rounds} <= _ids, (sorted(_ids), _rounds)


def test_h05b_the_archived_response_replays_the_real_applier(tmp_path, monkeypatch):
    """**重放**：读回归档正文 + 该轮的 `plan_in` → 真实应用器 → 同样的拒收结果.

    这是"无须依赖日志前缀猜测"的判据：归档正文 + 输入计划足以复现裁决。
    """
    _r, _svc, _rounds = _run_one_revision(
        tmp_path, monkeypatch, [_broken_carrier_plan()] + [_legal_plan()] * 3)
    _ledger = tmp_path / "step01_calls.jsonl"
    cl.set_ledger_path(_ledger)
    try:
        _rej = [x for x in _rounds if x.get("rejection") and x["plan_in"]]
        assert _rej, "至少要有一轮带输入计划的拒收"
        _rec = _rej[0]
        _body = cl.read_call_body(_rec["call_id"])
        assert _body, "归档正文必须读得到"
        _new, _why, _cid = o.apply_replan_ops(json.loads(json.dumps(_rec["plan_in"])),
                                              json.loads(_body), _SRC), None, None
    finally:
        cl.set_ledger_path(None)
    _plan, _why = _new[0], _new[1]
    assert _plan is None, "重放应当**同样被拒收**"
    assert _why == _rec["rejection"]["detail"], (_why, _rec["rejection"]["detail"])


def test_h06_the_first_version_attempt_is_archived_too(tmp_path, monkeypatch):
    """首版的每次尝试也留档（有 call_id 与具体记录），重放只需解析 + 结构判据."""
    _bad = {"main_events": [], "climax": "", "shot_beats": []}
    _r, _svc, _rounds = _run_one_revision(tmp_path, monkeypatch, [_bad] * (_N + 1))
    assert _r["failure"]["code"] == "outline_invalid"
    _first = [x for x in _rounds if x.get("phase") == "first_version"]
    assert len(_first) == _N + 1, _rounds
    assert all(x["call_id"] for x in _first) and all(x["problems_out"] for x in _first)
    _ledger = tmp_path / "step01_calls.jsonl"
    cl.set_ledger_path(_ledger)
    try:
        assert cl.read_call_body(_first[0]["call_id"]), "首版响应正文也要在盘上"
    finally:
        cl.set_ledger_path(None)
    assert "main_events" in _first[0]["problems_out"][0]["path"], _first[0]["problems_out"]


def test_h07_an_archive_failure_during_revision_still_decides(tmp_path, monkeypatch, capsys):
    """轮次留档失败 ⇒ 告警 + 证据缺失，**裁决照旧**（内容/调用/时间判据不变）."""
    _ledger = tmp_path / "step01_calls.jsonl"
    cl.set_ledger_path(_ledger)

    def _boom(*a, **k):
        raise OSError("round archive down（注入）")

    monkeypatch.setattr(o, "round_archive_writer", lambda *a, **k: _boom)
    try:
        _svc = _patch_ledgered(monkeypatch, [_broken_carrier_plan()] + [_legal_plan()] * _N)
        _r = o.generate_outline_result(_SRC)
    finally:
        cl.set_ledger_path(None)
    assert _r["executable"] is False and _r["revision"]["calls"] == _N, _r["revision"]
    assert _r["revision"]["stop_reason"] == "illegal_response", _r["revision"]
    _out = capsys.readouterr().out
    assert "修订轮留档失败" in _out and "证据缺失" in _out, _out[-600:]

# ---------------------------------------------------------------------------
# H08/H09 首版**每一次尝试**都关联留档（成功 / 调用失败）
# ---------------------------------------------------------------------------
def test_h08_a_first_version_that_passes_has_its_own_attempt_record(tmp_path, monkeypatch):
    """成功首版也要有尝试记录（`kind=ok` + `call_id` + `plan_out`）.

    复核之八 P2 实测：两章实跑的首版都是成功的，`step01_calls.jsonl` 里有调用、
    轮次档却是**空的**——事后对不上"这一版计划是哪次调用回来的"。
    """
    _r, _svc, _rounds = _run_one_revision(tmp_path, monkeypatch, [_legal_plan()] * 4)
    _first = [x for x in _rounds if x.get("phase") == "first_version"]
    assert len(_first) == 1, _rounds
    _rec = _first[0]
    assert _rec["kind"] == "ok" and _rec["call_id"], _rec
    assert isinstance(_rec["plan_out"], dict) and _rec["plan_out"].get("shot_beats"), _rec
    _ledger = tmp_path / "step01_calls.jsonl"
    cl.set_ledger_path(_ledger)
    try:
        assert _rec["call_id"] in {r["call_id"] for r in cl.read_calls(_ledger)}
        assert cl.read_call_body(_rec["call_id"]), "成功首版的正文必须也在盘上"
    finally:
        cl.set_ledger_path(None)


def test_h09_a_failed_call_has_its_own_attempt_record(tmp_path, monkeypatch):
    """**调用失败**（传输层）也要关联尝试记录：这一轮确实发过一次调用."""
    _r, _svc, _rounds = _run_one_revision(tmp_path, monkeypatch, [])
    assert _r["failure"]["code"] == "call_failed", _r["failure"]
    _first = [x for x in _rounds if x.get("phase") == "first_version"]
    assert len(_first) == 1 and _first[0]["kind"] == "call_failed", _rounds
    assert _first[0]["detail"], "失败原因要留在档里"


# ---------------------------------------------------------------------------
# H10/H11 证据故障可见：追加失败要告警；核验先确认文件在
# ---------------------------------------------------------------------------
def test_h10_a_ledger_append_failure_is_visible_and_never_blocks_generation(
        tmp_path, monkeypatch, capsys):
    """台账**追加失败** ⇒ 明确告警 + 标证据缺失，且**绝不影响生成**（复核之八 P2）."""
    from _text_gen.models import TextGenerationRequest, TextGenerationResult

    _blocker = tmp_path / "blocker"
    _blocker.write_text("x", encoding="utf-8")
    _ledger = _blocker / "sub" / "step01_calls.jsonl"      # 父路径是文件 ⇒ 追加必失败
    cl.set_ledger_path(_ledger)
    monkeypatch.setattr(cl, "bodies_dir", lambda *a, **k: tmp_path / "bodies")
    try:
        _cid = cl.record_call("agent_plan",
                              TextGenerationRequest(system_prompt="s", user_prompt="u"),
                              TextGenerationResult(success=True, text="正文"),
                              elapsed_ms=1.0)
    finally:
        cl.set_ledger_path(None)
    _out = capsys.readouterr().out
    assert _cid, "调用方仍拿得到 call_id（生成不受影响）"
    assert "追加失败" in _out and "证据缺失" in _out, _out
    assert cl.read_calls(_ledger) == [], "没写进去就是没写进去，不许假装留过档"


def test_h11_verify_body_requires_the_file_to_exist_and_be_readable(ledger):
    """正文文件**不在**就不算核验通过——空正文也一样（复核之八 P2）.

    旧写法 `read_call_body(...) or ""`：文件不存在与"正文本来就是空串"都变成 `b""`，
    于是空响应的那一行在文件被删掉之后仍然"核验通过"（摘要恰好对得上）。
    """
    from _text_gen.models import TextGenerationRequest, TextGenerationResult

    for _text in ("", "正文"):
        cl.record_call("agent_plan",
                       TextGenerationRequest(system_prompt="s", user_prompt="u"),
                       TextGenerationResult(success=True, text=_text), elapsed_ms=1.0)
    _rows = cl.read_calls(ledger)
    assert len(_rows) == 2 and all(cl.verify_body(r) for r in _rows), _rows
    for _row in _rows:
        os.remove(cl.call_body_path(_row["call_id"]))
        assert cl.verify_body(_row) is False, "文件没了 ⇒ 证据缺失 ⇒ 不许核验通过"

def test_h12_the_round_archive_creates_its_own_directory(tmp_path):
    """轮次档**自己建目录**：首次尝试可能**早于任何台账行**，那时父目录还不存在.

    旧写法直接 `open(..., "a")` ⇒ FileNotFoundError（成功的首版就这样丢过记录，
    而且是在"证据写入"这一步丢掉——正是最不该丢的地方）。
    """
    _p = tmp_path / "brand_new" / "step01_replan_rounds.jsonl"
    _w = o.round_archive_writer(_p)
    assert _w is not None
    _w({"phase": "first_version", "kind": "ok", "call_id": "c1"})
    _rows = [json.loads(x) for x in _p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert _rows and _rows[0]["call_id"] == "c1", _rows


def test_h06_the_ledger_records_a_prompt_digest_so_the_request_is_checkable(ledger):
    """**请求提示要有逐字摘要**（2026-10-11 逐笔诊断的缺口）.

    只留 `schema`/参数时，"这次请求的提示到底写没写清输出形状"无法用运行产物自证——
    实测一条修订拒收用归档响应复现不出，正因为请求正文没留。这里存**摘要不是正文**
    （不增体积、不含内容），但事后可按当前源码 + 同样输入重建提示并比对。
    """
    import hashlib
    from _text_gen.models import TextGenerationRequest, TextGenerationResult

    def _row(system, user):
        cl.record_call("agent_plan",
                       TextGenerationRequest(system_prompt=system, user_prompt=user,
                                             json_schema={"name": "chapter_outline"}),
                       TextGenerationResult(success=True, text="{}", finish_reason="stop"),
                       elapsed_ms=1.0)
        return cl.read_calls(ledger)[-1]

    _r = _row("SYS", "USER")
    _want = hashlib.sha256(b"system:SYS\nuser:USER").hexdigest()
    assert _r.get("prompt_sha256") == _want, _r
    assert _r.get("prompt_chars") == len("SYS") + len("USER")
    # 判别：提示变一个字 ⇒ 摘要必变（否则"比对"没有证明力）
    assert _row("SYS", "USER!").get("prompt_sha256") != _want
    # 正文仍**不落盘**（体积/敏感度口径不变）
    assert "SYS" not in json.dumps(_r, ensure_ascii=False)
