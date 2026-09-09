"""step01_script_gen.object_extractor - 场景物体尺寸提取模块

功能：
- extract_scene_objects(): 从小说原文提取反复出现的空间物体（船只/马车/厅堂等）
  ，并依据原文上下文推导其合理尺寸/容量/结构，镜像 character_extractor 模式。

为什么要做这一步（背景）：
- step01 的规则 25 曾把船规格硬编码为「长约四米、可容两三人」，导致图片模型
  把船画成只能装 2 人的小舢板。
- 靠硬编码无法覆盖各种物体（下次遇到真的大船/小船又会错）。改为从原文上下文
  推导：让 LLM 综合「空间活动线索 + 物理验证（稳性/容量/历史形制）」给出尺寸，
  再持久化到 SceneObjectsBank，跨章节统一引用。
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import json

from shared.scene_objects_bank import SceneObjectsBank
from _text_gen import create_service, TextGenerationRequest, config as text_config


# ========================================================================
# 场景物体提取 prompt
# ========================================================================

OBJECT_EXTRACTION_PROMPT = """
从以下小说原文中，提取**反复出现或承担关键剧情的空间物体**（船只、马车、轿子、厅堂、院落等），并依据原文上下文推导其**合理尺寸、容量与结构**。

已知物体库（若物体已在库中，请确认其出场；若本章对其尺寸/结构有补充，给出修正后的推导）：
{known_objects}

# 提取范围
- **必须提取**：同一场景反复出现、或剧情关键的空间物体（如主角乘的小舟、载人的马车）
- **可忽略**：纯背景点缀物、一次性出现且无空间意义的物件（如一个碗、一把伞、一盏灯）

# 尺寸推导规则（最重要）——必须综合原文上下文分析，禁止凭空编造
从原文提取所有与物体相关的**空间线索**，逐条做物理验证：
1. **容量线索**：同时在场的人数、装载的器具（如"杨旭坐船头、听香在舱中生炭炉"→ 至少容纳2人+炉具+酒菜）
2. **活动空间**：人物在其上做什么动作（盘膝坐、站立烹煮、蹲踞船舷、张十三跃上压得摇晃）→ 需多大甲板/舱面
3. **环境限制**：原文的环境约束（如"小船完全隐在荷花丛中"→ 船身窄、船舷低；"距岸一丈停靠"→ 吃水浅）
4. **物理验证**：排除两种极端——过小（独木舟般，容不下活动与人数）与过大（画舫般，窄航道无法穿行、人登船不可能"猛地一沉"）
5. **形制参考**：结合物体类别与历史常见形制（如古风湖湾游船=窄身舴艋舟/采莲舟，无篷；载客马车=两轮/四轮），给出合理 subtype
6. 单位统一用**米**，估算到小数点后一位；每条都要有原文依据，写进 size_reasoning

# 输出格式
只输出纯JSON，不要任何 markdown 代码块标记或前后缀文字。格式如下：
{{
  "scene_objects": [
    {{
      "identifier": "物体名称（跨章节保持一致，如：照月湾小舟）",
      "category": "船",
      "subtype": "窄身无篷舴艋舟",
      "length_m": 7.0,
      "width_m": 1.5,
      "height_m": 0.5,
      "capacity": "可容3-4人 + 炭炉酒菜",
      "structure": "船头/中舱/船梢三段；船头可盘膝坐、中舱敞舱可站立烹煮、船梢收窄",
      "consistent_terms": ["这条舴艋舟", "窄身小舟"],
      "size_reasoning": "原文'小船完全隐在荷花丛中'→船身窄、船舷低；'张十三跃上压得小船剧烈摇晃'→排除9米画舫；'听香在舱中生炭炉'→中舱可站立；综合推导长7m宽1.5m",
      "is_new": true
    }}
  ]
}}

# 规则
- identifier 用物体在原文中的称呼，跨章保持一致（如"照月湾小舟"、"青石马车"）
- category 取值建议：船/车/轿/厅堂/院落/其它
- consistent_terms：跨镜头统一称呼，全脚本必须用同一规格词；数组首个元素为"首次出现写全规格"的推荐写法
- size_reasoning：必须引用原文具体句子作为依据，说明如何排除过小与过大
- is_new: 该物体是否不在已知物体库中
- 只提取主要场景物体（有明确空间占位/反复出现），忽略纯背景

# 小说原文
{novel_text}
"""


# ========================================================================
# 场景物体提取函数
# ========================================================================

def extract_scene_objects(novel_text: str, bank: SceneObjectsBank) -> list[dict]:
    """从小说原文提取场景物体及其尺寸推导.

    Args:
        novel_text: 小说原文
        bank: SceneObjectsBank 实例，用于提供已知物体（已有尺寸）做修正/确认

    Returns:
        list[dict]: 提取到的场景物体列表；失败返回 []（不阻断主流程）
    """
    known_ids = bank.get_all_identifiers()
    known_text = "（暂无已知物体）"
    if known_ids:
        lines = []
        for ident in known_ids:
            obj = bank.get_object(ident)
            if obj:
                parts = [f"  - {ident}"]
                if obj.get("subtype"):
                    parts.append(f"形制:{obj['subtype']}")
                if obj.get("length_m") is not None:
                    parts.append(
                        f"尺寸:{obj['length_m']}m×{obj.get('width_m', '?')}m"
                        f"×{obj.get('height_m', '?')}m"
                    )
                if obj.get("capacity"):
                    parts.append(f"容量:{obj['capacity']}")
                if obj.get("structure"):
                    parts.append(f"结构:{obj['structure']}")
                lines.append("，".join(parts))
        if lines:
            known_text = "\n".join(lines)

    prompt = OBJECT_EXTRACTION_PROMPT.format(
        known_objects=known_text,
        novel_text=novel_text,
    )
    service = create_service()
    from _text_gen.deepseek_schema import SCENE_OBJECTS_SCHEMA
    req = TextGenerationRequest(
        system_prompt="你是古装剧场景设计专家，擅长依据原文上下文推导场景物体的合理尺寸、容量与结构，保证画面物理可信。只输出纯JSON，不要任何额外文字。",
        user_prompt=prompt,
        # v6.62.6（09-09）20000 → TEXT_EXTRACT_MAX_TOKENS（默认 6000）：
        # **上限不是"给足余量"，而是"给退化封顶"**。
        # 09-08 实测本调用正常输出仅 1039 tokens（4 个物体，含 size_reasoning），
        # 而 4 次跑批里 3 次 finish_reason=length 卡在 completion_tokens=20000
        # ——即模型偶发进入重复退化、把上限当目标一路写到顶，单次烧 3m49s
        # （~87 tok/s）后整份作废。6000 = 实测需求 5.8x 余量，正常输出不受影响，
        # 但退化的代价从 230s/20k tokens 降到 ~70s/6k tokens。
        # 走配置而非字面量：物体特别多的章节可在 .env.content 调高，不改代码。
        # 注意：这**不是**退化的根因修复（根因在采样端，见下），是给它封顶。
        # 未改采样参数：temperature=0.2 + 全局 top_p=0.2 是极贪心解码，正是
        # 重复退化的经典触发条件；但 v6.41 刻意收窄以治结构漂移，改动波及所有
        # 文本调用（含 deepseek 分镜生成），不在本次范围内单方面动。
        max_tokens=text_config.TEXT_EXTRACT_MAX_TOKENS,
        temperature=0.2,
        json_schema=SCENE_OBJECTS_SCHEMA,
        # 列表型提取：截断可由 _truncate_fix 抢救完整物体，不该整份丢弃
        allow_truncated=True,
    )
    result = service.generate(req)
    if not result.success:
        print(f"[WARN] 场景物体提取失败: {result.error}")
        return []
    if result.truncated:
        # 截断已放行，但必须显式告知：下面「提取到 N 个」会少报，静默少报正是
        # 09-08 那串问题的共同点
        print("[WARN] 场景物体提取输出被截断，仅保留已完整的物体（可能少于实际）")

    # 解析：走 shared json_repair 单源（09-06 实测 agent_plan 响应
    # finish_reason=length 截断，旧手写裸 json.loads 断尾即整体失败 →
    # 「预提取到 0 个场景物体」、物体库零增量；safe_parse_json 的
    # _truncate_fix 兜底会丢弃未完成尾对象、保留完整对象）
    from shared.utils.json_repair import safe_parse_json_object
    try:
        data = safe_parse_json_object(result.text)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[WARN] 场景物体提取 JSON 解析失败（含截断恢复后仍失败）: {e}")
        return []

    objs = data.get("scene_objects", [])
    # 截断残渣过滤（v6.62.6，09-09）：json_repair 抢救截断输出时，尾部那个没写完
    # 的物体会被削成 `{}`（截断落在**下一个 key** 上时，整对 key/value 一起丢）。
    # 无 identifier 的条目对下游毫无意义（bank 以 identifier 为主键，打印成 '?'），
    # 直接滤掉，别让抢救行为反过来往库里塞空壳。
    _raw_n = len(objs)
    objs = [o for o in objs if isinstance(o, dict) and str(o.get("identifier") or "").strip()]
    if len(objs) < _raw_n:
        print(f"[WARN] 场景物体提取丢弃 {_raw_n - len(objs)} 个无 identifier 的残缺条目（截断残渣）")
    # B3 修复（08-10）：物体尺寸合理性钳制——LLM 可能推「房间长500米」这类离谱值，
    # 原样入库入帧会导致画面比例崩坏（船画成巨轮/房间画成广场）。按 category 分档
    # 钳制到合理范围，越界值 WARN 并修正。
    for o in objs:
        _sanitize_object_dimensions(o)
    print(f"[INFO] 提取到 {len(objs)} 个场景物体: {[o.get('identifier', '?') for o in objs]}")
    return objs


# ========================================================================
# 物体尺寸合理性钳制（B3 修复 08-10）
#
# LLM 从原文推导尺寸时可能给出离谱值（房间长500m、小舟长0.5m），无任何代码校验
# 就入库入帧，画面比例直接崩坏。这里按 category 分档定义合理范围，越界钳制并 WARN。
# 范围参考：古风湖湾游船 2-15m、马车 2-8m、轿子 1-3m、厅堂 3-60m、院落 5-200m。
# ========================================================================

# category → (长度合理范围m, 宽度合理范围m, 高度合理范围m)
_OBJECT_DIM_RANGES = {
    "船": ((2, 15), (0.8, 5), (0.3, 3)),
    "车": ((2, 8), (1, 3), (1.5, 3.5)),
    "轿": ((1, 3), (0.8, 2), (1.5, 2.5)),
    "厅堂": ((3, 60), (3, 40), (3, 15)),
    "院落": ((5, 200), (5, 150), (2, 20)),
    "其它": ((0.5, 50), (0.5, 30), (0.5, 20)),
}
# category 兜底（未命中分档时用通用范围）
_OBJECT_DIM_FALLBACK = ((0.5, 50), (0.5, 30), (0.5, 20))


def _clamp_dim(value, lo, hi, field, ident, category):
    """钳制单个尺寸值到 [lo, hi]，越界时 WARN 并返回钳制值."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        print(f"[WARN] 场景物体 {ident}（{category}）{field} 非数值: {value}，跳过")
        return None
    if v < lo or v > hi:
        print(f"[WARN] 场景物体 {ident}（{category}）{field}={v}m 超出合理范围"
              f"[{lo}, {hi}]，钳制为 {max(lo, min(hi, v))}m")
        return max(lo, min(hi, v))
    return v


def _sanitize_object_dimensions(obj: dict) -> None:
    """按 category 钳制物体 length_m/width_m/height_m 到合理范围.

    category 未命中分档时用通用范围兜底。不合理的数值（负值/非数值）置 None
    （下游 _inject_object_size_anchor 对 None 跳过，不注入）。
    """
    category = (obj.get("category") or "").strip()
    ranges = _OBJECT_DIM_RANGES.get(category, _OBJECT_DIM_FALLBACK)
    ident = obj.get("identifier", "?")
    for field, (lo, hi) in zip(("length_m", "width_m", "height_m"), ranges):
        v = obj.get(field)
        if v is None:
            continue
        clamped = _clamp_dim(v, lo, hi, field, ident, category)
        obj[field] = clamped
