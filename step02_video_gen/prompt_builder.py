"""step02_video_gen.prompt_builder - Prompt 构建模块

功能：
- _build_rompt(): 构建单镜头 prompt
- _make_request_async(): 构建 VideoGenerationRequest
- _find_fallback_reference(): 查找备用参考图
"""
import sys
from pathlib import Path
import os
import json

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import style_prefix
from style_prefix import STYLE_PREFIX, NEGATIVE_PROMPT, ensure_prefix, sanitize_prompt


def _build_prompt(shot: dict, script: dict) -> str:
    """构建单镜头 prompt，motion__desc + subtitle + context_bridge."""
    motion_desc = shot.get("motion_desc", "").strip()
    if not motion_desc:
        motion_desc = shot.get("prompt", "")
    audio_desc = shot.get("subtitle", "").strip()
    prompt = f"{motion_desc}\n{audio_desc}" if audio_desc else motion_desc

    extras = []
    cb = shot.get("context_bridge", "").strip()
    if cb:
        extras.append(f"与上一镜视觉衔接：{cb}")
    if extras:
        prompt = f"{prompt}\n\n{'；'.join(extras)}。"
    return prompt


def _make_request(video_adapter, prompt: str, frame_paths: list, shot: dict, audio_duration: float = 0):
    """构建 VideoGenerationRequest，使用音频实际时长计算视频帧数."""
    from _video_gen import VideoGenerationRequest

    full_prompt = sanitize_prompt(ensure_prefix(prompt, STYLE_PREFIX))
    image_path = None
    for fp in (frame_paths or []):
        if fp and Path(fp).exists():
            image_path = fp
            break
    if not image_path:
        image_path = _find_fallback_reference(
            Path(os.getenv("VIMAX_WORKING_DIR", "./output/vimax_working"))
        )
    # 使用音频实际时长替代 LLM 预估的 duration，保证音画对齐
    duration = max(round(audio_duration), 3) if audio_duration > 0 else shot.get("duration", 5)
    return VideoGenerationRequest(
        prompt=full_prompt,
        clip_index=shot["index"],
        duration=duration,
        negative_prompt=NEGATIVE_PROMPT,
        size="960x1792",
        image_path=image_path,
    )


def _find_fallback_reference(working_dir: Path) -> str | None:
    """从 character_portraits_registry.json 找可用参考图."""
    registry_path = working_dir / "character_portraits_registry.json"
    if not registry_path.exists():
        return None
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        for ident, views in registry.items():
            front = views.get("front", {}).get("path", "")
            if front and Path(front).exists():
                print(f"  [fallback] 使用角色 {ident} 正面肖像")
                return front
    except Exception:
        pass
    return None