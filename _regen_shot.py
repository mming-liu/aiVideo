"""一次性脚本：仅重新生成指定镜头的尾帧/关键帧（验证布局锁修复）。

用法：改 TARGET_INDEX 后运行
    python _regen_shot.py
首帧哈希未变会走缓存；尾帧/关键帧哈希带 _nl 后缀自动重新生成。
"""
import asyncio
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

from shared.vimax_adapters import ImageGeneratorAiVideoAdapter
from shared.paths import script_file, vimax_working_dir
from shared.vimax_bridge import gen_shot_frames

TARGET_INDEX = 1  # 要重新生成的镜头 index


async def main():
    script_path = script_file()
    work_dir = vimax_working_dir()
    shots_dir = work_dir / "shots"

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    characters = script.get("characters", [])
    script_mtime = script_path.stat().st_mtime

    shot = next((s for s in script["shots"] if s["index"] == TARGET_INDEX), None)
    if shot is None:
        print(f"[!] shot index={TARGET_INDEX} not found")
        return

    adapter = ImageGeneratorAiVideoAdapter()
    result = await gen_shot_frames(
        shot, shots_dir, adapter, script_mtime,
        force=False, characters=characters,
    )
    print("ff:", result.get("ff_path"))
    print("lf:", result.get("lf_path"))
    print("kf:", result.get("keyframes_paths"))


if __name__ == "__main__":
    asyncio.run(main())
