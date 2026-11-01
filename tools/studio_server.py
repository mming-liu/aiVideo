# -*- coding: utf-8 -*-
"""studio_server - 本地 HTTP 审校工作台（零模型调用）.

用途：在浏览器里逐阶段查看 step01/02a/02b/03/04 的正式产物，并对"画面字段"
做**离线**修改与门禁重签。所有动作都在本机完成，不发起任何模型/TTS/视频调用。

设计约束（与 AGENTS.md / CLAUDE.md 对齐）：

1. **不碰 Step01 规则闭包**：本文件只 import 既有入口，不修改
   step01_script_gen 的规则源码，因此不改变 RULE_SOURCE_FINGERPRINT。
   重签走 tools.production_context.build_shot_patch_gate（该入口自身保证
   model_sends=0）。
2. **不新增第二套判据**：帧缓存命中与否复用 step02a_frame_gen.main 的判据
   语义（同参数、同哈希入口）；服务器侧只多返回 current/saved 两个哈希用于展示，
   并由 tests/tools/test_studio_server.py 交叉断言两者一致。
3. **状态不污染章节目录顶层**：审校锁定状态写
   <chapter>/_production/ui_review_state.json，任何写操作前先备份到
   <chapter>/_production/ui_backups/<timestamp>/。
4. **只监听回环地址**，且 /media 只允许读本 chapter 目录内的文件。

用法::

    python tools/studio_server.py --receipt output/<小说>/part_01_<部>/chapter_001/step01_adoption.json --port 8765
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:            # 供 python tools/studio_server.py 直接运行
    sys.path.insert(0, str(ROOT))

UI_DIR = Path(__file__).resolve().parent / "studio_ui"
STATE_SCHEMA = "studio.review_state/1"
OVERVIEW_SCHEMA = "studio.overview/1"
MAX_BODY_BYTES = 1 << 20                  # 1 MiB：只收 JSON 小补丁
MEDIA_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg",
    ".mp4", ".webm", ".mp3", ".wav", ".m4a", ".srt", ".txt", ".json",
})
FRAME_KINDS = ("ff", "lf")
STEP01_EDITABLE = ("prompt", "ff_desc", "lf_desc", "motion_desc",
                   "camera_movement", "frame_layouts", "keyframes")
KEEP_STAGES = ("step01", "step02a", "step02b", "step03", "step04")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class StudioError(RuntimeError):
    """可预期的业务失败：带稳定 code 与 HTTP 状态码，供前端直接展示."""

    def __init__(self, code: str, message: str, status: int = 400, **fields):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)
        self.fields = dict(fields)

    def payload(self) -> dict:
        return {"ok": False, "code": self.code, "message": self.message, **self.fields}


class Studio:
    """一个 chapter 的审校会话：读产物、写补丁、记锁定状态."""

    def __init__(self, receipt_path, *, novel_file: str | None = None):
        from tools import production_context as pc

        self.pc = pc
        self.novel_file = novel_file
        self.receipt_path = Path(receipt_path).resolve()
        self.receipt = pc.load_receipt(self.receipt_path)
        # 必须在 import 任何阶段模块之前生效（shared.config 是 import 期求值）。
        pc.apply_context(self.receipt)
        self.chapter = Path(self.receipt["formal_chapter"]).resolve()
        self.work = self.chapter / "vimax_working"
        self.production = self.chapter / "_production"
        self.state_path = self.production / "ui_review_state.json"
        self.lock = threading.RLock()

    # ---------------------------------------------------------------- 基础读
    def script(self) -> dict:
        path = self.chapter / "script_result.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise StudioError("script_unreadable", "脚本不可读：%s" % exc, 500) from exc
        except json.JSONDecodeError as exc:
            raise StudioError("script_not_json", "脚本不是合法 JSON：%s" % exc, 500) from exc

    def units(self) -> list[dict]:
        script = self.script()
        out = []
        hook = script.get("hook")
        if isinstance(hook, dict):
            out.append(hook)
        out.extend(s for s in (script.get("shots") or []) if isinstance(s, dict))
        return out

    def unit(self, index: int) -> dict:
        for unit in self.units():
            if int(unit.get("index", -1)) == int(index):
                return unit
        raise StudioError("shot_missing", "脚本里没有 shot %r" % index, 404)

    def characters(self) -> list:
        return list(self.script().get("characters") or [])

    def shot_dir(self, index: int) -> Path:
        return self.work / "shots" / str(int(index))

    # ------------------------------------------------------------ 锁定状态
    def state(self) -> dict:
        default = {"schema": STATE_SCHEMA, "updated_at": None,
                   "keep": {stage: [] for stage in KEEP_STAGES}, "history": []}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default
        if not isinstance(raw, dict) or raw.get("schema") != STATE_SCHEMA:
            return default
        keep = raw.get("keep") if isinstance(raw.get("keep"), dict) else {}
        for stage in KEEP_STAGES:
            values = keep.get(stage)
            default["keep"][stage] = [str(v) for v in values] if isinstance(values, list) else []
        default["updated_at"] = raw.get("updated_at")
        default["history"] = raw.get("history") if isinstance(raw.get("history"), list) else []
        return default

    def save_state(self, state: dict, *, action: str, detail: dict | None = None) -> None:
        from shared.json_io import atomic_dump_json

        state["schema"] = STATE_SCHEMA
        state["updated_at"] = _now()
        history = list(state.get("history") or [])
        history.append({"at": state["updated_at"], "action": action,
                        "detail": dict(detail or {}), "model_sends": 0})
        state["history"] = history[-200:]
        self.production.mkdir(parents=True, exist_ok=True)
        atomic_dump_json(self.state_path, state, ensure_ascii=False, indent=2)

    def is_kept(self, stage: str, key) -> bool:
        return str(key) in self.state()["keep"].get(stage, [])

    # ---------------------------------------------------------------- 备份
    def backup(self, label: str, paths: list[Path]) -> Path:
        """把即将改动的文件复制到 _production/ui_backups/<ts>/ 并写 manifest."""
        from shared.json_io import atomic_dump_json

        base = self.production / "ui_backups" / _stamp()
        n = 1
        while base.exists():
            n += 1
            base = self.production / "ui_backups" / ("%s_%d" % (_stamp(), n))
        base.mkdir(parents=True, exist_ok=True)
        copied = []
        for src in paths:
            src = Path(src)
            if not src.is_file():
                continue
            dst = base / src.name
            k = 1
            while dst.exists():
                k += 1
                dst = base / ("%s_%d%s" % (src.stem, k, src.suffix))
            shutil.copy2(src, dst)
            copied.append({"source": str(src), "backup": str(dst)})
        atomic_dump_json(base / "manifest.json", {
            "schema": "studio.backup/1", "at": _now(), "action": label,
            "chapter": str(self.chapter), "files": copied, "model_sends": 0,
        }, ensure_ascii=False, indent=2)
        return base

    # ------------------------------------------------------------ 帧缓存判据
    def frame_state(self, unit: dict, kind: str) -> dict:
        """与 step02a --plan-only 同判据，额外返回 current/saved 哈希用于展示.

        判据语义必须与 step02a_frame_gen.main._plan_frame_state 一致：
        同一 _get_frame_content_hash 入口、同一参考图收集、同一 lock_kind。
        """
        from shared.paths import FIRST_FRAME_FILENAME, LAST_FRAME_FILENAME
        from shared.ref_compose import active_image_ref_limit, fit_flat
        from shared.vimax_bridge import (
            _collect_frame_refs_for_shot, _get_frame_content_hash,
            _is_action_shot, is_empty_scene,
        )

        idx = int(unit.get("index", 0))
        is_ff = kind == "ff"
        shot_dir = self.shot_dir(idx)
        image_path = shot_dir / (FIRST_FRAME_FILENAME if is_ff else LAST_FRAME_FILENAME)
        hash_path = shot_dir / ("frame_hash.txt" if is_ff else "last_frame_hash.txt")
        out = {"kind": kind, "shot": idx, "image": str(image_path),
               "state": "missing", "current_hash": None, "saved_hash": None,
               "image_exists": image_path.is_file()}
        if not image_path.is_file():
            return out
        try:
            characters = self.characters()
            refs = _collect_frame_refs_for_shot(
                unit, characters, limit=5, for_frame="首帧" if is_ff else "尾帧")
            if not is_ff:
                ff = shot_dir / FIRST_FRAME_FILENAME
                if not ff.is_file():
                    return out
                refs = [str(ff)] + [p for p in refs if p != str(ff)]
            limit = active_image_ref_limit()
            valid = [p for p in refs if p and Path(p).exists()]
            if len(valid) > limit:
                # 与 plan-only 一致：超上限时不落盘参考表，退回"文件存在即命中"。
                out.update(state="cached", note="refs_over_limit")
                return out
            refs = fit_flat(refs, limit, shot_dir, "shot%d_%s" % (idx, kind))
            lock = "no_layout" if (not is_ff or _is_action_shot(unit, characters)) else "layout"
            current = _get_frame_content_hash(
                unit, is_empty_scene(unit, characters), refs,
                lock_kind=lock, characters=characters,
                frame_kind="first_frame" if is_ff else "last_frame")
        except Exception as exc:                       # 判据不可计算时不宣称命中
            out.update(state="stale", error="%s: %s" % (type(exc).__name__, exc))
            return out
        saved = None
        try:
            saved = hash_path.read_text(encoding="utf-8").strip()
        except OSError:
            saved = None
        out["current_hash"] = current
        out["saved_hash"] = saved
        out["state"] = "cached" if (saved and current == saved) else "stale"
        return out

    # ------------------------------------------------------------- 总览数据
    def overview(self) -> dict:
        from shared.quality_gate import per_shot_revisions

        script = self.script()
        revisions = per_shot_revisions(script)
        state = self.state()
        kept = set(state["keep"].get("step01") or [])
        gate = self._gate_summary()
        shots = []
        counts = {"cached": 0, "stale": 0, "missing": 0}
        for unit in self.units():
            idx = int(unit.get("index", 0))
            frames = {}
            for kind in FRAME_KINDS:
                info = self.frame_state(unit, kind)
                counts[info["state"]] = counts.get(info["state"], 0) + 1
                frames[kind] = {k: v for k, v in info.items()}
                frames[kind]["url"] = ("/media?path=" + str(Path(info["image"]))) if info["image_exists"] else None
            shots.append({
                "index": idx,
                "duration": unit.get("duration_sec") or unit.get("duration"),
                "revision": revisions.get(idx),
                "keep": str(idx) in kept,
                "fields": {k: unit.get(k) for k in STEP01_EDITABLE},
                "subtitle": unit.get("subtitle") or "",
                "narration": unit.get("narration_segment") or "",
                "frames": frames,
                "prompts": self._assembled_prompts(unit),
            })
        return {
            "schema": OVERVIEW_SCHEMA,
            "chapter": {
                "novel": (self.receipt.get("scope") or {}).get("novel"),
                "book_index": (self.receipt.get("scope") or {}).get("book_index"),
                "book_name": (self.receipt.get("scope") or {}).get("book_name"),
                "chapter": (self.receipt.get("scope") or {}).get("chapter"),
                "path": str(self.chapter),
                "run_id": self.receipt.get("source_run_id"),
                "script_revision": self.receipt.get("script_revision"),
                "adopted_at": self.receipt.get("adopted_at"),
            },
            "gate": gate,
            "safety": {"model_sends": 0,
                       "writes": [str(self.state_path), str(self.production / "ui_backups")]},
            "counts": {"shots": len(shots),
                       "kept": len(kept & {str(s["index"]) for s in shots}),
                       "frames_cached": counts.get("cached", 0),
                       "frames_stale": counts.get("stale", 0),
                       "frames_missing": counts.get("missing", 0)},
            "shots": shots,
            "media": self._media_index(),
            "steps": self._steps(gate),
            "keep": state["keep"],
        }

    def _gate_summary(self) -> dict:
        commit_path = self.chapter / "step01_commit.json"
        quality_path = self.chapter / "step01_quality.json"
        out = {"status": "unknown", "blocking": None, "rule_source_fingerprint": None,
               "rule_version": None, "script_revision": self.receipt.get("script_revision"),
               "verified": False, "error": None}
        try:
            commit = json.loads(commit_path.read_text(encoding="utf-8"))
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            out["error"] = str(exc)
            return out
        out.update(status=commit.get("status") or quality.get("status"),
                   blocking=commit.get("blocking_count"),
                   rule_source_fingerprint=commit.get("rule_source_fingerprint"),
                   rule_version=commit.get("rule_version"),
                   script_revision=commit.get("script_revision"),
                   audit_status=commit.get("audit_status"))
        try:
            self.pc.verify(self.receipt)
            out["verified"] = True
        except Exception as exc:                       # 只做展示，不在这里改产物
            out["error"] = getattr(exc, "message", str(exc))
        return out

    def _assembled_prompts(self, unit: dict) -> dict:
        try:
            from shared.vimax_bridge import compose_frame_prompt
        except Exception:
            return {}
        characters = self.characters()
        out = {}
        for kind, field in (("ff", "ff_desc"), ("lf", "lf_desc")):
            if not unit.get(field):
                continue
            try:
                out[kind] = compose_frame_prompt(
                    unit, characters, "first_frame" if kind == "ff" else "last_frame")
            except Exception as exc:
                out[kind] = "（组装失败：%s）" % exc
        return out

    def _media_index(self) -> dict:
        def rel(p: Path) -> dict:
            return {"name": p.name, "path": str(p),
                    "url": "/media?path=" + str(p), "size": p.stat().st_size}

        videos, audios, finals, subtitles = [], [], [], []
        for unit in self.units():
            idx = int(unit.get("index", 0))
            video = self.shot_dir(idx) / "video.mp4"
            if video.is_file():
                item = rel(video)
                item["shot"] = idx
                videos.append(item)
            for audio in sorted((self.chapter / "audio").glob("shot_%s*.mp3" % idx)):
                item = rel(audio)
                item["shot"] = idx
                audios.append(item)
        for pattern, bucket in (("final*.mp4", finals), ("*.srt", subtitles)):
            for path in sorted(self.chapter.glob(pattern)):
                if path.is_file():
                    bucket.append(rel(path))
        return {"videos": videos, "audios": audios, "finals": finals,
                "subtitles": subtitles}

    def _steps(self, gate: dict) -> list[dict]:
        media = self._media_index()
        return [
            {"id": "step01", "title": "剧本与分镜", "available": True,
             "detail": "门禁 %s" % (gate.get("status") or "unknown")},
            {"id": "step02a", "title": "立绘与首尾帧", "available": True,
             "detail": "帧缓存判据：内容哈希"},
            {"id": "step02b", "title": "分镜视频", "available": bool(media["videos"]),
             "detail": "%d 个分镜视频" % len(media["videos"])},
            {"id": "step03", "title": "TTS 语音", "available": bool(media["audios"]),
             "detail": "%d 条音轨" % len(media["audios"])},
            {"id": "step04", "title": "字幕与成片", "available": bool(media["finals"]),
             "detail": "%d 个成片" % len(media["finals"])},
        ]

    # ------------------------------------------------------------- 锁定动作
    def set_keep(self, stage: str, key: str, keep: bool) -> dict:
        if stage not in KEEP_STAGES:
            raise StudioError("keep_stage_invalid", "未知阶段：%r" % stage)
        with self.lock:
            state = self.state()
            items = [v for v in state["keep"].get(stage, []) if v != str(key)]
            if keep:
                items.append(str(key))
            state["keep"][stage] = sorted(set(items), key=lambda v: (len(v), v))
            self.save_state(state, action="keep", detail={"stage": stage, "key": str(key),
                                                          "keep": bool(keep)})
            return {"ok": True, "stage": stage, "key": str(key), "keep": bool(keep),
                    "state_file": str(self.state_path), "model_sends": 0}

    # ------------------------------------------------------- step01 单镜补丁
    def patch_shot(self, shot: int, changes: dict, *, apply: bool) -> dict:
        from shared.json_io import atomic_dump_json

        if not isinstance(changes, dict) or not changes:
            raise StudioError("patch_empty", "补丁内容为空")
        bad = sorted(set(changes) - set(STEP01_EDITABLE))
        if bad:
            raise StudioError("patch_field_not_allowed",
                              "这些字段不允许在网页上修改：%s" % "、".join(bad),
                              fields=bad)
        with self.lock:
            if self.is_kept("step01", shot):
                raise StudioError("shot_kept",
                                  "shot %s 已锁定（保持），先解锁再修改" % shot, 409)
            script = self.script()
            patch = {"shot_index": int(shot), "set": dict(changes)}
            new_script, meta = self.pc.apply_shot_patch(script, patch, int(shot))
            files, adoption, revision = self.pc.build_shot_patch_gate(
                self.receipt, new_script, patch_meta=meta, novel_file=self.novel_file)
            result = {"ok": True, "dry_run": not apply, "shot": int(shot),
                      "revision": revision, "before_revision": self.receipt.get("script_revision"),
                      "meta": meta, "model_sends": 0}
            if not apply:
                return result
            backup = self.backup("step01.patch.shot%s" % shot, [
                self.chapter / "script_result.json",
                self.chapter / "step01_quality.json",
                self.chapter / "step01_audit.json",
                self.chapter / "step01_commit.json",
                self.chapter / "step01_adoption.json",
            ])
            written = self.pc.apply_refreshed_gate(self.receipt, files)
            atomic_dump_json(self.chapter / "script_result.json", new_script,
                             ensure_ascii=False, indent=2)
            record_dir = self.production / "shot_patches"
            record_dir.mkdir(parents=True, exist_ok=True)
            atomic_dump_json(record_dir / ("shot_patch_%s.json" % _stamp()), {
                "schema": "production.shot_patch_record/1", "at": _now(), "meta": meta,
                "before_revision": self.receipt.get("script_revision"),
                "after_revision": revision, "patch": patch,
                "source": "studio_server", "model_sends": 0,
            }, ensure_ascii=False, indent=2)
            self.receipt.update({"script_revision": revision,
                                 "formal_sha256": adoption.get("formal_sha256") or {}})
            result.update(written=written, backup=str(backup))
            return result

    # ------------------------------------------------------ step02a 帧动作
    def frame_keep(self, shot: int, kind: str) -> dict:
        """保持此图：把当前内容哈希写回 *_hash.txt，0 成本消除 stale."""
        if kind not in FRAME_KINDS:
            raise StudioError("frame_kind_invalid", "kind 必须是 ff 或 lf")
        with self.lock:
            unit = self.unit(shot)
            info = self.frame_state(unit, kind)
            if not info["image_exists"]:
                raise StudioError("frame_missing",
                                  "shot %s 的 %s 帧还不存在" % (shot, kind), 404)
            if not info["current_hash"]:
                raise StudioError("frame_hash_unavailable",
                                  "当前哈希不可计算：%s" % (info.get("error") or "未知原因"), 409)
            hash_path = Path(info["image"]).with_name(
                "frame_hash.txt" if kind == "ff" else "last_frame_hash.txt")
            backup = self.backup("step02a.keep.shot%s.%s" % (shot, kind), [hash_path])
            hash_path.write_text(info["current_hash"] + chr(10), encoding="utf-8")
            self.set_keep("step02a", "%s:%s" % (shot, kind), True)
            return {"ok": True, "shot": int(shot), "kind": kind,
                    "hash": info["current_hash"], "hash_file": str(hash_path),
                    "backup": str(backup), "state": "cached", "model_sends": 0}

    def frame_regen(self, shot: int, kind: str) -> dict:
        """标记重抽：备份旧图与哈希，再移除哈希（plan-only 随即判 generate）."""
        if kind not in FRAME_KINDS:
            raise StudioError("frame_kind_invalid", "kind 必须是 ff 或 lf")
        with self.lock:
            unit = self.unit(shot)
            info = self.frame_state(unit, kind)
            image = Path(info["image"])
            hash_path = image.with_name("frame_hash.txt" if kind == "ff" else "last_frame_hash.txt")
            if not image.is_file() and not hash_path.is_file():
                raise StudioError("frame_missing",
                                  "shot %s 的 %s 帧没有可重抽的产物" % (shot, kind), 404)
            backup = self.backup("step02a.regen.shot%s.%s" % (shot, kind), [image, hash_path])
            removed = []
            for path in (hash_path, image):
                if path.is_file():
                    path.unlink()
                    removed.append(path.name)
            self.set_keep("step02a", "%s:%s" % (shot, kind), False)
            return {"ok": True, "shot": int(shot), "kind": kind, "removed": removed,
                    "backup": str(backup), "state": "missing", "model_sends": 0}

    # -------------------------------------------------------- step02a 预检
    def plan_step02a(self, shots: list[int] | None) -> dict:
        """复用生成链的预检报告（零网络账单）；只在内存里算，不落章节目录."""
        from step02a_frame_gen.main import _plan_only_report

        report = _plan_only_report(self.script(), self.work, shots, None)
        return {"ok": True, "stage": "step02a", "report": report, "model_sends": 0}

    # --------------------------------------------------------- step04 合成
    def compose_step04(self, shots: str | None) -> dict:
        from shared.shot_selector import parse_shot_selector

        spec = (shots or "").strip()
        if spec:
            try:
                parse_shot_selector(spec)
            except Exception as exc:
                raise StudioError("shots_invalid", "镜头范围不合法：%s" % exc) from exc
        with self.lock:
            backup = self.backup("step04.compose", [])
            cmd = [sys.executable, str(ROOT / "step04_merge" / "main.py")]
            if spec:
                cmd += ["--shots", spec]
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                                      text=True, encoding="utf-8", errors="replace",
                                      timeout=1800)
            except subprocess.TimeoutExpired:
                raise StudioError("compose_timeout", "合成超时（30 分钟）", 504)
            log = ((proc.stdout or "") + (proc.stderr or ""))[-8000:]
            outputs = []
            for pattern in ("final_shots_*.mp4", "final*.mp4"):
                for path in sorted(self.chapter.glob(pattern)):
                    if path.is_file():
                        outputs.append({"name": path.name, "path": str(path),
                                        "url": "/media?path=" + str(path),
                                        "size": path.stat().st_size})
            return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                    "shots": spec or None, "log": log, "outputs": outputs,
                    "backup": str(backup), "model_sends": 0}


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------
class StudioHandler(BaseHTTPRequestHandler):
    server_version = "aiVideoStudio/1.0"
    studio: Studio = None            # 由 main() 注入

    def log_message(self, fmt, *args):        # 精简日志，避免刷屏
        sys.stderr.write("[studio] %s - %s" % (self.address_string(), fmt % args) + chr(10))

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, exc: Exception):
        if isinstance(exc, StudioError):
            self._json(exc.status, exc.payload())
        else:
            self._json(500, {"ok": False, "code": "internal_error",
                             "message": "%s: %s" % (type(exc).__name__, exc)})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise StudioError("body_too_large", "请求体超过 1 MiB", 413)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StudioError("body_not_json", "请求体不是合法 JSON：%s" % exc)
        if not isinstance(payload, dict):
            raise StudioError("body_not_object", "请求体必须是 JSON 对象")
        return payload

    # ---- 路由 ----
    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path in ("/", "/index.html"):
                return self._static("index.html")
            if parsed.path.startswith("/assets/"):
                return self._static(unquote(parsed.path[len("/assets/"):]))
            if parsed.path == "/api/overview":
                return self._json(200, {"ok": True, **self.studio.overview()})
            if parsed.path == "/media":
                return self._media(parse_qs(parsed.query))
            if parsed.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            self._json(404, {"ok": False, "code": "not_found", "message": parsed.path})
        except Exception as exc:                       # 统一转 JSON，不吐 HTML 栈
            self._error(exc)

    do_HEAD = do_GET

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self._body()
            if parsed.path == "/api/keep":
                return self._json(200, self.studio.set_keep(
                    str(payload.get("stage") or ""), str(payload.get("key") or ""),
                    bool(payload.get("keep"))))
            if parsed.path == "/api/shot/patch":
                changes = payload.get("set") if isinstance(payload.get("set"), dict) else {}
                return self._json(200, self.studio.patch_shot(
                    int(payload.get("shot") or 0), changes,
                    apply=bool(payload.get("apply"))))
            if parsed.path == "/api/frame/keep":
                return self._json(200, self.studio.frame_keep(
                    int(payload.get("shot") or 0), str(payload.get("kind") or "")))
            if parsed.path == "/api/frame/regen":
                return self._json(200, self.studio.frame_regen(
                    int(payload.get("shot") or 0), str(payload.get("kind") or "")))
            if parsed.path == "/api/plan":
                shots = payload.get("shots")
                shots = [int(s) for s in shots] if isinstance(shots, list) and shots else None
                return self._json(200, self.studio.plan_step02a(shots))
            if parsed.path == "/api/compose":
                return self._json(200, self.studio.compose_step04(payload.get("shots")))
            self._json(404, {"ok": False, "code": "not_found", "message": parsed.path})
        except Exception as exc:
            self._error(exc)

    # ---- 静态资源 ----
    def _static(self, relative: str):
        target = (UI_DIR / relative).resolve()
        try:
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            raise StudioError("static_forbidden", "越界的静态路径", 403)
        if not target.is_file():
            raise StudioError("static_missing", "缺少文件：%s" % relative, 404)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # ---- 媒体（含 Range） ----
    def _media(self, query: dict):
        raw = (query.get("path") or [""])[0]
        path = Path(unquote(raw))
        if not raw or not path.is_absolute():
            raise StudioError("media_path_invalid", "path 必须是绝对路径")
        resolved = path.resolve()
        try:
            resolved.relative_to(self.studio.chapter)
        except ValueError:
            raise StudioError("media_forbidden", "只允许读取本 chapter 目录内的文件", 403)
        if resolved.suffix.lower() not in MEDIA_SUFFIXES:
            raise StudioError("media_type_forbidden",
                              "不支持的类型：%s" % resolved.suffix, 403)
        if not resolved.is_file():
            raise StudioError("media_missing", "文件不存在：%s" % resolved, 404)
        size = resolved.stat().st_size
        ctype = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        range_header = (self.headers.get("Range") or "").strip()
        if not range_header.startswith("bytes=") or "-" not in range_header:
            self._send(200, resolved.read_bytes(), ctype, {"Accept-Ranges": "bytes"})
            return
        head, _, tail = range_header[len("bytes="):].partition("-")
        if not head.isdigit() and not tail.isdigit():
            raise StudioError("range_invalid", "Range 头不合法", 416)
        if head == "":                            # bytes=-N 取尾部
            length = min(int(tail), size)
            start, end = max(0, size - length), size - 1
        else:
            start = int(head)
            end = int(tail) if tail.isdigit() else size - 1
        if start >= size or start > end:
            self._send(416, b"", ctype, {"Content-Range": "bytes */%d" % size})
            return
        end = min(end, size - 1)
        with open(resolved, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(end - start + 1)
        self._send(206, chunk, ctype, {
            "Accept-Ranges": "bytes",
            "Content-Range": "bytes %d-%d/%d" % (start, end, size),
        })


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="本地审校工作台（零模型调用；只读写本 chapter 目录）")
    parser.add_argument("--receipt", required=True, help="step01_adoption.json 路径")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅回环）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--novel-file", default=None, help="重签门禁时读取的原文文件")
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("拒绝启动：工作台会改写正式产物，只允许监听回环地址。", file=sys.stderr)
        return 2

    studio = Studio(args.receipt, novel_file=args.novel_file)
    StudioHandler.studio = studio
    httpd = ThreadingHTTPServer((args.host, args.port), StudioHandler)
    url = "http://%s:%d" % (args.host, args.port)
    print("=" * 66)
    print("aiVideo Studio  %s" % url)
    print("chapter : %s" % studio.chapter)
    print("receipt : %s" % studio.receipt_path)
    print("model_sends = 0（所有动作都在本机完成）")
    print("按 Ctrl+C 停止")
    print("=" * 66)
    if not args.no_open:
        try:
            import webbrowser
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
