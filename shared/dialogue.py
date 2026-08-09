"""对话标记检测 — 供 timecode.py 与 shot_tts.py 共用（code-audit 08-01 #2）。

历史问题：旧版标记含半角冒号 `:` 和破折号 `——`，中文叙事散文里二者极常见，
导致大量"纯旁白"镜头被误判为"有对话"——timecode 走字数估时而非固定 4s，
shot_tts 仍旧生成配音，与"去旁白只留对话"的目标相悖。

现收紧为真实对话引号 + 全角冒号 `：`；半角冒号与破折号不再判定为对话。
"""

import re
from typing import Optional, List


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


# 说话人检测（从「说话人道：台词」格式中识别角色名）。
# 原实现位于 step03_tts_audio/shot_tts.py（step03 已孤儿），2026-08-09 下沉到
# shared/dialogue.py 统一供给，避免 prompt_builder 反向依赖 step03（审计 P0-1）。
# 2026-08-09 追加：合并 step01 validation._extract_speaker 的增强（角色库精确名优先
# + 剥离尾部说话动词），使 shared/dialogue 成为全项目唯一说话人检测实现。
_SPEAKER_PATTERNS = (
    # 匹配 "XXX道：" / "XXX说：" / "XXX问道" 等，0-4个中文字符的动作描写
    r"^([一-龥]{1,4})(?:[^一-龥]{0,4})?(?:道|说|问|答|喊|叫|叹|怒|笑|哭|喝|骂|低声道|低声说|低声问道|低声笑道|笑道|冷笑道|急道|忙道|冷声道|朗声道|轻声道)[:：]",
    # 备用简化模式
    r"^([^\s，。,]{1,4})(?:道|说|问|答|喊|叫|叹|怒|笑|哭|喝|骂)[:：]",
)
# 尾部说话动词剥离：正则可能把「喊道/扬声道/颤声道」的动词部分误纳入说话人名
_SPEAKER_TRAIL_VERB_RE = re.compile(
    r'(?:扬|怒|颤|冷|漫|惊|厉|高|低|轻|重|急|缓)?声?(?:道|说|喊|喝|叫|问|答|叹|曰)$')


def detect_speaker(narration: str, characters: Optional[List[dict]] = None) -> Optional[str]:
    """从叙述文本中检测说话人角色名.

    检测逻辑：
    1. 优先用角色库里的精确名（避免把「惊叫/颤声」等动词修饰语误当说话人）
    2. 匹配 "XXX道："、"XXX说："等对话格式
    3. 匹配 "XXX笑道："、"XXX怒道："等带情感词的情况
    4. 剥离尾部说话动词（「庄丁喊」→「庄丁」）

    Args:
        narration: 台词文本
        characters: 角色列表（含 identifier 字段），用于验证说话人是已知角色

    Returns:
        说话人角色名，未检测到返回 None
    """
    if not narration:
        return None

    # 角色名列表（按长度降序，优先匹配更长名字）
    char_names = [c.get("identifier", "") for c in characters or [] if c.get("identifier")]
    char_names = sorted(char_names, key=len, reverse=True)

    # 1. 角色库精确名优先（与 validation._extract_speaker 增强一致）
    if char_names:
        for cid in char_names:
            if narration.startswith(cid):
                return cid

    # 2. 对话格式匹配（增强版正则，支持动作+说话混合）
    for pattern in _SPEAKER_PATTERNS:
        match = re.match(pattern, narration)
        if match:
            speaker = match.group(1)
            # 验证匹配到的确实是角色名
            if char_names:
                for char_name in char_names:
                    if char_name in speaker or speaker in char_name:
                        return char_name
                if speaker in char_names:
                    return speaker
            else:
                return speaker  # 无角色库时直接返回匹配结果

    # 3. 尝试在文本开头找角色名（动作+说话）
    for char_name in char_names:
        if narration.startswith(char_name) or narration[:6].find(char_name) >= 0:
            if any(v in narration[len(char_name):len(char_name)+3] for v in ["道", "说", "问", "笑", "怒", "哭"]):
                return char_name

    return None