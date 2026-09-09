"""step01_script_gen.asset_extractor - 场景/道具资产提取模块

功能：
- extract_scene_assets(): 从分镜脚本提取主要场景（大场景建立镜头 + 近景功能场景
  描述），并锚定地点/时间段/天气/色调，镜像 object_extractor 模式。
- extract_props(): 从分镜脚本提取核心道具（外观描述），供道具资产图生成。

方法论（references/ai-scene-prop-asset-guide.md）：
  1. 大场景建立镜头：确定地理环境、时间背景、天气色调和空间关系，所有镜头以此为统一参考
  2. 近景功能场景：从大场景派生，保证季节/光线/年代/风格一致
  3. 剧本道具提取：提取故事中的核心道具（如背篓）
  4. 道具资产图：单独生成规范的白底道具资产图，为后续贴图与视频生成提供稳定基础

与 object_extractor 的分工：
- SceneObjectsBank 锚定空间物体的尺寸/容量/结构（文字，物理可信）
- 本模块锚定场景的建立镜头/近景描述（供资产生成图 + r2v 场景参考）
  与道具的外观描述（供道具资产图生成）

失败不阻断主流程（返回空列表）。
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import json
import re

from _text_gen import create_service, TextGenerationRequest, config as text_config


# ========================================================================
# 场景资产提取 prompt
# ========================================================================

SCENE_ASSET_EXTRACTION_PROMPT = """
从以下分镜脚本中，提取**承担主要剧情空间的大场景**（如：河湾船只、府邸大门、山道关隘），并为每个场景生成大场景建立镜头描述与近景功能场景描述。

已知场景库（若场景已在库中，请确认其出场；若本章对场景有补充，给出修正后的描述）：
{known_scenes}

# 提取范围
- **必须提取**：承担主要剧情的空间（镜头数≥3 或关键剧情发生地）；同一地点按 scene/location 聚合
- **可忽略**：一次性过渡空镜、纯背景点缀（如单纯一处田埂远景只出现 1-2 镜）

# 场景描述要求（最重要）——必须先聚合再描述，禁止逐镜头罗列
1. **大场景建立镜头（wide_desc）**：站在"航拍/全景建立镜头"视角描述地理环境、时间背景、天气色调、空间关系；画面中不得出现任何人物、不得出现道具特写。必须覆盖：地理环境（山地/湖湾/街市/庭院…）、空间纵深（前景-中景-远景层次）、季节光线（时间/天气/主光方向）、年代风格（建筑/植被/器物年代感）
2. **近景功能场景（near_desc）**：同一地点"人物进入环境后"的镜头需求，如玉米地内部两行植株之间的通道。必须与 wide_desc 保持相同的季节、时间、天气、色调、光线方向、年代风格，明确写成"与大场景属于同一个地点"，不得变成另一处场景
3. 场景内出现的主要人物只影响"该场景能承载的活动"（如船只类场景需能容人、庭院需有路径），不要写进画面描述

# 输出格式
只输出纯JSON，不要任何 markdown 代码块标记或前后缀文字。格式如下：
{{
  "scene_assets": [
    {{
      "identifier": "场景名称（跨章节保持一致，使用原文中的地点称谓，如：<原文章节中的地点>）",
      "location": "物理地点（如：<原文章节中的具体地理位置>）",
      "time_of_day": "初秋上午",
      "weather": "轻薄云层，阳光柔和",
      "color_tone": "自然温暖朴素，轻微空气透视",
      "wide_desc": "超广角全景建立镜头描述，画面中不得出现人物……",
      "near_desc": "同一地点的内部/近景功能场景描述，与大场景保持季节/光线/年代/风格一致，画面中不得出现人物……",
      "consistent_terms": ["<原文章节中的地点核心词>"],
      "is_new": true
    }}
  ]
}}

# 规则
- identifier 用场景在原文中的称呼，跨章保持一致
- wide_desc 必须明确"画面中不得出现人物"；near_desc 同样禁人（近景场景是"人物可进入"的功能空间，不是人物特写）
- 时间段/天气/色调要与脚本中该场景多数镜头的描述一致，不一致时以多数为准
- is_new: 该场景是否不在已知场景库中

# 分镜脚本
{script_json}
"""


# ========================================================================
# 道具提取 prompt
# ========================================================================

PROP_EXTRACTION_PROMPT = """
从以下分镜脚本中，提取**承担关键剧情、反复出现或对人物形象有锚定作用的核心道具**（如：背篓、佩剑、纸伞、灯笼），并为每个道具生成外观描述，供道具资产图生成。

已知道具库（若道具已在库中，请确认其出场；若本章对外观有补充，给出修正后的描述）：
{known_props}

# 提取范围（v6.40 08-22 收紧，禁止 LLM 幻觉）
- **必须提取**：分镜脚本中**实际出现**的道具（prompt/lf_desc/ff_desc/motion_desc/main_subject/scene/location 任一字段能找到对应名）
- **严禁提取**（LLM 幻觉高发区）：
  - "可能用到" / "潜在场景" / "宴席可能用" / "厨房常见" 等推测性道具
  - 完整食物清单（活虾/嫩藕/肥鸡/嫩羊/糕点/酒菜/餐具等）——除非分镜某镜**明确**写到
  - 与本章剧情/场景/人物完全无关的"装饰类"道具
- **可忽略**：纯背景陈设、一次性出现且无剧情意义的物件（如一张普通木桌、一棵树）

# 外观描述要求（最重要）——供白底道具资产图生成，必须具体到可画的细节
1. **材质与工艺**：竹编/铸铁/丝绸/皮革…，编织纹理/锻造痕迹/缝线方式
2. **颜色与新旧**：主色、配色、使用痕迹（磨损/包浆/褪色/划痕），不要"崭新完美"
3. **结构与尺寸**：整体形制（上宽下窄/圆口/提梁…）、大致尺寸（与人的比例关系）、可辨识的部件（背带/剑鞘/挂钩）
4. **年代感**：符合故事时代背景（90年代农村/古风）的旧物特征

# 输出格式
只输出纯JSON，不要任何 markdown 代码块标记或前后缀文字。格式如下：
{{
  "props": [
    {{
      "identifier": "道具名称（跨章节保持一致，如：竹编背篓）",
      "category": "背具",
      "appearance": "天然竹条手工编织，上口略宽、下部略窄，浅黄褐色与深褐色交错，表面自然竹节与轻微磨损，配褪色深蓝灰色旧布背带……",
      "consistent_terms": ["背篓", "竹篓"],
      "is_new": true
    }}
  ]
}}

# 规则
- identifier 用道具在原文/脚本中的称呼，跨章保持一致
- appearance 必须具体到材质/颜色/结构/新旧痕迹，禁止抽象形容（如"看起来老旧"）
- 只提取主要道具（有明确剧情/空间占位/反复出现），忽略纯背景
- is_new: 该道具是否不在已知道具库中
- **强约束（兜底）**：每个 prop 的 identifier 或 consistent_terms **必须**能在分镜脚本的 6 字段（prompt/lf_desc/ff_desc/motion_desc/main_subject/scene/location）任一找到原文匹配。匹配不到的就是幻觉，必须丢弃。

# 分镜脚本
{script_json}
"""


# ========================================================================
# 场景资产提取函数
# ========================================================================

def extract_scene_assets(script: dict, bank) -> list[dict]:
    """从分镜脚本提取主要场景及其大场景/近景描述.

    Args:
        script: script_result.json（分镜脚本）
        bank: SceneAssetsBank 实例，用于提供已知场景（已有描述）做修正/确认

    Returns:
        list[dict]: 提取到的场景资产列表；失败返回 []（不阻断主流程）
    """
    known_text = _format_known_scenes(bank)
    prompt = SCENE_ASSET_EXTRACTION_PROMPT.format(
        known_scenes=known_text,
        script_json=_script_to_json(script),
    )
    service = create_service()
    from _text_gen.deepseek_schema import SCENE_ASSETS_SCHEMA
    req = TextGenerationRequest(
        system_prompt="你是影视美术指导，擅长从分镜脚本中抽象主要场景，生成电影级大场景建立镜头与近景功能场景描述，保证场景统一参考与空间逻辑可信。只输出纯JSON，不要任何额外文字。",
        user_prompt=prompt,
        # v6.62.6（09-09）20000 → TEXT_EXTRACT_MAX_TOKENS（默认 6000）：给偶发重复
        # 退化封顶（实测正常输出 818 tokens）。场景多的章节可在 .env.content 调高。
        # 不开 allow_truncated：裸 json.loads 无列表项抢救能力，理由同 character_extractor。
        max_tokens=text_config.TEXT_EXTRACT_MAX_TOKENS,
        temperature=0.2,
        json_schema=SCENE_ASSETS_SCHEMA,
    )
    result = service.generate(req)
    if not result.success:
        print(f"[WARN] 场景资产提取失败: {result.error}")
        return []

    raw = result.text.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            print(f"[WARN] 场景资产提取 JSON 解析失败")
            return []
        try:
            data = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            print(f"[WARN] 场景资产提取 JSON 解析失败")
            return []

    assets = data.get("scene_assets", [])
    print(f"[INFO] 提取到 {len(assets)} 个场景资产: {[a.get('identifier', '?') for a in assets]}")
    return assets


# ========================================================================
# 道具提取函数
# ========================================================================

def extract_props(script: dict, bank) -> list[dict]:
    """从分镜脚本提取核心道具及外观描述.

    Args:
        script: script_result.json（分镜脚本）
        bank: PropAssetsBank 实例，用于提供已知道具（已有外观）做修正/确认

    Returns:
        list[dict]: 提取到的道具列表；失败返回 []（不阻断主流程）
    """
    known_text = _format_known_props(bank)
    prompt = PROP_EXTRACTION_PROMPT.format(
        known_props=known_text,
        script_json=_script_to_json(script),
    )
    service = create_service()
    from _text_gen.deepseek_schema import PROPS_SCHEMA
    req = TextGenerationRequest(
        system_prompt="你是影视道具师，擅长从分镜脚本中提取核心道具并给出可绘画的具体外观描述，用于白底道具资产图生成。只输出纯JSON，不要任何额外文字。",
        user_prompt=prompt,
        # v6.62.6（09-09）12000 → TEXT_EXTRACT_MAX_TOKENS（默认 6000）：同上封顶
        # （实测正常输出 784 tokens）。
        max_tokens=text_config.TEXT_EXTRACT_MAX_TOKENS,
        temperature=0.2,
        json_schema=PROPS_SCHEMA,
    )
    result = service.generate(req)
    if not result.success:
        print(f"[WARN] 道具提取失败: {result.error}")
        return []

    raw = result.text.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            print(f"[WARN] 道具提取 JSON 解析失败")
            return []
        try:
            data = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            print(f"[WARN] 道具提取 JSON 解析失败")
            return []

    props = data.get("props", [])
    # v6.40 08-22：出口过滤 LLM 幻觉道具——每个 prop 的 identifier 或 consistent_terms
    # 必须在分镜脚本 7 字段（prompt/lf_desc/ff_desc/motion_desc/main_subject/scene/location）
    # 任一出现，否则视为幻觉丢弃
    props = _filter_props_against_script(props, script)
    print(f"[INFO] 提取到 {len(props)} 个道具资产: {[p.get('identifier', '?') for p in props]}")
    return props


def _filter_props_against_script(props: list, script: dict) -> list:
    """v6.40 08-22：出口过滤 LLM 幻觉道具（兜底，prompt 强约束失效时仍能拦住）.

    规则：每个 prop 的 identifier 或 consistent_terms 任一必须能在分镜脚本 7 字段
    （prompt/lf_desc/ff_desc/motion_desc/main_subject/scene/location）找到原文匹配。
    匹配不到 = LLM 幻觉 = 丢弃。

    例外：first_chapter 已在 props_bank 注册的"已知道具"（bank 传入时附 _is_known 标记）
    不参与本过滤——跨章复用优先。
    """
    # 拼接分镜脚本全文（7 字段 × N 镜 + hook）
    fields = ("prompt", "lf_desc", "ff_desc", "motion_desc", "main_subject", "scene", "location")
    all_text_parts = []
    for sh in script.get("shots", []):
        for f in fields:
            v = sh.get(f)
            if isinstance(v, str) and v:
                all_text_parts.append(v)
    hook = script.get("hook") or {}
    for f in fields:
        v = hook.get(f)
        if isinstance(v, str) and v:
            all_text_parts.append(v)
    all_text = "\n".join(all_text_parts)

    kept, dropped = [], []
    for p in props:
        ident = (p.get("identifier") or "").strip()
        terms = [ident] + [t for t in (p.get("consistent_terms") or []) if isinstance(t, str) and t.strip()]
        # 已知道具（已在 bank 注册）跳过过滤
        if p.get("_is_known"):
            kept.append(p)
            continue
        # 至少一个 term 在分镜 7 字段任一出现
        hit = any(term in all_text for term in terms if term)
        if hit:
            kept.append(p)
        else:
            dropped.append(p)
    if dropped:
        dropped_names = [d.get("identifier", "?") for d in dropped]
        print(f"[WARN] 出口过滤 LLM 幻觉道具: {len(dropped)} 个 → {dropped_names}")
    return kept


# ========================================================================
# 工具函数
# ========================================================================

def _script_to_json(script: dict) -> str:
    """把分镜脚本压缩成可送入 prompt 的 JSON（只保留与场景/道具相关的字段）. """
    # 每镜只保留 location/scene/main_subject/prompt/ff_desc/narration_segment
    compact = {
        "shots": [
            {
                "index": sh.get("index"),
                "location": sh.get("location", ""),
                "scene": sh.get("scene", ""),
                "main_subject": sh.get("main_subject", ""),
                "prompt": sh.get("prompt", ""),
            }
            for sh in script.get("shots", [])
        ],
    }
    hook = script.get("hook")
    if hook:
        compact["hook"] = {
            "scene": hook.get("scene", ""),
            "location": hook.get("location", ""),
            "prompt": hook.get("prompt", ""),
        }
    return json.dumps(compact, ensure_ascii=False)


def _format_known_scenes(bank) -> str:
    """把已知场景库格式化成可注入 prompt 的文本."""
    known_ids = bank.get_all_identifiers() if bank else []
    if not known_ids:
        return "（暂无已知场景）"
    lines = []
    for ident in known_ids:
        scene = bank.get_scene(ident)
        if scene:
            parts = [f"  - {ident}"]
            if scene.get("location"):
                parts.append(f"地点:{scene['location']}")
            if scene.get("time_of_day"):
                parts.append(f"时间:{scene['time_of_day']}")
            if scene.get("wide_desc"):
                parts.append(f"大场景:{scene['wide_desc'][:80]}")
            lines.append("，".join(parts))
    return "\n".join(lines)


def _format_known_props(bank) -> str:
    """把已知道具库格式化成可注入 prompt 的文本."""
    known_ids = bank.get_all_identifiers() if bank else []
    if not known_ids:
        return "（暂无已知道具）"
    lines = []
    for ident in known_ids:
        prop = bank.get_prop(ident)
        if prop:
            parts = [f"  - {ident}"]
            if prop.get("category"):
                parts.append(f"类别:{prop['category']}")
            if prop.get("appearance"):
                parts.append(f"外观:{prop['appearance'][:80]}")
            lines.append("，".join(parts))
    return "\n".join(lines)


# ============================================================================
# props_bank orphan 审计（v6.40 08-22）
# ============================================================================

def audit_orphan_props(bank, current_chapter: int, current_script: dict = None,
                       orphan_window: int = 3) -> dict:
    """审计 props_bank 中的孤儿道具（v6.40 08-22 拦截 LLM 幻觉累积）.

    判定孤儿：prop 的 last_chapter 与 current_chapter 差 > orphan_window 章，
    且当前分镜脚本不出现（若提供 current_script）。

    Returns:
        dict: {
            "total": int,            # 总道具数
            "active": list[str],     # 最近 N 章内仍出场的道具
            "orphan": list[dict],    # 孤儿道具列表（含 id + last_chapter + first_chapter + category）
            "hallucinated_now": list[str],  # 本章分镜未出现但 bank 标记为本章注册的（实锤幻觉）
        }
    """
    result = {"total": 0, "active": [], "orphan": [], "hallucinated_now": []}
    if not bank:
        return result

    all_props = {}
    if hasattr(bank, "_data") and isinstance(bank._data, dict):
        all_props = bank._data.get("props", {}) or {}
    elif hasattr(bank, "get_all_props"):
        all_props = bank.get_all_props() or {}
    result["total"] = len(all_props)

    # 拼接当前分镜全文（7 字段）
    cur_text = ""
    if current_script:
        fields = ("prompt", "lf_desc", "ff_desc", "motion_desc",
                  "main_subject", "scene", "location")
        for sh in current_script.get("shots", []):
            for f in fields:
                v = sh.get(f)
                if isinstance(v, str):
                    cur_text += v + "\n"
        hook = current_script.get("hook") or {}
        for f in fields:
            v = hook.get(f)
            if isinstance(v, str):
                cur_text += v + "\n"

    for ident, prop in all_props.items():
        first = prop.get("first_chapter")
        last = prop.get("last_chapter")
        # 本章注册 + 本章分镜未出现 = 实锤本章 LLM 幻觉
        if current_chapter and first == current_chapter and cur_text:
            terms = [ident] + [t for t in (prop.get("consistent_terms") or []) if t]
            if not any(t and t in cur_text for t in terms):
                result["hallucinated_now"].append(ident)
        # orphan 判定：last_chapter + orphan_window < current_chapter
        if last is not None and current_chapter and (current_chapter - last) > orphan_window:
            result["orphan"].append({
                "identifier": ident,
                "last_chapter": last,
                "first_chapter": first,
                "category": prop.get("category"),
            })
        else:
            result["active"].append(ident)

    return result
