"""Step01 调用索引构建器（M01）——把 S06 的逐次调用整理成可核对的索引.

**这是专项回放工具，依赖显式声明的外部数据集**，不属于干净检出即可运行的基础测试：

  --baseline-root  `baselines/2026-11-12-pre-layered/s06_ab`
                   已提交的 S06 冻结原件（6 次审计对照 + 1 次修订 + 1 次终局审计）。
  --live-root      `.pytest_tmp/s06`
                   现场过程数据（可选；S06 之后的复跑批次，未纳入版本管理）。
  --fixtures       `tests/step01_script_gen/fixtures/audit_narrative_families`
                   当前受控样本；用于核对「当前夹具能否重建当时发出的提示词」。

它做三件事：

1. **逐次索引**：把每次发送关联到 请求摘要 / 解析结果 / 耗时 / usage / 通道 / 代码版本 /
   候选身份。原始响应只在盘上留存了片段时，如实记为不可用，不补造。
2. **样本漂移核对**：用当前夹具重新构建审计提示词，与当时记录的 `prompt_sha1` 比对。
   不一致即说明「当时的结论属于另一版样本」，不得当成当前样本的证据。
3. **汇总**：按批次统计调用数、token 与耗时，供报告引用。

用法：
    python tools/build_step01_call_index.py --out docs/step01-layered-execution/artifacts/m01
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCHEMA = "step01_call_index.v1"

#: 每个批次的**授权链**（来源 / 范围 / 上限 / 是否已核实）。
#:
#: 2026-11-12 复核：索引必须能回答「这批发次获得了哪次授权」，而不只是消耗统计。
#: 盘上只有 S06 报告的自述口径；复跑批次没有任何授权记录——那 20 次只能确认
#: 「发生了真实发送」，**不能**判定属于原八次额度之内或之外，因此标 `unverified`。
CAMPAIGN_AUTHORIZATION = {
    "s06_ab": {
        "source": "docs/step01-s06-contract-ab-2026-11-12.md",
        "declared_scope": "审计对照 6 次 + 受限修订 1 次 + 终局审计 1 次",
        "declared_limit": 8,
        "status": "declared_in_report",
        "note": ("报告自述「实际发送 8 次，未超出授权」；独立授权凭证（工单/收据）"
                 "不在盘上，属报告自述口径"),
    },
    "s06_ab_rerun": {
        "source": None,
        "declared_scope": None,
        "declared_limit": None,
        "status": "unverified",
        "note": ("无报告、无授权记录、无预算凭据。只能确认「盘上有 20 次真实发送」，"
                 "不能判定它获得过哪次授权、也不能判定是否超出原八次额度"),
    },
}

#: 阶段一 A/B 对照的样本顺序（与当时运行脚本一致；只作导航，样本内容从夹具读取）
AB_SAMPLES = ("time_ellipsis_legal", "state_broken", "voice_broken")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _load_old_audit(path: Path):
    """加载当时使用的旧契约模块（冻结副本，不是当前 `audit.py`）."""
    spec = importlib.util.spec_from_file_location("m01_audit_old", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m01_audit_old"] = mod
    spec.loader.exec_module(mod)
    return mod


def _issue_identity(issue: dict) -> dict:
    """稳定问题身份：code + 镜头 + 字段路径（措辞漂移不影响身份）."""
    return {
        "code": str(issue.get("code") or ""),
        "shot_id": str(issue.get("shot_id") or ""),
        "field_path": str(issue.get("field_path") or ""),
        "severity": str(issue.get("severity") or ""),
    }


def _blocking_count(issues) -> int:
    return sum(1 for i in (issues or [])
               if str(i.get("severity")) in ("visual_risk", "blocking", "hard"))


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# 批次一：审计 A/B（6 次）+ 修订（1 次）+ 终局审计（1 次）
# ---------------------------------------------------------------------------
def _ab_calls(run_dir: Path, base: Path) -> list:
    res = _read_json(run_dir / "results.json")
    meta = res.get("meta") or {}
    calls = []
    for i, r in enumerate(res.get("results") or [], start=1):
        issues = r.get("issues") or []
        prompt_file = run_dir / ("prompt_%s_%s.txt" % (r.get("sample"), r.get("contract")))
        calls.append({
            "call_id": "%s/ab-%02d" % (run_dir.name, i),
            "phase": "audit_ab",
            "sample": r.get("sample"),
            "contract": r.get("contract"),
            "prompt_version": (meta.get("old_prompt_version") if r.get("contract") == "old"
                               else meta.get("new_prompt_version")),
            "request": {
                "prompt_file": _rel(prompt_file, base) if prompt_file.exists() else None,
                "prompt_sha1": r.get("prompt_sha1"),
                "prompt_chars": r.get("prompt_chars"),
            },
            "channel": {"provider": r.get("channel_provider") or meta.get("provider"),
                        "model": r.get("channel_model") or meta.get("model")},
            "status": r.get("status"),
            "error": r.get("error") or "",
            "attempts": r.get("attempts"),
            "elapsed_sec": r.get("elapsed_sec"),
            "usage": r.get("usage"),
            "parsed": {
                "issues_count": r.get("issues_count"),
                "blocking_count": r.get("blocking_count"),
                "issues": [_issue_identity(x) for x in issues],
            },
            "raw_response": {
                "available": False,
                "reason": ("运行脚本只保存 `raw_text[:400]`，而该批记录的 `raw_head` 为空；"
                           "原始响应未落盘，只有解析结果"),
            },
            "repo_head": meta.get("repo_head"),
            "budget_record": {"available": False,
                              "reason": "该批次未走预算凭据机制（无 budget_receipt.json）"},
            "candidate_identity": None,
        })
    return calls


def _phase2_calls(run_dir: Path, base: Path) -> list:
    calls = []
    diag = _read_json(run_dir / "repair_diag.json")
    summary = _read_json(run_dir / "summary.json")
    call_ok = next((row for row in (diag.get("diag") or [])
                    if row.get("kind") == "call_ok"), None)
    decision = next((row for row in (diag.get("diag") or [])
                     if "resolved" in row), None)
    final = _read_json(run_dir / "final_audit.json")

    calls.append({
        "call_id": "%s/repair-01" % run_dir.name,
        "phase": "repair",
        "sample": "u6_candidate",
        "contract": "repair_shot_replacements",
        "prompt_version": None,
        "request": {
            "prompt_file": None,
            "prompt_sha1": None,
            "prompt_chars": None,
            "note": ("请求体未单独落盘；`step01.log` 记录了请求体字节数与 sha256 "
                     "（见 log 的 `[WIRE]` 行）"),
        },
        "channel": {"provider": "agent_plan", "model": "deepseek-v4.1-flash"},
        "status": "ok" if call_ok else "unknown",
        "error": "",
        "attempts": 1,
        "elapsed_sec": summary.get("repair_sec"),
        "usage": (call_ok or {}).get("usage"),
        "parsed": {
            "issues_count": None,
            "blocking_count": None,
            "issues": [],
            "decision": {k: (decision or {}).get(k) for k in
                         ("targets", "groups", "accepted", "round_ok", "resolved",
                          "reason", "origin_reason", "audit_field_touched",
                          "revision_before", "revision_after", "rolled_back_groups")},
        },
        "raw_response": {
            "available": False,
            "reason": ("`_text_gen.agent_plan_provider` 只把 `resp.text[:500]` 写进日志；"
                       "日志里能看到的片段以 shot 7 的 prompt/ff_desc/lf_desc 开头，"
                       "其余镜头与后续字段（含 keyframes）无法从盘上恢复"),
            "partial": {
                "source": "step01.log（隔离输出目录）",
                "visible_chars": 500,
                "first_shot_id": "7",
                "visible_field_paths": ["prompt", "ff_desc", "lf_desc"],
            },
        },
        "repo_head": None,
        "candidate_identity": {
            "revision_before": (decision or {}).get("revision_before"),
            "revision_after": (decision or {}).get("revision_after"),
        },
    })

    calls.append({
        "call_id": "%s/final-audit-01" % run_dir.name,
        "phase": "final_audit",
        "sample": "u6_candidate",
        "contract": "script_audit",
        "prompt_version": final.get("audit_prompt_version"),
        "request": {
            "prompt_file": None,
            "prompt_sha1": None,
            "prompt_chars": None,
            "note": "请求体未单独落盘；`[WIRE]` 行记录了 97173 字节与 sha256",
        },
        "channel": {"provider": final.get("channel_provider"),
                    "model": final.get("model")},
        "status": final.get("status"),
        "error": final.get("error") or "",
        "attempts": 1,
        "elapsed_sec": summary.get("audit_sec"),
        "usage": final.get("usage"),
        "parsed": {
            "issues_count": len(final.get("issues") or []),
            "blocking_count": _blocking_count(final.get("issues")),
            "issues": [_issue_identity(x) for x in (final.get("issues") or [])],
        },
        "raw_response": {
            "available": False,
            "reason": "同 provider 的 500 字符截断；只有解析后的 issues",
        },
        "repo_head": None,
        "candidate_identity": {"revision": final.get("input_revision"),
                               "content_identity": final.get("content_identity")},
    })
    return calls


def _rerun_calls(run_dir: Path, base: Path) -> list:
    """S06 之后的复跑批次（现场数据）：只索引，不并入八次口径."""
    res = _read_json(run_dir / "results.json")
    meta = res.get("meta") or {}
    calls = []
    for i, r in enumerate(res.get("results") or [], start=1):
        if r.get("status") == "dry":
            continue
        calls.append({
            "call_id": "%s/ab-%02d" % (run_dir.name, i),
            "phase": "audit_ab_rerun",
            "sample": r.get("sample"),
            "contract": r.get("contract"),
            "prompt_version": (meta.get("old_prompt_version") if r.get("contract") == "old"
                               else meta.get("new_prompt_version")),
            "request": {
                "prompt_file": None,
                "prompt_sha1": r.get("prompt_sha1"),
                "prompt_chars": r.get("prompt_chars"),
            },
            "channel": {"provider": r.get("channel_provider") or meta.get("provider"),
                        "model": r.get("channel_model") or meta.get("model")},
            "status": r.get("status"),
            "error": r.get("error") or "",
            "attempts": r.get("attempts"),
            "elapsed_sec": r.get("elapsed_sec"),
            "usage": r.get("usage"),
            "parsed": {
                "issues_count": r.get("issues_count"),
                "blocking_count": r.get("blocking_count"),
                "issues": [_issue_identity(x) for x in (r.get("issues") or [])],
            },
            "raw_response": {"available": False,
                             "reason": "同批次脚本只保存 `raw_head[:400]`，该批同样为空"},
            "repo_head": meta.get("repo_head"),
            "budget_record": {"available": False,
                              "reason": "该批次未走预算凭据机制（无 budget_receipt.json）"},
            "candidate_identity": None,
        })
    return calls


# ---------------------------------------------------------------------------
# 样本漂移核对
# ---------------------------------------------------------------------------
def fixture_drift(fixtures: Path, recorded: dict) -> dict:
    """当前夹具能否逐字重建当时发出的提示词.

    `recorded` 形如 `{(sample, contract): prompt_sha1}`。只能核对仍在索引里的样本；
    重建失败（夹具缺失/模块不可用）如实记为 `error`，不跳过。
    """
    from step01_script_gen import audit as new_mod
    old_mod = None
    old_path = ROOT / "baselines/2026-11-12-pre-layered/s06_ab/tools/audit_old.py"
    if old_path.exists():
        try:
            old_mod = _load_old_audit(old_path)
        except Exception as exc:  # pragma: no cover - 诊断路径
            old_mod = None
            old_error = "旧契约模块加载失败：%r" % (exc,)
        else:
            old_error = ""
    else:
        old_error = "旧契约冻结副本缺失：%s" % _rel(old_path, ROOT)

    rows = []
    for (sample, contract), sha in sorted(recorded.items()):
        row = {"sample": sample, "contract": contract, "recorded_sha1": sha}
        path = fixtures / (sample + ".json")
        if not path.exists():
            row.update({"status": "fixture_missing",
                        "fixture": _rel(path, ROOT)})
            rows.append(row)
            continue
        mod = new_mod if contract == "new" else old_mod
        if mod is None:
            row.update({"status": "contract_unavailable", "error": old_error})
            rows.append(row)
            continue
        try:
            case = _read_json(path)
            prompt = mod.build_audit_prompt(case["novel"], case["plan"], case["script"])
        except Exception as exc:
            row.update({"status": "rebuild_error", "error": repr(exc)})
            rows.append(row)
            continue
        current = _sha1(prompt)
        row.update({
            "status": "match" if current == sha else "drift",
            "current_sha1": current,
            "current_chars": len(prompt),
            "fixture": _rel(path, ROOT),
            "fixture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
        rows.append(row)
    return {
        "checked": len(rows),
        "match": sum(1 for r in rows if r["status"] == "match"),
        "drift": sum(1 for r in rows if r["status"] == "drift"),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 汇总与输出
# ---------------------------------------------------------------------------
def _campaign(campaign_id: str, source: str, calls: list, note: str = "") -> dict:
    usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
             "total_tokens": 0}
    elapsed = 0.0
    for c in calls:
        u = c.get("usage") or {}
        if isinstance(u, dict):
            for k in usage:
                usage[k] += int(u.get(k) or 0)
        try:
            elapsed += float(c.get("elapsed_sec") or 0)
        except (TypeError, ValueError):
            pass
    auth = dict(CAMPAIGN_AUTHORIZATION.get(campaign_id) or {
        "source": None, "declared_scope": None, "declared_limit": None,
        "status": "unverified", "note": "该批次没有授权链记录",
    })
    auth["observed_sends"] = len(calls)
    return {
        "campaign_id": campaign_id,
        "source": source,
        "note": note,
        "authorization": auth,
        "calls": len(calls),
        "elapsed_sec_total": round(elapsed, 1),
        "usage_total": usage,
        "entries": calls,
    }


def build(args) -> dict:
    baseline_root = Path(args.baseline_root)
    fixtures = Path(args.fixtures)
    campaigns = []
    recorded = {}

    ab_run = baseline_root / "runs_20261112T070909"
    p2_run = baseline_root / "phase2_20261112T072011"
    if ab_run.exists() and p2_run.exists():
        calls = _ab_calls(ab_run, baseline_root) + _phase2_calls(p2_run, baseline_root)
        campaigns.append(_campaign(
            "s06_ab", _rel(ab_run.parent, ROOT), calls,
            "S06 报告所称的八次发送：6 次审计 A/B + 1 次受限修订 + 1 次终局审计"))
        for c in calls:
            if c["phase"] == "audit_ab" and c["request"]["prompt_sha1"]:
                recorded[(c["sample"], c["contract"])] = c["request"]["prompt_sha1"]
    else:
        campaigns.append({"campaign_id": "s06_ab", "source": _rel(ab_run.parent, ROOT),
                          "calls": 0, "entries": [],
                          "error": "冻结原件缺失（需要 --baseline-root）"})

    live_root = Path(args.live_root) if args.live_root else None
    if live_root and live_root.exists():
        rerun = []
        for d in sorted((live_root / "runs").glob("*")):
            if not (d / "results.json").exists():
                continue
            # 冻结批次已在上面的 campaign 里索引过：baseline 副本目录名带前缀
            # （`runs_<ts>`），现场目录名是裸 `<ts>`，按包含关系跳过，避免重复计数。
            if d.name in ab_run.name:
                continue
            rerun.extend(_rerun_calls(d, live_root))
        if rerun:
            campaigns.append(_campaign(
                "s06_ab_rerun", _rel(live_root, ROOT), rerun,
                "S06 之后的复跑批次（现场数据，未纳入版本管理）；不计入报告的八次口径"))

    drift = fixture_drift(fixtures, recorded) if recorded else {"checked": 0, "rows": []}

    all_calls = [c for camp in campaigns for c in (camp.get("entries") or [])]
    return {
        "schema": SCHEMA,
        "inputs": {
            "baseline_root": _rel(baseline_root, ROOT),
            "live_root": _rel(live_root, ROOT) if live_root else None,
            "fixtures": _rel(fixtures, ROOT),
        },
        "campaigns": campaigns,
        "fixture_drift": drift,
        "totals": {
            "campaigns": len(campaigns),
            "calls": len(all_calls),
            "sends_by_phase": {p: sum(1 for c in all_calls if c["phase"] == p)
                               for p in sorted({c["phase"] for c in all_calls})},
        },
    }


def _md(index: dict) -> str:
    out = ["# Step01 调用索引（M01）", ""]
    out.append("> 由 `tools/build_step01_call_index.py` 生成；不要手工编辑。")
    out.append("")
    out.append("输入：`%s`；夹具：`%s`"
               % (index["inputs"]["baseline_root"], index["inputs"]["fixtures"]))
    out.append("")
    for camp in index["campaigns"]:
        out.append("## 批次 `%s`" % camp["campaign_id"])
        out.append("")
        if camp.get("note"):
            out.append("> %s" % camp["note"])
            out.append("")
        auth = camp.get("authorization") or {}
        if auth:
            out.append("**授权链**：来源 `%s`；声明范围 %s；声明上限 %s；实发 %s；"
                       "核实状态 **%s**"
                       % (auth.get("source") or "—",
                          auth.get("declared_scope") or "—",
                          auth.get("declared_limit"),
                          auth.get("observed_sends"), auth.get("status")))
            if auth.get("note"):
                out.append("")
                out.append("> %s" % auth["note"])
            out.append("")
        if camp.get("error"):
            out.append("**缺失**：%s" % camp["error"])
            out.append("")
            continue
        out.append("调用 %d 次；累计耗时 %.1fs；token 合计 %s"
                   % (camp["calls"], camp["elapsed_sec_total"],
                      (camp["usage_total"] or {}).get("total_tokens")))
        out.append("")
        out.append("| # | 阶段 | 样本 | 契约 | 状态 | issues | blocking | 耗时 | tokens | 代码版本 |")
        out.append("|---|---|---|---|---|---|---|---|---|---|")
        for c in camp["entries"]:
            u = c.get("usage") or {}
            parsed = c.get("parsed") or {}
            out.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                c["call_id"], c["phase"], c.get("sample"),
                c.get("contract"), c.get("status"),
                parsed.get("issues_count"), parsed.get("blocking_count"),
                c.get("elapsed_sec"),
                u.get("total_tokens") if isinstance(u, dict) else "",
                c.get("repo_head") or "—"))
        out.append("")
        missing = [c for c in camp["entries"]
                   if not (c.get("raw_response") or {}).get("available")]
        if missing:
            out.append("**原始响应不可用**：%d/%d 次调用。原因：%s"
                       % (len(missing), camp["calls"],
                          missing[0]["raw_response"].get("reason")))
            out.append("")

    drift = index.get("fixture_drift") or {}
    out.append("## 样本漂移核对")
    out.append("")
    out.append("用**当前**夹具重建当时发出的审计提示词，与记录的 `prompt_sha1` 比对。")
    out.append("")
    out.append("| 样本 | 契约 | 结果 | 记录 | 当前 |")
    out.append("|---|---|---|---|---|")
    for r in drift.get("rows") or []:
        out.append("| %s | %s | %s | %s | %s |" % (
            r["sample"], r["contract"], r["status"],
            str(r.get("recorded_sha1"))[:12],
            str(r.get("current_sha1") or "—")[:12]))
    out.append("")
    out.append("核对 %s 项：一致 %s，漂移 %s。"
               % (drift.get("checked"), drift.get("match"), drift.get("drift")))
    out.append("")
    out.append("漂移的含义：该样本**当时的结论属于另一版样本**，不能当成当前样本的证据；"
               "当前版本若未重新发送，其真实模型表现记为**未验证**。")
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-root",
                    default=str(ROOT / "baselines/2026-11-12-pre-layered/s06_ab"))
    ap.add_argument("--live-root", default=str(ROOT / ".pytest_tmp/s06"))
    ap.add_argument("--fixtures",
                    default=str(ROOT / "tests/step01_script_gen/fixtures/audit_narrative_families"))
    ap.add_argument("--out", default=str(ROOT / "docs/step01-layered-execution/artifacts/m01"))
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    index = build(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from shared.json_io import atomic_dump_json
    atomic_dump_json(out / "call_index.json", index, ensure_ascii=False, indent=2)
    (out / "call_index.md").write_text(_md(index), encoding="utf-8")
    print(json.dumps({"out": str(out), "calls": index["totals"]["calls"],
                      "by_phase": index["totals"]["sends_by_phase"],
                      "drift": {k: index["fixture_drift"].get(k)
                                for k in ("checked", "match", "drift")}},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
