"""图片生成（首帧） — 业务入口

从 script_result.json 读取分镜脚本，为每个镜头生成首帧参考图，存到 output/images/.
视频阶段 (02_video_gen) 会以这些首帧图做 image-to-video.
每个镜头独立 try/except，单点失败不影响其他镜头.
"""

import json
import sys
from pathlib import Path

# 项目根目录在 01b_image_gen 上级
_project_root = Path(__file__).resolve().parent

from _image_gen import (
    config,
    create_service,
    ImageGenerationRequest,
    ImageGenerationResult,
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


def _image_exists_valid(shot_index: int, script_mtime: float | None = None) -> bool:
    """检查首帧图是否可跳过：存在、大小正常（>5KB）、且不比脚本旧.

    首帧图对画面锚定强于文字 prompt，一旦与 script_result.json 不同步必须重生成，
    否则即使 prompt 改了，image-to-video 仍由旧首帧决定画面.
    """
    img_path = Path(config.OUTPUT_DIR) / f"shot_{shot_index}.png"
    if not img_path.exists():
        return False
    size_kb = img_path.stat().st_size / 1024
    if size_kb < 5:
        print(f"  [!] 已有首帧 {img_path} 仅 {size_kb:.0f}KB（疑似失败），将重新生成")
        return False

    # 过期检测：首帧图早于 script_result.json → prompt 已更新，必须重新生成
    if script_mtime is not None:
        img_mtime = img_path.stat().st_mtime
        if img_mtime < script_mtime:
            print(f"  [!] 首帧 {img_path.name} 早于 script_result.json（prompt 已更新），将重新生成")
            return False

    print(f"  [OK] 跳过已生成的首帧{shot_index}（{size_kb:.0f}KB）")
    return True


def generate_all(script: dict) -> list:
    """逐个生成首帧图，返回结果列表."""
    service = create_service(config.PROVIDER)
    shots = script.get("shots", [])
    negative = script.get("negative_prompt", "")

    # 确保输出目录存在
    Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # 读取脚本修改时间，用于判断已存在首帧是否过期
    script_mtime = SCRIPT_FILE.stat().st_mtime if SCRIPT_FILE.exists() else None
    if script_mtime is not None:
        import datetime as _dt
        print(f"[INFO] script_result.json 修改时间: "
              f"{_dt.datetime.fromtimestamp(script_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
        print("[INFO] 早于此时间的首帧将被视为过期并重新生成")

    results: list = []
    succeeded = 0
    failed = 0

    for i, shot in enumerate(shots, 1):
        idx = shot["index"]
        print(f"\n{'─'*50}")
        print(f"[{i}/{len(shots)}] 正在生成镜头{idx} 首帧...")

        # 跳过已存在的有效首帧
        if _image_exists_valid(idx, script_mtime):
            results.append(ImageGenerationResult(
                success=True,
                save_path=str(Path(config.OUTPUT_DIR) / f"shot_{idx}.png"),
            ))
            succeeded += 1
            continue

        try:
            req = ImageGenerationRequest(
                prompt=shot["prompt"],
                shot_index=idx,
                negative_prompt=negative,
                size=config.IMAGE_SIZE,
            )
            result = service.generate(req)
        except Exception as e:
            print(f"  [FAIL] 镜头{idx} 首帧生成失败: {e}")
            result = ImageGenerationResult(success=False, error=str(e))

        results.append(result)
        if result.success:
            succeeded += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"首帧生成完毕：总计 {len(shots)} | 成功 {succeeded} | 失败 {failed}")
    print(f"{'='*60}")
    return results


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  首帧图片生成任务启动")
    print("=" * 60)

    # 1. 读取分镜脚本
    script = load_script(SCRIPT_FILE)
    shots = script.get("shots", [])
    print(f"\n分镜脚本: {len(shots)} 个镜头\n")

    # 2. 确认执行
    print(f"[提示] 每张首帧约需 5-30 秒生成，总计预计 {len(shots)*10} 秒左右")
    answer = input("确认开始生成首帧？(y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消。")
        sys.exit(0)

    # 3. 批量生成
    results = generate_all(script)

    # 4. 退出码
    if sum(1 for r in results if not r.success) > 0:
        sys.exit(1)
