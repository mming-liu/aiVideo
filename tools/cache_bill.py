# -*- coding: utf-8 -*-
"""S05：新冻结请求账单（只读，2026-11-17）.

汇总口径：
- 就绪子集 = 本次 plan-only 实际 ready 的镜头（逐镜 key/时长/参考帧）；
- 全章需求 = 13 镜（缺帧镜头单列 blocked，不折算成生成次数）；
- 缓存命中 / 待生成分列；生产扫描无视频即 absent；
- 自动重试配置与人工批准上限分列；
- 保护快照含章级正式产物、锁图、日志与 global/voice_registry.json。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.enhancement_io import sha256_file
from shared.paths import chapter_dir as _chapter_dir


def _load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _plan_entry(plan_path: Path, shot: int) -> dict:
    plan = _load(plan_path)
    for entry in plan.get("shots") or []:
        if int(entry.get("shot_index") or -1) == int(shot):
            return entry
    raise ValueError("plan 中没有 shot %s: %s" % (shot, plan_path))


def _ready_row(entry: dict) -> dict:
    req = entry.get("request") or {}
    refs = req.get("references") or []
    return {
        "shot": int(entry.get("shot_index")),
        "verdict": entry.get("verdict"),
        "request_key": entry.get("request_key"),
        "media_mode": req.get("media_mode"),
        "duration_sec": (req.get("parameters") or {}).get("duration"),
        "references": len(refs),
        "roles": [r.get("role") for r in refs],
        "generations": int(entry.get("generations") or 0),
    }


def _protection(chapter: Path, global_dir: Path) -> dict:
    chapter = Path(chapter)
    items = []
    for p in sorted(chapter.glob("*.json")) + sorted(chapter.glob("*.log")):
        if p.is_file():
            items.append({"path": str(p), "sha256": sha256_file(p)})
    shots = chapter / "vimax_working" / "shots"
    locks = []
    if shots.is_dir():
        for p in sorted(shots.rglob(".lock_frame*")):
            locks.append(str(p))
    voice = Path(global_dir) / "voice_registry.json"
    return {
        "chapter_files": items,
        "lock_files": locks,
        "voice_registry": ({"path": str(voice), "sha256": sha256_file(voice)}
                           if voice.is_file() else None),
        "audio_files": len(list((chapter / "audio").glob("*"))) if (chapter / "audio").is_dir() else 0,
        "clips": len(list((chapter / "clips").glob("*.mp4"))) if (chapter / "clips").is_dir() else 0,
    }


def build_bill(*, chapter: Path, global_dir: Path, shot1_plan: Path, shot2_plan: Path,
               frame_table: Path, replay: dict | None = None) -> dict:
    table = _load(frame_table)
    by_shot = {}
    for row in table.get("rows") or []:
        by_shot.setdefault(int(row.get("shot") or 0), []).append(row)
    shots_dir = Path(chapter) / "vimax_working" / "shots"

    shot1 = _ready_row(_plan_entry(shot1_plan, 1))
    shot2 = _ready_row(_plan_entry(shot2_plan, 2))
    ready = [r for r in (shot1, shot2) if r["verdict"] == "ready"]

    per_shot = []
    for idx in range(1, 14):
        rows = by_shot.get(idx) or []
        if not rows or any(r.get("status") == "shot_dir_missing" for r in rows):
            frame_state = "shot_dir_missing"
        elif any(r.get("status") == "missing" for r in rows):
            frame_state = "frames_missing"
        else:
            frame_state = "present"
        shot_dir = shots_dir / str(idx)
        cache = {
            "video": (shot_dir / "video.mp4").is_file(),
            "prompt_hash": (shot_dir / "prompt_hash.txt").is_file(),
            "frozen_snapshot": (shot_dir / "frozen_request.json").is_file(),
        }
        relation = ("absent" if not cache["video"]
                    else ("exact" if cache["frozen_snapshot"] else "legacy"))
        per_shot.append({"shot": idx, "frames": frame_state, "cache": cache,
                         "cache_relation": relation})

    blocked = [p for p in per_shot if p["frames"] != "present"]
    pending = [p["shot"] for p in per_shot if p["cache_relation"] == "absent"]
    auto_retries = None
    try:
        from shared.config import VIDEO_MAX_RETRIES
        auto_retries = int(VIDEO_MAX_RETRIES)
    except Exception:
        pass
    return {
        "schema": "cache_bill.v1",
        "chapter": str(chapter),
        "ready_subset": ready,
        "ready_generations": sum(r["generations"] for r in ready),
        "ready_duration_sec": sum(int(r["duration_sec"] or 0) for r in ready),
        "ready_references": sum(r["references"] for r in ready),
        "full_chapter": {"shots": 13, "ready": len(ready),
                         "blocked": len(blocked),
                         "blocked_shots": [p["shot"] for p in blocked]},
        "per_shot": per_shot,
        "cache": {"hits": 0, "pending": pending,
                  "controlled_replay": replay or {}},
        "billing": {"provider": "volcengine",
                    "model": "doubao-seedance-1-0-pro-250528",
                    "unit": "per generation (duration/reference count as billed by provider)",
                    "price": "pending_provider_quote",
                    "automatic_retries": auto_retries,
                    "human_approved_attempt_cap": 1,
                    "note": "VIDEO_MAX_RETRIES 是自动重试配置，不是调用授权；人工批准上限需 V02 单独确认"},
        "protection": _protection(chapter, global_dir),
        "model_sends": 0,
    }


def _md(bill: dict) -> str:
    lines = ["# 新冻结请求账单（S05）", "",
             "## 就绪子集", "",
             "| 镜 | 策略 | 时长 | 参考帧 | request_key(前12) |",
             "|---|---|---|---|---|"]
    for r in bill["ready_subset"]:
        lines.append("| {s} | {m} | {d}s | {r} | {k} |".format(
            s=r["shot"], m=r["media_mode"], d=r["duration_sec"], r=r["references"],
            k=(r["request_key"] or "")[:12]))
    lines += ["", "合计：%d 次生成 / %ds / %d 张参考帧。" % (
        bill["ready_generations"], bill["ready_duration_sec"], bill["ready_references"]),
              "", "## 全章", "",
              "13 镜；ready=%d；blocked=%d（%s）。" % (
                  bill["full_chapter"]["ready"], bill["full_chapter"]["blocked"],
                  ",".join(map(str, bill["full_chapter"]["blocked_shots"]))),
              "", "## 缓存", "",
              "生产命中 %d；待生成 %s；受控回放 %s。" % (
                  bill["cache"]["hits"], bill["cache"]["pending"],
                  json.dumps(bill["cache"]["controlled_replay"], ensure_ascii=False)),
              "", "## 计费口径", "",
              "- provider/model：`%s` / `%s`" % (bill["billing"]["provider"], bill["billing"]["model"]),
              "- 单价：%s（未在本仓声明，不编造金额）" % bill["billing"]["price"],
              "- 自动重试：%s；人工批准尝试上限：%s" % (
                  bill["billing"]["automatic_retries"], bill["billing"]["human_approved_attempt_cap"]),
              "", "## 保护快照", "",
              "- 章级文件 %d 个；锁图 %d 个；audio 文件 %d；clips %d；voice_registry=%s" % (
                  len(bill["protection"]["chapter_files"]), len(bill["protection"]["lock_files"]),
                  bill["protection"]["audio_files"], bill["protection"]["clips"],
                  (bill["protection"]["voice_registry"] or {}).get("sha256", "-")[:12])]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="S05 新冻结请求账单（只读）")
    parser.add_argument("--chapter-dir", default=None)
    parser.add_argument("--global-dir", default=None)
    parser.add_argument("--shot1-plan", required=True)
    parser.add_argument("--shot2-plan", required=True)
    parser.add_argument("--frame-table", required=True)
    parser.add_argument("--replay-json", default=None)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    chapter = Path(args.chapter_dir) if args.chapter_dir else _chapter_dir()
    global_dir = (Path(args.global_dir) if args.global_dir
                  else chapter.parent.parent / "global")
    replay = _load(Path(args.replay_json)) if args.replay_json else {}
    bill = build_bill(chapter=chapter, global_dir=global_dir,
                      shot1_plan=Path(args.shot1_plan), shot2_plan=Path(args.shot2_plan),
                      frame_table=Path(args.frame_table), replay=replay)
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bill, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_md:
        Path(args.out_md).write_text(_md(bill), encoding="utf-8")
    print(json.dumps({"ok": True, "ready_generations": bill["ready_generations"],
                      "ready_duration_sec": bill["ready_duration_sec"],
                      "blocked": bill["full_chapter"]["blocked"],
                      "json": str(out), "model_sends": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
