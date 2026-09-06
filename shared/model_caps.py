"""模型能力注册表 — Seedream 图片 / Seedance 视频 / 当前供应商视频能力.

不同模型档位的参数约束差异很大（时长/分辨率/参考图/有声/像素范围），
provider 生成请求前先按模型查表，避免用全局固定常量导致低版本模型报错、
高版本模型参数浪费。值全部来自火山方舟官方文档（2026-08 核对）：

- 图片生成 API（https://www.volcengine.com/docs/82379/1541523）：
  - Seedream 5.0 pro：档位 1K/1.5K/2K；像素 [1280x720(921600), 2048x2048x1.1025(4624220)]；参考图 ≤10
  - Seedream 5.0 lite：档位 2K/3K/4K；像素 [2560x1440(3686400), 4096x4096(16777216)]；参考图 ≤14
  - Seedream 4.5：档位 2K/4K；像素 [2560x1440(3686400), 4096x4096(16777216)]；参考图 ≤14
  - Seedream 4.0：档位 1K/2K/4K；像素 [1280x720(921600), 4096x4096(16777216)]；参考图 ≤14
  - 所有档位均不支持 negative_prompt / seed 参数
- 创建视频生成任务（https://www.volcengine.com/docs/82379/1520757）：
  - duration：2.5 [4,30]；2.0 系列 [4,15]；1.5 pro [4,12]；1.0 系列 [2,12]（可 -1 智能选择）
  - resolution：2.5 仅 480p/720p；2.0 480p~4k；2.0 fast/mini 仅 480p/720p；1.5 pro / 1.0 480p~1080p
  - role=reference_image（图生视频-参考图 / 全模态参考）：仅 2.5、2.0 系列
  - generate_audio（有声视频）：仅 2.5、2.0 系列、1.5 pro
  - Seedance 2.5 在首帧/首尾帧场景 ratio 仅支持 adaptive
- MiniMax 视频（国际站 api.minimax.io，2026-08 核对）：
  - v1 Hailuo（T2V-01/I2V-01/Hailuo-02 等）：01 系列或 1080P 仅 6s；768P 可 6 或 10s；无原生语音
  - v2 MiniMax-H3：duration [4,15]；支持首尾帧/多关键帧/参考图/参考音频；有声

底部「当前供应商视频能力」helper 供 step01 等阶段在生成前按
VIDEO_PROVIDER + VIDEO_REFERENCE_MODE 解析实际渲染分镜的视频模型
（单镜时长上限 / 是否原生有声 / 对白发声指令风格），使上游脚本也能跟模型走。

匹配策略与 shared/ref_compose.MODEL_REF_LIMITS 一致：模型 ID 经
shared.model_alias.normalize_model_name 规范化（小写 + 点号/下划线→破折号）后
子串匹配，顺序敏感（具体档位在前、泛化在后），未命中返回宽松默认值（按能力最弱档位兜底，
保证小尺寸/低版本请求不被误杀）。
"""

import os
from dataclasses import dataclass

from shared.model_alias import normalize_model_name


# ---------------------------------------------------------------------------
# 能力结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeedreamCap:
    """Seedream 文生图/图生图能力.

    tiers:   方式 1 支持的尺寸档位（"1K"/"2K"/...）；不在档位则用像素值方式 2
    px_min:  方式 2 最小总像素（宽x高乘积）
    px_max:  方式 2 最大总像素
    max_refs:参考图数量上限（图生图 image 数组）
    """
    tiers: tuple[str, ...]
    px_min: int
    px_max: int
    max_refs: int
    supports_negative_prompt: bool = False  # 官方图片生成 API 不支持
    supports_seed: bool = False             # 官方图片生成 API 不支持


@dataclass(frozen=True)
class SeedanceCap:
    """Seedance 视频生成能力.

    duration_range: (min, max) 秒；服务端还接受 -1 智能选择
    resolutions:    支持的分辨率（升序）
    supports_reference_image:  role=reference_image（图生视频-参考图/全模态参考）
    supports_generate_audio:   generate_audio 有声视频
    supports_reference_audio:  role=reference_audio 参考音频
    supports_seed:             seed 参数（可复现）
    i2v_frames_ratio_adaptive: 首帧/首尾帧场景 ratio 是否强制 adaptive（Seedance 2.5）
    """
    duration_range: tuple[int, int]
    resolutions: tuple[str, ...]
    supports_reference_image: bool
    supports_generate_audio: bool
    supports_reference_audio: bool = False
    supports_seed: bool = False
    i2v_frames_ratio_adaptive: bool = False


# ---------------------------------------------------------------------------
# 注册表（顺序敏感：具体在前，泛化在后）
# ---------------------------------------------------------------------------

SEEDREAM_CAPS: list[tuple[str, SeedreamCap]] = [
    # --- 火山免费预置模型（带日期后缀，先匹配先命中）---
    # max_refs 与 ref_compose.MODEL_REF_LIMITS 保持一致（免费档多参考图会贴/超 180s
    # 超时线，收紧到 3）；档位/像素沿用各自基座（4.0/4.5/5.0-lite）官方能力。
    ("doubao-seedream-5-0-lite-260128", SeedreamCap(("2K", "3K", "4K"), 3_686_400, 16_777_216, 3)),
    ("doubao-seedream-4-5-251128", SeedreamCap(("2K", "4K"), 3_686_400, 16_777_216, 3)),
    ("doubao-seedream-4-0-250828", SeedreamCap(("1K", "2K", "4K"), 921_600, 16_777_216, 3)),
    ("seedream-5-0-pro", SeedreamCap(("1K", "1.5K", "2K"), 921_600, 4_624_220, 10)),
    ("seedream-5-0-lite", SeedreamCap(("2K", "3K", "4K"), 3_686_400, 16_777_216, 14)),
    # 点号/下划线写法（doubao-seedream-5.0.lite、doubao_seedream_4_5）由
    # shared.model_alias.normalize_model_name 归一为破折号后在此命中，
    # 不再保留点号变体条目（v6.60.1 硬编码清理收敛）。
    ("seedream-4-5", SeedreamCap(("2K", "4K"), 3_686_400, 16_777_216, 14)),
    ("seedream-4-0", SeedreamCap(("1K", "2K", "4K"), 921_600, 16_777_216, 14)),
]

SEEDANCE_CAPS: list[tuple[str, SeedanceCap]] = [
    # 参数顺序：(duration, resolutions, supports_reference_image, supports_generate_audio,
    #            i2v_frames_ratio_adaptive, supports_reference_audio, supports_seed)
    # 2.5/2.0 系列支持参考音频（按官方文档全模态参考生视频场景）
    ("seedance-2-5", SeedanceCap((4, 30), ("480p", "720p"), True, True, True, True, True)),
    ("seedance-2-0-fast", SeedanceCap((4, 15), ("480p", "720p"), True, True, False, True, False)),
    ("seedance-2-0-mini", SeedanceCap((4, 15), ("480p", "720p"), True, True, False, True, False)),
    ("seedance-2-0", SeedanceCap((4, 15), ("480p", "720p", "1080p", "4k"), True, True, False, True, False)),
    ("seedance-1-5-pro", SeedanceCap((4, 12), ("480p", "720p", "1080p"), False, True, False, False, False)),
    ("seedance-1-5", SeedanceCap((4, 12), ("480p", "720p", "1080p"), False, False, False, False, False)),
    ("seedance-1-0", SeedanceCap((2, 12), ("480p", "720p", "1080p"), False, False, False, False, False)),
]

# 未命中注册表时的兜底：按能力最弱档位，小尺寸/低版本请求不被误杀
DEFAULT_SEEDREAM_CAP = SeedreamCap(("1K", "2K", "4K"), 921_600, 16_777_216, 14)
DEFAULT_SEEDANCE_CAP = SeedanceCap((4, 12), ("480p", "720p", "1080p"), False, False, False, False, False)


# ---------------------------------------------------------------------------
# 查找
# ---------------------------------------------------------------------------

def get_seedream_cap(model: str | None, default: SeedreamCap | None = None) -> SeedreamCap:
    """按模型 ID 子串查 Seedream 能力；未注册返回宽松默认."""
    if not model:
        return default or DEFAULT_SEEDREAM_CAP
    m = normalize_model_name(model)
    for pat, cap in SEEDREAM_CAPS:
        if pat in m:
            return cap
    return default or DEFAULT_SEEDREAM_CAP


def get_seedance_cap(model: str | None, default: SeedanceCap | None = None) -> SeedanceCap:
    """按模型 ID 子串查 Seedance 能力；未注册返回宽松默认."""
    if not model:
        return default or DEFAULT_SEEDANCE_CAP
    m = normalize_model_name(model)
    for pat, cap in SEEDANCE_CAPS:
        if pat in m:
            return cap
    return default or DEFAULT_SEEDANCE_CAP


# ---------------------------------------------------------------------------
# 当前供应商视频模型能力（step01 等上游按 VIDEO_PROVIDER+VIDEO_REFERENCE_MODE 解析）
# ---------------------------------------------------------------------------

def active_video_model() -> str | None:
    """当前 VIDEO_PROVIDER + VIDEO_REFERENCE_MODE 下实际渲染分镜的视频模型 ID.

    - volcengine：text→T2V 模型；image/r2v→I2V 模型
    - minimax：r2v→H3；text→T2V（v1 Hailuo）；image→I2V（v1 Hailuo）
    - bailian：text→T2V；image→I2V；r2v→R2V（wan2.7/happyhorse 官方时长上限均为 15s）
    - agent_plan：text→T2V；image→I2V；r2v→I2V（Agent Plan 默认模型）
    - dashscope / agnes：返回各自主模型
    """
    provider = os.getenv("VIDEO_PROVIDER", "bailian")
    mode = (os.getenv("VIDEO_REFERENCE_MODE", "text") or "text").strip().lower()
    if provider == "agent_plan":
        return os.getenv("AGENT_PLAN_T2V_MODEL", "doubao-seedance-2-0")
    if provider == "volcengine":
        if mode == "text":
            return os.getenv("VOLCENGINE_T2V_MODEL", "doubao-seedance-1-5-pro-251215")
        return os.getenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-5-pro-251215")
    if provider == "minimax":
        if mode == "r2v":
            return os.getenv("MINIMAX_H3_MODEL", "MiniMax-H3")
        if mode == "text":
            return os.getenv("MINIMAX_T2V_MODEL", "MiniMax-Hailuo-02")
        return os.getenv("MINIMAX_I2V_MODEL", "MiniMax-Hailuo-2.3-Fast")
    if provider == "bailian":
        if mode == "r2v":
            return os.getenv("BAILIAN_R2V_MODEL", "wan2.7-r2v-2026-06-12")
        if mode == "image":
            return os.getenv("BAILIAN_I2V_MODEL", "wan2.7-i2v-2026-04-25")
        return os.getenv("BAILIAN_T2V_MODEL", "wan2.7-t2v-2026-06-12")
    if provider == "dashscope":
        return os.getenv("DASHSCOPE_VIDEO_MODEL", "wan2.7-t2v")
    if provider == "agnes":
        return os.getenv("AGNES_VIDEO_MODEL", "agnes-video-v2.0")
    return None


# 跨供应商视频时长上限注册表（官方文档 2026-08 核对，子串匹配，具体在前）：
#   - 百炼/dashscope wan2.7 全系列、happyhorse：官方时长 [2,15]s
#   - Agnes：num_frames ≤441（8n+1），@24fps 最大 ≈18.4s → 取 18
_VIDEO_DURATION_CAPS: list[tuple[str, int]] = [
    # key 一律破折号形态（查表输入经 normalize_model_name 归一）
    ("wan2-7", 15),
    ("happyhorse", 15),
    ("agnes-video", 18),
]


# 视频时长区间注册表（下限, 上限）——仅官方声明了下限的模型才入表；未命中 (0, 0) = 不夹取。
#   - MiniMax H3（v2）：官方 duration [4,15]
_VIDEO_DURATION_RANGES: list[tuple[str, tuple[int, int]]] = [
    ("minimax-h3", (4, 15)),
]

# v1 Hailuo 时长规则（官方：01 系列或 1080P 仅 6s；768P/512P 可 6 或 10s）。
# model_caps 与 minimax_provider._resolve_v1_duration 共用同一组数字，禁止各自写死。
MINIMAX_V1_ONLY_DURATION = 6
MINIMAX_V1_MAX_DURATION = 10


# KF2V（首尾帧）模型能力集合（dashscope HTTP 旧链路，模型 ID 精确匹配）
KF2V_CAPABLE_MODELS = frozenset({"wan2.2-kf2v-flash", "wanx2.1-kf2v-plus", "wan2.7-i2v"})
KF2V_ONLY_MODELS = frozenset({"wan2.2-kf2v-flash", "wanx2.1-kf2v-plus"})


# Agnes 视频 num_frames 约束（num_frames = 8n+1；@24fps 441 帧 ≈ 18.4s →
# _VIDEO_DURATION_CAPS 的 agnes-video=18 即由此换算，两处保持同源）
AGNES_VIDEO_NUM_FRAMES_MOD = 8
AGNES_VIDEO_MAX_NUM_FRAMES = 441


# bailian 视频参数支持位（按模型子串查；未命中默认全部支持）。
# happyhorse 系官方不支持 negative_prompt / audio_url（wan2.7 系支持）——
# 支持/不支持属模型能力，禁止在 provider 里按模型名散写。
_VIDEO_PARAM_UNSUPPORTED: list[tuple[str, frozenset]] = [
    ("happyhorse", frozenset({"negative_prompt", "audio_url"})),
]
_VIDEO_KNOWN_PARAMS = frozenset({"negative_prompt", "audio_url"})


def video_supports_param(model: str | None, param: str) -> bool:
    """按模型 ID 查视频 API 参数支持位；未注册模型默认支持（True）.

    用于 bailian provider 提交前的参数门控（happyhorse 不吃 negative_prompt）。
    """
    if param not in _VIDEO_KNOWN_PARAMS:
        raise ValueError(f"未知视频参数支持位: {param}（已知: {sorted(_VIDEO_KNOWN_PARAMS)}）")
    if not model:
        return True
    m = normalize_model_name(model)
    for pat, unsupported in _VIDEO_PARAM_UNSUPPORTED:
        if pat in m:
            return param not in unsupported
    return True


# ---------------------------------------------------------------------------
# Seedance 系 provider 共用推导（volcengine / agent_plan 视频提交前调用；
# 2026-09 从两 provider 的逐字复制实现上移，只搬函数不改行为）
# ---------------------------------------------------------------------------

# 分辨率排序（升序），用于把推导档位收敛到模型允许的最高档
RES_ORDER = ("480p", "720p", "1080p", "4k")


def clamp_duration(value, cap) -> int:
    """夹到模型支持时长区间 [cap.duration_range]；解析失败回退区间中值."""
    lo, hi = cap.duration_range
    try:
        d = int(float(value))
    except (TypeError, ValueError):
        return (lo + hi) // 2
    return max(lo, min(hi, d))


def resolve_resolution(size: str | None, cap, override: str | None = None) -> str:
    """按模型能力推导分辨率：先按 size（如 720x1280）短边定档，再收敛到
    cap.resolutions 中「≤ 推导值」的最高档（如 2.5 只支持 720p，1080p 推导→720p）.
    可选 override：provider 的 *_VIDEO_RESOLUTION env 非空则强制该档位（仍按能力收敛）.
    """
    ov = (override or "").strip().lower()
    s = (size or "").strip().lower().replace("*", "x")
    if ov in RES_ORDER:
        derived = ov
    else:
        try:
            w, h = s.split("x")
            w, h = int(w), int(h)
        except (ValueError, AttributeError):
            derived = "720p"
        else:
            short = min(w, h)
            derived = "1080p" if short >= 1080 else ("720p" if short >= 720 else "480p")
    if derived in cap.resolutions:
        return derived
    best = cap.resolutions[0]
    for r in cap.resolutions:
        if RES_ORDER.index(r) <= RES_ORDER.index(derived):
            best = r
    return best


def model_duration_range(model: str | None) -> tuple[int, int]:
    """按模型 ID 子串查 (min, max) 时长区间；未注册返回 (0, 0)（不夹取）."""
    if not model:
        return (0, 0)
    m = normalize_model_name(model)
    for pat, rng in _VIDEO_DURATION_RANGES:
        if pat in m:
            return rng
    return (0, 0)


def _model_duration_cap(model: str | None) -> int:
    """按模型 ID 子串查视频时长上限；未注册返回 0（不夹取，保持现状）."""
    if not model:
        return 0
    m = normalize_model_name(model)
    for pat, cap in _VIDEO_DURATION_CAPS:
        if pat in m:
            return cap
    return 0


# prompt 长度上限注册表（火山方舟官方 Input Requirements，2026-08 核对）：
#   - Seedance 2.0/2.5（火山方舟 Doubao Seedance 2.0 提示词指南）：
#     输入长度限制 = 500 字符，tokens 限制 = 不超过 2000 tokens
#   - Seedance 1.x 系列：官方 Input Requirements 文档未声明 prompt 字符上限
#     （参 volcengine.com/docs/6492/2165104 输入要求表格），但建议保守到 500
#   - 百炼 wan2.7 全系列 / happyhorse：官方 prompt ≤5000 字符
#   - MiniMax H3（v2）：官方 ≤7000 字符
#   - MiniMax Hailuo（v1）：官方 ≤2000 字符
#   - Seedream / Agnes：官方未声明上限 → 0（不截断）
# 上限按字符数（不是 bytes）；provider 端用 _clip_prompt 按字符切。
# 注意：项目 prompt 经常超 500 字（角色站位清单+参考图+多段描述），到 Seedance
# 服务端会报 400。在 volcengine_provider 提交时已做截断兜底（bailian/minimax 也有）。
_PROMPT_LENGTH_CAPS: list[tuple[str, int]] = [
    # Seedance 2.0/2.5 严格 500 字符（官方提示词指南）
    ("seedance-2-0", 500),
    ("seedance-2-5", 500),
    # Seedance 1.x 未声明，按 2.x 同保守值
    ("seedance-1-5", 500),
    ("seedance-1-0", 500),
    ("seedance", 500),
    # 百炼 / MiniMax 已知（Hailuo 泛化匹配 v1 全家族含 403 回退 ID；
    # T2V-01/I2V-01 系不含 minimax/hailuo 子串，需单列——漏注册会从不截断漂移）
    ("wan2-7", 5000),
    ("happyhorse", 5000),
    ("minimax-h3", 7000),
    ("hailuo", 2000),
    ("t2v-01", 2000),
    ("i2v-01", 2000),
]


def _model_prompt_cap(model: str | None) -> int:
    """按模型 ID 子串查 prompt 长度上限；未注册返回 0（不截断）."""
    if not model:
        return 0
    m = normalize_model_name(model)
    for pat, cap in _PROMPT_LENGTH_CAPS:
        if pat in m:
            return cap
    return 0


def active_video_duration_cap() -> int:
    """当前视频模型单镜时长上限（秒）；0 = 无新增硬上限（沿用现有 ≤10s 引导）.

    step01 timecode 用此值夹取单镜时长，防止长对白/长 motion 分段生成的镜头
    超过模型上限被 provider 截断（如 Seedance 1.5 pro=12s、Hailuo v1 720P=6s）。
      volcengine → Seedance 能力表上限（1.5 pro=12 / 2.0=15 / 2.5=30 / 1.0=12）
      minimax    → r2v 走 H3（区间表 (4,15) 上限 15）；text/image 走 v1 Hailuo，
                   按 VIDEO_SIZE env 短边定档（≥1080→1080P=6s；≥720→768P 可 6/10→取 10；
                   01 系列→720P=6s；数字与 provider 同源，size 口径差异见分支内注释）
      bailian/dashscope/agnes → 按 _VIDEO_DURATION_CAPS 注册表（wan2.7=15 / happyhorse=15 /
                   agnes=18）；未注册模型返回 0（保持现状）
    """
    provider = os.getenv("VIDEO_PROVIDER", "bailian")
    if provider == "volcengine":
        return get_seedance_cap(active_video_model()).duration_range[1]
    if provider == "agent_plan":
        # 共享 Seedance 能力表（doubao-seedance-2-0 命中 seedance-2-0 → 15s）
        return get_seedance_cap(active_video_model()).duration_range[1]
    if provider == "minimax":
        model = active_video_model() or ""
        dmin, dmax = model_duration_range(model)
        if dmax:
            return dmax  # H3 等入区间表模型：上限即 cap[1]
        # v1 Hailuo：数字与 provider _resolve_v1_duration 共用 MINIMAX_V1_* 常量。
        # ⚠️ 两消费方取 size 的口径本就不同（行为保持，勿强行统一）：
        #   - 本函数（step01 全局预算）按 VIDEO_SIZE env 短边定档
        #   - provider 按 req.size 短边定档（step02b _make_request 固定发 720x1280→768P）；
        #     不读 MINIMAX_VIDEO_RESOLUTION env——provider 只在 size 解析失败才读它。
        #   VIDEO_SIZE 与 req.size 不一致时两边结论可能不同（如 VIDEO_SIZE=1440x2560
        #   判 1080P=6s，而 provider 实发 768P=10s），切 minimax 前先对齐 VIDEO_SIZE。
        if "01" in model:
            return MINIMAX_V1_ONLY_DURATION  # 01 系列固定 720P=6s
        size = (os.getenv("VIDEO_SIZE", "720x1280") or "720x1280").strip().lower().replace("*", "x")
        try:
            w, h = size.split("x")
            short = min(int(w), int(h))
        except (ValueError, AttributeError):
            short = 720
        # 1080P 仅 6s；768P/512P 可 6 或 10 → 取上限 10
        return MINIMAX_V1_ONLY_DURATION if short >= 1080 else MINIMAX_V1_MAX_DURATION
    # bailian / dashscope / agnes：按当前模型查跨供应商时长注册表
    return _model_duration_cap(active_video_model())


def active_video_prompt_cap() -> int:
    """当前视频模型的 prompt 长度上限（0 = 官方未声明/不截断）.

    provider 按此值截断（bailian=5000 / H3=7000 / Hailuo=2000）；step01 校验
    用同一值做单镜 prompt 长度预算，避免最终 prompt 超限被 provider 静默截断。
    """
    return _model_prompt_cap(active_video_model())


def active_video_supports_audio() -> bool:
    """当前视频模型是否自带语音（generate_audio / 原生发声）.

    False 表示成片无声：Seedance 1.0 系列、MiniMax v1 Hailuo。
    架构默认 VIDEO_MODEL_VOICE=1（对话交给视频模型），此处为 False 时必须提示
    换有声模型或设 VIDEO_MODEL_VOICE=0 走 step03 TTS，否则成片静音。
    """
    provider = os.getenv("VIDEO_PROVIDER", "bailian")
    mode = (os.getenv("VIDEO_REFERENCE_MODE", "text") or "text").strip().lower()
    if provider in ("volcengine", "agent_plan"):
        return get_seedance_cap(active_video_model()).supports_generate_audio
    if provider == "minimax":
        return mode == "r2v"  # 仅 H3 有声；v1 Hailuo 无声
    return True  # bailian(wan2.7 系列) / agnes / dashscope 默认有声


def active_video_uses_native_voice() -> bool:
    """对白发声是否由视频模型原生生成（无需 Agnes 风格「（用中文说）」指令）.

    volcengine / minimax / agent_plan = True（prompt 写自然对白，模型直接发声）；
    bailian / agnes / dashscope = False（沿用「（用中文说）」指令格式，历史行为）。
    """
    return os.getenv("VIDEO_PROVIDER", "bailian") in ("volcengine", "minimax", "agent_plan")


def active_video_is_seedance() -> bool:
    """当前视频供应商是否为火山引擎 Seedance.

    agent_plan 走同款 Seedance 模型，故同样归类为 Seedance 系。
    """
    return os.getenv("VIDEO_PROVIDER", "bailian") in ("volcengine", "agent_plan")


def active_video_is_h3() -> bool:
    """当前视频渲染是否走 MiniMax H3（r2v 全模态参考路径）."""
    provider = os.getenv("VIDEO_PROVIDER", "bailian")
    mode = (os.getenv("VIDEO_REFERENCE_MODE", "text") or "text").strip().lower()
    return provider == "minimax" and mode == "r2v"
