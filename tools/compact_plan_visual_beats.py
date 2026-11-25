# -*- coding: utf-8 -*-
"""紧凑节拍（S27/S30）：把连续的画面节拍合并，让一镜承载多个画面节拍.

只做**确定性关系重映射**，不改事实/证据/事件/台词：

- 仅合并连续的 `voice_ref.kind == "visual"` 节拍；
- 合并正文、目的、原文依据；保留首节的 uid，或按 `--new-uid` 规则生成；
- 所有绑定到旧节拍的事实/钩子载体映射到合并后的节拍；
- 通过 `compile_reallocated_plan()` 投影、重派生编号并重验；失败返回基线不变。

它不是自动“合镜”器：不猜引用，不按相似度合并，也不合并含声音的节拍。调用方拿到的
新计划仍需独立内容核验（`content_verification=not_run`）。
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def merge_visual_beats(view: dict, *, group_size: int = 2) -> tuple:
    """返回 `(candidate_view, old_to_new_mapping, changes)`；view 不被原地修改."""
    from shared.presentation_contract import view_admission_problems
    candidate = copy.deepcopy(view)
    beats = list(candidate.get("beats") or [])
    if group_size < 2:
        raise ValueError("group_size 必须 >= 2")
    if view_admission_problems(view):
        raise ValueError("基线视图不合法")

    old_to_new = {}
    out = []
    i = 0
    merged = 0
    while i < len(beats):
        run = []
        j = i
        while j < len(beats) and str((beats[j].get("voice_ref") or {}).get("kind")) == "visual":
            run.append(beats[j])
            j += 1
        if not run:
            out.append(beats[i])
            i += 1
            continue
        if len(run) >= group_size:
            for start in range(0, len(run), group_size):
                chunk = run[start:start + group_size]
                if len(chunk) == 1:
                    out.append(chunk[0])
                    continue
                first = chunk[0]
                uid = str(first.get("uid"))
                merged_beat = {
                    "uid": uid,
                    "event_uids": list(first.get("event_uids") or []),
                    "text": "；".join(str(b.get("text") or "").strip("；") for b in chunk
                                      if str(b.get("text") or "").strip()),
                    "purpose": "；".join(str(b.get("purpose") or "").strip("；") for b in chunk
                                         if str(b.get("purpose") or "").strip()),
                    "evidence": "；".join(str(b.get("evidence") or "").strip("；") for b in chunk
                                          if str(b.get("evidence") or "").strip()),
                    "source": copy.deepcopy(first.get("source") or {}),
                    "voice_ref": {"kind": "visual", "uid": uid},
                }
                for b in chunk:
                    old_to_new[str(b.get("uid"))] = uid
                out.append(merged_beat)
                merged += 1
            i = j
            continue
        out.extend(run)
        i = j or i + 1
    candidate["beats"] = out

    def _remap(carriers):
        seen, rows = set(), []
        for c in carriers or []:
            c2 = dict(c)
            uid = str(c2.get("uid") or "")
            if uid in old_to_new:
                c2["uid"] = old_to_new[uid]
            key = (str(c2.get("kind") or ""), str(c2.get("uid") or ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append(c2)
        return rows

    for binding in candidate.get("fact_bindings") or []:
        binding["carriers"] = _remap(binding.get("carriers"))
    hook = candidate.get("hook_bindings")
    if isinstance(hook, dict):
        hook["carriers"] = _remap(hook.get("carriers"))
    return candidate, old_to_new, {
        "beats_before": len(beats), "beats_after": len(out),
        "merged_chunks": merged, "mapped_uids": old_to_new,
    }


def speaker_map_for_view(view: dict, plan: dict) -> dict[str, str]:
    """只保留候选视图仍有声音单元的说话人；旧计划的孤立 uid 不得带入."""
    present: set[str] = set()
    for key in ("dialogue_units", "narration_units"):
        for unit in view.get(key) or []:
            if isinstance(unit, dict):
                uid = str(unit.get("unit_uid") or unit.get("uid") or "")
                if uid:
                    present.add(uid)
    return {
        str(u.get("unit_uid")): str(u.get("speaker") or "")
        for u in (plan.get("voice_units") or [])
        if str(u.get("unit_uid") or "") in present
        and str(u.get("source_kind") or "") == "novel_speech"
        and str(u.get("speaker") or "")
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--novel", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--group-size", type=int, default=2)
    args = ap.parse_args()
    from step01_script_gen.lightweight_plan import compile_reallocated_plan
    from shared.presentation_contract import view_from_plan
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))
    novel = args.novel.read_text(encoding="utf-8-sig")
    view, bad = view_from_plan(copy.deepcopy(plan))
    if bad:
        raise SystemExit("基线计划视图不合法：%s" % bad[:3])
    candidate_view, mapping, meta = merge_visual_beats(view, group_size=args.group_size)
    # 只声明候选视图**实际还有**的声音单元的说话人；合并/去重可能删掉旧 uid，
    # 从原计划整表带入会触发 `realloc_speaker_invalid`（ch1 实测 d07）。
    candidate_view["voice_speakers"] = speaker_map_for_view(candidate_view, plan)
    candidate, problems, changes = compile_reallocated_plan(
        plan, candidate_view, novel_text=novel)
    payload = {"accepted": candidate is not None, "meta": meta,
               "mapping": mapping, "problems": problems, "changes": changes}
    if candidate is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(candidate, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        payload["out"] = str(args.out)
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0 if candidate is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
