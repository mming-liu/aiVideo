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
- **static_features 必须包含「脸型」描述，按角色气质选型（跨章锁脸防变脸）**：
  猫系脸(细长眼/眼尾上挑/清冷警觉)→刺客/杀手/心思深沉；鹿系脸(大而清澈的眼/湿润眼神/温柔无辜)→白月光女主/弱质被保护者；
  狐系脸(眼尾上挑/尖下巴/狡黠魅惑)→妖妃/宠姬/反派美人；天鹅脸(修长/高鼻梁/清晰下颌线/贵气)→名门闺秀/权贵之女；
  雀斑脸(自然雀斑/真实素颜/野生感)→山野/质朴配角
  示例：'鹿系脸，大而清澈的眼睛，湿润眼神，鹅蛋脸，黑发挽髻，身姿袅娜'
- is_new: 该人物是否不在已知角色库中
- 只提取主要角色（有名字、有对话、有动作的），忽略纯背景路人

# 小说原文
{novel_text}
"""


# 角色名结尾可剥除的标点/称谓残余（LLM 常把「杨旭。」「杨旭，」的句读带进 identifier）
_TRAILING_NOISE = "。，、；；：:！!？?、 　"

# 全角→半角映射（B2 修复：LLM 可能输出全角字母/数字，导致「李A」「李A」两个名字）
_FULLWIDTH_MAP = {
    ch: chr(ord(ch) - 0xFEE0) for ch in "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
}


def _normalize_identifier(name: str) -> str:
    """角色名规范化（B2 修复 08-11）：strip + 剥尾部标点 + 全角字母归一.

    LLM 从原文摘取角色名时常把句读标点带进 identifier（「杨旭。」「杨旭，」），
    且可能用全角字母（「李Ａ」），导致同一角色被注册成两个独立角色
    （character_bank 按 identifier 精确 key 存）。这里归一化后再判重。
    """
    if not name:
        return name
    out = str(name).strip(_TRAILING_NOISE)
    for full, half in _FULLWIDTH_MAP.items():
        if full in out:
            out = out.replace(full, half)
    return out


# 相似名去重（B2 修复）：完全一致（归一化后）视为同一角色合并；
# 近似名（如「杨旭」vs「杨旭.」）在剥尾部标点后已一致，自然合并。
def _dedup_characters(chars: list[dict]) -> list[dict]:
    """按归一化后的 identifier 去重（B2 修复 08-11）.

    合并策略：保留首个出现条目，把重复条目的 static/dynamic_features 合并
    （两者互补时），避免「杨旭」「杨旭。」被注册成双角色后：
    - character_bank 出现两个 key（肖像/特征分裂）
    - prompt 锚点 [杨旭: ...] 只命中一个，另一个永远无肖像
    """
    seen: dict[str, dict] = {}
    order: list[str] = []
    for c in chars:
        ident = (c.get("identifier") or "").strip()
        norm = _normalize_identifier(ident)
        if not norm:
            continue
        c["identifier"] = norm
        if norm in seen:
            existing = seen[norm]
            # 合并 static/dynamic（已有则保留，新条目补充空字段）
            for field in ("static_features", "dynamic_features"):
                cur = existing.get(field, "")
                new = c.get(field, "")
                if new and new not in str(cur):
                    existing[field] = (str(cur) + "，" + new).strip("，")
            continue
        seen[norm] = c
        order.append(norm)
    # 保持原顺序返回
    return [seen[n] for n in order]


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
    from _text_gen.deepseek_schema import CHARACTERS_SCHEMA
    req = TextGenerationRequest(
        system_prompt="你是小说人物分析专家，精确提取出场人物信息。只输出纯JSON，不要任何额外文字。",
        user_prompt=prompt,
        max_tokens=20000,
        temperature=0.3,
        json_schema=CHARACTERS_SCHEMA,
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
    # B2 修复（08-11）：角色名规范化 + 去重（strip/剥尾部标点/全角归一/相似合并）。
    # 此前「杨旭」「杨旭。」被注册成双角色，character_bank 双 key、肖像分裂。
    chars = _dedup_characters(chars)
    _ensure_face_shape(chars, bank)
    print(f"[INFO] 提取到 {len(chars)} 个角色: {[c.get('identifier', '?') for c in chars]}")
    return chars


# ========================================================================
# 脸型词补齐（v1.1）
#
# 脸型库（.aiVideo/skills/portrait-mastery §2）要求每个角色 static_features 含脸型词，
# 跨章锁脸防变脸。但角色提取 prompt 的脸型规则只对新角色生效——已知角色
# 首次注册时 static_features 已锁定（CharacterBank update_dynamic_features
# 注释明确 static 不覆盖）。这里按角色名/特征关键词确定性映射，给已知角色
# 的 static_features 补脸型词（若已含脸型词则跳过，幂等）。
# ========================================================================

# 角色名/特征关键词 → 脸型词（气质→脸型映射，与 portrait-mastery §2 face-shapes 一致）
_FACE_SHAPE_MAPPING = (
    # 角色名精确匹配（高优先级，按名判断角色气质）
    ("听香", "鹿系脸，圆润柔和的脸型，大而清澈的眼睛，湿润眼神"),
    ("刺客", "猫系脸，细长杏眼，眼尾微微上扬，清冷警觉的眼神"),
    ("肖管事", "方脸，浓眉，目光精明"),

    # 特征关键词兜底（低优先级，新角色也能命中）
    ("少女", "鹿系脸，圆润柔和的脸型，大而清澈的眼睛"),
    ("女子", "鹅蛋脸，柳叶眉，眉眼含情"),
    ("老妇", "皱纹遍布，面色蜡黄"),
    ("杀手", "猫系脸，细长杏眼，眼尾上挑，冷冽"),
    ("丫鬟", "圆脸，眉眼清秀，稚气未脱"),
    ("妖妃", "狐系脸，眼尾明显上挑，尖下巴，妩媚"),
    ("宠姬", "狐系脸，眼尾上挑，尖下巴，娇媚"),
    ("闺秀", "天鹅脸，修长脸型，高鼻梁，清晰下颌线"),
    ("村姑", "雀斑脸，自然雀斑，真实素颜，清冷文艺"),
)

# 脸型词命中检测（static_features 已含脸型词则跳过）
_FACE_SHAPE_HINTS = (
    "系脸", "猫系", "鹿系", "狐系", "天鹅脸", "雀斑脸", "鹅蛋脸",
    "瓜子脸", "圆脸", "方脸", "长脸", "小脸", "菱形脸", "心形脸",
    "国字脸",  # B1 默认 male 脸型词，须进 HINTS 防二次叠加
)

# 默认脸型兜底（B1 修复 08-10）：新角色名字/特征关键词都不在 _FACE_SHAPE_MAPPING、
# LLM 又漏写脸型词时，按性别给一个确定性默认脸型——否则该角色 static_features
# 无脸型词就永久固化（register_character 锁定 static），跨章无法补救，肖像变脸。
# 值=脸型核心词（合并时只取核心词，避免与已有 static 描述重复堆叠）。
_DEFAULT_FACE_SHAPES = {
    "male": "国字脸",
    "female": "鹅蛋脸",
    "": "方脸",
}


def _ensure_face_shape(chars: list[dict], bank: CharacterBank) -> None:
    """给角色 static_features 补脸型词（已知角色复用 static_features 时）.

    - 已含脸型词（_FACE_SHAPE_HINTS）→ 跳过（幂等）
    - 名字匹配 _FACE_SHAPE_MAPPING → 用映射脸型词
    - 名字不匹配但特征关键词命中 → 追加映射脸型词
    - **都未命中（B1 兜底）→ 按性别补默认脸型**（male→国字脸、female→鹅蛋脸）
    只改内存 chars（script_result.json 的 characters），不改 CharacterBank
    static_features（首次锁定语义保留）；新角色由提取 prompt 规则直接生成，
    兜底在 extract_characters→_ensure_face_shape 于 register_character 之前生效。
    """
    for c in chars:
        sf = (c.get("static_features") or "").strip()
        ident = (c.get("identifier") or "").strip()
        if any(h in sf for h in _FACE_SHAPE_HINTS):
            continue  # 已有脸型词
        # 名字精确匹配
        matched = False
        for name, face in _FACE_SHAPE_MAPPING:
            if ident == name:
                c["static_features"] = _merge_face_shape(sf, face)
                matched = True
                break
        if matched:
            continue
        # 特征关键词兜底（名字未匹配时）
        for name, face in _FACE_SHAPE_MAPPING:
            if name != ident and name in f"{ident}{sf}":
                c["static_features"] = _merge_face_shape(sf, face)
                break
        else:
            # B1 最终兜底：名字/特征都不匹配 → 按性别补默认脸型
            gender = (c.get("gender") or "").strip().lower()
            default_face = _DEFAULT_FACE_SHAPES.get(gender, _DEFAULT_FACE_SHAPES[""])
            c["static_features"] = _merge_face_shape(sf, default_face)


def _merge_face_shape(sf: str, face: str) -> str:
    """把脸型词合并进 static_features（幂等、去重）."""
    if not sf:
        return face
    if face in sf:
        return sf
    # 脸型词放最前（人脸优先）
    return f"{face}，{sf}"