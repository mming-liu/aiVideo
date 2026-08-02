"""对话标记检测 — 供 timecode.py 与 shot_tts.py 共用（code-audit 08-01 #2）。

历史问题：旧版标记含半角冒号 `:` 和破折号 `——`，中文叙事散文里二者极常见，
导致大量"纯旁白"镜头被误判为"有对话"——timecode 走字数估时而非固定 4s，
shot_tts 仍旧生成配音，与"去旁白只留对话"的目标相悖。

现收紧为真实对话引号 + 全角冒号 `：`；半角冒号与破折号不再判定为对话。
"""


# 真正的对话标记：全角冒号 + 各类成对/全角引号
_DIALOGUE_MARKERS = (
    "：",          # 全角冒号（“他说：”）
    '"', "'",      # 半角引号
    "“", "”",      # 全角双引号
    "‘", "’",      # 全角单引号
    "「", "」",     # 直角双引号
    "『", "』",     # 直角单引号
)


def has_dialogue(text: str) -> bool:
    """判断文本是否含真实对话（引号或全角冒号）。

    精确版本：不含半角冒号 `:` 与破折号 `——`，避免叙事散文被误判为对话。
    """
    if not text:
        return False
    return any(marker in text for marker in _DIALOGUE_MARKERS)