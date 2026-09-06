"""图片 size 收敛 — Seedream 档位/像素值合法性兜底（volcengine / agent_plan 共用）.

Seedream 不同档位像素范围差异大（4.0 下限 921600，4.5/lite 下限 3686400），
请求 size 越界时须收敛到模型 [px_min, px_max] 区间，否则提交即 400。
能力区间来自 shared/model_caps.SEEDREAM_CAPS（勿在此处写死模型数值）。
"""

import re


def fit_pixel_size(w: int, h: int, pmin: int, pmax: int) -> str:
    """等比缩放宽高像素值到总像素 [pmin, pmax] 区间（偶数对齐，保持宽高比）.

    取整后可能仍越界，微调较短边逐步收敛；落到区间即可（比例仅漂移几个像素）。
    """
    total = w * h
    if pmin <= total <= pmax:
        return f"{w}x{h}"
    import math
    target = pmin if total < pmin else pmax
    scale = math.sqrt(target / total)
    w2 = max(16, int(w * scale) // 2 * 2)
    h2 = max(16, int(h * scale) // 2 * 2)
    for _ in range(8):
        t = w2 * h2
        if pmin <= t <= pmax:
            break
        if t < pmin:
            if h2 <= w2:
                h2 += 2
            else:
                w2 += 2
        else:
            if h2 >= w2:
                h2 -= 2
            else:
                w2 -= 2
    return f"{w2}x{h2}"


def fit_tier(size: str, tiers) -> str:
    """请求档位不在模型支持列表时，按档位序（面积单调）就近收敛.

    序号取档位数值前缀（"1K"=1.0 / "1.5K"=1.5 / "2K"=2 …），同距取较小档；
    请求无法解析时按 2K 对齐（与历史固定回退 "2K" 行为一致）。
    返回 tiers 中的原样写法（保持注册表大小写），无法收敛时原样返回请求值。
    """
    tiers = tuple(t for t in (tiers or ()) if t)
    if not tiers:
        return size

    def _num(s: str):
        m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*k", s.strip().lower())
        try:
            return float(m.group(1)) if m else None
        except ValueError:
            return None

    req_num = _num(size)
    if req_num is None:
        req_num = 2.0
    return min(tiers, key=lambda t: (abs((_num(t) if _num(t) is not None else 0.0) - req_num),
                                     _num(t) if _num(t) is not None else 0.0))
