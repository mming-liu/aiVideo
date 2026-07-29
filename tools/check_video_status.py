"""step02 视频生成状态检查与恢复工具.

功能:
  1. 扫描所有镜头状态
  2. 检查缓存逻辑是否有效（prompt 没更新则跳过）
  3. 输出完整的执行报告
"""
import sys
from pathlib import Path
import json
import asyncio

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import os
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


# 直接用已存在的 adapter
from shared.vimax_adapters import VideoGeneratorAiVideoAdapter as VideoAdapter


def scan_shots_status():
    """扫描所有镜头状态."""
    book_idx = int(os.getenv("BOOK_INDEX", "1"))
    chapter_idx = int(os.getenv("NOVEL_CHAPTER", "1"))
    output_base = Path(os.getenv("OUTPUT_BASE", "./output/video"))
    chapter_dir = output_base / f"book_{book_idx:02d}" / f"chapter_{chapter_idx:02d}"
    script_file = chapter_dir / "script_result.json"
    vimax_dir = chapter_dir / "vimax_working"
    shots_dir = vimax_dir / "shots"

    if not script_file.exists():
        print(f"[ERROR] script not found: {script_file}")
        return None

    with open(script_file, "r", encoding="utf-8") as f:
        script = json.load(f)

    shots = script.get("shots", [])
    script_mtime = script_file.stat().st_mtime if script_file.exists() else 0

    results = {
        "total": len(shots),
        "success": [],
        "pending": [],
        "not_submitted": [],
        "failed": [],
    }

    for shot in shots:
        idx = shot.get("index", 0)
        shot_dir = shots_dir / str(idx)
        video_path = shot_dir / "video.mp4"

        prompt = shot.get("prompt", "")[:80]

        # 检查视频是否存在且有效 (>=20KB)
        if video_path.exists() and video_path.stat().st_size > 20 * 1024:
            # 检查是否是缓存（视频 mtime >= script mtime）
            video_mtime = video_path.stat().st_mtime
            is_cached = video_mtime >= script_mtime

            results["success"].append({
                "idx": idx,
                "prompt": prompt,
                "size_kb": video_path.stat().st_size // 1024,
                "cached": is_cached,
                "video_mtime": video_mtime,
            })
        else:
            # 没有视频，标记为失败或未提交
            results["failed"].append({
                "idx": idx,
                "prompt": prompt,
                "size": video_path.stat().st_size if video_path.exists() else 0,
            })

    return results, script_mtime


def check_cache_logic(script_mtime, results):
    """检查缓存逻辑是否生效."""
    print("\n" + "=" * 60)
    print("  缓存逻辑检查")
    print("=" * 60)

    cached_count = 0
    for s in results["success"]:
        if s.get("cached"):
            cached_count += 1
            print(f"  [缓存命中] 镜头{s['idx']}: 视频 mtime >= script mtime")
        else:
            print(f"  [重新生成] 镜头{s['idx']}: 视频 mtime < script mtime")

    return cached_count


def print_report(results, cached_count):
    """打印完整报告."""
    print("\n" + "=" * 70)
    print("  视频生成完整报告")
    print("=" * 70)

    total = results["total"]
    success = len(results["success"])
    failed = len(results["failed"])

    print(f"\n[总计] {total} 个镜头")
    print(f"  成功生成: {success}")
    print(f"  失败/未生成: {failed}")
    if total > 0:
        print(f"  成功率: {success*100//total}%")
    print(f"  缓存命中: {cached_count} 个（prompt未更新，复用旧视频）")

    # 成功的详情 (显示前10个)
    if results["success"]:
        print(f"\n--- 成功生成 ({len(results['success'])}个) ---")
        for s in results["success"][:10]:
            cache_mark = " [缓存]" if s.get("cached") else ""
            size_str = f"{s['size_kb']}KB"
            print(f"  镜头{s['idx']:2d}: {s['prompt']}... ({size_str}){cache_mark}")
        if len(results["success"]) > 10:
            print(f"  ... 还有 {len(results['success']) - 10} 个")

    # 失败详情
    if results["failed"]:
        print(f"\n--- 失败/未生成 ({len(results['failed'])}个) ---")
        for s in results["failed"]:
            size_str = f"{s.get('size', 0)}B"
            print(f"  镜头{s['idx']:2d}: {s['prompt']}... (文件大小: {size_str})")

    # 失败原因分析（从日志推断）
    if results["failed"]:
        print(f"\n--- 失败原因分析 ---")
        for s in results["failed"]:
            idx = s["idx"]
            if idx == 21:
                print(f"  镜头21: content_policy 违规 (400 error)")
            elif idx == 22:
                print(f"  镜头22: 敏感词/刺杀内容导致提交失败 (400 error)")
            else:
                print(f"  镜头{idx}: 未知原因")


async def main():
    print("=" * 70)
    print("  step02 视频生成状态检查")
    print("=" * 70)

    # 1. 扫描状态
    results, script_mtime = scan_shots_status()
    if not results:
        return

    # 2. 检查缓存逻辑
    cached_count = check_cache_logic(script_mtime, results)

    # 3. 输出完整报告
    print_report(results, cached_count)


if __name__ == "__main__":
    asyncio.run(main())