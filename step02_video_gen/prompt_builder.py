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
    """构建单镜头 prompt，针对 Agnes AI 优化声音指令.

    策略：
    1. 区分旁白与对话：如果 subtitle 包含冒号或引号，视为对话。
    2. 角色锚点注入：从 script['characters'] 中提取当前 main_subject 的特征，加入 Prompt。
    3. 强制中文指令：使用 （用中文说）"subtitle" + NO English speech.
    """
    motion_desc = shot.get("motion_desc", "").strip()
    if not motion_desc:
        motion_desc = shot.get("prompt", "")

    subtitle = shot.get("subtitle", "").strip()
    main_subject = shot.get("main_subject", "")

    # 1. 获取角色特征（如果存在）
    char_features = ""
    characters = script.get("characters", [])
    for char in characters:
        if char.get("identifier") == main_subject:
            static = char.get("static_features", "")
            dynamic = char.get("dynamic_features", "")
            if static or dynamic:
                char_features = f"[{main_subject}: {static}, {dynamic}]"
            break

    # 2. 构建基础 Prompt
    parts = []
    if char_features:
        parts.append(char_features)  # 角色特征放最前面作为锚点
    parts.append(motion_desc)

    # 3. 处理声音指令
    if subtitle:
        # 判断是否为对话 (简单规则：包含冒号或引号)
        is_dialogue = ":" in subtitle or "：" in subtitle or '"' in subtitle or '"' in subtitle or '"' in subtitle

        if is_dialogue:
            # 对话模式：保留原文格式，Agnes 通常能识别 "Name: Content"
            # 但为了保险，显式添加中文指令
            audio_instruction = f'（用中文说）"{subtitle}"'
        else:
            # 旁白/环境音模式
            audio_instruction = f"（音效/旁白：{subtitle}）"

        parts.append(audio_instruction)
        parts.append("NO English speech.")  # 负面提示

    prompt = "\n".join(parts)

    # 4. 添加视觉衔接
    extras = []
    cb = shot.get("context_bridge", "").strip()
    if cb:
        extras.append(f"视觉衔接：{cb}")
    if extras:
        prompt = f"{prompt}\n\n{'；'.join(extras)}。"

    return prompt


def _make_request(video_adapter, prompt: str, frame_paths: list, shot: dict, audio_duration: float = 0):
    """构建 VideoGenerationRequest，使用音频实际时长计算视频帧数.

    frame_paths: [首帧路径, 尾帧路径(可选)]
    当提供尾帧时，ag-nes 使用关键帧动画模式（keyframes mode）。
    """
    from _video_gen import VideoGenerationRequest

    full_prompt = sanitize_prompt(ensure_prefix(prompt, STYLE_PREFIX), verbose=False)
    image_path = None
    last_frame_image_path = None

    # 第一个是首帧，第二个是尾帧
    for i, fp in enumerate((frame_paths or [])):
        if fp and Path(fp).exists():
            if i == 0:
                image_path = fp
            elif i == 1:
                last_frame_image_path = fp

    # 如果没有首帧，尝试备用参考图
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
        size="720x1280",
        image_path=image_path,
        last_frame_image_path=last_frame_image_path,
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
#                 print(f"  [fallback] 使用角色 {ident} 正面肖像")
                return front
    except Exception:
        pass
    return None
