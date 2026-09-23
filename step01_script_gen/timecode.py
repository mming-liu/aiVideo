"""step01_script_gen.timecode - 时间轴计算模块（v6.1 增强版）

功能：
- _compute_timecodes(): 根据文本长度计算每个镜头的 timecode
- _estimate_duration(): 优化的时长估算（考虑标点符号、句子长短、动态语速）
- 新增：keyframe_sample_count / movement_speed_multiplier / blank_hold_duration
"""
from __future__ import annotations

import sys
import re
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from shared import motion_segments as _ms
from shared.model_caps import active_video_duration_cap, active_video_model
from shared.paths import chapter_dir
from shared.tee_logger import get_logger

# v6.43（08-23 用户规则）：用项目 logger 统一记录，不再 print 散落
_logger = None
def _log():
    global _logger
    if _logger is None:
        _logger = get_logger(chapter_dir() / "step01.log", console_level="INFO")
    return _logger

# 语速配置（字符/秒）
SPEECH_RATE = 3.5
PAUSE_PER_SHOT = 1.5
MIN_SHOT_DURATION = 3.0
# 无对白旁白镜固定时长（投影评分与正式 timecode 同源）
NARRATION_FIXED_DURATION = 4.0
# 单镜硬上限（**项目编辑规则**，与视频模型能力取严：`min(model_cap, 本值)`）。
#
# v6.93（用户决定，2026-09-22）：8.0 → **15.0**。
# 依据：当前视频模型（`wan2.7-t2v-*`）的项目注册 cap 就是 15s，8s 是 2026-08-23 的
# 编辑规则。实测本章台词需求 9.8 / 12.5 / 14.7 / 20.2s——放宽后只剩 20.2 那条需要
# 拆镜，且 89 字那句从「切 4 镜」降到「切 2 镜」，成片节奏实质改善。
# 仍与模型取严：换成 cap 更小的模型（如 Seedance 1.0 = 12s）时自动跟随。
# 钩子时长另算（见 _compute_timecodes hook 段）。
MAX_SHOT_DURATION = 15.0
# 正片分镜总时长硬下限（08-23 用户规则）：不含钩子 ≥ 60s。
# 实际拉伸动作在 constraints/runtime_extend.py（W6，目标 65s 留裕度），
# 此处仅用于日志告警。钩子时长另算，不计入该下限。
MIN_TOTAL_SHOTS_DURATION = 60.0


def active_shot_cap() -> float:
    """**生效单镜上限** = `min(视频模型能力, 项目 MAX_SHOT_DURATION)`——全仓唯一源.

    v6.97（2026-09-22）：此前有 7 处各算各的，其中 3 处**写死 8.0**（把项目值从 8
    改成 15 时漏改）。最要命的是 `duration_budget._SHOT_CAP`——它按 8s 报
    `Severity.HARD`「必须拆镜」，把 8-12s 的**合法**镜头判成超载，顶起 `_has_hard`。

    模型参数限制必须跟着模型走（CLAUDE.md），所以只留这一份实现。
    """
    try:
        cap = float(active_video_duration_cap())
    except (TypeError, ValueError):
        cap = 0.0
    return MAX_SHOT_DURATION if cap <= 0 else min(cap, MAX_SHOT_DURATION)


def _estimate_duration(text: str) -> float:
    """优化后的时长估算函数（v6.1 新增）

    策略：
    1. 剔除标点符号，只计算有效汉字/单词
    2. 动态语速：短句慢读，长句快读（模拟人类呼吸）
    3. 增加停顿缓冲（根据标点符号数量）
    """
    if not text:
        return MIN_SHOT_DURATION

    # 1. 剔除标点符号，只计算有效汉字
    clean_text = re.sub(r'[^\w一-鿿]', '', text)
    char_count = len(clean_text)

    if char_count == 0:
        return MIN_SHOT_DURATION

    # 2. 动态语速：短句慢读，长句快读
    base_rate = 4.0  # 基础语速
    if char_count < 10:
        base_rate = 3.0  # 短句慢读
    elif char_count > 50:
        base_rate = 4.5  # 长句快读

    duration = char_count / base_rate

    # 3. 增加停顿缓冲（根据标点符号）
    pauses = text.count('，') * 0.3 + text.count('。') * 0.6 + text.count('！') * 0.5 + text.count('？') * 0.5
    duration = duration + pauses

    # 4. 确保最小时长
    return max(duration, MIN_SHOT_DURATION)

# variation_type → 运镜速度倍率 / 关键帧数量
_SPEED_MAP = {"small": 1.0, "medium": 0.5, "large": 0.3}
_KEYFRAME_MAP = {"small": 1, "medium": 2, "large": 3}


def _has_dialogue(text: str) -> bool:
    """检测文本是否包含对话（引号或全角冒号）。判定逻辑统一在 shared/dialogue.py。"""
    from shared.dialogue import has_dialogue
    return has_dialogue(text)


# v6.43（08-25）：超 cap 镜头自动拆镜的切点查找
# 优先级：句号 > 问号/感叹号 > 省略号 > 逗号（中点附近）> 切中字（中点）
_SPLIT_DELIMS_PRIORITY = ("。", "？", "！", "…", "，")


def _rewrite_motion_time(atom: str, new_a: float, new_b: float) -> str:
    """把一个 motion 原子段重写到本地时间轴（拆镜用）.

    v6.76：剥标签与时间格式化统一走 `shared.motion_segments`。原先用
    `f"{new_a:g}"` 输出 6 位有效数字，`cut_t=1.621622` 这类小数会写出
    「0-0.378378秒」残缺文本；`fmt_time` 统一截 2 位小数。
    """
    body = _ms.strip_time_label(atom)
    if not body:
        return ""
    return f"{_ms.fmt_time(new_a)}-{_ms.fmt_time(new_b)}秒：{body}"

def _split_motion_desc_at_cut(motion: str, cut_t: float, total_sec: float) -> tuple[str, str]:
    """把显式 motion 时段按 cut_t 切成左/右两半（各自本地时间轴）.

    v6.76：解析改走 `shared.motion_segments.atom_range`（与全仓同一套正则，
    分隔符集为并集、支持小数）。切分语义不变：跨越切点的原子段只写右半
    （本地时间从 0 起），左半不留内容——由调用方决定父镜如何回退。
    """
    if not motion:
        return "", ""
    left: list[str] = []
    right: list[str] = []
    for atom in _ms.atoms(motion):
        rng = _ms.atom_range(atom, total_sec)
        if rng is None:
            continue
        a, b = rng
        if b <= cut_t + 0.01:
            left.append(atom)
        elif a >= cut_t - 0.01:
            shifted = _rewrite_motion_time(atom, a - cut_t, b - cut_t)
            if shifted:
                right.append(shifted)
        else:
            # 跨切点：本段在右半从本地 0 续起
            shifted = _rewrite_motion_time(atom, 0.0, max(0.1, b - cut_t))
            if shifted:
                right.append(shifted)
    return "；".join(left), "；".join(right)

def _find_best_split_point(text: str) -> int:
    """在 text 中找最接近中点的优先级切点.

    Returns:
        切点位置（char index, 切点之前的字符保留在第一段）.
        找不到合适切点返回 -1.
    """
    if not text:
        return -1
    target = len(text) // 2
    best = (-1, float("inf"))  # (index, distance_to_target)
    for delim in _SPLIT_DELIMS_PRIORITY:
        for i, ch in enumerate(text):
            if ch == delim:
                # 切点必须在 [text_len*0.25, text_len*0.75] 区间，避免切出半句话
                if i < len(text) * 0.25 or i > len(text) * 0.75:
                    continue
                d = abs(i - target)
                if d < best[1]:
                    best = (i + 1, d)  # 切点 = delim 之后
        if best[0] >= 0:
            return best[0]
    return -1


def _event_boundary_cut(shot: dict, split_at: int, text: str, dur: float):
    """在 `temporal_plan` 的事件边界里挑物理切点；无可用边界 → None.

    v6.77（第三阶段）：把切点从「旁白字符比例 × 原时长」改为**事件边界**。
    字符位置与事件时刻本无关系——实测 `cut_t = 1.621622` 而 motion 分段落在
    2/4/5 秒，两者互不对齐，导致子镜 KF 归属错位（时间错配的根因）。

    旁白切点比例**只用来给候选边界排序**（取最接近的那个），不直接当时刻。

    v6.79（09-20，决定 1）**只接受确定性派生的边界**：`source == "llm"` 的
    事件表在本轮迁移验收完成前**不得驱动结构写入**。LLM 事件表进入
    `validate_events` 的严格校验（整镜覆盖 / 原文出处 / 与 motion 一致）之后，
    仍然只是「候选事实源」——它可能整段虚构却恰好结构合法，而拆镜会据此
    永久改变镜头结构。迁移期它只参与审计与一致性统计（`_check_temporal_plan`）。
    验收口径见 docs/step01-temporal-writers-audit-2026-09-19.md 决定 1：
    每个 provider 抽样 ≥100 镜，首轮结构合格率 ≥95%、一次 regen 后 100%。

    Returns:
        内部边界的秒数；无 `temporal_plan` / 无内部边界 / 仅有 LLM 事件表 → None
    """
    from shared.motion_segments import derive_temporal_plan

    if dur <= 0:
        return None
    plan = derive_temporal_plan(shot)
    if not plan or plan.get("source") != "derived":
        if plan and plan.get("source") == "llm":
            _log().warn(
                f"[SPLIT-BLOCKED] 镜头 {shot.get('index')} 只有 LLM 直出 temporal_plan，"
                "迁移验收完成前不用于拆镜切点（只参与审计统计）；"
                "需由上游完整 regen 生成带显式时段的 motion_desc 或多个完整子镜"
            )
        return None
    bounds = sorted(
        {round(ev["start_sec"], 3) for ev in plan["events"]}
        | {round(ev["end_sec"], 3) for ev in plan["events"]}
    )
    interior = [b for b in bounds if 0.05 < b < dur - 0.05]
    if not interior:
        return None
    target = dur * (split_at / max(len(text), 1))
    return min(interior, key=lambda b: (abs(b - target), b))


def _do_pre_split_oversized_shots(shots: list, shot_cap: float) -> int:
    """v6.43（08-25）修复 8s 静默 clamp：估时 > shot_cap 的 shot 拆为 2 镜.

    旧实现：timecode.py for 循环里 `if duration > shot_cap: duration = shot_cap`
    静默压缩时长 → 长对话镜头后半段被截断 / TTS 音画不同步。

    新实现：pre-pass 在 _compute_timecodes 主循环之前把 shots 里超 cap 的按
    narration_segment 句号切为 2 镜，原地修改 shots 列表。

    切镜策略（防递归爆炸）：
    1. 估时 > shot_cap 才拆（防 false positive）
    2. 二分找切点：必须满足"两段估时都 ≤ shot_cap"——保证拆 1 次就够
    3. 找切点：优先级 (。 > ？ > ！ > … > ，) 距中点最近且满足 ②
    4. 找不到合适切点 → 不拆（保留原版，下游约束再处理）
    5. shot_b 继承 shot_a 的 prompt/ff/lf/char_layout/motion_desc（连贯性）
    6. shot_b.narration_segment = 切后段；subtitle 同步
    7. shot_b.index = shot_a.index + 0.5（半整数；timeline 顺序按 list 序）

    Returns:
        拆出镜数（int；0 = 全部不需拆）
    """
    import copy
    split_count = 0
    i = 0
    while i < len(shots):
        shot = shots[i]
        # v6.51：每镜最多拆 1 次。_compute_timecodes 每次落盘都会重跑本
        # pre-pass（main.py 5 处调用），已拆出的子镜若估时仍 > cap 会被
        # 再次拆分，index +0.5 链式递增（7.5→8.0）与真实 shot 8 数值碰撞
        # （16:34 run 实测：两次 [SPLIT] 日志、shots 同时含 8.0 与 8）。
        if "_split_from" in shot or "_split_to" in shot:
            i += 1
            continue
        text = shot.get("narration_segment", "")
        if not text:
            i += 1
            continue
        estimated = _estimate_duration(text)
        if estimated <= shot_cap:
            i += 1
            continue
        # 找切点：必须满足切后两段估时都 ≤ shot_cap（防递归）
        split_at = _find_split_point_within_cap(text, shot_cap)
        if split_at <= 0 or split_at >= len(text) - 1:
            i += 1
            continue
        # 构造 shot_b
        shot_b = copy.deepcopy(shot)
        # narration_segment 切分
        shot_b["narration_segment"] = text[split_at:].strip()
        if shot_b.get("subtitle"):
            shot_b["subtitle"] = shot_b["narration_segment"]
        # ff_desc 继承 shot_a 的 lf_desc（接续）
        lf_a = shot.get("lf_desc", "")
        if lf_a:
            shot_b["ff_desc"] = lf_a
        # v6.51: 从原镜 keyframes 截取后半段（t_sec > 切点时间的那些），
        # 重新计算相对 t_sec（减去切点时间）。
        # 之前直接清空导致拆分镜无 kf、纯靠 ff/lf 端点驱动，视频漂。
        orig_kfs = shot.get("keyframes") or []
        orig_dur = float(shot.get("duration_sec") or 0)
        # v6.77（第三阶段）：切点取 **temporal_plan 事件边界**，不再用字符比例。
        cut_t = _event_boundary_cut(shot, split_at, text, orig_dur)
        if cut_t is None:
            # 无合法事件边界 → **不拆**（原实现会退化到「只要左段 ≤ cap」硬切，
            # 切点与事件无关 → KF 归属错位）。正确解法是让 LLM 把该镜重写成
            # 结构更清晰的多事件镜头；此处只做可观测上报，交上游 regen。
            _log().warn(
                f"[SPLIT-BLOCKED] 镜头 {shot.get('index')} 旁白估时 {estimated:.1f}s > "
                f"{shot_cap:.1f}s 需拆分，但其 temporal_plan 无可用事件边界"
                "（motion_desc 无时段标记、或只有一个事件）——保留原镜不拆，"
                "需 LLM 重写为多事件镜头"
            )
            i += 1
            continue
        new_kfs_with_idx: list[tuple[int, dict]] = []
        if orig_kfs and orig_dur > 0:
            for orig_idx, kf in enumerate(orig_kfs):
                if not isinstance(kf, dict):
                    continue
                t = float(kf.get("t_sec", 0) or 0)
                if t <= cut_t:
                    continue  # 前半段的 kf，跳过
                # 后半段的 kf，重新计算相对时间
                new_t = round(t - cut_t, 3)
                # 新镜时长 = 原时长 - cut_t
                new_dur = round(orig_dur - cut_t, 3)
                if new_t <= 0:
                    new_t = 0.1  # 防止 0s 起点（FF 已经是 t=0）
                if new_t >= new_dur:
                    new_t = round(new_dur - 0.1, 3)  # 防止超出结尾
                kf_copy = dict(kf)
                kf_copy["t_sec"] = new_t
                new_kfs_with_idx.append((orig_idx, kf_copy))
        new_kfs = [kf for _, kf in new_kfs_with_idx]
        shot_b["keyframes"] = new_kfs
        # Split explicit motion segments too; never copy the whole motion_desc
        # into both halves.  If no explicit segment survives, leave a short
        # continuation description rather than duplicating the original action.
        # v6.77：切点在事件边界上，故两侧各自持有本侧的事件段文本。
        # **不再**在任一侧为空时伪造「从首帧推进到切分状态，完成：<lf_desc>」
        # ——那是把首尾帧描述冒充动作过程。真的为空就让它为空并交由检查器上报。
        motion_left, motion_right = _split_motion_desc_at_cut(
            str(shot.get("motion_desc") or ""), cut_t, orig_dur)
        shot["motion_desc"] = motion_left
        shot_b["motion_desc"] = motion_right
        # v6.57+ frame_layouts.kfs 同步截取（与 keyframes 一一对应）
        # B9 修复（2026-09-03）：
        #   1. new_kfs 为空时也必须显式清空 _fl_b["kfs"]，否则 deepcopy 残留
        #      原 layout.kfs，触发 v6.57 S 约束「kfs 数量 ≠ keyframes 数量」HARD
        #   2. **按 index 对齐**（不是 desc 匹配）—— desc 在 LLM 输出可能重复或
        #      微改（"杨旭拔刀" vs "杨旭拔刀"），按 index 对齐最稳
        #   3. 原 layout.kfs 数量可能 < orig_kfs（边界情况），按 index 取可能
        #      越界——只取 min(len(orig_kfs_layout), len(orig_kfs)) 范围内的
        #      layout 项，多出的 new_kf 用最后一帧 layout 兜底（保证数量对齐）
        _fl_b = shot_b.get("frame_layouts")
        if isinstance(_fl_b, dict):
            orig_kfs_layout = list(_fl_b.get("kfs") or [])
            new_kfs_layout: list = []
            for orig_idx, _kf in new_kfs_with_idx:
                if orig_idx < len(orig_kfs_layout):
                    new_kfs_layout.append(orig_kfs_layout[orig_idx])
                elif orig_kfs_layout:
                    # 只在原 layout 本身缺项时才兜底；不得用“减完 cut_t 的新
                    # t_sec”去反查原索引，那会把所有帧都匹配到最后一帧。
                    new_kfs_layout.append(orig_kfs_layout[-1])
            # 严格按 new_kfs 数量截断（防御性）
            _fl_b["kfs"] = new_kfs_layout[:len(new_kfs)]
        if not shot_b.get("motion_desc"):
            # 没 motion_desc 时给个空的避免 validator 误报
            shot_b["motion_desc"] = ""
        # 标记拆镜关系（调试用，不进入 prompt）
        shot_b["_split_from"] = shot.get("index", i)
        # 索引防碰撞：+0.5 撞现有 index（int/float 数值相等即撞）时逐级细化，
        # 防止出现 8.0 与真实 shot 8 并存的数值重复（排序/查重即不稳定）
        _orig_idx = shot.get("index", i) or i
        _existing = {s.get("index") for s in shots if isinstance(s, dict)}
        new_idx = round(_orig_idx + 0.5, 4)
        for _frac in (0.25, 0.125, 0.0625):
            if new_idx not in _existing:
                break
            new_idx = round(_orig_idx + _frac, 4)
        shot_b["index"] = new_idx
        # v6.50（08-29）拆镜视觉差异化：此拆分器 shot_b.ff=shot_a.lf（接续）
        # 但 shot_b.lf 是同一文本深拷贝 → ff==lf 必现；prompt 深拷贝 → 相邻
        # 相似度 1.00（14:22 run shot11.5/12.5 实测）。按"后半镜"（part_i=1）
        # 差异化：景别 +1、过肩、情绪递进、焦点、lf 缓推兜底。
        try:
            from step01_script_gen.split_visuals import differentiate_split_visuals
            differentiate_split_visuals(shot, shot_b, part_i=1)
        except Exception:
            pass
        # v6.50（08-29）后半镜致命伤锚延续：拆分发生在约束层 injury_continuity
        # 之后，新 halves 没人再补锚（15:12 run shot12.5/14.5/15.5 ff/lf 无伤锚
        # 实测——刺杀后画面会画出无伤的杨旭）。原镜带真伤锚 + 非威胁角色在
        # 后半镜画面 → ff/lf 补锚。
        try:
            from step01_script_gen.prompt_semantic.registry import (
                INJURY_ANCHOR_TAIL as _IAT,
                STRONG_INJURY_ANCHORS as _SIA,
                BLOOD_STATE_WORDS as _BSW,
                COLLECTIVE_NAMES as _COLL,
            )
            _orig_txt = (shot.get("prompt") or "") + (shot.get("lf_desc") or "") + (shot.get("ff_desc") or "")
            if any(w in _orig_txt for w in _SIA):
                _victim = ""
                for _it in (shot_b.get("char_layout") or []):
                    _nm = str((_it or {}).get("name", "")).strip()
                    if (_nm and not any(t in _nm for t in ("刺客", "神秘人", "黑衣人", "杀手"))
                            and not any(w in _nm for w in _COLL)):
                        _victim = _nm
                        break
                _b_txt = (shot_b.get("ff_desc") or "") + (shot_b.get("lf_desc") or "")
                if _victim and _victim in _b_txt:
                    for _fld in ("ff_desc", "lf_desc"):
                        _t = shot_b.get(_fld) or ""
                        # v6.51：已锚判定用 BLOOD_STATE_WORDS（含 暗红/洇开/血迹 等
                        # 可见血状态词）。原用 STRONG（洇痕/血痕…）——变体锚
                        # 「暗红痕迹洇开，清晰可见」漏判 → 重复追加完整 _IAT →
                        # 下游复读去重把重复片段抠掉，残留「。暗红血迹洇开，血痕」
                        # 截断碎片（16:34 run shot8.0 实测）。
                        if _t and not any(w in _t for w in _BSW):
                            shot_b[_fld] = _t.rstrip("。") + f"。{_IAT}"
        except Exception:
            pass
        # v6.77：删除重复的差异化调用——此前同一函数内被调了两次
        # （2026-09-12 追加时漏看上方已有），纯重复执行。
        # 2026-09-12 根因修复：拆分后验证——后镜必须提供**新增视觉信息**，否则
        # 回滚不拆。判定（相邻重复不变量，12/12.5 复盘：0.94 重复镜占 12.8s）：
        #   a) 后镜自身非静态：jaccard(b.ff, b.lf) < 0.85
        #   b) 后镜终点推进：jaccard(a.lf, b.lf) < 0.85
        # （b.ff=a.lf 接续是分镜设计，不算重复。）差异化后仍不达标（如静态水面
        # 场景只有换景别词的伪差异化）⇒ 保留单镜长时长，视频生成端 clamp 兜底。
        try:
            from shared.split_similarity import jaccard_bigram as _jb_verify
            _a_lf = shot.get("lf_desc") or ""
            _b_ff = shot_b.get("ff_desc") or ""
            _b_lf = shot_b.get("lf_desc") or ""
            if _a_lf and _b_ff and _b_lf:
                if (_jb_verify(_b_ff, _b_lf) >= 0.85
                        or _jb_verify(_a_lf, _b_lf) >= 0.85):
                    i += 1
                    continue
        except Exception:
            pass
        # shot_a 留前半段
        shots[i]["narration_segment"] = text[:split_at].strip()
        if shots[i].get("subtitle"):
            shots[i]["subtitle"] = shots[i]["narration_segment"]
        # A split dialogue shot must not inherit the original victim/subject
        # verbatim.  When each half carries an explicit speaker, promote that
        # speaker to main_subject so downstream character anchors do not treat
        # a listener or corpse as the shot subject.
        try:
            from step01_script_gen.split_visuals import extract_speaker as _extract_split_speaker
            _split_char_names = [
                str(item.get("name") or "").strip()
                for item in (shots[i].get("char_layout") or [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
            _speaker_a = _extract_split_speaker(
                str(shots[i].get("narration_segment") or ""), _split_char_names
            )
            _speaker_b = _extract_split_speaker(
                str(shot_b.get("narration_segment") or ""), _split_char_names
            )
            _split_speaker = _speaker_a or _speaker_b
            if _split_speaker:
                shots[i]["main_subject"] = _speaker_a or _split_speaker
                shot_b["main_subject"] = _speaker_b or _split_speaker
        except Exception:
            pass
        shots[i]["_split_to"] = shot_b["index"]
        # 插入到 i+1
        shots.insert(i + 1, shot_b)
        split_count += 1
        # v6.43 08-25：限制单 shot 最多拆 1 次（i 跳过 shot_b 自身），防
        # 23s 长对话拆成 6 镜导致镜数爆炸。shot_b 估时仍可能 > cap，由
        # _compute_timecodes 主循环的 clamp 兜底（虽然会被截断，但至少
        # shot_a 是完整的）。
        i += 2
    if split_count:
        try:
            _log().info(
                f"[SPLIT] {split_count} 个镜头估时 > {shot_cap:.1f}s，已按 narration_segment 切为 2 镜"
            )
        except Exception:
            pass
    return split_count


def _find_split_point_within_cap(text: str, cap: float) -> int:
    """在 text 中找切点，满足切后两段估时都 ≤ cap（防递归拆镜）.

    策略：枚举优先级切点（。 > ？ > ！ > … > ，），找最接近中点且两段都 ≤ cap。
    退化：若两段都 ≤ cap 找不到，按"只强制左段 ≤ cap、右段任意"再找 1 次。
    Returns: 切点 index（0 或 len(text)-1 = 不可拆）.
    """
    if not text:
        return -1
    target = len(text) // 2
    best_strict = (-1, float("inf"))
    best_loose = (-1, float("inf"))  # 退化：只要求左段 ≤ cap
    for delim in _SPLIT_DELIMS_PRIORITY:
        for i, ch in enumerate(text):
            if ch != delim:
                continue
            # 切点必须在 [text_len*0.2, text_len*0.8] 区间（避免切出半句话）
            if i < len(text) * 0.2 or i > len(text) * 0.8:
                continue
            left = text[:i + 1]
            right = text[i + 1:]
            d = abs(i - target)
            # 严格：两段都 ≤ cap
            if _estimate_duration(left) <= cap and _estimate_duration(right) <= cap:
                if d < best_strict[1]:
                    best_strict = (i + 1, d)
            # 退化：只要求左段 ≤ cap（右段可能仍超 cap，由 shot_b 继续尝试拆）
            elif _estimate_duration(left) <= cap:
                if d < best_loose[1]:
                    best_loose = (i + 1, d)
        if best_strict[0] >= 0:
            return best_strict[0]
    return best_loose[0]  # 严格没找到就退化（仍优于不拆）


def _extract_motion_duration(motion_desc: str) -> float | None:
    """从 motion_desc 推算实际时长（v6.76 起委托统一解析器）.

    实现迁至 `shared.motion_segments.extract_total_duration`。并集法取最大：
    「前N秒」+「后M秒」求和 / 「N-M秒」取最大终点 / 「共N秒」显式声明。

    统一后分隔符集扩为并集（新增全角 `～`）——实测 `2～4秒：挥下` 原先返回
    None（正则漏了全角波浪号），会让 timecode 把该镜时长算回默认值。
    """
    return _ms.extract_total_duration(motion_desc)

def _align_frame_duration(raw: float, fps: float) -> float:
    """时长对齐整数帧 + 输出 ≤3 位小数（08-23 用户要求 duration 保留 2-3 位小数）.

    帧对齐铁律（v6.32）：duration × fps 必须是整数帧，否则视频生成多/少一帧卡顿。
    但 fps=24 时 1/24 = 0.04166… 是循环小数，直接 round(raw*fps)/fps 得到
    5.583333333333333 这类长小数；若粗暴 round(x, 2) 又破坏整数帧对齐
    （5.58×24=133.92 非整帧 → style_prefix.validate_fps_consistency 每次误报）。

    关键：fps=24 时 duration = 帧数/24 只有在帧数是 3 的倍数时才非循环
    （3k/24 = k/8，最多 3 位小数：0.125/0.375/0.5…）。因此把帧数归一化到
    最近 3 的倍数——同时满足「整数帧对齐」+「输出 ≤3 位小数」。

    通用化：仅当帧数×1000/fps 非整数（时长在 3 位内不可精确表示，如 fps=24/30）
    才归一化到 3 帧步长；fps=25 等 1/fps 可整除 1000 的帧率天然 2 位小数，跳过。
    最大偏差 1.5 帧 = 0.0625s（fps=24），远小于 QC 时长容差 1.5s。
    """
    if fps <= 0:
        return round(float(raw), 2)
    frames = round(float(raw) * fps)
    # 时长在 3 位小数内能否精确表示？帧数 × 1000 / fps 应为整数
    if abs(frames * 1000 / fps - round(frames * 1000 / fps)) > 1e-6:
        frames = round(frames / 3) * 3
    return frames / fps


def project_post_split_metrics(shots: list, *, fps=None, shot_cap=None) -> dict:
    """预估 timecode 拆镜后的有效镜数/时长，不修改输入、不写日志.

    候选打分必须在 timecode 拆镜前使用这个投影值：LLM 常给出 5s 的镜头，
    但 narration 实际估时 9-15s，_compute_timecodes 会拆成 2 镜并按语音重估
    时长。09-16 实测原始 17 镜/75.5s 的 100 分候选，拆后变成 21 镜/97.6s。
    """
    _shots = [s for s in (shots or []) if isinstance(s, dict)]
    try:
        _fps = float(fps or 24)
    except (TypeError, ValueError):
        _fps = 24.0
    if _fps <= 0:
        _fps = 24.0
    if shot_cap is None:
        _dur_cap = active_video_duration_cap()
        shot_cap = MAX_SHOT_DURATION if not _dur_cap else min(_dur_cap, MAX_SHOT_DURATION)
    try:
        shot_cap = float(shot_cap)
    except (TypeError, ValueError):
        shot_cap = MAX_SHOT_DURATION
    if shot_cap <= 0:
        shot_cap = MAX_SHOT_DURATION

    def _duration_for_text(text: str, motion_duration: float | None) -> float:
        speech_duration = _estimate_duration(text) if _has_dialogue(text) else 0.0
        if motion_duration and motion_duration > 0:
            base = max(motion_duration, speech_duration) if speech_duration > 0 else motion_duration
        elif _has_dialogue(text):
            base = speech_duration
        else:
            base = NARRATION_FIXED_DURATION
        raw = max(base, MIN_SHOT_DURATION)
        duration = _align_frame_duration(raw, _fps)
        if duration > shot_cap:
            duration = _align_frame_duration(shot_cap, _fps)
        return duration

    projected_shots = 0
    projected_duration = 0.0
    split_count = 0
    for shot in _shots:
        text = str(shot.get("narration_segment") or "")
        motion_duration = _extract_motion_duration(
            str(shot.get("motion_desc") or "")
        )
        if text and _estimate_duration(text) > shot_cap:
            split_at = _find_split_point_within_cap(text, shot_cap)
            if 0 < split_at < len(text) - 1:
                left = text[:split_at].strip()
                right = text[split_at:].strip()
                projected_duration += (
                    _duration_for_text(left, motion_duration)
                    + _duration_for_text(right, motion_duration)
                )
                projected_shots += 2
                split_count += 1
                continue
        projected_duration += _duration_for_text(text, motion_duration)
        projected_shots += 1

    return {
        "shot_count": projected_shots,
        "duration_sec": round(projected_duration, 2),
        "split_count": split_count,
    }


def write_timeline_from_durations(script: dict) -> None:
    """Rebuild timecodes/timeline from the final authoritative durations.

    `_compute_timecodes` estimates or clamps durations from motion/dialogue.
    Final hard-fix constraints can change `duration_sec` afterwards (runtime
    extension, merges, split cleanup).  This function never re-estimates; it
    only serializes the already-final durations into one consistent timeline.
    """
    shots = script.get("shots") or []
    if not shots:
        return
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    old_timeline = {
        e.get("index"): e for e in (script.get("timeline") or [])
        if isinstance(e, dict) and e.get("index") is not None
    }
    timeline: list[dict] = []
    current = 0.0

    if hook:
        try:
            hook_duration = float(hook.get("duration_sec") or hook.get("duration") or 0)
        except (TypeError, ValueError):
            hook_duration = 0.0
        if hook_duration > 0:
            hook_duration = round(hook_duration, 2)
            hook["duration"] = hook_duration
            hook["duration_sec"] = hook_duration
            hook["timecode"] = {"start": 0.0, "end": hook_duration}
            entry = dict(old_timeline.get(0) or {})
            entry.update({
                "index": 0,
                "is_hook": True,
                "start": 0.0,
                "end": hook_duration,
                "duration": hook_duration,
                "effective_duration": hook_duration,
            })
            timeline.append(entry)
            current = hook_duration

    for shot in shots:
        if not isinstance(shot, dict):
            continue
        idx = shot.get("index", len(timeline))
        try:
            duration = float(shot.get("duration_sec") or shot.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0:
            raise ValueError(f"shot {idx} has invalid duration_sec={duration!r}")
        duration = round(duration, 2)
        shot["duration"] = duration
        shot["duration_sec"] = duration
        shot["timecode"] = {
            "start": round(current, 2),
            "end": round(current + duration, 2),
        }
        entry = dict(old_timeline.get(idx) or {})
        entry.update({
            "index": idx,
            "start": round(current, 2),
            "end": round(current + duration, 2),
            "duration": duration,
            "effective_duration": duration,
        })
        for key in ("variation_type", "keyframe_sample_count",
                    "movement_speed_multiplier", "blank_hold_duration"):
            if key not in entry and key in shot:
                entry[key] = shot[key]
        timeline.append(entry)
        current += duration

    script["timeline"] = timeline
    script["total_duration"] = round(current, 2)
    script["total_effective_duration"] = round(current, 2)


def timeline_consistency_errors(script: dict) -> list[str]:
    """Return deterministic consistency errors for the final timeline."""
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    timeline = [e for e in (script.get("timeline") or []) if isinstance(e, dict)]
    errors: list[str] = []
    expected = []
    hook = script.get("hook") if isinstance(script.get("hook"), dict) else None
    if hook and float(hook.get("duration_sec") or hook.get("duration") or 0) > 0:
        expected.append(0)
    expected.extend(s.get("index") for s in shots)
    timeline_indexes = [e.get("index") for e in timeline]
    if timeline_indexes != expected:
        errors.append(f"timeline indexes {timeline_indexes} != expected {expected}")
    total = float(script.get("total_duration") or 0)
    if timeline and abs(float(timeline[-1].get("end") or 0) - total) > 0.011:
        timeline_end = timeline[-1].get("end")
        errors.append(f"timeline end {timeline_end}s != total_duration {total}s")
    if abs(sum(float(s.get("duration_sec") or 0) for s in shots)
           + (float(hook.get("duration_sec") or 0) if hook else 0.0) - total) > 0.011:
        errors.append("sum(duration_sec) != total_duration")
    return errors


def max_kf_t_sec(shot: dict) -> float:
    """该镜 `keyframes[].t_sec` 的**最大合法值**（无有效帧 → 0.0）.

    「帧写在哪个物理时刻，镜头就必须长到能拍到它」——钩子（`derive_hook_duration`）
    与单镜定向拆分的子镜都用这一条，故抽出来共用。
    """
    out = 0.0
    for kf in (shot.get("keyframes") or []):
        if not isinstance(kf, dict):
            continue
        try:
            t = float(kf.get("t_sec"))
        except (TypeError, ValueError):
            continue
        if t > out:
            out = t
    return out


def derive_hook_duration(hook: dict, fps: float = 24.0) -> float:
    """钩子的**权威时长**：`max(motion 分段, LLM duration, 台词估时, KF 最大 t_sec)`，
    下限 `MIN_SHOT_DURATION`，按 fps 对齐整数帧；无法推导 → 0.

    v6.87：把 **KF 最大 `t_sec`** 也纳入候选——关键帧写在哪个物理时刻，镜头就必须
    长到能拍到它（实测缺这一项导致钩子 3.0s 而 KF 到 7.2s，2 条 HARD）。

    v6.84（二次审核发现 5）：本函数从 `_compute_timecodes` 的钩子分支抽出，
    让**钩子质量门**也能在收集 issue 之前先把时长/时间轴定下来。原实现只在
    `_compute_timecodes` 里算时长，而钩子质量门跑在它**之前**——于是钩子的
    `t_sec=0` 首帧在质量门里看不见（`duration_sec` 还没派生），一路漏到落盘前
    的最终时间不变量才暴露，那时已经没有 regen 机会了。
    """
    if not isinstance(hook, dict):
        return 0.0
    # 1) motion_desc 时间分段（如"前2秒…2-4秒…4-6秒"→6s）优先，动作必须播完
    motion_duration = (_extract_motion_duration(hook.get("motion_desc", ""))
                       if hook.get("motion_desc") else None)
    # 2) LLM 给出的 duration（仅作参考）
    llm_duration = None
    raw = hook.get("duration_sec")
    if raw is None:
        raw = hook.get("duration")
    if raw is not None:
        try:
            llm_duration = float(raw)
        except (TypeError, ValueError):
            llm_duration = None
    # 3) 对话字数估算（钩子通常是尖叫/对话，留足口型时间）
    hook_text = hook.get("narration_segment", "")
    speech_duration = _estimate_duration(hook_text) if hook_text else 0.0

    # 4) **KF 时刻**：关键帧写在哪个物理时刻，镜头就必须长到能拍到它。
    #
    # v6.87（2026-09-21 23:19 实测）：原候选只有 (motion 分段, LLM duration, 台词估时)，
    # 不含 KF —— 于是模型按「约 7.2s 的钩子」写了 KF `t_sec=[2.4, 4.8, 7.2]`，
    # 而钩子时长被推成 **3.0s**（motion 只写到 3 秒、无台词），落盘产生 2 条
    # `kf_time_out_of_shot` HARD（「该帧不可能被拍到」）。
    #
    # 修法与 `kf_start_time_normalize` 同一原则：**用已经写好的数据推导容器，
    # 而不是搬动已写内容**（把 KF 挪到 3s 内会破坏模型设计的节拍）。钩子不受
    # 正片 60-90s 约束，且 7.2s 仍在单镜 8s 上限内。
    _kf_max = max_kf_t_sec(hook)
    kf_duration = _kf_max if _kf_max > 0 else None

    candidates = [d for d in (motion_duration, llm_duration, speech_duration, kf_duration)
                  if d and d > 0]
    dur = max(candidates) if candidates else MIN_SHOT_DURATION
    # 下限保护：钩子是开场悬念，至少给足动作时间，禁止 < MIN_SHOT_DURATION
    dur = max(dur, MIN_SHOT_DURATION)
    dur = _align_frame_duration(dur, fps or 24.0)
    # v6.88（2026-09-22 07:11 实测）：**覆盖内容优先于帧对齐**。
    #
    # `_align_frame_duration` 会把时长向最近的整数帧取整，方向可能向下：
    # 4.8s → 4.75s，于是「推导出的时长」把自己那条 KF（t_sec=4.8）挤出镜头，
    # 落盘产生 `kf_time_out_of_shot`（「该帧不可能被拍到」）。
    # 上一步已经把 KF 时刻纳入候选，这里必须保证对齐之后**仍然装得下**——
    # 否则等于修了一半。向上加帧直到覆盖（帧步长 = 1/fps，循环有硬上限）。
    _fps = float(fps or 24.0)
    if kf_duration and _fps > 0 and dur < kf_duration - 1e-9:
        _step = 1.0 / _fps
        for _ in range(240):
            dur = round(dur + _step, 6)
            if dur >= kf_duration - 1e-9:
                break
    return dur


def sync_hook_timeline(script: dict) -> float:
    """把钩子的 `duration` / `duration_sec` / `timecode` 派生成一致的一组值.

    钩子不受单镜 8s cap（时长另算），但仍受**模型 cap** 约束。返回钩子时长
    （0 = 无钩子）。
    """
    hook = script.get("hook")
    if not isinstance(hook, dict):
        return 0.0
    fps = float(script.get("fps", 24) or 24)
    if fps <= 0:
        fps = 24.0
    dur = derive_hook_duration(hook, fps)
    if dur <= 0:
        return 0.0
    dur_cap = active_video_duration_cap()
    if dur_cap and dur > dur_cap:
        dur = _align_frame_duration(dur_cap, fps)
    hook["duration"] = round(dur, 2)
    hook["duration_sec"] = round(dur, 2)
    hook["timecode"] = {"start": 0.0, "end": round(dur, 2)}
    return dur


def _compute_timecodes(script: dict, *, allow_split: bool = False) -> None:
    """根据 motion_desc 时间分段或对话字数计算每个镜头的 timecode。

    v6.6 修复：
    - 优先从 motion_desc 中解析时间分段（前N秒，后M秒）
    - 只有对话（带冒号/引号）才按字数估算时长
    - 纯旁白用固定短时长（4秒）

    v6.43（08-23 修复）钩子时间轴：钩子按播放顺序在头部（开场钩子→淡出黑场
    →正片），所以 timecode 0..hook_duration 归 hook，正片 shots 从
    hook_duration..hook_duration+shots_total。但 shots_total（正片分镜总时长）
    用于"60-90s"硬约束检查时**仍只算 shots（不含钩子）**——钩子另算。

    ## `allow_split`（v6.76 新增，v6.79 默认值反转为 False）

    `True` 时执行拆镜 pre-pass（`_do_pre_split_oversized_shots`）：估时超 cap 的
    镜头按 narration 切点复制成两个镜头。这个 pre-pass **已移出默认生产路径**
    （决定 3，见 `docs/step01-temporal-writers-implementation-review-2026-09-19.md`）：

      - 它复制 KF、按文字切 narration、深拷贝语义字段、制造半整数镜头 ID
        （`index + 0.5`），并把 ff/lf 抄成同一份文本；
      - 切点用旁白字符位置，与事件时刻无关，导致子镜 KF 归属错位；
      - 拆出的新 shot_id 对下游是全新身份，审计/修复轮的按镜判定全部要重算。

    正确流程是**在生成 ff/motion/KF/lf 之前**就把超 cap 镜头拆好，或让模型
    返回两个完整子镜——而不是在 timecode 阶段对已经写好的详细镜头做字段拼接。
    默认 `False` 时超 cap 只上报（`_oversized` 分支 + gate 侧
    `quality_issues._shot_duration_issues`），由完整镜头 regen 处理。

    `allow_split=True` 保留给旧数据诊断与迁移测试；新代码不应传入。
    """
    shots = script.get("shots", [])
    if not shots:
        return

    # v6.43：先算钩子时长（开放在前），正片 shots 起始时间 = hook_duration
    hook = script.get("hook")
    hook_duration = 0.0
    if hook and isinstance(hook, dict):
        # v6.84：时长推导抽到 `derive_hook_duration()`——钩子质量门也要用同一份
        # 实现（发现 5：门跑在时间派生之前，看不见 t_sec=0）。
        hook_duration = derive_hook_duration(hook, float(script.get("fps", 24) or 24))

    # 视频帧率对齐（v6.32，08-12）：时长 × fps 必须是整数帧，否则视频生成
    # 多/少一帧导致卡顿（step01.log 反复报「duration 与 fps=24 不对齐」）。
    # 旧实现按音频帧 1/30s 对齐，与视频 fps=24 冲突；改按 fps 对齐整数帧。
    fps = float(script.get("fps", 24) or 24)
    if fps <= 0:
        fps = 24.0

    # 视频模型单镜时长上限（0=不夹取）。防止长对白/长 motion 分段生成的镜头
    # 超过模型上限（如 Seedance 1.5 pro=12s、Hailuo v1 720P=6s）被 provider
    # 截断——provider 现在会按能力表夹取，但那是「截断内容」而非「规划时长」。
    dur_cap = active_video_duration_cap()
    # 08-23 用户规则：正片单镜 ≤ 8s（MAX_SHOT_DURATION）；模型 cap 更严则随模型。
    # 钩子不受 8s 限制（钩子时长另算，下方 hook 段仍只按模型 cap 夹取）。
    shot_cap = MAX_SHOT_DURATION if not dur_cap else min(dur_cap, MAX_SHOT_DURATION)
    clamped_count = 0

    # v6.43（08-25）：拆镜 pre-pass——估时 > shot_cap 的 shot 按 narration_segment
    # 句号切为 2 镜，避免 timecode 静默 clamp 截断长对话镜头（违反 v6.43 用户规则）。
    # 放在 fps/dur_cap 推导之后，让 _estimate_duration / 拆分都走同一 shot_cap。
    #
    # v6.79（09-20，决定 3）：默认路径**不再拆镜**。拆镜是可增删镜头的结构操作：
    # 它复制 KF、按旁白字符位置切 narration、深拷贝语义字段、制造半整数镜头 ID。
    # 超 cap 的正确处置是**先在生成详细视觉字段之前拆好**，或让模型返回两个完整
    # 子镜；对已经写好的镜头做字段拼接必然产出 ff==lf / KF 归属错位的子镜。
    # 这里只上报，由完整镜头 regen 处理（gate 侧 blocking 判定见
    # `quality_issues._shot_duration_issues`）。
    if allow_split:
        _do_pre_split_oversized_shots(shots, shot_cap)
    else:
        _oversized = [
            s for s in shots
            if isinstance(s, dict)
            and "_split_from" not in s and "_split_to" not in s
            and s.get("narration_segment")
            and _estimate_duration(s.get("narration_segment")) > shot_cap
        ]
        if _oversized:
            _log().warn(
                f"[SPLIT-BLOCKED] {len(_oversized)} 个镜头旁白估时 > {shot_cap:.1f}s "
                f"（index={[s.get('index') for s in _oversized]}）需拆分；"
                "本阶段不增删镜头（拆镜已在生产路径停用），需由上游完整 regen "
                "在生成详细视觉字段前完成拆分"
            )

    # 钩子按模型 cap + fps 整数帧对齐（与 shots 一致；钩子本身不受 8s cap）
    if hook is None:
        script["hook"] = None
    elif hook_duration > 0:
        hook_duration = _align_frame_duration(hook_duration, fps)
        if dur_cap and hook_duration > dur_cap:
            hook_duration = _align_frame_duration(dur_cap, fps)
        hook["duration"] = round(hook_duration, 2)
        hook["duration_sec"] = round(hook_duration, 2)
        # 钩子 timecode 放在播放顺序头部（0..hook_duration）
        hook["timecode"] = {
            "start": 0.0,
            "end": round(hook_duration, 2),
        }
    else:
        hook["duration"] = 0.0
        hook["duration_sec"] = 0.0
        hook["timecode"] = {"start": 0.0, "end": 0.0}

    # v6.43：正片 shots 起始时间 = hook_duration（钩子在播放顺序头部）
    current_time = hook_duration
    timeline = []
    for shot in shots:
        text = shot.get("narration_segment", "")
        motion_desc = shot.get("motion_desc", "")
        var_type = shot.get("variation_type", "small")

        # v6.6: 优先从 motion_desc 解析时间分段
        motion_duration = _extract_motion_duration(motion_desc) if motion_desc else None

        # 对话镜头时长必须以「能念完台词」为下限：取 motion 分段与字数估时的较大值，
        # 避免视频太短、TTS 长台词被截断（音画同步关键）。
        speech_duration = _estimate_duration(text) if _has_dialogue(text) else 0.0

        if motion_duration and motion_duration > 0:
            base_duration = max(motion_duration, speech_duration) if speech_duration > 0 else motion_duration
        elif _has_dialogue(text):
            # 有对话但无 motion 分段：按对话字数估算时长
            base_duration = speech_duration
        else:
            # 纯旁白：固定短时长
            base_duration = NARRATION_FIXED_DURATION

        # #29 修复 2026-08-14：先兜底 MIN_SHOT_DURATION、再按 fps 对齐整数帧。
        # 旧顺序「先对齐后 max」在分数 fps（如 23.976）下会被 max 兜出
        # 3.0s 非整帧时长，破坏对齐保证（step01.log 反复报 duration 与 fps 不对齐）。
        raw = max(base_duration, MIN_SHOT_DURATION)
        # 08-23：_align_frame_duration 兼顾「整数帧对齐」+「输出 ≤3 位小数」
        # （fps=24 时长不再出现 5.583333333333333 长小数）
        duration = _align_frame_duration(raw, fps)
        # 按单镜上限夹取（模型 cap / 8s 硬限取严；先夹后对齐，保证整帧）。
        # 超过模型上限会被 provider 截断内容；8s 是 08-23 用户规则（钩子另算）。
        if duration > shot_cap:
            duration = _align_frame_duration(shot_cap, fps)
            clamped_count += 1

        shot["duration"] = duration
        # duration_sec：视频时长的权威来源（剥离 TTS 后由它驱动），
        # 与 duration 同值；LLM 的 duration 仅作参考，此处覆盖。
        shot["duration_sec"] = duration
        shot["timecode"] = {
            "start": round(current_time, 2),
            "end": round(current_time + duration, 2),
        }

        # v5.0: 运镜量化参数
        shot["keyframe_sample_count"] = _KEYFRAME_MAP.get(var_type, 1)
        shot["movement_speed_multiplier"] = _SPEED_MAP.get(var_type, 1.0)
        shot["blank_hold_duration"] = round(PAUSE_PER_SHOT, 1)

        timeline.append({
            "index": shot.get("index", 0),
            "start": round(current_time, 2),
            "end": round(current_time + duration, 2),
            "duration": duration,
            "effective_duration": round(duration, 2),
            "variation_type": var_type,
            "keyframe_sample_count": shot["keyframe_sample_count"],
            "movement_speed_multiplier": shot["movement_speed_multiplier"],
            "blank_hold_duration": shot["blank_hold_duration"],
        })

        current_time += duration

    # v6.43：timeline 列表前置钩子条目（播放顺序：hook → shots）
    if hook and isinstance(hook, dict) and hook_duration > 0:
        timeline.insert(0, {
            "index": 0,
            "is_hook": True,
            "start": 0.0,
            "end": round(hook_duration, 2),
            "duration": hook_duration,
            "effective_duration": round(hook_duration, 2),
            "variation_type": hook.get("variation_type", "large"),
            "keyframe_sample_count": _KEYFRAME_MAP.get(hook.get("variation_type", "large"), 3),
            "movement_speed_multiplier": _SPEED_MAP.get(hook.get("variation_type", "large"), 0.3),
            "blank_hold_duration": round(PAUSE_PER_SHOT, 1),
        })

    script["timeline"] = timeline

    # 计算正片分镜总时长（08-23 用户规则：≥60s 下限只算正片分镜，钩子另算）
    shots_total = sum(s.get("duration", 0) for s in shots)
    # v6.43：total = hook_duration + shots_total（钩子在播放顺序头部）
    total = hook_duration + shots_total

    script["total_duration"] = round(total, 2)
    script["total_effective_duration"] = round(total, 2)
    # 08-23 用户规则日志：正片分镜时长（不含钩子）与钩子时长分开报
    _log().info(f"时间轴计算完成: {len(shots)} 镜 {shots_total:.1f}s（不含钩子）"
          + (f" + 1 钩子 {hook_duration:.1f}s（另算，开场在头部）" if hook and hook_duration > 0 else "")
          + f", 总计 {total:.1f}s")
    if shots_total < MIN_TOTAL_SHOTS_DURATION:
        _log().warn(f"正片分镜总时长 {shots_total:.1f}s < {MIN_TOTAL_SHOTS_DURATION:.0f}s 硬下限"
              f"（不含钩子），W6 时长拉伸将补足（constraints/runtime_extend）")
    if clamped_count:
        cap_src = f"，视频模型上限 {dur_cap}s" if dur_cap and dur_cap < MAX_SHOT_DURATION else ""
        # v6.43（08-23 用户规则）：clamp 升级为 WARN——LLM 应拆镜而非靠静默
        # clamp 压缩时长（会丢失动作链）。让跑批时一眼看到 prompt 哪里没拆。
        _log().warn(f"{clamped_count} 个镜头时长超过单镜上限 {shot_cap}s{cap_src}，"
              f"已夹取到 {shot_cap}s（active model: {active_video_model()}）。"
              f"v6.43 用户规则：单镜 > {shot_cap}s 应在 prompt 层拆成多镜，"
              f"不靠此处静默 clamp。")
