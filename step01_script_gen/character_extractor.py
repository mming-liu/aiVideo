"""step01_script_gen.character_extractor - 角色提取模块

功能：
- extract_characters(): 从小说原文提取角色
- CHARACTER_EXTRACTION_PROMPT: 角色提取 prompt 模板
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import json
import re
from shared.character_bank import CharacterBank
from _text_gen import create_service, TextGenerationRequest


# ========================================================================
# 角色提取 prompt
# ========================================================================

CHARACTER_EXTRACTION_PROMPT = """
从以下小说原文中，提取所有出场人物。

已知角色库（若角色已在库中，请确认其出场，描述本章中的衣着变化，以及性别/年龄是否变化）：
{known_characters}

# 输出格式
只输出纯JSON，必要markdown代码块标记，不要任何前后缀文字。格式如下：
{{
  "characters": [
    {{
      "identifier": "人物名称（跨章节保持一致，如杨旭、张十三）",
      "gender": "male 或 female，表示性别",
      "age_range": "young(青年) / middle(中年) / old(老年)，表示大致年龄段",
      "static_features": "外貌、体型等不变特征（如：剑眉星目，黑发束冠，身姿挺拔）",
      "dynamic_features": "本场景服装、配饰等可变特征（如：素白轻袍，平定四方巾）",
      "is_visible": true,
      "is_new": true
    }}
  ]
}}

# 规则
- identifier 必须使用小说中的原名，跨章保持一致
- 同一个人物只出现一次
- gender: 根据外貌/声音/名字判断性别，male=男性，female=女性
- age_range: 根据外貌描述/身份判断年龄段，young=青年(20-35岁)，middle=中年(36-55岁)，old=老年(56岁以上)
- static_features 描述脸型、五官、发式、体型等不随场景变化的特征
- dynamic_features 描述服装、配饰、手持物品等随场景变化的特征
- 描述要具体可视化：不要用"英俊"，要写"剑眉星目、"
- is_new: 该人物是否不在已知角色库中
- 只提取主要角色（有名字、有对话、有动作的），忽略纯背景路人

# 小说原文
{novel_text}
"""


# ========================================================================
# 角色提取函数
# ========================================================================

def extract_characters(novel_text: str, bank: CharacterBank) -> list[dict]:
    """从小说原文提取角色， 已知角色只确认出场，新角色描述外貌."""
    known_ids = bank.get_all_identifiers()
    known_text = "（暂无已知角色）"
    if known_ids:
        lines = []
        for ident in known_ids:
            char = bank.get_character(ident)
            if char:
                sf = char.get("static_features", "")
                lines.append(f"  - {ident}: {sf}")
        if lines:
            known_text = "\n".join(lines)

    prompt = CHARACTER_EXTRACTION_PROMPT.format(
        known_characters=known_text,
        novel_text=novel_text,
    )
    service = create_service()
    req = TextGenerationRequest(
        system_prompt="你是小说人物分析专家，精确提取出场人物信息。只输出纯JSON，不要任何额外文字。",
        user_prompt=prompt,
        max_tokens=20000,
        temperature=0.3,
    )
    result = service.generate(req)
    if not result.success:
        print(f"[WARN] 角色提取失败: {result.error}")
        return []

    # 解析
    raw = result.text.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(raw[start:end + 1])
        else:
            print(f"[WARN] 角色提取 JSON 解析失败")
            return []

    chars = data.get("characters", [])
    print(f"[INFO] 提取到 {len(chars)} 个角色: {[c.get('identifier', '?') for c in chars]}")
    return chars