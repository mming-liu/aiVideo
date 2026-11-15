# -*- coding: utf-8 -*-
"""shared.voice_state - 章节声音状态的**显式落盘与准入门禁**（Step E）.

``shared.voice_preparation`` / ``shared.execution_semantics`` 已经把「准备态
``prepared`` / 确认态 ``confirmed``」定义成两态；本模块负责把这套两态接到
主流程的两个**边界**上：

1. **写**（Step 03 真实合成完成后）：把本章实际产出、已核过「存在 / 非空 /
   可读时长 / 绑定 revision」的音轨摘要落成 ``voice_state.json``；
   ``--plan-only`` 或未完成真实合成时落 ``prepared``（明确「还没合成」，
   不是「合成失败」）。
2. **读**（Step 04 正式合成前）：有声单元必须 ``confirmed``，且
   ``script_revision`` 与本次正式稿一致；``prepared`` / 缺失 / 版本漂移
   一律 fail-closed 拒收。``--dry-run`` 允许在 ``prepared`` 下推演时间线
   （时间线推演不消费音频字节），但仍把差异写进报告。

本模块不调用任何 provider，只探测调用方给出的本地音频文件（ffprobe）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

SCHEMA = "voice_state.v1"

#: 章节级文件名（落在章目录；候选运行落在 attempt 内）。
FILENAME = "voice_state.json"

STATE_PREPARED = "prepared"
STATE_CONFIRMED = "confirmed"

#: 空文件/写了一半的文件不算「有音轨」。与 step03 成品验收的 10KB 口径分开：
#: 这里只判「确实是可读音频」，不重复成品验收的阈值判断。
MIN_BYTES = 1024


class VoiceStateError(RuntimeError):
    """声音状态准入失败（fail-closed）。

    ``code`` 供上层报告路由（与 ``TimelineError`` / ``RenderError`` 同一形态）；
    ``exit_code`` 让 step04 的 ``__main__`` 以拒收码退出，而不是通用 1。
    """

    exit_code = 2

    def __init__(self, code: str, message: str, **fields: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sha256_file(path: Path) -> str | None:
    try:
        import hashlib
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _probe_duration(path: Path) -> tuple[float | None, str]:
    """本地 ffprobe 读实际时长；不可用/不可读时返回原因，不猜。"""
    try:
        from shared.media_inspect import ffprobe_executable, probe_media
        if not ffprobe_executable():
            return None, "ffprobe_unavailable"
        info = probe_media(str(path))
        if not isinstance(info, dict):
            return None, "probe_unreadable"
        value = info.get("duration_sec")
        if value is None:
            value = (info.get("format") or {}).get("duration")
        try:
            duration = float(value) if value is not None else None
        except (TypeError, ValueError):
            duration = None
        return (duration, "") if duration is not None else (None, "duration_unreadable")
    except Exception as exc:                                  # noqa: BLE001
        return None, "probe_failed: %s" % str(exc)[:200]


def build_voice_state(script: dict | None, *, tracks: list[dict], scope: dict | None = None,
                      script_revision: str | None = None, source: str = "",
                      require_audio: bool = True, prepared_reason: str = "") -> dict:
    """把「本次真实合成产出的音轨」变成章节声音状态记录.

    ``tracks`` 每条至少含 ``unit_id`` / ``kind`` / ``path``；``kind`` 用
    ``voice_preparation`` 的词汇（``tts_dialogue`` / ``narration`` /
    ``native_dialogue``）。``required=True``（默认）表示这条音轨是本章成片
    必需的，缺它就不能算 ``confirmed``。

    ``state=confirmed`` 需**同时**满足：

    - ``script_revision`` 非空（无法绑定稿件身份的记录不升级为确认态）；
    - 每条 required 音轨的文件存在、字节数 ≥ ``MIN_BYTES``、ffprobe 时长 > 0；
    - 没有任何 blocking（每条失败音轨一条，带 ``code`` / ``unit_id``）。

    其它情况一律 ``prepared``，并把**具体原因**逐条留在 ``blocking`` 里；
    不把「没测出来」写成「已确认」。
    """
    revision = _text(script_revision)
    if not revision and isinstance(script, dict):
        revision = _text(script.get("script_revision"))
    units: list[dict] = []
    blocking: list[dict] = []
    digests: dict[str, str] = {}
    if not revision:
        blocking.append({
            "code": "script_revision_missing",
            "unit_id": "",
            "message": "声音状态无法绑定稿件 revision（空值）——不升级为 confirmed",
        })
    for raw in tracks or []:
        if not isinstance(raw, dict):
            continue
        unit_id = _text(raw.get("unit_id")) or "unit-?"
        kind = _text(raw.get("kind")) or "tts_dialogue"
        required = raw.get("required", True) is not False
        path_raw = _text(raw.get("path"))
        entry = {
            "unit_id": unit_id,
            "kind": kind,
            "shot_index": raw.get("shot_index"),
            "path": path_raw,
            "required": required,
            "sha256": None,
            "bytes": 0,
            "duration_sec": None,
            "state": STATE_PREPARED,
            "reason": "",
        }
        path = Path(path_raw) if path_raw else None
        if path is None or not path_raw:
            entry["reason"] = "audio_path_missing"
        elif not path.is_file():
            entry["reason"] = "audio_file_missing"
        else:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            entry["bytes"] = size
            entry["sha256"] = _sha256_file(path)
            if size < MIN_BYTES:
                entry["reason"] = "audio_file_too_small"
            elif not entry["sha256"]:
                entry["reason"] = "audio_digest_unreadable"
            else:
                duration, error = _probe_duration(path)
                if duration is None:
                    entry["reason"] = error or "duration_unreadable"
                elif duration <= 0:
                    entry["reason"] = "duration_not_positive"
                else:
                    entry["duration_sec"] = round(duration, 6)
                    entry["state"] = STATE_CONFIRMED
                    digests[unit_id] = entry["sha256"]
        if entry["state"] != STATE_CONFIRMED and entry["reason"] \
                and not str(entry["reason"]).startswith("not_required"):
            blocking.append({
                "code": entry["reason"] if entry["reason"] in (
                    "audio_path_missing", "audio_file_missing", "audio_file_too_small",
                    "audio_digest_unreadable", "ffprobe_unavailable",
                    "duration_unreadable", "duration_not_positive") else "audio_unconfirmed",
                "unit_id": unit_id,
                "message": "音轨未通过确认：%s" % entry["reason"],
            })
        units.append(entry)
    if require_audio and not units:
        blocking.append({
            "code": "audio_units_missing",
            "unit_id": "",
            "message": "本章需要独立音轨，但没有任何音轨被登记",
        })
    if not units and not blocking:
        # 没有登记任何音轨就无从确认——不允许 0/0 的空集合冒充 confirmed。
        blocking.append({
            "code": "audio_units_missing",
            "unit_id": "",
            "message": "没有登记任何音轨（plan-only 或未合成不得升级为确认态）",
        })
    if prepared_reason:
        # 调用方显式声明「本次没有真实合成」：即使文件恰好都在，也不升级为确认态。
        blocking.append({
            "code": prepared_reason,
            "unit_id": "",
            "message": "调用方声明本次未完成真实合成（%s）" % prepared_reason,
        })
    confirmed = [u for u in units if u["state"] == STATE_CONFIRMED]
    state = STATE_CONFIRMED if (not blocking and len(confirmed) == len(units)) \
        else STATE_PREPARED
    return {
        "schema": SCHEMA,
        "state": state,
        "script_revision": revision,
        "scope": dict(scope or {}),
        "source": source,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "units": units,
        "digests": digests,
        "blocking": blocking,
        "counts": {"required": len(units), "confirmed": len(confirmed),
                   "prepared": len(units) - len(confirmed)},
    }


def read_voice_state(path: str | Path) -> dict | None:
    """读章节声音状态；缺失/不可解析返回 None（准入方据此 fail-closed）。"""
    import json
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def assert_voice_state(path: str | Path, *, script_revision: str,
                       voice_required: bool, dry_run: bool = False) -> dict:
    """正式合成前的准入：有声单元必须 ``confirmed`` 且 revision 与正式稿一致.

    Returns:
        ``{"status": "confirmed"|"prepared"|"not_required"|"dry_run_prepared",
          "gate": ..., "path": ..., "reasons": [...], "blocking": [...]}``

    Raises:
        VoiceStateError：``voice_required`` 为真且（状态缺失 / 未确认 /
                revision 漂移）。``dry_run`` 为真时**不抛**，只把差异记在
                ``reasons`` 里——时间线推演不消费音频字节，允许在准备态下跑。
    """
    path = Path(path)
    report = {
        "status": "not_required",
        "gate": "skipped_no_voice_required",
        "path": str(path),
        "reasons": [],
        "blocking": [],
        "script_revision": "",
        "units_confirmed": 0,
        "units_required": 0,
    }
    if not voice_required:
        report["reasons"] = ["本章没有需要独立音轨的声音单元"]
        return report

    state = read_voice_state(path)
    if state is None:
        message = ("章节声音状态缺失：%s（step03 未完成真实合成或未写状态）；"
                   "正式合成拒收" % path)
        if dry_run:
            report.update({"status": "dry_run_prepared", "gate": "dry_run_allowed",
                           "reasons": [message.replace("；正式合成拒收", "")]})
            return report
        raise VoiceStateError("voice_state_missing", message, path=str(path))

    status = _text(state.get("state"))
    revision = _text(state.get("script_revision"))
    blocking = [b for b in (state.get("blocking") or []) if isinstance(b, dict)]
    units = [u for u in (state.get("units") or []) if isinstance(u, dict)]
    report.update({
        "script_revision": revision,
        "blocking": blocking,
        "units_confirmed": sum(1 for u in units if u.get("state") == STATE_CONFIRMED),
        "units_required": len(units),
        "checked_at": state.get("checked_at"),
    })
    if status != STATE_CONFIRMED:
        report["reasons"].append(
            "章节声音状态=%r（不是 %r）：%d/%d 条音轨未确认"
            % (status or "", STATE_CONFIRMED,
               len(units) - report["units_confirmed"], len(units)))
        if dry_run:
            report.update({"status": "dry_run_prepared", "gate": "dry_run_allowed"})
            return report
        raise VoiceStateError(
            "voice_state_not_confirmed",
            "章节声音状态仍为 %r，正式合成要求 %r；未确认音轨 %d 条"
            % (status or "", STATE_CONFIRMED,
               len(units) - report["units_confirmed"]),
            path=str(path), blocking=blocking)
    if revision != _text(script_revision):
        report["reasons"].append(
            "声音状态绑定 revision=%r 与正式稿 %r 不一致"
            % (revision, _text(script_revision)))
        if dry_run:
            report.update({"status": "dry_run_prepared", "gate": "dry_run_allowed"})
            return report
        raise VoiceStateError(
            "voice_state_revision_mismatch",
            "声音状态绑定 revision=%s 与本次正式稿 %s 不一致：旧稿音频不得配到新稿上"
            % (revision[:12] or "空", _text(script_revision)[:12]),
            path=str(path), state_revision=revision,
            script_revision=_text(script_revision))
    report.update({"status": "confirmed", "gate": "passed"})
    return report


__all__ = [
    "SCHEMA", "FILENAME", "STATE_CONFIRMED", "STATE_PREPARED", "MIN_BYTES",
    "VoiceStateError", "build_voice_state", "read_voice_state", "assert_voice_state",
]
