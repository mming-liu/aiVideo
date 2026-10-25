# -*- coding: utf-8 -*-
"""剧情诊断预览：把冻结计划渲染成**可播放的静音文字粗剪**（0 次模型调用）.

只回答"故事顺序、正文完整性、声音类别安排和估算节奏"，不证明正式制作通过：

- 每页一幅中性文字画面（Pillow 显式字体，不靠 ffmpeg drawtext 转义）；
- 逐页占位片段 → :class:`MergeTimeline` → 现有 FFmpeg 后端 ``render()``；
- 预览编号只用于渲染：``scope="plan_preview"``、revision=**完整计划摘要**，
  整数编号 → ``beat_uid/page_index`` 的映射只写预览清单，不回写计划；
- 声音按每条节拍**当前绑定逐次展示**（不做去重）；未绑定声音进入明确分隔的附录；
- 片头固定 2 秒；先量化各段帧数，再按字符权重分页，余数按页序分配，总帧数守恒；
- 静音 AAC 由后端补（``has_audio=False`` → ``anullsrc``），不伪造配音；
- 所有产物写 ``--work-root`` 下的隔离目录，标记 ``diagnostic_preview=true``，
  不写 ``step01_commit.json``、不进正式采纳清单。

用法::

    py -3.14 tools/build_plan_preview.py \
        --plan .p1a_exp/p4_stability_<组>/candidate_content.json \
        --novel output/step01_ab/<...>/novel.txt \
        --work-root .p1a_exp \
        --expect-plan-digest <64 位> --expect-novel-digest <64 位> \
        --font C:\\Windows\\Fonts\\msyh.ttc
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PREVIEW_SCOPE = "plan_preview"
SCHEMA = "plan_preview.v1"
BANNER = "诊断预览 · 未配音 · 未准入"
TITLE_SEC = 2.0
APPENDIX_HEADER_SEC = 2.0
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 1280
DEFAULT_FPS = 24
DEFAULT_VISUAL_SEC = 3.0
MAX_PAGES_PER_SEGMENT = 80

#: 版本冻结清单（只记录，不比对）。
FREEZE_FILES = (
    "tools/build_plan_preview.py",
    "step04_merge/timeline.py",
    "step04_merge/backends/ffmpeg.py",
    "shared/presentation_contract.py",
    "shared/media_contract.py",
    "shared/subtitle_contract.py",
    "step01_script_gen/plan_feasibility.py",
    "step01_script_gen/timecode.py",
)

_GUARD = {"service_attempts": 0}


class PreviewError(RuntimeError):
    """预览拒收/失败（结构化 code/stage，供退出码与报告使用）。"""

    def __init__(self, code: str, message: str, *, stage: str = "input", **fields):
        super().__init__(message)
        self.code = str(code)
        self.stage = str(stage)
        self.message = str(message)
        self.fields = dict(fields)

    def payload(self) -> dict:
        return {"schema": "plan_preview.failure.v1", "code": self.code,
                "stage": self.stage, "message": self.message, **self.fields}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _sha256_text(text) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_json(path: Path, value) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(str(text), encoding="utf-8")


def _git_facts() -> dict:
    out = {"head": None, "status_porcelain_sha256": None, "error": ""}
    try:
        _r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=20)
        if _r.returncode == 0:
            out["head"] = _r.stdout.strip() or None
        _s = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=20)
        if _s.returncode == 0:
            out["status_porcelain_sha256"] = _sha256_text(_s.stdout)
    except Exception as exc:                                         # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def _code_freeze() -> dict:
    rows = []
    for rel in FREEZE_FILES:
        p = ROOT / rel
        try:
            rows.append({"path": rel, "sha256": _sha256_file(p)})
        except OSError as exc:
            rows.append({"path": rel, "sha256": None, "error": str(exc)})
    return {"git": _git_facts(), "files": rows}


# ---------------------------------------------------------------------------
# 输入冻结（零写入）
# ---------------------------------------------------------------------------
def _load_inputs(plan_path: Path, novel_path: Path, *, expect_plan_digest: str,
                 expect_novel_digest: str) -> dict:
    from shared import presentation_contract as pc
    from shared.content_verification import plan_digest_of

    plan_path, novel_path = Path(plan_path), Path(novel_path)
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PreviewError("plan_unreadable", "计划不可读：%s" % exc,
                           path=str(plan_path)) from exc
    except json.JSONDecodeError as exc:
        raise PreviewError("plan_not_json", "计划不是合法 JSON：%s" % exc,
                           path=str(plan_path)) from exc
    if not isinstance(plan, dict):
        raise PreviewError("plan_not_object", "计划不是 JSON 对象",
                           path=str(plan_path))
    stripped = {k: v for k, v in plan.items()
                if k != "plan_verification" and not str(k).startswith("verification")}
    view, problems = pc.view_from_plan(stripped)
    if view is None or problems:
        raise PreviewError(
            "view_unreadable", "结构化视图不可读",
            problems=[{"code": str((p or {}).get("code") or ""),
                       "path": str((p or {}).get("path") or ""),
                       "uid": str((p or {}).get("uid") or "")}
                      for p in (problems or [])[:20]])
    digest = str(plan_digest_of(stripped))
    try:
        novel = novel_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreviewError("novel_unreadable", "原文不可读：%s" % exc,
                           path=str(novel_path)) from exc
    if not novel.strip():
        raise PreviewError("novel_empty", "原文为空", path=str(novel_path))
    novel_digest = _sha256_text(novel)

    bad = []
    if not str(expect_plan_digest or "").strip():
        bad.append("expect_plan_digest_missing")
    elif digest != str(expect_plan_digest).strip():
        bad.append("plan_digest_mismatch")
    if not str(expect_novel_digest or "").strip():
        bad.append("expect_novel_digest_missing")
    elif novel_digest != str(expect_novel_digest).strip():
        bad.append("novel_digest_mismatch")
    if bad:
        raise PreviewError("digest_mismatch", "冻结摘要缺失或不符：%s" % bad,
                           codes=bad, plan_digest=digest, novel_digest=novel_digest)
    return {"plan": stripped, "view": view, "plan_digest": digest,
            "plan_file_sha256": _sha256_file(plan_path),
            "novel": novel, "novel_digest": novel_digest,
            "novel_file_sha256": _sha256_file(novel_path),
            "plan_path": str(plan_path), "novel_path": str(novel_path)}


def _voice_index(view: dict) -> dict:
    """声音身份单源：``{uid: (kind, label, text)}``; kind ∈ dialogue/narration."""
    out = {}
    for unit in view.get("dialogue_units") or []:
        uid = str(unit.get("uid") or "")
        if uid:
            out[uid] = {"kind": "dialogue", "label": "对白",
                        "text": str(unit.get("text") or "")}
    for unit in view.get("narration_units") or []:
        uid = str(unit.get("uid") or "")
        if uid:
            out[uid] = {"kind": "narration", "label": "旁白",
                        "text": str(unit.get("text") or "")}
    return out


def _beat_records(view: dict, voices: dict) -> tuple:
    """节拍 → 记录；返回 ``(records, unbound_voice_uids)``.

    声音按**每条节拍当前绑定**逐次展示，不做去重；``visual`` 自指不当作声音。
    """
    records, used = [], set()
    for beat in view.get("beats") or []:
        uid = str(beat.get("uid") or "")
        ref = beat.get("voice_ref") if isinstance(beat.get("voice_ref"), dict) else {}
        ref_uid = str(ref.get("uid") or "")
        ref_kind = str(ref.get("kind") or "")
        voice = voices.get(ref_uid) if ref_kind in ("dialogue", "narration") else None
        if voice is not None:
            used.add(ref_uid)
        records.append({
            "beat_uid": uid, "action_text": str(beat.get("text") or ""),
            "voice_uid": ref_uid if voice is not None else "",
            "voice_kind": voice["kind"] if voice is not None else "",
            "voice_label": voice["label"] if voice is not None else "",
            "voice_text": voice["text"] if voice is not None else "",
        })
    unbound = [uid for uid in voices if uid not in used]
    return records, unbound


# ---------------------------------------------------------------------------
# 分页 / 帧数（纯函数，供离线用例直接断言）
# ---------------------------------------------------------------------------
#: 不能出现在行首/行尾的标点（简单禁则，避免“句号独占一行”“左括号落行尾”）。
_NO_LINE_START = "，。、；：？！）】》」』”’%,.!?:;)]}"
_NO_LINE_END = "（【《「『“‘([{"


def _wrap_lines(text, max_width, measure) -> list:
    """按测量函数折行；保留显式换行与空行，并做最小的中文行首行尾禁则."""
    lines = []
    for para in str(text or "").split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if not cur or measure(trial) <= max_width:
                cur = trial
                continue
            if ch in _NO_LINE_START and len(cur) > 1:
                carry = cur[-1]
                cur = cur[:-1]
                lines.append(cur)
                cur = carry + ch
            elif cur[-1] in _NO_LINE_END and len(cur) > 1:
                carry = cur[-1]
                cur = cur[:-1]
                lines.append(cur)
                cur = carry + ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def _paginate(lines: list, per_page: int) -> list:
    per_page = max(1, int(per_page))
    if not lines:
        return [[""]]
    return [lines[i:i + per_page] for i in range(0, len(lines), per_page)]


def _allocate_frames(total_frames: int, pages: list) -> list:
    """先定段总帧数，再按字符权重分页：每页≥1 帧，余数按页序分配，总数守恒."""
    n = len(pages)
    total = max(int(total_frames), n, 1)
    weights = [max(1, sum(len(str(x)) for x in page)) for page in pages]
    base = [1] * n
    remaining = total - n
    if remaining <= 0:
        return base
    wsum = float(sum(weights))
    alloc = [int(remaining * w / wsum) for w in weights]
    left = remaining - sum(alloc)
    for i in range(left):
        alloc[i % n] += 1
    return [b + a for b, a in zip(base, alloc)]


def _build_segments(view: dict, *, measure, max_width: int, lines_per_page: int,
                    voice_seconds_fn, visual_sec: float, fps: int,
                    reading_cps: float = 6.0,
                    title_sec: float = TITLE_SEC) -> dict:
    """把视图编排成分页片段（**不发模型、不写文件**）.

    时长口径：每页取 ``max(发声估时按字符权重分摊, 正文字符数 / reading_cps)``，
    即**阅读停留不低于读得完的时间**；发声估时单独记在 ``voice_*_sec`` 字段，
    不把阅读停留冒充正式镜头预算。

    返回 ``{"segments", "records", "unbound", "voice_stats", "title"}``。
    """
    from shared.media_contract import quantize_frame_count

    cps = float(reading_cps)
    if not (0.5 <= cps <= 40.0):
        raise PreviewError("bad_reading_cps", "阅读速度参数越界：%r" % reading_cps,
                           stage="layout")
    voices = _voice_index(view)
    records, unbound = _beat_records(view, voices)
    segments = []

    def _add(*, kind, beat_uid, page, pages, action_text, voice_uid, voice_kind,
             voice_label, voice_text, lines, frames, extra=None):
        seg = {
            "index": len(segments) + 1, "kind": kind,
            "beat_uid": beat_uid, "page": page, "pages": pages,
            "action_text": action_text, "voice_uid": voice_uid,
            "voice_kind": voice_kind, "voice_label": voice_label,
            "voice_text": voice_text,
            "lines": list(lines), "text": "\n".join(lines),
            "frames": int(frames),
        }
        if extra:
            seg.update(extra)
        segments.append(seg)
        return seg

    def _page_dwell(page, voice_frames, voice_share_sec):
        chars = sum(1 for line in page for ch in line if not ch.isspace())
        read_sec = chars / cps
        read_frames = max(1, quantize_frame_count(read_sec, fps))
        frames = max(int(voice_frames), read_frames)
        return {"chars": chars, "reading_sec": round(read_sec, 3),
                "reading_frames": read_frames, "voice_share_sec": round(voice_share_sec, 3),
                "dwell_basis": "reading" if read_frames > int(voice_frames) else "voice",
                "frames": frames}

    # 片头（固定时长，单独计入清单）
    title_frames = max(1, int(round(float(title_sec) * fps)))
    _add(kind="title", beat_uid="", page=1, pages=1, action_text="",
         voice_uid="", voice_kind="", voice_label="", voice_text="",
         lines=[], frames=title_frames,
         extra={"dwell_basis": "fixed", "chars": 0, "reading_sec": 0.0,
                "voice_share_sec": 0.0, "reading_frames": 0})

    # 节拍
    for rec in records:
        body = _wrap_lines(rec["action_text"], max_width, measure)
        if rec["voice_text"]:
            body += ["", "[%s %s]" % (rec["voice_label"], rec["voice_uid"])]
            body += _wrap_lines(rec["voice_text"], max_width, measure)
        pages_lines = _paginate(body, lines_per_page)
        if len(pages_lines) > MAX_PAGES_PER_SEGMENT:
            raise PreviewError("pages_exceeded",
                               "节拍 %s 分页 %d 超过上限 %d"
                               % (rec["beat_uid"], len(pages_lines),
                                  MAX_PAGES_PER_SEGMENT),
                               stage="layout", beat_uid=rec["beat_uid"])
        est = float(voice_seconds_fn([{"text": rec["voice_text"]}])) \
            if rec["voice_text"] else float(visual_sec)
        planned = max(1, quantize_frame_count(est, fps))
        voice_alloc = _allocate_frames(planned, pages_lines)
        weights = [max(1, sum(len(x) for x in page)) for page in pages_lines]
        wsum = float(sum(weights))
        for i, (page, voice_frames) in enumerate(zip(pages_lines, voice_alloc), 1):
            dwell = _page_dwell(page, voice_frames, est * weights[i - 1] / wsum)
            _add(kind="beat", beat_uid=rec["beat_uid"], page=i,
                 pages=len(pages_lines), action_text=rec["action_text"],
                 voice_uid=rec["voice_uid"], voice_kind=rec["voice_kind"],
                 voice_label=rec["voice_label"], voice_text=rec["voice_text"],
                 lines=page, frames=dwell["frames"],
                 extra={"estimate_sec": round(est, 3),
                        "voice_total_sec": round(est, 3), **dwell})

    # 未绑定声音附录（明确分隔，不伪装成故事节拍）
    appendix = []
    if unbound:
        _add(kind="appendix_header", beat_uid="", page=1, pages=1,
             action_text="", voice_uid="", voice_kind="", voice_label="",
             voice_text="", lines=[],
             frames=max(1, int(round(APPENDIX_HEADER_SEC * fps))),
             extra={"dwell_basis": "fixed", "chars": 0, "reading_sec": 0.0,
                    "voice_share_sec": 0.0, "reading_frames": 0})
        for uid in unbound:
            voice = voices[uid]
            body = ["[%s %s]" % (voice["label"], uid)]
            body += _wrap_lines(voice["text"], max_width, measure)
            pages_lines = _paginate(body, lines_per_page)
            est = float(voice_seconds_fn([{"text": voice["text"]}]))
            planned = max(1, quantize_frame_count(est, fps))
            voice_alloc = _allocate_frames(planned, pages_lines)
            weights = [max(1, sum(len(x) for x in page)) for page in pages_lines]
            wsum = float(sum(weights))
            for i, (page, voice_frames) in enumerate(zip(pages_lines, voice_alloc), 1):
                dwell = _page_dwell(page, voice_frames, est * weights[i - 1] / wsum)
                seg = _add(kind="appendix", beat_uid="", page=i,
                           pages=len(pages_lines), action_text="",
                           voice_uid=uid, voice_kind=voice["kind"],
                           voice_label=voice["label"], voice_text=voice["text"],
                           lines=page, frames=dwell["frames"],
                           extra={"estimate_sec": round(est, 3),
                                  "voice_total_sec": round(est, 3), **dwell})
                appendix.append(seg["index"])

    usage = {}
    for rec in records:
        if rec["voice_uid"]:
            usage.setdefault(rec["voice_uid"], []).append(rec["beat_uid"])
    voice_stats = {
        "source_entries": len(voices),
        "beat_displays": sum(1 for r in records if r["voice_uid"]),
        "appendix_displays": len(appendix),
        "reused": {uid: beats for uid, beats in usage.items() if len(beats) > 1},
        "unbound": list(unbound),
        "appendix_segments": appendix,
    }
    return {"segments": segments, "records": records, "unbound": unbound,
            "voice_stats": voice_stats, "title_frames": title_frames}


# ---------------------------------------------------------------------------
# 画面 / 占位片段 / 时间线 / 渲染
# ---------------------------------------------------------------------------
def _font_sizes(width: int) -> dict:
    margin = max(12, int(width * 0.05))
    body = max(12, int((width - 2 * margin) / 20))
    return {"margin": margin, "body": body,
            "head": max(11, int(body * 0.60)),
            "small": max(10, int(body * 0.48)),
            "title": max(18, int(body * 1.55))}


def _layout_metrics(width: int, height: int, sizes: dict) -> dict:
    line_h = max(1, int(sizes["body"] * 1.55))
    body_top = sizes["margin"] + int(sizes["head"] * 2.4)
    footer_h = int(sizes["small"] * 3.2)
    avail = height - body_top - footer_h
    return {"line_h": line_h, "body_top": body_top, "footer_h": footer_h,
            "lines_per_page": max(1, avail // line_h),
            "max_width": width - 2 * sizes["margin"]}


def _fit_title(lines, font_path, sizes: dict, max_width: int):
    """片头按标题字号折行；仍超宽就降字号，绝不左右越界."""
    from PIL import ImageFont
    for key in ("title", "head", "body"):
        font = ImageFont.truetype(str(font_path), sizes[key])
        wrapped = []
        for line in lines:
            wrapped += _wrap_lines(line, max_width, font.getlength)
        if wrapped and max(font.getlength(x) for x in wrapped) <= max_width:
            return font, wrapped
    raise PreviewError("title_overflow", "片头文字在最小字号下仍超出宽度",
                       stage="layout")


def _draw_page(path: Path, *, width: int, height: int, lines: list, sizes: dict,
               metrics: dict, header_right: str,
               footer_left: str, footer_right: str, fonts: dict,
               kind: str = "beat") -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), (16, 16, 20))
    draw = ImageDraw.Draw(img)
    margin = sizes["margin"]
    orange, grey, white = (255, 159, 67), (150, 150, 150), (232, 232, 232)
    draw.text((margin, margin), BANNER, font=fonts["head"], fill=orange)
    if header_right:
        w = draw.textlength(header_right, font=fonts["head"])
        draw.text((width - margin - w, margin), header_right,
                  font=fonts["head"], fill=grey)
    if kind == "title":
        font = fonts["title"]
        line_h = max(1, int(font.size * 1.7))
        cy = max(margin * 2, (height - len(lines) * line_h) // 2)
        for text in lines:
            w = draw.textlength(text, font=font)
            draw.text(((width - w) / 2, cy), text, font=font, fill=white)
            cy += line_h
    elif kind == "appendix_header":
        cy = height // 2 - sizes["title"]
        for text in lines:
            w = draw.textlength(text, font=fonts["title"])
            draw.text(((width - w) / 2, cy), text, font=fonts["title"], fill=orange)
            cy += int(sizes["title"] * 1.7)
    else:
        y = metrics["body_top"]
        for line in lines:
            draw.text((margin, y), line, font=fonts["body"], fill=white)
            y += metrics["line_h"]
    fy = height - margin - int(sizes["small"] * 1.4)
    draw.text((margin, fy), footer_left, font=fonts["small"], fill=grey)
    if footer_right:
        w = draw.textlength(footer_right, font=fonts["small"])
        draw.text((width - margin - w, fy), footer_right,
                  font=fonts["small"], fill=grey)
    img.save(str(path))


def _encode_still(ffmpeg: str, png: Path, out: Path, *, frames: int, fps: int,
                  width: int, height: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-i", str(png),
           "-frames:v", str(int(frames)), "-r", str(int(fps)),
           "-vf", "scale=%d:%d,format=yuv420p" % (int(width), int(height)),
           "-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p",
           "-an", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise PreviewError("clip_encode_failed",
                           "占位片段编码失败：%s" % (proc.stderr or "")[-400:],
                           stage="encode", png=str(png))


def _segment_header(seg: dict) -> str:
    if seg["kind"] == "title":
        return "片头 · 2.0s"
    if seg["kind"] == "appendix_header":
        return "附录分隔"
    if seg["kind"] == "appendix":
        return "附录 %s · %d/%d" % (seg["voice_uid"], seg["page"], seg["pages"])
    return "%s · %d/%d" % (seg["beat_uid"] or "?", seg["page"], seg["pages"])


def _install_service_guard() -> None:
    """预览只读计划：任何服务构造立即失败并计数（证明 0 次模型调用）."""
    import step01_script_gen.outline as outline
    _orig = outline.create_service

    def _blocked(*_a, **_k):
        _GUARD["service_attempts"] += 1
        raise PreviewError("service_construction_blocked",
                           "诊断预览不得构造文本服务", stage="guard")

    outline.create_service = _blocked
    return _orig


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def _storyboard_md(manifest: dict) -> str:
    lines = ["# 剧情预览分镜表（诊断预览，非正式制作）", "",
             "- 计划摘要：`%s`" % manifest["inputs"]["plan_digest"],
             "- 原文摘要：`%s`" % manifest["inputs"]["novel_digest"],
             "- 输出：`%s`（%d×%d@%d，约 %.2f s）"
             % (manifest["output"]["path"], manifest["params"]["width"],
                manifest["params"]["height"], manifest["params"]["fps"],
                manifest["total_sec"]),
             "- **未配音、未准入**：发声时长按字数估算；每页停留取"
             "“发声估时分摊”与“正文字数/%.1f 字每秒”的较大者，"
             "只为读得完，不是正式镜头预算。" % manifest["params"]["reading_cps"], "",
             "| # | 时间码 | 节拍 | 页 | 类型 | 声音 | 帧 | 秒 | 依据 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for seg in manifest["segments"]:
        kind = {"title": "片头", "beat": "节拍", "appendix": "附录声音",
                "appendix_header": "附录分隔"}.get(seg["kind"], seg["kind"])
        voice = ("%s %s" % (seg["voice_label"], seg["voice_uid"])
                 if seg["voice_uid"] else "—")
        lines.append("| %03d | %s–%s | %s | %d/%d | %s | %s | %d | %.2f | %s |"
                     % (seg["index"], _tc(seg["start_sec"]), _tc(seg["end_sec"]),
                        seg["beat_uid"] or "—", seg["page"], seg["pages"], kind,
                        voice, seg["frames"], seg["duration_sec"],
                        seg.get("dwell_basis") or "—"))
    lines += ["", "## 逐页全文", ""]
    for seg in manifest["segments"]:
        lines += ["### %03d · %s · %s–%s"
                  % (seg["index"],
                     seg["beat_uid"] or ("片头" if seg["kind"] == "title" else "附录"),
                     _tc(seg["start_sec"]), _tc(seg["end_sec"])), ""]
        if seg["kind"] == "title":
            lines += ["> 片头（2 秒）：诊断预览，未配音、未准入。", ""]
        elif seg["kind"] == "appendix_header":
            lines += ["> **未绑定声音附录**（不属于故事节拍）。", ""]
        else:
            lines += ["```text", seg["text"] or "（空）", "```", ""]
    return "\n".join(lines)


def _reading_view_md(manifest: dict, view: dict, records: list,
                     acceptance: list) -> str:
    facts_by_carrier = {}
    for row in view.get("fact_bindings") or []:
        for carrier in row.get("carriers") or []:
            facts_by_carrier.setdefault(str(carrier.get("uid") or ""), []).append(
                str(row.get("fact_uid") or ""))
    facts = {str(f.get("uid")): f for f in view.get("frozen_facts") or []}
    codes_by_uid = {}
    global_codes = []
    for item in acceptance or []:
        uid = str(item.get("uid") or "")
        if uid:
            codes_by_uid.setdefault(uid, []).append(str(item.get("code") or ""))
        else:
            global_codes.append(str(item.get("code") or ""))

    lines = ["# 剧情阅读版（诊断预览，非正式制作）", "",
             "计划摘要：`%s`；原文摘要：`%s`"
             % (manifest["inputs"]["plan_digest"], manifest["inputs"]["novel_digest"]),
             "",
             "声音统计：源条目 %d，节拍展示 %d 次，附录 %d 次；复用 %s。"
             % (manifest["counts"]["voice_source_entries"],
                manifest["counts"]["voice_beat_displays"],
                manifest["counts"]["voice_appendix_displays"],
                json.dumps(manifest["counts"]["voice_reused"], ensure_ascii=False)),
             ""]
    if global_codes:
        lines += ["统一验收的全局标记：%s" % "、".join(sorted(set(global_codes))), ""]
    for rec in records:
        lines += ["## %s%s" % (rec["beat_uid"],
                               (" · %s %s" % (rec["voice_label"], rec["voice_uid"]))
                               if rec["voice_uid"] else " · 纯画面"), ""]
        lines += ["**画面**：%s" % (rec["action_text"] or "（空）"), ""]
        if rec["voice_uid"]:
            lines += ["**%s（%s）**：%s"
                      % (rec["voice_label"], rec["voice_uid"],
                         rec["voice_text"] or "（空）"), ""]
        uids = []
        for key in (rec["beat_uid"], rec["voice_uid"]):
            for fid in facts_by_carrier.get(key) or []:
                if fid not in uids:
                    uids.append(fid)
        if uids:
            lines += ["**承载事实**：%s" % "、".join(
                "%s（%s）" % (fid, str((facts.get(fid) or {}).get("claim") or "")[:60])
                for fid in uids), ""]
        codes = sorted(set(codes_by_uid.get(rec["beat_uid"], [])
                           + codes_by_uid.get(rec["voice_uid"], [])))
        lines += ["**准入标记**：%s" % ("、".join(codes) if codes else "（无）"), "",
                  "**评审注记**：", ""]
    if manifest["counts"]["unbound"]:
        lines += ["## 未绑定声音附录", ""]
        for uid in manifest["counts"]["unbound"]:
            lines += ["- `%s`：见分镜表附录段" % uid]
        lines += [""]
    return "\n".join(lines)


def _tc(sec: float) -> str:
    sec = max(0.0, float(sec))
    m, s = divmod(sec, 60.0)
    return "%02d:%05.2f" % (int(m), s)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def build_plan_preview(plan_path, novel_path, work_root, *,
                       expected_plan_digest: str, expected_novel_digest: str,
                       font_path, width: int = DEFAULT_WIDTH,
                       height: int = DEFAULT_HEIGHT, visual_sec: float = DEFAULT_VISUAL_SEC,
                       reading_cps: float = 6.0,
                       timeout_sec: float = 600.0) -> dict:
    from shared.media_contract import MERGE_FPS
    from shared.paths import default_output_base, enhancement_delivery_root

    _GUARD["service_attempts"] = 0
    width, height = int(width), int(height)
    if width < 160 or height < 160:
        raise PreviewError("bad_dimensions", "预览尺寸过小：%dx%d" % (width, height))
    if not (0.5 <= float(visual_sec) <= 30.0):
        raise PreviewError("bad_visual_sec", "纯画面停留参数越界：%r" % visual_sec)
    fps = int(MERGE_FPS)  # 与合并后端同一帧率（24）

    root = Path(work_root).expanduser().resolve()
    formal = default_output_base().resolve()
    if root == formal or root.is_relative_to(formal):
        raise PreviewError("work_root_in_formal_output",
                           "work-root 不得落在正式输出根或其子目录：%s" % root,
                           path=str(root))

    info = _load_inputs(plan_path, novel_path,
                        expect_plan_digest=expected_plan_digest,
                        expect_novel_digest=expected_novel_digest)

    # 字体与字形覆盖：在创建任何产物之前失败
    from PIL import ImageFont
    from shared.subtitle_contract import load_font_spec, require_glyph_coverage
    try:
        font_spec = load_font_spec(str(font_path))
        all_texts = [BANNER, "剧情预览 · 诊断", "未绑定声音附录（不属于故事节拍）"]
        for beat in info["view"].get("beats") or []:
            all_texts.append(str(beat.get("text") or ""))
        for unit in ((info["view"].get("dialogue_units") or [])
                     + (info["view"].get("narration_units") or [])):
            all_texts.append(str(unit.get("text") or ""))
        require_glyph_coverage(font_spec, all_texts)
    except PreviewError:
        raise
    except Exception as exc:                                         # noqa: BLE001
        raise PreviewError("font_rejected", "字体不可用或缺字：%s" % exc,
                           stage="font", path=str(font_path)) from exc

    run_dir = (enhancement_delivery_root(base=root)
               / ("plan_preview_%s_%03d"
                  % (time.strftime("%Y%m%dT%H%M%S"),
                     int(time.time() * 1000) % 1000)))
    run_dir.mkdir(parents=True, exist_ok=False)
    # **导入前隔离日志**：timecode 在大纲链上模块级建 logger，绑 OUTPUT_BASE。
    os.environ["OUTPUT_BASE"] = str(run_dir / "output")
    os.environ["STEP01_LOG_PATH"] = str(run_dir / "step01.log")

    _orig_service = None
    try:
        from step01_script_gen import plan_feasibility as pf
        from step01_script_gen import outline as o

        _orig_service = _install_service_guard()

        sizes = _font_sizes(width)
        metrics = _layout_metrics(width, height, sizes)
        fonts = {
            "body": ImageFont.truetype(str(font_spec.path), sizes["body"]),
            "head": ImageFont.truetype(str(font_spec.path), sizes["head"]),
            "small": ImageFont.truetype(str(font_spec.path), sizes["small"]),
            "title": ImageFont.truetype(str(font_spec.path), sizes["title"]),
        }
        plan_spec = _build_segments(
            info["view"], measure=lambda s: fonts["body"].getlength(s),
            max_width=metrics["max_width"],
            lines_per_page=metrics["lines_per_page"],
            voice_seconds_fn=pf.voice_seconds, visual_sec=float(visual_sec),
            fps=fps, reading_cps=float(reading_cps), title_sec=TITLE_SEC)
        segments = plan_spec["segments"]
        if len(segments) < 2:
            raise PreviewError("no_beats", "视图没有可展示的节拍", stage="layout")

        acceptance = [{"code": str((p or {}).get("code") or ""),
                       "uid": str((p or {}).get("uid") or ""),
                       "path": str((p or {}).get("path") or "")}
                      for p in (o.plan_acceptance_problems(
                          info["plan"], info["novel"]) or [])]

        # 画面 + 占位片段
        frames_dir = run_dir / "frames"
        clips_dir = run_dir / "clips"
        frames_dir.mkdir(parents=True, exist_ok=True)
        clips_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_bin = "ffmpeg"
        if not _which(ffmpeg_bin):
            raise PreviewError("ffmpeg_missing", "找不到 ffmpeg", stage="encode")
        from step04_merge.backends.ffmpeg import probe_media

        clip_infos = []
        for seg in segments:
            header = _segment_header(seg)
            png = frames_dir / ("seg_%03d_p%d.png" % (seg["index"], seg["page"]))
            if seg["kind"] == "title":
                title_lines = ["剧情预览（诊断）",
                               "未配音 · 未准入 · 仅供评阅",
                               "计划 %s… / 原文 %s…"
                               % (info["plan_digest"][:12], info["novel_digest"][:12])]
                title_font, title_wrapped = _fit_title(
                    title_lines, font_spec.path, sizes, metrics["max_width"])
                _draw_page(png, width=width, height=height, lines=title_wrapped,
                           sizes=sizes, metrics=metrics, header_right="",
                           footer_left="diagnostic preview",
                           footer_right="",
                           fonts=dict(fonts, title=title_font), kind="title")
            elif seg["kind"] == "appendix_header":
                _draw_page(png, width=width, height=height,
                           lines=["未绑定声音附录", "以下条目不属于故事节拍"],
                           sizes=sizes, metrics=metrics, header_right=header,
                           footer_left="diagnostic preview",
                           footer_right="", fonts=fonts, kind="appendix_header")
            else:
                _draw_page(png, width=width, height=height, lines=seg["lines"],
                           sizes=sizes, metrics=metrics, header_right=header,
                           footer_left="≈ %.2fs（估算）"
                                       % (seg["frames"] / float(fps)),
                           footer_right="%03d/%03d" % (seg["index"], len(segments)),
                           fonts=fonts, kind="beat")
            clip = clips_dir / ("seg_%03d.mp4" % seg["index"])
            _encode_still(ffmpeg_bin, png, clip, frames=seg["frames"], fps=fps,
                          width=width, height=height)
            info_media = probe_media(clip)
            clip_infos.append({"path": clip, "raw_duration_sec": info_media.duration_sec})

        # MergeTimeline（预览身份，不回写计划）
        from step04_merge.timeline import (ClipEntry, MergeTimeline,
                                           stable_shot_id, validate_timeline)
        entries = []
        for i, (seg, clip) in enumerate(zip(segments, clip_infos), 1):
            entries.append(ClipEntry(
                shot_index=i, kind="shot",
                stable_id=stable_shot_id(PREVIEW_SCOPE, info["plan_digest"],
                                         "shot", i),
                path=clip["path"], duration_sec=0.0,
                raw_duration_sec=float(clip["raw_duration_sec"]), frames=0,
                has_audio=False, source="preview"))
        timeline = MergeTimeline(
            scope=PREVIEW_SCOPE, script_revision=info["plan_digest"],
            width=width, height=height, fps=fps, clips=tuple(entries),
            boundary_transitions=(), endpoint_effects=(), audio_mode="native",
            audio_tracks=(), subtitle_plan=None, overlays=(),
            source={"diagnostic_preview": True, "not_for_adoption": True,
                    "plan_digest": info["plan_digest"],
                    "novel_digest": info["novel_digest"]})
        validated = validate_timeline(timeline)

        from step04_merge.backends.ffmpeg import EncodeSettings, render
        out_path = run_dir / "preview.mp4"
        settings = EncodeSettings(width=width, height=height, loudnorm=False,
                                  timeout_sec=float(timeout_sec))
        res = render(validated, out_path, run_dir / "render",
                     settings=settings, evidence_dir=run_dir / "evidence")

        # 媒体检查
        got = probe_media(out_path)
        expected = float(validated.total_duration_sec)
        tol = 1.0 / fps + 1e-6
        decode = subprocess.run([ffmpeg_bin, "-v", "error", "-i", str(out_path),
                                 "-f", "null", "-"], capture_output=True,
                                text=True, timeout=600)
        checks = {
            "duration_sec": got.duration_sec,
            "expected_duration_sec": expected,
            "duration_within_one_frame": abs(got.duration_sec - expected) <= tol,
            "width": got.width, "height": got.height,
            "fps": got.fps, "has_audio": bool(got.has_audio),
            "decode_returncode": int(decode.returncode),
            "decode_ok": decode.returncode == 0,
        }
        bad = [k for k in ("duration_within_one_frame", "has_audio", "decode_ok")
               if not checks[k]]
        if (got.width, got.height) != (width, height):
            bad.append("dimensions")
        if abs(float(got.fps) - float(fps)) > 0.01:
            bad.append("fps")
        if bad:
            raise PreviewError("media_check_failed", "产物检查未通过：%s" % bad,
                               stage="verify", checks=checks)

        # 清单
        windows = validated.windows
        manifest_segments = []
        for seg, window in zip(segments, windows):
            row = {k: seg[k] for k in ("index", "kind", "beat_uid", "page", "pages",
                                       "voice_uid", "voice_kind", "voice_label",
                                       "voice_text", "action_text", "text",
                                       "frames", "estimate_sec", "voice_total_sec",
                                       "voice_share_sec", "reading_sec",
                                       "reading_frames", "dwell_basis", "chars")
                   if k in seg}
            row.update({
                "start_sec": round(window.start_sec, 6),
                "end_sec": round(window.end_sec, 6),
                "duration_sec": round(window.duration_sec, 6),
                "text_sha256": _sha256_text(seg["text"]),
            })
            manifest_segments.append(row)

        stats = plan_spec["voice_stats"]
        manifest = {
            "schema": SCHEMA, "diagnostic_preview": True,
            "not_for_adoption": True,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_dir": str(run_dir),
            "inputs": {
                "plan_path": info["plan_path"],
                "plan_digest": info["plan_digest"],
                "plan_file_sha256": info["plan_file_sha256"],
                "novel_path": info["novel_path"],
                "novel_digest": info["novel_digest"],
                "novel_file_sha256": info["novel_file_sha256"],
                "facts": [str(f.get("uid")) for f in
                          (info["view"].get("frozen_facts") or [])],
                "beats": [str(b.get("uid")) for b in
                          (info["view"].get("beats") or [])],
            },
            "params": {"width": width, "height": height, "fps": fps,
                       "visual_sec": float(visual_sec), "title_sec": TITLE_SEC,
                       "reading_cps": float(reading_cps),
                       "font": str(font_spec.path), "font_sha256": font_spec.sha256,
                       "font_sizes": sizes, "lines_per_page":
                           metrics["lines_per_page"]},
            "freeze": _code_freeze(),
            "counts": {
                "beats": len(info["view"].get("beats") or []),
                "segments": len(manifest_segments),
                "voice_source_entries": stats["source_entries"],
                "voice_beat_displays": stats["beat_displays"],
                "voice_appendix_displays": stats["appendix_displays"],
                "voice_reused": stats["reused"],
                "unbound": stats["unbound"],
                "appendix_segments": stats["appendix_segments"],
            },
            "acceptance_codes": acceptance,
            "segments": manifest_segments,
            "total_frames": sum(int(s["frames"]) for s in manifest_segments),
            "total_sec": round(float(validated.total_duration_sec), 6),
            "output": {"path": str(out_path), "sha256": res.sha256,
                       "bytes": res.bytes, "duration_sec": res.duration_sec,
                       "width": res.width, "height": res.height, "fps": res.fps,
                       "has_audio": res.has_audio, "ffmpeg_version":
                           res.ffmpeg_version, "stages": list(res.stages)},
            "guard": {"service_construction_attempts": _GUARD["service_attempts"],
                      "model_calls": 0},
            "checks": checks,
        }
        manifest["timeline"] = validated.to_dict()

        _write_json(run_dir / "preview_manifest.json", manifest)
        _write_json(run_dir / "checks.json", checks)
        _write_text(run_dir / "storyboard.md", _storyboard_md(manifest))
        _write_text(run_dir / "reading_view.md",
                    _reading_view_md(manifest, info["view"],
                                     plan_spec["records"], acceptance))
        return manifest
    except Exception as exc:                                         # noqa: BLE001
        try:
            payload = (exc.payload() if isinstance(exc, PreviewError) else
                       {"schema": "plan_preview.failure.v1", "code": "preview_failed",
                        "stage": "unknown", "message": "%s: %s"
                        % (type(exc).__name__, exc)})
            payload.update({"run_dir": str(run_dir),
                            "guard": dict(_GUARD)})
            _write_json(run_dir / "failure.json", payload)
        except Exception:                                            # noqa: BLE001
            pass
        raise
    finally:
        if _orig_service is not None:
            import step01_script_gen.outline as _outline
            _outline.create_service = _orig_service


def _which(name: str):
    import shutil
    return shutil.which(name)


def main():
    ap = argparse.ArgumentParser(description="剧情诊断预览（静音文字粗剪，0 次模型调用）")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--novel", required=True)
    ap.add_argument("--work-root", required=True)
    ap.add_argument("--font", required=True)
    ap.add_argument("--expect-plan-digest", required=True)
    ap.add_argument("--expect-novel-digest", required=True)
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    ap.add_argument("--visual-sec", type=float, default=DEFAULT_VISUAL_SEC)
    ap.add_argument("--reading-cps", type=float, default=6.0,
                    help="诊断预览的阅读速度（字/秒），只影响停留时间")
    ap.add_argument("--timeout-sec", type=float, default=600.0)
    args = ap.parse_args()
    try:
        manifest = build_plan_preview(
            Path(args.plan), Path(args.novel), Path(args.work_root),
            expected_plan_digest=args.expect_plan_digest,
            expected_novel_digest=args.expect_novel_digest,
            font_path=Path(args.font), width=args.width, height=args.height,
            visual_sec=args.visual_sec, reading_cps=args.reading_cps,
            timeout_sec=args.timeout_sec)
    except PreviewError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False, indent=1))
        raise SystemExit(2)
    print("preview:", manifest["output"]["path"])
    print("segments:", manifest["counts"]["segments"],
          "| beats:", manifest["counts"]["beats"],
          "| total:", manifest["total_sec"], "s")
    print("run:", manifest["run_dir"])


if __name__ == "__main__":
    main()
