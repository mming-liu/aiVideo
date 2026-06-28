"""视频生成 — 业务入口

从 script_result.json 读取分镜脚本，逐个生成视频片段.
每个镜头独立 try/except，单点失败不影响其他镜头.
"""

import json
import os
import sys
from pathlib import Path

# 项目根目录在 02_video_gen 上级
_project_root = Path(__file__).resolve().parent

from _video_gen import (
    config,
    create_service,
    VideoGenerationRequest,
    VideoGenerationResult,
    BatchGenerationResult,
)

SCRIPT_FILE = _project_root / "script_result.json"


def load_script(path: Path) -> dict:
    """读取分镜脚本 JSON."""
    if not path.exists():
        raise FileNotFoundError(
            f"分镜脚本不存在: {path}\n请先运行 01_script_gen.py"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_prompt(prefix: str, shot: dict, negative: str) -> VideoGenerationRequest:
    """拼接画面描述为完整 prompt."""
    shot_prompt = shot["prompt"]

    # 如果 prompt 未携带统一前缀，自动追加
    if prefix and prefix.split("，")[0] not in shot_prompt.split("，")[0]:
        full_prompt = f"{prefix}，{shot_prompt}"
    else:
        full_prompt = shot_prompt

    return VideoGenerationRequest(
        clip_index=shot["index"],
        prompt=full_prompt,
        duration=shot.get("duration", config.VIDEO_DURATION),
        negative_prompt=negative,
        size=config.VIDEO_SIZE,
    )


def _clip_exists_valid(clip_index: int) -> bool:
    """检查片段文件是否已存在且大小正常（> 20KB，排除生成失败的文件）."""
    clip_path = Path(config.OUTPUT_DIR) / f"clip_{clip_index}.mp4"
    if not clip_path.exists():
        return False
    size_kb = clip_path.stat().st_size / 1024
    if size_kb < 20:
        print(f"  ⚠ 已有片段 {clip_path} 仅 {size_kb:.0f}KB（疑似失败），将重新生成")
        return False
    print(f"  ✓ 跳过已生成的镜头{clip_index}（{size_kb:.0f}KB）")
    return True


def generate_all(script: dict) -> BatchGenerationResult:
    """逐个生成视频片段，返回汇总结果."""
    service = create_service(config.PROVIDER)
    shots = script.get("shots", [])
    negative = script.get("negative_prompt", "")

    prefix = (
        "古风权谋卧底影视剧，9:16竖屏，电影柔光，暗调低饱和，"
        "烛火局部光影，浅景深缓慢向前推镜，真人写实古装，无动漫，高清1080P"
    )

    # 确保输出目录存在
    Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    batch = BatchGenerationResult(total=len(shots))

    for i, shot in enumerate(shots, 1):
        print(f"\n{'─'*50}")
        print(f"[{i}/{len(shots)}] 正在生成镜头{shot['index']}...")
        print(f"  字幕: {shot.get('subtitle', 'N/A')}")

        # 跳过已存在的有效片段
        if _clip_exists_valid(shot["index"]):
            result = VideoGenerationResult(
                clip_index=shot["index"],
                success=True,
                save_path=str(
                    Path(config.OUTPUT_DIR) / f"clip_{shot['index']}.mp4"
                ),
            )
            batch.results.append(result)
            batch.succeeded += 1
            continue

        try:
            req = build_prompt(prefix, shot, negative)
            task_id = service.create_task(req)
            result = service.wait_for_completion(req, task_id)
        except Exception as e:
            print(f"  [FAIL] 镜头{shot['index']} 生成失败: {e}")
            result = VideoGenerationResult(
                clip_index=shot["index"],
                success=False,
                error=str(e),
            )

        batch.results.append(result)
        if result.success:
            batch.succeeded += 1
        else:
            batch.failed += 1

    return batch


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("="*60)
    print("  视频生成任务启动")
    print("="*60)

    # 1. 读取分镜脚本
    script = load_script(SCRIPT_FILE)
    shots = script.get("shots", [])
    narration = script.get("narration", "")
    print(f"\n分镜脚本: {len(shots)} 个镜头")
    print(f"旁白({len(narration)}字): {narration[:60]}...\n")

    # 2. 确认执行
    print(f"[提示] 每个镜头约需 1-5 分钟生成，总计预计 {len(shots)*2}- {len(shots)*5} 分钟")
    answer = input("确认开始生成？(y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消。")
        sys.exit(0)

    # 3. 批量生成
    batch = generate_all(script)
    print(batch.summary())

    # 4. 退出码
    if batch.failed > 0:
        sys.exit(1)
