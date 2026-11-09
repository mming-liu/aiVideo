# -*- coding: utf-8 -*-
"""Step02b 首尾帧哈希与差异度预检（M4，2026-11-09）.

纯本地、零网络：只读首帧/尾帧文件，计算 SHA-256 与下采样像素差，输出结构化
预检条目并可追加到**持久化账本**。它不生成视频、不调用视觉模型。

判据（确定性，不做语义猜测）：

- 文件缺失/不可读 → `blocked`（缺素材不能进提交）；
- 首尾帧 SHA-256 相同 → `blocked`（视频模型拿到同一张图，时间上不可能有变化）；
- 下采样灰度平均绝对差 ≤ ``LOW_DIFF_THRESHOLD`` → `ready` + warning（低差异，
  可能是几乎静止的首尾帧；是否可接受由评审决定，不在代码层硬拒）；
- 尺寸不同 → warning（图片模型/后处理可能改过画幅，差异仍可比较）。

账本结构：``step02b_frame_precheck.v1``，按镜头号记录 ff/lf 指纹、差异度、
verdict、检查时间与脚本 revision；重复检查追加到该镜 `attempts`（最多保留
``MAX_ATTEMPTS_PER_SHOT`` 条），不覆盖历史。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

SCHEMA = "step02b_frame_precheck.v1"
LOW_DIFF_THRESHOLD = 0.01
MAX_ATTEMPTS_PER_SHOT = 10
_SAMPLE_SIZE = (32, 32)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(path) -> dict:
    """单帧指纹；不可读时返回 `error` 而不是抛栈（由调用方判 blocked）."""
    p = Path(path)
    try:
        stat = p.stat()
    except OSError as exc:
        return {"path": str(p), "exists": False, "error": f"{type(exc).__name__}: {exc}"}
    data = {"path": str(p), "exists": True, "bytes": int(stat.st_size)}
    try:
        data["sha256"] = sha256_file(p)
    except OSError as exc:
        return {**data, "error": f"{type(exc).__name__}: {exc}"}
    try:
        from PIL import Image
        with Image.open(p) as im:
            data["width"], data["height"] = int(im.width), int(im.height)
            data["format"] = str(im.format or "")
    except Exception as exc:  # Pillow 缺失或文件损坏
        data["decode_error"] = f"{type(exc).__name__}: {exc}"
    return data


def _gray_samples(path):
    from PIL import Image
    with Image.open(path) as im:
        return list(im.convert("L").resize(_SAMPLE_SIZE).tobytes())


def difference(ff_path, lf_path) -> dict:
    """下采样灰度差；返回 `pixel_diff` ∈ [0,1] 与 `changed_ratio` ∈ [0,1]."""
    left = _gray_samples(ff_path)
    right = _gray_samples(lf_path)
    if len(left) != len(right):
        return {"pixel_diff": None, "changed_ratio": None,
                "error": "sample_size_mismatch"}
    total = len(left) or 1
    abs_sum = sum(abs(a - b) for a, b in zip(left, right))
    changed = sum(1 for a, b in zip(left, right) if abs(a - b) > 16)
    return {
        "pixel_diff": round(abs_sum / (255.0 * total), 6),
        "changed_ratio": round(changed / float(total), 6),
    }


def precheck_pair(ff_path, lf_path) -> dict:
    """首尾帧预检：返回 `{verdict, code, problems, warnings, ff, lf, difference}`."""
    problems: list[dict] = []
    warnings: list[dict] = []
    ff = fingerprint(ff_path) if ff_path else {"path": "", "exists": False}
    lf = fingerprint(lf_path) if lf_path else {"path": "", "exists": False}
    for label, item in (("ff", ff), ("lf", lf)):
        if not item.get("exists"):
            problems.append({"code": f"{label}_missing",
                             "message": f"缺少{label}帧文件: {item.get('path') or '（未提供）'}"})
        elif item.get("error"):
            problems.append({"code": f"{label}_unreadable",
                             "message": f"{label}帧不可读: {item['error']}"})
    diff = {}
    if not problems:
        try:
            diff = difference(ff["path"], lf["path"])
        except Exception as exc:
            diff = {"error": f"{type(exc).__name__}: {exc}"}
        if diff.get("error"):
            warnings.append({"code": "diff_unavailable",
                             "message": f"差异度计算失败（不影响哈希）：{diff['error']}"})
        if ff.get("sha256") and ff.get("sha256") == lf.get("sha256"):
            problems.append({"code": "frames_identical",
                             "message": "首帧与尾帧 SHA-256 相同（同一张图不可能产生动作位移）"})
        else:
            if (ff.get("width"), ff.get("height")) != (lf.get("width"), lf.get("height")):
                warnings.append({
                    "code": "frame_size_mismatch",
                    "message": "首帧/尾帧尺寸不同: %sx%s vs %sx%s"
                               % (ff.get("width"), ff.get("height"),
                                  lf.get("width"), lf.get("height")),
                })
            value = diff.get("pixel_diff")
            if isinstance(value, (int, float)) and value <= LOW_DIFF_THRESHOLD:
                warnings.append({
                    "code": "frames_low_difference",
                    "message": "首尾帧下采样灰度差 %.4f ≤ %.4f（可能接近静止；"
                               "是否接受由评审决定）" % (value, LOW_DIFF_THRESHOLD),
                    "pixel_diff": value,
                })
    return {
        "verdict": "blocked" if problems else "ready",
        "code": problems[0]["code"] if problems else "ok",
        "problems": problems,
        "warnings": warnings,
        "ff": ff,
        "lf": lf,
        "difference": diff,
    }


def precheck_shot(shot: dict, frame_paths) -> dict:
    """按镜头预检（从 `frame_paths` 里按文件名认 ff/lf；不猜位置）."""
    ff_path = lf_path = None
    for raw in frame_paths or ():
        name = Path(raw).name.lower()
        if name == "first_frame.png" or name.endswith("_ff.png"):
            ff_path = raw
        elif name == "last_frame.png" or name.endswith("_lf.png"):
            lf_path = raw
    result = precheck_pair(ff_path, lf_path)
    result["shot_index"] = shot.get("index") if isinstance(shot, dict) else None
    result["script_revision"] = str((shot or {}).get("script_revision") or "")
    result["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return result


def load_ledger(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"schema": SCHEMA, "shots": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"首尾帧预检账本不可读: {p}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"首尾帧预检账本身份不符: {p}")
    if not isinstance(data.get("shots"), dict):
        raise ValueError(f"首尾帧预检账本 shots 非法: {p}")
    return data


def record(path, entry: dict) -> dict:
    """把一条预检结果追加进账本（按镜头号保留最近 N 次），原子替换落盘."""
    data = load_ledger(path)
    index = str(entry.get("shot_index"))
    bucket = data["shots"].setdefault(index, {"attempts": []})
    attempts = bucket.setdefault("attempts", [])
    attempts.append(entry)
    if len(attempts) > MAX_ATTEMPTS_PER_SHOT:
        del attempts[:-MAX_ATTEMPTS_PER_SHOT]
    bucket["latest"] = entry
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return data


__all__ = ["LOW_DIFF_THRESHOLD", "SCHEMA", "difference", "fingerprint",
           "load_ledger", "precheck_pair", "precheck_shot", "record", "sha256_file"]
