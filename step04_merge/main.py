"""step04_merge - Video merge entry point"""
import sys
import subprocess
from pathlib import Path
import os

# Ensure imports resolve from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 运行范围（--chapter / --book / --book-name）必须在下面的 env 读取之前生效：
# CHAPTER_DIR / FINAL_OUT / SRT_OUT 等都是 import 时求值，main() 里再设 env 已经晚了。
if __name__ == "__main__":
    from shared.run_scope import bootstrap as _bootstrap_scope
    _bootstrap_scope("step04 视频合成")

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


# Configuration
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))
BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))

# 输出目录（统一路径来源: shared/paths.py）
from shared.paths import (
    chapter_dir, script_file, clips_dir, vimax_working_dir,
    vimax_shots_dir, shot_video_file, clip_file, audio_file,
    final_file, subtitles_file, stretched_dir,
)
CHAPTER_DIR = chapter_dir()
SCRIPT_FILE = script_file()
FINAL_OUT = final_file()
SRT_OUT = subtitles_file()
TEMP_DIR = stretched_dir()

# 模块级日志
from shared.tee_logger import get_logger

# 编码预设（匹配 merge_core.py；单源 shared/config.MERGE_PRESET）
from shared.config import MERGE_PRESET as _CFG_PRESET
VIDEO_PRESET = _CFG_PRESET

_logger = get_logger(CHAPTER_DIR / "step04.log", console_level="INFO")

# Video source
CLIP_SOURCE = os.getenv("CLIP_SOURCE", "vimax")
# 合并模式：2026-08 起仅 native（纯视频拼接，语音/背景音由视频模型生成，保留视频原声）
VIMAX_WD = vimax_working_dir()
if CLIP_SOURCE == "vimax":
  CLIP_DIR = vimax_shots_dir()
else:
  CLIP_DIR = Path(os.getenv("VIDEO_OUTPUT_DIR", str(clips_dir())))

DEBUG_SKIP_CACHE = os.getenv("DEBUG_SKIP_CACHE", "0").strip() == "1"

# Submodule imports
from step04_merge.stretch import get_media_duration
from step04_merge.srt_gen import generate_srt
import json
import re
import os


# v6.22：音频音量归一（FFMPEG_AUDIO_VOLUME 环境变量，如 5dB/0.8；默认空=不处理）
_AUDIO_VOLUME = os.getenv("FFMPEG_AUDIO_VOLUME", "").strip()

# v6.27（08-15）：画质/音频模式配置化（shared.config）
try:
    from shared.config import (
        MERGE_RESOLUTION as _RES,
        MERGE_CRF as _CRF,
        MERGE_AUDIO_MODE as _AUDIO_MODE,
        MERGE_AUDIO_LOUDNORM as _LOUDNORM,
    )
except ImportError:
    _RES, _CRF, _AUDIO_MODE, _LOUDNORM = "720x1280", "18", "native", "1"

# v6.73: resolve the real audio source from model capability + user mode.
from shared.audio_policy import resolve_audio_policy
_AUDIO_POLICY = resolve_audio_policy()
_AUDIO_MODE = _AUDIO_POLICY.effective_merge_mode
_m = re.match(r"(\d+)x(\d+)", _RES.strip())
if not _m:
    raise ValueError(f"MERGE_RESOLUTION 格式错误（应为 WxH，如 720x1280）: {_RES!r}")
OUT_W, OUT_H = int(_m.group(1)), int(_m.group(2))
# 字幕字号随分辨率缩放（720 宽→22，1080 宽→33）
SUB_FONT_SIZE = max(20, int(OUT_W * 22 / 720))


# v6.22：多转场映射表（clip_transition 文本关键词 → xfade transition 类型）
_TRANSITION_MAP = (
    # 黑场闪白 / 冲击转场
    ("黑场闪白", "fadeblack"),
    ("画面冲击", "fadeblack"),
    ("冲击转场", "fadeblack"),
    ("光线闪白", "fadewhite"),
    ("闪白", "fadewhite"),
    # 推移 / 推镜 / 拉远
    ("推移转场", "slideleft"),
    ("推近匹配", "smoothleft"),
    ("拉远揭示", "smoothright"),
    ("推移", "slideleft"),
    # 擦拭 / 扫光
    ("擦拭", "wipeleft"),
    ("扫光", "wipeleft"),
    ("物体擦拭", "wipeup"),
    # 平滑 / 柔化 / 虚化
    ("焦点虚化", "smoothup"),
    ("模糊叠化", "smoothdown"),
    ("平滑", "smoothleft"),
    ("柔化", "smoothdown"),
    # 旋转 / 径向
    ("旋转", "radial"),
    ("光圈", "radial"),
    ("径向", "radial"),
    # 叠化 / 溶解
    ("叠化", "fade"),
    ("dissolve", "fade"),
    ("淡入淡出", "fade"),
    ("交叉溶解", "fade"),
    # 匹配 / 空镜
    ("空镜匹配", "fade"),
    ("匹配剪辑", "fade"),
    ("动作匹配", "fade"),
    # 淡入 / 淡出
    ("淡入", "fade_in"),
    ("淡出", "fade_out"),
    # 硬切 / 跳跃
    ("硬切", "cut"),
    ("jump", "cut"),
    ("跳跃剪辑", "cut"),
)

# 可用的 xfade transition 白名单（校验 kind 合法，非法回退 fade）
_VALID_XFADE = {
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite",
    "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
}


# 旁白叠加的混音权重（2026-09-30 复核第 7 轮第 3 项）
# `amix:normalize=0` + 显式权重：默认归一化按**权重和**整体衰减，旁白条数一变原声
# 音量就跟着变。这里固定「原声 0.6 / 每条旁白 1.0」——原声**保留**（不被旁白顶掉，
# 这是复核明确要求的），旁白为主。
_NARRATION_BASE_GAIN = 0.6
_NARRATION_GAIN = 1.0


def _collect_shot_tts_paths(valid_shots, clips, script):
  """tts_mix：逐镜对白轨的收集 + **消费侧身份核对**（返回与 `valid_shots` 等长的路径表）.

  三处显式失败（都不静默降级）：音轨超出实际视频时长、缺音轨、音轨与当前文本不符。
  判据必须与**生成器**（`gen_shot_tts`）一致：纯叙述镜不出对白轨——它的文字是字幕
  （旁白由独立旁白轨承担），旧实现按「`narration_segment` 非空」要求音轨，纯叙述镜
  必然被记成缺失 ⇒ tts_mix 整段失败（2026-09-30 复核第 7/8 轮）。
  """
  from shared.paths import audio_file
  from step01_script_gen.plan_feasibility import (
    shot_needs_dialogue_track as _needs_dialogue_track)
  from shared.audio_identity import verify_text_signature as _vts
  from step01_script_gen.plan_feasibility import shot_voice_parts as _svp
  _plan = script.get("chapter_outline") if isinstance(script, dict) else None
  _plan = _plan if isinstance(_plan, dict) else None
  tts_paths: list = []
  n_found = 0
  missing_tts: list = []
  for _i, s in enumerate(valid_shots):
    _tp = audio_file(s.get('index', 0))
    _split_probe = _svp(s, _plan)
    _expect_probe = (_split_probe["dialogue"].strip() if _split_probe["entries"] else None)
    if _tp.exists() and _tp.stat().st_size > 10240 and _expect_probe == "":
      # 纯旁白镜（结构化分流后没有对白那一份）：**不消费**任何对白轨文件——
      # 磁盘上若是 round-8 之前的残留，收进来会与独立旁白轨重复发声（复核第 8 轮）。
      _logger.warn(f"[WARN] shot {s.get('index')} 是纯旁白镜，但存在对白音轨 {_tp.name}"
                   "（应是旧产物）——不消费它（旁白由独立旁白轨承担）；建议删除该文件")
      tts_paths.append(None)
      continue
    if _tp.exists() and _tp.stat().st_size > 10240:
      # A TTS track longer than the actual clip cannot be silently atrim'd.
      if _i < len(clips):
        _clip_dur = get_media_duration(clips[_i])
        _tts_dur = get_media_duration(_tp)
        if (_clip_dur > 0 and _tts_dur > _clip_dur + 0.05
                and os.getenv("TTS_MIX_ALLOW_TRIM", "0").strip() != "1"):
          raise RuntimeError(
            f"shot {s.get('index')} TTS 超出实际视频时长: "
            f"tts={_tts_dur:.2f}s clip={_clip_dur:.2f}s。请调整台词/停顿/镜头，"
            "禁止 step04 静默截句。显式允许可设 TTS_MIX_ALLOW_TRIM=1。"
          )
      # 逐镜对白轨的**消费侧**身份核对（复核第 8 轮：这个类别也应覆盖）：
      # 拿不到角色音色配置，只核**正文**指纹；缺边车是历史产物 ⇒ 告警不失败。
      _split = _svp(s, _plan)
      if _split["entries"]:
        _expect = _split["dialogue"].strip()
      else:
        # 旧稿口径：生成侧签的是**剥掉说话人前缀**之后的文本（`gen_shot_tts` 里的
        # `strip_speaker_prefix(raw, characters)`），消费侧必须用**同一个函数、同一份
        # characters** 复算——否则自己刚生成的音轨会被判「与当前文本不符」。
        from shared.dialogue import strip_speaker_prefix as _ssp
        _expect = _ssp(str(s.get("narration_segment", "")).strip(),
                       script.get("characters") or [])
      _why = _vts(_tp, _expect) if _expect else ""
      if _why and _why != "missing":
        raise RuntimeError(
          f"shot {s.get('index')} 的 TTS 音轨与当前文本不符（{_why}）。"
          "计划重选/改稿后旧音轨会顶着同一个文件名——请重跑 step03，"
          "或删掉该音轨后重跑。"
        )
      if _why == "missing":
        _logger.warn(f"[WARN] shot {s.get('index')} 的 TTS 音轨没有签名边车，"
                     "无法确认是否对应当前文本（历史产物）")
      tts_paths.append(str(_tp))
      n_found += 1
    elif _needs_dialogue_track(s, _plan):
      # 判据与**生成器**单源，且必须是**结构化分流**（只有旧稿才回退标点启发式）：
      # 用标点启发式时，「纯旁白镜、正文里带引号」会被误判成"该有对白轨" ⇒ 整章失败，
      # 而生成器按分流根本不会产出它（视角扫描实测）。
      tts_paths.append(None)
      missing_tts.append(s.get('index', 0))
    else:
      tts_paths.append(None)
  if missing_tts:
    # 2026-09-13 审计修复：tts_mix 下「需要 TTS 的镜头缺失」默认判定为失败——
    # 此前只记数量继续成片，缺失镜头静默保留视频原声（对白可能完全丢失）。
    # 显式降级：TTS_MIX_ALLOW_MISSING=1 恢复旧行为（缺失镜头保持纯视频原声）
    if os.getenv("TTS_MIX_ALLOW_MISSING", "0").strip() != "1":
      raise RuntimeError(
        f"tts_mix 模式缺少 {len(missing_tts)}/{len(valid_shots)} 个镜头的 TTS 音频"
        f"（shot index: {missing_tts}）。请先运行 step03 生成，"
        f"或设 TTS_MIX_ALLOW_MISSING=1 显式允许缺失镜头保持视频原声。"
      )
    _logger.warn(f"[WARN] tts_mix: {len(missing_tts)} 个镜头缺 TTS 音频，"
                 f"按 TTS_MIX_ALLOW_MISSING=1 保持纯视频原声: {missing_tts}")
  _logger.info(f"[INFO] tts_mix: {n_found}/{len(valid_shots)} 镜头有 TTS 音频")
  return tts_paths


def _collect_narration_tracks(script, valid_shots, clip_windows):
    """图外旁白 → 合并落点表 `[{path, index, start, end, dur, n_shots}]`.

    **落点单源**：`voice_units`（与候选层/终稿层/提交层的生成验收同一判据——
    「从承载节拍的第一镜起、连续消费完这条旁白」）。本函数只做两件事：
    ① 换算成最终时间轴上的秒数；② 核验音轨存在、且**讲得完**。
    """
    from shared.paths import narration_audio_file, audio_file
    from shared.audio_identity import verify_signature
    from step01_script_gen.plan_feasibility import (layout_voice_entries,
                                                    plan_narration_entries,
                                                    voice_units)
    from tts.voice_registry import NARRATOR_VOICE
    plan = script.get("chapter_outline")
    plan = plan if isinstance(plan, dict) else {}
    entries = plan_narration_entries(plan)
    if not entries:
        return []
    _pos = {str(s.get("index")): i for i, s in enumerate(valid_shots)}
    _win = {str(valid_shots[_i].get("index")): clip_windows[_i]
            for _i in range(min(len(valid_shots), len(clip_windows)))}
    # 全部声音条目（排版要算上对白的占用）；`_units` 只是旁白那一份（逐条核验用）
    _all_units = voice_units(plan, valid_shots)
    _units = [u for u in _all_units if u["kind"] == "narration"]
    # 先探测**真实**时长（存在性与时长核验本来就要做），再排版——用估计值排出来的
    # 时间轴，同镜两条旁白在真实时间轴上仍可能重叠。
    #
    # 2026-10-01 复核 P1：**对白的占用也必须是实测值**。只探旁白、对白用 `est_sec` 占位
    # 时，实测对白 5.67s / 估时 3.67s 就会让旁白从 3.67s 开始、重叠 2s。
    # 「排版占用」与「实际播放的音轨」必须绑定：
    #   - tts_mix：对白是独立音轨（`shot_<idx>.mp3`），实测时长；合并时按镜头窗口
    #     `atrim`，故占用取 min(实测, 镜头窗口)；
    #   - 原生：对白在视频原声里，**没有**发声时间信息（不是独立轨，探不到）——
    #     占用只能按估时避让，并记 `timing_provisional`，不宣称保证不重叠。
    _missing_pre: list = []
    _durs: dict = {}
    _provisional: list = []
    _occupied: dict = {}
    for _u in _all_units:
        if _u["kind"] != "narration":
            # **对白占用按「实际播放的每镜音轨」建区间，每条物理音轨计一次**（复核
            # 2026-10-01）：一条对白跨 N 镜就有 N 个 `shot_<镜头号>.mp3`，各从**自己那一镜**
            # 的窗口起点播（合并时 `asetpts=PTS-STARTPTS` + `atrim` 到镜头时长）。
            # 注意 `shot_<n>.mp3` 里的 n 是**镜头序号**，不是 D 编号——旧实现拿 D 编号去
            # 拼文件名（D1 在 shot8 时读的是 `shot_1.mp3`）。
            _run = [str(_s) for _s in (_u.get("shot_indexes") or [])]
            _ivs: list = []
            if _AUDIO_MODE == "tts_mix" and _run:
                for _sid in _run:
                    _w = _win.get(_sid)
                    if not _w:
                        continue
                    _s0, _s1 = float(_w[0]), float(_w[1])
                    _tp = audio_file(int(_sid)) if str(_sid).lstrip("-").isdigit() else None
                    _d = None
                    if _tp is not None and _tp.exists() and _tp.stat().st_size > 10240:
                        _raw = float(get_media_duration(_tp) or 0)
                        if _raw > 0.2:
                            _d = min(_raw, _s1 - _s0)
                    if _d is None:
                        # 该镜没有实测音轨：按估时按比例占位（不宣称保证不重叠）
                        _provisional.append(_u["id"])
                        _d = min(float(_u.get("est_sec") or 0.0) / max(len(_run), 1),
                                 _s1 - _s0)
                    if _d > 0:
                        _ivs.append((_s0, _s0 + _d))
            else:
                # 原生：对白在视频原声里，**没有**发声时间信息（不是独立音轨），
                # 只能按估时避让整段承载片段的起点——如实记 provisional。
                _w0 = _win.get(_run[0]) if _run else None
                if _w0:
                    _provisional.append(_u["id"])
                    _span = sum(float(_win[_s][1]) - float(_win[_s][0])
                                for _s in _run if _s in _win)
                    _d = min(float(_u.get("est_sec") or 0.0), _span or 0.0)
                    if _d > 0:
                        _ivs.append((float(_w0[0]), float(_w0[0]) + _d))
            if _ivs:
                _occupied[_u["id"]] = _ivs
            continue
        _k = int(_u["index"])
        _p0 = narration_audio_file(_k)
        if not (_p0.exists() and _p0.stat().st_size > 10240):
            _missing_pre.append(_k)
            continue
        _d = float(get_media_duration(_p0) or 0)
        if _d > 0.2:
            _durs[_u["id"]] = _d
    # **排版**（复核第 8 轮 P1）：同镜多条声音按序、不重叠、核可用时长——
    # 「在哪些镜头」解决不了「镜内何时开始」，两份落点都写 start=5.0 就会同时播放。
    # 喂**全部**声音条目（对白 + 旁白）：只喂旁白会让同镜的对白占用**不被算进来**，
    # 旁白仍与对白轨同起点（视角扫描实测）。
    _laid, _layout_problems = layout_voice_entries(_all_units, _win, durations=_durs,
                                                   occupied=_occupied)
    _narr_problems = [p for p in _layout_problems if str(p).startswith("N")]
    if _narr_problems:
        raise RuntimeError("图外旁白排不进承载片段（不截句）："
                           + "；".join(_narr_problems[:3]))
    _by_id = {t["id"]: t for t in _laid}
    tracks: list = []
    missing: list = []
    for _v in _units:
        _k = int(_v["index"])
        _run = [s for s in (_v["shot_indexes"] or []) if s in _pos]
        if not _run or len(_run) != len(_v["shot_indexes"] or []):
            raise RuntimeError(
                "旁白 N%d 在成稿里没有完整落点（承载它的镜头缺失，或没有镜头声明承载"
                "它的节拍）——旁白必须有地方讲，停止合并：%s"
                % (_k, str(_v["text"])[:24]))
        _p = narration_audio_file(_k)
        if not (_p.exists() and _p.stat().st_size > 10240):
            missing.append(_k)
            continue
        # **消费侧身份核对**（复核第 8 轮 P1）：文件在 ≠ 内容对。计划重选或 N 编号
        # 变化后，上一稿的旁白音轨会顶着同一个文件名——step03 的缓存检查替代不了这里。
        _why = verify_signature(_p, _v["text"], NARRATOR_VOICE)
        if _why:
            if os.getenv("NARRATION_ALLOW_STALE", "0").strip() == "1":
                _logger.warn(f"[WARN] 旁白 N{_k} 音轨身份不符（{_why}），按 "
                             f"NARRATION_ALLOW_STALE=1 仍然使用: {_p.name}")
            else:
                raise RuntimeError(
                    "旁白 N%d 音轨与当前计划不符（%s）：%s。请重跑 step03 生成，"
                    "或设 NARRATION_ALLOW_STALE=1 显式接受（会播出上一稿的旁白）。"
                    % (_k, _why, _p))
        _i0, _i1 = _pos[_run[0]], _pos[_run[-1]]
        _start = float((_by_id.get("N%d" % _k) or {}).get(
            "start", float(clip_windows[_i0][0])))
        _end = float(clip_windows[_i1][1])
        _dur = float(get_media_duration(_p) or 0)
        _avail = _end - _start
        if _dur <= 0.2:
            raise RuntimeError("旁白 N%d 音轨无效或时长探测失败: %s" % (_k, _p))
        if (0 < _avail < _dur - max(0.05, _avail * 0.02)
                and os.getenv("NARRATION_ALLOW_TRIM", "0").strip() != "1"):
            raise RuntimeError(
                "旁白 N%d 比它承载的镜头片段还长: 旁白 %.2fs > 片段 %.2fs"
                "（镜头 %s）。请调整旁白/镜头，**禁止截句**；"
                "显式允许可设 NARRATION_ALLOW_TRIM=1。"
                % (_k, _dur, _avail, _run))
        tracks.append({"path": _p, "index": _k, "start": _start, "end": _end,
                       "dur": _dur, "n_shots": len(_run),
                       # 该旁白的起点是否只按**估时**避让对白（原生对白无实测时间）
                       "timing_provisional": bool(_provisional)})
    if missing:
        if os.getenv("NARRATION_ALLOW_MISSING", "0").strip() != "1":
            raise RuntimeError(
                "缺少 %d 条图外旁白的 TTS 音轨（N%s）——合并会产出**没人念**的成片。"
                "请先运行 step03（audio/narration_<NN>.mp3），或设 "
                "NARRATION_ALLOW_MISSING=1 显式允许旁白不出声。"
                % (len(missing), missing))
        _logger.warn(f"[WARN] 缺少 {len(missing)} 条旁白音轨，按 "
                     f"NARRATION_ALLOW_MISSING=1 继续（这些旁白不会出声）: N{missing}")
    return tracks


def _parse_transition(text: str, default_dur: float = 0.5):
    """解析 clip_transition 字符串 → (kind, duration_seconds).

    kind: 'cut'(硬切) | 'fade_in'(淡入) | 'fade_out'(淡出) | xfade 转场名
    （fade/wipeleft/slideleft/fadeblack/radial... 多转场增强 v6.22）
    duration 从「转场时长0.5s/0.5秒」提取，clamp 到 0.1~1.5s（短转场防突兀）。
    """
    text = (text or "").strip()
    if not text:
        return "cut", 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*[秒s]", text)
    dur = float(m.group(1)) if m else default_dur
    # 是否显式提到转场（带「转场」字样或时长），用于区分「硬切」和「硬切+快速转场」
    has_transition_word = "转场" in text or m is not None

    # v6.22：多转场映射（长词优先）——黑场闪白/冲击→fadeblack、推移→slide、擦拭→wipe、
    # 旋转→radial、平滑→smooth、光线闪白→fadewhite，其余叠化/淡入淡出→fade
    kind = None
    for kw, xf in _TRANSITION_MAP:
        if kw in text:
            kind = xf
            break
    if kind is None:
        kind = "fade"
    # 纯「硬切」（无转场时长）→ cut
    if "硬切" in text and not has_transition_word:
        kind = "cut"
    # 「硬切，战斗转场时长0.2s」→ 快速交叉溶解（保留 fade）
    elif kind == "cut" and has_transition_word:
        kind = "fade"

    if kind == "cut":
        dur = 0.0
    else:
        dur = max(0.1, min(dur, 1.5))
    return kind, dur


def _compute_clip_windows(durations, transitions):
    """Return per-clip final (start,end) and total duration.

    xfade overlaps make cumulative-duration subtraction ambiguous.  The windows
    below are the single source shared by the ffmpeg filter and subtitles.
    """
    durs = list(durations or [])
    if not durs:
        return [], 0.0
    windows = [(0.0, float(durs[0]))]
    total = float(durs[0])
    for i in range(1, len(durs)):
        kind, tdur = transitions[i] if i < len(transitions) else ("cut", 0.0)
        if kind == "cut" or tdur <= 0.01:
            start = total
            end = start + float(durs[i])
        else:
            start = max(0.0, total - float(tdur))
            end = start + float(durs[i])
        windows.append((round(start, 3), round(end, 3)))
        total = end
    return windows, round(total, 3)


def _build_native_transition_filter(clips, transitions, video_filter,
                                    tts_paths=None, narration_tracks=None):
    """为 native 模式构建带转场的 filter_complex。

    视频：xfade（交叉溶解，要求各输入已归一化为同尺寸/帧率）
    音频：acrossfade（同步交叉淡化，避免硬切爆音）
    纯「硬切」：concat 直接拼接（不重叠）。
    v6.27（08-15）：
      - 分辨率配置化（MERGE_RESOLUTION，OUT_W×OUT_H），缩放用
        increase+crop 而非 pad——704 宽 hook 不再烧进 8px 黑边（轻微裁切
        上下边缘换取满幅）。
      - 无音频流片段用 anullsrc 补静音轨（回退 happyhorse 无声不再崩合并）。
      - 逐镜 loudnorm 响度归一（MERGE_AUDIO_LOUDNORM=1，替代固定增益——
        固定 +5dB 会放大安静镜头底噪且不解决镜头间响度差）。
      - tts_paths（MERGE_AUDIO_MODE=tts_mix 时）：每镜叠加 TTS 对白轨，
        视频原声经 sidechaincompress 闪避（对白出现时压低环境音）。
        TTS 缺失的镜保持纯原声。
      - narration_tracks（有图外旁白时）：在**拼接完成的最终音轨**上叠加独立 TTS
        旁白轨（`adelay` 到落点起点，`amix:normalize=0` 固定权重）——**两种模式都生效**
        （native 与 tts_mix 共用同一条路），角色原声保留。

    Returns:
        (filter_complex, video_label, audio_label, total_duration, clip_windows,
         n_extra_inputs)
        clip_windows 是每个片段在最终时间轴上的真实 (start, end)，字幕直接复用。
        n_extra_inputs: 额外音频输入数（tts_mix 模式，追加在视频输入之后）。
    """
    from step04_merge.stretch import has_audio_stream
    durs = [get_media_duration(c) for c in clips]
    n = len(clips)
    parts = []

    # TTS 输入映射：tts_paths[i] 非空 → 追加为输入 n+pending，记录输入序号
    tts_inputs: list[int | None] = [None] * n
    n_extra = 0
    if _AUDIO_MODE == "tts_mix":
        for i, tp in enumerate(tts_paths or []):
            if tp and Path(tp).exists():
                tts_inputs[i] = n + n_extra
                n_extra += 1
    # 旁白输入紧跟在 TTS 输入之后（调用方按同一顺序追加 `-i`）
    narration_inputs: list = []
    for _t in (narration_tracks or []):
        narration_inputs.append(n + n_extra)
        n_extra += 1

    # 归一化 + 截断每个输入（分辨率配置化；hook/shot 分辨率帧率不一致必须统一）
    for i, d in enumerate(durs):
        v_chain = (
            f"[{i}:v]fps=24,"
            f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},setsar=1,format=yuv420p,"
            f"trim=duration={d:.2f},setpts=PTS-STARTPTS,settb=AVTB"
        )
        # 首个镜头的「淡入」→ 从黑场淡入
        if i == 0 and transitions[0][0] == "fade_in":
            v_chain += f",fade=t=in:st=0:d={min(transitions[0][1], d):.2f}"
        parts.append(f"{v_chain}[nv{i}]")

        # 音频链：无声片段补静音轨（anullsrc），有声片段正常取 [i:a]
        if has_audio_stream(clips[i]):
            _a_chain = (
                f"[{i}:a]atrim=duration={d:.2f},asetpts=PTS-STARTPTS,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=44100"
            )
        else:
            _logger.warn(f"[WARN] 片段无音频流（补静音轨）: {clips[i].name}")
            _a_chain = (
                f"anullsrc=r=44100:channel_layouts=stereo,atrim=duration={d:.2f},"
                f"asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:channel_layouts=stereo"
            )
        # 响度归一：优先 loudnorm（EBU R128 统一感知响度）；
        # 显式配置了 FFMPEG_AUDIO_VOLUME 则用固定增益（旧行为兼容）
        if _LOUDNORM == "1" and not _AUDIO_VOLUME:
            _a_chain += ",loudnorm=I=-16:TP=-1.5:LRA=11"
        elif _AUDIO_VOLUME:
            _a_chain += f",volume={_AUDIO_VOLUME}"

        # tts_mix：叠加 TTS 对白轨 + 视频原声闪避，混音结果统一标记 [na{i}]
        if tts_inputs[i] is not None:
            _in = tts_inputs[i]
            # TTS 轨：截到镜头时长、不足补静音（对白从镜头起点播，电平基准）
            parts.append(
                f"[{_in}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"aresample=44100,atrim=duration={d:.2f},asetpts=PTS-STARTPTS,"
                f"apad=whole_dur={d:.2f}[tts{i}]"
            )
            # 原声压低 + TTS 对白为主（amix 权重：原声 0.6 / TTS 1.0）
            _a_chain += (
                f",[tts{i}]amix=inputs=2:duration=first:weights=0.6 1[na{i}]"
            )
        else:
            _a_chain += f"[na{i}]"
        parts.append(_a_chain)

    clip_windows, total = _compute_clip_windows(durs, transitions)
    cur_v, cur_a = "[nv0]", "[na0]"
    x = 0
    # transitions[i] 描述「从 clip i-1 进入 clip i」的转场
    for i in range(1, n):
        kind, tdur = transitions[i]
        if kind == "cut" or tdur <= 0.01:
            x += 1
            parts.append(f"{cur_v}[nv{i}]concat=n=2:v=1:a=0[vx{x}]")
            parts.append(f"{cur_a}[na{i}]concat=n=2:v=0:a=1[ax{x}]")
            cur_v, cur_a = f"[vx{x}]", f"[ax{x}]"
        else:
            offset = max(0.0, clip_windows[i][0] - 0.001)
            x += 1
            # v6.22：xfade transition 类型动态（fade/wipeleft/slideleft/fadeblack...），
            # kind 直接作 xfade transition 参数；非法回退 fade
            xfade_type = kind if kind in _VALID_XFADE else "fade"
            parts.append(
                f"{cur_v}[nv{i}]xfade=transition={xfade_type}:duration={tdur:.3f}:offset={offset:.3f}[vx{x}]"
            )
            parts.append(f"{cur_a}[na{i}]acrossfade=d={tdur:.3f}[ax{x}]")
            cur_v, cur_a = f"[vx{x}]", f"[ax{x}]"

    # 最后一个镜头的「淡出」→ 末尾淡出到黑
    if transitions[n - 1][0] == "fade_out":
        fade_d = min(transitions[n - 1][1], durs[-1])
        parts.append(f"{cur_v}fade=t=out:st={max(0.0, total - fade_d):.2f}:d={fade_d:.2f}[nvout]")
        cur_v = "[nvout]"

    # 图外旁白：延迟到落点起点后，叠在**最终音轨**上（此时 cur_a 已是拼接/转场后的
    # 完整音轨，绝对时间 = clip_windows 上的秒数）。
    # `normalize=0` + 显式权重：默认归一化按权重和衰减，旁白条数一变原声就跟着变。
    # `duration=first`：输出长度取第一路（拼接后的原声轨）——旁白**不会**把成片拉长。
    if narration_inputs:
        _labels = []
        for _j, _in in enumerate(narration_inputs):
            _ms = max(0, int(round(float(narration_tracks[_j]["start"]) * 1000)))
            parts.append(
                f"[{_in}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"aresample=44100,adelay={_ms}|{_ms}[nr{_j}]"
            )
            _labels.append(f"[nr{_j}]")
        _weights = " ".join([str(_NARRATION_BASE_GAIN)]
                            + [str(_NARRATION_GAIN)] * len(_labels))
        parts.append(
            f"{cur_a}{''.join(_labels)}amix=inputs={len(_labels) + 1}"
            f":duration=first:normalize=0:weights={_weights}[outa]"
        )
        cur_a = "[outa]"

    # 字幕+调色叠加在最终视频流上（与 filter_complex 同图，不能另用 -vf）
    parts.append(f"{cur_v}{video_filter}[outv]")

    parts = [p for p in parts if p]  # 清理占位空段
    return ";".join(parts), "[outv]", cur_a, total, clip_windows, n_extra


def _resolve_hook_clip(hook):
    """Return hook clip path or fail when the script planned a hook."""
    if not hook or not isinstance(hook, dict):
        return None
    from shared.paths import clip_file
    p = clip_file(0)
    if p.exists() and p.stat().st_size > 20000:
        return p
    raise FileNotFoundError(
        f"脚本包含 hook，但 hook 视频缺失或损坏: {p}。\n"
        "禁止静默跳过；请先完成 step02b 的 hook 生成。"
    )


def _validate_final_output(final_path: Path, expected_duration: float) -> float:
    """Decode-check the final file, duration tolerance and audio stream."""
    from step04_merge.merge_core import _find_exe
    from step04_merge.stretch import has_audio_stream
    if not final_path.exists() or final_path.stat().st_size <= 20 * 1024:
        raise RuntimeError(f"final.mp4 缺失或过小: {final_path}")
    actual = get_media_duration(final_path)
    if actual <= 0:
        raise RuntimeError(f"final.mp4 时长探测失败: {final_path}")
    tolerance = max(1.0, float(expected_duration or actual) * 0.03)
    if abs(actual - float(expected_duration or actual)) > tolerance:
        raise RuntimeError(
            f"final.mp4 时长偏差过大: actual={actual:.2f}s expected={expected_duration:.2f}s "
            f"tolerance={tolerance:.2f}s"
        )
    if not has_audio_stream(final_path):
        raise RuntimeError("final.mp4 无音频流，拒绝标记为可发布")
    ffmpeg = _find_exe("ffmpeg")
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(final_path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "final.mp4 完整解码失败: "
            + (proc.stderr or proc.stdout or "")[-500:]
        )
    return actual


def main():
  _logger.separator("=", 60)
  _logger.info(" Step 4: Video Merge (Stretch + Concat + Subtitle + Color)")
  _logger.info(" Output: final.mp4 (native, 语音/背景音由视频模型生成)")
  _logger.separator("=", 60)

  # Check prerequisites
  if not SCRIPT_FILE.exists():
    raise FileNotFoundError(
      f"Script not found: {SCRIPT_FILE}\n"
      f"Please run step01 first"
    )

  if CLIP_SOURCE == "vimax":
    if not VIMAX_WD.exists():
      raise FileNotFoundError(
        f"ViMax working dir not found: {VIMAX_WD}\n"
        f"Please run step02 first"
      )
    if not CLIP_DIR.exists():
      raise FileNotFoundError(
        f"Video clips dir not found: {CLIP_DIR}\n"
        f"Please run step02 to generate clips"
      )
  else:
    if not CLIP_DIR.exists():
      raise FileNotFoundError(
        f"Video clips dir not found: {CLIP_DIR}\n"
        f"Please run step02 first"
      )

  # Ensure output directories exist
  CHAPTER_DIR.mkdir(parents=True, exist_ok=True)
  TEMP_DIR.mkdir(parents=True, exist_ok=True)

  # 1. Load script
  with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
    script = json.load(f)

  from shared.quality_gate import require_gate, verify_step01_commit_manifest
  require_gate(CHAPTER_DIR / "step01_quality.json", script, stage="step01",
               required_report_type="quality_gate")
  # v6.103：三份 step01 产物必须属于同一个提交批次（见 shared/quality_gate）
  verify_step01_commit_manifest(CHAPTER_DIR, script, stage="step01",
                                expected_chapter=CHAPTER_INDEX)
  require_gate(CHAPTER_DIR / "step02b_quality.json", script, stage="step02b",
               required_report_type="product_acceptance", verify_outputs=True)

  shots = script.get("shots", [])
  if not shots:
    raise ValueError("No shots in script")

  _logger.info(f"[INFO] Loaded {len(shots)} shots")

  # 1.5. Check for hook video
  # Hook视频位置: clips/clip_0.mp4
  hook = script.get("hook")
  hook_clip = _resolve_hook_clip(hook)
  if hook_clip:
    _logger.info(f"[INFO] Hook video found: {hook_clip}")


  # 2. Collect video clips
  clips = []
  valid_shots = []
  missing_idxs = []

  # Hook goes first
  if hook_clip:
    clips.append(hook_clip)
    # Create a pseudo-shot for the hook so that SRT/subtitle handling works
    hook_shot = dict(hook)
    hook_shot["index"] = 0
    valid_shots.append(hook_shot)

  for shot in shots:
    idx = shot.get("index", 0)
    # v6.22：按 CLIP_SOURCE 分支处理 clip 路径——vimax 用 shots/{idx}/video.mp4，
    # 非 vimax（agens-clips 等）用扁平 clip_{idx}.mp4（此前硬编码 vimax 结构导致
    # 非 vimax 时合并路径不可用，code-review-2026-08-06 #10）
    if CLIP_SOURCE == "vimax":
      clip_path = shot_video_file(idx)
    else:
      clip_path = CLIP_DIR / f"clip_{idx}.mp4"
    if not clip_path.exists():
      _logger.warn(f"[WARN] Video clip not found: {clip_path}, skip this shot")
      missing_idxs.append(idx)
      continue
    clips.append(clip_path)
    valid_shots.append(shot)

  if missing_idxs:
    raise FileNotFoundError(
      f"缺少 {len(missing_idxs)} 个分镜视频（shot index: {missing_idxs}），"
      "停止合并，避免成片丢失剧情。请先完成 step02b。"
    )
  if not clips:
    raise FileNotFoundError(f"No valid video clips. Please run step02 first")

  _logger.info(f"[INFO] Video clips ready: {len(clips)}")

  # v6.73: resolve audio source from model capability + the real clip streams.
  # If native is advertised but clips are silent, switch to tts_mix; the TTS
  # presence check below then prevents silent final output.
  from step04_merge.stretch import has_audio_stream
  actual_audio_present = all(has_audio_stream(c) for c in clips)
  # 图外旁白是正交的一维（复核第 7 轮）：不改 `effective_merge_mode`，只在最终
  # 音轨上叠加独立 TTS 轨——角色原声保留（不许用「切 tts_mix」绕过）。
  from step01_script_gen.plan_feasibility import plan_narration_entries as _pne
  _plan_for_policy = script.get("chapter_outline")
  _narr_for_policy = _pne(_plan_for_policy
                          if isinstance(_plan_for_policy, dict) else {})
  _policy = resolve_audio_policy(actual_audio_present=actual_audio_present,
                                 narration_present=bool(_narr_for_policy))
  global _AUDIO_MODE
  _AUDIO_MODE = _policy.effective_merge_mode
  _logger.info(
    f"[AUDIO-POLICY] model={_policy.video_model or 'unknown'} "
    f"native_supported={_policy.model_supports_audio} actual_clip_audio={actual_audio_present} "
    f"effective={_AUDIO_MODE} narration_tts={_policy.narration_tts_required}"
    f"（旁白 {len(_narr_for_policy)} 条） reason={_policy.reason}"
  )

  # 3. Get target durations (视频模型生成，native 模式直接以视频实际时长为准)
  target_durations = [get_media_duration(c) for c in clips]
  zero_durations = [(i, str(c)) for i, c in enumerate(clips) if target_durations[i] <= 0]
  if zero_durations:
    # 2026-09-13 审计修复：时长探测失败 fail-fast——继续合并会基于 0 秒构建
    # 字幕/转场/filter，产出坏片或抛难定位的 ffmpeg filter 错误
    raise RuntimeError(
      f"{len(zero_durations)} 个片段时长探测失败（ffprobe 返回 0 或报错），"
      f"无法构建字幕时间轴与转场，停止合并: {[c for _, c in zero_durations]}"
    )

  # 4. Native merge（唯一模式，2026-08 起删除版本A/B双输出与TTS混音路径）：
  #    对话/背景音由视频模型生成，直接拼接保留每段视频原声。
  _logger.info("[INFO] Native merge: 直接拼接视频并保留原声（语音由视频模型生成）")

  # 解析每个分镜的转场，并生成最终片段 (start,end) 单一时间表。
  native_transitions = [_parse_transition(s.get("clip_transition")) for s in valid_shots]
  clip_windows, audio_duration = _compute_clip_windows(target_durations, native_transitions)
  _logger.info(f"[INFO] Total video duration (after transition overlap): {audio_duration:.1f}s")
  _logger.info(f"[INFO] Clip windows: {clip_windows}")
  _logger.info(f"[INFO] Native transitions: {[(k, round(t, 2)) for k, t in native_transitions]}")

  # 5. Generate SRT subtitles.  In tts_mix mode, follow the real TTS duration
  # instead of stretching text across the whole clip.
  speech_durations = None
  if _AUDIO_MODE == "tts_mix":
    from shared.paths import audio_file
    speech_durations = []
    for _s in valid_shots:
      _ap = audio_file(_s.get("index", 0))
      speech_durations.append(get_media_duration(_ap) if _ap.exists() else None)
  generate_srt(valid_shots, target_durations, SRT_OUT,
               clip_windows=clip_windows, speech_durations=speech_durations)

  # 6. Merge video - native（默认）：直接拼接保留视频原声；
  #    tts_mix（可选）：叠加 step03 TTS 对白轨（视频原声不满意时切换）
  _logger.separator("=", 60)
  _logger.info(f"[INFO] Merge: 转场 + 拼接 + 字幕 + 调色（音频模式: {_AUDIO_MODE}）")
  _logger.separator("=", 60)
  from step04_merge.merge_core import _run_cmd, _find_exe
  FFMPEG = _find_exe("ffmpeg")

  srt_path_abs = SRT_OUT.resolve().as_posix()
  escaped_srt = srt_path_abs.replace(":", "\\:")
  # v6.27：字幕可读性修复——白字黑边（原白字白边亮场景不可读）+ 中文字体
  # （Sans 在 Windows 会回退）+ 字号随分辨率缩放
  style = (
    f"FontName=Microsoft YaHei"
    f"\\,FontSize={SUB_FONT_SIZE}"
    f"\\,PrimaryColour=&H00FFFFFF"
    f"\\,OutlineColour=&H00000000"
    f"\\,Outline=2"
    f"\\,Shadow=1"
  )
  # v6.22：调色配置化（读取 merge_core 的环境变量，替代硬编码）
  from step04_merge.merge_core import CONTRAST, SATURATION, VIGNETTE_ANGLE
  color_filter = f"eq=contrast={CONTRAST}:saturation={SATURATION}"
  if VIGNETTE_ANGLE:
    color_filter += f",vignette=angle={VIGNETTE_ANGLE}"
  # v6.27：filter 顺序修复——调色在前、字幕最后（原顺序 vignette 会压暗
  # 字幕区域、eq 改变字幕白字的饱和度）
  video_filter = (
    f"{color_filter},"
    f"subtitles='{escaped_srt}':force_style='{style}'"
  )

  # tts_mix：为每个镜头找 step03 的 TTS 音频（audio/shot_{idx}.mp3）
  tts_paths = None
  if _AUDIO_MODE == "tts_mix":
    tts_paths = _collect_shot_tts_paths(valid_shots, clips, script)

  # 图外旁白：独立 TTS 轨叠在最终音轨之上（落点由 voice_units 单源给出）
  narration_tracks = _collect_narration_tracks(script, valid_shots, clip_windows)
  if narration_tracks:
    _logger.info(
      "[INFO] 图外旁白 %d 条（独立音轨，原声保留）: %s"
      % (len(narration_tracks),
         "、".join("N%d %.2fs@%.2fs/%.2fs" % (t["index"], t["dur"], t["start"],
                                             t["end"] - t["start"])
                   for t in narration_tracks)))
    if any(t.get("timing_provisional") for t in narration_tracks):
      # 准则 7：原生对白没有发声时间信息（不是独立音轨，探测不到），占用只能按**估时**
      # 避让——如实说明，不宣称保证不重叠。
      _logger.warn(
        "[WARN] 本模式的对白不是独立音轨（原生发声），其开口时间**无法探测**："
        "旁白起点是按**估时**避让的，不保证与原生语音不重叠。"
        "需要精确错峰请在 tts_mix 模式（独立对白轨，时长可实测）下合并。")

  _logger.info("[INFO] Native mode: applying transitions + concat filter")
  filter_complex, v_label, a_label, _, _, n_extra = _build_native_transition_filter(
    clips, native_transitions, video_filter, tts_paths=tts_paths,
    narration_tracks=narration_tracks
  )

  # Build command with multiple inputs
  cmd = [FFMPEG, "-y"]
  for c in clips:
    cmd.extend(["-i", str(c)])
  for tp in [t for t in (tts_paths or []) if t]:
    cmd.extend(["-i", tp])
  # 顺序不变量：旁白输入紧跟在 TTS 输入之后（`_build_native_transition_filter`
  # 按同一顺序编号，两边都由上面的列表派生，不得各写一套）
  for _t in narration_tracks:
    cmd.extend(["-i", str(_t["path"])])
  cmd.extend([
    "-filter_complex", filter_complex,
    "-map", v_label, "-map", a_label,
    "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(_CRF),
    "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
    str(FINAL_OUT),
  ])
  _logger.info(f"[INFO] 编码: preset={VIDEO_PRESET} crf={_CRF} 分辨率={OUT_W}x{OUT_H}"
               f"（MERGE_RESOLUTION 可调；上线 1080x1920 时同步改 BAILIAN_VIDEO_RESOLUTION=1080P）")
  # 2026-09-13 审计修复：先写临时文件，成功后 os.replace——此前直接覆盖
  # final.mp4，ffmpeg 中断会留下损坏文件被误当作可复用产物
  final_tmp = FINAL_OUT.with_name(FINAL_OUT.stem + ".tmp.mp4")
  cmd[-1] = str(final_tmp)
  try:
    subprocess.run(cmd, check=True)
    # Validate the temp file before replacing the previous publishable output.
    duration = _validate_final_output(final_tmp, audio_duration)
    os.replace(final_tmp, FINAL_OUT)
  except Exception:
    if final_tmp.exists():
      try:
        final_tmp.unlink()
      except OSError:
        pass
    raise
  _logger.info(f"[OK] Final video generated: {FINAL_OUT}")

  # 7. Report
  _logger.info("[INFO] Merge Report:")
  _logger.info(f" Audio:       Original video audio (native mode, 语音由视频模型生成)"
               + (f" + {len(narration_tracks)} 条图外旁白独立音轨（原声保留）"
                  if narration_tracks else ""))
  _logger.info(f" Final duration: {duration:.1f}s")
  _logger.info(f" Total video duration: {audio_duration:.1f}s")


if __name__ == "__main__":
  try:
    main()
  except Exception as e:
    _logger.error(f"[ERROR] {e}")
    sys.exit(1)
