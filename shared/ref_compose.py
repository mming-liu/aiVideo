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
  - Seedream 图片: 5.0 pro=10，5.0 lite / 4.5 / 4.0=14（官方图片生成 API，按模型档位细分）
  - MiniMax image-01: subject_reference 仅单张=1（官方文档）
  - MiniMax-H3: 全能参考 图≤9 / 混合≤12 文件；首尾帧专用槽 0~2（官方文档 2026-08）
  - Seedance 2.5: 图≤30（官方对比表 2026-08）；Seedance 2.0: 参考素材约 12
    注：模型 ID 用破折号（doubao-seedance-2-5-*），模式必须用 -2-5 而非 -2.5 才匹配得上

未注册模型回退到环境变量 IMAGE_MAX_REFS（默认 3）。
"""

import os
from pathlib import Path

from shared.model_alias import normalize_model_name
from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME

# (模型名子串, 参考图上限)。顺序敏感：具体在前，泛化在后。
MODEL_REF_LIMITS: list[tuple[str, int]] = [
    # --- 火山免费预置模型（带日期后缀，先匹配先命中）---
    # 免费额度 A/B 实测（2026-08-16）：2 张约 78-102s、7 张约 151-169s、
    # 8 张（含场景 near 图）>180s 必超时 → 收紧到 3：场景/人物/道具 各拼 1 张
    # 同质参考表（约 12MB，预计 ~100s，仍在 180s 内），比 2 张混拼保类别一致。
    # 付费正式档位模型仍走下方 seedream-4-5/4-0 官方能力条目（≤14）。
    ("doubao-seedream-4-0-250828", 3),
    ("doubao-seedream-4-5-251128", 3),
    ("doubao-seedream-5-0-lite-260128", 3),
    # Agent Plan 通道默认模型（无日期后缀，2026-08-30 注册）：免费档对齐 3 张约束
    ("doubao-seedream-5-0-lite", 3),
    # --- 图片模型（I2I 参考图）---
    ("qwen-image", 3),
    ("wan2-7-image", 9),
    ("wan2.6-image", 9),
    # Seedream 按官方档位细分（与 shared/model_caps.SEEDREAM_CAPS 一致）
    ("seedream-5-0-pro", 10),
    ("seedream-5-0-lite", 14),
    # 点号形式兜底官方免费预置模型 ID（doubao-seedream-5.0-lite）
    ("seedream-5.0-pro", 10),
    ("seedream-5.0-lite", 14),
    # 双点号形式（doubao-seedream-5.0.lite，方舟开通管理免费预置展示写法）
    ("seedream-5.0.pro", 10),
    ("seedream-5.0.lite", 14),
    ("seedream-4-5", 14),
    ("seedream-4-0", 14),
    ("seedream", 10),
    # MiniMax image-01: subject_reference 官方仅支持单张参考图
    ("image-01", 1),
    # --- 视频模型（r2v 参考 media）---
    ("happyhorse", 9),
    ("wan2-7-r2v", 5),
    ("wan2.6-r2v", 5),
    ("minimax-h3", 9),
    # 破折号形式匹配真实模型 ID（doubao-seedance-2-5-*）；点号形式兜底旧写法
    ("seedance-2-5", 30),
    ("seedance-2.5", 30),
    ("seedance-2-0", 12),
    ("seedance-2.0", 12),
    ("seedance", 12),
]


def model_supports_reference_image(model: str | None) -> bool:
    """该模型是否支持 `reference_image`（r2v）（v6.111，审核 P1-3）.

    **参考图数量 ≠ 模型能力**：`MODEL_REF_LIMITS` 里有泛化的 `seedance` 条目，
    它会给 Seedance 1.x 也返回数量 12，而能力表明确
    `supports_reference_image=False`——用「数量 > 0」当能力判据会把 1.x 放进来
    （实测 `doubao-seedance-1-0-pro-250528`：limit=12 / supports=False）。

    判定顺序：命中 Seedance 能力表 → 以表为准；否则（bailian/minimax 等）
    退回参考图数量注册表。
    """
    from shared.model_alias import normalize_model_name
    try:
        from shared.model_caps import SEEDANCE_CAPS
    except Exception:
        SEEDANCE_CAPS = []
    m = normalize_model_name(model or "")
    for pat, cap in SEEDANCE_CAPS:
        if pat in m:
            return bool(getattr(cap, "supports_reference_image", False))
    return get_ref_limit(model) > 0


def get_ref_limit(model: str | None, default: int | None = None) -> int:
    """按模型名查参考图上限；未注册模型统一兜底 IMAGE_MAX_REFS（默认 3）.

    ⚠️ 2026-09 决策：provider 不再传 default= 抄注册表值——未注册新模型的兜底
    统一走 IMAGE_MAX_REFS env。新增模型务必先在 MODEL_REF_LIMITS 注册，否则
    参考图上限与真实能力可能不符（多传报错、少传浪费一致性）。
    保留 default= 形参仅为向后兼容，生产代码勿再使用。
    """
    if default is None:
        env = os.getenv("IMAGE_MAX_REFS")
        default = int(env) if env else 3
    if not model:
        return default
    m = normalize_model_name(model)
    for pat, lim in MODEL_REF_LIMITS:
        if pat in m:
            return lim
    return default


_SHEET_LABELS = {
    # v6.52：保留什么/禁止什么分开写——只禁不取会被模型理解成整体拒绝参考。
    # 全身站姿照抄进画面是 hook ff「半身探出水面画出站立巨人+船尾第 4 蒙面人」
    # 的实测根因，必须点名禁姿态/持物/人数。
    "chars": "人物形象参考表（多角色头肩像横向拼合，仅取各角色面部/发型/上装特征；"
             "禁止照搬参考图人物的站姿、持物与人数到本画面，画面中角色人数与站位以文字描述为准）",
    "scene": "场景环境参考表（多张环境图拼合，只取环境光线与布局，禁止当作画面构图）",
    "props": "道具外观参考表（多件道具拼合，只取各道具外观，禁止当作画面构图）",
    "all": "综合参考表（多张参考图拼合，只取各单项特征，禁止当作画面构图）",
}


def identity_names_of(paths: list[str]) -> list[str]:
    """从头肩像路径提取角色名（拼表文件名后缀与逐人绑定用）.

    portrait_crops/神秘人_front_no_weapon.png → 神秘人（stem 首段）
    characters_bank/杨旭/front.png           → 杨旭（父目录名）
    """
    names: list[str] = []
    for p in paths or []:
        s = str(p).replace("\\", "/")
        if "/portrait_crops/" in s:
            stem = s.rsplit("/", 1)[-1][:-4] if s.endswith(".png") else s.rsplit("/", 1)[-1]
            nm = stem.split("_")[0]
            if nm:
                names.append(nm)
        elif "/characters_bank/" in s:
            nm = s.rsplit("/", 2)[-2]
            if nm:
                names.append(nm)
    return names


def describe_ref(path: str) -> str:
    """按路径判定参考图用途标签（供 prompt 里的「图N」清单复用）.

    识别约定：ref_sheet_*_{类别}.png（fit_refs 产物；v6.53 起 chars 表文件名
    带从左至右角色顺序后缀 ref_sheet_*_chars_A+B+C.png）、scenes_bank/、
    characters_bank/、portrait_crops/、props_bank/ 目录、first_frame/last_frame 帧名。
    """
    if not path:
        return ""
    p = str(path).replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    # 拼图表：ref_sheet_{tag}_{group}[_A+B+C].png
    if name.startswith("ref_sheet_"):
        base = name[:-4] if name.endswith(".png") else name
        group = "all"
        names_part = ""
        for pt in base.split("_")[2:]:
            if pt in ("chars", "scene", "props", "mix", "extra"):
                group = pt
            elif "+" in pt:
                names_part = pt
        label = _SHEET_LABELS.get(group, _SHEET_LABELS["all"])
        # v6.53 人↔参考图绑定：拼表内逐人点名（左→右），模型不再猜左一是谁
        if group == "chars" and names_part:
            ordered = "、".join(names_part.split("+"))
            label += f"；从左至右依次为：{ordered} 的头肩像，各自的长相与上装以对应位置为准"
        return label
    # 文件名分类匹配（非路径构建）；文件名契约单源 shared/paths.py 的 *_FILENAME 常量
    if name == FIRST_FRAME_FILENAME:
        return "视频起点画面（首帧，构图与人物站位基准）"
    if name == LAST_FRAME_FILENAME:
        return "视频终点画面（尾帧）"
    if "/scenes_bank/" in p:
        return "场景环境参考（只取环境光线与布局，禁止把图中内容当人物）"
    if "/portrait_crops/" in p:
        # v6.52：收集层裁出的头肩胸像缓存（源为 characters_bank 立绘）
        # v6.53：单图路径也点名角色（文件名首段即角色名）
        who = name.split("_")[0] if name else ""
        who_txt = f"{who}的" if who else ""
        return (f"{who_txt}人物形象参考（头肩胸像，仅取该角色的面部/发型/上装特征；"
                "禁止照搬图中姿态与持物）")
    if "/characters_bank/" in p:
        who = p.rsplit("/", 2)[-2] if "/" in p else ""
        who_txt = f"{who}的" if who else ""
        return (f"{who_txt}人物形象参考（头肩胸像，只取该角色的五官、发型与上装特征；"
                "禁止照搬图中站姿与持物）")
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
        elif config.IMAGE_PROVIDER == "volcengine":
            model = config.VOLCENGINE_T2I_MODEL
        elif config.IMAGE_PROVIDER == "minimax":
            model = config.MINIMAX_T2I_MODEL
        elif config.IMAGE_PROVIDER == "agent_plan":
            model = config.AGENT_PLAN_T2I_MODEL
        return get_ref_limit(model)
    except Exception:
        return get_ref_limit(None)


def _stitch(paths: list[str], output_path: str) -> str:
    from shared.portrait_stitch import stitch_portraits
    return stitch_portraits(paths, output_path)


def _atomic_reference_sheet(paths, output):
    """Content-addressed destination plus private temporary file for concurrent callers."""
    import hashlib
    import uuid
    from shared.portrait_stitch import stitch_portraits
    h = hashlib.sha256()
    for item in paths:
        p = Path(item)
        h.update(str(p).encode("utf-8"))
        h.update(hashlib.sha256(p.read_bytes()).digest())
    out = Path(output)
    out = out.with_name(out.stem + "_" + h.hexdigest()[:20] + out.suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name("." + out.stem + "." + uuid.uuid4().hex + ".png")
    try:
        result = stitch_portraits(paths, str(tmp))
        if not result:
            return ""
        os.replace(tmp, out)
        return str(out)
    finally:
        tmp.unlink(missing_ok=True)


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
        # v6.53 人↔参考图绑定：chars 表文件名带从左至右角色顺序（stitch 保序，
        # collect 顺序=动作主体优先的出镜顺序），describe_ref/build_ref_manifest
        # 据此生成逐人绑定说明——模型不再猜左一是谁
        suffix = ""
        if name == "chars":
            names = identity_names_of(paths)
            if names:
                suffix = "_" + "+".join(names)
        out = str(sheet_dir / f"ref_sheet_{sheet_tag}_{name}{suffix}.png")
        stitched = _atomic_reference_sheet(paths, out)
        return stitched if stitched else ""

    # 单张上限：全部拼成一张大参考表
    if limit == 1:
        merged = _sheet("all", flat)
        if merged:
            return [merged]
        return flat[:1]

    # 类别数超过名额：前面类别各占 1 名额，尾部类别共享 1 名额。
    # 尾巴恰好是单个类别时保留其真实类别名（scene/chars/props），拼图表命名与
    # 参考图编号说明才能对上（参考图3=道具外观参考表，而非"综合参考表"）；
    # 多个类别合并才叫 mix（综合参考表）。
    groups: list[tuple[str, list[str]]] = clean[: limit - 1]
    if len(clean) > limit - 1:
        tail_buckets = clean[limit - 1:]
        if len(tail_buckets) == 1:
            groups.append(tail_buckets[0])
        else:
            tail = [p for _, items in tail_buckets for p in items]
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
        stitched = _atomic_reference_sheet(valid[keep:], out)
        if stitched:
            return valid[:keep] + [stitched]
    except Exception:
        pass
    return valid[:limit]
