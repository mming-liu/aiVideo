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
你是一位顶级的国风动漫分镜导演和编剧。你的任务是将给定的小说文本转化为适合 Agnes AI 视频生成的专业分镜脚本。

### 核心任务
**【强制要求】镜头数量范围：{min_shots} - {max_shots} 个镜头。必须生成该范围内的镜头数量！**
1. **蒙太奇拆分**：严禁流水账！必须将文本拆解为多个短镜头（每个镜头 2-5 秒）。
   - 环境铺垫：拆分为特写或空镜（如：烈日特写、知了特写、荷叶特写）。
   - 动作连贯：高潮动作（如刺杀）必须拆分为“跃出水面”、“寒光一闪”、“刺入心口”等独立镜头。
2. **视觉转译**：将文学描写转化为 Agnes AI 能理解的视觉语言。
   - 错误示范：“清音梵唱般的箫声”
   - 正确示范：“特写镜头，年轻公子闭目吹箫，手指修长，周围飘落几片荷花瓣，光影柔和”
3. **角色一致性**：必须利用提供的【角色库】，在每个镜头的 prompt 中强制注入角色特征。
4. **音画同步**：精确标注声音指令，确保 Agnes AI 生成带中文配音的视频。

### 角色库 (Character Bank)
{character_anchors}

### 角色出现规则（重要！）
每个镜头应该根据以下规则确定画面中应出现的角色：
1. **main_subject 优先**：如果 main_subject 是人物名（如"杨旭吹箫"、"听香尖叫"），该角色必须出现
2. **旁白提及**：旁白中提到的人物很可能出现
3. **场景语义**：根据场景判断（如"湖上"必有杨旭和听香，"田埂"必有老三/娃娃）

**示例**：
- 镜头：main_subject="杨旭吹箫" → 必须出现杨旭，prompt开头标注 [杨旭: 剑眉星目，黑发束冠...]
- 镜头：main_subject="照月湾" → 可能是空镜，不强制出现人物
- 镜头：main_subject="刺客出水" → 必须出现刺客（可标记为 is_visible=false 如果不想露脸）

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
      "narration_segment": "该镜头对应的旁白或对话文本（如果是纯空镜则为空字符串）",
      "subtitle": "画面上显示的字幕（通常为对话内容）",
      "main_subject": "画面主体（如：杨旭、刺客、荷塘空镜）",
      "core_keywords": ["关键词1", "关键词2"],
      "prompt": "【核心提示词】**必须以角色锚点开头**（如 [杨旭: 剑眉星目...]），包含视觉转译后的画面描述、镜头景别（特写/中景/远景）、光影色调。",
      "motion_desc": "画面中的动态描述（如：人物动作、镜头推拉摇移）",
      "ff_desc": "首帧画面描述（用于生成第一帧图片，需具体、稳定）",
      "lf_desc": "尾帧画面描述（用于生成最后一帧图片，需考虑与下一镜头的衔接）",
      "context_bridge": "视觉衔接描述（如何从上一镜头过渡到本镜头）",
      "audio_cue": "音效描述（如：水花声、刀剑声）",
      "fx_effect": "特效描述（如：寒光、水波纹）",
      "color_tone": "色调氛围（如：冷色调、暖黄光）",
      "clip_transition": "转场方式（如：淡入淡出、硬切）"
    }}
  ],
  "hook": {{
    "index": 0,
    "narration_segment": "开场钩子文案（用于吸引观众，通常是高潮片段）",
    "subtitle": "钩子字幕",
    "main_subject": "钩子主体",
    "core_keywords": ["关键词1", "关键词2"],
    "prompt": "钩子画面的提示词（需极具视觉冲击力）",
    "motion_desc": "钩子动作描述",
    "ff_desc": "钩子首帧",
    "lf_desc": "钩子尾帧",
    "context_bridge": "",
    "audio_cue": "钩子音效",
    "fx_effect": "钩子特效",
    "color_tone": "钩子色调",
    "clip_transition": "淡出"
  }}
}}

### Agnes AI 声音生成指令（必须严格遵守）
如果 narration_segment 中包含人物对话，必须在 prompt 末尾显式添加声音指令。
格式规则：
1. 纯旁白：（音效：风声，蝉鸣）
2. 包含对话：保留旁白文本，但在 prompt 结尾追加指令。
   例如 narration_segment 为 "杨旭大喊：小心！快跑！"
   prompt 结尾必须包含：（用中文说，愤怒急切地）"小心！快跑！"
   注意：只提取引号内的内容作为 TTS 文本，不要包含“杨旭大喊”这几个字。

### 输入小说文本
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
