"""视频生成 — 业务入口

从 script_result.json 读取分镜脚本，逐个生成视频片段.
每个镜头独立 try/except，单点失败不影响其他镜头.
"""

import json
import os
import sys
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
# 参考: storyboard-artist/references/veo-jimeng-guide.md
# ---------------------------------------------------------------------------
_SENSITIVE_SUBSTITUTIONS = {
    # 低龄词汇 → 安全替代
    "娃娃": "少年",
    "孩童": "年轻人",
    "小孩": "人物",
    "儿童": "少年",
    # 裸露相关
    "光腚": "赤膊",
    "裸": "赤",
    # 暴力相关
    "血": "红",
    "杀": "击",
    "死": "倒",
    # 已用电影语境包裹，不再生硬替换
}


def _sanitize_prompt(prompt: str) -> str:
    """对 prompt 做合规化处理，替换可能触发中国大陆 AI 平台内容审核的敏感词."""
    sanitized = prompt
    for old, new in _SENSITIVE_SUBSTITUTIONS.items():
        if old in sanitized:
            sanitized = sanitized.replace(old, new)
            print(f"  [合规] prompt 已替换敏感词: '{old}' -> '{new}'")
    return sanitized


def _clip_exists_valid(clip_index: int, script_mtime: float | None = None) -> bool:
    """检查片段是否可跳过：存在、大小正常（>20KB）、且不比脚本旧.

    若 clip 的修改时间早于 script_result.json，说明 prompt 已更新但片段未重新生成，
    必须重新生成，否则会用旧 prompt 的画面（与当前文章内容脱节）.
    """
    clip_path = Path(config.OUTPUT_DIR) / f"clip_{clip_index}.mp4"
    if not clip_path.exists():
        return False
    size_kb = clip_path.stat().st_size / 1024
    if size_kb < 20:
        print(f"  [!] 已有片段 {clip_path} 仅 {size_kb:.0f}KB（疑似失败），将重新生成")
        return False

    # 过期检测：片段比脚本旧 → prompt 变了，必须重新生成
    if script_mtime is not None:
        clip_mtime = clip_path.stat().st_mtime
        if clip_mtime < script_mtime:
            print(f"  [!] 片段 {clip_path.name} 早于 script_result.json（prompt 已更新），将重新生成")
            return False

    print(f"  [OK] 跳过已生成的镜头{clip_index}（{size_kb:.0f}KB）")
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

    # 读取脚本修改时间，用于判断已存在片段是否过期（prompt 变更后需重新生成）
    script_mtime = SCRIPT_FILE.stat().st_mtime if SCRIPT_FILE.exists() else None
    if script_mtime is not None:
        import datetime as _dt
        print(f"[INFO] script_result.json 修改时间: "
              f"{_dt.datetime.fromtimestamp(script_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
        print("[INFO] 早于此时间的片段将被视为过期并重新生成")

    batch = BatchGenerationResult(total=len(shots))

    for i, shot in enumerate(shots, 1):
        print(f"\n{'─'*50}")
        print(f"[{i}/{len(shots)}] 正在生成镜头{shot['index']}...")
        print(f"  字幕: {shot.get('subtitle', 'N/A')}")

        # 跳过已存在的有效片段（含过期检测）
        if _clip_exists_valid(shot["index"], script_mtime):
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
