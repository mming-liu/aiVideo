"""只读诊断：某章 clips/clip_<idx>.mp4 缺片的历史环节定位（S09）.

用途：回答「这条镜头为什么没有成片」——是没提交、提交后没有终局、远端失败、
下载失败，还是本地只剩副本缺失。**不发任何网络请求、不改任何文件。**

判据全部来自本机已有材料：
  shots/<idx>/video.mp4            生产视频本体
  clips/clip_<idx>.mp4             step04 合并用副本
  step02.log                      提交/轮询/终局日志（_submit_video 与 _poll_video 写这里）

分类（优先级从上到下）：
  clip_ok              本体与副本都在
  only_clip_missing    本体在、副本缺 —— 可直接本地补拷，不需要重新生成
  remote_failed        日志有「生成失败」/「最终失败」
  download_failed      日志有「下载失败」
  submitted_no_terminal 已提交、有轮询记录，但没有终局行（进程中断/被清理）
  not_submitted        日志里没有该镜的提交记录
  unknown              材料不足

用法：
  python tools/diagnose_video_gaps.py --chapter-dir output/video/<book>/<part>/chapter_001
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _read_log(chapter_dir: Path) -> list[str]:
    for name in ("step02.log", "step02b.log"):
        p = chapter_dir / name
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
    return []


def classify(chapter_dir: Path, idx: int, log_lines: list[str]) -> dict:
    shots = chapter_dir / "vimax_working" / "shots" / str(idx)
    video = shots / "video.mp4"
    clip = chapter_dir / "clips" / f"clip_{idx}.mp4"

    video_ok = video.exists() and video.stat().st_size > 20 * 1024
    clip_ok = clip.exists() and clip.stat().st_size > 20 * 1024

    pat = re.compile(r"\[Shot %d\]" % idx)
    lines = [ln for ln in log_lines if pat.search(ln)]
    submitted = any("Task submitted" in ln for ln in lines)
    polls = [ln for ln in lines if "Processing" in ln]
    failed = any(("生成失败" in ln) or ("最终失败" in ln) for ln in lines)
    dl_failed = any("下载失败" in ln for ln in lines)

    if clip_ok and video_ok:
        kind = "clip_ok"
    elif clip_ok and not video_ok:
        # 副本在、本体缺：下游合并可用，但重跑缓存判据会 miss（本体是缓存依据）。
        kind = "video_missing_clip_ok"
    elif video_ok and not clip_ok:
        kind = "only_clip_missing"
    elif failed:
        kind = "remote_failed"
    elif dl_failed:
        kind = "download_failed"
    elif submitted and polls:
        kind = "submitted_no_terminal"
    elif submitted:
        kind = "submitted_no_poll"
    else:
        kind = "not_submitted"

    return {
        "shot": idx,
        "classification": kind,
        "video_present": video_ok,
        "clip_present": clip_ok,
        "submitted": submitted,
        "poll_count": len(polls),
        "log_entries": len(lines),
        "last_log_line": lines[-1][:160] if lines else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="只读诊断视频缺片环节")
    ap.add_argument("--chapter-dir", required=True, help="chapter_XXX 目录")
    ap.add_argument("--shots", default="", help="只查指定镜头，逗号分隔；默认按 clips 与日志推断")
    ap.add_argument("--json", action="store_true", help="输出 JSON（默认同样输出 JSON）")
    args = ap.parse_args()

    chapter_dir = Path(args.chapter_dir)
    if not chapter_dir.exists():
        raise SystemExit(f"目录不存在: {chapter_dir}")
    log_lines = _read_log(chapter_dir)

    if args.shots.strip():
        idxs = [int(x) for x in args.shots.split(",") if x.strip()]
    else:
        found = set()
        for ln in log_lines:
            m = re.search(r"\[Shot (\d+)\]", ln)
            if m:
                found.add(int(m.group(1)))
        clips_dir = chapter_dir / "clips"
        if clips_dir.exists():
            for p in clips_dir.glob("clip_*.mp4"):
                m = re.search(r"clip_(\d+)\.mp4$", p.name)
                if m:
                    found.add(int(m.group(1)))
        idxs = sorted(found)

    report = [classify(chapter_dir, i, log_lines) for i in idxs]
    print(json.dumps({"chapter_dir": str(chapter_dir), "shots": report},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
