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
from style_prefix import (
    STYLE_PREFIX, NEGATIVE_PROMPT, ensure_prefix, sanitize_prompt,
    is_empty_scene,
)

# 是否允许视频模型自带语音（默认关闭）：
# 模型自带配音不可控——会随机生成英文语音（镜头11等多镜实测），且与 TTS
# 双声重叠。默认让模型只生成环境音效，全部语音（旁白+对话）由 step03 TTS 提供。
# 设 VIDEO_MODEL_VOICE=1 可恢复旧行为（模型自带中文配音）。
_MODEL_VOICE = os.getenv("VIDEO_MODEL_VOICE", "0").strip() == "1"


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

    # 1.5 空镜检测：main_subject 不是角色、画面描述未提到角色名、也无泛人物字眼
    # （否则"旁白"语音指令会诱导模型在纯景物镜头里生成一个说话的人）
    is_character_shot = not is_empty_scene(shot, characters)

    # 2. 构建基础 Prompt
    parts = []
    if char_features:
        parts.append(char_features)  # 角色特征放最前面作为锚点
    parts.append(motion_desc)

    # 3. 处理声音指令
    if subtitle:
        # 判断是否为对话 (简单规则：包含冒号或引号)
        is_dialogue = ":" in subtitle or "：" in subtitle or '"' in subtitle or '"' in subtitle or '"' in subtitle

        if _MODEL_VOICE:
            # 旧行为：模型自带中文配音（不可控，可能随机出英文）
            if is_dialogue:
                parts.append(f'（用中文普通话说）"{subtitle}"')
            else:
                parts.append(f"（中文普通话画外音旁白，仅声音：{subtitle}）")
            parts.append("所有语音必须为中文普通话，禁止任何英文语音。")
        else:
            # 默认：视频只生成环境音效，语音全部由 TTS 叠加——从源头杜绝英文配音
            if is_dialogue and is_character_shot:
                parts.append("角色可有开口说话的口型与神态，但不发出人声。")
            parts.append(
                "音频只保留贴合画面的环境音效（风声、水声、虫鸣、衣料与器物声等），"
                "禁止任何人声、旁白、对话、歌声。"
            )

    # 4. 空镜约束：无角色镜头明确禁止人物出现（加强版）
    if not is_character_shot:
        parts.append("空镜头，画面中严禁出现任何人物、身影、人影、轮廓或人形。只拍摄自然景物特写。")

    # 4.5 风格收束（放在末尾对抗长 prompt 注意力衰减——风格漂移多发于镜头后半段）必须明确：写实古风真人，禁止任何动画风格
    parts.append(
        "整段视频每一帧都保持写实古风真人风格，首尾画风完全一致，禁止中途转为动漫、动画、2D渲染、卡通脸或任何非写实风格。"
    )

    prompt = "\n".join(parts)

    # 5. 添加视觉衔接
    extras = []
    cb = shot.get("context_bridge", "").strip()
    if cb:
        extras.append(f"视觉衔接：{cb}")
    if extras:
        prompt = f"{prompt}\n\n{'；'.join(extras)}。"

    return prompt


def _make_request(video_adapter, prompt: str, frame_paths: list, shot: dict, audio_duration: float = 0,
                  characters: list | None = None):
    """构建 VideoGenerationRequest，使用音频实际时长计算视频帧数.

    frame_paths: [首帧路径, 尾帧路径(可选)]
    当提供尾帧时，ag-nes 使用关键帧动画模式（keyframes mode）。
    """
    from _video_gen import VideoGenerationRequest

    full_prompt = sanitize_prompt(ensure_prefix(prompt, STYLE_PREFIX), verbose=False)
    # 静音人声模式下，负面提示词同步追加人声类条目（双保险）
    negative = NEGATIVE_PROMPT if _MODEL_VOICE \
        else NEGATIVE_PROMPT + "、人声、旁白配音、对话语音、歌声"
    # 物理逻辑约束：防止违反常识的场景（车马上船、水上行走等）
    negative += "、车马在水中行驶、马车在水上跑、人在水上走、物体违反重力"
    image_path = None
    last_frame_image_path = None
    extra_keyframe_paths = []

    # 第一个是首帧，第二个是尾帧，第三个及以后是多关键帧
    for i, fp in enumerate((frame_paths or [])):
        if fp and Path(fp).exists():
            if i == 0:
                image_path = fp
            elif i == 1:
                last_frame_image_path = fp
            elif i >= 2:
                extra_keyframe_paths.append(fp)

    # 如果没有首帧，尝试备用参考图（仅限镜头里实际出现的角色，空镜绝不注入肖像）
    if not image_path:
        from shared.paths import vimax_working_dir
        image_path = _find_fallback_reference(vimax_working_dir(), shot, characters)

    # 使用音频实际时长替代 LLM 预估的 duration，保证音画对齐
    duration = max(round(audio_duration), 3) if audio_duration > 0 else shot.get("duration", 5)

    # 多关键帧优先：3+ 帧时使用多关键帧模式，2帧时使用首尾双锚定
    if extra_keyframe_paths:
        extra_keyframe_paths = extra_keyframe_paths  # 使用多关键帧
    elif image_path and last_frame_image_path:
        extra_keyframe_paths = []  # 使用首尾双锚定
    else:
        extra_keyframe_paths = []

    return VideoGenerationRequest(
        prompt=full_prompt,
        clip_index=shot["index"],
        duration=duration,
        negative_prompt=negative,
        size="720x1280",
        image_path=image_path,
        last_frame_image_path=last_frame_image_path,
        extra_keyframe_paths=extra_keyframe_paths,
    )


def _find_fallback_reference(working_dir: Path, shot: dict | None = None,
                             characters: list | None = None) -> str | None:
    """从 character_portraits_registry.json 找可用参考图.

    只返回镜头画面里实际出现的角色的肖像：备用参考图会成为视频的首个
    关键帧（image-to-video），若不加限制会把注册表第一个角色（通常是
    主角）的肖像塞进所有首帧缺失的镜头，导致与主角无关的片段开头出现
    主角画面。空镜（is_empty_scene）一律不注入肖像，降级 text-to-video。
    """
    if shot is None:
        return None
    if is_empty_scene(shot, characters or []):
        return None

    # 镜头文本：只看本镜头自身的画面描述（不含 context_bridge，见 is_empty_scene 注释）
    search_text = " ".join([
        shot.get("main_subject", ""),
        shot.get("motion_desc", "").strip() or shot.get("prompt", ""),
        shot.get("ff_desc", ""),
    ])

    registry_path = working_dir / "character_portraits_registry.json"
    if not registry_path.exists():
        return None
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        for ident, views in registry.items():
            if not ident or ident not in search_text:
                continue
            front = views.get("front", {}).get("path", "")
            if front and Path(front).exists():
                print(f"  [fallback] 镜头{shot.get('index', '?')} 首帧缺失，使用角色 {ident} 正面肖像")
                return front
    except Exception:
        pass
    return None
