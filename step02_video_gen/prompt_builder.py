"""step02_video_gen.prompt_builder - Prompt 构建模块

功能：
- _build_prompt(): 构建单镜头 prompt（派生自首尾帧描述，确保图视一致性）
- _make_request(): 构建 VideoGenerationRequest（支持连续接续/多关键帧模式）
- _find_fallback_reference(): 查找备用参考图
"""
from __future__ import annotations
import sys
from pathlib import Path
import os
import json

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import style_prefix
from style_prefix import (
    STYLE_PREFIX, NEGATIVE_PROMPT, ensure_prefix, sanitize_prompt,
    is_empty_scene,
)
from shared.style_cinematic import get_cinematic_prefix, get_director_negative
from shared.config import CINEMATIC_STYLE

# 是否允许视频模型自带语音（默认关闭）：
# 模型自带配音不可控——会随机生成英文语音（镜头11等多镜实测），且与 TTS
# 双声重叠。默认让模型只生成环境音效，全部语音（旁白+对话）由 step03 TTS 提供。
# 设 VIDEO_MODEL_VOICE=1 可恢复旧行为（模型自带中文配音）。
_MODEL_VOICE = os.getenv("VIDEO_MODEL_VOICE", "0").strip() == "1"

# 参考图模式: "image" = 图生视频（需帧图片），"text" = 纯文生视频（默认）
_REFERENCE_MODE = os.getenv("VIDEO_REFERENCE_MODE", "text").strip().lower()
_USE_TEXT_TO_VIDEO = _REFERENCE_MODE == "text"


def _extract_characters_from_desc(desc: str, characters: list) -> list:
    """从描述中提取出现的角色名字列表。"""
    if not desc:
        return []
    found = []
    for char in characters:
        ident = char.get("identifier", "")
        if ident and ident in desc:
            found.append(ident)
    return found


def _build_prompt(shot: dict, script: dict, mode: str = "A") -> str:
    """构建单镜头 prompt，派生自首尾帧描述，确保图片与视频一致性。

    策略（方案A - 派生）：
    1. 视频首帧画面 ← 直接使用 ff_desc 描述
    2. 视频中段动作 ← 从 motion_desc 提取（保留时间分段）
    3. 视频尾帧画面 ← 直接使用 lf_desc 描述
    4. 添加人物与场景一致性约束
    """
    # ========== 1. 提取首尾帧描述 ==========
    ff_desc = shot.get("ff_desc", "").strip()
    lf_desc = shot.get("lf_desc", "").strip()
    motion_desc = shot.get("motion_desc", "").strip()
    if not motion_desc:
        motion_desc = shot.get("prompt", "")

    subtitle = shot.get("subtitle", "").strip()
    main_subject = shot.get("main_subject", "")
    location = shot.get("location", "") or shot.get("scene", "")

    # ========== 2. 获取角色特征 ==========
    char_features = ""
    characters = script.get("characters", [])
    for char in characters:
        if char.get("identifier") == main_subject:
            static = char.get("static_features", "")
            dynamic = char.get("dynamic_features", "")
            if static or dynamic:
                char_features = f"[{main_subject}: {static}, {dynamic}]"
            break

    # ========== 3. 空镜检测 ==========
    is_character_shot = not is_empty_scene(shot, characters)

    # ========== 4. 构建派生视频 prompt（首帧->动作->尾帧） ==========
    parts = []

    # 4.1 角色特征锚点（放最前面）
    if char_features:
        parts.append(char_features)

    # 4.2 首帧画面描述（视频起点）
    if ff_desc:
        parts.append(f"【视频起点】{ff_desc}")

    # 4.3 中段动作描述（保留时间分段）
    if motion_desc:
        parts.append(f"【视频中段】{motion_desc}")

    # 4.4 尾帧画面描述（视频终点）
    if lf_desc:
        parts.append(f"【视频终点】{lf_desc}")

    # ========== 5. 人物与场景一致性约束 ==========
    # 5.1 首尾帧人物一致性：首帧出现的人物，尾帧必须出现
    ff_chars = _extract_characters_from_desc(ff_desc, characters)
    lf_chars = _extract_characters_from_desc(lf_desc, characters)
    if ff_chars and lf_chars:
        common_chars = set(ff_chars) & set(lf_chars)
        if common_chars:
            chars_list = "、".join(common_chars)
            parts.append(
                f"【人物一致性约束】首帧到尾帧全程保持相同人物："
                f"{chars_list}的服装、发型、五官必须完全一致，"
                f"禁止中途换装或整形。"
            )

    # 5.2 场景一致性：首尾帧场景必须匹配
    if location and (ff_desc or lf_desc):
        parts.append(
            f"【场景一致性约束】画面场景始终保持在{location}，"
            f"禁止场景突变或场景跳跃。"
        )

    # 5.3 动作连贯性：首帧状态 -> 中段动作 -> 尾帧状态 必须逻辑连贯
    parts.append(
        "【动作连贯性】视频画面从首帧到尾帧必须平滑过渡："
        "首帧画面状态是中段动作的起点，"
        "尾帧画面状态是中段动作的终点，"
        "三个阶段的动作必须连贯，"
        "禁止画面突然跳变或人物突然消失。"
    )

    # ========== 6. 处理声音指令 ==========
    if subtitle:
        is_dialogue = (
            ":" in subtitle
            or "：" in subtitle
            or '"' in subtitle
            or "“" in subtitle
            or "‘" in subtitle
        )

        if _MODEL_VOICE:
            if is_dialogue:
                parts.append(f'（用中文普通话说）"{subtitle}"')
            else:
                parts.append(
                    f"（中文普通话画外音旁白，仅声音：{subtitle}）"
                )
            parts.append("所有语音必须为中文普通话，禁止任何英文语音。")
        else:
            if is_dialogue and is_character_shot:
                parts.append("角色可有开口说话的口型与神态，但不发出人声。")
            parts.append(
                "音频只保留贴合画面的环境音效"
                "（风声、水声、虫鸣、衣料与器物声等），"
                "禁止任何人声、旁白、对话、歌声。"
            )

    # ========== 7. 空镜约束 ==========
    if not is_character_shot:
        parts.append(
            "空镜头，画面中严禁出现任何人物、身影、人影、"
            "轮廓或人形。只拍摄自然景物特写。"
        )

    # ========== 8. 风格收束 ==========
    parts.append(
        "整段视频每一帧都保持写实古风真人风格，首尾画风完全一致，"
        "禁止中途转为动漫、动画、2D渲染、卡通脸或任何非写实风格。"
    )

    # ========== 9. 模式B连续接续指令 ==========
    if mode == "B":
        parts.append(
            "【连续接续模式】延续参考图像中的人物五官、发型、"
            "服饰、材质、光影、场景透视，"
            "禁止人物外貌漂移、服装变化、色彩突变、场景跳跃。"
            "镜头运动平稳自然，"
            "与上一镜头无缝衔接。"
        )

    prompt = "\n".join(parts)

    # ========== 10. 添加视觉衔接 ==========
    extras = []
    cb = shot.get("context_bridge", "").strip()
    if cb:
        extras.append(f"视觉衔接：{cb}")
    if extras:
        prompt = f"{prompt}\n\n{'；'.join(extras)}。"

    return prompt


def _make_request(video_adapter, prompt: str, frame_paths: list, shot: dict,
                  audio_duration: float = 0, characters: list | None = None,
                  continuity_ref_image: str | None = None, mode: str = "A"):
    """构建 VideoGenerationRequest.

    frame_paths: [首帧路径, 尾帧路径(可选), 中间关键帧...]
      模式A（关键帧模式）用：首帧+尾帧->双锚定，多帧->多关键帧叙事。
    continuity_ref_image: 模式B时传入上一镜视频尾帧路径作为 I2V 参考图，
      替代本镜首帧（不再生成 start_frame 图片）。
    mode: "A"（关键帧模式）或 "B"（I2V 连续接续模式）。
      模式B时跳过 frame_paths 首帧解析，直接用 continuity_ref_image 作 image_path。
    """
    from _video_gen import VideoGenerationRequest

    # 注入导演风格前缀（如果有配置）
    cinematic_prefix = (
        get_cinematic_prefix(CINEMATIC_STYLE) if CINEMATIC_STYLE else ""
    )
    full_prompt = sanitize_prompt(
        ensure_prefix(prompt, STYLE_PREFIX), verbose=False
    )
    if cinematic_prefix:
        full_prompt = cinematic_prefix + full_prompt

    # 静音人声模式下，负面提示词同步追加人声类条目（双保险）
    negative = (
        NEGATIVE_PROMPT
        if _MODEL_VOICE
        else NEGATIVE_PROMPT + "、人声、旁白配音、对话语音、歌声"
    )
    # 物理逻辑约束：防止违反常识的场景（车马上船、水上行走等）
    negative += (
        "、车马在水中行驶、马车在水上跑、人在水上走、物体违反重力"
    )
    # v6.5 Agens AI 幻觉防护：追加负向词屏蔽常见幻觉元素
    negative += (
        "、換臉、五官變形、臉型改變、"
        "凭空道具、多余杂物、六指畸形、扭曲肢体、"
        "人物消失、服装突变、凭空人物"
    )
    # 导演风格负面提示词
    if CINEMATIC_STYLE:
        director_neg = get_director_negative(CINEMATIC_STYLE)
        if director_neg:
            negative += "、" + director_neg
    image_path = None
    last_frame_image_path = None
    extra_keyframe_paths = []

    if _USE_TEXT_TO_VIDEO:
        # 文生视频模式：完全不使用任何参考图（强制 T2V）
        pass
    else:
        # 图生视频模式：使用帧图片作为参考
        if mode == "B" and continuity_ref_image:
            image_path = continuity_ref_image
            for i, fp in enumerate((frame_paths or [])):
                if fp and Path(fp).exists() and i >= 1:
                    last_frame_image_path = fp
                    break
        else:
            for i, fp in enumerate((frame_paths or [])):
                if fp and Path(fp).exists():
                    if i == 0:
                        image_path = fp
                    elif i == 1:
                        last_frame_image_path = fp
                    elif i >= 2:
                        extra_keyframe_paths.append(fp)

    # 模式A下如果没有有效帧图片，不注入任何参考图（纯 text-to-video）
    # 避免角色肖像参考图误导视频模型生成错误内容
    # 模式B下如果没有前一镜尾帧，也降级为 text-to-video

    # 使用音频实际时长替代 LLM 预估的 duration，保证音画对齐
    duration = (
        max(round(audio_duration), 3)
        if audio_duration > 0
        else shot.get("duration", 5)
    )

    # 多关键帧优先：3+ 帧时使用多关键帧模式，2帧时使用首尾双锚定
    if extra_keyframe_paths:
        pass  # 使用多关键帧
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

    # 镜头文本：只看本镜头自身的画面描述（不含 context_bridge）
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
