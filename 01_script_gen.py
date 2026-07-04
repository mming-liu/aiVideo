from dotenv import load_dotenv
import json
import os
import re
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
PROMPT_MAX_CHARS = int(os.getenv("PROMPT_MAX_CHARS", "8000"))  # 给 LLM 的原文上限，默认 8000 确保单章完整

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
你是资深国风动漫分镜导演兼动画摄影指导，擅长把小说原文转化为动漫电影级视觉描述。请严格基于下方小说原文，生成一部约55秒、9:16竖屏抖音短视频的动漫电影级分镜脚本。画面风格为精美2D国风动画，非真人写实。

# 核心铁律（违反任一即不合格）
1. 音字同步：shot[i].subtitle 必须与 shot[i].narration_segment 逐字相同；narration 字段必须等于6个 subtitle 按序拼接。
2. **严格画面-旁白绑定（最核心铁律，违反直接废弃）**：
   - 每个镜头的 prompt 必须精确描绘该镜头 narration_segment/subtitle 所述的主体、动作、环境、物体。
   - **逐词对应**：subtitle 中每个名词（人/物/场景）和动词（动作/事件）必须在 prompt 中有对应的视觉元素。
   - **禁止跑题**：subtitle 说"田野烈日"，prompt 就不能写"室内烛光"；subtitle 说"人物对峙"，prompt 就不能写"人物独坐饮茶"。
   - **反向校验**：prompt 中每个视觉元素都必须能从 subtitle 中找到文本依据。prompt 中出现的额外人物/动作/场景，如果 subtitle 未提及→删除。
   - **违规示例（绝对禁止）**：subtitle="太阳像火炉般烘烤着大地" 但 prompt=写"竹林月下流水潺潺" → 不合格。subtitle=写人物A 但 prompt=出现人物B → 不合格。
3. 全弧线覆盖：6个镜头均匀覆盖原文完整情节弧线——镜头1-2取开头（氛围渲染）、镜头3-4取中段（事态发展）、镜头5-6必须取结尾（冲突/战斗/刺杀/逃离/反转等强事件）；禁止6镜全部取自前半段。
4. 动漫电影化描述：每个 prompt 须含景别、机位、运镜、主体外貌与动作链、环境光影、色彩情绪、原文关键词、情绪词；风格为2D国风动画，禁止真人写实描述。
5. **平台合规（语义级禁令，违反则被AI审核拦截）**：
  - **禁止性描写**：不得出现任何性暗示、裸露画面、亲密接触、身体部位特写。拥抱亲吻一律退化为眼神交流或转身离去。
  - **禁止血腥暴力细节**：不得出现刺入/砍伤/折断等暴力动作的生理细节描述（如"刺进心口""鲜血飞溅""伤口可见"）。打斗场面用远景侧写——剑光一闪/身影交错/水面碎纹/袖风猎猎——绝不写流血细节。
  - **词汇替换**：用"少年/年轻人"代"娃娃/孩童"，"赤足/赤膊"代"光腚/裸"，"红"代"血"，"交锋/出手"代"杀/刺"。

# 镜头语言库（必须从中选用并写入 prompt）

景别（6镜至少用3种不同景别，禁止相邻同景别）：
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

# 生成步骤（严格按序执行）

第1步 — 将原文按情节均匀切为6个连续段落，不跳段不遗漏，覆盖从头到尾的全部转折；每段约700-900字。
  - 镜头1-2 ← 段落1-2（开头，氛围渲染）
  - 镜头3-4 ← 段落3-4（中段，事态发展）
  - 镜头5-6 ← 段落5-6（结尾，高潮/冲突/转折；若原文结尾有战斗、刺杀、冲突、逃离、反转等强事件，镜头5-6必须呈现，不得用风景/氛围替代）

第2步 — 为每个段落生成字段：
  - narration_segment：从该段摘取最核心的1句原文原句（16-25字，不改写）。6段总旁白必须达到120-150字，否则不合格。
  - subtitle：与 narration_segment 逐字相同
  - prompt：按上面结构模板生成
  - duration：朗读秒数估算（中文约4字/秒 + 1s停顿，取5-7秒）
  - 校验：6个duration之和必须在28-36秒之间（≈视频总长30s），否则调整narration_segment字数

第3步 — 拼接6个 narration_segment 为 narration 字段，总字数120-150字，对应朗读时长28-36秒。

# 自检清单（输出前逐条自检，不合格重做）
- [ ] shot[i].subtitle == shot[i].narration_segment（逐字相同）
- [ ] narration == 6个 subtitle 按序拼接
- [ ] 每个 prompt 含对应 narration_segment 的原文关键词
- [ ] 6镜至少3种景别，无相邻同景别
- [ ] 每个 prompt 80-150字，以固定前缀开头
- [ ] 镜头5-6 的 narration_segment 来自原文后半段（约50%位置之后），覆盖结尾强事件
- [ ] 风格关键词正确：必须包含"2D动画""国风""无真人写实"中的至少一项
- [ ] 所有 prompt 不含过度裸露描写

# 输出格式
只输出纯JSON，不要markdown代码块标记，不要任何前后缀文字。结构如下：
{{
  "narration": "6个narration_segment按序拼接，总字数120-150字",
  "negative_prompt": "真人写实、写实风格、畸形五官、明亮强光、现代服装、3D渲染、塑料质感、明亮阳光",
  "shots": [
    {{
      "index": 1,
      "duration": 5,
      "narration_segment": "太阳像火炉般烘烤着青州府大地，田间土皮都在冒烟",
      "subtitle": "太阳像火炉般烘烤着青州府大地，田间土皮都在冒烟",
      "prompt": "国风动漫影视剧，9:16竖屏，电影柔光，暗调低饱和，烛火局部光影，浅景深缓慢向前推镜，精美2D动画风格，无真人写实，高清1080P，远景，极低机位仰拍，缓拉镜头，干裂黄褐色大地蒸腾扭曲热浪，青衣少年赤足奔跑扬起金色尘土粒子，远处枯柳耷拉蝉鸣虚化，烈日高悬正空，灵光颗粒在空气里浮动，暖金与焦褐色调交织，燥热慵懒"
    }},
    ...共6个镜头，duration 5-7秒，总和28-36秒
  ]
}}

# 小说原文
{novel_text}
"""

SYSTEM_PROMPT = (
    "你是资深国风动漫分镜导演兼动画摄影指导，擅长把小说原文转化为动漫电影级视觉描述。\n"
    "铁律（违反则不合格）：\n"
    "1. 音字同步：shot[i].subtitle 必须与 shot[i].narration_segment 逐字相同，narration 等于6个subtitle按序拼接；\n"
    "2. **画面-旁白逐词绑定（最高优先级）**：prompt 的每个视觉元素（人物、动作、场景、物体）必须严格来自对应 subtitle 文本。\n"
    "   - subtitle 说什么，画面就拍什么；subtitle 没说的，prompt 不能出现。\n"
    "   - 示例：subtitle 有\"烈日\" → prompt 必须有烈日或强光；subtitle 有\"奔跑\" → prompt 必须有奔跑动作。\n"
    "   - 反例：subtitle 写\"田野\" 但 prompt 写\"宫殿\" → 严重违规，直接废弃。\n"
    "3. 全弧线覆盖：6个镜头均匀覆盖原文完整情节——镜头1-2取开头、3-4取中段、5-6必须取结尾（含冲突/战斗/反转等强事件），禁止全部取自前半段；\n"
    "4. 动漫电影化描述：每个prompt须含景别、机位、运镜、主体动作、环境光影、色彩情绪、原文关键词、情绪词，并以固定前缀开头；风格为精美2D动画，无真人写实；\n"
    "5. 语义级合规（违规则被审核拦截）：\n"
    "   - 禁止性暗示/性描写/亲密接触，有情人退化为眼神交流或转身离去；\n"
    "   - 禁止血腥暴力生理细节（刺入/砍伤/流血/伤口），打斗用远景侧写（剑光一闪/身影交错/水面碎纹），绝不描述流血；\n"
    "   - 低龄/裸露/血腥词一律替换（少年代娃娃、红代血、交锋代刺杀）。\n"
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


def build_prompt(novel_text: str) -> str:
    """构建最终发送给 LLM 的 prompt，注入镜头语言参考表."""
    return PROMPT_TEMPLATE.format(
        novel_text=novel_text,
        shot_sizes=_format_list(storyboard_refs.SHOT_SIZES),
        camera_angles=_format_list(storyboard_refs.CAMERA_ANGLES),
        camera_movements=_format_list(storyboard_refs.CAMERA_MOVEMENTS),
        depth_of_field=_format_list(storyboard_refs.DEPTH_OF_FIELD),
        lighting_moods=_format_dict(storyboard_refs.LIGHTING_MOODS),
        color_palettes=_format_dict(storyboard_refs.COLOR_PALETTES),
        costumes=_format_list(storyboard_refs.ANCIENT_COSTUMES),
        locations=_format_list(storyboard_refs.ANCIENT_LOCATIONS),
    )


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

    for i, shot in enumerate(shots, 1):
        seg = shot.get("narration_segment", "").strip()
        sub = shot.get("subtitle", "").strip()
        prompt = shot.get("prompt", "")

        if seg and sub and seg != sub:
            print(f"[WARN] 镜头{i}: subtitle 与 narration_segment 不一致")
            print(f"       subtitle={sub}")
            print(f"       narration_segment={seg}")

        if seg and seg not in prompt and not any(kw in prompt for kw in seg.split()[:3]):
            print(f"[WARN] 镜头{i}: prompt 可能缺少原文关键词，请检查")
            print(f"       prompt={prompt[:80]}...")

        # 新增：画面-字幕关键词交叉校验（无 jieba 依赖）
        if sub and prompt:
            _check_prompt_subtitle_alignment(i, sub, prompt)

    if shots and narration:
        reconstructed = "".join(s.get("subtitle", "") for s in shots)
        if narration.replace("，", "").replace("。", "") != reconstructed.replace("，", "").replace("。", ""):
            print("[WARN] narration 与所有 subtitle 拼接不完全一致（可能因标点/空格差异）")


def _check_prompt_subtitle_alignment(shot_idx: int, subtitle: str, prompt: str) -> None:
    """校验 prompt 与 subtitle 的硬性矛盾（确认跑题才报警）.

    检测三种跑题模式：
    1. 场景/地点矛盾 — 字幕有室内词但 prompt 只有室外词（或反之）
    2. 命名实体全部缺失 — ≥3 个实体词一个都不在 prompt 里
    """
    # 去除风格前缀，只在实质 prompt 中搜索
    prompt_body = prompt.split("高清1080P")[-1] if "高清1080P" in prompt else prompt

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

    # --- 检测2: 命名实体全部缺失 ---
    clean_sub = _strip_punct(subtitle)
    _stop_char = set("的了是这在和着与之地就会可要还能没被把让为对从向里外前后上下中大"
                     "小多少很个你我他她它己等做只子么一二十百千万年月日时得过又才刚正"
                     "便自其各如更最非来去进出上下到说看把让被不")
    entities = _extract_noun_phrases(clean_sub, _stop_char)

    if len(entities) >= 3:
        found = [e for e in entities if e in prompt_body]
        if not found:
            print(f"[WARN] 镜头{shot_idx}: 字幕 {len(entities)} 个实体词全部缺失"
                  f"（严重跑题）: {entities[:6]}")
        elif len(found) <= 1 and len(entities) >= 5:
            print(f"[WARN] 镜头{shot_idx}: 字幕实体词仅 {len(found)}/{len(entities)} 命中"
                  f"（可能跑题），缺失: {[e for e in entities if e not in prompt_body][:4]}")


def _extract_noun_phrases(text: str, stop_chars: set) -> list[str]:
    """从汉字文本中提取连续的实词片段（≥2字，不含虚字）."""
    phrases = []
    i = 0
    while i < len(text) - 1:
        if text[i] not in stop_chars and text[i + 1] not in stop_chars:
            # 尝试3字
            if i + 2 < len(text) and text[i + 2] not in stop_chars:
                phrases.append(text[i:i + 3])
                i += 3
                continue
            phrases.append(text[i:i + 2])
            i += 2
        else:
            i += 1
    return phrases


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


# ---------------------------------------------------------------------------
# 5. 主流程
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[INFO] 当前 Text Provider: {config.PROVIDER}")

    # Step 1 — 读取指定章节
    novel_content = read_chapter(chapter=CHAPTER_INDEX, max_chars=PROMPT_MAX_CHARS)
    print(f"[INFO] 选取第 {CHAPTER_INDEX} 章前 {len(novel_content)} 字符作为素材")
    print(f"[INFO] 原文预览: {novel_content[:100]}...\n")

    # Step 2 — 调用 API 生成分镜
    raw_response = gen_spy_script(novel_content)

    # Step 3 — 解析并保存
    script = parse_response(raw_response)

    # Step 3.5 — 角色抽取（跨章节一致性）
    if CHARACTER_EXTRACTION_ENABLED:
        print(f"\n[INFO] 正在提取角色...")
        bank = CharacterBank()
        bank.load()
        characters = extract_characters(novel_content, bank)
        if characters:
            # 新角色注册到 bank（static_features 首次锁定）
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
                    # 已知角色：只更新动态特征和最后出场章节
                    bank.update_dynamic_features(ident, char.get("dynamic_features", ""))
                    bank.update_character_chapter(ident, CHAPTER_INDEX)
            bank.save()
            script["characters"] = characters
            print(f"[INFO] 角色库已更新，共 {bank.get_meta().get('total_characters', 0)} 个角色")
    else:
        print(f"\n[INFO] 角色提取已禁用（CHARACTER_EXTRACTION_ENABLED=0）")
        script["characters"] = []

    OUTPUT_FILE = "script_result.json"
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
