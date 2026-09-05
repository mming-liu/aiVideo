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
    is_empty_scene, video_negative_prompt,
)
from shared.style_cinematic import get_cinematic_prefix, get_director_negative
from shared.config import CINEMATIC_STYLE
from shared.motion_segments import build_motion_segments, format_motion_segments, count_effective_segments
from shared.paths import FIRST_FRAME_FILENAME, first_frame_file

# 是否允许视频模型自带语音（默认开启）：
# 2026-08 架构变更：对话已交给视频模型（模型生成说话+口型+台词），step02 不再
# 调用 TTS。设 VIDEO_MODEL_VOICE=0 可回到旧行为（模型只生成环境音效，全部语音由
# step03 TTS 提供）。
# 当模型本身不支持 generate_audio（如 Seedance 1.0、MiniMax v1 Hailuo）时，强制
# 关掉所有声音/对白指令注入（对话台词、背景音、说话人音色）——这些指令对无声模型
# 是 prompt 噪声，模型可能尝试"画"语音相关动作（张嘴/手势）但发不出声，导致
# 嘴型/画面异常。改为只注入纯画面描述 + 提示有「无声」状态。
_MODEL_VOICE = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
# 运行时检测当前视频模型是否原生有声：无声模型时 _MODEL_VOICE 强制为 False，
# 后续对话/背景音指令自动跳过。
try:
    from shared.model_caps import active_video_supports_audio
    if not active_video_supports_audio():
        _MODEL_VOICE = False
        import logging as _logging
        _logging.getLogger(__name__).info(
            "[prompt_builder] 当前视频模型不支持 generate_audio，"
            "自动关闭对话/背景音指令注入（仅注入纯画面描述）"
        )
except Exception:
    pass  # 加载失败时保持 env 决定

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
    """从台词行检测说话人角色名（复用 shared.dialogue 的权威实现）。

    说话人检测统一收敛在 shared/dialogue.detect_speaker（原 step03_tts_audio/
    shot_tts.py 实现已下沉，2026-08-09 审计 P0-1）。
    """
    try:
        from shared.dialogue import detect_speaker
        return detect_speaker(text, characters or [])
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

# v6.25：空间物体（船/马车/轿/辇等可容纳人或作环境舞台的物体）在 r2v 参考中
# 属于「环境/尺寸锚点」，与手持道具（箫/伞/酒壶/弯刀）不同——它们决定画面的
# 空间比例，防模型画小或比例漂移。道具 bank 里 category=交通工具 即这类物体。
_SPACE_OBJECT_CATEGORIES = ("交通工具",)


def _is_space_object_prop(entry: dict) -> bool:
    """判断道具 bank 条目是否为空间物体（船/马车/轿等，作环境/尺寸锚点）."""
    category = (entry.get("category") or "").strip()
    return category in _SPACE_OBJECT_CATEGORIES or any(
        w in category for w in ("船", "车", "轿", "辇", "舟")
    )


# 无 bank 上下文时（_classify_reference_purpose 只有路径），按形制字识别空间物体
_SPACE_OBJECT_IDENTIFIER_WORDS = (
    "舴艋舟", "扁舟", "楼船", "画舫", "乌篷", "帆船", "采莲舟", "渡船",
    "马车", "牛车", "板车", "轿子", "花轿", "轿辇", "步辇", "舟",
)


def _is_space_object_identifier(ident: str) -> bool:
    """按标识符/形制字判断是否为空间物体（船/车/轿/舟）."""
    return any(w in ident for w in _SPACE_OBJECT_IDENTIFIER_WORDS)


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
    0.5. ref_sheet_*_{类别}.png（r2v 收集层 fit_refs 拼表产物）→ 按类别给
        「参考表」用途（多角色/多道具拼合，取各单项特征，非画面构图）
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
    if name == FIRST_FRAME_FILENAME:
        return ("视频起点构图锚点，画面从该静态瞬间开始，时间、场景、人物站位、"
                "动作起点与图中完全一致，后续动作在此基础上推进")

    # 0.5. 参考表（fit_refs 拼图产物）：按文件名类别给「参考表」用途
    if name.startswith("ref_sheet_"):
        from shared.ref_compose import describe_ref
        return describe_ref(str(path))

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
        # v6.25：空间物体（船/车/轿）作环境/尺寸锚点，非手持道具外观——
        # 白底道具图对船无用（它不告诉你船占画面多大），必须让模型以它
        # 作为画面空间比例与舞台规模的基准。
        if _is_space_object_identifier(parent_dir) or _is_space_object_identifier(stem):
            return (f"场景「{parent_dir}」空间物体参考：图中物体的尺寸、形态、"
                    f"船体/车体结构、与画面的相对比例以参考图为准，画面中的船/车"
                    f"必须保持该大小，禁止画小或比例漂移，人物与它的空间关系以此为准")
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
    # v6.24：图N = 参考图列表第N张（与 provider media 顺序一致，同 happyhorse [Image N]）。
    # 修正 v6.22 缺陷：旧实现只数角色肖像，遇到前置首帧/场景参考图时编号错位。
    idx = 0
    for p in (ref_paths or []):
        if not p or not Path(p).exists():
            continue
        idx += 1
        purpose = _classify_reference_purpose(str(p), shot, registry, characters)
        refs.append(f"- 图{idx}（{Path(p).name}）：{purpose}")
    if not refs:
        return ""
    head = ("【参考图用途】以下每张参考图用途不同，图N 严格对应参考图列表第N张"
            "（首帧/场景/道具也占位编号），严格按标注用途取用："
            "人物参考只取人物特征，场景参考只取环境光线，"
            "禁止把人物参考当场景、把场景参考当人物。")
    head += ("\n  多人镜头请用角色所在位置的「图N」指代该角色并分配其行为"
             "（如'图3的杨旭握紧酒壶，图4的听香望向画右'——编号以本清单标注为准），"
             "一个角色一个编号，禁止混用。")
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


def _build_space_object_anchor(shot: dict, script: dict) -> str:
    """构建空间物体尺寸/舞台锚点（v6.25 船小根因修复）.

    从 script.scene_objects 找本镜出现的空间物体（船/马车/轿等，含 size 数据），
    注入绝对尺寸与「它是画面主体舞台」的语义。防止视频模型在 r2v 图没船、
    只有文字「小舟」时自由发挥把船画小。

    script.scene_objects 由 step01 object_extractor 提取：{identifier: {category,
    subtype, length_m, width_m, height_m, capacity, structure, consistent_terms}}。
    命中：location/ff/prompt/lf 含对象的 consistent_terms 或 identifier。
    """
    scene_objects = (script or {}).get("scene_objects") or {}
    if not scene_objects:
        return ""
    location = shot.get("location", "") or shot.get("scene", "")
    probe = " ".join([
        location, shot.get("scene", ""), shot.get("ff_desc", ""),
        shot.get("motion_desc", "") or shot.get("prompt", ""),
        shot.get("lf_desc", ""),
    ])
    for ident, obj in scene_objects.items():
        if not isinstance(obj, dict):
            continue
        terms = [ident] + list(obj.get("consistent_terms", []) or [])
        subtype = (obj.get("subtype") or "").strip()
        if subtype:
            terms.append(subtype)
        # B5 修复（08-11）：location 走模糊匹配（公共连续子串 ≥2 字，
        # 「照月湾湖上」↔「照月湾小舟」→ 命中）；镜头全文子串匹配兜底
        # （ff/lf/motion/prompt 含对象名/称呼/形制字）。旧实现 `term in probe`
        # 纯子串，location 写「湖上」不写「小舟」就漏配、尺寸锚点永不注入。
        from shared.assets_bank import scene_matches_location
        loc_matched = any(
            scene_matches_location(term, location) for term in terms if term
        )
        text_matched = any(
            term and term in probe for term in terms if term
        )
        if not (loc_matched or text_matched):
            continue
        subtype = (obj.get("subtype") or ident or "").strip()
        category = (obj.get("category") or "").strip()
        length_m = obj.get("length_m")
        width_m = obj.get("width_m")
        dims = []
        if length_m:
            dims.append(f"{length_m:g}米长")
        if width_m:
            dims.append(f"{width_m:g}米宽")
        if obj.get("height_m"):
            dims.append(f"{obj['height_m']:g}米高")
        capacity = (obj.get("capacity") or "").strip()
        structure = (obj.get("structure") or "").strip()
        body_word = "车体" if "车" in category else ("轿体" if "轿" in category else "船体")
        parts_anchor = [f"【空间物体】本镜舞台是{subtype}（{'、'.join(dims) or '具体尺寸见参考图'}）"]
        if capacity:
            parts_anchor.append(f"，{capacity}")
        if structure:
            parts_anchor.append(f"，{structure[:40]}")
        parts_anchor.append(
            f"。它是所有人物活动的舞台，{body_word}必须在画面中清晰可见、"
            f"占据合理主体比例，禁止画成小舢板/独木舟大小或比例漂移。"
        )
        return "".join(parts_anchor)
    return ""


# v6.24：远景镜头词库（capsule 远景省略规则，与 validation._WIDE_SHOT_WORDS 同步）
_WIDE_SHOT_WORDS = {"远景", "大远景", "全景", "大全景", "俯拍", "航拍", "俯瞰", "鸟瞰", "广角"}


def _shorten_feature(feat: str, max_len: int = 25) -> str:
    """多角色/远景镜头：特征压缩到 ~max_len 字，保留开头（通常为服装主色/剪影）.

    防止多人镜头锚点膨胀、远景写精细五官导致 AI 把脸放大（capsule 吸收）。
    """
    feat = (feat or "").strip()
    if len(feat) <= max_len:
        return feat
    return feat[:max_len].rstrip("，、；：。") + "…"


# 开场画面（视频起点）禁注入的动作结果/武器词：收敛到 shared/content_safety
# （审计 S2），与 step02a `_inject_character_costume` frame_kind="first_frame"
# 过滤共用单一词表，不再各持一份。
from shared.content_safety import strip_action_result as _safety_strip_action_result


def _clean_dynamic_for_start(dynamic: str) -> str:
    """从 dynamic_features 剥离开场画面不应出现的动作结果/武器词（复用 shared 单一实现）."""
    return _safety_strip_action_result(dynamic)


def _is_wide_shot(shot: dict) -> bool:
    """远景/大远景/全景/俯拍/航拍镜头探测（胶囊远景省略规则）."""
    probe = " ".join([shot.get("ff_desc", ""), shot.get("prompt", ""),
                      shot.get("location", ""), shot.get("scene", "")])
    return any(w in probe for w in _WIDE_SHOT_WORDS)


def _build_character_layout_block(shot: dict, characters: list) -> str:
    """构建「人物站位清单」区块（豆包模板吸收 #2 + 站位台吸收 v6.35）.

    逐角色列出镜头开场画面实际出现的角色及其身份特征，显式枚举 + 身份锚定，
    防止视频模型多人镜头漏人/错位/脑补陌生人（与 ff_desc 的完整静态布局同源，
    此处把它抽成逐角色清单强化遵循度）。

    只基于 ff_desc + main_subject 判定开场在场角色（晚进场的角色由 motion_desc
    描述其登场，不锁进开场清单，避免过度约束）。情绪只允许具象动作，禁止
    抽象情绪词（遵守 SYSTEM_PROMPT 规则 17.2，不倒退到豆包式的「悲伤/愤怒」）。
    空镜无角色，返回空串。
    """
    probe = " ".join([shot.get("ff_desc", ""), shot.get("main_subject", "")])
    # 结构化站位（规则51/站位台吸收 v6.35）优先：shot.char_layout 已由 LLM 显式给出
    # 每出镜角色的 position/facing/role，直接读入清单；文本探测（ff_desc+main_subject）
    # 作兜底补漏，保证漏填/晚进场角色仍被列出。
    layout = shot.get("char_layout") or []
    known = {c.get("identifier") for c in characters if c.get("identifier")}
    layout_lines = []
    layout_names = set()
    for item in (layout if isinstance(layout, list) else []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name or name not in known:
            continue  # 只认角色注册表内的名字，防 LLM 编造路人
        layout_names.add(name)
        bits = [b for b in (str(item.get("position", "")).strip(),
                            str(item.get("facing", "")).strip()) if b]
        if not bits:
            bits = ["位置/朝向以视频起点描述为准"]
        line = f"- {name}：{'，'.join(bits)}"
        role = str(item.get("role", "")).strip()
        if role in ("主", "次", "背景"):
            line += f"（{role}）"
        layout_lines.append(line)
    if not probe and not layout_lines:
        return ""
    appearing = [c for c in characters
                 if c.get("identifier") and c["identifier"] in probe
                 and c["identifier"] not in layout_names]

    fallback_lines = []
    main_ident = (shot.get("main_subject") or "").strip()
    wide = _is_wide_shot(shot)
    many = (len(layout_lines) + len(appearing)) >= 3
    for c in appearing:
        ident = c["identifier"]
        if wide:
            # 远景：只留身份+服装主色剪影，禁精细五官（规则39）
            short = _shorten_feature(c.get("static_features", ""), 15)
            fallback_lines.append(f"- {ident}（远景剪影）：{short}，禁止五官与服装细节")
            continue
        if many and ident != main_ident:
            # 多角色镜头：非主角压缩到 ~25 字，防锚点膨胀（capsule 短锚点）
            short = _shorten_feature(c.get("static_features", ""), 25)
            fallback_lines.append(f"- {ident}：{short}，位置/朝向/动作以视频起点描述为准")
        else:
            # 开场画面 = 视频起点（ff_desc 前一刻状态），dynamic_features 是全章状态
            # （含溅血/持刀等动作结果），必须过滤——否则视频模型把血污/武器画进开场
            # （08-09 首帧杨旭胸口血迹、刺客血眼、听香溅血）。
            feats = [f for f in (c.get("static_features", ""), _clean_dynamic_for_start(c.get("dynamic_features", ""))) if f]
            if feats:
                fallback_lines.append(f"- {ident}：{'，'.join(feats)}，位置/朝向/动作以视频起点描述为准")
            else:
                fallback_lines.append(f"- {ident}：位置/朝向/动作以视频起点描述为准")

    lines = layout_lines + fallback_lines
    if not lines:
        return ""
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
    characters = script.get("characters", [])
    ff_desc = shot.get("ff_desc", "").strip()
    # v6.18：ff_desc 归一化 + 回写（多动作/进行态堆叠 → 单一静态瞬间）。
    # 回写保证【视频起点】、layout_block 的角色探测、_get_shot_hash 三处一致；
    # 也保证视频缓存自动失效（帧变了视频该重做）。r2v/text 模式不依赖
    # step02a 时，这里兜底让视频起点也用静态文本。
    if ff_desc:
        from style_prefix import normalize_ff_desc
        norm = normalize_ff_desc(ff_desc, shot, characters)
        if norm != ff_desc:
            shot["ff_desc"] = ff_desc = norm
    lf_desc = shot.get("lf_desc", "").strip()
    # v6.29：视频终点兜底归一化（与 step01 post_fix 对称，保证 step02 直接跑时
    # 中间态 lf 也被清洗；回写保证视频哈希正确失效）
    if lf_desc:
        from style_prefix import normalize_lf_desc
        _norm_lf = normalize_lf_desc(lf_desc, shot, characters)
        if _norm_lf != lf_desc:
            shot["lf_desc"] = lf_desc = _norm_lf
    motion_desc = shot.get("motion_desc", "").strip()
    if not motion_desc:
        motion_desc = shot.get("prompt", "")

    subtitle = shot.get("subtitle", "").strip()
    main_subject = shot.get("main_subject", "")
    location = shot.get("location", "") or shot.get("scene", "")

    # ========== 2. 获取角色特征 ==========
    char_features = ""
    for char in characters:
        if char.get("identifier") == main_subject:
            static = char.get("static_features", "")
            # 开场画面 = 视频起点（前一刻），dynamic_features 全章状态含溅血/持刀等
            # 动作结果，必须过滤（08-09 首帧杨旭胸口血迹/刺客血眼/听香溅血）
            dynamic = _clean_dynamic_for_start(char.get("dynamic_features", ""))
            if static or dynamic:
                # 远景镜头：主角特征也截断到剪影级，禁精细五官（规则39）
                if _is_wide_shot(shot):
                    static = _shorten_feature(static, 15)
                char_features = f"[{main_subject}: {static}, {dynamic}]"
            break

    # ========== 3. 空镜检测 ==========
    is_character_shot = not is_empty_scene(shot, characters)

    # ========== 4. 构建派生视频 prompt（首帧->动作->尾帧） ==========
    parts = []

    # 4.0a seedance 铁律3：视频模型画面禁止渲染任何字幕/文字/水印。
    # 字幕由 step04 ffmpeg 后期统一烧录，这里只禁「模型画字」。
    parts.append(
        "画面无任何字幕、文字、水印、logo、图标；禁止在画面中叠加文字。"
    )

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

    # 4.1c 空间物体尺寸/舞台锚点（v6.25 船小根因修复）：船/马车等作为舞台主体，
    # 注入绝对尺寸 + 统一称呼，防 r2v 图没船时模型自由发挥画小。
    space_anchor = _build_space_object_anchor(shot, script)
    if space_anchor:
        parts.append(space_anchor)

    # 4.1d 道具空间方位锚定（A3 修复 08-10）：同框手持道具各写落点，防视频模型
    # 自由摆放重叠（酒壶钓杆叠一起）。复用 step02a 的 _extract_prop_layout 从
    # shot 文本提取道具方位（钓杆在左手/酒壶在右手），视频起点/中段/终点共用。
    try:
        from shared.vimax_bridge import _extract_prop_layout
        prop_anchor = _extract_prop_layout(shot, characters)
        if prop_anchor:
            parts.append(prop_anchor)
    except Exception:
        pass

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

    # ========== 8b. 色彩氛围锚点（spark-video mood_anchor 吸收）==========
    tone = (shot.get("color_tone") or "").strip()
    if not tone:
        # 兜底：脚本级全局色调
        tone = (script.get("color_tone")
                or script.get("global_config", {}).get("color_tone")
                or "").strip()
    if tone:
        parts.append(
            f"【色彩氛围】全片色调锚定：{tone}；每一帧的明暗、饱和度与色温"
            f"都服从这一基调，禁止中途跳色。"
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

    # v6.58 (Phase C-2): motion_desc 时间分段显式标注。
    # 若 motion_desc 含显式时段（如「前2秒：X；2-4秒：Y」），追加一段
    # 「【时间分段】0-2秒：X；2-4秒：Y」让视频模型按段执行动作。
    # 0/1 段时跳过——单段信息已在 motion_desc 文本里；用 effective count
    # 排除 implicit 空段（如「前3秒」只产 1 段有效）。
    motion_segs = build_motion_segments(shot)
    if count_effective_segments(motion_segs) >= 2:
        seg_text = format_motion_segments(motion_segs, style="zh")
        if seg_text:
            prompt = f"{prompt}\n\n【时间分段】{seg_text}"

    # ========== 10. 添加视觉衔接 ==========
    extras = []
    cb = shot.get("context_bridge", "").strip()
    if cb:
        extras.append(f"视觉衔接：{cb}")
    if extras:
        prompt = f"{prompt}\n\n{'；'.join(extras)}。"

    return prompt


def _build_seedance_concise_prompt(shot: dict) -> str:
    """Seedance 专用精简 prompt（2026-08-20，方案 2 上游精简）.

    Seedance 硬约束中文 ≤500 字符（@图引用不计），完整 _build_prompt 注入
    18 段共 ~1500-1600 字符。精简原则：
    - 保留：ff/motion/lf 三段（核心画面信息）
    - 保留：字幕禁渲染（模型默认需明确禁止，否则可能画字）
    - r2v 模式：角色/场景/道具/色彩已通过参考图视觉化，无须文字重复
    - Seedance 自身有风格倾向：删 "整段写实古风风格收束"（45 字）
    - 删 "人物一致性约束"（80 字，r2v 角色肖像已锚定）
    - 删 "场景一致性约束"（30 字，r2v 场景资产已锚定）
    - 删 "动作连贯性"（70 字，起点/中段/终点三段已隐含）
    - 对话/背景音：audio_cue 单行保留（10-30 字），台词/说话人/音色全部删
      （_MODEL_VOICE 在 Seedance 1.0 上自动 False，prompt 指令无效）

    volcengine provider 仍保留 _seedance_compress_prompt 作兜底——精简后
    仍超 500 字符的极端 case 由它按权重删低权重段。
    """
    parts = ["画面无任何字幕、文字、水印、logo、图标；禁止在画面中叠加文字。"]
    # v6.58 (Phase C-1): 运镜指令提到最前，提升模型注意力权重
    camera_movement = (shot.get("camera_movement") or "").strip()
    if camera_movement:
        parts.append(f"运镜：{camera_movement}")
    ff_desc = shot.get("ff_desc", "").strip()
    lf_desc = shot.get("lf_desc", "").strip()
    motion_desc = shot.get("motion_desc", "").strip() or shot.get("prompt", "")
    if ff_desc:
        parts.append(f"【视频起点】{ff_desc}")
    if motion_desc:
        parts.append(f"【视频中段】{motion_desc}")
    if lf_desc:
        parts.append(f"【视频终点】{lf_desc}")
    # v6.58 (Phase C-2): 精简路径下也加时间分段（Seedance 2.0+ 官方支持）
    # —— 但 500 字硬约束下必须控成本：仅 2-3 段 + 分段文本简短时加
    motion_segs = build_motion_segments(shot)
    if 2 <= count_effective_segments(motion_segs) <= 4:
        seg_text = format_motion_segments(motion_segs, style="en_s")  # Seedance 2.0 推荐英文 's'
        if seg_text and len(seg_text) <= 80:
            parts.append(f"【时间分段】{seg_text}")
    # 背景音效（audio_cue 单行）——对话/音色留给视频模型自己理解
    audio_cue = shot.get("audio_cue", "").strip()
    if audio_cue:
        parts.append(f"（背景音效：{audio_cue}）")
    return "\n".join(parts)


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
    # Seedance 专用精简路径（2026-08-20 方案 2 上游精简）：从源头减少注入量，
    # 让 prompt 自然 ≤500 字符，provider 侧 compress 沦为兜底。
    # 其他 provider（bailian/agnes/minimax）继续用完整 _build_prompt，
    # 不影响其 prompt 架构。用 active_video_is_seedance() 统一判断——
    # volcengine / agent_plan 等 Seedance 系 provider 自动跟随，避免硬编码名单。
    from shared.model_caps import active_video_is_seedance
    if active_video_is_seedance():
        # 跳过 cinematic_prefix + STYLE_PREFIX：Seedance 自身有风格倾向，
        # 长风格前缀是噪声（与"drop 风格收束"对称）
        full_prompt = _build_seedance_concise_prompt(shot)
    else:
        # 完整 prompt 路径（其他 provider 不动）
        full_prompt = sanitize_prompt(
            ensure_prefix(prompt, STYLE_PREFIX), verbose=False
        )
        if cinematic_prefix:
            full_prompt = cinematic_prefix + full_prompt

    # _MODEL_VOICE=0 时（旧行为），负面提示词追加人声类条目（双保险）。
    # 英文 token 仅 Agnes 使用，中文模型用纯中文版（video_negative_prompt 按 VIDEO_PROVIDER 判定）
    negative = (
        video_negative_prompt()
        if _MODEL_VOICE
        else video_negative_prompt() + "、人声、旁白配音、对话语音、歌声"
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
        # v6.27：中间关键帧（kf_0/1）只有 provider 真会传给模型才收集——
        # agnes image 模式消费 extra_keyframe_paths；bailian wan2.7-i2v 的
        # media 组合固定为首帧+尾帧(+音频)，kf 传不进去。收集了但不传，
        # _build_reference_list 会在 prompt 里虚假声明「有关键帧参考图」，
        # 语义污染模型。r2v/text 模式本就不收集（用户决策 08-15：
        # keyframes 留给图生图用，其他两个模式不用）。
        # _provider 已在函数开头赋值（精简路径路由用），此处复用
        # 2026-09-05 修复：原 _provider 实际未在 _make_request 内赋值（08-15 引入 bug），
        # 改读 VIDEO_PROVIDER 环境变量（与 _USE_TEXT_TO_VIDEO / _REFERENCE_MODE 同源）
        _kf_enabled = os.getenv("VIDEO_PROVIDER", "").strip().lower() != "bailian"
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
                    elif i >= 2 and _kf_enabled:
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

    # v6.35（08-15，P18 遗留）：固定 seed——由镜头序号 + prompt 哈希派生。
    # 此前 seed 恒 None → 每次提交纯抽卡：QC 判坏后重提交拿到全新随机结果，
    # 好坏反复横跳，同一镜头 A/B 测试 prompt 改进也无法对照。固定 seed 后
    # 同 prompt 同参考重提交结果可复现（wan2.7 文档支持 seed）。
    try:
        import hashlib as _hl
        _seed_src = f"{shot.get('index', 0)}|{shot.get('ff_desc', '')}|{shot.get('lf_desc', '')}|{prompt}"
        _seed = int(_hl.md5(_seed_src.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
    except Exception:
        _seed = None

    # 把 r2v 模式实际提交的参考图列表缓存到 shot，让 submit_poll._get_shot_hash
    # 把 r2v 参考图变化纳入哈希（换角色肖像/场景资产图应触发重生成）
    if _REFERENCE_MODE == "r2v":
        shot["_r2v_refs_cache"] = list(reference_paths or [])

    # 运镜指令：step01 端的 camera_movement 结构化字段（中文运镜词）
    camera_movement = str(shot.get("camera_movement") or "").strip()

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
        seed=_seed,
        camera_movement=camera_movement,
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
        from shared.paths import first_frame_file as _fff
        ff_candidate = _fff(shot.get("index", 0))
        if ff_candidate.exists() and ff_candidate.stat().st_size > 10 * 1024:
            composition_refs = [str(ff_candidate)]
            _log_r2v_collect(f"  首帧构图锚点: {ff_candidate.name}（动作起点/时间/人物站位以它为准）")
        else:
            # A7 修复（08-11）：首帧缺失时 r2v 无构图锚点——旧逻辑场景资产图兜底
            # 只锚环境，不锚人物站位/动作起点，导致 r2v 视频构图自由发挥（时间错、
            # 人物缺失、动作假跳）。_find_fallback_reference 已写好但从未接线，这里
            # 在首帧缺失时调用它：从角色注册表找本镜出镜角色的肖像作构图锚点
            # （空镜返回 None，降级纯文生视频）。
            _fallback = _find_fallback_reference(
                vimax_working_dir(), shot=shot, characters=characters or [],
            )
            if _fallback:
                composition_refs = [_fallback]
                _log_r2v_collect(f"  首帧缺失，fallback 参考图: {Path(_fallback).name}")
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
            # 出镜判定支持威胁源别名（刺客→神秘人/黑衣人——合规替换后文本用
            # "神秘人"，但注册表 key 是"刺客"；仅 ident in search_text 会漏掉威胁源
            # 肖像，视频模型只能自由发挥刺客形象。与图片侧 _collect_portrait_refs
            # 的别名修复同源（08-10 A2）。局部导入避免模块级耦合。
            if not ident:
                continue
            try:
                from shared.vimax_bridge import _char_aliases
                ident_hit = any(a in search_text for a in _char_aliases(ident, characters))
            except Exception:
                ident_hit = ident in search_text
            if not ident_hit:
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

    # ---- 3. 道具资产图（空间物体拆出，作环境/尺寸锚点；手持道具作外观参考）----
    prop_refs: list[str] = []
    space_refs: list[str] = []
    try:
        from shared.assets_bank import PropAssetsBank
        pbank = PropAssetsBank()
        pbank.load()
        # ---- 先收集所有命中 (pid, hit)，再过滤别名嵌套/重复 ----
        # 同一件道具不得注入两件参考图：匕首同时命中「刺客弯刀」「刺客匕首」时
        # （后者的「寒光匕首」是更具体别名），只保留命中词最长的那件。
        # 08-16 刺客弯刀残留：register_prop 别名去重防源头，此处匹配层兜底。
        hits: list[tuple[str, str]] = []
        for pid in pbank.get_all_identifiers():
            if not pid:
                continue
            entry = pbank.get_prop(pid) or {}
            # 匹配源：道具名 + 道具 consistent_terms（别名）。
            # 只做道具名匹配会漏（如"弯刀"在 ff_desc 而道具名是"刺客弯刀"，
            # 但 consistent_terms 含"弯刀"可命中）。
            match_sources = [pid] + list(entry.get("consistent_terms", []) or [])
            # 命中词：identifier 精确命中优先，否则按别名长度降序（长词更具体，
            # 如「寒光匕首」优先于「匕首」）。
            hit = None
            if pid and (pid in search_text or pid in loc):
                hit = pid
            else:
                hit = next(
                    (t for t in sorted(match_sources, key=len, reverse=True)
                     if t and t != pid and (t in search_text or t in loc)),
                    None,
                )
            if hit:
                hits.append((pid, hit))

        # 过滤：hit 是另一命中的子串 → 同一件，丢弃（保留更具体别名的那件）；
        # 命中词完全相同时保留先出现的。
        kept = []
        for i, (pid, hit) in enumerate(hits):
            subsumed = False
            for j, (op, oh) in enumerate(hits):
                if op == pid:
                    continue
                if (hit == oh and j < i) or (hit in oh and len(hit) < len(oh)):
                    subsumed = True
                    break
            if subsumed:
                _log_r2v_collect(f"  道具「{pid}」命中词「{hit}」被更具体别名覆盖，视为同一件，跳过")
                continue
            kept.append((pid, hit))

        for pid, _ in kept:
            img = pbank.get_asset_image(pid)
            if not img:
                continue
            entry = pbank.get_prop(pid) or {}
            # v6.25：船/马车等空间物体拆到 space_refs（环境/尺寸锚点，提前到
            # 角色肖像前）——手持道具不决定画面空间比例，船才决定。
            if _is_space_object_prop(entry):
                if img not in space_refs:
                    space_refs.append(img)
                    _log_r2v_collect(
                        f"  空间物体「{pid}」匹配 → {Path(img).name}（环境/尺寸锚点，提前注入）"
                    )
                continue
            prop_refs.append(img)
            _log_r2v_collect(f"  道具「{pid}」匹配 → {Path(img).name}")
    except Exception as e:
        _log_r2v_collect(f"  道具资产收集异常: {e}")
        prop_refs = []

    # ---- 组装 ----
    # 首帧构图锚点存在：它已锚定正确时间/场景/人物站位/动作起点，替代场景资产图
    # （wide.png 是黄昏远景，与首帧的深青夜色冲突，会让视频时间漂移）。场景资产图
    # 只在无首帧时注入，作环境兜底。空间物体图（船/马车）紧随场景后、角色肖像前，
    # 作为尺寸/舞台锚点（船大小由它 + 文字锚点共同锁定）。
    #
    # v6.26（08-15）：旧版 limit=5 平面截断会丢尾部参考图（道具被挤掉）。
    # 改为类别分组 + 按当前 r2v 模型 media 上限无损压缩（wan2.7-r2v=5 /
    # happyhorse=9，注册表 shared.ref_compose）——超限类别拼参考表，
    # 参考图必须全部传过去。压缩后再生成「图N」清单（_build_r2v_purpose_list
    # 拿到的就是这份最终列表），编号与 provider media 顺序严格对齐。
    from shared.ref_compose import fit_refs, get_ref_limit
    from shared.model_caps import active_video_model
    # r2v 参考图上限按「当前 provider 实际提交 r2v 的模型」解析，
    # 用 active_video_model() 统一推导——volcengine/agent_plan/bailian/minimax
    # 都走同一份注册表，避免逐 provider 硬编码新增遗漏。
    r2v_model = active_video_model() or ""
    media_limit = get_ref_limit(r2v_model)
    # 首帧构图锚点永远原图直传占第 1 名额（构图/时间/站位基准，拼表会严重损失）
    head_refs = composition_refs[:1]
    buckets = [
        ("scene", ([scene_refs[0]] if scene_refs and not composition_refs else [])
         + list(space_refs)),
        ("chars", list(char_refs)),
        ("props", list(prop_refs)),
    ]
    from shared.paths import shot_work_dir
    sheet_dir = shot_work_dir(shot.get("index", 0))
    refs = head_refs + fit_refs(
        buckets, max(1, media_limit - len(head_refs)), sheet_dir, f"r2v{shot.get('index', 0)}",
    )
    _log_r2v_collect(
        f"r2v 参考图收集完成（镜头{shot.get('index', '?')}）: "
        f"首帧{len(composition_refs)} / 场景{len(scene_refs)} / "
        f"空间物体{len(space_refs)} / 角色{len(char_refs)} / "
        f"道具{len(prop_refs)} / 模型上限{media_limit} / 传出{len(refs)}张"
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
