# -*- coding: utf-8 -*-
"""FF/KF/LF 审核联系表（总计划阶段 7 / 工作包 A）.

把整章各镜的首帧、中间关键帧、尾帧排成一张审核图，让角色漂移、空间方向翻转、
动作阶段缺失、构图重复和缺图问题**在生成视频之前**被人工发现。

来源：[Storyboard grid to video](https://github.com/LearnPrompt/awesome-seedance/blob/main/docs/templates/en/storyboard-grid-to-video.md)

## 契约（非 step01 吸收计划 §3）

  - **只读**：只读 `script_result.json` 的镜头顺序/镜号/关键帧数量与
    `vimax_working/shots/<idx>/` 下的帧图片，**绝不**回写分镜、绝不动原始帧；
  - **不调用任何生成模型**；联系表**不得**进入 `reference_paths`；
  - 缺图用灰色占位并在 JSON 里记 `missing`，**不静默跳过**；
  - 关键帧按**数字**序号排序（`keyframe_10` 不得排在 `keyframe_2` 前）；
  - 横竖图都按比例 letterbox，不裁剪、不变形；
  - 镜号/帧类型/源文件名写在**画面外**的边框区，画面内不绘制任何时间码/字幕/水印；
  - 宽度超上限自动分页；
  - 同样输入重复运行得到**相同 JSON 与相同排列**；
  - 失败不破坏已有审核图：先写临时文件，成功后再原子替换；
  - step01 semantic gate 未通过时，仍可汇总已有图片，但 JSON 必须标
    `upstream_blocked`，**不得**被当成「可生成」的依据。

用法：
    python tools/build_storyboard_contact_sheet.py [--chapter N] [--out-dir DIR]
                                                   [--cell 300] [--max-width 4200]
                                                   [--json-only]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.paths import (  # noqa: E402
    FIRST_FRAME_FILENAME, KEYFRAME_FILENAME_FMT, LAST_FRAME_FILENAME,
    chapter_dir, shot_work_dir,
)

SHEET_BASENAME = "storyboard_contact_sheet"
SHEET_JSON = "storyboard_contact_sheet.json"
DEFAULT_CELL = 300          # 单格内容区边长（像素）
DEFAULT_MAX_WIDTH = 4200    # 单页最大宽度
LABEL_H = 26                # 每格上方标签条高度
GUTTER = 8                  # 格间距
MARGIN = 16                 # 页面外边距
PAGE_BG = (26, 26, 30)
MISSING_BG = (90, 90, 96)
LABEL_BG = (16, 16, 20)
LABEL_FG = (232, 232, 236)


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _kf_sort_key(path: Path) -> tuple:
    """关键帧按**数字**序号排序（字典序会把 keyframe_10 排到 keyframe_2 前）."""
    stem = path.stem
    digits = "".join(ch for ch in stem if ch.isdigit())
    return (int(digits) if digits else 1 << 30, path.name)


def keyframe_paths(shot_idx: int) -> list[Path]:
    """该镜的中间关键帧（按数字序号排序；不修改 shared/paths.py）."""
    d = shot_work_dir(shot_idx)
    if not d.is_dir():
        return []
    prefix = KEYFRAME_FILENAME_FMT.split("{")[0]
    return sorted([p for p in d.glob(f"{prefix}*.png")], key=_kf_sort_key)


def collect_cells(script: dict) -> list[dict]:
    """按 `script_result.json` 的镜头顺序收集每镜的帧格."""
    units: list[tuple] = []
    hook = script.get("hook")
    if isinstance(hook, dict):
        units.append(("hook", 0, hook))
    for i, shot in enumerate(script.get("shots") or []):
        if isinstance(shot, dict):
            units.append((str(shot.get("index", i + 1)),
                          int(shot.get("index", i + 1)), shot))

    rows: list[dict] = []
    for label, idx, shot in units:
        d = shot_work_dir(idx)
        cells: list[dict] = []
        ff = d / FIRST_FRAME_FILENAME
        cells.append({"kind": "ff", "path": str(ff)})
        for n, kp in enumerate(keyframe_paths(idx)):
            cells.append({"kind": f"kf{n}", "path": str(kp)})
        lf = d / LAST_FRAME_FILENAME
        cells.append({"kind": "lf", "path": str(lf)})
        rows.append({
            "shot": label,
            "shot_dir": str(d),
            "kf_count": len([c for c in cells if c["kind"].startswith("kf")]),
            "cells": cells,
        })
    return rows


def _load_cell_image(path: Path, box: int):
    from PIL import Image
    try:
        img = Image.open(path)
        img.load()
        return img.convert("RGB")
    except Exception:
        return None


def _fit(img, box: int):
    """等比缩放到 box 内并 letterbox（不裁剪、不变形）."""
    from PIL import Image
    w, h = img.size
    if w <= 0 or h <= 0:
        return None
    scale = min(box / float(w), box / float(h))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (box, box), PAGE_BG)
    canvas.paste(resized, ((box - nw) // 2, (box - nh) // 2))
    return canvas


def build_sheet(rows: list[dict], *, cell: int, max_width: int,
                json_only: bool = False, out_dir: Path | None = None) -> dict:
    """渲染联系表并返回审核 JSON（不写文件；写盘由 `render` 负责）."""
    # 每行的格序固定为 FF → KF0…KFn → LF（行内左对齐；KF 数不同的行右端会有空位，
    # 这是「每镜一帧数不齐」的如实呈现，不做补位、不居中）。
    cols_total = max(len(r["cells"]) for r in rows) if rows else 0
    if cols_total == 0:
        return {"pages": [], "rows": [], "missing": [], "cols_total": 0}

    cell_w = cell + GUTTER
    cols_per_page = max(1, (max_width - 2 * MARGIN + GUTTER) // cell_w)
    rows_per_page = max(1, int(os.getenv("CONTACT_SHEET_ROWS_PER_PAGE", "8") or "8"))

    pages: list[dict] = []
    col_chunks = [list(range(s, min(s + cols_per_page, cols_total)))
                  for s in range(0, cols_total, cols_per_page)]
    row_chunks = [rows[s:s + rows_per_page]
                  for s in range(0, len(rows), rows_per_page)]

    missing: list[dict] = []
    for pi, (rchunk, cchunk) in enumerate(
            [(r, c) for r in row_chunks for c in col_chunks], start=1):
        cells_meta: list[dict] = []
        for ri, row in enumerate(rchunk):
            for ci, col in enumerate(cchunk):
                if col >= len(row["cells"]):
                    continue
                spec = row["cells"][col]
                src = Path(spec["path"])
                exists = src.exists()
                meta = {
                    "shot": row["shot"],
                    "kind": spec["kind"],
                    "source": str(src),
                    "exists": exists,
                    "row": ri,
                    "col": ci,
                    "page": pi,
                }
                if exists:
                    meta["sha1"] = _sha1_file(src)
                    try:
                        from PIL import Image
                        with Image.open(src) as _im:
                            meta["size"] = list(_im.size)
                    except Exception:
                        meta["size"] = None
                else:
                    missing.append({"shot": row["shot"], "kind": spec["kind"],
                                    "source": str(src)})
                cells_meta.append(meta)
        pages.append({
            "page": pi,
            "cols": list(cchunk),
            "cells": cells_meta,
            "rows": [r["shot"] for r in rchunk],
        })
    return {
        "cols_total": cols_total,
        "cols_per_page": cols_per_page,
        "rows_per_page": rows_per_page,
        "pages": pages,
        "rows": [{"shot": r["shot"], "kf_count": r["kf_count"],
                  "cells": len(r["cells"])} for r in rows],
        "missing": missing,
    }


def render(rows: list[dict], manifest: dict, *, cell: int, out_dir: Path) -> list[Path]:
    """渲染每一页并**原子**写盘；返回写出的图片路径."""
    from PIL import Image, ImageDraw

    written: list[Path] = []
    page_meta = manifest.get("pages") or []
    multi = len(page_meta) > 1
    for page in page_meta:
        pi = page["page"]
        cells = page["cells"]
        if not cells:
            continue
        cols = max(c["col"] for c in cells) + 1
        rws = max(c["row"] for c in cells) + 1
        width = 2 * MARGIN + cols * cell + (cols - 1) * GUTTER
        height = 2 * MARGIN + rws * (cell + LABEL_H) + (rws - 1) * GUTTER
        canvas = Image.new("RGB", (width, height), PAGE_BG)
        draw = ImageDraw.Draw(canvas)
        for c in cells:
            x = MARGIN + c["col"] * (cell + GUTTER)
            y = MARGIN + c["row"] * (cell + LABEL_H + GUTTER)
            # 标签条在**画面外**（联系表自身是审核产物，不是给模型看的输入）
            draw.rectangle([x, y, x + cell, y + LABEL_H - 2], fill=LABEL_BG)
            label = f"{c['shot']} {c['kind']}"
            draw.text((x + 4, y + 5), label, fill=LABEL_FG)
            img = _load_cell_image(Path(c["source"]), cell) if c["exists"] else None
            if img is None:
                box = Image.new("RGB", (cell, cell), MISSING_BG)
                bd = ImageDraw.Draw(box)
                bd.text((6, cell // 2 - 6), "MISSING", fill=LABEL_FG)
                canvas.paste(box, (x, y + LABEL_H))
            else:
                fitted = _fit(img, cell)
                if fitted is not None:
                    canvas.paste(fitted, (x, y + LABEL_H))
        name = (f"{SHEET_BASENAME}_{pi:02d}.jpg" if multi
                else f"{SHEET_BASENAME}.jpg")
        target = out_dir / name
        tmp = out_dir / f".{name}.tmp-{os.getpid()}"
        try:
            canvas.save(tmp, format="JPEG", quality=88)
            os.replace(tmp, target)
        except Exception:
            # 失败不得留下半截文件，也不得破坏已有的审核图
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        page["file"] = str(target)
        written.append(target)
    return written


def _upstream_status(chapter: Path) -> str:
    """上游 gate 状态：manifest COMMITTED 且三份摘要相符 → eligible，否则 blocked.

    联系表**不因为 blocked 而拒绝生成**（它只是人工审核产物），但必须如实标注
    ——否则「静态 PASS」会被误读成「可以进生成」。
    """
    try:
        # v6.111（审核 P2-6）：只验 manifest 会把不 eligible 的上游标成 eligible
        # ——用与真实生成同一对检查（semantic gate + manifest）。
        from shared.quality_gate import QualityGateError, require_step01_upstream
        require_step01_upstream(chapter, stage="step01")
        return "eligible"
    except QualityGateError:
        return "upstream_blocked"
    except Exception:
        return "upstream_blocked"


def build(chapter: int | None = None, *, out_dir: Path | None = None,
          cell: int = DEFAULT_CELL, max_width: int = DEFAULT_MAX_WIDTH,
          json_only: bool = False) -> dict:
    chapter_path = chapter_dir(chapter)
    script_path = chapter_path / "script_result.json"
    if not script_path.exists():
        raise FileNotFoundError(f"script not found: {script_path}")
    script = json.loads(script_path.read_text(encoding="utf-8"))
    rows = collect_cells(script)
    manifest = build_sheet(rows, cell=cell, max_width=max_width)
    manifest.update({
        "chapter_dir": str(chapter_path),
        "script_revision": script.get("script_revision"),
        "shot_count": len(script.get("shots") or []),
        "has_hook": isinstance(script.get("hook"), dict),
        "cell_px": cell,
        "max_width": max_width,
        # 口径说明：本文件是**本地 PIL 派生**产物，不计入 API 图片额度
        "local_derived": True,
        "api_generated_images": 0,
        "upstream_gate_status": _upstream_status(chapter_path),
    })
    write_dir = Path(out_dir) if out_dir else chapter_path
    write_dir.mkdir(parents=True, exist_ok=True)
    if not json_only:
        render(rows, manifest, cell=cell, out_dir=write_dir)
    tmp_json = write_dir / f".{SHEET_JSON}.tmp-{os.getpid()}"
    tmp_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    os.replace(tmp_json, write_dir / SHEET_JSON)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chapter", type=int, default=None)
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--cell", type=int, default=DEFAULT_CELL)
    ap.add_argument("--max-width", type=int, default=DEFAULT_MAX_WIDTH)
    ap.add_argument("--json-only", action="store_true",
                    help="只产出审核 JSON（不渲染图片）")
    args = ap.parse_args(argv)
    manifest = build(args.chapter, out_dir=args.out_dir, cell=args.cell,
                     max_width=args.max_width, json_only=args.json_only)
    # 打印**实际写入目录**（此前打的是 chapter_dir，`--out-dir` 时误导）
    _out = Path(args.out_dir) if args.out_dir else Path(manifest["chapter_dir"])
    print(json.dumps({
        "pages": len(manifest.get("pages") or []),
        "shots": manifest.get("shot_count"),
        "missing": len(manifest.get("missing") or []),
        "upstream_gate_status": manifest.get("upstream_gate_status"),
        "out_dir": str(_out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
