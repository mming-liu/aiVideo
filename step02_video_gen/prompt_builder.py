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
import re

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import style_prefix
from style_prefix import (
    STYLE_PREFIX, NEGATIVE_PROMPT, ensure_prefix, sanitize_prompt,
    is_empty_scene,
)
from shared.style_cinematic import get_cinematic_prefix, get_director_negative
from shared.config import CINEMATIC_STYLE

# 是否允许视频模型自带语音（默认开启）：
# 2026-08 架构变更：对话已交给视频模型（模型生成说话+口型+台词），step02 不再
# 调用 TTS。设 VIDEO_MODEL_VOICE=0 可回到旧行为（模型只生成环境音效，全部语音由
# step03 TTS 提供）。
_MODEL_VOICE = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"

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
# 声音一致性（2026-08）：台词 + 说话人 + 音色描述注入
# ========================================================================

_SPEAKER_PREFIX_RE = re.compile(
    r"^([一-鿿]{1,8}?)(?:道|说|问|答|喊|叫|叹|怒|笑|哭|喝|骂|"
    r"低声道|低声说|低声问道|低声笑道|笑道|冷笑道|急道|忙道|"
    r"冷声道|朗声道|轻声道|声道|声说)[:：]"
)


def _extract_speaker_line(text: str) -> str:
    """从「说话人道：台词」中提取台词部分（去掉说话人前缀）。

    例：「听香尖叫道：救命啊——」→「救命啊——」
    匹配不到前缀时原样返回（无说话人前缀，仅台词）。
    """
    if not text:
        return text
    m = _SPEAKER_PREFIX_RE.match(text)
    if m:
        return text[m.end():].strip()
    return text.strip()


def _detect_speaker_from_line(text: str, characters: list) -> str | None:
    """从台词行检测说话人角色名（复用 step03 shot_tts 的检测逻辑）。

    step03_tts_audio/shot_tts.py._detect_speaker_from_narration 是权威实现，
    此处直接用 import 复用，避免两处逻辑漂移。
    """
    try:
        from step03_tts_audio.shot_tts import _detect_speaker_from_narration
        return _detect_speaker_from_narration(text, characters or [])
    except Exception:
        return None


def _get_voice_description(text: str, characters: list) -> str:
    """获取说话人的音色描述（如「青年男声，阳光」），用于 prompt 声音一致性。

    复用 tts/voice_registry 的每角色音色映射（跨章持久化）：get_voice 会为
    新角色分配并保存音色，返回含 style 描述的配置。把 style 拼进台词指令，
    让视频模型按描述发声——同名角色跨镜头音色一致。
    无角色/匹配失败时返回空串。
    """
    speaker = _detect_speaker_from_line(text, characters)
    if not speaker:
        return ""
    try:
        from tts.voice_registry import get_registry
        from tts.voice_registry import AVAILABLE_VOICES
        reg = get_registry()
        # 用角色属性匹配音色（与 step03 相同逻辑）
        gender, age_range = None, None
        for c in (characters or []):
            if c.get("identifier") == speaker:
                gender = c.get("gender", "").lower()
                age = c.get("age_range", "").lower()
                if gender in ("male", "female"):
                    gender = gender
                else:
                    gender = None
                age_range = age if age in ("young", "middle", "old") else None
                break
        voice = reg.get_voice(speaker, gender, age_range)
        style = voice.get("style", "").strip()
        return style
    except Exception:
        return ""


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


def _load_scene_assets_registry() -> dict:
    """加载场景资产注册表（存在资产图的场景）.

    格式: {identifier: {wide: {path, description}, near: {path, description}}}
    资产图由 step02a asset_gen 从 global/scenes_bank 生成，跨章复用。
    """
    try:
        from shared.assets_bank import SceneAssetsBank
        bank = SceneAssetsBank()
        bank.load()
        reg = bank.get_all_assets_as_registry()
        return reg if isinstance(reg, dict) else {}
    except Exception:
        return {}


def _load_prop_assets_registry() -> dict:
    """加载道具资产注册表（存在资产图的道具）.

    格式: {identifier: {path, description}}
    资产图由 step02a asset_gen 从 global/props_bank 生成，跨章复用。
    """
    try:
        from shared.assets_bank import PropAssetsBank
        bank = PropAssetsBank()
        bank.load()
        reg = bank.get_all_assets_as_registry()
        return reg if isinstance(reg, dict) else {}
    except Exception:
        return {}


def _classify_reference_purpose(path: str, shot: dict, registry: dict,
                                characters: list) -> str:
    """为一张 r2v 参考图判定用途标签（seedance2-skill @引用语义吸收）.

    判定优先级：
    0. 路径为本镜头已生成的首帧（first_frame.png）→ 视频起点构图锚点
    1. 路径文件名含角色名 → 该角色的人物形象参考
    2. 路径属于注册表里某角色的肖像（front/side/back）→ 该角色的人物形象参考
    3. 参考图父目录名 = 角色名（characters_bank/{角色}/）→ 该角色人物参考
    4. 路径属于场景资产库（scenes_bank/{场景}/wide|near.png）→ 场景/环境参考
    5. 路径属于道具资产库（props_bank/{道具}/prop.png）→ 道具外观参考
    6. 镜头的 ff_desc/prompt 中出现的角色里，谁命中路径 → 该角色参考
    7. 都不命中 → 按镜头场景/环境词判定为场景参考，否则兜底「一致性参考」

    最终用途说明显式写清角色/场景/道具用途，避免模型把人物肖像当场景、把
    场景参考当人物（seedance2-skill 避坑第1条「引用模糊」）。
    """
    from pathlib import Path as _P
    name = _P(path).name
    stem = _P(path).stem

    # 0. 本镜首帧：视频起点构图锚点（时间/场景/人物站位/动作起点以它为准）
    if name == "first_frame.png":
        return ("视频起点构图锚点，画面从该静态瞬间开始，时间、场景、人物站位、"
                "动作起点与图中完全一致，后续动作在此基础上推进")

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

    # 4. 场景资产库（scenes_bank/{场景}/wide|near.png）
    if "scenes_bank" in str(path):
        scene_name = parent_dir
        is_wide = "wide" in name.lower()
        view_label = "大场景建立镜头" if is_wide else "近景功能场景"
        return f"场景「{scene_name}」{view_label}参考，图中环境、光线、空间布局、季节色调以参考图为准，画面应保持与该地点一致"

    # 5. 道具资产库（props_bank/{道具}/prop.png）
    if "props_bank" in str(path):
        return f"道具「{parent_dir}」外观参考，图中道具的材质、颜色、结构、尺寸与使用痕迹以参考图为准，画面出现该道具时必须一致"

    # 6. keyframes 目录中的帧图：仅当 main_subject 是已知角色时按该角色标注。
    #    不猜——路径不含角色名的帧图，无法确定其内容，宁可落到场景/一致性兜底，
    #    也不能硬扣一个角色（避免人物参考被误标，或场景帧被错标成人物）。
    ms = shot.get("main_subject", "").strip()
    if ms and ("keyframes" in str(path) or "kf" in str(path).lower()):
        for ch in characters:
            if ch.get("identifier") and ch["identifier"] == ms:
                return f"角色「{ms}」人物形象参考，图中人物五官、发型、服装以参考图为准"

    # 7. 兜底：场景/环境 或 一致性
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
    # 2026-08 架构变更：对话/背景音交给视频模型（wan2.7 系列有声视频）。
    # 每个镜头都注入场景背景音（audio_cue），对话镜头额外注入台词+说话人+音色描述。
    # 台词文本本身写进 prompt，让模型念出来并与口型同步。
    dialogue_text = shot.get("narration_segment", "").strip()
    is_dialogue = (
        ":" in dialogue_text or "：" in dialogue_text
        or '"' in dialogue_text or "“" in dialogue_text or "‘" in dialogue_text
    )

    # 6.0 场景背景音（所有镜头）：audio_cue 由 step01 生成，如「蝉鸣声渐起、水波轻拍」。
    # 贴合当前场景即可——对话镜头同样保留背景音（人声在前、环境音垫底）。
    audio_cue = shot.get("audio_cue", "").strip()
    if _MODEL_VOICE:
        if audio_cue:
            parts.append(f"（背景音效：{audio_cue}）")
        elif not is_character_shot:
            parts.append("（背景音效：贴合场景的环境音，如风声、水声、虫鸣）")
        if is_dialogue and dialogue_text:
            # 台词 + 说话人 + 音色描述：只注入台词与说话人，让模型念出台词并同步口型。
            # 声音一致性用 voice_registry 的 style 描述（如「青年男声，阳光」）。
            voice_desc = _get_voice_description(dialogue_text, characters)
            speaker_line = _extract_speaker_line(dialogue_text)
            if voice_desc:
                parts.append(
                    f'（用中文普通话说，{voice_desc}）"{speaker_line}"'
                )
            else:
                parts.append(f'（用中文普通话说）"{speaker_line}"')
            parts.append("所有语音必须为中文普通话，禁止任何英文语音。")
    else:
        # 旧行为：模型不发声，语音由 step03 TTS 提供
        if is_dialogue and is_character_shot:
            parts.append("角色可有开口说话的口型与神态，但不发出人声。")
        if audio_cue:
            parts.append(f"（背景音效：{audio_cue}）")
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
                  characters: list | None = None,
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

    # _MODEL_VOICE=0 时（旧行为），负面提示词追加人声类条目（双保险）
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

    # 时长改由 shot["duration_sec"] 控制（step01 timecode 权威计算）。
    # 不再由 TTS 音频时长驱动——视频模型自带对话/背景音后，音频不再是时长来源。
    # 回退到 duration（旧分镜脚本无 duration_sec 字段）。
    # 强制 int：API 要求 parameters.duration 为整数（脚本里可能是 3.0 float，会被拒）。
    duration = int(shot.get("duration_sec") or shot.get("duration", 5))

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
    """收集 r2v 模式的参考图（角色肖像 + 场景资产 + 道具资产，最多 limit 个）.

    参考图来源（优先级：场景环境 → 出镜角色肖像 → 出镜道具）：
    1. 镜头所属场景的资产图（大场景 wide 优先，近景 near 次之）——
       scenes_bank/{场景}/wide.png|near.png，锚定环境/光线/色调
    2. 本镜头实际出镜角色的肖像（front/side/back，视角匹配；特写镜头优先
       face 面部特写锁五官，组合参考图法）——character_portraits_registry.json
    3. 本镜头出现的道具资产图 —— props_bank/{道具}/prop.png

    空镜（画面禁止人物）：只收场景资产图，不注入任何角色肖像——
    肖像会诱导模型把人物画进空镜。

    与 _select_best_reference 的区别：后者为图生图（首帧/尾帧）选单张最优，
    且可能回退到风格锚点；r2v 需要多张并只认角色肖像/场景/道具资产
    （风格锚点对角色一致性无意义）。返回空列表时由 _make_request 降级为纯文生视频。
    """
    registry = registry if registry is not None else _load_character_registry()

    search_text = " ".join([
        shot.get("ff_desc", "").strip() or "",
        shot.get("motion_desc", "").strip() or shot.get("prompt", ""),
        shot.get("lf_desc", "").strip() or "",
    ])
    loc = shot.get("location", "") or shot.get("scene", "")

    # ---- 0. 首帧构图锚点（最高优先级）----
    # step02a 已为本镜生成正确的首帧（正确时间/场景/人物站位/动作起点）。
    # r2v 若不传首帧，视频模型只能拿场景资产图+站姿肖像自由发挥构图，
    # 造成时间错（黄昏）、船大小错、人物缺失、动作假跳（code-audit 08-05）。
    # 首帧存在时以它为构图锚点，并在组装时替代场景资产图的环境锚定（避免时间冲突）。
    composition_refs: list[str] = []
    try:
        from shared.paths import vimax_working_dir
        ff_candidate = vimax_working_dir() / "shots" / str(shot.get("index", 0)) / "first_frame.png"
        if ff_candidate.exists() and ff_candidate.stat().st_size > 10 * 1024:
            composition_refs = [str(ff_candidate)]
            _log_r2v_collect(f"  首帧构图锚点: {ff_candidate.name}（动作起点/时间/人物站位以它为准）")
    except Exception as e:
        _log_r2v_collect(f"  首帧构图锚点收集异常: {e}")

    # ---- 1. 场景资产图（环境锚定，首帧缺失时才需要）----
    scene_refs: list[str] = []
    if loc:
        try:
            from shared.assets_bank import SceneAssetsBank
            sbank = SceneAssetsBank()
            sbank.load()
            for sid in sbank.get_all_identifiers():
                if not sid:
                    continue
                scene = sbank.get_scene(sid) or {}
                # 匹配源：场景名 + 场景 consistent_terms（别名）+ 场景 location。
                # 模糊匹配：公共连续子串 ≥2 字即算同一地点
                # （"照月湾小舟" vs "云河镇照月湾" → 共"照月湾"3字命中）。
                # 单纯双向子串会漏（如"照月湾荷花丛中，一叶小舟上"不含完整场景名）。
                match_sources = [sid] + list(scene.get("consistent_terms", []) or [])
                if scene.get("location"):
                    match_sources.append(scene["location"])
                from shared.assets_bank import scene_matches_location
                matched = any(scene_matches_location(src, loc) for src in match_sources if src)
                if not matched:
                    continue
                wide = sbank.get_wide_image(sid)
                near = sbank.get_near_image(sid)
                if wide:
                    scene_refs.append(wide)
                elif near:
                    scene_refs.append(near)
                _log_r2v_collect(
                    f"  场景「{sid}」匹配(location='{loc[:30]}') → "
                    f"{Path(wide or near).name}（匹配源: {[s for s in match_sources if s][:4]}）"
                )
                break
        except Exception as e:
            _log_r2v_collect(f"  场景资产收集异常: {e}")
            scene_refs = []

    # ---- 空镜：只收场景资产图（禁止人物肖像，道具图不注入——空镜无道具特写）----
    if is_empty_scene(shot, characters or []):
        return scene_refs[:limit]

    # ---- 2. 出镜角色肖像（视角匹配）----
    char_refs: list[str] = []
    if registry:
        view_pref = "front"
        for kw in ("背面", "背影", "背向", "背对", "背身", "后背", "背朝"):
            if kw in search_text:
                view_pref = "back"
                break
        for kw in ("侧身", "侧面", "侧脸", "侧目", "半侧", "侧对", "侧立"):
            if kw in search_text:
                view_pref = "side"
                break
        # 面部特写视角（face 肖像）：特写/大特写/面部/表情镜头优先用独立面部特写
        # 锁脸防变脸（组合参考图法：三视图 + 面部特写同时输入，模型优先锁面部特征）。
        is_face_closeup = any(
            kw in search_text for kw in ("大特写", "特写", "面部", "表情", "脸部", "脸的特写",
                                         "面部特写", "微表情", "眉", "眼尾", "泪", "嘴角")
        )

        for ch in (characters or []):
            ident = ch.get("identifier", "")
            if not ident or ident not in search_text:
                continue
            views = registry.get(ident)
            if not views:
                continue
            picked = None
            # 特写镜头：face 优先（锁定五官），其次按视角/默认视图
            if is_face_closeup:
                face_p = views.get("face", {})
                fpath = face_p.get("path", "") if isinstance(face_p, dict) else ""
                if fpath and Path(fpath).exists():
                    picked = fpath
            if not picked:
                for view in [view_pref, "front", "side", "back"]:
                    p = views.get(view, {})
                    path = p.get("path", "") if isinstance(p, dict) else ""
                    if path and Path(path).exists():
                        picked = path
                        break
            if picked and picked not in char_refs:
                char_refs.append(picked)

    # ---- 3. 出镜道具资产图 ----
    prop_refs: list[str] = []
    try:
        from shared.assets_bank import PropAssetsBank
        pbank = PropAssetsBank()
        pbank.load()
        for pid in pbank.get_all_identifiers():
            if not pid:
                continue
            entry = pbank.get_prop(pid) or {}
            # 匹配源：道具名 + 道具 consistent_terms（别名）。
            # 只做道具名匹配会漏（如"弯刀"在 ff_desc 而道具名是"刺客弯刀"，
            # 但 consistent_terms 含"弯刀"可命中）。
            match_sources = [pid] + list(entry.get("consistent_terms", []) or [])
            if any(src and (src in search_text or src in loc) for src in match_sources):
                img = pbank.get_asset_image(pid)
                if img:
                    prop_refs.append(img)
                    _log_r2v_collect(
                        f"  道具「{pid}」匹配 → {Path(img).name}（匹配源: {[s for s in match_sources if s][:4]}）"
                    )
    except Exception as e:
        _log_r2v_collect(f"  道具资产收集异常: {e}")
        prop_refs = []

    # ---- 组装 ----
    # 首帧构图锚点存在：它已锚定正确时间/场景/人物站位/动作起点，替代场景资产图
    # （wide.png 是黄昏远景，与首帧的深青夜色冲突，会让视频时间漂移）。场景资产图
    # 只在无首帧时注入，作环境兜底。
    refs: list[str] = []
    base = composition_refs + ([scene_refs[0]] if scene_refs and not composition_refs else [])
    for p in base + char_refs + prop_refs:
        if p not in refs:
            refs.append(p)
        if len(refs) >= limit:
            break
    _log_r2v_collect(
        f"r2v 参考图收集完成（镜头{shot.get('index', '?')}）: "
        f"首帧{len(composition_refs)} / 场景{len(scene_refs)} / "
        f"角色{len(char_refs)} / 道具{len(prop_refs)} / 共{len(refs)}张"
    )
    for i, p in enumerate(refs):
        _log_r2v_collect(f"    [{i}] {Path(p).parent.name}/{Path(p).name}")
    return refs


def _log_r2v_collect(msg: str) -> None:
    """输出 r2v 参考图收集日志（写 step02*.log，便于分析参考图组成）."""
    try:
        from shared.paths import chapter_dir
        from shared.tee_logger import get_logger
        logger = get_logger(chapter_dir() / "step02b.log", console_level="INFO")
        logger.info(f"[R2V-COLLECT] {msg}")
    except Exception:
        print(f"[R2V-COLLECT] {msg}")


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
        is_closeup = any(kw in search_text for kw in ("大特写", "特写", "面部", "表情", "脸部"))
        for ident, views in registry.items():
            if not ident or ident not in search_text:
                continue
            # 特写镜头优先面部特写（锁五官），否则用正面全身
            if is_closeup:
                face = views.get("face", {}).get("path", "")
                if face and Path(face).exists():
                    print(f"  [fallback] 镜头{shot.get('index', '?')} 首帧缺失，使用角色 {ident} 面部特写肖像")
                    return face
            front = views.get("front", {}).get("path", "")
            if front and Path(front).exists():
                print(f"  [fallback] 镜头{shot.get('index', '?')} 首帧缺失，使用角色 {ident} 正面肖像")
                return front
    except Exception:
        pass
    return None
