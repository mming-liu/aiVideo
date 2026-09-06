"""F11. 景别词缺失回填约束（v6.62, 09-06）.

09-05/09-06 chapter_001 实测：13 镜 shot.prompt 全部无景别词（framing 字段
也是空串），LLM 收到镜头语言词库但不执行「画面描述 + 镜头景别」的字段要求——
后果链：
  - _check_shot_size_diversity 每轮报「景别多样性不足：1 种」
  - framing_jump / framing_diversity_v2 两个约束对空输入恒跳过（死亡约束）
  - 图片/视频模型拿不到景别指令 → 构图全靠 ff_desc 的文字密度猜

修法（确定性回填，不依赖 LLM regen）：
  prompt 无任何景别词时，按内容启发式推断景别并前置「[景别]」：
  - 空镜/环境词（空镜/水面/月亮/荷叶/风景，无角色词）→ 远景
  - 五官/局部词（双目/瞳孔/嘴唇/手指/刀尖+特写语境）→ 特写
  - 两人对话词（道：/问/答 + 同框）→ 中近景
  - 打斗/跳跃/奔跑（跃/扑/刺/窜/腾）→ 中景
  - 兜底 → 中景（硬下限，prompt 必须有景别指令）

景别词表 = storyboard_refs.SHOT_SIZES 短名（与 _shot_size_level 同源）。
幂等：prompt 已含任一景别词 → 跳过（前置的「[景别]」自身含景别词，天然幂等）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity

import storyboard_refs

# 官方景别短名 = SHOT_SIZES 每项首段（validation._shot_size_level 同口径），
# 单源派生，不再手抄副本（旧副本曾多出「近景」与源头漂移）。
# 「近景」不在官方 9 种内但属合法景别词：仅用于「已含景别词→跳过回填」判定；
# 推断返回值仍只用官方词表，避免注入官方外景别。
_FRAMING_WORDS = tuple(s.split(" ")[0] for s in storyboard_refs.SHOT_SIZES) + ("近景",)

# 空镜/环境词（无人物画面）
_EMPTY_SCENE_WORDS = ("空镜", "环境", "涟漪", "月光洒", "月色", "星光", "薄雾",
                      "水面荡漾", "静泊", "无人物")

# 局部特写词
_CLOSEUP_WORDS = ("瞳孔", "双目", "双眼", "眼神", "眼角", "嘴唇", "指尖", "手指",
                  "刀尖", "洇痕", "指纹", "睫毛", "露出双眼", "只露出")

# 对话词（含对话标点）
_DIALOGUE_WORDS = ("道：", "问道", "答道", "低喝", "质问", "喊话", "应答")

# 动作/打斗词
_ACTION_WORDS = ("跃", "扑", "刺", "窜", "腾", "冲", "挥", "砸", "踹",
                 "纵身", "飞掠", "扑出", "潜伏", "倒悬")


def _infer_framing(text: str, has_chars: bool) -> str:
    """从 prompt 内容推断景别（词表封闭可扩展）.

    v6.62: 有角色时动作/特写优先于环境词（shot4「月光洒…潜伏倒悬」实测被
    环境词抢先判远景——有角色镜环境只是背景，动作/五官才是构图主体）。
    """
    # 1) 局部特写（五官/武器尖端——无论有无角色）
    if any(w in text for w in _CLOSEUP_WORDS):
        return "特写"
    # 2) 对话 → 中近景
    if any(w in text for w in _DIALOGUE_WORDS):
        return "中近景"
    # 3) 动作/打斗 → 中景
    if any(w in text for w in _ACTION_WORDS):
        return "中景"
    # 4) 纯空镜（无角色 + 环境词）→ 远景
    if not has_chars and any(w in text for w in _EMPTY_SCENE_WORDS):
        return "远景"
    # 5) 有角色的环境互动 → 中远景
    if has_chars and any(w in text for w in _EMPTY_SCENE_WORDS):
        return "中远景"
    # 兜底：中景
    return "中景"


class FramingPrefixFillConstraint(Constraint):
    """F11. prompt 无景别词 → 按内容推断景别前置「[景别]」."""

    name = "framing_prefix_fill"
    severity = Severity.WARN
    fields = ("prompt",)
    skip_hook = False  # hook prompt 同样需要景别指令

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            prompt = str(shot.get("prompt") or "")
            if not prompt.strip():
                continue
            # 幂等：已有任一景别词 → 跳过
            if any(w in prompt for w in _FRAMING_WORDS):
                continue
            # 出镜角色（frame_layouts ff on_screen > char_layout > main_subject 非空镜）
            has_chars = False
            fl = shot.get("frame_layouts")
            if isinstance(fl, dict):
                has_chars = any(
                    isinstance(it, dict) and it.get("on_screen")
                    for it in (fl.get("ff") or [])
                )
            if not has_chars:
                has_chars = bool(shot.get("char_layout"))
            ms = str(shot.get("main_subject") or "")
            if not has_chars and ms and not any(w in ms for w in ("空镜", "环境", "无人物")):
                has_chars = True
            framing = _infer_framing(prompt, has_chars)
            new_prompt = f"[{framing}]{prompt}"
            shot["prompt"] = new_prompt
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index", idx),
                    action="prepend",
                    target="prompt",
                    before=prompt[:40],
                    after=new_prompt[:40],
                )
            )
        return repairs
