"""step04_merge - Video merge entry point"""
import sys
import subprocess
from pathlib import Path
import os

# Ensure imports resolve from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 运行范围与增强参数（--chapter/--book/--book-name 与候选参数）必须在下面的 env
# 读取之前生效：CHAPTER_DIR / FINAL_OUT / SRT_OUT 等都是 import 时求值，
# main() 里再设 env 已经晚了。解析入口在 step04_merge/cli.py（计划 §A4、§15）。
# 非脚本 import 不解析用户 argv；main() 在 options=None 时直接取这里的结果，不二次解析。
_options = None
if __name__ == "__main__":
    from step04_merge.cli import parse_bootstrap as _bootstrap_scope
    _options = _bootstrap_scope()

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
    final_file, subtitles_file, stretched_dir, execution_log_file,
)
CHAPTER_DIR = chapter_dir()
SCRIPT_FILE = script_file()
FINAL_OUT = final_file()
SRT_OUT = subtitles_file()
TEMP_DIR = stretched_dir()

# 候选上下文（计划 §A4）：任一增强参数出现时，产物一律落在本 attempt 内。
# SRT / TEMP / log / final_tmp / 候选 mp4 全部从这里路由——不能只改 FINAL_OUT。
_ATTEMPT = None
if _options is not None and getattr(_options, "enhancement_requested", False):
    from step04_merge.cli import create_attempt_context as _create_attempt
    _ATTEMPT = _create_attempt(_options, chapter=CHAPTER_INDEX)
    FINAL_OUT = _ATTEMPT.candidate_out
    SRT_OUT = _ATTEMPT.srt_path
    TEMP_DIR = _ATTEMPT.work_dir

# 模块级日志（路径单源 shared.paths.execution_log_file：候选模式写 attempt 日志）
from shared.tee_logger import get_logger

# 编码预设（匹配 merge_core.py；单源 shared/config.MERGE_PRESET）
from shared.config import MERGE_PRESET as _CFG_PRESET
VIDEO_PRESET = _CFG_PRESET

_logger = get_logger(execution_log_file("step04", _ATTEMPT, CHAPTER_INDEX), console_level="INFO")

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
from step04_merge.stretch import get_media_duration, has_audio_stream
from step04_merge.srt_gen import build_srt_plan
from step04_merge.timeline import (
    SCHEMA_MERGE_TIMELINE, ClipEntry, MergeTimeline, TimelineAudioTrack, TimelineError,
    overlays_from_motion_plan, resolve_merge_candidates, stable_shot_id,
    transitions_from_legacy, validate_timeline,
)
from step04_merge.backends.ffmpeg import (
    EncodeSettings, RenderError, compile_graph, render,
)
from shared.media_contract import (
    ENDPOINT_FADE_IN, ENDPOINT_FADE_OUT, LEGAL_BOUNDARY_TRANSITIONS,
    LEGAL_ENDPOINT_EFFECTS,
    LEGAL_XFADE_TRANSITIONS, MERGE_FPS,
)
from shared.subtitle_contract import (
    SubtitleContractError, SubtitleStyle, ffmpeg_subtitle_filter, load_font_spec, write_srt,
)
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
    # 端点（首淡入 / 末淡出）：**排在"淡入淡出"之后**，否则长词会被短词先截走。
    # 与显式名 fade_in/fade_out 同一语义（各自带自身时长，不减总长）。
    ("淡入", ENDPOINT_FADE_IN),
    ("淡出", ENDPOINT_FADE_OUT),
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

# 可用的 xfade transition 白名单：**唯一集合**在 shared.media_contract
# （2026-10-03 S 包，§15「转场合法集合」），本模块直接消费 LEGAL_BOUNDARY_TRANSITIONS /
# LEGAL_ENDPOINT_EFFECTS / LEGAL_XFADE_TRANSITIONS，不再维护第二份集合。
# 旧文本关键词归一（下面的 _TRANSITION_MAP）只是**旧输入适配层**；
# 归一后未知 kind 一律**拒收**，不再静默回退 fade。


# 旁白叠加的混音权重（2026-09-30 复核第 7 轮第 3 项）
# `amix:normalize=0` + 显式权重：默认归一化按**权重和**整体衰减，旁白条数一变原声
# 音量就跟着变。这里固定「原声 0.6 / 每条旁白 1.0」——原声**保留**（不被旁白顶掉，
# 这是复核明确要求的），旁白为主。
_NARRATION_BASE_GAIN = 0.6
_NARRATION_GAIN = 1.0
# tts_mix：对白覆盖在原声之上时的原声权重（TTS 轨固定 1.0）
_TTS_BASE_GAIN = 0.6


def _resolve_dialogue_path(index, resolve=None):
    """逐镜对白轨路径：候选 resolver 优先（--audio-candidates），否则旧磁盘单源."""
    if resolve is not None:
        return resolve("dialogue", index)
    from shared.paths import audio_file
    return audio_file(index)


def _resolve_narration_path(index, resolve=None):
    """图外旁白轨路径：候选 resolver 优先，否则旧磁盘单源."""
    if resolve is not None:
        return resolve("narration", index)
    from shared.paths import narration_audio_file
    return narration_audio_file(index)


def _collect_shot_tts_paths(valid_shots, clips, script, resolve=None):
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
    _tp = _resolve_dialogue_path(s.get('index', 0), resolve)
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


def _collect_narration_tracks(script, valid_shots, clip_windows, resolve=None,
                              identities=None):
    """图外旁白 → 合并落点表 `[{path, index, start, end, dur, n_shots}]`.

    **落点单源**：`voice_units`（与候选层/终稿层/提交层的生成验收同一判据——
    「从承载节拍的第一镜起、连续消费完这条旁白」）。本函数只做两件事：
    ① 换算成最终时间轴上的秒数；② 核验音轨存在、且**讲得完**。
    """
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
                    # 占用必须量**实际播放的那条轨**：候选模式下 `audio_file()`
                    # 指的是生产轨，量它会让旁白按旧轨长度避让，而真正播出的是候选轨
                    # （复核实测：候选对白 4s / 旧轨 1s ⇒ 旁白从 1s 起、重叠 3s）。
                    _tp = (_resolve_dialogue_path(int(_sid), resolve)
                           if str(_sid).lstrip("-").isdigit() else None)
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
        _p0 = _resolve_narration_path(_k, resolve)
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
        _p = _resolve_narration_path(_k, resolve)
        if _p is None or not (Path(_p).exists() and Path(_p).stat().st_size > 10240):
            missing.append(_k)
            continue
        _p = Path(_p)
        # **消费侧身份核对**（复核第 8 轮 P1）：文件在 ≠ 内容对。计划重选或 N 编号
        # 变化后，上一稿的旁白音轨会顶着同一个文件名——step03 的缓存检查替代不了这里。
        #
        # 2026-10-03 复核 P1-8：候选来源（--audio-candidates）下，身份以**候选 manifest
        # 的同一记录**为准（占用/播放/核验三者消费同一份）；只有生产轨道才回退旧
        # `.voice` signature——否则合法的新 profile 旁白会被旧口径无条件拒收。
        _ident = dict((identities or {}).get(_k) or {})
        if _ident:
            from shared.audio_identity import sha256_text as _sha_text
            from shared.audio_identity import verify_identity as _verify_ident

            _why = _verify_ident(_p, _ident)
            if not _why:
                _want = str(_ident.get("text_hash") or "")
                _got = _sha_text(_v["text"])
                if not _want:
                    _why = "候选富身份缺少 text_hash（证明不了讲的是这一段）"
                elif _want != _got:
                    _why = ("旁白文本与候选身份不符（text_hash %s ≠ %s）"
                            % (_want[:12], _got[:12]))
        else:
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


_EXPLICIT_KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _parse_transition(text: str, default_dur: float = 0.5):
    """解析 clip_transition 字符串 → (kind, duration_seconds)【旧输入适配层】.

    kind: 'cut'(硬切) | 'fade_in'(首端点淡入) | 'fade_out'(末端点淡出) | xfade 转场名。

    2026-10-03（S 包，§15「转场合法集合」）：
    - 关键词归一表 ``_TRANSITION_MAP`` 保留为**旧输入适配层**（自由文本照旧）；
    - 显式 kind 写法（纯 ASCII 标识符）必须落在
      ``shared.media_contract`` 的合法集合内，**否则拒收**——不再静默回退 fade；
    - fade_in/fade_out 只有端点含义，位置不对由 timeline 层拒收。
    """
    text = (text or "").strip()
    if not text:
        return "cut", 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*[秒s]", text)
    dur = float(m.group(1)) if m else default_dur
    has_transition_word = "转场" in text or m is not None

    kind = None
    # **先认显式合法名**（cut/wipeleft/radial/fade_in/fade_out…）：它们是已经规范化
    # 的 kind，不是"待适配的中文描述"。旧写法把它们一律落到 fade，既改了转场种类
    # 又可能改总时长/丢端点效果（2026-10-03 复核 P2-11）。
    if _EXPLICIT_KIND_RE.match(text) \
            and text in (LEGAL_BOUNDARY_TRANSITIONS | LEGAL_ENDPOINT_EFFECTS):
        kind = text
    if kind is None:
        for kw, xf in _TRANSITION_MAP:
            if kw in text:
                kind = xf
                break
    if kind is None:
        if _EXPLICIT_KIND_RE.match(text) and text not in LEGAL_BOUNDARY_TRANSITIONS \
                and text not in LEGAL_ENDPOINT_EFFECTS:
            raise TimelineError(
                "illegal_transition_kind",
                f"未知转场 kind（拒收，不静默回退 fade）: {text!r}；"
                f"合法集合: {sorted(LEGAL_BOUNDARY_TRANSITIONS | LEGAL_ENDPOINT_EFFECTS)}")
        kind = "fade"
    # 中文描述里的"硬切"仍按 cut；但**显式名**（已经是 kind）不受这层改写影响
    explicit = (kind is not None and kind == text
                and kind in (LEGAL_BOUNDARY_TRANSITIONS | LEGAL_ENDPOINT_EFFECTS))
    if not explicit:
        if "硬切" in text and not has_transition_word:
            kind = "cut"
        elif kind == "cut" and has_transition_word:
            kind = "fade"

    if kind == "cut":
        dur = 0.0
    else:
        # 端点效果（首淡入/末淡出）**有自己的时长**：它不减总长这件事由时间线
        # （`transitions_from_legacy` → `EndpointEffect`）负责，不在这里清成 0
        # ——清成 0 会被时间线按"端点时长必须为正"拒收（2026-10-04 复核 P1-7）。
        dur = max(0.1, min(dur, 1.5))
    return kind, dur


def _compute_clip_windows(durations, transitions):
    """Return per-clip final (start,end) and total duration.【旧签名适配层】

    xfade overlaps make cumulative-duration subtraction ambiguous.  The windows
    below are the single source shared by the ffmpeg filter and subtitles.
    2026-10-03：时间算术迁入 ``step04_merge.timeline``（同一套判据），本函数只做
    旧参数（旧 transitions 列表）到新表示的转换——旧 ``transitions[0]==fade_in`` /
    末尾 ``fade_out`` 迁成**端点效果**，其余位置原样作为边界过渡。

    **新旧差异（有意，§15「转场表示」的 golden 为准）**：旧实现把
    ``transitions[n-1]==("fade_out", d)`` **同时**当边界消费——kind 不在旧
    ``_VALID_XFADE`` 里 ⇒ 静默回退成 ``xfade=fade``（重叠 d 秒），随后再叠一次末端点；
    例：[2.0, 3.0] + 末淡出 0.5 在旧实现里总长 4.5。新契约把末端点与边界分开
    （端点不减总长），同一输入总长 5.0，差异 = 原 tdur（少一次 xfade）。
    这条差异在 ``tools/verify_merge_timeline.py`` 的旧入口对照里显式声明为允许差异。
    """
    from step04_merge.timeline import windows_from_legacy
    return windows_from_legacy(durations, transitions)


# ------------------------------------------------------------------ 时间线与后端装配

def _subtitle_style() -> SubtitleStyle:
    """字幕绘制参数：**显式**字体路径/摘要优先（缺字体/缺字形在编码前拒收）.

    未配置 ``MERGE_SUBTITLE_FONT_FILE`` 时沿用旧合同的 libass 字体名解析；候选/增强
    模式（``require_explicit_font``）下这会被后端拒绝，不静默 fallback。
    """
    font = None
    font_path = os.getenv("MERGE_SUBTITLE_FONT_FILE", "").strip()
    if font_path:
        expected = os.getenv("MERGE_SUBTITLE_FONT_SHA256", "").strip() or None
        font = load_font_spec(font_path, expected)
    return SubtitleStyle(font=font, font_size=SUB_FONT_SIZE)


def _encode_settings(*, require_explicit_font: bool = False) -> EncodeSettings:
    """把当前生效配置折成后端设置（后端不自己读 env）。"""
    return EncodeSettings(
        width=OUT_W, height=OUT_H, crf=str(_CRF), preset=str(VIDEO_PRESET),
        audio_volume=_AUDIO_VOLUME, loudnorm=(_LOUDNORM == "1"),
        tts_base_gain=_TTS_BASE_GAIN, narration_base_gain=_NARRATION_BASE_GAIN,
        narration_gain=_NARRATION_GAIN, subtitle_style=_subtitle_style(),
        require_explicit_font=require_explicit_font,
    )


def _color_filter() -> str:
    """既定调色（配置化；与 merge_core 的环境变量单源）."""
    from step04_merge.merge_core import CONTRAST, SATURATION, VIGNETTE_ANGLE
    color_filter = f"eq=contrast={CONTRAST}:saturation={SATURATION}"
    if VIGNETTE_ANGLE:
        color_filter += f",vignette=angle={VIGNETTE_ANGLE}"
    return color_filter


def _build_timeline(clips, durations, transitions, *, scope, script_revision,
                    clip_kinds=None, shot_indexes=None, audio_mode="native",
                    tts_paths=None, narration_tracks=None, subtitle_plan=None,
                    overlays=(), source=None, fps=MERGE_FPS, clip_has_audio=None,
                    shot_digests=None, tts_identities=None,
                    narration_identities=None) -> MergeTimeline:
    """旧参数（路径表 + 旧 transitions 列表）→ :class:`MergeTimeline`（唯一装配点）."""
    clip_list = [Path(c) for c in clips]
    n = len(clip_list)
    kinds = list(clip_kinds or ["shot"] * n)
    indexes = list(shot_indexes if shot_indexes is not None else range(n))
    boundaries, endpoints, notes = transitions_from_legacy(transitions, n_clips=n, fps=fps)
    for note in notes:
        _logger.warn(f"[WARN] {note}")

    entries: list[ClipEntry] = []
    for i in range(n):
        kind = str(kinds[i])
        index = int(indexes[i])
        digest = None
        if shot_digests:
            digest = shot_digests.get(index, shot_digests.get(str(index)))
        entries.append(ClipEntry(
            shot_index=index, kind=kind,
            # 身份接已有单源：scope + 整稿 revision + 逐镜语义摘要（per_shot_revisions）
            stable_id=stable_shot_id(scope, script_revision, kind, index, digest),
            path=clip_list[i], duration_sec=0.0,
            raw_duration_sec=float(durations[i]), frames=0,
            has_audio=bool(clip_has_audio[i]) if clip_has_audio is not None
            else has_audio_stream(clip_list[i])))

    tracks: list[TimelineAudioTrack] = []
    if audio_mode == "tts_mix":
        for i, path in enumerate(tts_paths or []):
            if not path:
                continue
            tracks.append(TimelineAudioTrack(
                kind="shot_tts", stable_id=f"{entries[i].stable_id}:tts",
                path=Path(path), clip_index=i, gain=_TTS_BASE_GAIN,
                identity=dict((tts_identities or {}).get(str(indexes[i])) or {}) or None))
    for track in (narration_tracks or []):
        tracks.append(TimelineAudioTrack(
            kind="narration", stable_id=f"narration:N{track['index']}",
            path=Path(track["path"]), start_sec=float(track["start"]),
            end_sec=float(track["end"]), gain=_NARRATION_GAIN,
            identity=dict((narration_identities or {}).get(str(track["index"])) or {}) or None))

    return MergeTimeline(
        scope=scope, script_revision=script_revision, width=OUT_W, height=OUT_H, fps=fps,
        clips=tuple(entries), boundary_transitions=boundaries, endpoint_effects=endpoints,
        audio_mode=audio_mode, audio_tracks=tuple(tracks), subtitle_plan=subtitle_plan,
        overlays=tuple(overlays), source=dict(source or {}))


def _build_native_transition_filter(clips, transitions, video_filter,
                                    tts_paths=None, narration_tracks=None):
    """为 native 模式构建带转场的 filter_complex【旧签名适配层，计划 §A4】.

    视频：xfade（交叉溶解，要求各输入已归一化为同尺寸/帧率）
    音频：acrossfade（同步交叉淡化，避免硬切爆音）
    纯「硬切」：concat 直接拼接（不重叠）。
    2026-10-03（S 包）：实现已换成 **MergeTimeline + 单一 FFmpeg 后端**——原来的
    巨型字符串拼接删掉，输入表与 filter 索引同源（见 backends.ffmpeg.build_inputs）；
    非法转场/非法时间不再静默 clamp 或回退 fade，而是拒收。

    Returns:
        (filter_complex, video_label, audio_label, total_duration, clip_windows,
         n_extra_inputs)
        clip_windows 是每个片段在最终时间轴上的真实 (start, end)，字幕直接复用。
        n_extra_inputs: 额外音频输入数（tts_mix 模式，追加在视频输入之后）。
    """
    clip_list = [Path(c) for c in clips]
    durations = [get_media_duration(c) for c in clip_list]
    timeline = _build_timeline(clip_list, durations, transitions, scope="legacy",
                               script_revision="", audio_mode=_AUDIO_MODE,
                               tts_paths=tts_paths, narration_tracks=narration_tracks)
    validated = validate_timeline(timeline)
    inputs, filter_complex, v_label, a_label, _meta = compile_graph(
        validated, _encode_settings(), trailing_filter=video_filter, logger=_logger)
    clip_windows = [(w.start_sec, w.end_sec) for w in validated.windows]
    n_extra = len(inputs) - len(clip_list)
    return (filter_complex, v_label, a_label, validated.total_duration_sec,
            clip_windows, n_extra)


def _resolve_hook_clip(hook, candidates=None):
    """Return hook clip path or fail when the script planned a hook.

    2026-10-03（S 包）：hook 是**独立身份**（index=0 不等于第一正文镜头）。
    给了候选清单时按 ("hook", 0) 取；清单里没有这一单元即拒收，不回退磁盘定位。
    """
    if not hook or not isinstance(hook, dict):
        return None
    if candidates is not None:
        hit = candidates.get(("hook", 0))
        if hit is None:
            raise FileNotFoundError(
                "候选清单缺少 hook 单元（稳定身份 hook/index=0）——不回退磁盘定位；"
                "请补全候选清单或去掉 hook。")
        return hit
    from shared.paths import clip_file
    p = clip_file(0)
    if p.exists() and p.stat().st_size > 20000:
        return p
    raise FileNotFoundError(
        f"脚本包含 hook，但 hook 视频缺失或损坏: {p}。\n"
        "禁止静默跳过；请先完成 step02b 的 hook 生成。"
    )


def _run_identity(script) -> tuple:
  """本次成片的 scope 与 script_revision（cue/overlay/候选同源，不各算各的）."""
  from shared.quality_gate import compute_script_revision
  revision = str(script.get("revision") or "").strip() or compute_script_revision(script)
  scope = _ATTEMPT.scope if _ATTEMPT is not None else f"chapter_{CHAPTER_INDEX:03d}"
  return scope, revision


def _ensure_attempt(options):
  """候选上下文：CLI 顶部已建则复用；API 直接调用时现建并 rebind 路径与日志."""
  global _ATTEMPT, FINAL_OUT, SRT_OUT, TEMP_DIR, _logger
  requested = options is not None and getattr(options, "enhancement_requested", False)
  if not requested:
    return None
  root = Path(options.require_candidate_root()).resolve()
  if _ATTEMPT is not None and _ATTEMPT.package_root == root:
    return _ATTEMPT
  from step04_merge.cli import create_attempt_context
  _ATTEMPT = create_attempt_context(options, chapter=CHAPTER_INDEX)
  FINAL_OUT = _ATTEMPT.candidate_out
  SRT_OUT = _ATTEMPT.srt_path
  TEMP_DIR = _ATTEMPT.work_dir
  _logger = get_logger(execution_log_file("step04", _ATTEMPT, CHAPTER_INDEX), console_level="INFO")
  return _ATTEMPT


def _record_failure(attempt, exc) -> None:
  """失败留档（阶段/代码可区分）。旧 accepted 不动——提交是最后一步。"""
  try:
    from shared import enhancement_io as eio
    eio.append_event(attempt.root, "failed", before={"state": "running"},
                     after={"state": "failed"},
                     code=str(getattr(exc, "code", type(exc).__name__)),
                     stage=str(getattr(exc, "stage", "merge")),
                     message=str(exc)[:400])
  except Exception as nested:  # 留档失败也要说出来，不静默
    _logger.warn(f"[WARN] 失败事件留档失败（本行即证据缺失的如实标注）: {nested}")


def _commit_candidate(attempt, timeline, result, options, subtitle_plan, srt_path,
                      card_manifest=None) -> dict:
  """候选提交：冻结输入 → 不可变 manifest（一次写成）→ accepted 两阶段替换 + 读回.

  `card_manifest`：本 attempt 已落盘的**文字卡清单路径**（相对 attempt 根）。它进
  `seal_manifest()` 的**唯一**出口——复核 2026-10-05 P1：此前在渲染**之前**去读尚未
  创建的 manifest、失败只告警，于是"清单文件写成功"与"交付证据已登记"是两件事，
  accepted 读回只有视频与解码证据、没有卡片清单及其摘要。清单写成功不等于证据已登记。
  """
  from shared import enhancement_io as eio
  timeline_dict = timeline.to_dict()
  input_key = eio.canonical_json_digest(timeline_dict)
  eio.freeze_inputs(attempt.root, {
    "schema_version": SCHEMA_MERGE_TIMELINE,
    "scope": timeline.scope,
    "script_revision": timeline.script_revision,
    "input_key": input_key,
    "timeline": timeline_dict,
    "options": {"motion_plan": str(options.motion_plan) if options.motion_plan else None,
                "subtitle_timings": str(options.subtitle_timings) if options.subtitle_timings else None,
                "video_candidates": str(options.video_candidates) if options.video_candidates else None,
                "audio_candidates": str(options.audio_candidates) if options.audio_candidates else None,
                "screen_card_plan": str(options.screen_card_plan) if getattr(options, "screen_card_plan", None) else None},
  })
  eio.append_event(attempt.root, "rendered", before={"state": "running"},
                   after={"state": "rendered"}, attempt_id=attempt.attempt_id,
                   input_key=input_key)
  artifacts = [eio.artifact_record(
    result.path, role="candidate_video",
    media={"duration_sec": result.duration_sec, "width": result.width,
           "height": result.height, "fps": result.fps, "has_audio": result.has_audio})]
  if subtitle_plan is not None and subtitle_plan.cues:
    artifacts.append(eio.artifact_record(Path(srt_path), role="subtitles"))
  evidence_path = attempt.outputs_dir / "media_decode_evidence.json"
  if evidence_path.is_file():
    artifacts.append(eio.artifact_record(evidence_path, role="media_decode_evidence"))
  # **卡片清单与它的资产**是交付证据的一部分：登记进 artifacts（带摘要），
  # accepted 读回据此能指出"成片里那张卡的证据在哪"。
  if card_manifest:
    from pathlib import Path as _P
    _cm = _P(card_manifest)
    if not _cm.is_file():
      raise RenderError("card_manifest_missing", "seal",
                        "文字卡清单不存在，无法登记进候选 manifest：%s" % _cm,
                        exit_code=2)
    artifacts.append(eio.artifact_record(_cm, role="screen_card_plan"))
    _cm_dir = _cm.parent
    for _seg in sorted(_cm_dir.rglob("*.png")):
      artifacts.append(eio.artifact_record(_seg, role="screen_card_asset"))
  checks = [
    {"name": "timeline_validated", "status": "pass",
     "detail": {"clips": len(timeline.clips), "total_duration_sec": timeline.total_duration_sec}},
    {"name": "media_decoded", "status": "pass"},
    {"name": "duration_within_tolerance", "status": "pass",
     "detail": {"actual": result.duration_sec, "expected": result.expected_duration_sec}},
    {"name": "audio_present", "status": "pass"},
    {"name": "subtitle_cue_single_source", "status": "pass" if subtitle_plan else "n/a"},
    {"name": "screen_card_evidence_registered",
     "status": "pass" if card_manifest else "n/a",
     "detail": {"path": str(card_manifest) if card_manifest else None}},
  ]
  declarations = dict((timeline.source or {}).get("source_declarations") or {})
  manifest = eio.seal_manifest(attempt.root, {
    "kind": "merge",
    "scope": timeline.scope,
    "script_revision": timeline.script_revision,
    "input_key": input_key,
    "ffmpeg_version": result.ffmpeg_version,
    # 来源**声明**（B 逐字复制，绝不由检测倒推）：本产物自身声明的时间区间与内部切点
    "detector_parameters": _detector_parameters(timeline),
    "allowed_intervals": list(declarations.get("allowed_intervals") or []),
    "declared_internal_cuts": list(declarations.get("declared_internal_cuts") or []),
    "prohibited_kinds": list(declarations.get("prohibited_kinds") or []),
    "render": {"stages": list(result.stages), "elapsed_sec": result.elapsed_sec,
               "expected_duration_sec": result.expected_duration_sec},
    "artifacts": artifacts,
    "checks": checks,
  })
  accepted = eio.commit_accepted(attempt.package_root, attempt_id=attempt.attempt_id,
                                 manifest_path=attempt.manifest_path,
                                 manifest_sha256=manifest["manifest_sha256"],
                                 input_key=input_key)
  eio.append_event(attempt.root, "accepted", before={"state": "rendered"},
                   after={"state": "accepted"}, attempt_id=attempt.attempt_id,
                   manifest_sha256=manifest["manifest_sha256"])
  return accepted


def _dump_card_manifest(manifest, attempt):
    """把 `screen_card_plan.v1` 落到本次 attempt（**不碰正式产物**）→ 路径；失败抛.

    ⚠️ **写失败必须让候选失败**（工作包 3 第 6 条 / 复核 2026-10-05 P1）：清单是卡片
    的**交付证据**——它记录了每页的正文区间、时窗、资产摘要与消费关系。写不进去却继续
    走，交付里就会**声称证据完整而实际上没有**，事后无法解释成片里那张卡是哪一页、
    覆盖哪几帧。旧实现只告警，等于把"证据缺失"当成了"没关系"。

    落点是路径单源 `shared.paths.screen_card_plan_file`；**先写临时文件再替换**，
    避免半份清单留在盘上。
    """
    from shared.paths import screen_card_plan_file
    from shared.json_io import (commit_json_swap, discard_json_swap,
                                prepare_json_swap)
    _p = screen_card_plan_file(getattr(attempt, "root", None),
                              getattr(attempt, "experiment_id", None))
    _p.parent.mkdir(parents=True, exist_ok=True)
    _tmp = prepare_json_swap(_p, manifest, ensure_ascii=False, indent=1)
    commit_json_swap(_p, _tmp)
    return str(_p)


def _source_declarations(timeline, script, shots) -> dict:
    """来源**声明**的时间区间与内部切点（B 逐字复制；**不**由检测倒推）.

    - ``allowed_intervals``：本产物自己声明的合法非常规区间——端点淡入/淡出的黑场段
      （``kind="black"``，来自 timeline 的端点效果）与边界过渡重叠段
      （``kind="transition"``，来自边界表示）；每条带 ``source_locator``（RFC6901）。
    - ``declared_internal_cuts``：step01 的时间语义单源（``shot["temporal_plan"]["events"]``）
      声明的镜内切点；零长区间（start==end）表示一个切点。
    - ``prohibited_kinds``：来源**没有**声明禁用类目时为空表——不猜、不倒推。
    """
    windows = timeline.windows
    allowed: list = []
    for i, effect in enumerate(timeline.endpoint_effects):
        window = windows[int(effect.clip_index)]
        if effect.kind == ENDPOINT_FADE_IN:
            start, end = window.start_sec, window.start_sec + effect.duration_sec
        else:
            start, end = window.end_sec - effect.duration_sec, window.end_sec
        allowed.append({"kind": "black", "start_sec": round(start, 6),
                        "end_sec": round(end, 6),
                        "source_locator": f"/endpoint_effects/{i}"})
    for i, boundary in enumerate(timeline.boundary_transitions):
        if float(boundary.duration_sec) <= 0:
            continue
        window = windows[int(boundary.index)]
        allowed.append({"kind": "transition", "start_sec": round(window.start_sec, 6),
                        "end_sec": round(window.start_sec + float(boundary.duration_sec), 6),
                        "source_locator": f"/boundary_transitions/{i}"})
    cuts: list = []
    for k, shot in enumerate(shots or []):
        if not isinstance(shot, dict):
            continue
        plan = shot.get("temporal_plan")
        events = plan.get("events") if isinstance(plan, dict) else None
        if not events:
            continue
        index = int(shot.get("index", k))
        hit = timeline.clip_for_shot(index)
        if hit is None:
            continue
        window = windows[timeline.clips.index(hit)]
        for j, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            try:
                at = float(event.get("start_sec"))
            except (TypeError, ValueError):
                continue
            absolute = round(window.start_sec + at, 6)
            cuts.append({"kind": "cut", "shot_index": index, "start_sec": absolute,
                         "end_sec": absolute,
                         "source_locator": f"/shots/{k}/temporal_plan/events/{j}"})
    return {"allowed_intervals": allowed, "declared_internal_cuts": cuts,
            "prohibited_kinds": []}


def _detector_parameters(timeline) -> dict:
    """给 B 的检测器参数（**来源声明**的一部分，B 原样使用）.

    ffmpeg blackdetect 的默认 ``min_duration`` 是 2.0s——端点淡入/淡出（常见 0.5s）
    与边界过渡在默认值下**检不出**（B 不伪造事件，但也看不到）。这里按本产物自己
    声明的最短黑场区间取一半（不低于一帧），让声明的区间在 B 侧可判读。
    """
    fps = int(timeline.fps or MERGE_FPS)
    floor = 1.0 / fps
    declared = [float(e.duration_sec) for e in timeline.endpoint_effects
                if float(e.duration_sec) > 0]
    if declared:
        value = max(floor, round(min(declared) / 2.0, 3))
    else:
        value = max(floor, 0.2)
    return {"black": {"min_duration_sec": value}}


def _validate_candidate_path(attempt, clips, audio_paths) -> Path:
  """候选 mp4 的路径守卫：必须在实验根内、不与输入/正式产物冲突."""
  from shared import enhancement_io as eio
  inputs = tuple(Path(c) for c in clips) + tuple(Path(p) for p in audio_paths if p)
  return eio.assert_candidate_path(attempt.candidate_out,
                                   experiment_root=attempt.package_root,
                                   inputs=inputs,
                                   formal=eio.production_paths(CHAPTER_INDEX),
                                   field_path="candidate_out")


def _load_validated_timings(options, audio_mode):
  """--subtitle-timings（C 包校验后的对齐结果）：只在 tts_mix 独立对白轨下受支持."""
  if options is None or options.subtitle_timings is None:
    return None
  if audio_mode != "tts_mix":
    _logger.warn("[WARN] --subtitle-timings 只在 tts_mix 独立对白轨下受支持"
                 "（native 对白/图外旁白不改字幕产品语义）；本次不消费该候选")
    return None
  path = Path(options.subtitle_timings)
  if not path.is_file():
    raise TimelineError("subtitle_timings_missing", f"对齐候选不存在: {path}", path=str(path))
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except Exception as exc:
    raise TimelineError("subtitle_timings_unparsable",
                        f"对齐候选解析失败: {path}: {exc}", path=str(path)) from exc


def _shot_audio_hashes(valid_shots, resolve) -> dict:
  """逐镜对白轨的实际摘要（对齐候选的音频身份核对用；只在有候选时计算）."""
  from shared import enhancement_io as eio
  hashes = {}
  for shot in valid_shots:
    index = shot.get("index", 0)
    path = _resolve_dialogue_path(index, resolve)
    if path is not None and Path(path).is_file():
      hashes[str(index)] = eio.sha256_file(Path(path))
  return hashes


def main(options=None):
  """step04 入口。``options=None`` 时取模块顶部已解析的 ``_options``（不二次解析）."""
  opts = options if options is not None else _options
  attempt = _ensure_attempt(opts)
  lock = None
  if attempt is not None:
    from shared.enhancement_io import AttemptLock
    lock = AttemptLock(attempt.root / ".lock", purpose="step04-merge").acquire()
  try:
    return _run_merge(opts, attempt)
  except Exception as exc:
    if attempt is not None:
      _record_failure(attempt, exc)
    raise
  finally:
    if lock is not None:
      lock.release()


def _run_merge(options, attempt):
  _logger.separator("=", 60)
  _logger.info(" Step 4: Video Merge (Stretch + Concat + Subtitle + Color)")
  _logger.info(" Output: final.mp4 (native, 语音/背景音由视频模型生成)")
  if attempt is not None:
    _logger.info(f"[CANDIDATE] 实验={attempt.experiment_id} attempt={attempt.attempt_id}")
    _logger.info(f"[CANDIDATE] 候选输出={attempt.candidate_out}")
    _logger.info(f"[CANDIDATE] SRT/TEMP/日志均在 attempt 内: {attempt.root}")
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

  # 同源身份 + 候选清单（按稳定身份取片/取音轨，不按镜号猜路径）
  scope, script_revision = _run_identity(script)
  # 逐镜身份摘要走**已有单源**（shared.quality_gate.per_shot_revisions / shot_semantic_digest）
  from shared.quality_gate import per_shot_revisions as _per_shot_revisions
  shot_digests = _per_shot_revisions(script)
  _logger.info(f"[INFO] scope={scope} script_revision={script_revision[:12]} "
               f"shot_digests={len(shot_digests)}")
  # 候选输入的唯一解析入口是 F 的 shared.candidate_inputs（不在本包另做路径解析）
  candidates = None
  if options is not None and (options.video_candidates is not None
                              or options.audio_candidates is not None):
    if options.audio_candidates is not None and options.video_candidates is None:
      raise TimelineError(
        "candidate_anchor_missing",
        "给 --audio-candidates 时必须同时给 --video-candidates"
        "（F 的 resolver 以视频 manifest 为身份锚，两侧 scope/revision 必须一致）")
    # 期望身份必须**显式声明**（F 的准入不猜"这是哪一章"）：取自本次生效配置
    from shared.run_scope import current_scope_identity
    candidates = resolve_merge_candidates(
      options.video_candidates, options.audio_candidates, script_revision=script_revision,
      expect_scope=current_scope_identity(),
      context=attempt, experiment_root=(attempt.package_root if attempt else None))
    _logger.info(
      f"[INFO] 候选输入: 视频 {len(candidates.video_map)} / 对白 {len(candidates.dialogue)} / "
      f"旁白 {len(candidates.narration)}（video={options.video_candidates}, "
      f"audio={options.audio_candidates}；富身份已核）")
  video_map = candidates.video_map if candidates is not None else None
  audio_resolve = None
  if candidates is not None and (candidates.dialogue or candidates.narration):
    def audio_resolve(kind, index, _c=candidates):
      table = _c.dialogue if kind == "dialogue" else _c.narration
      return table.get(int(index))

  # 1.5. Check for hook video
  # Hook视频位置: clips/clip_0.mp4（候选模式下由 --video-candidates 给稳定身份）
  hook = script.get("hook")
  hook_clip = _resolve_hook_clip(hook, candidates=video_map)
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
    if video_map is not None:
      clip_path = video_map.get(("shot", int(idx)))
      if clip_path is None:
        raise FileNotFoundError(
          f"候选清单缺少 shot {idx} 的视频单元（按稳定身份取片，不回退磁盘定位）"
        )
    elif CLIP_SOURCE == "vimax":
      # vimax 用 shots/{idx}/video.mp4
      clip_path = shot_video_file(idx)
    else:
      # 非 vimax（agens-clips 等）用扁平 clip_{idx}.mp4（code-review-2026-08-06 #10）
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
  clip_audio_flags = [has_audio_stream(c) for c in clips]
  actual_audio_present = all(clip_audio_flags)
  # 图外旁白是正交的一维（复核第 7 轮）：不改 `effective_merge_mode`，只在最终
  # 音轨上叠加独立 TTS 轨——角色原声保留（不许用「切 tts_mix」绕过）。
  from step01_script_gen.plan_feasibility import plan_narration_entries as _pne
  from shared.audio_policy import require_narration_contract as _require_narr
  _plan_for_policy = script.get("chapter_outline")
  if not isinstance(_plan_for_policy, dict):
    _plan_for_policy = {}
  # **按计划自称的模式**核声音合同（§5）：新模式出现图外旁白 ⇒ **合同冲突、返回规划者**，
  # 不静默清空、不伪造一条旁白音轨冒充合法无旁白。缺记录不能通过软门放行。
  _narr_contract = _require_narr(_plan_for_policy)
  _narr_ok = True
  _narr_for_policy = _pne(_plan_for_policy)
  _policy = resolve_audio_policy(actual_audio_present=actual_audio_present,
                                 narration_present=bool(_narr_for_policy))
  global _AUDIO_MODE
  _AUDIO_MODE = _policy.effective_merge_mode
  _logger.info(
    f"[AUDIO-POLICY] mode={_narr_contract['mode']} "
    f"model={_policy.video_model or 'unknown'} "
    f"native_supported={_policy.model_supports_audio} actual_clip_audio={actual_audio_present} "
    f"effective={_AUDIO_MODE} narration_tts={_policy.narration_tts_required}"
    f"（旁白 {len(_narr_for_policy)} 条） reason={_policy.reason}"
    f" contract={_narr_contract['reason']}"
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

  # 4. 时间线：显式 shot_index 排序 + 边界/端点分离（唯一时间算术在 timeline.py）
  _logger.info("[INFO] Merge: 显式时间线（clips → 转场 → 音频 → overlay → 字幕）")
  native_transitions = [_parse_transition(s.get("clip_transition")) for s in valid_shots]
  clip_kinds = ["hook" if (hook_clip is not None and i == 0) else "shot"
                for i in range(len(clips))]
  shot_indexes = [int(s.get("index", i)) for i, s in enumerate(valid_shots)]
  provisional = validate_timeline(_build_timeline(
    clips, target_durations, native_transitions, scope=scope, script_revision=script_revision,
    clip_kinds=clip_kinds, shot_indexes=shot_indexes, audio_mode=_AUDIO_MODE,
    clip_has_audio=clip_audio_flags, shot_digests=shot_digests))
  clip_windows = [(w.start_sec, w.end_sec) for w in provisional.windows]
  audio_duration = provisional.total_duration_sec
  _logger.info(f"[INFO] Total video duration (after transition overlap): {audio_duration:.2f}s")
  _logger.info(f"[INFO] Clip windows: {clip_windows}")
  _logger.info(f"[INFO] Native transitions: {[(k, round(t, 2)) for k, t in native_transitions]}")

  # 5. 字幕：cue 唯一源（旧口径或 C 包合法对齐候选）；SRT 与字幕滤镜消费同一份列表
  speech_durations = None
  if _AUDIO_MODE == "tts_mix":
    speech_durations = []
    for _s in valid_shots:
      _ap = _resolve_dialogue_path(_s.get("index", 0), audio_resolve)
      speech_durations.append(get_media_duration(_ap) if _ap and Path(_ap).exists() else None)
  validated_timings = _load_validated_timings(options, _AUDIO_MODE)
  subtitle_plan = build_srt_plan(
    valid_shots, target_durations, clip_windows=clip_windows,
    speech_durations=speech_durations, validated_timings=validated_timings,
    script_revision=script_revision,
    shot_audio_sha256=(_shot_audio_hashes(valid_shots, audio_resolve)
                       if validated_timings is not None else None))
  write_srt(subtitle_plan, SRT_OUT)
  _logger.info(f"[OK] SRT generated: {SRT_OUT} ({len(subtitle_plan.cues)} subtitles, "
               f"source={subtitle_plan.source})")
  if subtitle_plan.fallbacks:
    _logger.warn(f"[WARN] 对齐候选逐镜降级 {len(subtitle_plan.fallbacks)} 处: "
                 f"{subtitle_plan.fallbacks[:4]}")

  # 6. Merge video - native（默认）：直接拼接保留视频原声；
  #    tts_mix（可选）：叠加 step03 TTS 对白轨（视频原声不满意时切换）
  _logger.separator("=", 60)
  _logger.info(f"[INFO] Merge: 转场 + 拼接 + 字幕 + 调色（音频模式: {_AUDIO_MODE}）")
  _logger.separator("=", 60)

  # tts_mix：为每个镜头找 step03 的 TTS 音频（audio/shot_{idx}.mp3 或候选清单）
  tts_paths = None
  tts_identities = {}
  if _AUDIO_MODE == "tts_mix":
    tts_paths = _collect_shot_tts_paths(valid_shots, clips, script, resolve=audio_resolve)
    if candidates is not None:
      tts_identities = {str(_s.get("index", 0)):
                        dict(candidates.dialogue_identity.get(int(_s.get("index", 0))) or {})
                        for _s in valid_shots}

  # 图外旁白：独立 TTS 轨叠在最终音轨之上（落点由 voice_units 单源给出）
  narration_tracks = _collect_narration_tracks(
      script, valid_shots, clip_windows, resolve=audio_resolve,
      identities=(candidates.narration_identity if candidates is not None else None))
  narration_identities = {}
  if narration_tracks and candidates is not None:
    narration_identities = {str(t["index"]): dict(
      candidates.narration_identity.get(int(t["index"])) or {}) for t in narration_tracks}
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

  # 6.5 动效叠加（--motion-plan）：局部时间 → 镜头窗内绝对时间；越界/重复/跨 revision 拒收
  overlays = ()
  if options is not None and options.motion_plan is not None:
    _motion = json.loads(Path(options.motion_plan).read_text(encoding="utf-8"))
    overlays = overlays_from_motion_plan(_motion, scope=scope, script_revision=script_revision,
                                         clips=provisional.clips, windows=provisional.windows)
    _logger.info(f"[INFO] 动效 overlay {len(overlays)} 条（shot 局部时间已换算为绝对时间）")

  # 6.6 画面文字卡（--screen-card-plan / 从当前稿生产）：清单校验 → 叠加条目
  #
  # 与动效**分账**：两个 schema 各自解析，同镜冲突由 `segment_overlays` 明确拒收
  # （不静默覆盖）。`screen_hybrid` 稿**漏传清单不许静默合成无卡视频**——卡片缺失就
  # 是内容没表达出来，不是"效果可选"。
  from step04_merge.screen_cards import ScreenCardError, screen_card_overlays
  try:
    _card_entries, _card_manifest = screen_card_overlays(
        script, options, provisional, attempt=attempt, scope=scope,
        script_revision=script_revision, narration_contract_ok=_narr_ok)
  except ScreenCardError as _e:
    # **候选失败，正式产物不换**（§7「绘制/FFmpeg失败 → 保留诊断，候选失败」）。
    # 逐条落进诊断，别只留一个 code。
    _logger.error("[ERROR] 文字卡链路失败（%s）：%s", _e.code, _e.reason)
    raise RenderError(_e.code, "cards", _e.reason, exit_code=2) from _e
  overlays = tuple(overlays) + tuple(_card_entries)
  if _card_manifest is not None:
    _logger.info("[INFO] 文字卡片段 %d 条（profile %s）"
                 % (len(_card_manifest.get("segments") or []),
                    (_card_manifest.get("profile") or {}).get("profile_digest", "")[:12]))
    # **清单写不进候选 = 交付证据缺失 ⇒ 候选失败**（不再只告警）。写成同一条失败出口，
    # 与卡片链路的其它拒收一样：正式产物不动、诊断留得住。
    try:
      _card_manifest_path = _dump_card_manifest(_card_manifest, attempt)
    except (OSError, ValueError, TypeError) as _exc:
      _logger.error("[ERROR] 文字卡清单留档失败（交付证据缺失）：%s", _exc)
      raise RenderError("card_manifest_write_failed", "cards",
                        "文字卡清单留档失败（卡片是交付证据，缺它就无法解释成片里那张卡）：%s"
                        % _exc, exit_code=2) from _exc
    # ⚠️ **不要在这里改 manifest**（复核 2026-10-05 P1）：渲染**之前** manifest 还没被
    # `seal_manifest()` 创建，读它必然失败、只告警——于是"清单写成功"与"交付证据已登记"
    # 成了两件事。清单由 `_commit_candidate()` 在**唯一封存出口**登记（带摘要）。
  else:
    _card_manifest_path = None

  # 7. 渲染：唯一 FFmpeg 后端（编译 → 编码 → 验收 → 发布），失败按阶段区分
  timeline = validate_timeline(_build_timeline(
    clips, target_durations, native_transitions, scope=scope, script_revision=script_revision,
    clip_kinds=clip_kinds, shot_indexes=shot_indexes, audio_mode=_AUDIO_MODE,
    tts_paths=tts_paths, narration_tracks=narration_tracks, subtitle_plan=subtitle_plan,
    overlays=overlays, clip_has_audio=clip_audio_flags, shot_digests=shot_digests,
    tts_identities=tts_identities, narration_identities=narration_identities,
    source={"clip_source": CLIP_SOURCE,
            "video_candidates": str(options.video_candidates) if options and options.video_candidates else None,
            "audio_candidates": str(options.audio_candidates) if options and options.audio_candidates else None,
            "motion_plan": str(options.motion_plan) if options and options.motion_plan else None}))
  # 来源声明基于**已验证**的时间线（窗口/端点/边界都在规范化之后），供 B 逐字复制
  from dataclasses import replace as _replace
  timeline = _replace(timeline, source={
      **(timeline.source or {}),
      "source_declarations": _source_declarations(timeline, script, shots)})
  if attempt is not None:
    _validate_candidate_path(attempt, clips,
                             [t.path for t in timeline.audio_tracks])
  settings = _encode_settings(require_explicit_font=(attempt is not None))
  subtitle_filter = ""
  if subtitle_plan.cues:
    try:
      subtitle_filter = ffmpeg_subtitle_filter(SRT_OUT, subtitle_plan, settings.subtitle_style)
    except SubtitleContractError as exc:
      raise RenderError(exc.code, "compile", str(exc), exit_code=2) from exc
  _logger.info(f"[INFO] 编码: preset={VIDEO_PRESET} crf={_CRF} 分辨率={OUT_W}x{OUT_H}"
               f"（MERGE_RESOLUTION 可调；上线 1080x1920 时同步改 BAILIAN_VIDEO_RESOLUTION=1080P）")
  result = render(timeline, FINAL_OUT, TEMP_DIR, settings=settings,
                  color_filter=_color_filter(), subtitle_filter=subtitle_filter,
                  logger=_logger, srt_path=SRT_OUT,
                  evidence_dir=(attempt.outputs_dir if attempt is not None else None))
  duration = result.duration_sec
  _logger.info(f"[OK] Final video generated: {FINAL_OUT}")
  _logger.info(f"[INFO] ffmpeg={result.ffmpeg_version} sha256={result.sha256[:12]} "
               f"实测 {duration:.2f}s {result.width}x{result.height}@{result.fps:g} "
               f"音频={result.has_audio} 耗时={result.elapsed_sec:.1f}s "
               f"阶段={'→'.join(result.stages)}")

  # 8. 候选提交（只在增强模式）：冻结输入 → manifest → accepted 替换 + 读回
  if attempt is not None:
    accepted = _commit_candidate(attempt, timeline, result, options, subtitle_plan, SRT_OUT,
                               card_manifest=_card_manifest_path)
    _logger.info(f"[CANDIDATE] accepted: attempt={accepted['attempt_id']} "
                 f"manifest={Path(accepted['manifest_path']).name} input_key={accepted['input_key'][:12]}")

  # 9. Report
  _logger.info("[INFO] Merge Report:")
  _logger.info(f" Audio:       Original video audio (native mode, 语音由视频模型生成)"
               + (f" + {len(narration_tracks)} 条图外旁白独立音轨（原声保留）"
                  if narration_tracks else ""))
  _logger.info(f" Final duration: {duration:.2f}s")
  _logger.info(f" Total video duration: {audio_duration:.2f}s")


if __name__ == "__main__":
  try:
    main()
  except Exception as e:
    _logger.error(f"[ERROR] {e}")
    _candidate_run = _options is not None and getattr(_options, "enhancement_requested", False)
    _exit_code = getattr(e, "exit_code", None)
    sys.exit(int(_exit_code) if _candidate_run and _exit_code else 1)
