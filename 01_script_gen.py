from dotenv import load_dotenv
import hashlib
import json
import os
import re
from pathlib import Path
from chapter_reader import extract_section
import storyboard_refs

from _text_gen import config, create_service, TextGenerationRequest
from character_bank import CharacterBank

load_dotenv()

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))  # 默认第 1 章
CHARACTER_EXTRACTION_ENABLED = os.getenv("CHARACTER_EXTRACTION_ENABLED", "1").strip() == "1"
PROMPT_MAX_CHARS = int(os.getenv("PROMPT_MAX_CHARS", "12000"))  # 给 LLM 的原文上限，默认 12000 确保单章完整
SHOT_COUNT_MIN = int(os.getenv("SHOT_COUNT_MIN", "4"))  # LLM 自适应镜头数下限
SHOT_COUNT_MAX = int(os.getenv("SHOT_COUNT_MAX", "20"))  # LLM 自适应镜头数上限，不设硬限制

# ---------------------------------------------------------------------------
# 1. 从小说提取指定章节
# ---------------------------------------------------------------------------

def read_chapter(path: str = "锦衣夜行_77119小说.txt", chapter: int = 1,
                 max_chars: int = 8000) -> str:
    """提取小说指定章节，截取前 max_chars 字符（控制 token 用量）."""
    full_text = extract_section(path, chapter)
    # 截取前 max_chars 字符
    truncated = full_text[:max_chars]
    if len(full_text) > max_chars:
        print(f"[INFO] 章节原文共 {len(full_text)} 字符，"
              f"截取前 {max_chars} 字符用于生成分镜")
    return truncated


# ---------------------------------------------------------------------------
# 2. 构建 prompt
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """
# 角色定位
你是资深国风动漫分镜导演兼动画摄影指导，擅长把小说原文转化为动漫电影级视觉描述。请严格基于下方小说原文，生成一部 9:16 竖屏抖音短视频的动漫电影级分镜脚本。画面风格为精美2D国风动画，非真人写实。

# 第一步：决定镜头数与总时长（必做，写在最前面）
根据以下规则自行决定本批分镜的镜头数 N 和总时长（范围 {min_shots}-{max_shots} 镜，无强制时长限制）：
- 情节密度高（场景多、事件线复杂）-> N 取偏大值，每镜 duration 可取短一些
- 情节舒缓、氛围渲染为主 -> N 取偏小值，每镜 duration 可取长一些
- 原文约 {max_chars} 字符，每镜覆盖约 300-600 字为宜
- **每条 duration 自行决定（3-12秒）**，高潮镜头给 8-12 秒，过渡镜头 3-5 秒
- **narration_segment 自行决定字数摘取，不限 35-45 字**，以原文最佳片段为准
- **关键新增**：若分镜数 * 每镜时长不足以覆盖完整章节内容，则增加分镜数 N 或增加每镜 duration，直到能完整覆盖
- 请在输出 JSON 的 `shot_count` 字段中写明 N

# 核心铁律（违反任一即不合格）
1. 音字同步：shot[i].subtitle 必须与 shot[i].narration_segment 逐字相同；narration 字段必须等于所有 subtitle 按序拼接。
2. **严格画面-旁白绑定（最核心铁律，违反直接废弃）**：
   - 每个镜头的 prompt 必须精确描绘该镜头 narration_segment/subtitle 所述的主体、动作、环境、物体。
   - **逐词对应**：subtitle 中每个名词（人/物/场景）和动词（动作/事件）必须在 prompt 中有对应的视觉元素。
   - **禁止跑题**：subtitle 说"田野烈日"，prompt 就不能写"室内烛光"；subtitle 说"人物对峙"，prompt 就不能写"人物独坐饮茶"。
   - **反向校验**：prompt 中每个视觉元素都必须能从 subtitle 中找到文本依据。prompt 中出现的额外人物/动作/场景，如果 subtitle 未提及→删除。
   - **违规示例（绝对禁止）**：subtitle="太阳像火炉般烘烤着大地" 但 prompt=写"竹林月下流水潺潺" → 不合格。subtitle=写人物A 但 prompt=出现人物B → 不合格。
3. 全弧线覆盖：N 个镜头均匀覆盖原文完整情节弧线——前 1/3 镜头取开头（氛围渲染、背景铺设）、中间 1/3 取中段（事态发展、人物互动）、后 1/3 必须取结尾（冲突/战斗/刺杀/逃离/反转等强事件）；禁止全部镜头取自前半段。
4. 动漫电影化描述：每个 prompt 须含景别、机位、运镜、主体外貌与动作链、环境光影、色彩情绪、原文关键词、情绪词；风格为2D国风动画，禁止真人写实描述。
5. **平台合规（语义级禁令，违反则被AI审核拦截）**：
  - **禁止性描写**：不得出现任何性暗示、裸露画面、亲密接触、身体部位特写。拥抱亲吻一律退化为眼神交流或转身离去。
  - **禁止血腥暴力细节**：不得出现刺入/砍伤/折断等暴力动作的生理细节描述（如"刺进心口""鲜血飞溅""伤口可见"）。打斗场面用远景侧写——剑光一闪/身影交错/水面碎纹/袖风猎猎——绝不写流血细节。
  - **词汇替换**："赤足/赤膊"代"光腚/裸"，"红"代"血"，"交锋/出手"代"杀/刺"。

# 镜头语言库（必须从中选用并写入 prompt）

景别（N 镜至少用 ceil(N/2) 种不同景别，禁止相邻同景别）：
{shot_sizes}

机位角度：
{camera_angles}

运镜方式：
{camera_movements}

景深/构图（可选，强化画面控制）：
{depth_of_field}

光影氛围（按时段选择）：
{lighting_moods}

色彩情绪（国风动漫常用）：
{color_palettes}

人物外貌/服装参考（跨镜头保持人物一致）：
{costumes}

场景元素参考：
{locations}

摄像机分组指导（2-3台摄像机，各自角色明确，同机镜头连续不交错）：
{camera_grouping}

# 单镜头 prompt 结构模板
固定前缀 + 景别 + 机位角度 + 运镜方式 + 主体人物外貌与具体动作链（含微观物理细节与动漫特效）+ 环境场景与时间光源 + 色彩情绪 + 原文关键词 + 情绪词。

固定前缀（每个 prompt 必须以此开头，与 style_prefix.py / skill_spy.txt 保持一致）：
国风动漫影视剧，9:16竖屏，电影柔光，暗调低饱和，烛火局部光影，浅景深缓慢向前推镜，精美2D动画风格，无真人写实，高清1080P

写作要求：
- 主体动作必须具体：不说"走路"，说"双手负后缓步前行，长发随步伐轻扬，衣摆荡出弧形轨迹"；不说"看"，说"眼眸微眯，瞳孔高光凝缩，睫毛轻颤"。
- 必须含至少一个微观物理细节或动漫特效：发丝飘动、衣袂翻飞、灵光粒子飘散、水面涟漪、竹叶簌簌落下、光线下巴阴影、眼角泪光等。
- 必须出现 narration_segment 中的原文关键名词/动作。
- 相邻镜头景别不得重复。
- 每个镜头须有明确情绪词（热血激昂、清幽孤寂、紧张压抑、温暖治愈、悲壮决绝等）。
- 单条 prompt 长度 80-150 字。

# 画面-字幕逐词绑定（最重要，输出前逐镜核对）
- **强制逐词覆盖**：subtitle 中的每个核心名词（人物名、地名、关键物件、自然元素），都必须在画面描述中有视觉对应。
  - 对应规则：同一个词最佳（subtitle 有"大地"→画面也有"大地"）；同义视觉化变体可接受（"太阳"→"烈日""骄阳"；"火炉"已是太阳的隐喻则不需额外写火炉）；抽象上位词替代不可接受（"太阳"→"光源""高光"不合格）。
  - subtitle 出现人物名 → 画面必须写同一人物名
- subtitle 出现"水""河""湖""湾" → 画面必须出现"水面""河流""湾"（同一核心字"水""河""湾"）
- subtitle 出现"舟""船" → 画面必须写"小舟""船"
- **反例（禁止）**：subtitle="太阳像火炉般烘烤着大地" → 画面只写"烈日当空、黄褐色大地"→ 缺"火炉""烘烤"的视觉对应→ 不合格
- **覆盖率自检**：输出前，对每个镜头，逐个核对 subtitle 的核心实词是否在画面描述中有视觉对应。缺一个就是跑题。
- **这是比合规更基础的铁律——如果画面和字幕说的是两回事，整个视频就废了。**

# 生成步骤（严格按序执行）

 第1步 — 先通读每段 subtitle，列出其中的核心实词（地名/人物名/关键物件/自然元素），然后在写画面描述时逐个确保有对应的视觉元素。这篇先列词再写画面的步骤不能跳过。

第2步 — 将原文按情节均匀切为 N 个连续段落（N >= 6，至少6镜），不跳段不遗漏，覆盖从头到尾的全部转折。
  - 前段镜头 ← 开头段落（氛围渲染、场景建立、人物出场）
  - 中段镜头 ← 中段段落（事态发展、人物互动、情节推进）
  - 后段镜头 ← 结尾段落（高潮/冲突/转折；若原文结尾有战斗、刺杀、冲突、逃离、反转等强事件，必须呈现，不得用风景/氛围替代）
  - 三段镜头数量大致均匀即可，无需严格等分。

第3步 — 为每个段落生成基本字段：
  - narration_segment：从该段摘取最核心的原文原句（自行决定字数，以最佳摘句为准）
  - subtitle：与 narration_segment 逐字相同
  - prompt：按上面结构模板生成
  - duration：每条自行决定（3-12秒），高潮镜头（large variation）给 8-12 秒，过渡镜头给 3-5 秒。**不同镜头应给不同 duration**。
  - cam_idx：分配摄像机编号（1-3，同一摄像机镜头连续不交错）
  - variation_type：判断变幅（small/medium/large），至少 ceil(N/3) 个非 small
  - ff_desc：首帧静态快照（80-120字，纯静态，无运动动词）
  - lf_desc：尾帧静态快照（80-120字，small时留空""）
  - motion_desc：帧间运动描述（50-100字，纯过程，含运镜+主体动作）
  - context_bridge：视觉衔接描述（30-60字，镜头1留空""）

第4步 — 拼接所有 narration_segment 为 narration 字段

# 自检清单（输出前逐条自检，不合格重做）
- [ ] shot[i].subtitle == shot[i].narration_segment（逐字相同）
- [ ] narration == 所有 subtitle 按序拼接
- [ ] shot_count == 实际 shots 数组长度
- [ ] 每个 prompt 含对应 narration_segment 的原文关键词
- [ ] N 镜至少 ceil(N/2) 种景别，无相邻同景别
- [ ] 每个 prompt 80-150字，以固定前缀开头
- [ ] 后 1/3 镜头的 narration_segment 来自原文后半段（约50%位置之后），覆盖结尾强事件
- [ ] 风格关键词正确：必须包含"2D动画""国风""无真人写实"中的至少一项
- [ ] 所有 prompt 不含血腥暴力生理细节（用远景侧写替代）
- [ ] 所有 prompt 不含性暗示/裸露描写

# 输出格式
只输出纯JSON，不要markdown代码块标记，不要任何前后缀文字。结构如下：
{{
  "shot_count": N,
  "narration": "所有narration_segment按序拼接",
  "total_duration": "所有duration之和（秒），供下游视频生成参考",
  "negative_prompt": "真人写实、写实风格、畸形五官、明亮强光、现代服装、3D渲染、塑料质感、明亮阳光",
  "shots": [
    {{
      "index": 1,
      "duration": 6,
      "narration_segment": "太阳像火炉般烘烤着青州府大地，田间土皮都在冒烟",
      "subtitle": "太阳像火炉般烘烤着青州府大地，田间土皮都在冒烟",
      "cam_idx": 1,
      "variation_type": "small",
      "ff_desc": "远景，极低机位仰拍，干裂黄褐色大地蒸腾扭曲热浪，烈日高悬，枯柳静止，暖金焦褐色调",
      "lf_desc": "",
      "motion_desc": "镜头缓拉上摇，热浪蒸腾扭曲，远处蝉鸣柳枝微动",
      "context_bridge": "",
      "prompt": "国风动漫影视剧，…"
    }},
    {{
      "index": 2,
      "cam_idx": 1,
      "variation_type": "medium",
      "ff_desc": "全景，黄昏水湾，夕阳半没，余晖碎金洒水面，碧荷铺满，柳树成荫",
      "lf_desc": "全景，夕阳沉入地平线，天空深蓝紫红，荷叶颜色变暗，远处灯火初上",
      "motion_desc": "镜头缓摇跟随夕阳下沉，天空暖金过渡到深蓝紫红，柳枝晚风中渐摆",
      "context_bridge": "上一镜烈日高悬→本镜黄昏水湾，时间从正午推进到黄昏，光影从强顶光转为暖金侧逆光",
      ...
    }},
    ...共N个镜头
  ]
}}

# 小说原文
{novel_text}
"""

SYSTEM_PROMPT = (
    "你是资深国风动漫分镜导演兼动画摄影指导，擅长把小说原文转化为动漫电影级视觉描述。\n"
    "铁律（违反则不合格）：\n"
    "1. 音字同步：shot[i].subtitle 必须与 shot[i].narration_segment 逐字相同，narration 等于所有subtitle按序拼接；\n"
    "2. **画面-旁白逐词绑定（最高优先级）**：prompt 的每个视觉元素（人物、动作、场景、物体）必须严格来自对应 subtitle 文本。\n"
    "   - subtitle 说什么，画面就拍什么；subtitle 没说的，prompt 不能出现。\n"
    "   - 示例：subtitle 有\"烈日\" → prompt 必须有烈日或强光；subtitle 有\"奔跑\" → prompt 必须有奔跑动作。\n"
    "   - 反例：subtitle 写\"田野\" 但 prompt 写\"宫殿\" → 严重违规，直接废弃。\n"
    "3. 全弧线覆盖：所有镜头均匀覆盖原文完整情节——前段镜头取开头、中段镜头取中段、后段镜头必须取结尾（含冲突/战斗/反转等强事件），禁止全部取自前半段；\n"
    "4. 动漫电影化描述：每个prompt须含景别、机位、运镜、主体动作、环境光影、色彩情绪、原文关键词、情绪词，并以固定前缀开头；风格为精美2D动画，无真人写实；\n"
    "5. 语义级合规（违规则被审核拦截）：\n"
    "   - 禁止性暗示/性描写/亲密接触，有情人退化为眼神交流或转身离去；\n"
    "   - 禁止血腥暴力生理细节（刺入/砍伤/流血/伤口），打斗用远景侧写（剑光一闪/身影交错/水面碎纹），绝不描述流血；\n"
    "   - 低龄/裸露/血腥词一律替换（红代血、交锋代刺杀）。\n"
    "6. 摄像机分组与镜头连贯性：\n"
    "   - 所有镜头分到2-3台摄像机（cam_idx=1,2,3），同摄像机镜头连续；\n"
    "   - 每镜拆解为 ff_desc/lf_desc/motion_desc；\n"
    "   - ff/lf 纯静态不含运动，motion 纯过程不含静态；\n"
    "   - variation_type 至少 ceil(N/3) 个 medium 或 large；\n"
    "   - 镜头2及之后写 context_bridge（30-60字）；\n"
    "   - 输出 shot_count 字段，值等于实际镜头数。\n"
    "只输出纯JSON，不要markdown代码块、不要任何额外文字。"
)

# ---------------------------------------------------------------------------
# 角色提取 prompt（跨章节一致性）
# ---------------------------------------------------------------------------

CHARACTER_EXTRACTION_PROMPT = """
从以下小说原文中，提取所有出场人物。

已知角色库（若角色已在库中，只需确认其出场，不需重复描述外貌）：
{known_characters}

# 输出格式
只输出纯JSON，不要markdown代码块标记，不要任何前后缀文字。格式如下：
{{
  "characters": [
    {{
      "identifier": "人物名称（跨章节保持一致，如杨旭、张十三）",
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
- static_features 描述脸型、五官、发式、体型等不随场景变化的特征
- dynamic_features 描述服装、配饰、手持物品等随场景变化的特征
- 描述要具体可视化：不要用"英俊"，要写"剑眉星目、鼻梁高挺"
- is_new: 该人物是否不在已知角色库中
- 只提取主要角色（有名字、有对话、有动作的），忽略纯背景路人

# 小说原文
{novel_text}
"""


def extract_characters(novel_text: str, bank: CharacterBank) -> list[dict]:
    """从小说原文提取角色，已知角色只确认出场，新角色描述外貌."""
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
        max_tokens=2000,
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


def _format_list(items: list) -> str:
    """将列表格式化为换行符分隔的字符串."""
    return "\n".join(f"  - {item}" for item in items)


def _format_dict(d: dict) -> str:
    """将字典格式化为 key: value 的字符串."""
    return "\n".join(f"  - {k}: {v}" for k, v in d.items())


def _load_local_skill_context() -> str:
    """加载伴生技能作为 prompt 附加上下文（若存在）."""
    try:
        from pathlib import Path
        loader_path = Path(__file__).resolve().parent / ".aiVideo" / "skills_loader.py"
        if not loader_path.exists():
            return ""
        import importlib.util
        spec = importlib.util.spec_from_file_location("skills_loader", str(loader_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        body = mod.read_local_skill("generate-script")
        if body:
            return f"\n\n# 项目专属偏好（来自 .aiVideo/skills/generate-script-local/SKILL.md）\n{body}"
    except Exception:
        pass
    return ""


def build_prompt(novel_text: str) -> str:
    """构建最终发送给 LLM 的 prompt，注入镜头语言参考表 + 伴生技能偏好."""
    prompt = PROMPT_TEMPLATE.format(
        novel_text=novel_text,
        min_shots=SHOT_COUNT_MIN,
        max_shots=SHOT_COUNT_MAX,
        max_chars=PROMPT_MAX_CHARS,
        shot_sizes=_format_list(storyboard_refs.SHOT_SIZES),
        camera_angles=_format_list(storyboard_refs.CAMERA_ANGLES),
        camera_movements=_format_list(storyboard_refs.CAMERA_MOVEMENTS),
        depth_of_field=_format_list(storyboard_refs.DEPTH_OF_FIELD),
        lighting_moods=_format_dict(storyboard_refs.LIGHTING_MOODS),
        color_palettes=_format_dict(storyboard_refs.COLOR_PALETTES),
        costumes=_format_list(storyboard_refs.ANCIENT_COSTUMES),
        locations=_format_list(storyboard_refs.ANCIENT_LOCATIONS),
        camera_grouping=_format_list(storyboard_refs.CAMERA_GROUPING),
    )
    # 追加伴生技能上下文
    local_ctx = _load_local_skill_context()
    if local_ctx:
        prompt = prompt + local_ctx
    return prompt


# ---------------------------------------------------------------------------
# 3. 文本生成（通过 _text_gen 服务）
# ---------------------------------------------------------------------------

def gen_spy_script(novel_text: str) -> str:
    """调用文本生成服务生成分镜脚本（返回纯文本）."""
    prompt = build_prompt(novel_text)
    service = create_service()
    req = TextGenerationRequest(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
        max_tokens=4000,
        temperature=0.7,
    )
    result = service.generate(req)
    if not result.success:
        raise RuntimeError(f"文本生成失败: {result.error}")
    return result.text


# ---------------------------------------------------------------------------
# 4. 解析响应 → 提取并保存分镜 JSON
# ---------------------------------------------------------------------------

def _validate_script(script: dict) -> None:
    """校验分镜脚本的基本约束，给出警告但不阻断."""
    shots = script.get("shots", [])
    narration = script.get("narration", "")

    # --- 镜头数校验 ---
    shot_count = script.get("shot_count")
    if shot_count is None:
        print(f"[WARN] JSON 中缺少 shot_count 字段，建议 LLM 在输出中包含此字段")
    elif not isinstance(shot_count, int) or shot_count < SHOT_COUNT_MIN or shot_count > SHOT_COUNT_MAX:
        print(f"[WARN] shot_count={shot_count} 超出配置范围 [{SHOT_COUNT_MIN}, {SHOT_COUNT_MAX}]")
    elif shot_count != len(shots):
        print(f"[WARN] 声明 shot_count={shot_count} 但实际镜头数为 {len(shots)}，不一致")

    # --- 景别多样性校验 ---
    import collections
    shot_sizes_used = []
    for shot in shots:
        prompt = shot.get("prompt", "")
        for sz in storyboard_refs.SHOT_SIZES:
            sz_short = sz.split(" ")[0]  # 取中文名如"特写"
            if sz_short in prompt:
                shot_sizes_used.append(sz_short)
                break
    unique_sizes = len(set(shot_sizes_used))
    min_required = max(3, -(len(shots) // -2))  # ceil(N/2)，至少3
    if unique_sizes < min_required:
        print(f"[WARN] 景别多样性不足：{unique_sizes} 种（{shot_sizes_used}），要求至少 {min_required} 种")
    # 检查相邻同景别
    for i in range(len(shot_sizes_used) - 1):
        if shot_sizes_used[i] and shot_sizes_used[i] == shot_sizes_used[i + 1]:
            print(f"[WARN] 镜头{i+1}与镜头{i+2}景别重复（{shot_sizes_used[i]}）")

    for i, shot in enumerate(shots, 1):
        seg = shot.get("narration_segment", "").strip()
        sub = shot.get("subtitle", "").strip()
        # 搜索范围 = prompt + ff_desc + motion_desc（v3 架构中画面信息分散在三个字段）
        search_text = " ".join([
            shot.get("prompt", ""),
            shot.get("ff_desc", ""),
            shot.get("motion_desc", ""),
        ])

        if seg and sub and seg != sub:
            print(f"[WARN] 镜头{i}: subtitle 与 narration_segment 不一致")
            print(f"       subtitle={sub}")
            print(f"       narration_segment={seg}")

        # 关键帧检查移交给 _check_prompt_subtitle_alignment 的逐字覆盖率检测
        # seg in search_text 对中文无效（同一语义不同措辞天然不全字匹配）

        if sub and search_text:
            _check_prompt_subtitle_alignment(i, sub, search_text)

    if shots and narration:
        reconstructed = "".join(s.get("subtitle", "") for s in shots)
        if narration.replace("，", "").replace("。", "") != reconstructed.replace("，", "").replace("。", ""):
            print("[WARN] narration 与所有 subtitle 拼接不完全一致（可能因标点/空格差异）")


def _check_prompt_subtitle_alignment(shot_idx: int, subtitle: str, search_text: str) -> None:
    """校验 prompt 与 subtitle 的硬性矛盾（确认跑题才报警）.

    检测三种跑题模式：
    1. 场景/地点矛盾 — 字幕有室内词但 prompt 只有室外词（或反之）
    2. 命名实体全部缺失 — ≥3 个实体词一个都不在 prompt 里
    """
    # 去除风格前缀，只在实质搜索文本中检测
    prompt_body = search_text.split("高清1080P")[-1] if "高清1080P" in search_text else search_text

    # --- 检测1: 场景/地点矛盾 ---
    _indoor = {"宫殿", "府衙", "签押房", "大堂", "书房", "监牢", "密室", "酒楼", "客栈",
               "后花园"}
    _outdoor = {"竹林", "山巅", "云海", "河畔", "月下", "田野", "枯柳",
                "樱花", "庭院", "道场", "长街", "山路"}
    # "庭院" 可内可外，不偏任一边
    _indoor |= {"飞檐", "画舫", "烛火", "灯火"}
    _outdoor |= {"烈日", "城门", "军营", "古城", "青州"}

    sub_indoor = _indoor & set(subtitle)
    sub_outdoor = _outdoor & set(subtitle)
    prompt_indoor = _indoor & set(prompt_body)
    prompt_outdoor = _outdoor & set(prompt_body)

    sub_has = sub_indoor or sub_outdoor
    prompt_has = prompt_indoor or prompt_outdoor
    if sub_has and prompt_has:
        # 字幕定位室内但 prompt 定位室外（或反之）→ 矛盾
        if (sub_indoor and prompt_outdoor and not prompt_indoor
                and not (sub_indoor & prompt_indoor)):
            print(f"[WARN] 镜头{shot_idx}: 字幕在室内({sub_indoor})，"
                  f"prompt 在室外({prompt_outdoor})，场景矛盾")
            return
        if (sub_outdoor and prompt_indoor and not prompt_outdoor
                and not (sub_outdoor & prompt_outdoor)):
            print(f"[WARN] 镜头{shot_idx}: 字幕在室外({sub_outdoor})，"
                  f"prompt 在室内({prompt_indoor})，场景矛盾")
            return

    # --- 检测2: 字幕中的汉字在画面描述中的覆盖率（极窄阈值，只抓确认跑题）---
    _check_subtitle_char_coverage(shot_idx, subtitle, prompt_body)


def _check_subtitle_char_coverage(shot_idx: int, subtitle: str, search_text: str) -> None:
    """检测字幕汉字在画面描述中的覆盖率（极窄阈值，只抓确认跑题）.

    设计原则：字幕（文学化原文摘句）和画面描述（视觉化场景）是两套措辞体系，
    逐字覆盖率天然偏低。只有当核心内容字几乎完全找不到时才报警。
    阈值：汉字命中率 < 15% 且 2-gram 命中率 = 0。对"太阳->烈日"不误报。
    """
    chars = [c for c in subtitle if '一' <= c <= '鿿']
    if len(chars) < 10:
        return

    found = [c for c in chars if c in search_text]
    char_hit_rate = len(found) / len(chars)

    bigrams_in_sub = set()
    for i in range(len(chars) - 1):
        bigrams_in_sub.add(chars[i] + chars[i + 1])
    bigrams_found = {bg for bg in bigrams_in_sub if bg in search_text}
    bg_hit_rate = len(bigrams_found) / len(bigrams_in_sub) if bigrams_in_sub else 1

    if char_hit_rate < 0.15 and bg_hit_rate == 0:
        missing_bg = list(bigrams_in_sub - bigrams_found)[:6]
        print(f"[WARN] 镜头{shot_idx}: 画面描述与字幕内容严重脱节"
              f"（汉字命中{char_hit_rate:.0%}、二字组合全部未命中）")
        print(f"       未命中的二字组合: {missing_bg}")


def _strip_punct(text: str) -> str:
    """移除中文标点和空白，仅保留汉字."""
    import re
    return re.sub(r'[^一-鿿]', '', text)


def parse_response(raw_text: str) -> dict:
    """从 LLM 返回的文本中提取分镜 JSON."""
    print(f"[INFO] 模型返回文本长度: {len(raw_text)} 字符")

    # 清理 markdown 代码块标记
    cleaned = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # 解析 JSON
    try:
        script = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[WARN] JSON 解析失败，尝试修复... 前 200 字符: {cleaned[:200]}")
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            script = json.loads(cleaned[start : end + 1])
        else:
            raise ValueError(f"无法解析模型返回的 JSON: {e}") from e

    _validate_script(script)
    return script


def _compute_timecodes(script: dict) -> None:
    """计算每个镜头的时间轴（timecode），并生成 timeline 数组.

    每个镜头分配 start 和 end 时间戳，单位：秒。
    用于后续 TTS 和合并阶段精确对齐。
    """
    shots = script.get("shots", [])
    if not shots:
        return

    # 预留 0.3s 空镜缓冲（每个镜头前后各 0.15s）
    # 实际画面时间 = duration - 0.3s
    buffer_per_shot = 0.3

    current_time = 0.0
    timeline = []

    for shot in shots:
        duration = shot.get("duration", 5)
        # 实际画面时间，预留缓冲
        effective_duration = max(duration - buffer_per_shot, 0.5)
        end_time = current_time + effective_duration

        # 写入 timecode 字段
        shot["timecode"] = {
            "start": round(current_time, 2),
            "end": round(end_time, 2),
        }
        shot["effective_duration"] = round(effective_duration, 2)

        timeline.append({
            "index": shot.get("index", 0),
            "start": round(current_time, 2),
            "end": round(end_time, 2),
            "duration": duration,
            "effective_duration": round(effective_duration, 2),
        })

        current_time = end_time + buffer_per_shot  # 下一镜头从空镜结束后开始

    # 顶层 timeline 数组
    script["timeline"] = timeline
    script["total_effective_duration"] = round(current_time - buffer_per_shot, 2)  # 减去最后一个缓冲


# ---------------------------------------------------------------------------
# 5. 主流程
# ---------------------------------------------------------------------------

def _hash_source(script_path: str, novel_preview: str, chapter: int) -> str:
    """对 prompt 源码 + 小说首段 + 章号的组合做轻量指纹.

    三者任一变化 → 指纹变化 → 重新生成分镜，确保不同章节独立缓存.
    novel_preview 取前 500 字符而非全文，在防碰撞和性能间平衡.
    """
    src_hash = hashlib.sha256(
        Path(script_path).read_bytes()
    ).hexdigest()[:16]
    novel_hash = hashlib.sha256(
        novel_preview.encode("utf-8")
    ).hexdigest()[:16]
    chapter_part = hashlib.sha256(
        f"chapter_{chapter}".encode()
    ).hexdigest()[:8]
    return f"{src_hash}:{chapter_part}:{novel_hash}"


def _prompt_changed(build_py: Path, script_json: Path, novel_preview: str, chapter: int) -> bool:
    """判断是否需要重新生成分镜：prompt 源码、小说内容或章节号有变化."""
    if not script_json.exists():
        return True
    current_hash = _hash_source(str(build_py), novel_preview, chapter)
    try:
        with open(script_json, "r", encoding="utf-8") as f:
            existing = json.load(f)
        stored_hash = existing.get("_source_hash", "")
        return stored_hash != current_hash
    except Exception:
        return True


if __name__ == "__main__":
    OUTPUT_FILE = "script_result.json"
    print(f"[INFO] 当前 Text Provider: {config.PROVIDER}")

    # Step 1 — 读取指定章节
    novel_content = read_chapter(chapter=CHAPTER_INDEX, max_chars=PROMPT_MAX_CHARS)
    print(f"[INFO] 选取第 {CHAPTER_INDEX} 章前 {len(novel_content)} 字符作为素材")
    print(f"[INFO] 原文预览: {novel_content[:100]}...\n")

    # Step 2 — 检查是否需要重新生成
    BUILD_PY = Path(__file__).resolve()
    SCRIPT_FILE = Path(OUTPUT_FILE)
    current_hash = _hash_source(str(BUILD_PY), novel_content, CHAPTER_INDEX)
    prompt_needs_regen = _prompt_changed(BUILD_PY, SCRIPT_FILE, novel_content, CHAPTER_INDEX)

    if not prompt_needs_regen and SCRIPT_FILE.exists():
        print("[INFO] prompt 与小说内容未变，无需重新生成分镜脚本")
        print(f"[INFO] 直接使用现有: {SCRIPT_FILE}")
        with open(SCRIPT_FILE, "r", encoding="utf-8") as f:
            script = json.load(f)
    else:
        # Step 3 — 调用 API 生成分镜
        raw_response = gen_spy_script(novel_content)

        # Step 4 — 解析并保存
        script = parse_response(raw_response)
        script["_source_hash"] = current_hash
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)

    # Step 3.5 — 角色抽取（跨章节一致性）
    if CHARACTER_EXTRACTION_ENABLED:
        print(f"\n[INFO] 正在提取角色...")
        bank = CharacterBank()
        bank.load()
        characters = extract_characters(novel_content, bank)
        if characters:
            for char in characters:
                ident = char.get("identifier", "")
                if char.get("is_new", True) or not bank.has_character(ident):
                    bank.register_character(
                        identifier=ident,
                        static_features=char.get("static_features", ""),
                        dynamic_features=char.get("dynamic_features", ""),
                        chapter_num=CHAPTER_INDEX,
                    )
                else:
                    bank.update_dynamic_features(ident, char.get("dynamic_features", ""))
                    bank.update_character_chapter(ident, CHAPTER_INDEX)
            bank.save()
            script["characters"] = characters
            print(f"[INFO] 角色库已更新，共 {bank.get_meta().get('total_characters', 0)} 个角色")
    else:
        print(f"\n[INFO] 角色提取已禁用（CHARACTER_EXTRACTION_ENABLED=0）")
        script["characters"] = []

    # 如果跳过了 LLM 生成（复用已有文件），则不需要重新保存
    if not prompt_needs_regen and SCRIPT_FILE.exists():
        pass  # 文件已在上面读取，无需重写
    else:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)

    # Step 4 — 打印摘要
    print(f"\n{'='*60}")
    print(f"分镜脚本已保存至 {OUTPUT_FILE}")
    print(f"{'='*60}")
    print(f"旁白文案 ({len(script.get('narration', ''))} 字):")
    print(f"  {script.get('narration', 'N/A')[:200]}...\n")
    print(f"负面提示词:")
    print(f"  {script.get('negative_prompt', 'N/A')}\n")

    shots = script.get("shots", [])
    print(f"共 {len(shots)} 个镜头:\n")
    for shot in shots:
        print(f"  镜头{shot.get('index', '?')} ({shot.get('duration', '?')}s)")
        print(f"    字幕: {shot.get('subtitle', 'N/A')}")
        print(f"    画面: {shot.get('prompt', 'N/A')[:120]}...")
        print()

    # Step 4.5 — 计算时间轴（timecode），每个镜头占一段时间
    _compute_timecodes(script)
    # 保存带 timecode 的脚本
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 时间轴已计算并保存")
