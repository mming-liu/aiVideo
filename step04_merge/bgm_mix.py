# -*- coding: utf-8 -*-
"""step04_merge.bgm_mix — BGM 氛围层与确定性 Ducking（2026-11-28 O8）.

默认关闭（`MERGE_BGM_ENABLED=0`）：关闭时本模块**不参与编译**，`compile_graph`
产出的滤镜图与历史逐字节一致。

开启后的三段式处理：

1. **输入**：`-stream_loop -1` 循环 + `atrim` 铺满成片时长，统一 48 kHz 立体声；
2. **包络**：由 `MergeTimeline` 上已知的对白/旁白人声区间合并、按 attack/release
   外扩，生成**确定性** `volume` 包络表达式（无抽吸感的线性斜坡，毫秒级可测）；
3. **混音**：`amix=inputs=2:duration=first:normalize=0`（不把 BGM 再除一次，
   人声电平不变）。

开关打开但文件缺失/增益非法 ⇒ :class:`BgmError`（fail-fast，不静默跳过）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

SAMPLE_RATE = 48000
SUPPORTED_SUFFIXES = (".wav", ".mp3", ".flac")
#: 包络表达式的软上限（Windows 命令行 32k 限制的保守预算）；超限时按最小间隔合并
MAX_EXPRESSION_CHARS = 12000
_EPS = 1e-6


class BgmError(RuntimeError):
    """BGM 配置/资源非法（fail-fast，不静默降级为无人声氛围）。"""

    def __init__(self, code: str, message: str, **fields):
        self.code = str(code)
        self.fields = dict(fields)
        super().__init__(message)

    def payload(self) -> dict:
        return {"code": self.code, "message": str(self), **self.fields}


def db_to_linear(db: float) -> float:
    return float(10.0 ** (float(db) / 20.0))


@dataclass(frozen=True)
class BgmConfig:
    """BGM 生效参数（值来自 `shared.config.bgm_config()`，本类只做校验/换算）."""

    path: str = ""
    base_gain_db: float = -16.0
    duck_gain_db: float = -26.0
    fade_in_sec: float = 1.0
    fade_out_sec: float = 2.0
    attack_sec: float = 0.15
    release_sec: float = 0.35
    enabled: bool = True

    @classmethod
    def from_mapping(cls, cfg: Mapping | None) -> "BgmConfig | None":
        """`None` / `enabled=False` ⇒ `None`（调用方据此完全不碰滤镜图）."""
        if not cfg:
            return None
        data = dict(cfg)
        if not bool(data.get("enabled")):
            return None
        return cls(path=str(data.get("path") or ""),
                   base_gain_db=float(data.get("base_gain_db", -16.0)),
                   duck_gain_db=float(data.get("duck_gain_db", -26.0)),
                   fade_in_sec=float(data.get("fade_in_sec", 1.0)),
                   fade_out_sec=float(data.get("fade_out_sec", 2.0)),
                   attack_sec=float(data.get("attack_sec", 0.15)),
                   release_sec=float(data.get("release_sec", 0.35)),
                   enabled=True)

    @property
    def base_linear(self) -> float:
        return db_to_linear(self.base_gain_db)

    @property
    def duck_linear(self) -> float:
        return db_to_linear(self.duck_gain_db)

    def validate(self, *, require_file: bool = True) -> "BgmConfig":
        """校验并返回自身；非法即抛 :class:`BgmError`."""
        if not self.enabled:
            return self
        numbers = {
            "base_gain_db": self.base_gain_db, "duck_gain_db": self.duck_gain_db,
            "fade_in_sec": self.fade_in_sec, "fade_out_sec": self.fade_out_sec,
            "attack_sec": self.attack_sec, "release_sec": self.release_sec,
        }
        bad = {k: v for k, v in numbers.items() if not math.isfinite(float(v))}
        if bad:
            raise BgmError("bgm_config_invalid", "BGM 配置含 NaN/Inf: %r" % bad,
                           fields=bad)
        for key in ("fade_in_sec", "fade_out_sec", "attack_sec", "release_sec"):
            if float(numbers[key]) < 0:
                raise BgmError("bgm_config_invalid", "BGM %s 不能为负: %r"
                               % (key, numbers[key]), field=key)
        if self.duck_gain_db > self.base_gain_db:
            raise BgmError(
                "bgm_gain_order",
                "MERGE_BGM_DUCK_GAIN_DB(%.2f) 必须 <= MERGE_BGM_BASE_GAIN_DB(%.2f)"
                % (self.duck_gain_db, self.base_gain_db))
        if not self.path:
            raise BgmError("bgm_missing", "MERGE_BGM_ENABLED=1 但 MERGE_BGM_PATH 为空")
        path = Path(self.path)
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise BgmError("bgm_format", "BGM 只支持 %s，收到 %s"
                           % ("/".join(SUPPORTED_SUFFIXES), path.suffix or "(无扩展名)"),
                           path=str(path))
        if require_file and not path.is_file():
            raise BgmError("bgm_missing", "BGM 文件不存在: %s" % path, path=str(path))
        return self


# ---------------------------------------------------------------------------
# 人声区间
# ---------------------------------------------------------------------------
def merge_intervals(intervals: Iterable[Sequence[float]], *, gap: float = 0.0
                    ) -> list[tuple[float, float]]:
    """排序并合并重叠/相邻（间隔 <= gap）的区间；空区间丢弃."""
    rows = sorted((float(a), float(b)) for a, b in intervals if float(b) > float(a))
    merged: list[tuple[float, float]] = []
    for start, end in rows:
        if merged and start <= merged[-1][1] + float(gap) + _EPS:
            prev = merged[-1]
            merged[-1] = (prev[0], max(prev[1], end))
        else:
            merged.append((start, end))
    return merged


def voice_intervals(timeline) -> list[tuple[float, float]]:
    """从时间线收集全部人声区间（逐镜 TTS 按镜头窗口；旁白按起止秒）.

    `tts_mix` 下逐镜 TTS 被 `apad` 铺满整个镜头窗口，所以人声区间 = 镜头窗口；
    旁白按自身 `[start, end]`。区间合并后返回，保证包络不重复起坡。
    """
    windows = list(getattr(timeline, "windows", ()) or ())
    rows: list[tuple[float, float]] = []
    for track in getattr(timeline, "audio_tracks", ()) or ():
        kind = str(getattr(track, "kind", "") or "")
        if kind == "shot_tts":
            index = getattr(track, "clip_index", None)
            if index is None:
                continue
            index = int(index)
            if 0 <= index < len(windows):
                window = windows[index]
                rows.append((float(window.start_sec), float(window.end_sec)))
        elif kind == "narration":
            start = float(getattr(track, "start_sec", 0.0) or 0.0)
            end = float(getattr(track, "end_sec", 0.0) or 0.0)
            if end > start:
                rows.append((start, end))
    return merge_intervals(rows)


def duck_segments(intervals: Iterable[Sequence[float]], *, attack: float,
                  release: float, total: float) -> list[tuple[float, float, float, float]]:
    """把人声区间扩成 `(ramp_start, start, end, ramp_end)` 并裁到 `[0, total]`."""
    total = max(0.0, float(total))
    rows: list[tuple[float, float, float, float]] = []
    for start, end in merge_intervals(intervals):
        ramp_start = max(0.0, float(start) - float(attack))
        ramp_end = min(total, float(end) + float(release))
        start = min(max(0.0, float(start)), total)
        end = min(max(0.0, float(end)), total)
        if ramp_end <= ramp_start or end <= start:
            continue
        rows.append((ramp_start, start, end, ramp_end))
    # 外扩后可能重叠：合并成一段，避免同一时刻两条斜坡相撞
    merged: list[tuple[float, float, float, float]] = []
    for row in rows:
        if merged and row[0] <= merged[-1][3] + _EPS:
            prev = merged[-1]
            merged[-1] = (prev[0], max(prev[1], row[1]), max(prev[2], row[2]),
                          max(prev[3], row[3]))
        else:
            merged.append(row)
    return merged


def envelope_expression(segments: Sequence[Sequence[float]], *, base_linear: float,
                        duck_linear: float) -> str:
    """生成 `volume` 包络表达式（线性斜坡；ffmpeg `eval=frame` 用）.

    结构：`if(lt(t,ramp_start), base, if(lt(t,start), 斜坡降, if(lt(t,end), duck,
    if(lt(t,ramp_end), 斜坡升, <下一段>))))`——段之间以「下一段」为缺省，保证多段
    连续；`t` 是采样时刻（秒）。表达式超长（长片区间多）时按最小间隔合并，确定性
    收敛到 :data:`MAX_EXPRESSION_CHARS` 以内。
    """
    rows = [tuple(float(x) for x in row) for row in segments]
    expr = _rebuild(rows, base_linear, duck_linear)
    while len(expr) > MAX_EXPRESSION_CHARS and len(rows) > 1:
        rows = _coalesce_closest(rows)
        expr = _rebuild(rows, base_linear, duck_linear)
    return expr


def _rebuild(segments, base_linear, duck_linear) -> str:
    base = "%.9f" % float(base_linear)
    duck = "%.9f" % float(duck_linear)

    def build(index: int) -> str:
        if index >= len(segments):
            return base
        ramp_start, start, end, ramp_end = (float(x) for x in segments[index])
        rest = build(index + 1)
        down = ("(%.9f+(%.9f-%.9f)*(t-%.6f)/%.6f)"
                % (base_linear, duck_linear, base_linear, ramp_start,
                   max(start - ramp_start, _EPS)))
        up = ("(%.9f+(%.9f-%.9f)*(t-%.6f)/%.6f)"
              % (duck_linear, base_linear, duck_linear, end,
                 max(ramp_end - end, _EPS)))
        return ("if(lt(t,%.6f),%s,if(lt(t,%.6f),%s,if(lt(t,%.6f),%s,"
                "if(lt(t,%.6f),%s,%s))))"
                % (ramp_start, base, start, down, end, duck, ramp_end, up, rest))

    return build(0)


def _coalesce_closest(segments: Sequence[Sequence[float]]):
    rows = [tuple(float(x) for x in row) for row in segments]
    best = None
    for i in range(len(rows) - 1):
        gap = rows[i + 1][0] - rows[i][3]
        if best is None or gap < best[0]:
            best = (gap, i)
    index = best[1] if best else 0
    left, right = rows[index], rows[index + 1]
    merged = (left[0], max(left[1], right[1]), max(left[2], right[2]),
              max(left[3], right[3]))
    return rows[:index] + [merged] + rows[index + 2:]


# ---------------------------------------------------------------------------
# 滤镜链与接线
# ---------------------------------------------------------------------------
def bgm_chain(config: BgmConfig, *, total_duration: float,
              intervals: Iterable[Sequence[float]]) -> str:
    """BGM 分支的完整音频滤镜链（循环由输入侧 `-stream_loop -1` 负责）."""
    total = max(_EPS, float(total_duration))
    segments = duck_segments(intervals, attack=config.attack_sec,
                             release=config.release_sec, total=total)
    expr = envelope_expression(segments, base_linear=config.base_linear,
                               duck_linear=config.duck_linear)
    chain = (f"aresample={SAMPLE_RATE},"
             "aformat=sample_fmts=fltp:channel_layouts=stereo,"
             f"atrim=duration={total:.6f},asetpts=PTS-STARTPTS,"
             f"volume='{expr}':eval=frame")
    fade_in = min(float(config.fade_in_sec), total)
    fade_out = min(float(config.fade_out_sec), total)
    if fade_in > 0:
        chain += f",afade=t=in:st=0:d={fade_in:.6f}"
    if fade_out > 0:
        chain += (f",afade=t=out:st={max(0.0, total - fade_out):.6f}:"
                  f"d={fade_out:.6f}")
    return chain


def bgm_input_args(config: BgmConfig) -> tuple[str, ...]:
    """BGM 输入的额外参数：无限循环，由 `atrim` 截到成片时长."""
    return ("-stream_loop", "-1")


def apply_bgm_to_graph(*, parts: list[str], audio_label: str, inputs,
                       timeline, config: BgmConfig | None,
                       total_duration: float) -> tuple[str, tuple]:
    """把 BGM 分支接到已编译的音频链末尾；`config is None` 时**原样返回**."""
    if config is None or not config.enabled:
        return audio_label, inputs
    config.validate(require_file=True)
    from step04_merge.backends.ffmpeg import InputSpec  # 延迟导入避免环

    index = len(inputs)
    spec = InputSpec(index=index, role="bgm", path=Path(config.path),
                     stable_id="bgm", extra_args=bgm_input_args(config))
    inputs = tuple(inputs) + (spec,)
    chain = bgm_chain(config, total_duration=total_duration,
                      intervals=voice_intervals(timeline))
    parts.append(f"[{index}:a]{chain}[bgm]")
    parts.append(f"{audio_label}[bgm]amix=inputs=2:duration=first:"
                 f"normalize=0:weights=1 1[outabgm]")
    return "[outabgm]", inputs


# ---------------------------------------------------------------------------
# 本地音效钩子（纯数据；无音效库 = 返回空，不发外部请求）
# ---------------------------------------------------------------------------
def resolve_local_sfx(audio_cue, sfx_dir) -> dict:
    """把 `audio_cue` 映射到本地音效文件（只读、纯数据、零网络）.

    `audio_cue` 可以是字符串或含 `sfx`/`cue`/`name` 键的字典；返回
    `{"cue": ..., "path": str|None, "reason": ...}`。找不到时 `reason` 说明原因，
    **不**发起任何外部请求，也不报错（音效是可选的氛围增强）。
    """
    if isinstance(audio_cue, Mapping):
        cue = str(audio_cue.get("sfx") or audio_cue.get("cue")
                  or audio_cue.get("name") or "").strip()
    else:
        cue = str(audio_cue or "").strip()
    if not cue:
        return {"cue": "", "path": None, "reason": "cue_empty"}
    if not sfx_dir:
        return {"cue": cue, "path": None, "reason": "sfx_dir_unset"}
    root = Path(sfx_dir)
    if not root.is_dir():
        return {"cue": cue, "path": None, "reason": "sfx_dir_missing"}
    safe = Path(cue).name
    for suffix in SUPPORTED_SUFFIXES:
        candidate = root / (safe + suffix)
        if candidate.is_file():
            return {"cue": cue, "path": str(candidate), "reason": "ok"}
    return {"cue": cue, "path": None, "reason": "not_found"}


__all__ = ["BgmConfig", "BgmError", "MAX_EXPRESSION_CHARS", "SAMPLE_RATE",
           "SUPPORTED_SUFFIXES", "apply_bgm_to_graph", "bgm_chain",
           "bgm_input_args", "db_to_linear", "duck_segments", "envelope_expression",
           "merge_intervals", "resolve_local_sfx", "voice_intervals"]
