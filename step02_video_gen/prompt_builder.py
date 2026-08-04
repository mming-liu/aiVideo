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


# ========================================================================
# 豆包 Fast 模板吸收（2026-08-04）：参考图清单 / 人物站位清单 / 场景时间
# ========================================================================

_TIME_WORDS = (
    "黎明", "破晓", "清晨", "早晨", "早上", "朝阳", "旭日",
    "正午", "中午", "午后", "下午", "傍晚", "黄昏", "日落", "夕阳",
    "入夜", "夜晚", "深夜", "午夜", "月夜", "星光",
)


def _detect_time_of_day(text: str) -> str:
    """从画面描述中探测时间段（晨/午/暮/夜），未命中返回空串."""
    if not text:
        return ""
    for w in _TIME_WORDS:
        if w in text:
            return w
    return ""


# 场景/环境词表：用于区分 r2v 参考图是「场景参考」还是「人物参考」。
# 命中这些词的参考图 → 用途标注「场景/环境参考」；否则按角色注册表标注人物用途。
_SCENE_REF_WORDS = (
    "场景", "环境", "背景", "地点", "场景", "风景", "景色", "山水", "湖面", "水面",
    "荷塘", "池塘", "庭院", "回廊", "楼阁", "屋檐", "屋宇", "屋顶", "城墙", "城门",
    "街道", "市集", "田野", "田埂", "村庄", "山寨", "山峦", "山崖", "山谷", "树林",
    "竹林", "森林", "江河", "河流", "瀑布", "石桥", "拱桥", "木桥", "小舟", "船",
    "马车", "花轿", "轿子", "月亮", "月光", "夜空", "星空", "天边", "夕阳", "黄昏",
    "烛火", "灯笼", "灯光", "屋内", "室内", "大堂", "殿内", "书房", "卧室", "城墙头",
)


def _load_character_registry() -> dict:
    """加载角色注册表（存在时）.

    优先级：工作目录的 character_portraits_registry.json（step02a/step02b 启动时
    由 character_bank 写入，含最新动态特征）→ CharacterBank 全角色导出
    （新章节直接跑 step02b 时工作目录还没有 registry，回退到全书级持久化 bank）.

    注册表格式：{角色名: {front/side/back: {path, description}}}.
    """
    try:
        from shared.paths import vimax_working_dir
        registry_path = vimax_working_dir() / "character_portraits_registry.json"
        if registry_path.exists():
            with open(registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data:
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    # 回退：全书级 CharacterBank（跨章持久化，路径为绝对路径）
    try:
        from shared.character_bank import CharacterBank
        bank = CharacterBank()
        bank.load()
        reg = bank.get_all_portraits_as_registry()
        return reg if isinstance(reg, dict) else {}
    except Exception:
        return {}


def _classify_reference_purpose(path: str, shot: dict, registry: dict,
                                characters: list) -> str:
    """为一张 r2v 参考图判定用途标签（seedance2-skill @引用语义吸收）.

    判定优先级：
    1. 路径文件名含角色名 → 该角色的人物形象参考
    2. 路径属于注册表里某角色的肖像（front/side/back）→ 该角色的人物形象参考
    3. 参考图父目录名 = 角色名（characters_bank/{角色}/）→ 该角色人物参考
    4. 镜头的 ff_desc/prompt 中出现的角色里，谁命中路径 → 该角色参考
    5. 都不命中 → 按镜头场景/环境词判定为场景参考，否则兜底「一致性参考」

    最终用途说明显式写清角色/场景用途，避免模型把人物肖像当场景、把场景
    参考当人物（seedance2-skill 避坑第1条「引用模糊」）。
    """
    from pathlib import Path as _P
    name = _P(path).name
    stem = _P(path).stem

    # 1. 文件名直接含角色名（如 杨旭_front.png）
    for ch in characters:
        ident = ch.get("identifier", "")
        if ident and ident in (name, stem):
            return f"角色「{ident}」人物形象参考，图中人物五官、发型、服装以参考图为准"
    for ident in registry:
        if ident and ident in (name, stem):
            return f"角色「{ident}」人物形象参考，图中人物五官、发型、服装以参考图为准"

    # 2. 路径属于注册表某角色的肖像
    for ident, views in registry.items():
        if not ident:
            continue
        for view_data in views.values():
            if not isinstance(view_data, dict):
                continue
            p = view_data.get("path", "")
            if p and str(p).replace("\\", "/") == str(path).replace("\\", "/"):
                return f"角色「{ident}」人物形象参考，图中人物五官、发型、服装以参考图为准"

    # 3. 父目录名 = 角色名（characters_bank/{角色}/...）
    parent_dir = _P(path).parent.name
    for ch in characters:
        ident = ch.get("identifier", "")
        if ident and parent_dir == ident:
            return f"角色「{ident}」人物形象参考，图中人物五官、发型、服装以参考图为准"

    # 4. keyframes 目录中的帧图：仅当 main_subject 是已知角色时按该角色标注。
    #    不猜——路径不含角色名的帧图，无法确定其内容，宁可落到场景/一致性兜底，
    #    也不能硬扣一个角色（避免人物参考被误标，或场景帧被错标成人物）。
    ms = shot.get("main_subject", "").strip()
    if ms and ("keyframes" in str(path) or "kf" in str(path).lower()):
        for ch in characters:
            if ch.get("identifier") and ch["identifier"] == ms:
                return f"角色「{ms}」人物形象参考，图中人物五官、发型、服装以参考图为准"

    # 5. 兜底：场景/环境 或 一致性
    scene_probe = " ".join([shot.get("location", ""), shot.get("scene", ""),
                            shot.get("ff_desc", ""), shot.get("prompt", "")])
    if any(w in scene_probe for w in _SCENE_REF_WORDS):
        return "场景/环境参考，图中环境、光线、空间布局以参考图为准"
    return "一致性参考，图中人物与场景风格以参考图为准"


def _build_r2v_purpose_list(shot: dict, registry: dict = None,
                            characters: list | None = None,
                            ref_paths: list | None = None) -> str:
    """构建 r2v 参考图逐图用途清单（seedance2-skill @引用语义吸收）.

    与运行时 _build_reference_list（豆包模板吸收）不同：后者只声明首帧/尾帧/
    关键帧的时间语义；本函数针对 r2v 模式给**每一张参考图**标注具体用途
    （人物形象/场景/道具/一致性），实现 seedance2-skill 的「@图片N 作为XX」。
    无注册表信息时回退为通用「一致性参考」声明，不丢原有语义。

    shot: 当前镜头 dict
    registry: character_portraits_registry.json 内容（可选）
    characters: script["characters"]（可选，用于角色名匹配）
    ref_paths: 实际传入 provider 的 reference_paths 列表（r2v 模式的参考图）
    """
    registry = registry if registry is not None else _load_character_registry()
    characters = characters or []
    refs = []
    for p in (ref_paths or []):
        if not p or not Path(p).exists():
            continue
        purpose = _classify_reference_purpose(str(p), shot, registry, characters)
        refs.append(f"- {Path(p).name}：{purpose}")
    if not refs:
        return ""
    head = ("【参考图用途】以下每张参考图用途不同，严格按标注用途取用：\n"
            "  人物参考只取人物特征，场景参考只取环境光线，"
            "禁止把人物参考当场景、把场景参考当人物。")
    return head + "\n" + "\n".join(refs)


def _build_scene_environment(shot: dict) -> str:
    """构建场景时间环境标注（豆包模板吸收 #3）.

    视频 prompt 开头锚定「地点 + 时间段」，给模型一个早期空间基准。
    时间段从 ff_desc/prompt 的已有描写中探测，不新增内容、不写死。
    """
    location = shot.get("location", "") or shot.get("scene", "")
    if not location:
        return ""
    probe = f"{shot.get('ff_desc', '')} {shot.get('prompt', '')}"
    t = _detect_time_of_day(probe)
    if t:
        return f"【场景环境】{location}，{t}"
    return f"【场景环境】{location}"


def _build_character_layout_block(shot: dict, characters: list) -> str:
    """构建「人物站位清单」区块（豆包模板吸收 #2）.

    逐角色列出镜头开场画面实际出现的角色及其身份特征，显式枚举 + 身份锚定，
    防止视频模型多人镜头漏人/错位/脑补陌生人（与 ff_desc 的完整静态布局同源，
    此处把它抽成逐角色清单强化遵循度）。

    只基于 ff_desc + main_subject 判定开场在场角色（晚进场的角色由 motion_desc
    描述其登场，不锁进开场清单，避免过度约束）。情绪只允许具象动作，禁止
    抽象情绪词（遵守 SYSTEM_PROMPT 规则 17.2，不倒退到豆包式的「悲伤/愤怒」）。
    空镜无角色，返回空串。
    """
    probe = " ".join([shot.get("ff_desc", ""), shot.get("main_subject", "")])
    if not probe:
        return ""
    appearing = [c for c in characters
                 if c.get("identifier") and c["identifier"] in probe]
    if not appearing:
        return ""

    lines = []
    for c in appearing:
        ident = c["identifier"]
        feats = [f for f in (c.get("static_features", ""), c.get("dynamic_features", "")) if f]
        if feats:
            lines.append(f"- {ident}：{'，'.join(feats)}，位置/朝向/动作以视频起点描述为准")
        else:
            lines.append(f"- {ident}：位置/朝向/动作以视频起点描述为准")

    return (
        "【人物站位清单】本镜头开场画面包含以下角色，必须在开场画面中正确出现，"
        "不得缺失、错位或替换为他人，位置/朝向/动作与视频起点描述一致，"
        "角色间视线方向相互吻合；各角色情绪用具体面部与肢体动作表现"
        "（瞪眼/垂眸/攥拳/肩背起伏等），禁止抽象情绪词。\n"
        + "\n".join(lines)
    )


def _build_reference_list(image_path, last_frame_image_path, extra_keyframe_paths,
                          mode: str = "A", ref_kind: str = "image") -> str:
    """构建「参考图清单」声明（豆包模板吸收 #1）.

    让视频模型明确每张传入参考图的语义：首帧起点 / 尾帧终点 / 关键帧 /
    r2v 角色一致性参考。不声明时模型可能把首帧当角色参考图、把关键帧当
    尾帧，造成构图混乱。text 模式无参考图，返回空串。

    ref_kind: "image"（图生视频，帧图有明确时间语义）或 "r2v"（参考生视频，
      参考图只作角色/道具/场景一致性锚定，无时间语义）。
    """
    n = 1
    refs = []
    if ref_kind == "r2v":
        # r2v：所有参考图都是「角色/道具/场景一致性」参考
        label = ("角色/道具/场景一致性参考，图中人物的五官、发型、服装、"
                 "道具与场景风格以参考图为准")
        for p in ([image_path] if image_path else []) \
                + ([last_frame_image_path] if last_frame_image_path else []) \
                + list(extra_keyframe_paths or []):
            if p and Path(p).exists():
                refs.append(f"参考图{n}：{label}")
                n += 1
    else:
        if mode == "B" and image_path:
            refs.append("参考图1：上一镜头结尾画面，本镜头画面从这里无缝延续，"
                        "人物姿态与构图以它为基准")
            n = 2
        elif image_path:
            refs.append("参考图1：视频起点画面（首帧），本镜头画面从这里开始，"
                        "人物位置与构图以它为基准")
            n = 2
        if last_frame_image_path:
            refs.append(f"参考图{n}：视频终点画面（尾帧），动作结束后画面落在该构图")
            n += 1
        for p in (extra_keyframe_paths or []):
            if p and Path(p).exists():
                refs.append(f"参考图{n}：动作过程关键帧，展示动作推进到该时刻的瞬间画面")
                n += 1
    if not refs:
        return ""
    head = "【参考图清单】本次生成使用以下参考图，严格按其所示内容生成，禁止与参考图冲突："
    return head + "；".join(refs)


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

    # 4.0 场景时间环境标注（豆包模板吸收 #3）
    env = _build_scene_environment(shot)
    if env:
        parts.append(env)

    # 4.1 角色特征锚点（放最前面）
    if char_features:
        parts.append(char_features)

    # 4.1b 人物站位清单（豆包模板吸收 #2）
    layout_block = _build_character_layout_block(shot, characters)
    if layout_block:
        parts.append(layout_block)

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
        reference_paths = []
        pass
    elif _REFERENCE_MODE == "r2v":
        # R2V 模式：所有参考图都进 reference_paths（参考生视频，角色/道具/场景一致性锚定），
        # 不传首帧/尾帧/关键帧。此前 r2v 参考图被按位置塞进 image_path/last_frame/
        # extra_keyframes（首帧/尾帧时间语义），而 bailian R2V 只看 reference_paths——
        # 参考图语义从未真正到达 provider。此处显式归位。
        # 参考图由本函数权威收集（出镜角色肖像，视角匹配，最多5个）：
        # 调用方传 frame_paths 时收集的是不存在的 keyframes/ 子目录（step02b 旧逻辑），
        # 或与 bailian「reference_paths ≤5」约定冲突的首帧/尾帧/关键帧——一律忽略，
        # 只以 registry 肖像为准，避免 r2v 参考图静默断链（code-audit 08-04）。
        extra_keyframe_paths = []
        image_path = None
        last_frame_image_path = None
        reference_paths = _collect_r2v_references(
            shot, characters=characters or [], registry=_load_character_registry(),
        )
    else:
        # 图生视频模式：使用帧图片作为参考
        reference_paths = []
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

    # 参考图清单声明（豆包模板吸收 #1）：在路径解析完成后再注入，
    # 让视频模型明确每张参考图的语义（首帧起点/尾帧终点/关键帧/r2v 一致性参考）。
    # text 模式无参考图返回空串，不改变现有 text 模式 prompt 形态。
    if _REFERENCE_MODE == "r2v":
        # r2v：给每张参考图标注具体用途（人物形象/场景/一致性），
        # 实现 seedance2-skill 的「@图片N 作为XX」逐图语义声明。
        r2v_purposes = _build_r2v_purpose_list(
            shot, registry=_load_character_registry(), characters=characters or [],
            ref_paths=reference_paths,
        )
        if r2v_purposes:
            full_prompt = full_prompt + "\n\n" + r2v_purposes
    else:
        ref_list = _build_reference_list(
            image_path, last_frame_image_path, extra_keyframe_paths,
            mode=mode, ref_kind=_REFERENCE_MODE,
        )
        if ref_list:
            full_prompt = full_prompt + "\n\n" + ref_list

    return VideoGenerationRequest(
        prompt=full_prompt,
        clip_index=shot["index"],
        duration=duration,
        negative_prompt=negative,
        size="720x1280",
        image_path=image_path,
        last_frame_image_path=last_frame_image_path,
        extra_keyframe_paths=extra_keyframe_paths,
        reference_paths=reference_paths,
    )


def _collect_r2v_references(shot: dict, characters: list | None = None,
                            registry: dict | None = None,
                            limit: int = 5) -> list[str]:
    """收集 r2v 模式的参考图（角色肖像，最多 limit 个）.

    r2v 模式的参考图来自 character_portraits_registry.json 中「本镜头实际
    出镜角色」的肖像（front/side/back），按出现顺序 + 视角词（背面/侧面/
    正面）匹配最合适视图；注册表缺失时回退到出镜角色存在的前/侧/背任意一张。
    空镜（画面禁止人物）返回空列表——肖像会诱导模型把人物画进空镜。

    与 _select_best_reference 的区别：后者为图生图（首帧/尾帧）选单张最优，
    且可能回退到风格锚点；r2v 需要多张并只认角色肖像（风格锚点对角色一致性
    无意义）。返回空列表时由 _make_request 降级为纯文生视频。
    """
    if is_empty_scene(shot, characters or []):
        return []

    registry = registry if registry is not None else _load_character_registry()
    if not registry:
        return []

    search_text = " ".join([
        shot.get("ff_desc", "").strip() or "",
        shot.get("motion_desc", "").strip() or shot.get("prompt", ""),
        shot.get("lf_desc", "").strip() or "",
    ])
    view_pref = "front"
    for kw in ("背面", "背影", "背向", "背对", "背身", "后背", "背朝"):
        if kw in search_text:
            view_pref = "back"
            break
    for kw in ("侧身", "侧面", "侧脸", "侧目", "半侧", "侧对", "侧立"):
        if kw in search_text:
            view_pref = "side"
            break

    refs: list[str] = []
    for ch in (characters or []):
        ident = ch.get("identifier", "")
        if not ident or ident not in search_text:
            continue
        views = registry.get(ident)
        if not views:
            continue
        picked = None
        for view in [view_pref, "front", "side", "back"]:
            p = views.get(view, {})
            path = p.get("path", "") if isinstance(p, dict) else ""
            if path and Path(path).exists():
                picked = path
                break
        if picked and picked not in refs:
            refs.append(picked)
            if len(refs) >= limit:
                break
    return refs


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
