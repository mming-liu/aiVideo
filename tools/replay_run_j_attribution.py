# -*- coding: utf-8 -*-
"""M01：run j 原答卷的**离线阶段归因回放**（零模型 / 零网络 / 零正式产物写入）.

历史现场只保存了「合并后 A vs 已整轮回退的最终稿 D」的 36 个字段差异
（`postprocess_reverted`），没有保存后处理结束时的 B 快照，因而无法回答
「哪些字段真被 writer 改回、哪些只是事务回滚」。本工具用当前代码重放：

    A = 真实 `_apply_shot_replacements` 应用 run j 原答卷（授权集合取历史台账）
    B = 按事务顺序跑真实后处理链，逐 writer 由生产 `RepairEffectLedger` 冻结
    D = 历史最终稿（revision_after == revision_before ⇒ 整轮回退到基线）

结论只覆盖可证明的阶段差异；历史终局不改写，也不冒充「重新跑了一次真实运行」。

用法：
    python tools/replay_run_j_attribution.py --json-out <path>
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _chapter_rel(chapter: int):
    """<小说名>/part_NN_<部名>/chapter_NNN，书名/部名来自项目配置."""
    import shared.config  # noqa: F401  加载 .env.content
    from shared.paths import chapter_relpath
    return chapter_relpath(chapter)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUN_J = ROOT / ".dsh/step01_fix_verify_20261115j/output" / _chapter_rel(1)
SNAPSHOT = RUN_J / "snapshots/script_result_20261115_175552.json"
ANSWER = RUN_J / "step01_call_bodies/c1abb36fe6f7042f5.txt"
ROUNDS = RUN_J / "step01_repair_rounds.json"
AUDIT = RUN_J / "step01_audit.blocked.json"
NOVEL = RUN_J / "_resume/novel_aba1adc22826.txt"


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _warnings_delta(m, base, post, novel) -> dict:
    """确定性 checker 在「基线 → 后处理稿」上的读数（无网络；失败如实标注）."""
    try:
        before = m._collect_repair_warnings(base, novel) or {}
        after = m._collect_repair_warnings(post, novel) or {}
        bi, ai = before.get("issues") or [], after.get("issues") or []
        return {
            "before_count": len(bi), "after_count": len(ai),
            "codes_before": sorted({str(x.get("code")) for x in bi}),
            "codes_after": sorted({str(x.get("code")) for x in ai}),
        }
    except Exception as exc:  # noqa: BLE001
        return {"unavailable": "%s: %s" % (type(exc).__name__, exc)}


def _target_analysis(audit, hist_map, decision) -> dict:
    """M01 追问：模型改动若在后处理阶段仍在，目标问题是否曾被解决？

    只给可核验的事实（原问题码 / 补丁是否补上问题要求的事件 / 确定性
    checker 前后计数 / 历史裁决），不代替内容评阅下「质量是否合格」的结论。
    """
    issues = [x for x in (audit.get("issues") or [])
              if str(x.get("shot_id")) in ("4", "shot_4")]
    merged_motion = str((hist_map.get("motion_desc") or {})
                        .get("values", {}).get("merged") or "")
    merged_ff = str((hist_map.get("ff_desc") or {})
                    .get("values", {}).get("merged") or "")
    return {
        "shot4_issue_codes": [x.get("code") for x in issues],
        "shot4_issue_field_paths": [x.get("field_path") for x in issues],
        "patch_touched_fields": sorted(p for p in ("motion_desc", "ff_desc")
                                      if p in hist_map),
        "patch_motion_desc_added_grab_event": any(
            kw in merged_motion for kw in ("抓发", "步摇", "碰落")),
        "patch_ff_desc_still_declared_hair_down": "秀发披散" in merged_ff,
        "historical_resolved": decision.get("resolved"),
        "historical_reason": decision.get("reason"),
        "note": "原问题建议「首帧保持发髻 + motion_desc 补抓发碰落步摇事件」；"
                "回放中的补丁文本与确定性 checker 读数用于判断补丁是否命中该要求，"
                "不改写历史裁决，也不作内容质量结论。",
    }


def _node(writer: str, script, allowed):
    from step01_script_gen.repair_attribution import authorized_path_snapshot
    return {"node": "writer", "writer": writer,
            "authorized_paths": authorized_path_snapshot(script, allowed)}


def _val(v, limit: int = 220) -> str:
    """把任意字段值渲染成单行可读文本（超长截断并标注）。"""
    if isinstance(v, (dict, list)):
        txt = json.dumps(v, ensure_ascii=False)
    else:
        txt = str(v)
    txt = txt.replace("\n", "\\n")
    return txt if len(txt) <= limit else txt[:limit] + "…"


def _shot_by_id(script, sid: str):
    """按目标 id 取镜头：真实稿件用 `index`（int），部分渠道用 `shot_id`。"""
    for s in (script.get("shots") or []):
        if not isinstance(s, dict):
            continue
        if str(s.get("shot_id") or "") == sid:
            return s
        if str(s.get("index", "")) == sid:
            return s
    return None


def _render_draft(base, merged, final, picked, decision, all_fields) -> str:
    """M05：把冻结现场的修订前后稿件渲染成可阅读对照（不含任何时间戳）。"""
    lines: list = [
        "# run j 冻结现场：修订前后可读对照",
        "",
        "> 由 `tools/replay_run_j_attribution.py --markdown-out` 生成；"
        "零模型调用、零正式产物写入。",
        "> 字段值来自真实回放阶段：`基线(A)` / `合并后(B前的模型写入)` / `最终返回稿(D)`。",
        "",
        "## 终局",
        "",
        "- accepted：`%s`；reason：`%s`；resolved：`%s`"
        % (decision.get("accepted"), decision.get("reason"),
           decision.get("resolved")),
        "- revision：`%s` → `%s`（相等即整轮回退到基线）"
        % (decision.get("revision_before"), decision.get("revision_after")),
        "",
    ]
    for tid in sorted(picked):
        sid = tid[5:] if tid.startswith("shot_") else tid
        b, mg, f = (_shot_by_id(base, sid), _shot_by_id(merged, sid),
                    _shot_by_id(final, sid))
        if b is None:
            lines += ["## %s" % tid, "", "_基线稿中不存在该目标_", ""]
            continue
        lines += ["## %s" % tid, "", "| 项 | 基线(A) | 合并后(B) | 最终(D) |",
                  "| --- | --- | --- | --- |"]
        for label, key in (("prompt", "prompt"),
                           ("motion_desc", "motion_desc"),
                           ("char_layout", "char_layout")):
            lines.append("| `%s` | %s | %s | %s |"
                         % (label, _val((b or {}).get(key)),
                            _val((mg or {}).get(key)),
                            _val((f or {}).get(key))))
        _kt = [k.get("t_sec") for k in (b.get("keyframes") or [])
               if isinstance(k, dict)]
        _km = [k.get("t_sec") for k in (mg.get("keyframes") or [])
               if isinstance(k, dict)]
        _kf = [k.get("t_sec") for k in (f.get("keyframes") or [])
               if isinstance(k, dict)]
        lines.append("| `keyframes.t_sec` | %s | %s | %s |"
                     % (_val(_kt), _val(_km), _val(_kf)))
        for tbl in ("ff", "lf", "kfs"):
            _bv, _mv, _fv = ((b.get("frame_layouts") or {}).get(tbl),
                             (mg.get("frame_layouts") or {}).get(tbl),
                             (f.get("frame_layouts") or {}).get(tbl))
            lines.append("| `frame_layouts.%s` 长度 | %s | %s | %s |"
                         % (tbl, len(_bv) if isinstance(_bv, list) else "—",
                            len(_mv) if isinstance(_mv, list) else "—",
                            len(_fv) if isinstance(_fv, list) else "—"))
        _k0b = ((b.get("frame_layouts") or {}).get("kfs") or [None])[0]
        _k0m = ((mg.get("frame_layouts") or {}).get("kfs") or [None])[0]
        _k0f = ((f.get("frame_layouts") or {}).get("kfs") or [None])[0]
        lines.append("| `kfs[0][0]` | %s | %s | %s |"
                     % (_val(_k0b), _val(_k0m), _val(_k0f)))
        lines.append("")
        _hit = {p: r for p, r in (all_fields.get(tid) or {}).items()
                if r.get("transaction_rolled_back") or r.get("writer_changed")}
        if _hit:
            lines += ["本目标在回放中的字段级归属（含曾由 writer 改写或随事务回退的字段）：", "",
                      "| 字段 | 主阶段 | 模型写入 | writer 改写 | 事务回退 | 首发 writer |",
                      "| --- | --- | --- | --- | --- | --- |"]
            for p in sorted(_hit):
                r = _hit[p]
                lines.append("| `%s` | `%s` | %s | %s | %s | `%s` |"
                             % (p, r["stage"], r["model_written"],
                                r["writer_changed"],
                                r["transaction_rolled_back"],
                                r.get("first_writer") or ""))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    import step01_script_gen.main as m
    import step01_script_gen.constraints as constraints
    import step01_script_gen.validation as validation
    from shared.quality_gate import compute_script_revision
    from shared import style_prefix
    from step01_script_gen.repair_attribution import (
        attribute_field_loss, authorized_path_snapshot, changed_paths)
    from step01_script_gen.repair_effects import RepairEffectLedger

    base = _load(SNAPSHOT)
    answer = _load(ANSWER)
    rounds = _load(ROUNDS)
    audit = _load(AUDIT)
    decision = [x for x in rounds
                if isinstance(x, dict) and "accepted" in x][0]
    novel = NOVEL.read_text(encoding="utf-8") if NOVEL.is_file() else ""

    allowed = {tid: set(v.get("allowed") or [])
               for tid, v in (decision.get("authorization") or {}).items()}
    picked = set(decision.get("targets") or [])
    hist_reverted = (decision.get("postprocess_reverted") or {}).get("shot_4") or []

    rev_base = compute_script_revision(base)
    inputs = {p.name: _sha(p) for p in (SNAPSHOT, ANSWER, ROUNDS, AUDIT)}
    inputs["novel_resume"] = _sha(NOVEL) if NOVEL.is_file() else ""

    # ---- A：真实替换入口 -------------------------------------------------
    mappings: dict = {}
    merged, n_applied, rejected = m._apply_shot_replacements(
        copy.deepcopy(base), answer.get("shot_replacements") or [],
        picked, hook_replacement=answer.get("hook_replacement"),
        allowed_by_target=allowed, frame_mappings_out=mappings)
    rev_merged = compute_script_revision(merged)

    # ---- B：按事务顺序跑真实后处理 ---------------------------------------
    ledger = RepairEffectLedger(round_id="replay-run-j-M01",
                                source_revision=rev_base,
                                frame_targets=set(picked),
                                allowed_by_target=dict(allowed))
    ledger.freeze_candidate(base, rev_base)
    ledger.freeze_merged(merged, [])
    nodes = list(ledger.nodes)

    s = copy.deepcopy(merged)
    writer_steps: list = []
    errors: list = []
    try:
        s = style_prefix.inject_global_config_into_script(s)
        nodes.append(_node("style_prefix.inject_global_config_into_script", s, allowed))
        writer_steps.append("inject_global_config_into_script")
    except Exception as exc:  # noqa: BLE001
        errors.append("inject_global_config_into_script: %s" % exc)
    try:
        m.write_timeline_from_durations(s)
        nodes.append(_node("write_timeline_from_durations", s, allowed))
        writer_steps.append("write_timeline_from_durations")
    except Exception as exc:  # noqa: BLE001
        errors.append("write_timeline_from_durations: %s" % exc)
    try:
        frozen = m._snapshot_untargeted(s, set(picked))
    except Exception as exc:  # noqa: BLE001
        frozen = None
        errors.append("snapshot_untargeted: %s" % exc)
    try:
        s = validation.post_fix_shot_prompts(s)
        nodes.append(_node("post_fix_shot_prompts", s, allowed))
        writer_steps.append("post_fix_shot_prompts")
    except Exception as exc:  # noqa: BLE001
        errors.append("post_fix_shot_prompts: %s" % exc)
    repair_indices = set()
    for tid in picked:
        bare = tid[5:] if tid.startswith("shot_") else tid
        try:
            repair_indices.add(int(bare))
        except (TypeError, ValueError):
            pass
    try:
        hard_started = len(ledger.nodes)
        s = constraints.run_hard_fix(
            s, is_in_repair_loop=True, is_final_pass=False,
            novel_text=novel, on_constraint_done=ledger.observe_writer,
            repair_effects=None, repair_shot_indices=repair_indices)
        # on_constraint_done 已经把每个约束的快照写进 ledger.nodes
        for n in ledger.nodes[hard_started:]:
            nodes.append({"node": "writer", "writer": n.get("writer") or "?",
                          "authorized_paths": n.get("authorized_paths") or {}})
        writer_steps.append("run_hard_fix(%d constraints)" %
                            max(0, len(ledger.nodes) - hard_started))
    except Exception as exc:  # noqa: BLE001
        errors.append("run_hard_fix: %s: %s" % (type(exc).__name__, exc))
    try:
        if frozen is not None:
            m._restore_untargeted(s, frozen)
        nodes.append(_node("restore_untargeted", s, allowed))
        writer_steps.append("restore_untargeted")
    except Exception as exc:  # noqa: BLE001
        errors.append("restore_untargeted: %s" % exc)

    rev_post = compute_script_revision(s)
    # D：历史终局整轮回退到基线（台账 revision_before == revision_after）
    final = copy.deepcopy(base) if (decision.get("revision_before")
                                   == decision.get("revision_after")) else None
    if final is None:
        final = copy.deepcopy(s)
    rev_final = compute_script_revision(final)

    snaps = {
        "baseline": authorized_path_snapshot(base, allowed),
        "merged": authorized_path_snapshot(merged, allowed),
        "postprocess": authorized_path_snapshot(s, allowed),
        "post_rollback": authorized_path_snapshot(s, allowed),
        "final": authorized_path_snapshot(final, allowed),
    }
    attr = attribute_field_loss(
        snapshots=snaps, writer_nodes=nodes,
        preview_scripts={"baseline": base, "merged": merged,
                         "postprocess": s, "final": final})

    fields = attr["fields"]
    hist_map = {}
    writer_changed_fields = {}
    for tid, paths in fields.items():
        for p, r in paths.items():
            brief = {"stage": r["stage"], "stages": r["stages"],
                     "model_written": r["model_written"],
                     "writer_changed": r["writer_changed"],
                     "first_writer": r["first_writer"],
                     "transaction_rolled_back": r["transaction_rolled_back"],
                     "digests": r["digests"],
                     "values": r.get("values") or {}}
            if r["writer_changed"]:
                writer_changed_fields["%s:%s" % (tid, p)] = brief
            if p in hist_reverted:
                hist_map[p] = brief
    stages_hist: dict = {}
    for p, r in hist_map.items():
        stages_hist[r["stage"]] = stages_hist.get(r["stage"], 0) + 1

    out = {
        # 不含生成时间戳：覆盖重跑必须逐字节一致，否则登记的 sha256 无法复核。
        # 需要现场时间信息时读输入文件的 mtime（见 input_mtimes）。
        "generator": "tools/replay_run_j_attribution.py",
        "status": "ok",
        "scope": "M01 离线阶段归因回放；零模型调用、零正式产物写入",
        "run": "step01_fix_verify_20261115j（run j）",
        "input_sha256": inputs,
        "input_mtimes": {
            p.name: datetime.fromtimestamp(p.stat().st_mtime).astimezone()
                    .isoformat(timespec="seconds")
            for p in (SNAPSHOT, ANSWER, ROUNDS, AUDIT) if p.is_file()},
        "frozen_revision": rev_base,
        "revision_identity_ok": rev_base == decision.get("revision_before"),
        "historical_outcome": {
            "accepted": decision.get("accepted"),
            "reason": decision.get("reason"),
            "resolved": decision.get("resolved"),
            "revision_before": decision.get("revision_before"),
            "revision_after": decision.get("revision_after"),
            "postprocess_reverted_shot4_count": len(hist_reverted),
            "note": "历史台账未保存后处理结束时（B）的授权路径快照，故其 36 字段"
                    "无法区分 writer 改写与事务回滚；本回放不改写该历史记录。",
        },
        "replace_entry": {
            "applied": n_applied,
            "rejected": rejected,
            "exported_mapping": {k: {kk: v for kk, v in mp.items()
                                     if kk in ("source", "source_revision",
                                               "before_count", "after_count",
                                               "old_to_new", "added_indices")}
                                 for k, mp in mappings.items() if isinstance(mp, dict)},
        },
        "postprocess_writers": writer_steps,
        "postprocess_errors": errors,
        "stage_revisions": {"baseline": rev_base, "merged": rev_merged,
                            "postprocess": rev_post, "final": rev_final},
        "a_to_b_postprocess_reverted": changed_paths(snaps["merged"],
                                                    snaps["postprocess"]),
        "b_to_d_transaction_rollback": changed_paths(snaps["postprocess"],
                                                     snaps["final"]),
        "field_attribution_summary": attr["summary"],
        "target_problem_analysis": _target_analysis(audit, hist_map, decision),
        "historical_36_fields_attribution": hist_map,
        "historical_36_fields_by_stage": stages_hist,
        "historical_36_fields_not_seen": sorted(
            set(hist_reverted) - set(hist_map)),
        "audit_issue_codes": [x.get("code") for x in (audit.get("issues") or [])],
        "shot4_original_issues": [
            {"code": x.get("code"), "field_path": x.get("field_path"),
             "message": str(x.get("message") or "")[:300]}
            for x in (audit.get("issues") or [])
            if str(x.get("shot_id")) in ("4", "shot_4")],
        "writer_changed_fields": writer_changed_fields,
        "model_changes_survived_postprocess": sum(
            1 for r in hist_map.values()
            if r["model_written"] and not r["writer_changed"]),
        "deterministic_warnings_delta": _warnings_delta(m, base, s, novel),
        "conclusion": {
            "historical_36_all_transaction_rollback":
                stages_hist.get("transaction_rollback", 0) == len(hist_reverted),
            "writer_changed_in_historical_36": sum(
                1 for r in hist_map.values() if r["writer_changed"]),
            "pure_writer_reverts_in_historical_36": sum(
                1 for r in hist_map.values()
                if r["writer_changed"] and r["model_written"]
                and not r["transaction_rolled_back"]),
            "note": "历史 postprocess_reverted 的 36 字段来自「合并后 → 已整轮回退的"
                    "最终稿」，不能归因到后处理 writer。回放显示模型改动在后处理阶段"
                    "基本保留；历史裁决 resolved=0 / no_target_resolved 属目标未解决，"
                    "应沿原问题与补丁内容调查，而不是先改 writer。本回放未重跑真实"
                    "模型、未改写任何历史台账。",
        },
    }

    payload = json.dumps(out, ensure_ascii=False, indent=2)
    md = _render_draft(base, merged, final, picked, decision, fields)
    ap = argparse.ArgumentParser()
    ap.add_argument("--json-out", default="")
    ap.add_argument("--markdown-out", default="")
    args = ap.parse_args()
    if args.markdown_out:
        _mp = pathlib.Path(args.markdown_out)
        _mp.parent.mkdir(parents=True, exist_ok=True)
        _mp.write_text(md, encoding="utf-8", newline="\n")
    if args.json_out:
        p = pathlib.Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
