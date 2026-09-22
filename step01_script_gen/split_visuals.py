"""拆镜视觉差异化共用模块（v6.50，08-29；v6.79 evidence-gated）.

背景（13:46 run + 14:22 run 双实测）：项目里有**两个**拆镜器，都会把
prompt/ff/lf 原样复制给子镜——
  1. `validation._hard_fix_split_long_dialogue_shot`（F3.6 长对话拆镜）
  2. `timecode._do_pre_split_oversized_shots`（估时超 cap pre-pass，半整数
     索引来源；shot_b.ff=shot_a.lf（接续）但 shot_b.lf 是同一文本深拷贝
     → ff==lf 必然出现；prompt 深拷贝 → 相邻相似度 1.00）

本模块只做**镜头层**差异化（确定性，对 Seedance image 模式有效——差异必须
落在 ff/lf 上，它们才是图片模型的实际输入）：
  A. 景别按拆分深度推近（对白张力升级 = 镜头推近）
  D. ff==lf 兜底：lf 追加/改写镜头缓推指令

**已删除的三项**（v6.77 起）：B 过肩正反打、C 情绪递进 + 焦点轮换、
以及 D 分支里按说话人删除「X位于…」位置分句。它们都是**编造或删除故事
事实**——谁的视角、什么情绪、画面聚焦谁、角色站在哪，原文都没有依据或
本就是事实。拆镜只应改变**镜头语言**，不该替故事做决定。
（见 docs/step01-temporal-writers-audit-2026-09-19.md 第三阶段、
  docs/step01-temporal-writers-implementation-review-2026-09-19.md 发现 7）

若两半仍高度相似（静态场景），由 `split_content_similarity` WARN 上报，
不再靠编造内容或删除事实掩盖。
"""
from __future__ import annotations

import re

FRAMING_LADDER: tuple[str, ...] = (
    "大远景", "远景", "全景", "中景", "中近景", "近景", "特写", "大特写",
)


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
    """就地差异化子镜的 prompt/ff_desc/lf_desc —— **只做镜头层**差异化.

    Args:
        orig: 原始（被拆）镜头 dict
        new_shot: 新构造的子镜 dict——就地改写 prompt/ff_desc/lf_desc
        part_i: 子镜序号（0 起）。0=保持原描述；≥1 景别逐级推近

    v6.77（第三阶段，evidence-gated）：**移除语义注入**。原先还会追加
    「过肩视角，越X的肩望向Y」「气氛骤紧 / 气氛剑拔弩张」「画面聚焦X
    （说话神情 / 聆听反应）」。这些是**编造的故事事实**——谁的视角、什么情绪、
    画面聚焦谁，原文都没有依据。拆镜只应改变**镜头语言**，不该替故事做决定
    （见 docs/step01-temporal-writers-audit-2026-09-19.md 第三阶段）。

    保留的两项都属镜头层：
      A. 景别按拆分深度推近（FRAMING_LADDER）
      D. ff≈lf 兜底：相似度 ≥0.85 时给 lf 补一条推镜指令

    若两半仍高度相似（静态场景），由 `split_content_similarity` WARN 上报，
    不再靠编造内容掩盖。
    """
    # ff/lf 先剥运镜短语（幂等保证：二次应用先剥掉上次追加的推镜再重加，
    # 结果收敛不漂移）
    ff = _strip_camera_push(new_shot.get("ff_desc", "") or "")
    lf = _strip_camera_push(new_shot.get("lf_desc", "") or "")

    # A. 景别按拆分深度推近
    steps = part_i
    prompt, framing = shift_framing(new_shot.get("prompt", "") or "", steps)
    ff, _ = shift_framing(ff, steps)
    lf, _ = shift_framing(lf, steps)

    # D. ff≈lf 终检（拆镜后 ff/lf 常共享长基底；相似度 ≥0.85 即破）
    if ff and lf:
        import difflib
        if difflib.SequenceMatcher(None, ff.rstrip("。"), lf.rstrip("。")).ratio() >= 0.85:
            # lf 补一条推镜指令——**镜头层**差异化
            lf = _upsert_camera_push(lf, framing or "近景")
            # v6.79（2026-09-20）：**删除**原先在此处的「构图一致性修复」。
            #
            # 旧实现在 lf≈ff 时按说话人前缀删除 lf_desc 里所有「X位于…」分句，
            # 理由是「lf 已推近，全景站位与之矛盾」。但推近镜头**并不证明角色
            # 不再位于画左**——那个位置仍是 frame_layout、轴线连续性和下一镜
            # 衔接需要的事实。删除它等于用相似度这个质量信号去删事实字段
            # （实测 `中景，张三位于画左，手持刀` 拆分后丢掉「张三位于画左」）。
            #
            # 正确处置：只保留景别/运镜词的结构化更新，不删任何人物、道具、
            # 姿态、位置分句；两半仍高度相似时由 `split_content_similarity`
            # WARN 上报并触发完整子镜重生。
            # 见 docs/step01-temporal-writers-implementation-review-2026-09-19.md 发现 7。

    new_shot["prompt"] = prompt
    new_shot["ff_desc"] = ff
    new_shot["lf_desc"] = lf
