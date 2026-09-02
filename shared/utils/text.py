"""文本处理工具函数（跨阶段共用）.

v6.51: 从各处散落的文本清理逻辑中抽取，统一入口。
"""
from __future__ import annotations

import re


# 空间锚点标记：LLM 在 prompt/ff_desc 里标注的「（画中船中）」「（画中）」
# 「（画中水面）」「（画中甲板）」类提示符。
# 这些是给代码看的空间标记，对图片/视频模型无意义且污染 prompt。
_SPATIAL_ANCHOR_PATTERN = re.compile(r"[（(][画畫]中[^）)]*[）)]")


def strip_spatial_anchors(text: str) -> str:
    """清理文本中的空间锚点标记（（画中X）/ (画中X)）.

    幂等：多次调用结果一致。
    不区分全角/半角括号。

    例：
      '一手按稳船舷（画中船中），刀尖朝下' → '一手按稳船舷，刀尖朝下'
      '黑衣人（画左）手持匕首（画中）' → '黑衣人手持匕首'
    """
    if not text:
        return text
    # 去掉标记后可能残留多余空格/双标点，一起清理
    cleaned = _SPATIAL_ANCHOR_PATTERN.sub("", text)
    # 清理 "（画中X）," → ","  这类残留标点拼接
    cleaned = re.sub(r"([，、。；：])[，、。；：]+", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip("，、。；： ")


def strip_debug_markers(text: str) -> str:
    """清理 prompt 中的调试/元信息标记.

    清理项：
      - 「（AI生成）」「(AI生成)」
      - 「（幅度极小）」「（测试）」类调试注释

    幂等。
    """
    if not text:
        return text
    _DEBUG_PATTERNS = [
        re.compile(r"[（(]AI生成[）)]"),
        re.compile(r"[（(]幅度极小[）)]"),
        re.compile(r"[（(]测试[）)]"),
        re.compile(r"[（(]debug[）)]", re.IGNORECASE),
    ]
    for pat in _DEBUG_PATTERNS:
        text = pat.sub("", text)
    text = re.sub(r"([，、。；：])[，、。；：]+", r"\1", text)
    return text.strip("，、。；： ")


__all__ = [
    "strip_spatial_anchors",
    "strip_debug_markers",
]
