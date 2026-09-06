"""模型名标准化 — 消除各文件各自做 model.lower() 判定的问题.

所有 provider / model_caps / ref_compose 统一走规范化入口，
避免每个地方自己写字符串处理逻辑。

设计原则：
  - 轻量：只做规范化 + 家族识别，不改各模块的匹配逻辑
  - 兜底：不认识的模型原样返回，不抛异常，上层用自己的默认值
  - 向后兼容：各模块继续用子串匹配，只是先 normalize 一下再传

用法：
  from shared.model_alias import normalize_model_name, resolve_model_family

  model = normalize_model_name("Doubao-Seedance_2.5_Pro")
  # → "doubao-seedance-2-5-pro"

  family = resolve_model_family(model)
  # → "seedance"
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# 1. 规范化
# ---------------------------------------------------------------------------

def normalize_model_name(model: str) -> str:
    """模型名标准化：小写 + 标点统一（下划线/点号→破折号）+ 去首尾空白.

    为什么统一成破折号：
      - 火山方舟模型 ID 原生用破折号（doubao-seedream-4-5-250828）
      - 下划线写法（doubao_seedream_4_5）常见于环境变量
      - 点号写法（seedance 2.5 / seedance-2.5 / seedream-5.0.lite）常见于
        方舟开通管理展示与文档讨论
    统一成破折号后，各模块的子串匹配更可靠，注册表无需为点号变体保留
    重复条目（v6.60.1 前曾为 5.0 pro/lite 维护 4 条点号/双点号副本）。

    点号全量归一的安全性：本函数只用于「模型 ID」语境（查表/家族识别），
    不处理 URL/域名——若未来需要区分，应另开函数而非在此放宽。

    幂等：多次调用结果相同。空字符串原样返回。
    """
    if not model:
        return ""
    m = model.strip().lower()
    # 下划线 → 破折号
    m = m.replace("_", "-")
    # 点号 → 破折号（全量：版本号 2.5 → 2-5；尾段 .pro/.lite → -pro/-lite）
    m = m.replace(".", "-")
    # 连续破折号 → 单个
    m = re.sub(r"-{2,}", "-", m)
    return m.strip("-")


# ---------------------------------------------------------------------------
# 2. 家族识别
# ---------------------------------------------------------------------------

# (匹配子串, 家族名) — 按"具体在前"的顺序排列，先匹配先返回
_MODEL_FAMILIES: list[tuple[str, str]] = [
    # 视频模型
    ("seedance", "seedance"),     # 火山 Seedance 系列
    ("happyhorse", "happyhorse"),  # 阿里 HappyHorse
    ("wan2", "wan"),              # 阿里 Wan 2.x 系列
    ("hailuo", "hailuo"),         # MiniMax 海螺
    ("minimax-h3", "minimax-h3"),  # MiniMax H3
    ("h3", "minimax-h3"),         # MiniMax H3（简写）
    ("minimax", "minimax"),       # MiniMax 其他
    ("seedream", "seedream"),     # 火山 Seedream 图片系列
    ("qwen-image", "qwen-image"), # 通义万相图片
    ("qwen", "qwen"),             # 通义系列其他
    ("agnes", "agnes"),           # Agnes
    ("kling", "kling"),           # 可灵
    ("sora", "sora"),             # Sora
]


def resolve_model_family(model: str) -> str:
    """识别模型家族（seedance / seedream / wan / minimax / ...）.

    返回值是家族名（小写），用于能力查询/逻辑分派的快速路径。
    不认识的模型返回 "unknown"，上层走兜底逻辑。

    注意：返回"家族"不代表返回"标准 ID"——只是粗分类。
    细粒度能力（时长/分辨率/参考图数）仍走各模块的注册表。
    """
    if not model:
        return "unknown"
    norm = normalize_model_name(model)
    for pat, family in _MODEL_FAMILIES:
        if pat in norm:
            return family
    return "unknown"


# ---------------------------------------------------------------------------
# 3. 参考图上限（统一入口，内部仍走 ref_compose 注册表）
# ---------------------------------------------------------------------------

def get_ref_limit(model: str, default: int | None = None) -> int:
    """参考图上限（统一入口，内部调用 ref_compose.get_ref_limit）.

    这里只是一层薄封装，真实数据在 ref_compose.MODEL_REF_LIMITS。
    目的：让调用方不需要 import ref_compose，只 import model_alias 即可。
    """
    from shared.ref_compose import get_ref_limit as _ref
    return _ref(model, default)


# ---------------------------------------------------------------------------
# 4. 视频时长上限（统一入口，内部走 model_caps）
# ---------------------------------------------------------------------------

def get_video_duration_cap(model: str) -> tuple[int, int] | None:
    """视频时长上限（秒，(min, max)）；未注册返回 None，上层走自己的默认值.

    组合逻辑：
      - Seedance 系（volcengine/agent_plan 共用能力表）→ 命中条目的 duration_range
      - 其它（MiniMax H3 等区间表）→ model_caps.model_duration_range
      - 都未命中 → None
    ⚠️ v6.60.1 修复：旧实现调 `active_video_duration_cap(model)`——该函数无参数
    （自读 VIDEO_PROVIDER env），调用必 TypeError 且被裸 except 吞成 None；
    本封装建好后从未被调用，bug 因此从未暴露。
    """
    if not model:
        return None
    from shared.model_caps import SEEDANCE_CAPS, model_duration_range

    m = normalize_model_name(model)
    for pat, cap in SEEDANCE_CAPS:
        if pat in m:
            return cap.duration_range
    dmin, dmax = model_duration_range(m)
    if dmax:
        return (dmin, dmax)
    return None


__all__ = [
    "normalize_model_name",
    "resolve_model_family",
    "get_ref_limit",
    "get_video_duration_cap",
    "_MODEL_FAMILIES",
]
