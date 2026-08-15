"""通用参考图上限适配 — 模型注册表 + 无丢失压缩.

设计目标（2026-08-15 需求）：参考图一个都不能丢，不同模型/场景用不同方法：
  1. 总数 ≤ 模型上限        → 原图直传（保真优先）
  2. 超上限且有类别分组      → 类别内拼参考表（每类同质：环境/人物/道具）
  3. 超上限且无类别信息      → 保留前 N-1 张原图 + 尾部拼 1 张（provider 兜底）
  4. 模型只收单张            → 全部拼成 1 张大参考表

市面模型参考图上限注册表（子串匹配，大小写不敏感，先匹配先命中）：
  - qwen-image 系列（2.0/3.0/edit）: I2I 0~3 张（API 实测 "supports 0~3 image items"）
  - wan2.7-image / -pro: 9 张（百炼文档）
  - wan2.7-r2v / wan2.6-r2v: media 5 个（百炼文档）
  - happyhorse-1.1-r2v: media 9 个（百炼文档）
  - MiniMax-H3: 全能参考 图≤9 / 混合≤12 文件；首尾帧专用槽 0~2（官方文档 2026-08）
  - Seedance 2.0: 参考素材约 12 个；Seedance 2.5: 图≤30、混合≤50（官方对比表 2026-08）

未注册模型回退到环境变量 IMAGE_MAX_REFS（默认 3）。
"""

import os
from pathlib import Path

# (模型名子串, 参考图上限)。顺序敏感：具体在前，泛化在后。
MODEL_REF_LIMITS: list[tuple[str, int]] = [
    # --- 图片模型（I2I 参考图）---
    ("qwen-image", 3),
    ("wan2.7-image", 9),
    ("wan2.6-image", 9),
    # --- 视频模型（r2v 参考 media）---
    ("happyhorse", 9),
    ("wan2.7-r2v", 5),
    ("wan2.6-r2v", 5),
    ("minimax-h3", 9),
    ("seedance-2.5", 30),
    ("seedance-2.0", 12),
    ("seedance", 12),
]


def get_ref_limit(model: str | None, default: int | None = None) -> int:
    """按模型名查参考图上限；未注册模型用 IMAGE_MAX_REFS 或 default."""
    if default is None:
        env = os.getenv("IMAGE_MAX_REFS")
        default = int(env) if env else 3
    if not model:
        return default
    m = model.lower()
    for pat, lim in MODEL_REF_LIMITS:
        if pat in m:
            return lim
    return default


_SHEET_LABELS = {
    "chars": "人物形象参考表（多角色肖像横向拼合，只取各角色长相与服装，禁止当作画面构图）",
    "scene": "场景环境参考表（多张环境图拼合，只取环境光线与布局，禁止当作画面构图）",
    "props": "道具外观参考表（多件道具拼合，只取各道具外观，禁止当作画面构图）",
    "all": "综合参考表（多张参考图拼合，只取各单项特征，禁止当作画面构图）",
}


def describe_ref(path: str) -> str:
    """按路径判定参考图用途标签（供 prompt 里的「图N」清单复用）.

    识别约定：ref_sheet_*_{类别}.png（fit_refs 产物）、scenes_bank/、
    characters_bank/、props_bank/ 目录、first_frame/last_frame 帧名。
    """
    if not path:
        return ""
    p = str(path).replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    # 拼图表：ref_sheet_{tag}_{group}.png
    if name.startswith("ref_sheet_"):
        parts = name[:-4].split("_")  # 去 .png
        group = parts[-1] if len(parts) >= 3 else "all"
        return _SHEET_LABELS.get(group, _SHEET_LABELS["all"])
    if name == "first_frame.png":
        return "视频起点画面（首帧，构图与人物站位基准）"
    if name == "last_frame.png":
        return "视频终点画面（尾帧）"
    if "/scenes_bank/" in p:
        return "场景环境参考（只取环境光线与布局，禁止把图中内容当人物）"
    if "/characters_bank/" in p:
        return "人物形象参考（只取该角色的五官、发型与服装）"
    if "/props_bank/" in p:
        return "道具/空间物体外观参考（只取该物体外观与比例）"
    return "一致性参考（人物/场景/道具特征以该图为准）"


def build_ref_manifest(paths: list[str], numbered: bool = True) -> str:
    """生成「图N：用途」参考图清单文本（prompt 注入用）.

    numbered=True 用「图1/图2」（wan2.7-r2v 图N 引用格式、MiniMax/Seedance
    自然语言指代通用）；False 用「参考图1」（图生图 prompt 风格）。
    """
    entries = []
    for i, p in enumerate(paths or [], start=1):
        label = describe_ref(p)
        if label:
            prefix = f"图{i}" if numbered else f"参考图{i}"
            entries.append(f"{prefix}：{label}")
    if not entries:
        return ""
    head = "【参考图说明】以下参考图各司其职，编号严格对应参考图列表顺序，禁止混用用途："
    return head + "；".join(entries)


def active_image_ref_limit() -> int:
    """当前 IMAGE_PROVIDER 对应主模型的参考图上限（bridge 层组装时用）."""
    try:
        from shared import config
        model = None
        if config.IMAGE_PROVIDER == "bailian":
            model = config.BAILIAN_T2I_MODEL
        elif config.IMAGE_PROVIDER == "agnes":
            model = config.AGNES_IMAGE_MODEL
        return get_ref_limit(model)
    except Exception:
        return get_ref_limit(None)


def _stitch(paths: list[str], output_path: str) -> str:
    from shared.portrait_stitch import stitch_portraits
    return stitch_portraits(paths, output_path)


def fit_refs(
    buckets: list[tuple[str, list[str]]],
    limit: int,
    sheet_dir: Path | str,
    sheet_tag: str,
) -> list[str]:
    """把分组参考图无损压缩到 limit 个名额.

    Args:
        buckets: [(类别名, [路径...]), ...] 按优先级降序（前面的类别保留更多原图名额）
        limit:   模型参考图上限
        sheet_dir: 参考表落盘目录（如 vimax_working/shots/<idx>）
        sheet_tag: 参考表文件名前缀（如 "ff"/"lf"/"shot3"），防不同帧互相覆盖

    Returns:
        ≤limit 个路径的列表。极端兜底（拼图全部失败）时裁剪到 limit 并保留
        优先级在前的图，绝不返回超过 limit 的列表（防 provider 400）。
    """
    seen: set[str] = set()
    clean: list[tuple[str, list[str]]] = []
    flat: list[str] = []
    for name, items in buckets:
        bucket: list[str] = []
        for p in items or []:
            if p and p not in seen and Path(p).exists():
                seen.add(p)
                bucket.append(p)
                flat.append(p)
        if bucket:
            clean.append((name, bucket))

    if len(flat) <= limit:
        return flat
    if limit <= 0:
        return []

    sheet_dir = Path(sheet_dir)
    try:
        sheet_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def _sheet(name: str, paths: list[str]) -> str:
        out = str(sheet_dir / f"ref_sheet_{sheet_tag}_{name}.png")
        stitched = _stitch(paths, out)
        return stitched if stitched else ""

    # 单张上限：全部拼成一张大参考表
    if limit == 1:
        merged = _sheet("all", flat)
        if merged:
            return [merged]
        return flat[:1]

    # 类别数超过名额：前面类别各占 1 名额，尾部类别合并成一组共享 1 名额
    groups: list[tuple[str, list[str]]] = clean[: limit - 1]
    if len(clean) > limit - 1:
        tail = [p for _, items in clean[limit - 1:] for p in items]
        groups.append(("mix", tail))

    # 基础名额：每组 1 个；剩余名额分给「图最多」的组（让更多原图保真直传）
    slots = [1] * len(groups)
    leftover = limit - len(groups)
    while leftover > 0:
        cand = max(range(len(groups)), key=lambda i: len(groups[i][1]) - slots[i])
        if len(groups[cand][1]) - slots[cand] <= 0:
            break
        slots[cand] += 1
        leftover -= 1

    composed: list[str] = []
    for (name, items), s in zip(groups, slots):
        if len(items) <= s:
            composed += items
            continue
        keep, tail = items[: s - 1], items[s - 1:]
        if len(tail) >= 2:
            stitched = _sheet(name, tail)
            composed += keep + ([stitched] if stitched else tail[:1])
        else:
            composed += keep + tail
    # 拼图异常兜底：裁剪保优先级（比 400 中断生成轻）
    if len(composed) > limit:
        composed = composed[:limit]
    return composed


def fit_flat(
    paths: list[str],
    limit: int,
    sheet_dir: Path | str,
    sheet_tag: str,
) -> list[str]:
    """无类别信息的兜底压缩：保留前 limit-1 张原图 + 尾部拼 1 张参考表."""
    valid = [p for p in paths or [] if p and Path(p).exists()]
    if len(valid) <= limit:
        return valid
    if limit <= 1:
        return fit_refs([("all", valid)], limit, sheet_dir, sheet_tag)
    keep = limit - 1
    try:
        sheet_dir = Path(sheet_dir)
        sheet_dir.mkdir(parents=True, exist_ok=True)
        out = str(sheet_dir / f"ref_sheet_{sheet_tag}_extra.png")
        from shared.portrait_stitch import stitch_portraits
        stitched = stitch_portraits(valid[keep:], out)
        if stitched:
            return valid[:keep] + [stitched]
    except Exception:
        pass
    return valid[:limit]
