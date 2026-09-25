# -*- coding: utf-8 -*-
"""诊断工具：道具状态候选锚点是否**真的进了提示词、模型是否写出来**（v6.125.2）.

用户 2026-09-25 的验收口径：

> 接线后先做一次诊断生成，核对模型是否写出释放事件、是否落在正确镜头，
> 以及之后的 ff/lf/kfs 是否一致；再讨论能否升级为选稿依据。

## 本工具只做诊断

- **不接** BEST-GUARD / 质量门；**不自动删 `props`**；不写任何正式产物
  （`script_result.json` / `step01_audit.json` / `step01_quality.json` /
  `step01_commit.json` 一律拒绝写出）。
- 大纲缓存默认写在 `--out` 同目录的 `chapter_outline.diag.cache.json`，
  不碰章节目录里的正式缓存。
- 报告写 `<out>.diag.json`：候选锚点、提示词里是否含独立块、模型产出的释放事件
  落在哪一镜、释放之后各帧 `ff/lf/kfs` 的 `props` 是否仍声明持有。
  v6.127（用户 2026-09-25 复核）：`cause_at`（起因帧）与 `release_written_at`
  （稿件**实际写出**的释放）**分开报告**，两者互不推导；稿件没写释放时后者为
  `None`（走重生／拒绝候选）。`diff.clear_candidates` 标 `advisory_only`——
  **只读建议，不是执行许可**。

## 用法

    python -m tools.diag_prop_anchor_generation \
        --out output/.../chapter_001/snapshots/diag_propgen_<ts>.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.config import NOVEL_CHAPTER, NOVEL_NAME, BOOK_INDEX, BOOK_NAME
from shared.json_io import atomic_dump_json

_OFFICIAL_NAMES = ("script_result.json", "step01_audit.json", "step01_quality.json",
                   "step01_commit.json", "step01_repair_rounds.json",
                   "step01_audit_rounds.json")


def _chapter_dir() -> Path:
    return (ROOT / "output" / "video" / NOVEL_NAME
            / f"part_{BOOK_INDEX:02d}_{BOOK_NAME}"
            / f"chapter_{NOVEL_CHAPTER:03d}")


def _guard_out_path(out: Path) -> None:
    if out.name in _OFFICIAL_NAMES:
        raise SystemExit(f"[ABORT] --out 指向正式产物名 `{out.name}`；请换诊断路径")
    if out.parent.resolve() == _chapter_dir().resolve():
        raise SystemExit("[ABORT] --out 落在章节目录根部；请写到 snapshots/ 或临时目录")


def _frames_of(shot: dict) -> list:
    from step01_script_gen.cross_field_checks import frame_entries, frame_ids, frame_text
    out = []
    for fid in frame_ids(shot):
        names = []
        for e in frame_entries(shot, fid):
            props = [str(p).strip() for p in (e.get("props") or []) if str(p).strip()]
            if props:
                names.append({"holder": str(e.get("name") or ""), "props": props})
        out.append({"frame": fid, "text": frame_text(shot, fid)[:160], "entries": names})
    return out


def _all_release_clauses(script: dict) -> list:
    """稿里**所有**含释放谓词的分句（**不预设道具名**）——串名检查的输入.

    用户 2026-09-25 第 2 点：候选是「酒囊」而稿里写「酒壶已脱手」时，用候选名去扫
    根本扫不到。必须先无偏地找出"稿里释放了什么"，再和候选名比对。
    """
    from step01_script_gen.prop_state_anchor import _ALL_RELEASE_MARKERS, _MARKER_AFTER

    out = []
    for s in (script.get("shots") or []):
        if not isinstance(s, dict):
            continue
        for f in _frames_of(s):
            for clause in re.split(r"[，。；！？]", f["text"] or ""):
                if not clause.strip():
                    continue
                _hit = (any(m in clause for m in _MARKER_AFTER)
                        or any(m in clause for m in _ALL_RELEASE_MARKERS)
                        or bool(dg_motion_away(clause)))
                if _hit:
                    out.append({"shot_index": s.get("index"), "frame": f["frame"],
                                "clause": clause})
    return out


def dg_motion_away(clause: str) -> bool:
    """该分句是否是"位移式"释放（拖/拽/拉/扯/卷 + 走/逃/离）——复用模块正则."""
    from step01_script_gen.prop_state_anchor import _MOTION_OBJ_RE
    return bool(_MOTION_OBJ_RE.search(clause))


def _prop_nouns_in(clause: str, exclude: str = "") -> set:
    """该分句里**释放谓词**前后出现的道具名（候选之外的，用于串名检查）.

    v6.125.3：不能拿"任意 2–4 字窗口"当道具名——「酒壶已脱手」会被 4 字窗口吞成
    「酒壶已脱」，反而报不出「酒壶」。改为取**谓词前的名（剥掉 已/也/便/就 等助词）
    + 谓词后的名（剥掉量词）**，通用、可核对。
    """
    from step01_script_gen.prop_state_anchor import _NON_PROP_NOUNS, _clean_noun

    _VERBS = ("脱手", "滑落", "掉落", "坠入", "坠地", "松开", "撒手", "丢了",
              "丢下", "放下", "抛出", "掷出", "拖走")
    _TAIL = "已也便就都竟却又再还仍"

    def _keep(n: str) -> bool:
        return (2 <= len(n) <= 4 and n != exclude
                and not any(w in n for w in _NON_PROP_NOUNS))

    out = set()
    for _v in _VERBS:
        _i = (clause or "").find(_v)
        if _i < 0:
            continue
        _pre = clause[max(0, _i - 4):_i]
        while _pre and _pre[-1] in _TAIL:
            _pre = _pre[:-1]
        _n = _clean_noun(_pre)
        if _keep(_n):
            out.add(_n)
        _post = clause[_i + len(_v):]
        _m = re.match(r"[一-龥]{2,6}", _post)
        _n2 = _clean_noun(_m.group(0))[:4] if _m else ""
        if _keep(_n2):
            out.add(_n2)
    return out


def _prop_mentions_in_order(script: dict, tokens) -> list:
    """按**帧序**列出提到该道具的所有帧与分句——供人工判"释放时点是否正确".

    工具无法离线确定预期时点，所以把顺序摊开给人看（是否早于起因事件）。
    """
    from step01_script_gen.prop_state_anchor import _frame_order

    _ord = _frame_order(script)
    _rows = []
    for s in (script.get("shots") or []):
        if not isinstance(s, dict):
            continue
        for f in _frames_of(s):
            for clause in re.split(r"[，。；！？]", f["text"] or ""):
                if any(t in clause for t in tokens):
                    _rows.append({"order": _ord.get((s.get("index"), f["frame"]), -1),
                                  "shot_index": s.get("index"), "frame": f["frame"],
                                  "clause": clause})
    return sorted(_rows, key=lambda r: r["order"])


def _release_report(script: dict, tokens) -> dict:
    """模型有没有写出释放事件、落在哪一帧、**之后所有帧**是否仍持有.

    v6.125.3（用户 2026-09-25 纠正）：判据必须按**帧序**而不是按镜头——
    同一镜内 `ff → kf0 → … → lf` 的顺序里，早于释放点的帧持有道具是**合法**的；
    旧实现按镜头过滤会把它们算成"仍持有"（灯盏那 2 处就是这么误报的）。
    另单列**释放帧本身**是否仍持有（它是"结果该不该已清"的关键判据）。

    只按「镜头」比会掩盖同镜倒序（诊断稿 shot4 的 ff 写已脱手、kf0 又写持杆）。
    """
    from step01_script_gen.prop_state_anchor import _ALL_RELEASE_MARKERS, _frame_order

    _shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    _ord = _frame_order(script)
    _hits = []
    for s in _shots:
        for f in _frames_of(s):
            for clause in re.split(r"[，。；！？]", f["text"] or ""):
                if any(t in clause for t in tokens) and any(
                        m in clause for m in _ALL_RELEASE_MARKERS):
                    _hits.append({"shot_index": s.get("index"), "frame": f["frame"],
                                  "clause": clause})
    _pt = (_hits[0]["shot_index"], _hits[0]["frame"]) if _hits else None
    _rel = _ord.get(_pt, -1) if _pt else None
    _at, _after = [], []
    if _rel is not None:
        for s in _shots:
            for f in _frames_of(s):
                for e in f["entries"]:
                    if not any(p in tokens for p in e["props"]):
                        continue
                    _o = _ord.get((s.get("index"), f["frame"]), -1)
                    _item = {"shot_index": s.get("index"), "frame": f["frame"],
                             "holder": e["holder"], "props": e["props"]}
                    if _o == _rel:
                        _at.append(_item)
                    elif _o > _rel:
                        _after.append(_item)
    return {"release_mentions": _hits, "release_point": _pt,
            "still_holding_at_release_frame": _at,
            "still_holding_after": _after,
            "frames_after_release": (len([1 for k, v in _ord.items()
                                          if _rel is not None and v > _rel]))}


def main(argv=None) -> int:
    _p = argparse.ArgumentParser(description="道具候选锚点的诊断生成（不写正式产物）")
    _p.add_argument("--out", required=True)
    _p.add_argument("--model", default=None)
    _p.add_argument("--cache", default=None, help="大纲缓存路径（默认 <out 同目录>）")
    args = _p.parse_args(argv)

    out = Path(args.out).resolve()
    _guard_out_path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache).resolve() if args.cache else \
        out.with_name("chapter_outline.diag.cache.json")

    import step01_script_gen.main as m
    from step01_script_gen.outline import generate_outline, outline_to_anchor
    from step01_script_gen.prop_state_anchor import render_prop_anchor_block
    from step01_script_gen.prompts import resolve_model_prompt_profile
    from step01_script_gen.validation import parse_response

    # 与生产同源：章节读取走 main.read_chapter（`shared.chapter_reader` 只有
    # extract_section/extract_chapter，没有这个封装）
    novel_text = m.read_chapter("锦衣夜行_77119小说.txt", chapter=NOVEL_CHAPTER)
    outline = generate_outline(novel_text, args.model, cache_path=cache) or {}
    anchors = outline.get("prop_state_candidates") or []
    block = render_prop_anchor_block(anchors)
    builder, system = resolve_model_prompt_profile(args.model)
    prompt = builder(novel_text, None, None, outline_to_anchor(outline),
                     prop_anchor_block=block)

    from _text_gen.deepseek_schema import SCRIPT_SCHEMA
    from _text_gen.models import TextGenerationRequest
    service = m.create_service()
    req = TextGenerationRequest(system_prompt=system, user_prompt=prompt,
                                temperature=0.2, json_schema=SCRIPT_SCHEMA,
                                model=args.model)
    result = service.generate(req)
    report = {"diagnostic": True, "model": args.model or "provider 默认",
              "outline_cache": str(cache), "anchors": anchors,
              "block_in_prompt": bool(block) and (block in prompt),
              "prompt_chars": len(prompt),
              "generation_success": bool(getattr(result, "success", False)),
              "error": str(getattr(result, "error", "") or "")}
    if not report["generation_success"]:
        atomic_dump_json(out.with_suffix(".diag.json"), report, ensure_ascii=False,
                         indent=2)
        print(json.dumps(report, ensure_ascii=False))
        return 2
    script = parse_response(result.text, novel_text)
    report["shots"] = len(script.get("shots") or [])
    from step01_script_gen.prop_state_anchor import (
        locate_release, precise_state_diff, release_verification)
    for a in anchors:
        _guess = a.get("prop_guess") or ""
        _tok = {_guess}
        _rep = _release_report(script, _tok)
        # 串名检查（用户 2026-09-25 第 2 点）：候选是「酒囊」而稿里写「酒壶已脱手」
        # ——必须**不预设道具名**地找出稿里释放了什么，再与候选名比对，
        # 防止修好一件又带错另一件。
        _released = sorted({n for c in _all_release_clauses(script)
                            for n in _prop_nouns_in(c["clause"])})
        _other = [n for n in _released if n != _guess]
        # 时点定位（v6.127，用户 2026-09-25 复核）：`cause_at`（起因帧）与
        # `release_at`（稿件**实际写出**的释放）**分开记录**，不做"起因帧⇒释放帧"
        # 的推导；对不上就记「未知」——不得据此清 `props`，也没有执行许可字段。
        _anchor = {"prop_guess": _guess, "evidence": a.get("evidence")}
        _loc = locate_release(script, _anchor, None, chapter_text=novel_text)
        _ver = release_verification(script, _anchor, None,
                                    cause_at=(_loc["cause_at"]
                                              if _loc["cause_usable"] else None))
        # 只读差异报告：精确到叶子的残留 + 描述 + 再次拾取/转交（**不自动改**）
        _diff = precise_state_diff(script, _anchor, None, chapter_text=novel_text)
        report.setdefault("per_anchor", []).append(dict(
            a, **_rep,
            release_state=_ver["state"],
            cause_at=_loc["cause_at"], cause_usable=_loc["cause_usable"],
            release_written_at=_loc["release_at"],
            release_known=_loc["release_known"],
            release_reason=_loc["release_reason"] or _loc["reason"],
            source_pair=_loc["source_pair"],
            release_events=[(e["shot_index"], e["frame"], e["relation"])
                            for e in _ver["release_events"]],
            diff={k: _diff[k] for k in (
                "reference", "reference_kind", "timing_state", "residuals",
                "premature_residuals", "descriptions_still_holding",
                "re_pickup", "transfer", "clear_candidates", "advisory_only",
                "blocked_reason", "read_only", "apply")},
            prop_name_match=(_guess in _released),
            released_prop_names=_released,
            released_prop_names_other_than_candidate=_other,
            prop_mentions_in_order=_prop_mentions_in_order(script, _tok)))
    atomic_dump_json(out, script, ensure_ascii=False, indent=2)
    atomic_dump_json(out.with_suffix(".diag.json"), report, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in
                      ("shots", "block_in_prompt", "prompt_chars",
                       "generation_success")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
