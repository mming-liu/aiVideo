"""视频生成 — 业务入口

从 script_result.json 读取分镜脚本，逐个生成视频片段.
每个镜头独立 try/except，单点失败不影响其他镜头.
"""

import json
import os
import sys
import time
from pathlib import Path

# 项目根目录在 02_video_gen 上级
from dotenv import load_dotenv

load_dotenv()
_project_root = Path(__file__).resolve().parent

from _video_gen import (
    config,
    create_service,
    VideoGenerationRequest,
    VideoGenerationResult,
    BatchGenerationResult,
)
from style_prefix import STYLE_PREFIX, sanitize_prompt as _sanitize_prompt

SCRIPT_FILE = _project_root / "script_result.json"
# 首帧图目录（与 _image_gen/config.py 的 IMAGE_OUTPUT_DIR 保持一致）
_IMAGE_DIR = _project_root / "output" / "images"


def load_script(path: Path) -> dict:
    """读取分镜脚本 JSON."""
    if not path.exists():
        raise FileNotFoundError(
            f"分镜脚本不存在: {path}\n请先运行 01_script_gen.py"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_prompt(prefix: str, shot: dict, negative: str) -> VideoGenerationRequest:
    """拼接画面描述为完整 prompt，含合规化处理与字幕追加."""
    shot_prompt = shot["prompt"]

    # 如果 prompt 未携带统一前缀，自动追加
    if prefix and prefix.split("，")[0] not in shot_prompt.split("，")[0]:
        full_prompt = f"{prefix}，{shot_prompt}"
    else:
        full_prompt = shot_prompt

    # 合规化处理：替换可能触发内容审核的敏感词
    full_prompt = _sanitize_prompt(full_prompt)

    # 将字幕文本追加到 prompt，让视频生成模型了解即将显示在画面上的文字
    subtitle = shot.get("subtitle", "").strip()
    if subtitle:
        full_prompt = f"{full_prompt}。本镜头画面中的字幕文字：「{subtitle}」"

    # 查找首帧图（由 01b_image_gen.py 生成）；存在则做 image-to-video
    image_path = _find_first_frame(shot.get("index"))

    return VideoGenerationRequest(
        clip_index=shot["index"],
        prompt=full_prompt,
        duration=shot.get("duration", config.VIDEO_DURATION),
        negative_prompt=negative,
        size=config.VIDEO_SIZE,
        image_path=image_path,
    )


def _find_first_frame(shot_index) -> str | None:
    """查找 output/images/shot_{index}.png 首帧图.

    存在且有效（>5KB）则返回路径字符串，否则返回 None（降级为 text-to-video）.
    """
    if shot_index is None:
        return None
    img_path = Path(_IMAGE_DIR) / f"shot_{shot_index}.png"
    if not img_path.exists():
        return None
    if img_path.stat().st_size < 5 * 1024:
        print(f"  [!] 首帧图 {img_path} 过小（疑似失败），忽略，按 text-to-video 生成")
        return None
    print(f"  [OK] 使用首帧图做 image-to-video: {img_path}")
    return str(img_path)


# ---------------------------------------------------------------------------
# 合规化处理：中国大陆平台（DashScope/即梦）内容审核规避
# 实现见 style_prefix.py（运行时单一来源，vimax_adapters 复用）
# ---------------------------------------------------------------------------


def _clip_exists_valid(
    clip_index: int,
    script_mtime: float | None = None,
    source_mtime: float | None = None,
) -> bool:
    """检查片段是否可跳过：存在、大小正常（>20KB）、且不比脚本/源码旧.

    陈旧基准取 max(script_result.json mtime, 02_video_gen.py mtime)：
      - script_result.json 变了 → prompt 内容变了
      - 02_video_gen.py 变了 → 硬编码 prefix 或 build_prompt 逻辑变了
    任一基准晚于 clip 生成时间，都必须重新生成，否则画面与最新 prompt 脱节.
    """
    clip_path = Path(config.OUTPUT_DIR) / f"clip_{clip_index}.mp4"
    if not clip_path.exists():
        return False
    size_kb = clip_path.stat().st_size / 1024
    if size_kb < 20:
        print(f"  [!] 已有片段 {clip_path} 仅 {size_kb:.0f}KB（疑似失败），将重新生成")
        return False

    # 过期检测：clip 早于任一基准 → 视为陈旧
    baseline = max(filter(lambda t: t is not None, [script_mtime, source_mtime]), default=None)
    if baseline is not None:
        clip_mtime = clip_path.stat().st_mtime
        if clip_mtime < baseline:
            reasons = []
            if script_mtime is not None and clip_mtime < script_mtime:
                reasons.append("script_result.json")
            if source_mtime is not None and clip_mtime < source_mtime:
                reasons.append("02_video_gen.py（含 prefix）")
            print(f"  [!] 片段 {clip_path.name} 早于 {'/'.join(reasons)}（prompt 已更新），将重新生成")
            return False

    print(f"  [OK] 跳过已生成的镜头{clip_index}（{size_kb:.0f}KB）")
    return True


def generate_all(script: dict) -> BatchGenerationResult:
    """逐个生成视频片段，返回汇总结果."""
    service = create_service(config.PROVIDER)
    shots = script.get("shots", [])
    negative = script.get("negative_prompt", "")

    # 画面统一前缀：来自 style_prefix.STYLE_PREFIX（单一来源，与 01/skill_spy/适配器一致）
    prefix = STYLE_PREFIX

    # 确保输出目录存在
    Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # 读取脚本修改时间，用于判断已存在片段是否过期（prompt 变更后需重新生成）
    script_mtime = SCRIPT_FILE.stat().st_mtime if SCRIPT_FILE.exists() else None
    # 02 自身源码 mtime：捕获 prefix / build_prompt 逻辑改动（这些不在 json 里）
    source_mtime = Path(__file__).resolve().stat().st_mtime
    import datetime as _dt
    if script_mtime is not None:
        print(f"[INFO] script_result.json 修改时间: "
              f"{_dt.datetime.fromtimestamp(script_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[INFO] 02_video_gen.py 修改时间: "
          f"{_dt.datetime.fromtimestamp(source_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
    print("[INFO] 早于以上任一时间的片段将被视为过期并重新生成")

    batch = BatchGenerationResult(total=len(shots))

    for i, shot in enumerate(shots, 1):
        print(f"\n{'─'*50}")
        print(f"[{i}/{len(shots)}] 正在生成镜头{shot['index']}...")
        print(f"  字幕: {shot.get('subtitle', 'N/A')}")

        # 跳过已存在的有效片段（含过期检测）
        if _clip_exists_valid(shot["index"], script_mtime, source_mtime):
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

        # 镜头间间隔：控制调用频率，避免触发 API 速率限制
        if i < len(shots):
            interval = float(os.getenv("VIDEO_CLIP_INTERVAL", "65"))
            print(f"  [等待] {interval:.0f}s 后处理下一个镜头...")
            time.sleep(interval)

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

    # 2. 确认执行（可通过环境变量 VIDEO_AUTO_CONFIRM=1 跳过）
    auto_confirm = os.getenv("VIDEO_AUTO_CONFIRM", "").strip() == "1"
    print(f"[提示] 每个镜头约需 1-5 分钟生成，总计预计 {len(shots)*2}- {len(shots)*5} 分钟")
    if auto_confirm:
        answer = "y"
        print("确认开始生成？(y/N): y（VIDEO_AUTO_CONFIRM=1 自动确认）")
    else:
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
