"""tests.enhancements.build_fixtures - 增强包夹具生成器（**本文件当前只含 A 包/motion 部分**）.

约定（计划 §A5/§15「回归与完整交付基线」）
----------------------------------------
* 夹具生成器由执行者提供真实对象与真实摘要：字体**实际复制**后算 sha256，
  ``base_input_key`` 用与 CLI 相同的规范摘要函数现算，禁止用文档占位字符串跑测试。
* 本文件按包追加：A 包提供 ``build_motion_bundle`` / ``build_revision_cases``；
  S 包的 ``build_valid_merge_fixture`` 由 S 包在本文件内追加（同名函数的共用约定，
  互不覆盖）。**本轮只写 motion 相关函数。**
* 所有产物落在调用方给定的隔离 root 下，不触碰正式产物目录。

夹具内容（两套，中文人名/地名不同、长短文本不同、方向关系相反）
--------------------------------------------------------------
* ``content_a``：沈砚 / 苏晚 / 青州→临淄→蓟城（北上，长文本）
* ``content_b``：陆行舟 / 阿箬 / 汴梁→江宁→临安（南下，短文本）

模板各一：title / letter / map（map 无 overlays，只导隔离单片）。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from shared import enhancement_io as eio
from shared.motion_render import schema as schema_mod
from shared.motion_render import validate as validate_mod
from shared.media_contract import MERGE_FPS, quantize_frame_count

FONT_FILES: dict[str, str] = {
    "font_sans": "NotoSansSC-VF.ttf",
    "font_serif": "NotoSerifSC-VF.ttf",
}

LICENSE_SOURCE = ("OFL-1.1 (Noto Sans SC / Noto Serif SC, SIL Open Font License 1.1); "
                  "本机系统副本，仅隔离样片使用，未核对再分发条款")

CANVAS = {
    "width": 1080,
    "height": 1920,
    "fps": MERGE_FPS,
    "safe_rect": [40, 300, 1000, 900],
    "subtitle_exclusion_rect": [0, 1500, 1080, 420],
}

#: 初始样片字号 = 画幅宽 × 0.04，**量化**为整数（§A2）
FONT_SIZE_PX = int(round(CANVAS["width"] * 0.04))

SHOT_DURATIONS = {"hook": 2.0, "1": 6.0, "2": 5.0}


# ---------------------------------------------------------------- 内容

def _content_a() -> dict:
    letter = ("沈砚提灯穿过青州的雨巷，袖口的血早已被雨水冲淡。"
              "他要在天明前赶到临淄，把苏晚托付的信交到守城人手里。")
    return {
        "fixture_id": "content_a",
        "script_revision": "fixture-a-0001",
        "chapter_title": "青州夜雨",
        "shots": [
            {"index": 1, "subtitle": "雨停之前，城门不会开。", "narration_segment": letter},
            {"index": 2, "subtitle": "马蹄声压住了更鼓。", "narration_segment": "青州到临淄三百里。"},
        ],
        "hook": {"subtitle": "更鼓敲过三遍。", "narration_segment": "雨里有人在等一个答案。"},
        "map_relations": [
            {
                "nodes": [
                    {"node_id": "qingzhou", "label": "青州", "x": 0.15, "y": 0.72},
                    {"node_id": "linzi", "label": "临淄", "x": 0.50, "y": 0.42},
                    {"node_id": "jicheng", "label": "蓟城", "x": 0.86, "y": 0.12},
                ],
                "edges": [
                    {"from_node": "qingzhou", "to_node": "linzi"},
                    {"from_node": "linzi", "to_node": "jicheng"},
                ],
            }
        ],
        "shot_durations": SHOT_DURATIONS,
    }


def _content_b() -> dict:
    return {
        "fixture_id": "content_b",
        "script_revision": "fixture-b-0001",
        "chapter_title": "汴梁春晓",
        "shots": [
            {"index": 1, "subtitle": "春水初生。", "narration_segment": "阿箬在汴梁等了三天。"},
            {"index": 2, "subtitle": "船靠岸了。", "narration_segment": "陆行舟从江宁来。"},
        ],
        "hook": {"subtitle": "灯还没熄。", "narration_segment": "有人在数江上的船。"},
        "map_relations": [
            {
                "nodes": [
                    {"node_id": "bianliang", "label": "汴梁", "x": 0.18, "y": 0.20},
                    {"node_id": "jiangning", "label": "江宁", "x": 0.52, "y": 0.52},
                    {"node_id": "linan", "label": "临安", "x": 0.86, "y": 0.86},
                ],
                "edges": [
                    {"from_node": "bianliang", "to_node": "jiangning"},
                    {"from_node": "jiangning", "to_node": "linan"},
                ],
            }
        ],
        "shot_durations": SHOT_DURATIONS,
    }


# ---------------------------------------------------------------- 素材

#: 只读素材的一次性缓存目录（跨会话复用、可随时删除）。
#: 存在的唯一目的：让硬链接**不直接指向系统目录**（见 `_canonical_asset`）。
_ASSET_CACHE_DIR = Path(tempfile.gettempdir()) / "aivideo_asset_cache"


def _canonical_asset(src: Path) -> Path:
    """把只读素材物化到一次性缓存，返回缓存路径；源的 size/mtime 变了就刷新。

    为什么不直接从 ``C:\\Windows\\Fonts`` 硬链接：硬链接与原文件**共享同一份数据**，
    任何一次误写都会把**系统字体**改坏。先落到可再生的缓存，最坏只坏缓存。
    """
    _ASSET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _ASSET_CACHE_DIR / src.name
    stamp = cached.with_name(cached.name + ".stamp")
    stat = src.stat()
    want = "%d:%d" % (stat.st_size, int(stat.st_mtime))
    try:
        if cached.is_file() and stamp.read_text(encoding="utf-8").strip() == want:
            return cached
    except OSError:
        pass
    shutil.copyfile(src, cached)
    try:
        stamp.write_text(want, encoding="utf-8")
    except OSError:
        pass
    return cached


def _link_or_copy(src: Path, dst: Path) -> str:
    """把素材挂进隔离根：硬链接（同盘 0 字节、免管理员）→ 符号链接 → 复制.

    三种形态的**内容逐字节相同**，所以 ``eio.sha256_file(dst)`` 取到的仍是真实摘要，
    与旧实现 ``shutil.copyfile`` 在契约上等价；差别只是不再每个隔离根各占 24 MB。
    跨盘符时 ``os.link`` 会失败，此时按符号链接 → 复制的顺序兜底。
    """
    try:
        if dst.is_symlink() or dst.exists():
            dst.unlink()
    except OSError:
        pass
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        pass
    try:
        os.symlink(src, dst)
        return "symlink"
    except OSError:
        pass
    shutil.copyfile(src, dst)
    return "copy"


def copy_font_assets(root: Path, font_dir: Path | None = None) -> dict:
    """把字体素材挂进 ``<root>/assets/fonts/`` 并返回带**真实摘要**的 asset 表.

    2026-10 复核 P0（磁盘）：旧实现每个隔离根 `shutil.copyfile` 一份 24 MB 字体，
    而隔离根按 PID 生成、从不回收，是 `.pytest_tmp` 膨胀的一个来源。现改为
    硬链接（内容不变、摘要不变、磁盘 0 字节），源先经 `_canonical_asset` 落到
    一次性缓存，避免链接直接指向系统字体目录。
    """
    src_dir = Path(font_dir) if font_dir else Path(r"C:\Windows\Fonts")
    out_dir = Path(root) / "assets" / "fonts"
    out_dir.mkdir(parents=True, exist_ok=True)
    assets: dict[str, dict] = {}
    missing: list[str] = []
    for asset_id, filename in FONT_FILES.items():
        src = src_dir / filename
        if not src.is_file():
            missing.append(str(src))
            continue
        dst = out_dir / filename
        _link_or_copy(_canonical_asset(src), dst)
        assets[asset_id] = {
            "path": f"fonts/{filename}",
            "sha256": eio.sha256_file(dst),
            "license_source": LICENSE_SOURCE,
            "kind": "font",
        }
    if missing:
        raise FileNotFoundError(
            "字体素材缺失（可用 --font-dir 指定可分发/系统字体目录）: " + ", ".join(missing))
    return assets


# ---------------------------------------------------------------- bundle 构造

def _segments_for(text: str, splits: Sequence[int], windows: Sequence[tuple[float, float]]) -> list[dict]:
    """按 code point 切成若干段（``splits`` 是切点，段覆盖全文、无缝无叠）。"""
    bounds = [0, *list(splits), len(text)]
    out: list[dict] = []
    for i in range(len(bounds) - 1):
        cs, ce = bounds[i], bounds[i + 1]
        if ce <= cs:
            continue
        st, en = windows[min(i, len(windows) - 1)]
        out.append({"segment_id": f"seg{i + 1}", "text": text[cs:ce],
                    "char_start": cs, "char_end": ce, "start_sec": st, "end_sec": en})
    return out


def _title_spec(spec_id: str, *, text: str, fixture_id: str, rect: list[int],
                duration_sec: float, font_asset_id: str = "font_sans",
                color: Sequence[float] = (238, 232, 214, 1.0)) -> dict:
    return {
        "spec_id": spec_id, "template": "title", "duration_sec": duration_sec,
        "font_asset_id": font_asset_id, "font_size_px": FONT_SIZE_PX,
        "color": list(color), "rect": list(rect),
        "source_text": text,
        "source_locator": {"fixture_id": fixture_id, "field_path": "/chapter_title"},
        "segments": _segments_for(text, [], [(0.0, duration_sec)]),
        "nodes": [], "edges": [],
    }


def _letter_spec(spec_id: str, *, text: str, fixture_id: str, rect: list[int],
                 duration_sec: float, font_asset_id: str = "font_sans",
                 locator_path: str = "/shots/0/narration_segment") -> dict:
    cut = max(1, len(text) // 2)
    half = round(duration_sec / 2 * 100) / 100
    return {
        "spec_id": spec_id, "template": "letter", "duration_sec": duration_sec,
        "font_asset_id": font_asset_id, "font_size_px": FONT_SIZE_PX,
        "color": [236, 228, 206, 1.0], "rect": list(rect),
        "source_text": text,
        "source_locator": {"fixture_id": fixture_id, "field_path": locator_path},
        "segments": _segments_for(text, [cut], [(0.0, half), (half, duration_sec)]),
        "nodes": [], "edges": [],
    }


def _map_spec(spec_id: str, doc: Mapping[str, Any], *, rect: list[int], duration_sec: float,
              relation_index: int = 0, font_asset_id: str = "font_sans") -> dict:
    relation = (doc.get("map_relations") or [])[relation_index]
    nodes = [{"node_id": n["node_id"], "label": n["label"], "x": float(n["x"]), "y": float(n["y"])}
             for n in relation["nodes"]]
    n_edges = len(relation["edges"])
    span = round(duration_sec / max(1, n_edges) * 100) / 100
    edges = []
    for i, e in enumerate(relation["edges"]):
        st = round(i * span, 2)
        en = duration_sec if i == n_edges - 1 else round((i + 1) * span, 2)
        edges.append({"edge_id": f"e{i + 1}", "from_node": e["from_node"],
                      "to_node": e["to_node"], "start_sec": st, "end_sec": en})
    return {
        "spec_id": spec_id, "template": "map", "duration_sec": duration_sec,
        "font_asset_id": font_asset_id, "font_size_px": FONT_SIZE_PX,
        "color": [220, 226, 235, 1.0], "rect": list(rect),
        "source_text": "",
        "source_locator": {"fixture_id": doc["fixture_id"],
                           "field_path": f"/map_relations/{relation_index}"},
        "segments": [], "nodes": nodes, "edges": edges,
    }


def _bundle(experiment_id: str, doc: Mapping[str, Any], assets: Mapping[str, Any],
            specs: list[dict], overlays: list[dict]) -> dict:
    return {
        "schema_version": schema_mod.SCHEMA_MOTION_BUNDLE,
        "experiment_id": experiment_id,
        "source": {
            "kind": schema_mod.SOURCE_KIND_FIXTURE,
            "source_sha256": validate_mod.fixture_doc_sha256(doc),
            "fixture_id": doc["fixture_id"],
        },
        "canvas": copy.deepcopy(CANVAS),
        "assets": copy.deepcopy(dict(assets)),
        "specs": specs,
        "overlays": overlays,
    }


def _overlay(overlay_id: str, spec_id: str, shot_index: int, duration_sec: float,
             start_sec: float = 0.0) -> dict:
    return {"overlay_id": overlay_id, "spec_id": spec_id, "shot_index": shot_index,
            "start_sec": start_sec, "end_sec": round(start_sec + duration_sec, 3)}


def scale_bundle_geometry(bundle: Mapping[str, Any], scale: float) -> dict:
    """把画幅/rect/字号按同一比例缩放（**测试用小画幅**，几何关系保持一致）。"""
    if scale == 1.0:
        return copy.deepcopy(dict(bundle))
    out = copy.deepcopy(dict(bundle))
    canvas = out["canvas"]
    for key in ("width", "height"):
        canvas[key] = max(1, int(round(canvas[key] * scale)))
    for key in ("safe_rect", "subtitle_exclusion_rect"):
        canvas[key] = [int(round(v * scale)) for v in canvas[key]]
    for spec in out["specs"]:
        spec["rect"] = [int(round(v * scale)) for v in spec["rect"]]
        spec["font_size_px"] = max(1, int(round(spec["font_size_px"] * scale)))
    return out


def build_motion_bundle(root: str | Path, *, experiment_id: str = "motion-fixture",
                        font_dir: str | Path | None = None, scale: float = 1.0) -> dict:
    """生成 A 包夹具：两套内容 × 三模板 = 6 个 bundle（+ 冻结 fixture 文档与字体素材）。

    Returns: ``{"root", "asset_root", "fixtures", "cases": {name: {...}}, "fonts": {...}}``
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    assets = copy_font_assets(root, Path(font_dir) if font_dir else None)
    fixtures_dir = root / "assets" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    docs = {"content_a": _content_a(), "content_b": _content_b()}
    for fid, doc in docs.items():
        (fixtures_dir / f"{fid}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    cases: dict[str, dict] = {}
    for fid, doc in docs.items():
        is_a = fid == "content_a"
        for template in ("title", "letter", "map"):
            cases[f"{fid}__{template}"] = _make_case(
                root, experiment_id, doc, assets, template=template, is_long=is_a,
                scale=scale)

    return {
        "root": str(root),
        "asset_root": str(root / "assets"),
        "fixtures": {fid: str(fixtures_dir / f"{fid}.json") for fid in docs},
        "cases": cases,
        "fonts": {"sans": str(root / "assets" / "fonts" / FONT_FILES["font_sans"]),
                  "serif": str(root / "assets" / "fonts" / FONT_FILES["font_serif"]),
                  "label": str(root / "assets" / "fonts" / FONT_FILES["font_sans"])},
        "assets": assets,
        "shot_windows": shot_windows_for(next(iter(docs.values()))),
    }


def ensure_fixtures(root: str | Path, *, experiment_id: str = "motion-fixture",
                    font_dir: str | Path | None = None, scale: float = 0.25) -> dict:
    """幂等构建（跨测试模块复用同一份夹具，避免重复复制 40MB+ 字体）。

    同一 root 下用 ``.built.json`` 记参数；参数一致就直接复用，参数变了重建。
    """
    root = Path(root)
    marker = root / ".built.json"
    if marker.is_file():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if data.get("scale") == scale and data.get("experiment_id") == experiment_id:
                return data
        except Exception:
            pass
    built = build_motion_bundle(root, experiment_id=experiment_id, font_dir=font_dir,
                                scale=scale)
    built["scale"] = scale
    marker.write_text(json.dumps(built, ensure_ascii=False, indent=2), encoding="utf-8")
    return built


def _make_case(root: Path, experiment_id: str, doc: Mapping[str, Any],
               assets: Mapping[str, Any], *, template: str, is_long: bool,
               scale: float = 1.0) -> dict:
    name = f"{doc['fixture_id']}__{template}"
    letter_text = doc["shots"][0]["narration_segment"]
    duration = {"title": 3.0, "letter": 6.0, "map": 5.0}[template]
    if template == "title":
        specs = [
            _title_spec("t_main", text=doc["chapter_title"], fixture_id=doc["fixture_id"],
                        rect=[80, 420, 920, 260], duration_sec=duration),
            # 同一来源的第二版式：基线上就是「重复显示有旧绑定」，供 delete_rebind 用
            _title_spec("t_alt", text=doc["chapter_title"], fixture_id=doc["fixture_id"],
                        rect=[80, 720, 920, 260], duration_sec=duration,
                        font_asset_id="font_serif", color=(214, 226, 240, 1.0)),
        ]
        overlays = [_overlay("ov_main", "t_main", 1, duration)]
    elif template == "letter":
        specs = [_letter_spec("l_main", text=letter_text, fixture_id=doc["fixture_id"],
                              rect=[80, 400, 920, 760], duration_sec=duration)]
        overlays = [_overlay("ov_letter", "l_main", 1, duration)]
    else:
        specs = [_map_spec("m_main", doc, rect=[80, 400, 920, 760], duration_sec=duration)]
        overlays = []  # map 只导隔离单片，禁止进 Step04 overlays（§A2）

    bundle = scale_bundle_geometry(_bundle(experiment_id, doc, assets, specs, overlays), scale)
    case_dir = Path(root) / "cases" / name
    case_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = case_dir / "bundle.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    frames = quantize_frame_count(duration, MERGE_FPS)
    return {
        "name": name, "template": template, "fixture_id": doc["fixture_id"],
        "bundle": str(bundle_path), "duration_sec": duration,
        "frame_count": frames,
        "keyframe_indexes": [0, max(0, frames // 3), max(0, (2 * frames) // 3), frames - 1],
        "input_key": eio.canonical_json_digest(bundle),
        "is_long": is_long,
    }


# ---------------------------------------------------------------- 镜头窗表

def shot_windows_for(doc: Mapping[str, Any], *, origin_sec: float = 0.0) -> dict:
    """由冻结 fixture 文档派生镜头窗表（S 的 MergeTimeline 在生产里给同一形状）。"""
    durations = doc.get("shot_durations") or SHOT_DURATIONS
    windows: dict[int, dict] = {}
    cursor = float(origin_sec)
    hook = doc.get("hook")
    if isinstance(hook, Mapping):
        dur = float(durations.get("hook", 2.0))
        windows[0] = {"start_sec": cursor, "end_sec": cursor + dur,
                      "shot_id": f"{doc['script_revision']}:hook", "is_hook": True}
        cursor += dur
    for shot in doc.get("shots") or []:
        idx = int(shot["index"])
        dur = float(durations.get(str(idx), 4.0))
        windows[idx] = {"start_sec": cursor, "end_sec": cursor + dur,
                        "shot_id": f"{doc['script_revision']}:shot_{idx}", "is_hook": False}
        cursor += dur
    return windows


# ---------------------------------------------------------------- 修订用例

def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_bundle(path: Path, bundle: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _spec_by_id(bundle: Mapping[str, Any], spec_id: str) -> dict:
    for spec in bundle["specs"]:
        if spec["spec_id"] == spec_id:
            return spec
    raise KeyError(spec_id)


def _overlay_by_id(bundle: Mapping[str, Any], overlay_id: str) -> dict:
    for ov in bundle["overlays"]:
        if ov["overlay_id"] == overlay_id:
            return ov
    raise KeyError(overlay_id)


def build_revision_cases(root: str | Path, *, base_bundle_path: str | Path,
                         experiment_id: str = "motion-fixture",
                         accepted_input_key: str | None = None) -> dict:
    """构造 ``motion_revision.v1`` 请求（真实对象 + 真实摘要）。

    ``accepted_input_key`` 缺省时按**与 CLI 相同的规范摘要**现算（不是占位字符串）。
    Returns: ``{case: {"bundle", "request", "expect", "expect_codes", "note"}}``
    """
    root = Path(root)
    base_path = Path(base_bundle_path)
    base = _load(base_path)
    # 修订用例里的 rect 一律**从基线的 canvas 派生**（夹具可能按比例缩放过）
    safe = [int(v) for v in base["canvas"]["safe_rect"]]
    sx, sy, sw, sh = safe
    half_h = max(1, sh // 2)
    base_key = accepted_input_key if accepted_input_key is not None else eio.canonical_json_digest(base)
    cases: dict[str, dict] = {}
    out_dir = root / "revisions"
    out_dir.mkdir(parents=True, exist_ok=True)

    def emit(name: str, bundle: dict, *, expect: str, codes: Sequence[str], note: str,
             base_input_key: str | None = None, request_id: str | None = None) -> None:
        case_dir = out_dir / name
        bundle_path = Path(_write_bundle(case_dir / "bundle.json", bundle))
        request = {
            "schema_version": schema_mod.SCHEMA_MOTION_REVISION,
            "request_id": request_id or f"req-{name}",
            "base_input_key": base_input_key if base_input_key is not None else base_key,
            "replacement_bundle": bundle,
            "change_reason": note,
        }
        request_path = case_dir / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        cases[name] = {"name": name, "bundle": str(bundle_path), "request": str(request_path),
                       "expect": expect, "expect_codes": list(codes), "note": note,
                       "replacement_input_key": eio.canonical_json_digest(bundle)}

    # 1) 文本超容 → 改 rect + 分段（正文不变）
    b = copy.deepcopy(base)
    spec = _spec_by_id(b, "t_main")
    spec["rect"] = list(safe)
    text = spec["source_text"]
    cut = max(1, len(text) // 2)
    spec["segments"] = [
        {"segment_id": "seg1", "text": text[:cut], "char_start": 0, "char_end": cut,
         "start_sec": 0.0, "end_sec": 1.5},
        {"segment_id": "seg2", "text": text[cut:], "char_start": cut, "char_end": len(text),
         "start_sec": 1.5, "end_sec": spec["duration_sec"]},
    ]
    emit("layout_split", b, expect="accepted", codes=[],
         note="文本超容：加大 rect 并把标题拆成两段（正文逐字不变）")

    # 2) 缺字体/字形 → 完整 assets 替换字体并改 font_asset_id
    b = copy.deepcopy(base)
    serif = b["assets"]["font_serif"]
    b["assets"] = {"font_serif": serif}
    for sp in b["specs"]:
        sp["font_asset_id"] = "font_serif"
    emit("font_replace", b, expect="accepted", codes=[],
         note="换成已冻结的另一种字体并改写全部 font_asset_id")

    # 3) 叠加冲突/越界 → 重排镜头局部时间
    b = copy.deepcopy(base)
    ov = _overlay_by_id(b, "ov_main")
    ov["start_sec"], ov["end_sec"] = 1.5, 4.5
    emit("retime_overlap", b, expect="accepted", codes=[],
         note="把叠加窗在同一镜内后移（时长不变，仍落在镜窗内）")

    # 4) 新增并绑定
    b = copy.deepcopy(base)
    _fixture_root = base_path.parent.parent.parent
    doc = validate_mod.load_fixture_doc(_fixture_root / "assets", base["source"]["fixture_id"])
    new_spec = _title_spec("t_new", text=doc["shots"][0]["subtitle"],
                           fixture_id=base["source"]["fixture_id"],
                           rect=[sx, sy + half_h, sw, half_h], duration_sec=3.0)
    new_spec["source_locator"] = {"fixture_id": base["source"]["fixture_id"],
                                  "field_path": "/shots/0/subtitle"}
    # 字号跟随基线（夹具可能按比例缩放过画幅，硬编码字号会撑爆 rect）
    new_spec["font_size_px"] = _spec_by_id(base, "t_main")["font_size_px"]
    b["specs"].append(new_spec)
    b["overlays"].append(_overlay("ov_new", "t_new", 2, 3.0))
    emit("add_bind", b, expect="accepted", codes=[], note="新增 spec 与 overlay，两表同步新增 ID")

    # 5) 删除并撤销/重绑（旧 ID 的两表同步变更，核心文字不减少）
    b = copy.deepcopy(base)
    b["specs"] = [s for s in b["specs"] if s["spec_id"] != "t_main"]
    _overlay_by_id(b, "ov_main")["spec_id"] = "t_alt"
    emit("delete_rebind", b, expect="accepted", codes=[],
         note="删除 t_main 并把 ov_main 重绑到同正文的 t_alt（旧引用撤销、核心文字不减少）")

    # 6) 拆分并重映射
    b = copy.deepcopy(base)
    src = _spec_by_id(b, "t_main")
    text = src["source_text"]
    cut = max(1, len(text) // 2)
    p1 = copy.deepcopy(src)
    p1.update({"spec_id": "t_p1", "rect": [sx, sy, sw, half_h], "duration_sec": 1.5,
               "source_text": text[:cut],
               "segments": [{"segment_id": "seg1", "text": text[:cut], "char_start": 0,
                             "char_end": cut, "start_sec": 0.0, "end_sec": 1.5}]})
    p2 = copy.deepcopy(src)
    p2.update({"spec_id": "t_p2", "rect": [sx, sy + half_h, sw, half_h], "duration_sec": 1.5,
               "source_text": text[cut:],
               "segments": [{"segment_id": "seg1", "text": text[cut:], "char_start": 0,
                             "char_end": len(text) - cut, "start_sec": 0.0, "end_sec": 1.5}]})
    b["specs"] = [s for s in b["specs"] if s["spec_id"] != "t_main"] + [p1, p2]
    b["overlays"] = [ov for ov in b["overlays"] if ov["overlay_id"] != "ov_main"] + [
        _overlay("ov_p1", "t_p1", 1, 1.5), _overlay("ov_p2", "t_p2", 1, 1.5, start_sec=3.0)]
    emit("split_remap", b, expect="accepted", codes=[],
         note="原 spec 拆成两个新 ID 并同步绑定，撤销旧 spec/overlay")

    # 7) 局部修好但仍残余（正文段落被改写 → 必须拒收，不移动 accepted）
    b = copy.deepcopy(base)
    _spec_by_id(b, "t_main")["rect"] = list(safe)
    _spec_by_id(b, "t_main")["segments"][0]["text"] = _spec_by_id(b, "t_main")["segments"][0]["text"] + "。"
    emit("partial_fix", b, expect="rejected", codes=["derived_char_index_mismatch"],
         note="版式修好了但正文被改写：代码重算 char 引用即发现不符，拒收残余")

    # 7b) 局部残余：正文被换成等长异文（char 引用自洽）⇒ 完整重验报 segment_text_mismatch
    b = copy.deepcopy(base)
    spec = _spec_by_id(b, "t_main")
    seg0 = spec["segments"][0]
    original = seg0["text"]
    seg0["text"] = original[1] + original[0] + original[2:] if len(original) >= 2 else original
    emit("residual_text_swap", b, expect="rejected", codes=["segment_text_mismatch"],
         note="正文被换成等长异文：完整重验返回残余，不渲染不提交")

    # 8) 无变化（同 key 且合法）→ 重跑执行/恢复，成功即 accepted
    emit("no_change", copy.deepcopy(base), expect="accepted", codes=[],
         note="无变化且合法：重跑执行并重新采纳（no_change 不得误判无解）")

    # 9) 无变化但内容非法 → 返回具体残余，不渲染不提交
    b = copy.deepcopy(base)
    _spec_by_id(b, "t_main")["segments"] = _spec_by_id(b, "t_main")["segments"][:1]
    _spec_by_id(b, "t_main")["segments"][0]["char_end"] = len(_spec_by_id(b, "t_main")["source_text"]) - 1
    _spec_by_id(b, "t_main")["segments"][0]["text"] = _spec_by_id(b, "t_main")["source_text"][:-1]
    emit("no_change_still_illegal", b, expect="rejected", codes=["segment_coverage_incomplete"],
         note="返回的仍是非法内容：给具体残余，不渲染不提交")

    # 10) 旧 key / 旧 revision → 拒收并给出当前 key
    b = copy.deepcopy(base)
    for sp in b["specs"]:
        sp["font_asset_id"] = "font_serif"
    b["assets"] = {"font_serif": b["assets"]["font_serif"]}
    emit("stale_base_key", b, expect="rejected", codes=["stale_base_key"],
         base_input_key="0" * 64, note="请求基于旧 key：拒收并回报当前 key，不自动重基")

    # 11) 守卫：换来源
    b = copy.deepcopy(base)
    b["source"]["source_sha256"] = "1" * 64
    emit("guard_source_changed", b, expect="rejected", codes=["source_changed"],
         note="修订试图换掉来源摘要：范围与来源守卫拒收")

    # 12) 守卫：删掉一段（核心文字减少）
    b = copy.deepcopy(base)
    b["specs"] = []
    emit("guard_text_reduced", b, expect="rejected",
         codes=["core_text_reduced"],
         note="删光 spec：核心文字减少，范围与来源守卫拒收")

    # 13) 守卫：新造重复显示（把 t_alt 复制一份覆盖同一区间）
    b = copy.deepcopy(base)
    dup = copy.deepcopy(_spec_by_id(b, "t_alt"))
    dup["spec_id"] = "t_dup"
    b["specs"].append(dup)
    emit("guard_duplicate_copy", b, expect="rejected", codes=["duplicate_binding_without_base"],
         note="复制一份同区间的 spec：重复显示没有基线旧绑定，拒收")

    return {"root": str(out_dir), "base_bundle": str(base_path),
            "base_input_key": base_key, "cases": cases}



# ---------------------------------------------------------------- 取帧桩（缺浏览器时的可执行对照）

class DeterministicStubShooter:
    """确定性取帧桩：``seek(t)`` 的**像素化替身**，用于在无 Playwright/Chromium 的
    环境里跑通「帧计划 → 取帧 → 校验 → 编码 → 提交」整条链路。

    它**不是**渲染器的替代品，也不证明模板正确：
      * 画面是 t 的纯函数（同一 t 逐字节一致），用来断言乱序 seek、帧数、RGBA；
      * 真实模板的字体/版式/逐字显现仍由浏览器侧权威测量负责（本机 not_run）。
    """

    def __init__(self, *, alpha_half_band: bool = True):
        self.calls: list[dict] = []
        self.alpha_half_band = alpha_half_band

    def available(self) -> bool:
        return True

    def shoot(self, spec):
        from PIL import Image, ImageDraw

        self.calls.append(spec.to_dict())
        written = []
        for req in spec.requests:
            img = Image.new("RGBA", (int(spec.width), int(spec.height)), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            frac = 0.0 if spec.mode == "static" else min(1.0, max(0.0, float(req.t_sec)))
            band_w = max(1, int(spec.width * (0.25 + 0.5 * (frac % 1.0))))
            half = int(spec.height * 0.5)
            draw.rectangle([0, half, band_w, min(half + 40, spec.height)], fill=(240, 230, 200, 255))
            if self.alpha_half_band:
                draw.rectangle([0, half + 40, max(1, band_w // 2), min(half + 80, spec.height)],
                               fill=(120, 200, 255, 128))
            Path(req.out_path).parent.mkdir(parents=True, exist_ok=True)
            img.save(req.out_path)
            written.append(Path(req.out_path))
        return written



__all__ = ["build_motion_bundle", "build_revision_cases", "shot_windows_for",
           "copy_font_assets", "DeterministicStubShooter", "scale_bundle_geometry",
           "ensure_fixtures",
           "FONT_FILES", "CANVAS", "FONT_SIZE_PX", "SHOT_DURATIONS"]
