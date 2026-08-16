"""shared.shot_selector - 分镜/帧类型选择器解析

把命令行 "--shots 3,5-7,9" 形式的字符串解析成镜头 index 集合，
"--frames ff,lf,kf0" 形式的字符串解析成帧类型集合，
供 step02a / step02b 的指定镜头/指定帧生成能力复用。

约定：
  - index 从 0 开始；0 = hook（钩子镜头），1+ = 正文镜头
  - 支持单个 index、闭区间（a-b）及逗号混排
  - 空白与重复自动忽略/去重；非法输入抛 ValueError
"""
from __future__ import annotations


def parse_shot_selector(spec: str | None) -> set[int] | None:
    """解析分镜选择器字符串。

    Args:
        spec: 如 "3,5-7,9"；None 或空串表示不限制（返回 None = 全部镜头）

    Returns:
        set[int] 指定镜头 index 集合；None 表示全部镜头

    Raises:
        ValueError: 含非法 token（非数字 / 区间起点大于终点 / 负数）
    """
    if spec is None:
        return None
    spec = spec.strip()
    if not spec:
        return None

    indices: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s.strip()), int(hi_s.strip())
            if lo < 0 or hi < 0:
                raise ValueError(f"非法镜头区间（负数）: {part}")
            if lo > hi:
                raise ValueError(f"非法镜头区间（起点>终点）: {part}")
            indices.update(range(lo, hi + 1))
        else:
            val = int(part)
            if val < 0:
                raise ValueError(f"非法镜头序号（负数）: {part}")
            indices.add(val)

    return indices or None


def parse_frame_selector(spec: str | None) -> set[str] | None:
    """解析帧类型选择器字符串。

    Args:
        spec: 如 "ff,lf,kf0"；None 或空串表示不限制（返回 None = 默认 Mode A/B 行为）

    合法 token（逗号分隔、大小写不敏感）：
      - ff        首帧（first_frame.png）
      - lf        尾帧（last_frame.png）
      - kf        全部中间关键帧（keyframe_{i}.png 全量）
      - kf0/kf1/...  指定序号的中间关键帧（keyframe_0.png / keyframe_1.png ...）

    Returns:
        set[str] 帧类型 token 集合；None 表示不限制

    Raises:
        ValueError: 含非法 token（非 ff/lf/kf/kfN 形式）
    """
    if spec is None:
        return None
    spec = spec.strip()
    if not spec:
        return None

    tokens: set[str] = set()
    for part in spec.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part in ("ff", "lf", "kf"):
            tokens.add(part)
        elif part.startswith("kf") and part[2:].isdigit():
            tokens.add(part)
        else:
            raise ValueError(f"非法帧类型: {part}（支持 ff/lf/kf/kf0/kf1...）")

    return tokens or None
