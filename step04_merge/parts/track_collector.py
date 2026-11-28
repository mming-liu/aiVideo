# -*- coding: utf-8 -*-
"""S06: `step04_merge.main` 的 track_collector 职责（2026-11-28 拆分）.

父模块保留配置/日志/运行状态与编排；本模块只放纯函数，父模块需要的运行值
通过 `_pv`/`_pc` 在调用点读取，导入期不回导父模块。
"""
from pathlib import Path
import os
from step04_merge.stretch import get_media_duration
_PARENT = "step04_merge.main"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name, *args, **kwargs):
    return _pv(name)(*args, **kwargs)


class _ParentLogger:
    """把日志调用转发到父模块当前 logger（运行中会被 `_ensure_attempt` 替换）."""

    def __getattr__(self, item):
        return getattr(_pv("_logger"), item)


_logger = _ParentLogger()


def _collect_shot_tts_paths(valid_shots, clips, script, resolve=None, plan=None,
                           *, overflow_policy="fail",
                           tolerance_sec=0.15):
  """tts_mix：逐镜对白轨的收集 + **消费侧身份核对**（返回与 `valid_shots` 等长的路径表）.

  三处显式失败（都不静默降级）：音轨超出实际视频时长、缺音轨、音轨与当前文本不符。
  判据必须与**生成器**（`gen_shot_tts`）一致：纯叙述镜不出对白轨——它的文字是字幕
  （旁白由独立旁白轨承担），旧实现按「`narration_segment` 非空」要求音轨，纯叙述镜
  必然被记成缺失 ⇒ tts_mix 整段失败（2026-09-30 复核第 7/8 轮）。

  S05/M1：超窗是否直接失败由 `overflow_policy` 决定——`fail` 在此拒收；其余策略
  由 `audio_fit.plan_audio_fit` 统一计算补齐/加速，随后在时间线上落实（本函数只收
  集路径，不改时长）。
  """
  from step01_script_gen.plan_feasibility import (
    shot_needs_dialogue_track as _needs_dialogue_track)
  from shared.audio_identity import verify_text_signature as _vts
  from step01_script_gen.plan_feasibility import shot_voice_parts as _svp
  _plan = plan if isinstance(plan, dict) and plan else (
    script.get("chapter_outline") if isinstance(script, dict) else None)
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
      # A TTS track longer than the actual clip must not be silently atrim'd.
      if _i < len(clips):
        _clip_dur = get_media_duration(clips[_i])
        _tts_dur = get_media_duration(_tp)
        if _clip_dur > 0 and _tts_dur > _clip_dur + max(0.0, float(tolerance_sec)):
          if str(overflow_policy) == "fail":
            raise RuntimeError(
              f"shot {s.get('index')} TTS 超出实际视频时长: "
              f"tts={_tts_dur:.2f}s clip={_clip_dur:.2f}s。请调整台词/停顿/镜头，"
              "禁止 step04 静默截句。显式允许可设 TTS_MIX_ALLOW_TRIM=1，"
              "或用 MERGE_AUDIO_OVERFLOW_POLICY=pad_video_tail / gentle_atempo。"
            )
          if str(overflow_policy) == "trim":
            _logger.warn(
              "[WARN] shot %s TTS 超窗 %.2fs，按 TTS_MIX_ALLOW_TRIM/"
              "MERGE_AUDIO_OVERFLOW_POLICY=trim **显式**截断（尾音会丢，已记录）"
              % (s.get('index'), _tts_dur - _clip_dur))
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
                              identities=None, only_indices=None, plan=None):
    """图外旁白 → 合并落点表 `[{path, index, start, end, dur, n_shots}]`.

    **落点单源**：`voice_units`（与候选层/终稿层/提交层的生成验收同一判据——
    「从承载节拍的第一镜起、连续消费完这条旁白」）。本函数只做两件事：
    ① 换算成最终时间轴上的秒数；② 核验音轨存在、且**讲得完**。
    """
    from shared.audio_identity import verify_signature
    from step01_script_gen.plan_feasibility import (broadcast_narration_entries,
                                                    layout_voice_entries,
                                                    voice_units)
    from tts.voice_registry import NARRATOR_VOICE
    plan = plan if isinstance(plan, dict) and plan else script.get("chapter_outline")
    plan = plan if isinstance(plan, dict) else {}
    # S22-5：与 step03 同源——开启声音改编时，step04 的旁白文本/签名必须读改编层，
    # 否则 step03 签的是改编文本、step04 拿原文核对，必然判"与当前计划不符"。
    entries = broadcast_narration_entries(plan)
    from shared.narration_voice import narration_role
    _roles = [narration_role(e, script.get("characters") or []) for e in entries]
    if not entries:
        return []
    _pos = {str(s.get("index")): i for i, s in enumerate(valid_shots)}
    _win = {str(valid_shots[_i].get("index")): clip_windows[_i]
            for _i in range(min(len(valid_shots), len(clip_windows)))}
    # 全部声音条目（排版要算上对白的占用）；`_units` 只是旁白那一份（逐条核验用）
    _all_units = voice_units(plan, valid_shots)
    _units = [u for u in _all_units if u["kind"] == "narration"]
    if only_indices is not None:
        # S19-3：按**全章**承载集合裁剪旁白——子集外/跨子集的旁白直接跳过，
        # 否则下面的「没有完整落点」守卫会在 --shots 1 时必炸。
        _full_shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
        _full_units = {int(u["index"]): u for u in voice_units(plan, _full_shots)
                       if u["kind"] == "narration"}
        _keep: set = set()
        for _u in _units:
            _k = int(_u["index"])
            _full_run = ((_full_units.get(_k) or {}).get("shot_indexes")
                         or _u.get("shot_indexes") or [])
            _decision = _narration_subset_decision(_full_run, only_indices)
            if _decision == "keep":
                _keep.add(_k)
            elif _decision == "skip_cross":
                _logger.warn("[WARN] 旁白 N%d 跨出本次 --shots 子集（承载 %s），"
                             "跳过（不截句）" % (_k, sorted(int(i) for i in _full_run)))
            else:
                _logger.info("[INFO] 旁白 N%d 不在本次 --shots 子集内，跳过" % _k)
        _all_units = [u for u in _all_units
                      if u["kind"] != "narration" or int(u["index"]) in _keep]
        _units = [u for u in _units if int(u["index"]) in _keep]
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
            if _pv("_AUDIO_MODE") == "tts_mix" and _run:
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
        if "voice_role" in entries[_k - 1]:
            from shared.audio_identity import read_identity as _read_ident
            if not _ident:
                _ident = _read_ident(_p) or {}
            if _ident.get("voice_role") != _roles[_k - 1]:
                raise RuntimeError("旁白 N%d voice_role 身份不符或缺失，必须重新生成音轨" % _k)
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

def _narration_subset_decision(full_run, only_indices) -> str:
  """旁白在 --shots 子集下的处置：keep / skip_other / skip_cross.

  full_run 是旁白在**全章**的完整承载集合。子集合并时：
    - 与子集无交集 → 属于其他镜头，跳过；
    - 部分相交但未完全包含 → 跨镜旁白，跳过（禁止截句）；
    - 完全落在子集内 → 正常排版混音。
  """
  if only_indices is None:
    return "keep"
  wanted = {int(i) for i in only_indices}
  run = {int(i) for i in (full_run or [])}
  if not run or not (run & wanted):
    return "skip_other"
  if not run <= wanted:
    return "skip_cross"
  return "keep"

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
