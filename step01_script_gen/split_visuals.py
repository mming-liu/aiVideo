# -*- coding: utf-8 -*-
"""拆镜视觉差异化共用模块（v6.50，08-29）.

背景（13:46 run + 14:22 run 双实测）：项目里有**两个**拆镜器，都会把
prompt/ff/lf 原样复制给子镜——
  1. `validation._hard_fix_split_long_dialogue_shot`（F3.6 长对话拆镜）
  2. `timecode._do_pre_split_oversized_shots`（估时超 cap pre-pass，半整数
     索引来源；shot_b.ff=shot_a.lf（接续）但 shot_b.lf 是同一文本深拷贝
     → ff==lf 必然出现；prompt 深拷贝 → 相邻相似度 1.00）

本模块提供两处共用的差异化三件套（确定性，对 Seedance image 模式有效——
差异必须落在 ff/lf 上，它们才是图片模型的实际输入）：
  A. 景别按拆分深度推近（对白张力升级 = 镜头推近）
  B. 过肩正反打：相邻子镜交换机位方向
  C. part≥1 情绪递进 + 缓镜；焦点轮换（双人→说话人→听者反应）
  D. ff==lf 兜底：lf 追加镜头缓推
"""
from __future__ import annotations

import re

FRAMING_LADDER: tuple[str, ...] = (
    "大远景", "远景", "全景", "中景", "中近景", "近景", "特写", "大特写",
)

# 情绪阶梯（镜头内也递进：ff=第 N 级，lf=第 N+1 级）——ff/lf 共享基底+
# 过肩+伤锚时（接续拆分必现），靠焦点轮换+推近+情绪递进三重差异破自抄
_MOOD_LADDER: tuple[str, ...] = ("气氛骤紧", "气氛愈发紧绷", "气氛剑拔弩张", "一触即发")


def shift_framing(text: str, steps: int) -> tuple[str, str]:
    """把 text 中第一个景别词沿梯子推近 steps 级，返回 (新文本, 新景别词)."""
    if not text or steps <= 0:
        return text, ""
    # 长词优先匹配（大特写>特写、中近景>近景、大远景>远景）
    cur_idx = -1
    for w in sorted(FRAMING_LADDER, key=len, reverse=True):
        if w in text:
            cur_idx = FRAMING_LADDER.index(w)
            break
    if cur_idx < 0:
        return text, ""
    new_idx = min(len(FRAMING_LADDER) - 1, cur_idx + steps)
    old_w, new_w = FRAMING_LADDER[cur_idx], FRAMING_LADDER[new_idx]
    if new_w == old_w:
        return text, old_w
    return text.replace(old_w, new_w, 1), new_w


def extract_speaker(narr: str, char_names: list[str]) -> str:
    """从 narration 前缀（「张十三冷冷道：」）抽说话人名（限已知角色）."""
    m = re.match(r"^([\u4e00-\u9fff]{1,6})[：:]", (narr or "").strip())
    if not m:
        return ""
    prefix = m.group(1)
    for name in sorted(char_names, key=len, reverse=True):
        if prefix.startswith(name):
            return name
    return ""


# 运镜短语（「镜头较首帧缓推至远景」及变体）——只该出现在 lf（动作结束态）
# 与视频 prompt 里；ff 是静态首帧，携带运镜指令无意义且是 ff≈lf 的最大共享块
# （16:34 run shot8.0 ff 继承 7.5.lf 的推镜短语，sim 0.935 实测）。
_CAMERA_PUSH_RE = re.compile(r"，?镜头[^，。；]*?推至[^，。；]*")


def _strip_camera_push(text: str) -> str:
    """剥掉文本中的运镜短语（用于 ff 静态首帧）."""
    if not text:
        return text
    return _CAMERA_PUSH_RE.sub("", text)


def _upsert_camera_push(lf: str, framing: str) -> str:
    """lf 必带「缓推至X」：已继承旧短语 → 原位改写推近目标；没有 → 追加.

    原位改写而非盲目追加：链式拆镜（现已禁）或 lf 已含推镜短语时，
    追加会出现两条推镜指令且措辞近似，成为 ff/lf 新的共享块。
    """
    if not lf:
        return lf
    if _CAMERA_PUSH_RE.search(lf):
        return _CAMERA_PUSH_RE.sub(f"，镜头较首帧缓推至{framing}", lf, count=1)
    return lf.rstrip("。") + f"，镜头较首帧缓推至{framing}。"


_INERT_SPLIT_WORDS = (
    "已死", "尸体", "断气", "气绝", "昏迷", "失去意识", "无生命",
    "仰卧", "仰面躺", "倒地不起", "没入水中", "不见踪影",
)


def _char_inert_in_text(name: str, text: str) -> bool:
    if not name or not text:
        return False
    import re as _re_inert
    for m in _re_inert.finditer(_re_inert.escape(name), text):
        window = text[max(0, m.start() - 12):m.end() + 24]
        if any(w in window for w in _INERT_SPLIT_WORDS):
            return True
    return False


def differentiate_split_visuals(orig: dict, new_shot: dict, part_i: int) -> None:
    """就地差异化子镜的 prompt/ff_desc/lf_desc（两个拆镜器共用）.

    Args:
        orig: 原始（被拆）镜头 dict——取 char_layout
        new_shot: 新构造的子镜 dict——就地改写 prompt/ff_desc/lf_desc
        part_i: 子镜序号（0 起）。0=保持原描述；≥1 逐级推近+换机位+情绪递进
    """
    char_names = [str(it.get("name", "")).strip()
                  for it in (orig.get("char_layout") or []) if isinstance(it, dict)]
    char_names = [n for n in char_names if n]
    # 说话人优先从原镜 narration 抽（前缀在原镜首句；timecode 拆分器给子镜
    # 切的是后半段、无前缀——08-29 实测「画面聚焦（说话神情）」空名根因）
    speaker = (extract_speaker(orig.get("narration_segment", ""), char_names)
               or extract_speaker(new_shot.get("narration_segment", ""), char_names))
    _combined_text = " ".join(filter(None, [
        str(orig.get("ff_desc") or ""), str(orig.get("lf_desc") or ""),
        str(orig.get("motion_desc") or ""), str(orig.get("prompt") or ""),
        str(new_shot.get("ff_desc") or ""), str(new_shot.get("lf_desc") or ""),
    ]))
    listener = next(
        (n for n in char_names
         if n and n != speaker and not _char_inert_in_text(n, _combined_text)),
        "",
    )

    # ff/lf 先剥运镜短语（必须在 A 景别位移之前——否则 A 会把继承推镜短语里
    # 的景别词错位升级，产出「推至中景…缓推至近景」双指令；也保证幂等：
    # 二次应用时先剥掉上次追加的推镜再重加，结果收敛不漂移）
    ff = _strip_camera_push(new_shot.get("ff_desc", "") or "")
    lf = _strip_camera_push(new_shot.get("lf_desc", "") or "")

    # A. 景别按拆分深度推近
    steps = part_i
    prompt, framing = shift_framing(new_shot.get("prompt", "") or "", steps)
    ff, _ = shift_framing(ff, steps)
    lf, _ = shift_framing(lf, steps)

    # B. 过肩正反打：偶数子镜越听者肩望说者，奇数子镜反向
    if speaker and listener:
        pov = (f"过肩视角，越{listener}的肩望向{speaker}" if part_i % 2 == 0
               else f"过肩视角，越{speaker}的肩望向{listener}")
        if "过肩" not in ff:
            ff = ff.rstrip("。") + f"，{pov}。"
        if "过肩" not in lf:
            lf = lf.rstrip("。") + f"，{pov}。"
        if "过肩" not in prompt:
            prompt = prompt.rstrip("。") + f"。{pov}。"

    # C. part≥1 情绪递进 + 缓镜（镜头内 ff→lf 也沿阶梯进一级）
    if part_i >= 1:
        mood_idx = 1 if part_i == 1 else 2
        mood = _MOOD_LADDER[mood_idx]
        mood_lf = _MOOD_LADDER[min(mood_idx + 1, len(_MOOD_LADDER) - 1)]
        if mood not in prompt:
            prompt = prompt.rstrip("。") + f"。{mood}，镜头缓推向对话双方。"
        if mood not in ff:
            ff = ff.rstrip("。") + f"，{mood}。"
        if mood_lf not in lf:
            lf = lf.rstrip("。") + f"，{mood_lf}。"

    # E. 焦点轮换（对白三镜覆盖：双人 → 说话人 → 听者反应）
    if part_i >= 1 and (speaker or listener):
        focus, kind = ((speaker, "说话神情") if part_i % 2 == 1
                       else (listener or speaker, "聆听反应"))
        tag = f"画面聚焦{focus}（{kind}）"
        if tag not in prompt:
            prompt = prompt.rstrip("。") + f"。{tag}。"
        if tag not in ff:
            ff = ff.rstrip("。") + f"，{tag}。"
        # E2. ff/lf 结构性差异化：lf = 推近一级（景别梯子）+ 焦点换到另一侧
        #     （说话神情 ↔ 聆听反应）。接续拆分的 lf 与 ff 共享 90+ 字基底、
        #     同侧焦点与同一伤锚，文本相似度 0.85-0.94、成图近乎同帧（16:34
        #     run shot7.5/8.0/9.5 实测）——image 模式下视频没有动作可插值。
        #     换焦点+推近后两帧画面主体与景别都不同。
        lfocus, lkind = ((listener or speaker, "聆听反应") if kind == "说话神情"
                         else (speaker, "说话神情"))
        ltag = f"画面聚焦{lfocus}（{lkind}）"
        # E2b. lf 推近到反应特写后，说话人/听者的全景站位从句（「X位于画…」）
        #      与近景构图矛盾，且是 ff/lf 最大的共享块（16:34 run sim 0.85-0.94
        #      主因）。按从句剥除；受害者/道具站位行保留（伤锚连续性依赖）。
        _segs = re.split(r"([，。；])", lf)
        _kept: list[str] = []
        for _k in range(0, len(_segs), 2):
            _seg, _delim = _segs[_k], (_segs[_k + 1] if _k + 1 < len(_segs) else "")
            if ((_seg.startswith(speaker + "位于") if speaker else False)
                    or (_seg.startswith(listener + "位于") if listener else False)):
                continue
            _kept.append(_seg + _delim)
        lf = "".join(_kept)
        if ltag not in lf:
            # 推近目标按拆分深度走梯子（近景→特写），与「画面聚焦」主体
            # 突出语义一致，避免出现「推至远景却聚焦面部」的自相矛盾
            push_target = "特写" if part_i >= 2 else "近景"
            lf = lf.rstrip("。") + f"，镜头较首帧缓推至{push_target}，{ltag}。"
        elif "缓推至" not in lf:
            # 幂等补齐：上次应用过（ltag 在）但本函数开头的剥镜把推镜短语
            # 一并剥掉了——原位重插到 ltag 前，保证二次应用字节级收敛
            push_target = "特写" if part_i >= 2 else "近景"
            _anchor = f"，{ltag}。"
            if _anchor in lf:
                lf = lf.replace(_anchor, f"，镜头较首帧缓推至{push_target}{_anchor}", 1)
            else:
                lf = lf.rstrip("。") + f"，镜头较首帧缓推至{push_target}。"

    # D. ff≈lf 终检（无说话人/听者可轮换时的兜底，如动作镜拆分）：
    #    v6.50 口径：相似度 ≥0.85 即破（15:12 run 实测差 2-3 字的"自抄"
    #    变体逃过精确匹配）
    if ff and lf and not (part_i >= 1 and (speaker or listener)):
        import difflib
        if difflib.SequenceMatcher(None, ff.rstrip("。"), lf.rstrip("。")).ratio() >= 0.85:
            push = framing or "近景"
            lf = _upsert_camera_push(lf, push)

    new_shot["prompt"] = prompt
    new_shot["ff_desc"] = ff
    new_shot["lf_desc"] = lf
