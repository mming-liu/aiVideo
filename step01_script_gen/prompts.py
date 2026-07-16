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
# 角色定位
你是资深国风动漫分镜导演兼动画摄影指导，擅长把小说原文转化为动漫电影级视觉描述。请严格基于下方小说原文，生成一部 9:16 竖屏抖音短视频的动漫电影级分镜脚本。画面风格为精美2D国风动画，非真人写实。

# 角色一致性强制锚点（必须严格遵守）
{character_anchors}
上述角色特征必须体现在每个涉及该人物的镜头 prompt 中，確保画风统一。

# Agnes AI 声音生成指令（必须严格遵守，v6.1 细化格式）
格式规则：
1. 纯旁白/叙述：（音效：蝉鸣声，风声，热浪蒸腾声）
2. 包含对话：保留旁白文本，prompt 结尾必须追加指令。
   - 正确格式：（用中文说）"对话内容"
   - 正确格式：（用中文说，愤怒急切地）"对话内容"
   - 错误格式：prompt 中包含"杨旭说"作为 TTS 读出的文本
   - 示例：narration_segment = "杨旭大喊：小心！快跑！"
     prompt 结尾追加: （用中文说，惊恐急切地）"小心！快跑！"
     不要让 TTS 读出"杨旭说"或"杨旭大喊"，只提取引号内的对话内容。

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
3. 全弧线覆盖：N 个镜头均匀覆盖原文完整情节弧线——前 1/3 镜头取开头（氛围渲染、背景铺设）、中间 1/3 取中段（事态发展、人物互动）、后 1/3 必须取结尾（冲突/战斗/刺杀/逃离/反转等强事件）；禁止全部镜头取自前半段。
4. 动漫电影化描述：每个 prompt 须含景别、机位、运镜、主体外貌与动作链、环境光影、色彩情绪、原文关键词、情绪词；风格为2D国风动画，禁止真人写实描述。
5. **平台合规（语义级禁令，违反则被AI审核拦截）**：
  - **禁止性描写**：不得出现任何性暗示、裸露画面、亲密接触、身体部位特写。拥抱亲吻一律退化为眼神交流或转身离去。
  - **禁止血腥暴力细节**：不得出现刺入/砍伤/折断等暴力动作的生理细节描述（如"刺进心口""鲜血飞溅""伤口可见"）。打斗场面用远景侧写——剑光一闪/身影交错/水面碎纹/袖风猎猎——绝不写流血细节。
  - **词汇替换**："赤足/赤膊"代"光腚/裸"，"红"代"血"，"交锋/出手"代"杀/刺"。

# 第二步：镜头语言库（必须从中选用并写入 prompt）

## 景别（N 镜至少用 ceil(N/2) 种不同景别，禁止相邻同景别）：
{shot_sizes}

## 机位角度：
{camera_angles}

## 运镜方式（含量化参数参考）：
{camera_movements}

## 景深/构图：
{depth_of_field}

## 光影氛围（按时段选择）：
{lighting_moods}

## 色彩情绪：
{color_palettes}

## 人物外貌/服装参考：
{costumes}

## 场景元素参考：
{locations}

## 摄像机分组指导：
{camera_grouping}

## 天气/气候标签（本片全局为"酷暑燥热"，每镜可微调）：
{weather_tags}

## 动态粒子/特效系统（每镜至少选 1 个注入 prompt）：
{particle_effects}

## 地面物理动态描述模板（本片主角赤足行走，必须参考）：
{ground_physics}

## 古风材质/质感关键词（正向约束，每镜至少用 2 个）：
{texture_descriptors}

## 光影分层描述（必须分层描述，不能笼统写"光影"）：
{lighting_layers}

## 镜头转场方式（每镜必须指定 clip_transition）：
{transitions}

## 环境配套音效（每镜输出 audio_cue 字段）：
{audio_cues}

## 运镜量化参数模板（写入 timeline 的 movement_speed_multiplier 参考）：
{movement_params}

## 情绪氛围关键词（全局"燥热慵懒"，每镜可微调）：
{atmosphere_keywords}

## 禁止材质/负面视觉元素（严禁出现在 prompt 中）：
{banned_visuals}

# 第三步：单镜头 prompt 结构模板
固定前缀 + 景别 + 机位角度 + 运镜方式 + 主体人物外貌与具体动作链（含微观物理细节与动漫特效）+ 物理特效描述（粒子/热浪/扬尘）+ 材质纹理描述（2个）+ 光影分层描述（主/辅/环境/轮廓）+ 环境场景与时间光源 + 色彩情绪 + 原文关键词 + 情绪词。

固定前缀（每个 prompt 必须以此开头，与 style_prefix.py 一致）：
{style_prefix}

物理特效强制项（本片特征，每镜必须视觉化呈现至少一项）：
- 空气扭曲热浪：高温使空气蒸腾扭曲，远景虚化变形
- 金色扬尘：干燥尘土微粒在阳光照射下呈金色漂浮
- 灵光漂浮颗粒：微光粒子在空气中缓慢四散飘动
- 蝉鸣视觉化：蝉鸣声以虚化波纹/画面微颤形式呈现

光影分层强制约束（每镜必须按以下分层描述）：
- 主光源（烈日顶光 / 落日侧逆光 / 烛火点光）
- 辅光源（天光漫射 / 水面反射 / 墙壁反射）
- 环境光基底（暗调低饱和 / 晨雾漫射 / 均匀漫射）
- 边缘/轮廓光（金色轮廓光勾勒人物）
- 眼神光（瞳孔中光源亮点）

材质质感强制约束（每镜必须含至少 2 个正面材质关键词）：
- 古风2D动画专属：大地干裂肌理、枯柳干枯木纹、少年布衣粗麻质感、颗粒质感、手绘平涂+渐变结合
- 禁止：塑料质感、3D金属、写实照片肌理、现代合成材质

景深分层控制（本片特色，每镜必须按以下方式之一描述）：
- 远景枯柳：中度虚化（高斯模糊半径约 15px），作为画面层次背景
- 中景少年：保持清晰聚焦，强调轮廓与面部光影
- 近景大地热浪：超浅景深，前景局部清晰，其余虚化

写作要求：
- 主体动作必须具体：不说"走路"，说"赤足踏在干裂焦土上，足弓着地瞬间尘土从趾缝溢出，步伐沉重留下深深脚印"；不说"看"，说"少年眼眸微眯，瞳孔高光凝缩，睫毛在烈日下投射细碎阴影"。
- 必须含至少一个微观物理细节或动漫特效：发丝飘动、衣袂翻飞、灵光粒子飘散、热浪扭曲、水面涟漪、尘土扬起等。
- 必须出现 narration_segment 中的原文关键名词/动作。
- 相邻镜头景别不得重复。
- 每个镜头须有明确情绪词（燥热慵懒、孤寂决绝、紧张压抑、壮阔悲凉等）。
- 单条 prompt 长度 80-150 字。

# 画面-字幕逐词绑定（最重要，输出前逐镜核对）
- **强制逐词覆盖**：subtitle 中的每个核心名词（人物名、地名、关键物件、自然元素），都必须在画面描述中有视觉对应。
  - 对应规则：同一个词最佳（subtitle有"大地"→画面也有"大地"）；同义视觉化变体可接受（"太阳"→"烈日""骄阳"）；抽象上位词替代不可接受（"太阳"→"光源""高光"不合格）。
  - subtitle 出现人物名 → 画面必须写同一人物名
- **程序级校验**：系统将提取 subtitle 中的核心实词并与画面描述做强制性匹配。覆盖率低于 50% 的镜头将被标记为不合格。
- **这是比合规更基础的铁律——如果画面和字幕说的是两回事，整个视频就废了。**

# 第四步：生成步骤（严格按序执行）

第 0 步（新增必做）— **通读全章，列出地点及其特征**
先完整阅读整章小说，记录以下信息：
- 所有出现的地点（地点名+特征描述）
- 地点之间的关系（空间位置、距离感）
- 例："船上"→"游船，画舫式，有女眷，不是独木舟，应该是中型画舫，装饰精美"
- 例："岸边"→"荷塘岸边，有灯笼，有石阶"
- 这些地点特征将用于后续每个镜头的场景设计，确保环境合理
- **特别注意**：涉及水上场景时，船只要符合文中描述（有人女眷→画舫/游船，非独木舟）

第 1 步 — 先通读每段 subtitle，列出其中的核心实词（地名/人物名/关键物件/自然元素），然后在写画面描述时逐个确保有对应的视觉元素。这篇先列词再写画面的步骤不能跳过。

第 2 步 — 将原文按情节均匀切为 N 个连续段落（N >= 6，至少6镜），不跳段不遗漏，覆盖从头到尾的全部转折。
  - 前段镜头 ← 开头段落（氛围渲染、场景建立、人物出场）
  - 中段镜头 ← 中段段落（事态发展、人物互动、情节推进）
  - 后段镜头 ← 结尾段落（高潮/冲突/转折；若原文结尾有战斗、刺杀、冲突、逃离、反转等强事件，必须呈现，不得用风景/氛围替代）
  - 三段镜头数量大致均匀即可，无需严格等分。
第 3 步 — 为每个段落生成基本字段：
  - narration_segment：从该段摘取最核心的原文原句（自行决定字数，以最佳摘句为准）
  - subtitle：与 narration_segment 逐字相同
  - prompt：按上面结构模板生成，必须包含物理特效/光影分层/材质描述
  - main_subject：镜头核心主体（人物名/场景元素/物体名），用于后期人物画风一致性校验
  - core_keywords：从 subtitle 提取的强制视觉关键词列表（至少 3 个，字符串数组）
  - fx_effect：独立动漫特效描述（如"空气扭曲热浪""金色扬尘漂浮""灵光颗粒飘散"），与基础画面描述分离
  - audio_cue：画面配套音效描述（如"蝉鸣聒噪""赤足踏在干土上的沉闷脚步声"）
  - mask_area：光影遮罩区域描述（如"烛火局部光影范围—面部右半侧光，左半暗""烈日强光区域地面高亮"）
  - color_tone：单镜头专属色调（如"暖金焦褐色""冷蓝+暖金对比"）
  - clip_transition：镜头转场方式（如"硬切 cut""模糊叠化 blur dissolve""淡入 fade in"）
  - cam_idx：分配摄像机编号（1-4，同一摄像机镜头连续不交错）
  - variation_type：判断变幅（small/medium/large），至少 ceil(N/3) 个非 small
  - ff_desc：首帧静态快照（80-120字，纯静态，无运动动词）
  - lf_desc：尾帧静态快照（80-120字，small时留空""）
  - motion_desc：帧间运动描述（50-100字，纯过程，含运镜+主体动作）
  - context_bridge：视觉衔接描述（30-60字，镜头1留空""）
  - **不需要 duration 字段——系统根据 TTS 实际生成长度自动计算每个镜头的时长**

第 4 步 — 拼接所有 narration_segment 为 narration 字段

# 第五步（新增）：节奏与时长控制规则
注意：LLM 不需要输出 duration 字段（系统自动按 TTS 计算），
但需根据内容性质在 motion_desc 中体现时长感（快/慢节奏暗示）。

## 节奏控制
- **高潮/情绪爆发镜头**：对峙、拔剑、噩耗冲击等关键情节，motion_desc 应使用慢速推镜/环绕/升格运镜，暗示该镜偏长（6-8s阅读节奏），给情绪留足释放空间。
- **激烈运动镜头**：策马狂奔、奔跑、打斗等，motion_desc 应用快速跟拍/甩镜/手持微晃，暗示快速剪辑节奏（4-5s阅读节奏）。
- **过渡/氛围镜头**：风景空镜、静态等待、过渡叙事，motion_desc 用固定或缓推，节奏适中（3-5s阅读节奏），避免前半段拖沓。
- **关键信息传递镜头**：人物对话、密信、线索揭示等，motion_desc 用缓慢推镜聚焦，给观众阅读和理解的时间（5-7s阅读节奏）。
- **不要平均分配**：连续 3 个以上短节奏镜头后需要一个长节奏镜头"换气"；连续 2 个长节奏后需一个短节奏加速。

# 第六步（新增）：画面连贯性优化规则

## 场景过渡（context_bridge 必写字段，增强镜头间空间逻辑）
- **空间关联**：A镜人物在画面左侧离开 → B镜应指示人物从画面右侧进入（或给出新位置关系），用 context_bridge 写明空间方位变化。
- **视角延续**：两镜间若摄像机机位变组（如从 cam_idx=1 切到 cam_idx=2），context_bridge 必须说明视角切换的逻辑。
- **时间连续**：时间跳跃（正午→黄昏）须在 context_bridge 中写明时间推进逻辑，并至少在过渡前 2 镜开始暗示（光影渐斜、影子拉长、色调渐暖）。
- **动作衔接**：前一镜结束动作为"人物A转身离开" → 后一镜动作应为"人物A已走远/来到新地点"或"人物B看向A离开方向"，用 motion_desc 保持动作链不中断。

## motion_desc 的连贯性
- motion_desc 必须承接上一镜的结尾状态并推向下一镜的初始状态，形成连续运动流。
- **禁止**：上一镜人物在策马，本镜 motion_desc 写"静止站立"作为首帧 → 应写"翻身下马"或"勒马减速"作为过渡。

## 色彩过渡（color_tone 字段的渐变控制）
- 全片色彩基调变化应自然渐变，不可突变。过渡长度至少跨 2-3 个镜头。
- **禁止**：前17镜"暖金焦褐"→ 第18镜"橙红深紫"，跨度太大 → 应在第15-16镜开始向橙调偏移。
- 冷色调切换（如进入密林/山洞）应通过 mask_area 中的"光线被遮挡"过渡，而非直接跳色调。

# 第七步（新增）：情绪强化规则

## 人物情绪递进链（跨镜头累积，非每镜独立）
- 情绪不能每镜归零重置。同一叙事段内的多个镜头，情绪应从 **铺垫 → 积累 → 爆发 → 余波** 走完整曲线。
- **铺垫**：前段镜头中通过微表情/小动作暗示内心（眉头微蹙、握拳又松开、眼神偏移）。
- **积累**：中段镜头动作幅度加大（脚步变快、呼吸急促、手微颤）。
- **爆发**：高潮镜头全开情绪张力（手臂青筋、牙关紧咬、眼眶发红）。
- **余波**：爆发的下一镜，人物不应立刻回归平静，应有"余震"（沉重呼吸、身体微晃、手还未完全松开）。

## 关键信息的画面冲击感
- 当 narration_segment 包含重大转折信息（噩耗、背叛、秘密被发现）时：
  - 该镜 audio_cue 应使用"环境音骤然下降/消失"或"对白回声"效果，让观众注意力完全集中到文字信息。
  - 运镜配合：关键信息镜头用"缓慢推镜至特写"(push-in)，信息说完后用"缓拉"(pull-out)留给观众反应空间。
  - 若有路人在场，路人表情/动作应有相应变化（转头、变色、手中器物滑落等）。

## 高潮镜头的情绪释放
- 高潮镜头（战斗/对峙/爆发）必须有**提前半秒的"蓄力瞬间"**：
  - ff_desc 中描写"蓄势/静止/屏息"的瞬间（如"剑拔到一半悬停的瞬间"）
  - motion_desc 中再写动作爆发（"剑光一闪、身影交错"）
  - 这个"静止→爆发"的顺序制造情绪张力，避免"上来就打"的平淡感。

# 第八步（新增）：细节补全规则

## 人物固定细节（贯穿全片，不可遗漏）
- **佩剑/武器**：如角色有佩剑，每镜至少在前半段镜头中描述一次剑鞘特征（颜色、纹路、破损等），强化身份标识。
- **服装一致性**：同一叙事段内服装不变。跨天/跨场景通过 ff_desc 做渐进变化（"衣襟已沾满尘土""袖口撕裂"），非无理由换装。
- **面部特征锚点**：主角的主要面部特征（眉形、眼睛形状、发型）至少每 3 镜在 prompt 中再现一次，防 AI 画风漂移。
- **配角特征锚点**：有台词的配角出场的第一镜必须描述其标志性特征。

## 环境细节（世界观暗示）
- **背景细节分层**：每镜 prompt 至少包含一层"中景描述"（主体所在处）和一层"远景/背景暗示"（远处的田野枯死、城墙上的告示、远处冒烟的山头），丰富世界观但不干扰主体。
- **伏笔类细节**：如果后续剧情反转涉及当前场景的元素（城墙上贴的告示、桌上的异样信件），当前镜应在 ff_desc/prompt 中无痕迹地埋入。
- **季节/气候一致性**：全片季节气候必须统一。若第一镜写"正午酷暑，干裂大地"，后镜就不能写"湿漉漉的雨天"。微变化可以，需渐变。
- **地面/足部细节**（本片主角赤足）：每 2-3 镜至少一次在 ff_desc 或 motion_desc 中加入地面接触细节（足底沾土、脚印深浅、沙砾嵌在趾缝等）。

## 信息递进补全
- **不一次性给完**：一个重大信息不应由一个镜头全部说明。分成 2-3 个镜头递进释放：暗示 → 部分信息 → 全貌。
- **补叙视角**：如果关键信息是主角不在场时发生的，通过"传信者的话语 + 听众的面部反应"来补叙，不直接切到事件现场。

# 输出结构说明
脚本顶层结构如下。你必须严格按此结构输出。

## 全局渲染参数（固定值，逐字一致）：
- resolution: "1080*1920"
- ratio: "9:16"
- fps: 24
- render_style: "2D国风动画"
- global_lighting: "电影柔光，暗调低饱和，烛火局部光影"
- global_camera_base: "浅景深，支持缓慢向前推镜，推镜速度约0.3倍常规"
- global_weather: "酷暑燥热，空气蒸腾扭曲"
- global_atmosphere: "燥热慵懒感，金色扬尘弥漫，大地蒸腾"
- negative_prompt: 固定字符串

## timeline 数组中每项新增参数：
- keyframe_sample_count: 关键帧采样数量（small=1, medium=2, large=3）
- movement_speed_multiplier: 运镜速度倍率（0.3=极慢速, 0.5=半速, 1.0=常速, 2.0=快速）
- blank_hold_duration: 留白空镜时长（固定约1.5s）

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
- [ ] 每镜 prompt 含物理特效（热浪/扬尘/灵光粒子至少一项）
- [ ] 每镜 prompt 含光影分层描述（主/辅/环境/边缘光）
- [ ] 每镜 prompt 含至少 2 个正面材质关键词
- [ ] 每镜含 main_subject / core_keywords / fx_effect / audio_cue / mask_area / color_tone / clip_transition 字段
- [ ] core_keywords 提取自 subtitle，且视觉上全部在 prompt 中有所体现
- [ ] context_bridge 写明镜头间的空间/视角/时间衔接逻辑
- [ ] 色彩（color_tone）过渡跨 2-3 镜渐变，无突变
- [ ] 情绪递进从铺垫到爆发走完整曲线，不每镜归零

# 输出格式
只输出纯JSON，不要markdown代码块标记，不要任何前后缀文字。结构如下：
{{
  "shot_count": N,
  "narration": "所有narration_segment按序拼接",
  "resolution": "1080*1920",
  "ratio": "9:16",
  "fps": 24,
  "render_style": "2D国风动画",
  "global_lighting": "电影柔光，暗调低饱和，烛火局部光影",
  "global_camera_base": "浅景深，支持缓慢向前推镜，推镜速度约0.3倍常规",
  "global_weather": "酷暑燥热，空气蒸腾扭曲",
  "global_atmosphere": "燥热慵懒感，金色扬尘弥漫，大地蒸腾",
  "negative_prompt": "真人写实、写实风格、畸形五官、明亮强光、现代服装、3D渲染、塑料质感、明亮阳光",
  "shots": [
    {{
      "index": 1,
      "narration_segment": "太阳像火炉般烘烤着青州府大地，田间土皮都在冒烟",
      "subtitle": "太阳像火炉般烘烤着青州府大地，田间土皮都在冒烟",
      "main_subject": "青州府大地",
      "core_keywords": ["太阳", "火炉", "烘烤", "青州府", "大地", "田间", "土皮", "冒烟"],
      "fx_effect": "空气扭曲热浪，蒸腾气流从地面升起，金色扬尘在阳光中漂浮",
      "audio_cue": "蝉鸣聒噪，热风呼啸掠过大地",
      "mask_area": "烈日强光区域—地面高亮曝光，阴影极短且浓黑",
      "color_tone": "暖金焦褐色",
      "clip_transition": "淡入 fade in",
      "cam_idx": 1,
      "variation_type": "small",
      "ff_desc": "远景，极低机位仰拍，干裂黄褐色大地蒸腾扭曲热浪，烈日高悬，枯柳静止，大地干裂肌理可见",
      "lf_desc": "",
      "motion_desc": "镜头缓拉上摇，热浪蒸腾扭曲地面空气，远处蝉鸣柳枝微动",
      "context_bridge": "",
      "prompt": "国风动漫影视剧，9:16竖屏，电影柔光，暗调低饱和，烛火局部光影，浅景深缓慢向前推镜，精美2D动画风格，无真人写实，高清1080P。远景，极低机位仰拍干裂黄褐色大地，空气热浪扭曲蒸腾，金色扬尘在阳光中漂浮，烈日高悬呈强烈顶光，大地干裂肌理清晰可见，远处枯柳中度虚化作为背景层次，暖金焦褐色调，燥热慵懒氛围"
    }},
    ...共N个镜头
  ]
}}

# 小说原文
{novel_text}
"""


# ========================================================================
# 系统提示词（v5.0 增强）
# ========================================================================

SYSTEM_PROMPT = (
    "你是资深国风动漫分镜导演兼动画摄影指导，擅长把小说原文转化为动漫电影级视觉描述。\n"
    "铁律（违反则不合格）：\n"
    "1. 音字同步：shot[i].subtitle 必须与 shot[i].narration_segment 逐字相同，narration 等于所有subtitle按序拼接；\n"
    "2. **画面-旁白逐词绑定（最高优先级）**：prompt 的每个视觉元素（人物、动作、场景、物体）必须严格来自对应 subtitle 文本。\n"
    "   - subtitle 说什么，画面就拍什么；subtitle 没说的，prompt 不能出现。\n"
    "   - 反例：subtitle 写\"田野\" 但 prompt 写\"宫殿\" → 严重违规，直接废弃。\n"
    "3. 全弧线覆盖：所有镜头均匀覆盖原文完整情节——前段镜头取开头、中段镜头取中段、后段镜头必须取结尾（含冲突/战斗/反转等强事件），禁止全部取自前半段；\n"
    "4. 动漫电影化描述：每个prompt须含景别、机位、运镜、主体动作、环境光影、物理特效（热浪/扬尘/灵光粒子至少一项）、光影分层描述（主/辅/环境/轮廓光）、材质描述（至少2个正面材质词）、色彩情绪、原文关键词、情绪词，并以固定前缀开头；风格为精美2D动画，无真人写实；\n"
    "5. 语义级合规：禁止性暗示/血腥暴力生理细节，打斗用远景侧写；\n"
    "6. 节奏控制：高潮镜头慢推/环绕给足情绪释放（6-8s节奏），运动镜头快速跟拍（4-5s），过渡镜头固定或缓推；相邻镜头节奏不可单调重复；\n"
    "7. 画面连贯：context_bridge 写明空间/视角/时间衔接逻辑；motion_desc 承接上镜结尾状态；color_tone 跨2-3镜渐变，不突变；\n"
    "8. 情绪递进：走\"铺垫→积累→爆发→余波\"完整曲线，高潮镜头有蓄力瞬间（静止→爆发）；\n"
    "9. 细节锚点：主角面部特征每3镜再现，配角出场镜给出标志性特征；每镜含中景+远景/背景两层环境暗示；赤足足部细节每2-3镜出现一次；\n"
    "10. 输出全局渲染参数顶层字段（resolution/ratio/fps/render_style/global_lighting/global_camera_base/global_weather/global_atmosphere）。\n"
    "11. 每镜含 main_subject/core_keywords/fx_effect/audio_cue/mask_area/color_tone/clip_transition 字段。\n"
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
   - prompt：使用与主分镜相同的视觉语言（国风2D动画、景别机位运镜等）
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
                    # 格式化为：[角色名: 静态特征 + 动态特征]
                    features = f"{static} {dynamic}".strip()
                    anchors.append(f"[{ident}: {features}]")
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
    # 构建角色锚点
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
