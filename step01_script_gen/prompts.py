"""step01_script_gen.prompts - Prompt 模板定义

包含：
- PROMPT_TEMPLATE: 分镜生成主模板
- SYSTEM_PROMPT: 系统提示词
- build_rompt(): 构建完整 prompt
- _format_list(), _format_dict(): 格式化辅助
- _load_local_skill_context(): 加载伴生技能
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import storyboard_refs


# ========================================================================
# 主 prompt 模板
# ========================================================================

PROMPT_TEMPLATE = """
# 角色定位
你是资深国风动漫分镜导演兼动画摄影指导，擅长把小说原文转化为动漫电影级视觉描述。请严格基于下方小说原文，生成一部 9:16 竖屏抖音短视频的动漫电影级分镜脚本。画面风格为精美2D国风动画，菲真人写实。

# 第一步：决定镜头数（必做，写在最前面）
根据以下规则自行决定本批分镜的镜头数 N（范围 {min_shots}-{max_shots} 镜）：
- 情节密度高（场景多、事件线复杂）-> N 取偏大值
- 情节舒缓、氛围渲染为主 -> N 取偏小值
- 原文约 {novel_char_count} 字符，每镜覆盖约 300-600 字为宜
- **narration_segment 自行决定字数摘取，不限 35-45 字**，以原文最佳片段为准
- **关键新增**：若 N 个镜头不足以覆盖完整章节内容，则增加镜头数 N 直到能完整覆盖
- 请在输出 JSON 的 `shot_count` 字段中写明 N
- **注意：每镜时长（duration）由系统根据 TTS 实际生成长度自动决定，不需要在 JSON 中输出 duration 字段**

# 核心铁律（违反任一即不合格）
1. 音字同步：shot[i].subtitle 必须与 shot[i].narration_segment 逐字相同；narration 字段必须等于所有 subtitle 按序拼接。
2. **严格画面-旁白绑定（最核心铁律，违反直接废弃）**：
   - 每个镜头的 prompt 必须精确描绘该镜头 narration_segment/subtitle 所述的主体、动作、环境、物体。
   - **逐词对应**：subtitle 中每个名词（人/物/场景）和动词（动作/事件）必须在 prompt 中有对应的视觉元素。
   - **禁止跑题**：subtitle 说"田野烈日"，prompt 就不能写"室内烛光"；subtitle 说"人物对峙"，prompt 就不能写"人物独坐饮茶"。
   - **反向校验**：prompt 中每个视觉元素都必须能从 subtitle 中找到文本依据。prompt 中出现的额外人物/动作/场景，如果 subtitle 未提及→删除。
   - **违规示例（绝对禁止）**：subtitle="太阳像火炉般烘烤着大地" 但 prompt=写"竹林月下流水潺潺" → 不合格。subtitle=写人物A 但 prompt=出现人物B → 不合格。
3. 全弧线覆盖：N 个镜头均匀覆盖原文完整情节弧线——前 1/3 镜头取开头（氛围渲染、背景铺设）、中间 1/3 取中段（事态发展、人物互动）、后 1/3 必须取结尾（冲突/战斗/刺杀/逃离/反转等强事件）；禁止全部镜头取自前半段。
4. 动漫电影化描述：每个 prompt 须含景别、机位、运镜、主体外貌与动作链、主体动作、主体动作与微观物理细节与动漫特效)、环境与光照、色彩与情绪、原文关键词、情绪词；风格为2D国风动画，禁止真人写实描述。
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

景深/构图（可选，強化画面控制）：
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
"""


# ========================================================================
# 系统提示词
# ========================================================================

SYSTEM_PROMPT = (
    "你是资深国风动漫分镜导演兼动画摄影指导，擅长把小说原文转化为动漫电影级视觉描述。\n"
    "铁律（违反则不合格）：\n"
    "1. 音字同步：shot[i].subtitle 必须与 shot[i].narration_segment 逐字相同，narration 等于所有subtitle按序拼接；\n"
    "2. **画面-旁白逐词绑定（最高优先级）**：prompt 的每个视觉元素（人物、动作、场景、物体）必须严格来自对应 subtitle 文本。\n"
    "   - subtitle 说什么，画面就拍什么；subtitle 没说的，prompt 不能出现。\n"
    "   - 示例：subtitle 有\"烈日\" → prompt 必须有烈日或强光；subtitle 有\"奔跑\" → prompt 必须有奔跑动作。\n"
    "   - 反例：subtitle 写\"田野\" 但 prompt 写\"宫殿\" → 严重违规，直接废弃。\n"
    "3. 全弧线覆盖：所有镜头均匀覆盖原文完整情节——前段镜头取开头、中段镜头取中段、后段镜头必须取结尾（含冲突/战斗/反转等强事件），禁止全部取自前半段；\n"
    "4. 动漫电影化描述：每个prompt须含景别、机位、运镜、主体动作、环境光影、色彩情绪、原文关键词、情绪词，并已固定前缀开头；风格为精美2D动画，无真人写实；\n"
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
    "   - **不需要 duration 字段**——系统根据 TTS 实际生成长度自动决定每镜时长。\n"
    "只输出纯JSON，不要markdown代码块、不要任何额外文字。"
)


# ========================================================================
# 格式化辅助函数
# ========================================================================

def _format_list(items: list) -> str:
    """将列表格式化为换行符分隔的字符串."""
    return "\n".join(f"  - {item}" for item in items)


def _format_dict(d: dict) -> str:
    """将字典格式化为 key: value 的字符串."""
    return "\n".join(f"  - {k}: {v}" for k, v in d.items())


# ========================================================================
# 构建完整 prompt
# ========================================================================

def build_prompt(novel_text: str, min_shots: int, max_shots: int) -> str:
    """构建最终发送给 LLM 的 prompt，注入镜头语言参考表 + 伴生技能偏好."""
    prompt = PROMPT_TEMPLATE.format(
        novel_text=novel_text,
        min_shots=min_shots,
        max_shots=max_shots,
        novel_char_count=len(novel_text),
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


def _load_local_skill_context() -> str:
    """加载伴生技能作为 prompt 附加上下文（若存在）."""
    try:
        loader_path = _PROJECT_ROOT / ".aiVideo" / "skills_loader.py"
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