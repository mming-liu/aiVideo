# -*- coding: utf-8 -*-
"""**D 阶段验收矩阵**（工作包 §4 D / §7.7）：首版、缓存、控制器、生成前门用**同一份**上下文.

逐条覆盖 §7.1 的五个缺口：

- G1 修订控制器进生产（首版未通过统一验收 ⇒ 走 `run_plan_revision`，不再是"直接停下"）；
- G2 结构化结果（`executable` / `source` / `plan` / `acceptance` / `revision` / `failure` / `cache`）；
- G3 生成前门接**同一个**绝对截止时刻（`check_plan_before_generation(deadline_monotonic=)`）；
- G4 截止算一次并贯穿并发入口；预提取大纲不再降级成裸计划（失败原因保真到 main）；
- G5 并发层不再把失败吞成 None。

判据只用**真入口**（`generate_outline_result` / `extract_all_concurrent` /
`_stop_if_outline_unavailable`），模型调用由本地桩替代，不发网络请求。
"""
from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen import plan_feasibility as pf                          # noqa: E402
from step01_script_gen.concurrent_extract import extract_all_concurrent       # noqa: E402

#: 规划额度**单一来源**（2026-11-15 用户批准 4→6；测试不再写字面量 3/4）
_N = o.DEFAULT_REVISION_MAX_CALLS

_LINE = "甲板上有风，帆布猎猎作响。"
_SRC = "开头。%s事件乙。事件丙。" % _LINE


def _legal_plan(**over):
    """**完整验收通过**的计划（夹具；与 C 阶段矩阵同一形状）."""
    p = {"main_events": ["甲", "乙", "丙"], "climax": "丙", "causal_chain": ["甲→乙→丙"],
         "required_facts": ["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE],
         "hook_spoken": [], "hook_carrier": "", "narration_lines": [],
         "dialogue_lines": [_LINE],
         "shot_beats": ["节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
                        "节拍：乙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE,
                        "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE]}
    p.update(over)
    return p


def _broken_plan():
    """承载错位（F1 声明由 B3/台词1 承载，而 B3 是画面节拍）⇒ 统一验收报 `carrier_fact_line_mismatch`."""
    return _legal_plan(required_facts=[
        "甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B3/台词1" % _LINE])


class _Svc:
    """本地服务桩：按顺序回放给定响应（payload 用尽即失败）."""

    def __init__(self, payloads=(), error="stub: 没有更多响应"):
        self.payloads = list(payloads)
        self.error = error
        self.reqs: list = []

    def generate(self, request):
        from _text_gen.models import TextGenerationResult
        self.reqs.append(request)
        if not self.payloads:
            return TextGenerationResult(success=False, error=self.error)
        _p = self.payloads.pop(0)
        if isinstance(_p, str):
            return TextGenerationResult(success=True, text=_p)
        return TextGenerationResult(success=True, text=json.dumps(_p, ensure_ascii=False))


def _patch(monkeypatch, payloads=(), error="stub: 没有更多响应"):
    _svc = _Svc(payloads, error=error)
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _svc)
    # 2026-10（核验协议迁移）：本文件的 `payloads` 只按**规划**调用配量。核验协议演化后
    # 核验也要发请求，若它也走 `create_service` 就会把顺序回放列表耗光 ⇒
    # `核验调用失败：stub: 没有更多响应` → `executable=False` → 缓存不写，成片误红。
    # 经 `generate_outline_result` 的 `verify=` 覆盖缝注入**合法**核验记录
    # （`verification_helpers.legal_record_for`，四轴齐全、身份随 `digest` 绑定）。
    from tests import verification_helpers as _vh
    _gor_orig = o.generate_outline_result

    def _gor_with_verify(*a, **k):
        # 2026-10：原文取**本次调用**传进去的那一份——用模块常量猜会在
        # "本文件传 `_WIDE_NOVEL`、常量里却先命中 `_SRC`"这类情形下把记录
        # 绑到**另一份原文**上 ⇒ `核验记录违约` ⇒ "核验记录无法复核"。
        _novel = (a[0] if a else None) or k.get("novel_text") or _SRC
        k.setdefault("verify",
                     lambda view, digest: _vh.legal_record_for(view, _novel,
                                                              plan_digest=digest))
        return _gor_orig(*a, **k)

    monkeypatch.setattr(o, "generate_outline_result", _gor_with_verify)
    return _svc


def _write_cache(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# D01 首版 → 验收 → 缓存 → 复用
# ---------------------------------------------------------------------------
def test_d01_first_version_is_zero_revision_and_the_cache_is_reused(tmp_path, monkeypatch):
    """D01：首版 0 次修订且 executable=True；缓存内容正确；第二次 `source=cache` 零调用."""
    _cache = tmp_path / "chapter_outline.cache.json"
    _svc = _patch(monkeypatch, [_legal_plan()])
    _r1 = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r1["executable"] is True and _r1["source"] == "initial"
    assert _r1["failure"] is None
    assert _r1["revision"]["calls"] == 0 and _r1["revision"]["rounds"] == 0, _r1["revision"]
    assert _r1["acceptance"]["problems"] == [] and _r1["acceptance"]["verdict"] == "ok"
    assert len(_svc.reqs) == 1, "首版通过验收 ⇒ 只发一次调用（零修订）"
    assert _r1["cache"]["hit"] is False and _r1["cache"]["written"] is True
    _disk = json.loads(_cache.read_text(encoding="utf-8"))
    assert set(("source_hash", "plan_contract_version", "outline")) <= set(_disk), _disk.keys()
    assert _disk["plan_contract_version"] == pf.PLAN_CONTRACT_VERSION
    assert _disk["outline"] == _r1["plan"], "缓存内容与下发计划必须逐字一致"

    _svc2 = _patch(monkeypatch, [])
    _r2 = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r2["executable"] is True and _r2["source"] == "cache"
    assert _r2["cache"]["hit"] is True and _r2["cache"]["written"] is False, _r2["cache"]
    assert _r2["revision"]["calls"] == 0
    assert _svc2.reqs == [], "缓存命中不许再调模型"
    assert _r2["plan"] == _r1["plan"], "复用计划与缓存中计划语义一致"
    assert json.loads(_cache.read_text(encoding="utf-8"))["outline"] == _r2["plan"]


def test_d01_the_compat_forwarder_only_returns_executable_plans(tmp_path, monkeypatch):
    """§7.3：`generate_outline` 只转发 `executable=True` 的计划（否则 None）."""
    _patch(monkeypatch, [_broken_plan()])
    assert o.generate_outline(_SRC) is None
    _patch(monkeypatch, [_legal_plan()])
    _p = o.generate_outline(_SRC)
    assert isinstance(_p, dict) and _p["dialogue_lines"] == [_LINE]


# ---------------------------------------------------------------------------
# D02 缓存分支表（§7.5）
# ---------------------------------------------------------------------------
def test_d02_a_stale_source_hash_falls_back_to_a_first_version(tmp_path, monkeypatch):
    _cache = tmp_path / "c.json"
    _write_cache(_cache, {"source_hash": "旧的键", "plan_contract_version":
                          pf.PLAN_CONTRACT_VERSION, "outline": _legal_plan()})
    _svc = _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["source"] == "initial" and _r["cache"]["hit"] is False
    assert len(_svc.reqs) == 1, "source_hash 不匹配 ⇒ 必须重新规划"
    assert _r["executable"] is True


def test_d02_a_stale_contract_version_is_backed_up_not_declared_infeasible(tmp_path, monkeypatch):
    """契约版本不同 ⇒ 留 `.bak-contractN` + 重新产出首版，**不**报"故事不可行"."""
    _cache = tmp_path / "c.json"
    _svc = _patch(monkeypatch, [_legal_plan()])
    o.generate_outline_result(_SRC, cache_path=_cache)
    _disk = json.loads(_cache.read_text(encoding="utf-8"))
    _disk["plan_contract_version"] = 3
    _write_cache(_cache, _disk)
    _svc2 = _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    # 约定：`.bak-contractN` 的 N 是**新版**契约号（"为升到 vN 而留的档"）
    _bak = ".bak-contract%d" % pf.PLAN_CONTRACT_VERSION
    assert _r["cache"]["hit"] is False, _r["cache"]
    assert str(_r["cache"]["backup"]).endswith(_bak), _r["cache"]
    assert (tmp_path / ("c.json" + _bak)).exists(), "旧缓存必须留档"
    assert len(_svc2.reqs) == 1
    assert _r["source"] == "initial" and _r["executable"] is True
    assert not (tmp_path / "step01_plan_infeasible.json").exists(), (
        "版本失效不是「故事不可行」")


def test_d02_a_cache_plan_that_fails_acceptance_is_never_reused(tmp_path, monkeypatch):
    """缓存里的计划也要过**同一份**验收：不通过 ⇒ 留 `.bak-rejected` 并重新规划."""
    _cache = tmp_path / "c.json"
    _svc = _patch(monkeypatch, [_legal_plan()])
    o.generate_outline_result(_SRC, cache_path=_cache)
    _disk = json.loads(_cache.read_text(encoding="utf-8"))
    _disk["outline"] = _broken_plan()
    _write_cache(_cache, _disk)
    _svc2 = _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["cache"]["hit"] is False
    assert str(_r["cache"]["backup"]).endswith(".bak-rejected"), _r["cache"]
    assert (tmp_path / "c.json.bak-rejected").exists()
    assert len(_svc2.reqs) == 1, "失败版本不许复用 ⇒ 必须重新规划"


def test_d02_an_invalid_first_version_is_not_executable(tmp_path, monkeypatch):
    """首版结构不合格 ⇒ `outline_invalid`（不是"故事不可行"），没有可下发的计划.

    复核 2026-10-02 之五：首版不合格**不再立即终止**——在**同一份额度与截止**内把
    **具体字段与原因**反馈给规划器重问（每次重请求花掉一次规划机会）。这里让桩三次都
    回同一个不合格响应 ⇒ 额度耗尽后仍是 `outline_invalid`（响应违约），只有一次
    `call_failed` 的桩序列才该得到 `call_failed`（传输失败维持独立归因）。
    """
    _bad_first = {"main_events": [], "climax": "", "shot_beats": []}
    _svc = _patch(monkeypatch, [_bad_first] * (_N + 1))
    _r = o.generate_outline_result(_SRC)
    assert _r["executable"] is False and _r["failure"]["code"] == "outline_invalid", _r["failure"]
    assert _r["plan"] is None
    # 早停时 `revision.stop_reason` 与 `failure.code` **同一键位给同一个码**（§7.2）
    assert _r["revision"]["stop_reason"] == "outline_invalid", _r["revision"]
    assert len(_svc.reqs) == _N + 1, "首版 1 次 + 额度内重请求 3 次"
    # **反馈的是具体字段与原因**（旧写法只有"关键字段缺失/类型错误"）
    assert "main_events" in _svc.reqs[1].user_prompt, _svc.reqs[1].user_prompt[-800:]
    assert _r["failure"]["problems"], "被拒的具体记录必须落进失败声明"
    assert "main_events" in _r["failure"]["detail"], _r["failure"]["detail"]

    _svc2 = _patch(monkeypatch, ["不是 JSON"] * (_N + 1))
    _r2 = o.generate_outline_result(_SRC)
    assert _r2["failure"]["code"] == "illegal_response", _r2["failure"]
    assert _r2["revision"]["stop_reason"] == "illegal_response", _r2["revision"]
    assert len(_svc2.reqs) == _N + 1


def test_d02_a_carrier_revision_runs_through_the_controller(tmp_path, monkeypatch):
    """承载失败 ⇒ 进 `run_plan_revision`（同一验收链）：修好则 executable，改不好则停."""
    _cache = tmp_path / "c.json"
    # ① 一次合法操作修好 ⇒ executable=True、calls=1、写缓存
    _svc = _patch(monkeypatch, [_broken_plan(),
                                {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]}])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True and _r["revision"]["calls"] == 1, _r["revision"]
    assert _r["revision"]["stop_reason"] == "ok" and _r["cache"]["written"] is True
    assert len(_svc.reqs) == 2, "首版 + 一轮修订"
    # 修订请求必须是**操作式**（不能带"输出整份大纲"的格式表——复核 P1 的形态）
    _rev_prompt = _svc.reqs[1].user_prompt
    assert "## 请**只输出操作**" in _rev_prompt and "# 输出格式" not in _rev_prompt
    # ② 改不好（桩三轮都回整份计划 ⇒ 每轮都是非法操作）⇒ 额度耗尽，主终局仍是
    #    **响应违约**（复核 2026-10-02 之五）、**不写缓存**
    _cache2 = tmp_path / "c2.json"
    _patch(monkeypatch, [_broken_plan()] + [_broken_plan()] * _N)
    _r2 = o.generate_outline_result(_SRC, cache_path=_cache2)
    assert _r2["executable"] is False
    assert _r2["revision"]["stop_reason"] == "illegal_response", _r2["revision"]
    assert _r2["failure"]["code"] == "illegal_response"
    assert _r2["revision"]["calls"] == _N, "额度用尽（每次非法都扣一次）"
    assert _r2["revision"]["quota_exhausted"] is True, _r2["revision"]
    assert _r2["revision"]["plan_level_conclusion"] is False, "响应违约不是「计划不可用」的结论"
    assert _r2["revision"]["rejections"], "被拒的具体原因必须留档"
    assert not _cache2.exists(), "未通过统一验收的候选不许写进可执行缓存"
    assert [x["code"] for x in _r2["acceptance"]["problems"]], _r2["acceptance"]


# ---------------------------------------------------------------------------
# D03 缓存原子写失败
# ---------------------------------------------------------------------------
def test_d03_cache_write_failure_keeps_the_plan_usable(tmp_path, monkeypatch):
    """D03：写盘失败**只降级缓存可用性**——计划照旧可用，旧文件保留，绝不写成"不可行"."""
    _cache = tmp_path / "c.json"
    _write_cache(_cache, {"source_hash": "旧键", "plan_contract_version":
                          pf.PLAN_CONTRACT_VERSION, "outline": _legal_plan()})
    _before = _cache.read_text(encoding="utf-8")
    _patch(monkeypatch, [_legal_plan()])

    def _boom(*a, **k):
        raise OSError("disk full（注入）")

    # 注入点在**准备**这一步（复核之八 P1 把"写临时文件"与"替换"拆成两阶段，
    # 生产路径不再经过 `atomic_dump_json`；契约不变，故障切点跟着下沉）
    monkeypatch.setattr("shared.json_io.prepare_json_swap", _boom)
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True and isinstance(_r["plan"], dict)
    assert _r["failure"] is None, "缓存写失败不许改写成计划不可行"
    assert _r["cache"]["written"] is False and _r["cache"]["write_error"], _r["cache"]
    assert _cache.read_text(encoding="utf-8") == _before, "旧缓存文件必须原样保留"


# ---------------------------------------------------------------------------
# D04 同一份截止 + 并发失败保真
# ---------------------------------------------------------------------------
def test_d04_the_same_absolute_deadline_reaches_every_entry(monkeypatch):
    """D04：`extract_all_concurrent` → 首版请求与 `run_plan_revision` 收到**同一个**绝对时刻."""
    _DL = 1_700_000_000.5
    _seen: dict = {}
    _real = o.run_plan_revision

    def _spy(plan, novel_text, **kw):
        _seen.update(kw)
        return _real(plan, novel_text, **kw)

    monkeypatch.setattr(o, "run_plan_revision", _spy)
    _svc = _patch(monkeypatch, [_broken_plan(),
                                {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]}])
    _res = extract_all_concurrent(_SRC, None, None, enable_characters=False,
                                 enable_scene_objects=False, enable_outline=True,
                                 outline_cache_path=None,
                                 outline_deadline_monotonic=_DL)
    # 复核之六 P1：**首版请求与控制器消费同一份截止**——`budget.deadline_at`
    # = min(整次截止, 现在 + 本阶段上限)；本阶段上限更早时它就是更早的那个（旧写法
    # 首版只拿整次截止 ⇒ 本阶段上限可以被外层绕过）。
    _budget = _seen.get("budget")
    assert _budget is not None, _seen
    assert _svc.reqs[0].deadline_monotonic == _budget.deadline_at, "首版模型调用必须用同一份规划截止"
    assert _budget.deadline_at <= _DL, ("大于整次截止就不是「更早者」了", _budget.deadline_at, _DL)
    assert _seen.get("run_deadline_at") == _DL, ("控制器只开一次的那张表必须用同一个绝对截止",
                                                _seen)
    assert isinstance(_res["outline"], dict) and _res["outline"]["executable"] is True
    # 未启用的项**根本不出键**（既有行为，逐字不变）
    assert "characters" not in _res and "scene_objects" not in _res, sorted(_res)


def test_d04_an_outline_exception_becomes_a_structured_failure(monkeypatch):
    """G5：并发层不再把异常吞成 None——`failure.code`/`detail` 保真（type + message）."""
    def _boom(*a, **k):
        raise RuntimeError("通道炸了")

    monkeypatch.setattr(o, "generate_outline_result", _boom)
    _res = extract_all_concurrent(_SRC, None, None, enable_characters=False,
                                 enable_scene_objects=False, enable_outline=True)
    _out = _res["outline"]
    assert isinstance(_out, dict) and _out["executable"] is False, _out
    assert _out["failure"]["code"] == "call_failed"
    assert "RuntimeError" in _out["failure"]["detail"] and "通道炸了" in _out["failure"]["detail"]
    assert _out["plan"] is None


def test_d04_characters_and_scene_objects_keep_their_none_behavior(monkeypatch):
    """资产提取的行为不变：抛异常 ⇒ 对应字段 None（不阻断），大纲照常出结构化结果."""
    import step01_script_gen.character_extractor as _ce
    import step01_script_gen.object_extractor as _oe

    def _boom(*a, **k):
        raise RuntimeError("资产提取失败")

    monkeypatch.setattr(_ce, "extract_characters", _boom)
    monkeypatch.setattr(_oe, "extract_scene_objects", _boom)
    _patch(monkeypatch, [_legal_plan()])
    _res = extract_all_concurrent(_SRC, None, None, enable_characters=True,
                                 enable_scene_objects=True, enable_outline=True)
    assert _res["characters"] is None and _res["scene_objects"] is None
    assert _res["outline"]["executable"] is True


def test_d04_an_unavailable_outline_writes_a_diagnostic_and_stops(tmp_path, monkeypatch):
    """§7.6：main 拿到结构化失败 ⇒ 按 `failure.code` 写诊断并**停止**（不冒充内容结论）."""
    from step01_script_gen import main as _m
    monkeypatch.setattr(_m, "CHAPTER_DIR", tmp_path)
    _res = {"executable": False, "plan": None,
            "failure": {"code": "call_failed", "detail": "RuntimeError: 通道炸了"}}
    with pytest.raises(pf.PlanInfeasibleError) as _e:
        _m._stop_if_outline_unavailable(_res, _SRC, None)
    assert "call_failed" in str(_e.value)
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["verdict"] == "unevaluable" and _d["conclusion"] == "call_failed", _d
    assert "call_failed" in _d["reasons"][0]
    # 有基线 ⇒ 交给**生成前统一入口**（同一份验收链、同一份诊断）
    _res2 = {"executable": False, "plan": _broken_plan(),
             "failure": {"code": "contract_unclosed", "detail": "承载没闭合"}}
    with pytest.raises(pf.PlanContractInvalidError):
        _m._stop_if_outline_unavailable(_res2, _SRC, None)
    assert (tmp_path / "step01_plan_contract_invalid.json").exists()
    # 可执行 ⇒ 什么都不做（不写诊断、不抛）
    _res3 = {"executable": True, "plan": _legal_plan(), "failure": None}
    _m._stop_if_outline_unavailable(_res3, _SRC, None)


def test_d04_the_deadline_is_computed_once_and_checked_before_candidates():
    """§7.4 / D04：`_planning_deadline_at()` 在 main 里**只算一次**；判"能不能下发"在
    **候选生成之前**（⇒ executable=False 时候选生成调用次数为 0）."""
    from step01_script_gen import main as _m
    _src = inspect.getsource(_m.main)
    assert _src.count("_planning_deadline_at()") == 1, (
        "同一次运行只许算一次绝对截止时刻（各处不得重算 now+cap）")
    assert _src.count("_deadline_monotonic = _planning_deadline_at()") == 1
    # 两条消费路径都**显式**带上它。注意不能用子串 `deadline_monotonic=_deadline_monotonic`：
    # `outline_deadline_monotonic=...` 也包含这段，撤掉候选入口的传参照样能"通过"。
    assert "outline_deadline_monotonic=_deadline_monotonic" in _src
    assert re.search(
        r"_generate_script_candidates\([^)]*deadline_monotonic=_deadline_monotonic",
        _src, re.S), "候选生成入口必须接同一份绝对截止"
    _i_stop = _src.index("_stop_if_outline_unavailable(_outline_result")
    _i_cand = _src.index("_candidates = _generate_script_candidates(")
    assert _i_stop < _i_cand, "不可执行时必须**先停**，再谈候选生成"


def test_d04_the_pre_generation_gate_takes_the_same_deadline(tmp_path, monkeypatch):
    """G3：生成前门接同一份截止；截止已到 ⇒ `deadline_exhausted` 诊断（不是"故事不可行"）."""
    from step01_script_gen import main as _m
    _src = inspect.getsource(_m._generate_script_candidates)
    assert "deadline_monotonic=deadline_monotonic" in _src
    _past = 1.0                       # 远早于现在的单调钟 ⇒ 必定已过
    with pytest.raises(pf.PlanInfeasibleError) as _e:
        pf.check_plan_before_generation(_legal_plan(), chapter_dir=tmp_path,
                                        novel_text=_SRC, deadline_monotonic=_past)
    _msg = str(_e.value)
    assert "deadline_exhausted" in _msg and "时间" in _msg, _msg
    assert "不是「故事装不下" in _msg, "错误文本必须把「时间用尽」与「故事不可行」分开：%s" % _msg
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["conclusion"] == "deadline_exhausted" and _d["verdict"] == "unevaluable", _d
    # 合法对照：不设截止 ⇒ 判定与旧行为一致（这份计划通过）
    _rep = pf.check_plan_before_generation(_legal_plan(), chapter_dir=tmp_path,
                                           novel_text=_SRC)
    assert _rep["verdict"] == "feasible"


def test_d01_a_clean_plan_past_the_deadline_is_not_executable(monkeypatch):
    """§7.2：`executable` 的判据是**两条**——完整验收为空**且**未过截止.

    判别性：只看验收为空（不看截止）⇒ 本用例转红（过点的计划会被当成可下发，
    而下游的生成调用根本没有时间预算）。
    """
    _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, deadline_monotonic=1.0)   # 远早于现在 ⇒ 必过点
    # 截止前候选内容本身通过，但内容核验控制器已按截止记录失败；验收报告须保留
    # 该失败证据，不能伪装成空问题。最终不可执行由 deadline_exhausted 收口。
    assert [p["code"] for p in _r["acceptance"]["problems"]] == [
        "verify_deadline_exhausted"], _r["acceptance"]
    assert _r["executable"] is False, _r
    assert _r["failure"]["code"] == "deadline_exhausted", _r["failure"]
    assert _r["acceptance"]["verdict"] == "deadline", _r["acceptance"]
    assert _r["revision"]["deadline_exhausted"] is True or _r["revision"]["calls"] >= 0


def test_d01_mismatched_deadline_arguments_take_the_earlier_one(monkeypatch, capsys):
    """§7.2：两个截止参数不一致 ⇒ 取**更早者**并告警（不静默各开一张表）."""
    _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, deadline_monotonic=1.0, run_deadline_at=9e9)
    _out = capsys.readouterr().out
    assert "不一致" in _out and "更早者" in _out, _out
    assert _r["executable"] is False and _r["failure"]["code"] == "deadline_exhausted"


# ---------------------------------------------------------------------------
# D05 复核 2026-10-02（之三）P1：**调用类失败就是主终局**（有基线也不许被内容门顶替）
# 旧写法：控制器返回 `call_failed`/`illegal_response` 且还有基线计划时，main 又去跑
# 生成前内容门 ⇒ 终局变成 `PlanContractInvalidError`/`plan_contract_invalid`，
# 真实的调用失败只剩在修订历史里（归因反了）。
# ---------------------------------------------------------------------------
_D05 = [("call_failed", []),                       # 桩的响应被用尽 ⇒ TextGenerationResult(success=False)
        # 修订连续三轮都回不可解析的文本 ⇒ 额度耗尽，主终局仍是**响应违约**
        ("illegal_response", ["不是 JSON"] * _N)]


@pytest.mark.parametrize("code,payloads", _D05, ids=[x[0] for x in _D05])
def test_d05_a_call_class_failure_stays_the_terminal_even_with_a_baseline(
        tmp_path, monkeypatch, code, payloads):
    """**贯通用例**（首版有问题 ⇒ 进控制器 ⇒ 修订调用失败/非法响应），两种失败各一条.

    断言：终局是**调用类失败**（`PlanInfeasibleError` + `conclusion=<码>`），
    基线的内容问题**单独记录**在同一份诊断里；**不落** `plan_contract_invalid`。
    """
    from step01_script_gen import main as _m
    monkeypatch.setattr(_m, "CHAPTER_DIR", tmp_path)
    _patch(monkeypatch, [_broken_plan()] + list(payloads))
    _out = extract_all_concurrent(_SRC, None, None, enable_characters=False,
                                  enable_scene_objects=False,
                                  enable_outline=True)["outline"]
    assert _out["executable"] is False and _out["failure"]["code"] == code, _out
    assert isinstance(_out["plan"], dict) and _out["plan"], "基线计划还在（供诊断）"
    assert _out["revision"]["stop_reason"] == code, _out["revision"]
    with pytest.raises(pf.PlanInfeasibleError) as _e:
        _m._stop_if_outline_unavailable(_out, _SRC, None)
    assert code in str(_e.value), str(_e.value)
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["conclusion"] == code and _d["verdict"] == "unevaluable", _d
    assert _d["baseline_problems"], "基线的内容问题必须**单独记录**（另列一栏）"
    assert any("carrier" in str(x) or "承载" in str(x) for x in _d["baseline_problems"]), \
        _d["baseline_problems"]
    assert not (tmp_path / "step01_plan_contract_invalid.json").exists(), (
        "调用类失败不许被写成内容结论（plan_contract_invalid）")


def test_d05_a_plan_level_stop_still_goes_through_the_content_gate(tmp_path, monkeypatch):
    """**对照**：计划层结论候选（如 `contract_unclosed`）**仍然**交给生成前统一入口——
    那份计划的真实结论就是内容问题，不许被降级成"这一轮没跑成".
    """
    from step01_script_gen import main as _m
    monkeypatch.setattr(_m, "CHAPTER_DIR", tmp_path)
    _res = {"executable": False, "plan": _broken_plan(),
            "failure": {"code": "contract_unclosed", "detail": "承载没闭合"}}
    with pytest.raises(pf.PlanContractInvalidError):
        _m._stop_if_outline_unavailable(_res, _SRC, None)
    assert (tmp_path / "step01_plan_contract_invalid.json").exists()
    assert not (tmp_path / "step01_plan_infeasible.json").exists()


# ---------------------------------------------------------------------------
# D06 复核 2026-10-02（之三）P2：**留档失败不许声称"已留档"、更不许照旧覆盖**
# 三条路（契约版本失效 / 验收拒收 / 常规轮转）共用一处留档；写不成就**不覆盖**
# （旧稿是唯一副本），失败如实上报。
# ---------------------------------------------------------------------------
def _backup_boom(suffix):
    """**只让指定后缀**的留档失败（其它路径照常）——这样注入的故障就是被测的那一条，
    不会被另一条留档路径顺手兜住（兜住本身是对的，但会让判别性实验分不清是谁挡下的）.

    签名跟 `_cache_backup(cache_path, suffix)` 一致：留档**自己读盘**（复核之四 D-2）。
    """
    _real = o._cache_backup

    def _f(cache_path, suf):
        if suf == suffix:
            raise OSError("备份盘写不进去（注入 %s）" % suf)
        return _real(cache_path, suf)
    return _f


def test_d06_a_failed_backup_on_a_stale_contract_version_keeps_the_old_cache(
        tmp_path, monkeypatch, capsys):
    _cache = tmp_path / "c.json"
    _patch(monkeypatch, [_legal_plan()])
    o.generate_outline_result(_SRC, cache_path=_cache)
    _disk = json.loads(_cache.read_text(encoding="utf-8"))
    _disk["plan_contract_version"] = 3
    _write_cache(_cache, _disk)
    _before = _cache.read_text(encoding="utf-8")
    _bak = tmp_path / ("c.json.bak-contract%d" % pf.PLAN_CONTRACT_VERSION)
    monkeypatch.setattr(o, "_cache_backup", _backup_boom(_bak.name[len("c.json"):]))
    _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True and isinstance(_r["plan"], dict), _r
    assert _r["cache"]["backup"] is None and _r["cache"]["backup_error"], _r["cache"]
    assert _r["cache"]["written"] is False, "留档失败 ⇒ 不许覆盖旧缓存"
    assert _cache.read_text(encoding="utf-8") == _before, "旧缓存必须原样保留"
    assert not _bak.exists(), "没写成就不许报告成已留档"
    _out = capsys.readouterr().out
    assert "留档失败" in _out and "旧缓存已留档" not in _out, _out


def test_d06_a_failed_backup_on_a_rejected_cache_keeps_the_old_cache(
        tmp_path, monkeypatch, capsys):
    _cache = tmp_path / "c.json"
    _patch(monkeypatch, [_legal_plan()])
    o.generate_outline_result(_SRC, cache_path=_cache)
    _disk = json.loads(_cache.read_text(encoding="utf-8"))
    _disk["outline"] = _broken_plan()
    _write_cache(_cache, _disk)
    _before = _cache.read_text(encoding="utf-8")
    monkeypatch.setattr(o, "_cache_backup", _backup_boom(".bak-rejected"))
    _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True, _r
    assert _r["cache"]["backup"] is None and _r["cache"]["backup_error"], _r["cache"]
    assert _r["cache"]["written"] is False
    assert _cache.read_text(encoding="utf-8") == _before
    assert not (tmp_path / "c.json.bak-rejected").exists()
    assert "留档失败" in capsys.readouterr().out


def test_d06_a_failed_rotation_backup_skips_the_overwrite(tmp_path, monkeypatch, capsys):
    """**常规轮转**（覆盖前 `_rotate_outline_cache_on_regen`）同一处留档：失败 ⇒ 不覆盖."""
    _cache = tmp_path / "c.json"
    _write_cache(_cache, {"source_hash": "旧键", "plan_contract_version":
                          pf.PLAN_CONTRACT_VERSION, "outline": _legal_plan()})
    _before = _cache.read_text(encoding="utf-8")
    monkeypatch.setattr(o, "_cache_backup", _backup_boom(".bak"))
    _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True and isinstance(_r["plan"], dict)
    assert _r["cache"]["written"] is False and _r["cache"]["backup_error"], _r["cache"]
    assert _cache.read_text(encoding="utf-8") == _before, "旧缓存必须原样保留"
    assert not (tmp_path / "c.json.bak").exists()
    assert "留档失败" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# D07 复核 2026-10-02（之四）D-1：**主原因取修订失败的真实详情**，基线问题是**另列**附栏
# 旧写法：`failure.detail` 填的是基线自己的内容问题（F1 承载），真实的
# `call_failed: transport reset at gateway` 只剩在 `revision.history` 里；而 main 的
# "另列基线问题"这一步自己抛异常时，兜底项是**字符串**，随后被 `.get()` 调用 ⇒
# `AttributeError`，诊断一次也没写成。两处修在**同一个终局出口**。
# ---------------------------------------------------------------------------
def test_d07_the_detail_is_the_revision_reason_not_the_baseline_problem(tmp_path, monkeypatch):
    """贯通用例：首版有承载问题 ⇒ 修订**调用失败** ⇒ 终局详情是**传输失败**，
    基线问题留在 `acceptance.problems`（另列附栏），不冒充主原因.
    """
    _patch(monkeypatch, [_broken_plan()], error="transport reset at gateway")
    _r = o.generate_outline_result(_SRC)
    assert _r["failure"]["code"] == "call_failed", _r["failure"]
    assert "transport reset at gateway" in _r["failure"]["detail"], _r["failure"]
    assert "F1 声明由 B3 承载" not in _r["failure"]["detail"], (
        "基线自己的内容问题不许当主原因（那是另列的附栏）")
    assert _r["revision"]["history"][-1]["detail"], _r["revision"]["history"]
    assert any("carrier" in str(x.get("code")) for x in _r["acceptance"]["problems"]), (
        _r["acceptance"])

    # 终局出口：诊断里两件事都在、各归各位
    from step01_script_gen import main as _m
    monkeypatch.setattr(_m, "CHAPTER_DIR", tmp_path)
    with pytest.raises(pf.PlanInfeasibleError):
        _m._stop_if_outline_unavailable(_r, _SRC, None)
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["conclusion"] == "call_failed" and "transport reset at gateway" in _d["detail"], _d
    assert _d["baseline_problems"] and all(isinstance(x, str) for x in _d["baseline_problems"]), (
        _d["baseline_problems"])


def test_d07_mixed_record_types_in_the_appendix_do_not_crash_the_terminal(tmp_path, monkeypatch):
    """附栏**类型稳定**：记录可能是字典也可能是字符串——旧写法对兜底项调 `.get()` 抛
    `AttributeError`，诊断一次也没写（复核实证）.

    判别性：把 main 的 `record_text` 换回 `x.get("reason") or x.get("code")` ⇒ 转红。
    """
    from step01_script_gen import main as _m
    monkeypatch.setattr(_m, "CHAPTER_DIR", tmp_path)
    _res = {"executable": False, "plan": _broken_plan(),
            "failure": {"code": "call_failed", "detail": "transport reset at gateway"},
            "acceptance": {"problems": ["一整条字符串记录（回退项）",
                                        {"code": "carrier_fact_line_mismatch",
                                         "reason": "字典记录：F1 承载错位"}]}}
    with pytest.raises(pf.PlanInfeasibleError):
        _m._stop_if_outline_unavailable(_res, _SRC, None)
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["baseline_problems"] == ["一整条字符串记录（回退项）",
                                       "字典记录：F1 承载错位"], _d["baseline_problems"]
    assert _d["conclusion"] == "call_failed"


def test_d07_a_failing_baseline_listing_still_writes_the_diagnostic(tmp_path, monkeypatch):
    """**复核的实际回放**：另列基线问题的那一步**自己抛异常**时，终局仍要写成诊断
    （旧写法：兜底项是字符串 → `.get()` → `AttributeError` → 诊断一次也没写）.
    """
    from step01_script_gen import main as _m
    monkeypatch.setattr(_m, "CHAPTER_DIR", tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("验收链自身抛异常（注入）")
    monkeypatch.setattr(o, "plan_acceptance_problems", _boom)
    _res = {"executable": False, "plan": _broken_plan(),
            "failure": {"code": "call_failed", "detail": "transport reset at gateway"}}
    with pytest.raises(pf.PlanInfeasibleError) as _e:
        _m._stop_if_outline_unavailable(_res, _SRC, None)
    assert "AttributeError" not in str(_e.value)
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["conclusion"] == "call_failed" and "transport reset at gateway" in _d["detail"]
    assert _d["baseline_problems"] and "无法列出" in _d["baseline_problems"][0], _d["baseline_problems"]


def test_d07_the_appendix_accepts_a_single_string(tmp_path):
    """`dump_plan_unavailable` 的 `baseline_problems` **收单条字符串**（不是逐字符拆开）."""
    pf.dump_plan_unavailable(tmp_path, code="call_failed", detail="d",
                             plan=_broken_plan(), baseline_problems="一整条记录")
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["baseline_problems"] == ["一整条记录"], _d["baseline_problems"]


def test_d07_the_problem_record_helpers_tolerate_non_dict_records():
    """同一处归一：`problem_records` / `record_text` / `residual_class` 都不假设记录是字典.

    判别性：把 `residual_class` 改回 `str((p or {}).get("code") or "")` ⇒ 第一段转红
    （字符串记录上 `.get()` 抛 `AttributeError`）。
    """
    from step01_script_gen import plan_revision as pr
    assert pr.problem_records(None) == [] and pr.problem_records("一条") == ["一条"]
    assert pr.problem_records([{"code": "x"}]) == [{"code": "x"}]
    assert pr.record_text("一条") == "一条"
    assert pr.record_text({"code": "c", "reason": "r"}) == "r"
    assert pr.residual_class([]) == "none"
    # 字符串记录没有 `code`：**不许崩**，也不许被读成"没有问题"——按**非预算**一类算
    # （与旧实现给"没有 code 的记录"的归类一致：有记录就说明还有问题）
    assert pr.residual_class(["字符串记录"]) == "contract"
    assert pr.residual_class([{"code": "budget_over"}]) == "budget"


# ---------------------------------------------------------------------------
# D08 复核 2026-10-02（之四）D-2：**旧缓存读不出** ⇒ 留档做不成 ⇒ **不许覆盖**
# 旧实现：首读失败转首版；轮转函数再读失败便当"没有旧稿"直接 return，随后照旧覆盖
# （离线实测：`written=True`、无备份、旧文件被换掉）。留档现在**自己读盘**，读不出与
# 写不成同一条出口。
# ---------------------------------------------------------------------------
def _break_reads(monkeypatch, target):
    """让 `target` 的**读**（文本与字节两条）都抛 `OSError`——模拟"旧文件读不出"."""
    _rt, _rb = Path.read_text, Path.read_bytes

    def _t(self, *a, **k):
        if Path(self) == target:
            raise OSError("磁盘读失败（注入）")
        return _rt(self, *a, **k)

    def _b(self, *a, **k):
        if Path(self) == target:
            raise OSError("磁盘读失败（注入）")
        return _rb(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _t)
    monkeypatch.setattr(Path, "read_bytes", _b)


def test_d08_an_unreadable_old_cache_is_never_overwritten(tmp_path, monkeypatch, capsys):
    """**复核的实际回放**：旧缓存读不出 ⇒ `written=False` + `backup_error` + 旧文件原样.

    判别性：把 `_cache_backup` 换回"调用方先读、传进来"（或让轮转在读失败时 `return`）
    ⇒ 转红（`written=True`、无备份、旧文件被换掉）。
    """
    _cache = tmp_path / "c.json"
    _patch(monkeypatch, [_legal_plan()])
    o.generate_outline_result(_SRC, cache_path=_cache)
    _disk = json.loads(_cache.read_text(encoding="utf-8"))
    _disk["_marker"] = "旧稿"                       # 让新旧内容**不同**（否则证不了"被覆盖"）
    _write_cache(_cache, _disk)
    _before = _cache.read_bytes()
    _break_reads(monkeypatch, _cache)
    _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True, _r
    assert _r["cache"]["written"] is False, "留档做不成 ⇒ 不许覆盖旧缓存"
    assert _r["cache"]["backup"] is None and _r["cache"]["backup_error"], _r["cache"]
    assert _cache.open("rb").read() == _before, "旧缓存必须原样保留"
    assert not (tmp_path / "c.json.bak").exists(), "没写成就不许声称已留档"
    _out = capsys.readouterr().out
    assert "留档失败" in _out and "旧缓存已留档" not in _out, _out


def test_d08_an_unparseable_old_cache_is_still_archived_verbatim(tmp_path, monkeypatch):
    """**对照**：旧文件**读得出但解析不了**（坏 JSON）不算"没法留档"——按字节留档后照常更新.

    判别性：留档改回"先 `json.loads` 再备份"（旧实现）⇒ 轮转在读失败处提前 return，
    坏缓存被直接覆盖、`.bak` 不存在 ⇒ 转红。
    """
    _cache = tmp_path / "c.json"
    _garbage = "{ 这不是 JSON —— 上一版留下的坏缓存"
    _cache.write_text(_garbage, encoding="utf-8")
    _patch(monkeypatch, [_legal_plan()])
    _r = o.generate_outline_result(_SRC, cache_path=_cache)
    assert _r["executable"] is True, _r
    assert _r["cache"]["written"] is True, _r["cache"]
    assert (tmp_path / "c.json.bak").read_text(encoding="utf-8") == _garbage, (
        "旧内容必须**逐字**留档（按字节，不依赖解析）")
