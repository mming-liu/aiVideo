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
      - v6.62（09-06）：LLM 自我修正元文本（「抽出匕首收回腰间？不，原文未拔，
        他推臂一送…」——「？不，」是 LLM 写给校验器的内心独白，直接进了生成
        prompt）。仅删元短语本身，保留两侧正文重新接续。

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
    # LLM 自我修正元文本（治 shot6「…收回腰间？不，原文未拔，他推臂一送」）
    # 「X？不，[元说明]，正解Y」→ 「X，Y」；逐段处理，幂等（修完不再命中）。
    _META_FIX_PATTERNS = [
        # ？不，原文未拔， → （删除元说明到下一个逗号）
        re.compile(r"？不，[^，。；\n]{0,20}原文[^，。；\n]{0,20}，"),
        re.compile(r"？不，[^，。；\n]{0,20}(?:应是|应该|其实|改为|实为|正解)[^，。；\n]{0,20}，"),
        re.compile(r"？不，"),
    ]
    for pat in _META_FIX_PATTERNS:
        text = pat.sub("，", text)
    # v6.62（09-06）：修复历史损坏文本——boat_repl 旧版容量注入拼坏过的
    # 「可容可容2-3人（杨旭、听香、炭炉已倾倒…」（双「可容」+ 未闭合括号把
    # 后文吞进括号、句意断在半截）。规范为「可容2-3人），」并接回正文。
    # 名单边界用「顿号分隔短名段」严格模式——「（杨旭、听香、」命中，「炭炉
    # 已倾倒」因「已」不属于顿号序列自然排除，正文零吞失。
    # 单「可容」版本同修（「可容2-3人（杨旭、听香、炭炉…」）。
    _CAP_LIST = r"可容([0-9０-９.\-－~～]+)人?（((?:[一-龥]{1,4}、)+)"
    text = re.sub(r"可容可容\s*" + _CAP_LIST[2:], r"可容\1人），", text)
    text = re.sub(_CAP_LIST, r"可容\1人），", text)
    # 「可容N人杨旭、听香、炭炉已倾倒…」——历史修复后残留的无括号人名列表
    # （顿号短段 + 非顿号正文开头），删顿号段保留正文（「炭炉已倾倒」）。
    text = re.sub(
        r"(可容[0-9０-９.\-－~～]+人)\s*(?:[一-龥]{1,6}、){1,4}(?=[一-龥])",
        r"\1", text,
    )
    # v6.62（09-06）：防叠兜底（历史坏文本）——「舴艋小舟」被规则1 整词替换后
    # 产出「舴艋无篷舴艋舟」（前缀形制词 + 替换文本重复）。规范为「无篷舴艋舟」。
    text = text.replace("舴艋无篷舴艋舟", "无篷舴艋舟").replace("无篷无篷舴艋舟", "无篷舴艋舟")
    # 通用括号平衡兜底：剥「可容」注入产生的孤立未闭合括号残段
    # （「（杨旭、听香、炭炉已倾倒」类——括号内无闭合直接接正文）。
    # 做法：逐个「（」统计未闭合位置，若该括号后面直到句末/长距离都没有「）」，
    # 删掉「（」字符本身（保留内容，避免吞正文）。
    if text.count("（") > text.count("）"):
        out = []
        depth = 0
        for ch in text:
            if ch == "（":
                depth += 1
                if depth > text.count("）"):  # 多余的「（」丢弃
                    depth -= 1
                    continue
            elif ch == "）":
                depth = max(0, depth - 1)
            out.append(ch)
        text = "".join(out)
    text = re.sub(r"([，、。；：])[，、。；：]+", r"\1", text)
    return text.strip("，、。；： ")


__all__ = [
    "strip_spatial_anchors",
    "strip_debug_markers",
]
