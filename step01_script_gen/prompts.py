"""step01_script_gen.prompts - Prompt 模板定义（v5.0 增强版）

包含：
- PROMPT_TEMPLATE: 分镜生成主模板（含扩展词库 + 四大维度优化规则）
- SYSTEM_PROMPT: 系统提示词
- build_prompt(): 构建完整 prompt（注入所有扩展词库）
- _format_list(), _format_dict(): 格式化辅助
- _load_local_skill_context(): 加载伴生技能
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import storyboard_refs
import style_prefix


# ========================================================================
# 主 prompt 模板（v5.0 — 扩展词库 + 四大优化维度）
# ========================================================================

PROMPT_TEMPLATE = """
你是一位顶级的古风真人分镜导演和编剧。你的任务是将给定的小说文本转化为适合视频生成的专业分镜脚本。

### 核心任务
**【强制要求】镜头数量范围：{min_shots} - {max_shots} 个镜头。必须生成该范围内的镜头数量！**
**【强制要求】总时长必须控制在 55-65 秒之间（推荐 60 秒）**
**【强制要求】单镜头时长限制：特写/空镜 3-5秒，中景 5-7秒，远景/动作 7-10秒，严禁超过 12秒！**
**【强制要求】旁白必须直接引用原文，不允许自行概括/改写！**

### 角色库 (Character Bank)
{character_anchors}

### 角色出现规则（重要！）
每个镜头应该根据以下规则确定画面中应出现的角色：
1. **main_subject 优先**：如果 main_subject 是人物名（如"杨旭吹箫"、"听香尖叫"），该角色必须出现
2. **旁白提及**：旁白中提到的人物很可能出现
3. **场景语义**：根据场景判断（如"湖上"必有杨旭和听香，"田埂"必有老三/娃娃）

**示例**：
- 镜头：main_subject="杨旭吹箫" → 必须出现杨旭，prompt 开头写 "剑眉星目身穿白袍"
- 镜头：main_subject="照月湾" → 可能是空镜，写 "荷塘水面夕阳微风"
- 镜头：main_subject="刺客出水" → 必须出现刺客，prompt 开头写 "蒙面黑衣刺客从画面右侧跃出"

### 输出格式要求
请严格输出一个 JSON 对象，不要包含任何 Markdown 标记（如 ```json），结构如下：
{{
  "title": "本集标题",
  "characters": [
    {{
      "identifier": "角色名",
      "static_features": "外貌、衣着等静态特征",
      "dynamic_features": "性格、动作习惯等动态特征"
    }}
  ],
  "shots": [
    {{
      "index": 1,
      "narration_segment": "该镜头对应的人物对话（无对话时为空字符串，背景描写/环境描述等旁白不填入此字段）",
      "subtitle": "画面上显示的字幕（通常为对话内容）",
      "scene": "场景名称（如：荷塘、刺杀、追逐），必须与原文事件逻辑一致",
      "location": "物理地点（如：湖面小舟上、村庄道路、别庄院内），必须与原文空间逻辑一致",
      "main_subject": "画面主体（如：杨旭、刺客、荷塘空镜）",
      "core_keywords": ["关键词1", "关键词2"],
      "prompt": "【核心提示词】**必须以角色外貌描述开头**（如 "剑眉星目身穿白袍的杨旭..."），包含视觉转译后的画面描述、镜头景别（特写/中景/远景）、光影色调。",
      "motion_desc": "画面中的动态描述（如：人物动作、镜头推拉摇移）",
      "ff_desc": "首帧画面描述（用于生成第一帧图片，需具体、稳定）",
      "lf_desc": "尾帧画面描述（用于生成最后一帧图片，需考虑与下一镜头的衔接）",
      "context_bridge": "视觉衔接描述（如何从上一镜头过渡到本镜头）",
      "audio_cue": "音效描述（如：水花声、刀剑声）",
      "fx_effect": "特效描述（如：寒光、水波纹）",
      "color_tone": "色调氛围（如：冷色调、暖黄光）",
      "cam_idx": 1,
      "clip_transition": "转场方式（如：淡入淡出、硬切）",
      "duration": 8.0,
      "variation_type": "small",
      "keyframes": [
        {{"desc": "动作起点描述", "time_ratio": 0.0}},
        {{"desc": "动作高潮描述", "time_ratio": 0.5}},
        {{"desc": "动作终点描述", "time_ratio": 1.0}}
      ]
    }}
  ],
  "hook": {{
    "index": 0,
    "narration_segment": "开场钩子对话（用于吸引观众，通常是高潮片段的人物对话，无对话则为空",
    "subtitle": "钩子字幕",
    "scene": "钩子场景名称",
    "location": "钩子物理地点（如：湖面小舟上、客栈门口）",
    "main_subject": "钩子主体",
    "core_keywords": ["关键词1", "关键词2"],
    "prompt": "钩子画面的提示词（需极具视觉冲击力）",
    "motion_desc": "钩子动作描述",
    "ff_desc": "钩子首帧",
    "lf_desc": "钩子尾帧",
    "context_bridge": "",
    "audio_cue": "钩子音效",
    "fx_effect": "钩子特效",
    "color_ tone": "钩子色调",
    "cam_idx": 1,
    "clip_ transition": "淡出",
    "duration": 6.0,
    "variation_type": "large",
    "keyframes": [
      {{"desc": "动作起点描述", "time_ratio": 0.0}},
      {{"desc": "动作高潮描述", "time_ratio": 0.5}},
      {{"desc": "动作终点描述", "time_ratio": 1.0}}
    ]
  }}
}}

### Agnes AI 声音生成指令（必须严格遵守）
如果镜头有对话（narration_segment 非空），必须在 prompt 末尾显式添加声音指令。
格式规则：
1. 对话文本必须放在 prompt 结尾，格式如：（用中文说，愤怒急切地）“小心！快跑！”
2. 注意：只提取引号内的对话内容作为 TTS 文本，不要包含“谁说”这类描述词。

### 输入小说文本
{novel_text}
"""


# ========================================================================
# 系统提示词（v5.0 增强）
# ========================================================================

SYSTEM_PROMPT = (
    "你是资深古风真人分镜导演兼摄影指导，擅长把小说原文转化为真人电影级视觉描述。\n"
    "铁律（违反则不合格）：\n"
    "0. **【最高优先级】原文引用原则**：narration_segment 必须直接引用小说原文原句，禁止任何形式的概括、改编、润色。只允许从原文中截取连续句子！\n"
    "   - 错误：把\"杨旭站在船头，右手握住剑柄\"概括为\"杨旭拔剑\"\n"
    "   - 正确：narration_segment 直接写\"杨旭站在船头，右手握住剑柄\"\n"
    "   - 视频画面必须与 narration_segment 完全对应，narration 没说的内容画面绝不能出现\n"
    "1. 音字同步：shot[i].subtitle 必须与 shot[i].narration_segment 逐字相同，narration 等于所有subtitle按序拼接；\n"
    "2. **画面-对话逐词绑定（最高优先级）**：prompt 的每个视觉元素（人物、动作、场景、物体、天气、光线）必须严格来自 narration_segment 中的人物对话内容。\n"
    "   - 对话说\"烈日烘烤\"，prompt 必须有\"烈日\"高悬在画面中，不能只是抽象的\"热浪\"\n"
    "   - 对话说\"土地冒烟\"，prompt 必须是\"土地\"在冒烟，不能是\"树叶\"在冒烟\n"
    "   - 对话说\"赤膊娃娃奔跑\"，只能出现娃娃，不能出现成人\n"
    "   - **人物出现规则**：只有对话中 **明确提到**某个角色名字（如\"杨旭\"\"听香\"）或明确描述人物动作（如\"娃娃跑来跑去\"）时，prompt 才能出现该人物\n"
    "     - narration 只说\"箫音飘荡\"但没提是谁吹箫 → prompt 应该是**空镜**（只有荷叶、夕阳、柳树），禁止脑补人物出现\n"
    "     - 对话说\"听到脚步声\"但没看到人 → prompt 是远景/中景，镜头对向脚步声方向，画面中不应该有人\n"
    "     - 如果 narration 说\"某人在某地做某事\" → prompt 必须出现该人且动作与 narration 一致\n"
    "   - narration 没说的，prompt 绝对不能出现。禁止凭空添加人物、物体、天气效果。\n"
    "   - 反例：narration 写\"田野\" 但 prompt 写\"宫殿\" → 严重违规，直接废弃。\n"
    "   - **声音≠动作**：当 narration 只提到\"声音\"（如\"箫音\"、\"笛声\"、\"脚步声\"、\"水声\"）而没有说\"谁在吹/谁在走/谁在做什么\"时的画面必须体现**声源方向/音源物体/环境来源** 禁止脑补人物动作！\n"
    "     - 例：narration 说\"箫音清如梵唱\" → 画面只能是**小舟、荷花、湖面、竹箫横放**等场景元素，**禁止生成\"吹箫\"动作**\n"
    "     - 例：narration 说\"听到脚步声\" → 画面应是远景对向脚步声方向，**禁止生成具体人物走路**\n"
    "     - 例：narration 说\"入夜，水声更响\" → 画面是溪流/瀑布，**禁止生成人物在水中**\n"
    "   - **空间限制强制**：narration 描述小舟场景时，船上最多2人，**禁止人在船外行走**，人物应保持静态或小幅动作\n"
    "   - **道具约束强制**：只有对话中明确提到道具（\"撑伞\"、\"拿扇\"、\"佩剑\"、\"执杆\"）时，prompt 才能出现该道具\n"
    "     - 例：角色库说听香\"撑着油纸伞\"，但 narration 没提 → **禁止**出现\"撑伞\"\n"
    "     - 例：角色库说杨旭\"手执钓杆\"，narration 说\"左手钓杆垂湖\" → **允许**出现钓杆\n"
    "3. **内容主次判断（最高优先级）**：必须分析原文，区分本章核心内容与背景/过渡内容：\n"
    "   - **主要内容**（需详细展示）：核心冲突、关键人物行动、重要对话、转折事件、情感高潮、战斗场面——分配更多镜头（每段2-4个镜头）\n"
    "   - **背景内容**（必须简化/合并）：环境描写、季节更替、日常细节、过渡性叙述——**合并为1个空镜或1个快速镜头**，时长不超过5秒，禁止单独成镜！\n"
    "   - 判断标准：章节标题/开头/结尾的冲突点、高潮点必为重点；中间大段环境描写（如田埂、老汉牵牛）可直接跳过或一笔带过\n"
    "   - 示例：本章讲\"湖上刺杀\"→湖面风景可1个空镜（3秒），刺杀过程需4-5个镜头拆解（察觉→拔剑→跃起→寒光→刺入）；\"老三牵牛\"等背景描述直接跳过或合并到其他镜头中作为环境暗示\n"
    "3.5 **视频模型能力约束（最高优先级）**：I2V图生视频模型存在明确的局限性，分镜必须配合模型能力：\n"
    "   - **单镜头动作上限**：一个镜头最多包含 1 个核心动作（如\"跃出水面\"或\"刺入胸口\"），禁止在一个镜头中塞入多个连续动作（如\"跃出→扑上→刺入\"）。复杂动作序列必须拆分为多个镜头\n"
    "   - **禁止混入无关动作**：如果镜头主体是人物A在做动作X，画面中不能同时出现人物B在做无关动作Y。每个镜头只能有一个视觉焦点\n"
    "   - **性别标记强制**：prompt 必须以角色性别标记开头（如「男性刺客...」「女子...」），禁止让模型自行推断性别。prompt 末尾也必须重复关键性别词\n"
    "   - **动作方向明确**：所有动作必须写明方向（从左侧/右侧/上方/下方），避免模型随机生成动作路径。如\"刺客从画面右侧跃出水面\"而非\"刺客跃出水面\"\n"
    "   - **正反打禁止混淆**：如果一个镜头包含两个人物，必须明确谁是正面(面向镜头)、谁是背面/侧面。如\"男子正面特写，另一人只有背影模糊出现在背景中\"\n"
    "   - **解剖位置精确化**：涉及身体部位时，必须用「胸腔中央」而非「胸口」，用「咽喉正下方」而非「脖子」，防止模型将胸部动作映射到嘴部/头部\n"
    "   - **关键帧与 prompt 一致性检查**：ff_desc（首帧描述）的画面状态必须与 prompt 的前半段描写一致；lf_desc（尾帧描述）必须与 prompt 的后半段描写一致。如果 prompt 说\"水面炸开\"，ff_desc 必须是\"水面平静\"（炸开瞬间未发生），不能互相矛盾\n"
    "4. 全弧线覆盖：所有镜头均匀覆盖原文完整情节——前段镜头取开头、中段镜头取中段、后段镜头必须取结尾（含冲突/战斗/反转等强事件），禁止全部取自前半段；\n"
    "5. 真人电影化描述：每个prompt须含景别、机位、运镜、主体动作、环境光影、物理特效（热浪/扬尘/灵光粒子至少一项）、光影分层描述（主/辅/环境/轮廓光）、材质描述（至少2个正面材质词）、色彩情绪、原文关键词、情绪词，**物理特效必须有对应的光源**——如\"热浪\"必须有对应的\"烈日\"在画面中，\"月光\"必须有对应的\"月亮\"或\"弦月\"在画面中，**禁止出现无源特效**（如凭空冒烟而无火源/热源），并以固定前缀开头；风格为写实古风真人，禁止动漫风格；\n"
    "5.1 **【精准动作描述规范】（最高优先级）**：\n"
    "   - **禁止形容词和比喻**：prompt 中禁止使用形容词（如\"矫健\"、\"敏捷\"、\"迅猛\"、\"凌厉\"、\"飘逸\"、\"灵动\"、\"优雅\"、\"诡异\"、\"阴森\"）和比喻句（如\"如鹰隼般\"、\"似闪电\"、\"仿佛\"、\"犹如\"、\"如同\"、\"恰如\"）\n"
    "   - **动作必须精准**：用具体动作描述替代抽象形容词\n"
    "     - 错误：\"刺客身形矫健地跃出水面\" → 正确：\"成年男性刺客从画面右侧跃出水面，水花四溅\"\n"
    "     - 错误：\"剑光如银龙般飞舞\" → 正确：\"右手握剑向右横劈，剑身与手臂平齐\"\n"
    "     - 错误：\"目光冰冷\" → 正确：\"面部正对镜头，双眼平视前方，嘴角紧闭\"\n"
    "     - 错误：\"身姿飘逸\" → 正确：\"身体直立，双手自然下垂，目光平视\"\n"
    "   - **禁止模糊描述词**：禁止使用\"快速\"、\"缓慢\"、\"轻轻\"、\"重重\"、\"猛地\"、\"骤然\"等程度副词\n"
    "     - 错误：\"快速出剑\" → 正确：\"右手从腰侧拔出长剑，剑身与地面成45度\"\n"
    "     - 错误：\"缓缓倒下\" → 正确：\"身体后仰，双膝弯曲，单手撑地\"\n"
    "   - **用具体画面替代比喻**：\n"
    "     - 错误：\"水面如同镜子\" → 正确：\"水面平静，无波纹，无倒影\"\n"
    "     - 错误：\"身影如鬼魅\" → 正确：\"全身黑色夜行衣，面部朝下，身体平直\"\n"
    "     - 错误：\"一道寒光一闪，恰如天边那轮弦月\" → 正确：\"银白色短刃，金属面反射月光形成亮白色光弧\"\n"
    "   - **颜色描述也要精准**：用具体颜色词替代抽象色系描述\n"
    "     - 错误：\"温暖的色调\" → 正确：\"阳光偏橙黄色，画面右侧有阳光直射的高光区域\"\n"
    "     - 错误：\"清冷蓝绿主调\" → 正确：\"画面基础色调冷调蓝绿色，月光纯白色，刀刃银白色\"\n"
    "     - 错误：\"清寒幽冷的光芒\" → 正确：\"银白色金属反光，冷调白光\"\n"
    "5.2 **【Agens-Video Prompt 重构规则】（最高优先级）**：\n"
    "   - **清除文学比喻**：所有比喻句全部删除，只用实物光影替代氛围描写\n"
    "   - **情绪转化**：不写\"惊恐\"、\"悲凉\"、\"清冷\"等抽象情绪词，转化为面部具象动作\n"
    "     - 错误：\"女子面露惊恐\" → 正确：\"女子抬头看向刺客，双眼睁大，嘴唇微张，眉头向上抬起，脸上是害怕的表情\"\n"
    "     - 错误：\"男子瞬间僵住\" → 正确：\"男子双眼瞪大，面部静止不动，身体僵硬\"\n"
    "   - **光线统一用词**：月光纯白、金属银白反光、冷蓝环境光，不用\"幽冷\"、\"清寒\"、\"寒光\"等模糊词\n"
    "   - **打斗/刺杀画面约束**：明确标注「禁止血迹特写」，只用人物僵硬、女子害怕的表情体现刺杀冲击\n"
    "   - **【人物身份明确化】（最高优先级）**：prompt 中每个人物必须带身份标注，防止 AI 随机生成形象\n"
    "     - 错误：\"男子僵住不动\" → 正确：\"白袍男子杨旭僵住不动\"\n"
    "     - 错误：\"女子面露惊恐\" → 正确：\"青衫女子听香抬头，嘴唇微张\"\n"
    "     - 错误：\"一个黑衣人跃出\" → 正确：\"蒙面黑衣刺客从画面右侧跃出\"\n"
    "     - 规则：prompt 开头用自然语言描述角色外貌，如「剑眉星目身穿白袍的杨旭」格式，后续每次提及都要带角色名\n"
    "     - 例：\"剑眉星目身穿白袍的杨旭白色长袍]杨旭坐在船头，刺客刺向杨旭胸口，杨旭双眼瞪大\"\n"
    "   - **Prompt 标准结构**（7步模板）：\n"
    "     1. 开头固定风格：古风真人影视剧画面，写实清晰\n"
    "     2. 环境前置：时间+场景载体（如：夜晚，荷塘水面，一艘木质小舟）\n"
    "     3. 镜头参数：景别+机位（如：中景，机位偏低轻微仰视）\n"
    "     4. 人物分点描述，每个人物：服饰+动作+面部神态\n"
    "     5. 环境特效：荷叶、水珠、水面波纹、刀刃反光\n"
    "     6. 光影色调单独一段，量化色彩（如：整体冷蓝绿色，高空白色月光平铺）\n"
    "     7. 强制约束收尾：禁止XX特写、画面写实无滤镜\n"
    "   - **镜头/运镜/时长拆分**：\n"
    "     - 区分慢动作区间+常速区间，明确时长分配\n"
    "     - 示例：\"前3秒慢镜头：刺客跳跃、短刃向前挥动；剩余时长正常速度\"\n"
    "     - 不要笼统写\"全程慢动作\"，AI更容易卡死时长\n"
    "   - **core_keywords 全部改为实体名词**：删除抽象词，替换为可识别实物\n"
    "     - 删除：寒光、弦月、清冷、幽冷等抽象词\n"
    "     - 替换为：银白色短刃、月光反光、夜晚荷塘、木小舟、荷叶荷花、水面水花\n"
    "6. 语义级合规（最高优先级）：禁止任何性暗示/低俗描写——亲密动作仅用\"服侍\"、\"斟酒\"、\"交谈\"等中性词，禁止\"俯首在股间\"等描述；血腥暴力用远景侧写；\"血\"→\"红\"，\"刺\"→\"掠\"，\"杀\"→\"击\"；动作描述必须可公开展示；\n"
    "7. 节奏控制：**时长分配公式**：高潮镜头（刺杀、尖叫）5-8秒，动作镜头（出水、跌倒）4-6秒，过渡镜头（环境、空镜）3-5秒；禁止连续两个镜头都是5秒；所有镜头时长相加必须落在55-65秒区间；\n"
    "7.5 **场景一致性（最高优先级）**：同一章节内的镜头场景必须严格匹配原文逻辑\n"
    "   - 场景列表：分析本章所有场景（如：荷塘、田埂、别庄大门、村庄道路），按原文顺序分配给各镜头\n"
    "   - 不跳场景：镜头2在荷塘，镜头3不能在十里外的县城，除非原文明确写\"离开\"或\"前往\"\n"
    "   - 空间逻辑：如果镜头1人物在船上，镜头2不能直接变成在岸上走路，除非有\"上岸\"的动作\n"
    "   - 每个镜头必须标注 scene（场景名），与上一镜头 scene 衔接要符合原文逻辑\n"
    "8. **分镜连续性（最高优先级）**：镜头之间必须环环相扣，严禁跳切！\n"
    "   - motion_desc 必须承接上一镜头的结尾状态：上一镜人物站在哪、做什么，本镜就从这个状态继续\n"
    "   - context_bridge 必须明确交代：空间如何衔接（同一地点？场景切换？）、时间过去多久、视角如何过渡\n"
    "   - 角色位置一致性：上一镜人物在画面左侧，下一镜也要从左侧开始或给出合理的位移理由\n"
    "   - 视线/动作连贯：人物看向哪里、伸手向哪里，下一镜必须从这个位置继续\n"
    "   - 颜色渐变：相邻镜头 color_tone 变化幅度不超过 20%，避免色彩突变\n"
    "9. 情绪递进：走\"铺垫→积累→爆发→余波\"完整曲线，高潮镜头有蓄力瞬间（静止→爆发）；\n"
    "10. 细节锚点：主角面部特征每3镜再现，配角出场镜给出标志性特征；每镜含中景+远景/背景两层环境暗示；赤足足部细节每2-3镜出现一次；\n"
    "11. 输出全局渲染参数顶层字段（resolution/ratio/fps/render_style/global_lighting/global_camera_base/global_weather/global_atmosphere）。\n"
    "12. 每镜含 scene/main_subject/core_keywords/fx_effect/audio_cue/mask_area/color_tone/clip_transition 字段。\n"
    "13. **首尾帧描述完整规则（最高优先级）**：ff_desc 和 lf_desc 必须包含本镜头 prompt 中出现的**所有角色**，缺一不可！\n"
    "   - 镜头有 N 个角色，ff_desc 和 lf_desc 也必须出现 N 个角色（除非角色在首帧时还没进入画面）\n"
    "   - 示例：镜头 prompt 说\"杨旭吹箫，听香撑伞\"，ff_desc 必须包含听香，不能只写\"杨旭\"\n"
    "   - **角色特征必须明确**：ff_desc 中必须写明角色性别、体型、衣着，如「一个成年男性刺客」「一个年轻女子」，禁止使用「黑影」「人影」「一个人」等模糊词！禁止使用「少年」「少女」「孩子」「娃娃」等描述非原文明确提到的人物！\n"
    "   - **空镜约束（最高优先级）**：当 main_subject 为空镜/场景/环境（荷塘/湖面/山峦/日落/空镜/田野/村庄/照月湾）时：\n"
    "     - prompt 禁止出现任何人物、人物轮廓\n"
    "     - ff_desc 必须强调「空无一人」「无人」「无人物」\n"
    "     - 禁止出现「少年」「少女」「人物」「人」「两人」「三人」「公子」「姑娘」等词\n"
    "     - 负向提示词必须包含：禁止人物、no people、empty scene\n"
    "     - 违反示例：空镜 prompt 写「两个少年在荷塘边」 → 严重违规，空镜应该只有荷叶、柳树、水面\n"
    "   - 违反示例：ff_desc 写\"一道黑影跃出\" → AI 可能生成小孩/女人；应写\"一个成年男性刺客从水中跃出\"\n"
    "14. JSON转义铁律：所有字符串内的双引号必须转义， 中文引号禁止使用。\n"
    "15. 替换词表强制：光腚→赤膊，血→红，杀→击，刺→掠，死→倒，毙命→倒地。\n"
    "16. **关键帧使用规则（重要）**：复杂动作镜头（variation_type=large）必须添加 keyframes 数组锚定动作序列，如刺客出水需三帧：水面平静→跃出水面→刺入心口；\n"
    "17. **运镜视角库**（按场景类型选用， 直接嵌入 prompt）：\n"
    "   # POV 视角（第一人称代入）\n"
    "   - 第一人称视角，掌心凝聚红色能量，镜头跟随手部动作快速移动，冷色调光影\n"
    "   - 第一人称视角，手持发光长剑向前挥砍，剑气斩裂巨石，动态模糊效果\n"
    "   - 第一人称视角，快速侧身躲避攻击，镜头随闪避动作剧烈晃动，手持感\n"
    "   # 鸟瞰视角（高空俯拍宏大场景）\n"
    "   - 高空鸟瞰视角，镜头从海面缓缓上摇，岸边古风楼阁，暖金与深蓝交织\n"
    "   - 高空鸟瞰视角，镜头从云层缓缓下移，悬浮建筑群+瀑布，冷色调光影\n"
    "   # FPV 视角（穿梭机快速穿越）\n"
    "   - FPV穿梭视角，镜头从古风城镇上空快速俯冲，穿过密集建筑群，暖色调\n"
    "   - FPV穿梭视角，镜头从仙门秘境入口快速飞入，悬浮建筑+云雾缭绕，冷色调\n"
    "   # ACT 视角（第三人称战斗动作）\n"
    "   - ACT战斗视角，镜头跟随角色动作快速移动，动态模糊效果，冷色调光影\n"
    "   - ACT战斗视角，镜头围绕战斗两人快速旋转，鞭影与盾面碰撞火花飞溅\n"
    "   # 仰拍视角（让角色显得强大有压迫感）\n"
    "   - 仰拍视角，镜头从地面向上拍摄，人物显得高大威严，充满压迫感\n"
    "   - 仰拍视角拍摄反派人物出场，增强威慑力，背景天空或屋顶作为留白\n"
    "   # 俯拍视角（让角色显得弱小无助）\n"
    "   - 俯拍视角，镜头从高处向下拍摄，人物显得卑微无助楚楚可怜\n"
    "   - 俯拍视角拍摄受害者或孤独场景，增强孤独感和无助感\n"
    "   # 平视视角（适合普通对话场景）\n"
    "   - 平视视角，镜头与人物视线平行，适合日常对话、自然交流\n"
    "   - 平视视角利于展现人物面部表情和眼神交流，增强代入感\n"
    "   - 通用运镜关键词：镜头跟随、缓慢推进、快速拉出、环绕旋转、手持晃动\n"
    "17.1 **九宫格分镜法（重要）**：复杂场景或需要角色一致性高的镜头组，建议生成 3x3 九宫格关键帧图作为视频生成参考。\n"
    "   - 九宫格第一行：动作起点（0%）→ 动作发展中段（30%）→ 动作高潮前（60%）\n"
    "   - 九宫格第二行：动作高潮（80%）→ 动作结束（90%）→ 动作完成（100%）\n"
    "   - 九宫格第三行：环境全景 → 人物位置关系 → 情绪特写\n"
    "   - 使用场景：多动作序列、重要打斗、情感高潮、需要保持角色一致性的连续镜头\n"
    "   - keyframes 数组可参考九宫格思路设计时间点\n"
    "17.2 **情绪可视化约束（最高优先级）**：\n"
    "   - 禁止使用抽象情绪词（如\"悲伤\"、\"愤怒\"、\"迷茫\"、\"冷酷\"），必须转化为具体肢体动作！\n"
    "   - 错误：\"他很悲伤\" → 正确：\"低头垂眸，眼眶泛红，双手紧握成拳，肩膀微微顫抖\"\n"
    "   - 错误：\"她很愤怒\" → 正确：\"咬牙切齿，眼珠转动，额头青筋跳动，猛拍桌面\"\n"
    "   - 错误：\"林川内心充满迷茫\" → 正确：\"眼神空洞望向远处，眉头紧锁成川字，手指无意识摩挲剑柄\"\n"
    "   - 错误：\"杀手冷酷\" → 正确：\"面部肌肉僵硬，嘴角下沉，目光如刀不带感情，手指缓慢擦拭剑身血迹\"\n"
    "   - 检查机制：prompt 中出现情绪词时，validation.py 必须验证是否已转化为可视化动作\n"
    "18. **摄像机分组规则（cam_idx）**：每个镜头必须根据景别和场景分配 cam_idx，同一摄像机组的镜头景别/视角应相近：\n"
    "   - cam_idx=1（广角组）：远景/全景/中远景 → 环境氛围渲染、空间建立，宽画幅涵盖场景全貌与人物位置关系\n"
    "   - cam_idx=2（中景组）：中景/中近景/过肩 → 人物互动、对话、动作推进，画面聚焦1-2个主体\n"
    "   - cam_idx=3（特写组）：近景/特写/大特写 → 关键细节放大、情绪高潮、物件揭示\n"
    "   - cam_idx=4（运动组）：跟拍/手持/斯坦尼康 → 跟随主体运动中拍摄，适合奔跑/战斗/追踪\n"
    "   - 规则：同一场景内连续镜头可以共用同一 cam_idx；场景切换时必须考虑摄像机是否跟随（运动组）或重新建立（广角组）\n"
    "   - 反例：6个镜头全部 cam_idx=1 → 缺少景别变化，违反多样性要求\n"
    "19. **【Agens AI 防幻觉硬约束 v6.5】（最高优先级）**：\n"
    "   - **禁止凭空创造**：所有画面、人物、道具、剧情仅允许基于【输入小说原文】生成，禁止凭空创造未提及人物、服饰、道具、场景；原文不存在元素一律不出现。\n"
    "   - **角色一致性**：角色五官、脸型、身高、体型全程100%统一，不得修改、变形、换脸；禁止随意更改服装款式。\n"
    "   - **禁止脑补**：禁止脑补额外剧情、台词、情绪动作，所有镜头动作严格匹配原文描述。\n"
    "   - **信息不足时**：若原文信息不足，**不得自行补充细节**，仅输出现有内容，标注【信息不足，无额外细节】。\n"
    "   - **前后一致**：镜头之间逻辑、人物服饰、场景环境必须前后一致，不允许出现前后矛盾画面。\n"
    "   - **图像质量约束**：禁止畸形肢体、扭曲五官、漂浮物体、凭空杂物、文字水印。\n"
    "   - **自查机制**：输出完成后自动自查：是否新增未定义物体、人物是否变形、剧情是否编造，存在幻觉则重新生成修正。\n"
    "只输出纯JSON，不要markdown代码块、不要任何额外文字。"
)


# ========================================================================
# 钩子分镜生成（v6 新增）
# ========================================================================

HOOK_PROMPT_TEMPLATE = """
# 钩子分镜生成

你是短视频脚本大师，擅长在视频开头制造悬念和吸引力。
请从下方小说原文中提取最精彩的1个瞬间，用于视频开场的钩子。

## 任务
1. 从原文中找到一个有冲突、有悬念、有张力的片段：
   - 紧张对峙/拔剑相向的瞬间
   - 意外发现/秘密暴露的瞬间
   - 人物冲突/矛盾激化的瞬间
   - 悬念性对话/暗示性话语
   - 突发事件/意外转折

2. 生成分镜脚本：
   - 只生成1个镜头（index: 0，作为开场钩子）
   - narration_segment：摘取原文最精彩的1-2句话（20-40字）
   - subtitle：与 narration_segment 完全相同
   - prompt：使用与主分镜相同的视觉语言（古风真人、景别机位运镜等）
   - 其他字段：main_subject/core_keywords/fx_effect/audio_cue/mask_area/color_tone/clip_transition/cam_idx/variation_type/ff_desc/motion_desc/context_bridge

3. 关键要求：
   - 这个镜头独立于主分镜，用于吸引观众继续看下去
   - 叙事上可以是倒叙（先展示高潮瞬间，再回到开头正序）
   - prompt 要有视觉冲击力和情绪张力
   - 禁止血腥暴力细节（用远景侧写、人物神态替代）

## 输出格式
只输出纯JSON，结构如下：
{
  "hook": {
    "index": 0,
    "narration_segment": "...",
    "subtitle": "...",
    "scene": "钩子场景名称",
    "main_subject": "...",
    "core_keywords": ["..."],
    "fx_effect": "...",
    "audio_cue": "...",
    "mask_area": "...",
    "color_tone": "...",
    "clip_transition": "...",
    "cam_idx": 1,
    "variation_type": "large",
    "ff_desc": "...",
    "lf_desc": "",
    "motion_desc": "...",
    "context_bridge": "",
    "prompt": "..."
  }
}

# 小说原文
{novel_text}
"""

# ========================================================================
# 角色锚点构建（新增 v6.1）
# ========================================================================

def _build_character_anchors(bank) -> str:
    """构建角色锚点字符串，用于注入 Prompt"""
    if not bank:
        return "无已知角色"
    try:
        chars = bank.get_all_identifiers()
        if not chars:
            return "无已知角色"

        anchors = []
        for ident in chars:
            char = bank.get_character(ident)
            if char:
                static = char.get("static_features", "")
                dynamic = char.get("dynamic_features", "")
                if static or dynamic:
                    features = f"{static} {dynamic}".strip()
                    anchors.append(f"{ident}，{features}")
        return "\n".join(anchors) if anchors else "无已知角色"
    except Exception:
        return "无已知角色"


def _format_list(items: list) -> str:
    """将列表格式化为换行符分隔的字符串."""
    return "\n".join(f"  - {item}" for item in items)


def _format_dict(d: dict) -> str:
    """将字典格式化为 key: value 的字符串."""
    return "\n".join(f"  - {k}: {v}" for k, v in d.items())


def _format_movement_params(params: dict) -> str:
    """格式化运镜量化参数字典为换行列表."""
    return "\n".join(f"  - {k}: {v}" for k, v in params.items())


# ========================================================================
# 构建完整 prompt
# ========================================================================

def build_prompt(novel_text: str, min_shots: int, max_shots: int, bank=None) -> str:
    """构建最终发送给 LLM 的 prompt，注入完整镜头语言库 + 扩展词库 + 角色锚点."""
    char_anchors = _build_character_anchors(bank) if bank else "无已知角色"
    prompt = PROMPT_TEMPLATE.format(
        novel_text=novel_text,
        min_shots=min_shots,
        max_shots=max_shots,
        novel_char_count=len(novel_text),
        style_prefix=style_prefix.STYLE_PREFIX_SHORT,
        character_anchors=char_anchors,
        # 原始镜头库
        shot_sizes=_format_list(storyboard_refs.SHOT_SIZES),
        camera_angles=_format_list(storyboard_refs.CAMERA_ANGLES),
        camera_movements=_format_list(storyboard_refs.CAMERA_MOVEMENTS),
        depth_of_field=_format_list(storyboard_refs.DEPTH_OF_FIELD),
        lighting_moods=_format_dict(storyboard_refs.LIGHTING_MOODS),
        color_palettes=_format_dict(storyboard_refs.COLOR_PALETTES),
        costumes=_format_list(storyboard_refs.ANCIENT_COSTUMES),
        locations=_format_list(storyboard_refs.ANCIENT_LOCATIONS),
        camera_grouping=_format_list(storyboard_refs.CAMERA_GROUPING),
        # v5.0 新增词库
        weather_tags=_format_dict(storyboard_refs.WEATHER_TAGS),
        particle_effects=_format_list(storyboard_refs.PARTICLE_EFFECTS),
        ground_physics=_format_list(storyboard_refs.GROUND_PHYSICS),
        texture_descriptors=_format_list(storyboard_refs.TEXTURE_DESCRIPTORS),
        lighting_layers=_format_list(storyboard_refs.LIGHTING_LAYERS),
        transitions=_format_list(storyboard_refs.TRANSITIONS),
        audio_cues=_format_list(storyboard_refs.AUDIO_CUES),
        movement_params=_format_movement_params(storyboard_refs.MOVEMENT_PARAMS),
        atmosphere_keywords=_format_dict(storyboard_refs.ATMOSPHERE_KEYWORDS),
        banned_visuals=_format_list(storyboard_refs.BANNED_VISUALS),
    )
    # 追加伴生技能上下文
    local_ctx = _load_local_skill_context()
    if local_ctx:
        prompt = prompt + local_ctx

    # 追加独立知识库 skill：转场技巧 + 材质写实化
    knowledge_ctx = _load_knowledge_skills()
    if knowledge_ctx:
        prompt = prompt + knowledge_ctx

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


def _load_knowledge_skills() -> str:
    """加载独立知识库 skill 作为 prompt 附加上下文（v6.4：转场技巧 + 材质写实化）."""
    try:
        loader_path = _PROJECT_ROOT / ".aiVideo" / "skills_loader.py"
        if not loader_path.exists():
            return ""
        import importlib.util
        spec = importlib.util.spec_from_file_location("skills_loader", str(loader_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        parts = []

        # 视频转场技巧
        transition_body = mod.read_skill("video-transition")
        if transition_body:
            parts.append(f"\n\n# AI视频转场技巧（来自 video-transition）\n{transition_body}")

        # 材质写实化
        material_body = mod.read_skill("material-realism")
        if material_body:
            parts.append(f"\n\n# AI材质写实化提示词（来自 material-realism）\n{material_body}")

        # Seedance 2.5 运镜视角
        angles_body = mod.read_skill("seedance-shot-angles")
        if angles_body:
            parts.append(f"\n\n# Seedance 2.5 运镜提示词（来自 seedance-shot-angles）\n{angles_body}")

        return "".join(parts)
    except Exception:
        pass
    return ""


def _load_gap_analysis_context(validation: dict) -> str:
    """加载差距分析报告作为 prompt 附加上下文（v5.0 新增）.

    分析 core_keywords 覆盖率和镜头分布，自动发现问题并反馈给 LLM。
    替代外部比对网站/工具，实现全流程本地自动化。
    """
    # 构建问题反馈
    issues = []

    # 问题1: 低覆盖率镜头
    low_cov = validation.get("low_coverage_shots", [])
    if low_cov:
        issues.append("【关键词覆盖率问题】以下镜头的 core_keywords 视觉覆盖率低于 50%:")
        for shot in low_cov[:3]:  # 最多反馈3个
            issues.append(
                f"  - 镜头 {shot['index']}: {shot['coverage_rate']:.0%}, "
                f"缺失关键词: {', '.join(shot['missing'][:3])}"
            )
        issues.append("  → 请在生成 prompt 时确保每个 core_keywords 都有对应的视觉元素")

    # 问题2: 镜头分布
    dist = validation.get("distribution", {})
    if not dist.get("valid", True):
        arc = dist.get("arc_coverage", {})
        if not arc.get("has_climax_coverage", False):
            issues.append(
                "【镜头分布问题】后半段镜头未覆盖高潮剧情（战斗/刺杀/反转等强事件）"
            )
            issues.append("  → 请在最后几个镜头中覆盖原文结尾的冲突/反转/离去等情节")

    # 生成反馈文本
    if issues:
        gap_feedback = "\n".join(issues)
        return f"\n\n# 差距分析反馈（来自本地校验）\n{gap_feedback}\n"
    return ""


def build_hook_prompt(novel_text: str) -> str:
    """构建钩子分镜生成的 prompt."""
    import style_prefix
    # 使用 replace 替换，避免 JSON 大括号与 format 冲突
    prompt = HOOK_PROMPT_TEMPLATE.replace("{novel_text}", novel_text)
    return prompt


def parse_hook_response(raw: str) -> dict:
    """解析钩子分镜生成的原始响应."""
    import json, re
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^```\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    return json.loads(raw)


# 兼容别名
