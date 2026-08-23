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
# 从 .md 文件加载 prompt 模板（v6.39 拆分，2026-08-22）
# 改 prompt 内容只改 .md，不再动 .py——避免 SyntaxError / git checkout 反复回滚
_PROMPTS_DIR = Path(__file__).parent / "prompts"

def _load_prompt(name: str) -> str:
    """从 prompts/<name>.md 读模板内容（去掉开头 HTML 注释行）."""
    path = _PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    # 去掉 <!-- ... --> 注释行
    lines = []
    for line in text.split(chr(10)):
        if line.lstrip().startswith("<!--"):
            continue
        lines.append(line)
    return chr(10).join(lines).strip(chr(10))

PROMPT_TEMPLATE = _load_prompt("main")
SYSTEM_PROMPT = _load_prompt("system")
HOOK_PROMPT_TEMPLATE = _load_prompt("hook")



# ========================================================================
# 主 prompt 模板（v5.0 — 扩展词库 + 四大优化维度）
# ========================================================================




# ========================================================================
# 系统提示词（v5.0 增强）
# ========================================================================




# ========================================================================
# 钩子分镜生成（v6 新增）
# ========================================================================



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


def _build_scene_object_anchors(objects_bank) -> str:
    """构建场景物体尺寸锚点字符串，用于注入 Prompt.

    每个物体输出一行「首次出现写全规格」的推荐写法（consistent_terms 首项），
    供 LLM 在涉及该物体的每个镜头 prompt/ff_desc/lf_desc 中写出锚定尺寸。
    无已知物体时返回哨兵字符串（与角色锚点一致的约定，调用方据非哨兵判断是否注入）。
    """
    if not objects_bank:
        return "无场景物体"
    try:
        idents = objects_bank.get_all_identifiers()
        if not idents:
            return "无场景物体"

        anchors = []
        for ident in idents:
            obj = objects_bank.get_object(ident)
            if not obj:
                continue
            parts = [f"- {ident}"]
            if obj.get("category"):
                parts.append(f"类别:{obj['category']}")
            if obj.get("subtype"):
                parts.append(f"形制:{obj['subtype']}")
            dims = []
            if obj.get("length_m") is not None:
                dims.append(f"长{obj['length_m']}米")
            if obj.get("width_m") is not None:
                dims.append(f"宽{obj['width_m']}米")
            if obj.get("height_m") is not None:
                dims.append(f"高{obj['height_m']}米")
            if dims:
                parts.append("尺寸:" + "、".join(dims))
            if obj.get("capacity"):
                parts.append(f"容量:{obj['capacity']}")
            if obj.get("structure"):
                parts.append(f"结构:{obj['structure']}")
            terms = obj.get("consistent_terms") or []
            if terms:
                parts.append(f"统一称呼:{terms[0]}")
            anchors.append("，".join(parts))
        return "\n".join(anchors) if anchors else "无场景物体"
    except Exception:
        return "无场景物体"


def _format_kv(items) -> str:
    """将列表或字典格式化为「  - key: value」列表字符串.

    - list/tuple/set → 每项一行「  - item」
    - dict → 每行「  - key: value」
    统一替代原先 _format_list / _format_dict / _format_movement_params 三个
    功能高度重叠的函数。
    """
    if isinstance(items, dict):
        return "\n".join(f"  - {k}: {v}" for k, v in items.items())
    return "\n".join(f"  - {item}" for item in items)


def _build_cinematic_vocab() -> str:
    """汇总 storyboard_refs 的镜头语言词库，作为独立 section 拼入 prompt（code-audit 07-31 #1）.

    旧实现把各词库作为 build_prompt 里 .format() 的 kwargs 传入，但 PROMPT_TEMPLATE
    从未声明这些占位符 → 被 .format() 静默忽略，词库实际是死代码、LLM 从未见过。
    改为这里显式拼接成一段，由 build_prompt 在 format 完成后再 append。
    """
    sections = [
        ("景别", _format_kv(storyboard_refs.SHOT_SIZES)),
        ("机位角度", _format_kv(storyboard_refs.CAMERA_ANGLES)),
        ("运镜方式", _format_kv(storyboard_refs.CAMERA_MOVEMENTS)),
        ("景深/对焦", _format_kv(storyboard_refs.DEPTH_OF_FIELD)),
        ("光影氛围", _format_kv(storyboard_refs.LIGHTING_MOODS)),
        ("色调", _format_kv(storyboard_refs.COLOR_PALETTES)),
        ("古代服饰", _format_kv(storyboard_refs.ANCIENT_COSTUMES)),
        ("古代场景", _format_kv(storyboard_refs.ANCIENT_LOCATIONS)),
        ("摄像机分组", _format_kv(storyboard_refs.CAMERA_GROUPING)),
        ("天气", _format_kv(storyboard_refs.WEATHER_TAGS)),
        ("粒子特效", _format_kv(storyboard_refs.PARTICLE_EFFECTS)),
        ("地面物理", _format_kv(storyboard_refs.GROUND_PHYSICS)),
        ("材质描述", _format_kv(storyboard_refs.TEXTURE_DESCRIPTORS)),
        ("光照层次", _format_kv(storyboard_refs.LIGHTING_LAYERS)),
        ("转场方式", _format_kv(storyboard_refs.TRANSITIONS)),
        ("音效提示", _format_kv(storyboard_refs.AUDIO_CUES)),
        ("运镜参数", _format_kv(storyboard_refs.MOVEMENT_PARAMS)),
        ("氛围关键词", _format_kv(storyboard_refs.ATMOSPHERE_KEYWORDS)),
        ("禁止元素", _format_kv(storyboard_refs.BANNED_VISUALS)),
    ]
    lines = ["### 镜头语言词库（从下列词汇中选择合适项，帮助画面描述更专业）"]
    for name, body in sections:
        if not body:
            continue
        lines.append(f"[{name}]")
        lines.append(body)
    return "\n".join(lines)


# ========================================================================
# 构建完整 prompt
# ========================================================================

def build_prompt(novel_text: str, bank=None, objects_bank=None,
                 outline_anchor: str = "") -> str:
    """构建最终发送给 LLM 的 prompt，注入完整镜头语言库 + 扩展词库 + 角色锚点 + 场景物体锚点 + 大纲锚点."""
    from shared.config import SHOT_COUNT_MIN, SHOT_COUNT_MAX
    char_anchors = _build_character_anchors(bank) if bank else "无已知角色"
    scene_object_anchors = _build_scene_object_anchors(objects_bank) if objects_bank else "无场景物体"
    prompt = PROMPT_TEMPLATE.format(
        novel_text=novel_text,
        character_anchors=char_anchors,
        scene_object_anchors=scene_object_anchors,
        audio_instruction=_build_audio_instruction(),
        # v6.37：硬约束镜头数上下限（从 env 读，避免硬编码 25/12）
        shot_count_min=SHOT_COUNT_MIN,
        shot_count_max=SHOT_COUNT_MAX,
    )
    # v6.23：大纲锚点（ShortVideoDirector director-outline 吸收）——分镜参考大纲，
    # 保证冲突/高潮分布。大纲为空时不注入。
    if outline_anchor:
        prompt = prompt + "\n\n### 本章大纲（分镜必须覆盖以下事件与高潮）\n" + outline_anchor
    # 追加镜头语言词库。旧实现把这些词库作为 .format() 的 kwargs 传入，但 PROMPT_TEMPLATE
    # 从未声明对应占位符，.format() 静默忽略 → 词库从未到达 LLM（code-audit 07-31 #1）。
    # 现改为 format 完成后再拼接：既避开大模板里 `{ }` 的转义风险，也确保 LLM 真实看到词库。
    vocab = _build_cinematic_vocab()
    if vocab:
        prompt = prompt + "\n" + vocab
    # 追加伴生技能上下文
    local_ctx = _load_local_skill_context()
    if local_ctx:
        prompt = prompt + local_ctx

    # 追加独立知识库 skill：转场技巧 + 材质写实化
    knowledge_ctx = _load_knowledge_skills()
    if knowledge_ctx:
        prompt = prompt + knowledge_ctx

    # 视频模型时长上限引导（仅当当前模型上限 < 模板里的 ≤10s 引导时收紧）
    cap_ctx = _duration_cap_guidance()
    if cap_ctx:
        prompt = prompt + cap_ctx

    return prompt


def _build_audio_instruction() -> str:
    """按当前视频供应商返回对白/声音生成指令段（模板 {audio_instruction} 占位）.

    旧供应商（bailian/agnes/dashscope）：保留「（用中文说）」指令格式（历史行为，
    TTS/模型靠它识别中文发音与情绪）。
    新供应商（volcengine/minimax）：视频模型原生发声，prompt 写自然对白即可，
    禁止添加「用中文说」指令前缀。
    """
    from shared.model_caps import active_video_uses_native_voice
    if active_video_uses_native_voice():
        return (
            "### 声音生成指令（视频模型自带语音）\n"
            "视频模型会直接为对白生成说话+口型：prompt 中按规则0写自然台词"
            "（说话人道：台词）即可，由模型自行发声。**禁止**添加「（用中文说）」这类指令前缀。\n"
        )
        return "（VIDEO_MODEL_VOICE=1，声音由视频模型原生生成，无需 prompt 额外指令）"


def _duration_cap_guidance() -> str:
    """当前视频模型单镜时长上限引导（仅当上限 < 模板里的 ≤10s 引导时收紧）.

    模板已要求「单镜头时长：特写/空镜 2-4秒，中景 3-6秒，远景/动作 5-8秒，
    严禁超过 10 秒」。对上限更低的模型（如 Hailuo v1 720P=6s）必须覆盖该引导，
    否则 LLM 会按 10s 规划、生成超上限镜头被 provider 截断。
    """
    from shared.model_caps import active_video_duration_cap
    cap = active_video_duration_cap()
    if not cap or cap >= 10:
        return ""
    return (
        f"\n\n### 视频模型时长上限 {cap} 秒（必须遵守，覆盖上述 10 秒引导）\n"
        f"当前视频模型单镜时长上限为 {cap} 秒。任何镜头（含钩子）时长不得超过 {cap} 秒，"
        f"超出会被视频服务截断。动作/对白规划必须落在 {cap} 秒内；"
        f"无法在 {cap} 秒内完成的动作拆成多个 2-3 秒短镜快切。\n"
    )


def _get_skills_loader():
    """加载 .aiVideo/skills_loader.py 模块并返回；失败返回 None.

    统一供 _load_local_skill_context 和 _load_knowledge_skills 使用，
    避免两处重复 importlib 初始化代码。
    """
    loader_path = _PROJECT_ROOT / ".aiVideo" / "skills_loader.py"
    if not loader_path.exists():
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("skills_loader", str(loader_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _load_local_skill_context() -> str:
    """加载伴生技能作为 prompt 附加上下文（若存在）."""
    mod = _get_skills_loader()
    if not mod:
        return ""
    try:
        body = mod.read_local_skill("generate-script")
        if body:
            return f"\n\n# 项目专属偏好（来自 .aiVideo/skills/generate-script-local/SKILL.md）\n{body}"
    except Exception:
        pass
    return ""


def _load_knowledge_skills() -> str:
    """加载独立知识库 skill 作为 prompt 附加上下文（v6.4：转场技巧 + 材质写实化）."""
    mod = _get_skills_loader()
    if not mod:
        return ""
    try:
        parts = []

        # Seedance 镜头工艺全集（v2.0 合并：13 个 seedance-* 技能）
        craft_body = mod.read_skill("seedance-craft")
        if craft_body:
            parts.append(f"\n\n# Seedance 镜头工艺全集（来自 seedance-craft）\n{craft_body}")

        # 镜头工艺全集（v1.0 合并：advanced-camera-movement + situation-camera + shot-assembling + focal-switch + occlusion-layering）
        camera_body = mod.read_skill("camera-mastery")
        if camera_body:
            parts.append(f"\n\n# 镜头工艺全集（来自 camera-mastery）\n{camera_body}")

        # 过渡工艺全集（v1.0 合并：video-transition + flashback-lens + speed-rhythm）
        transition_body = mod.read_skill("transition-mastery")
        if transition_body:
            parts.append(f"\n\n# 过渡工艺全集（来自 transition-mastery）\n{transition_body}")

        # 打斗工艺全集（v1.0 合并：combat-action + combat-arc + battle-layered + wuxia_combat）
        combat_body = mod.read_skill("combat-mastery")
        if combat_body:
            parts.append(f"\n\n# 打斗工艺全集（来自 combat-mastery）\n{combat_body}")

        # 人像工艺全集（v1.0 合并：portrait-lighting + face-shapes + face-deplastic）
        portrait_body = mod.read_skill("portrait-mastery")
        if portrait_body:
            parts.append(f"\n\n# 人像工艺全集（来自 portrait-mastery）\n{portrait_body}")

        # 角色表演工艺全集（v1.0 合并：restrained-emotion + voice-punctuation）
        performance_body = mod.read_skill("character-performance")
        if performance_body:
            parts.append(f"\n\n# 角色表演工艺全集（来自 character-performance）\n{performance_body}")

        # 材质写实化
        material_body = mod.read_skill("material-realism")
        if material_body:
            parts.append(f"\n\n# AI材质写实化提示词（来自 material-realism）\n{material_body}")

        # 故事圣经（世界观/人物/主线设定）
        bible_body = mod.read_skill("story-bible")
        if bible_body:
            parts.append(f"\n\n# 故事圣经（来自 story-bible）\n{bible_body}")

        # 人物出场四大方式（远景渺小/背影神秘/细节上摇/特写内心——高光登场选型）
        entrance_body = mod.read_skill("character-entrance")
        if entrance_body:
            parts.append(f"\n\n# 人物出场四大方式（来自 character-entrance）\n{entrance_body}")

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
        # C1 修复（08-11）：旧实现读 `arc.arc_coverage.has_climax_coverage`，但
        # gap_analysis 返回的 distribution dict 用的是顶层 `has_climax_coverage`
        # ——键路径不匹配，分支永远不进、分布问题反馈从不到达 LLM。改读真实键。
        has_climax = bool(dist.get("has_climax_coverage", False))
        if not has_climax:
            issues.append(
                "【镜头分布问题】后半段镜头未覆盖高潮剧情（战斗/刺杀/反转等强事件）"
            )
            issues.append("  → 请在最后几个镜头中覆盖原文结尾的冲突/反转/离去等情节")

    # 生成反馈文本
    if issues:
        gap_feedback = "\n".join(issues)
        return f"\n\n# 差距分析反馈（来自本地校验）\n{gap_feedback}\n"
    return ""


def build_hook_prompt(novel_text: str, bank=None, objects_bank=None,
                      forbidden_lines: list[str] | None = None) -> str:
    """构建钩子分镜生成的 prompt，可选注入角色锚点 + 场景物体锚点.

    v6.32（08-12）新增 forbidden_lines：主分镜各镜头台词体列表。钩子 LLM
    生成时看不到主分镜文本（C4 修复的盲区），把「不得重复的台词」直接喂给它，
    从源头避免「钩子台词与主分镜镜头X重复」穿帮。
    """
    # 注入角色锚点（使 LLM 知道原文人物称呼对应哪个角色名）
    char_anchor_str = ""
    if bank:
        char_anchors = _build_character_anchors(bank)
        if char_anchors and char_anchors != "无已知角色":
            char_anchor_str = (
                "\n\n### 角色参考\n"
                "以下是已有角色及其特征，prompt 中必须使用角色名（而非'公子''小姐'等模糊称呼）：\n"
                f"{char_anchors}"
            )
    # 注入场景物体尺寸锚点（使 LLM 知道场景物体的锚定尺寸/容量/统一称呼）
    obj_anchor_str = ""
    if objects_bank:
        obj_anchors = _build_scene_object_anchors(objects_bank)
        if obj_anchors and obj_anchors != "无场景物体":
            obj_anchor_str = (
                "\n\n### 场景物体尺寸参考\n"
                "以下是已锚定的场景物体尺寸，钩子画面涉及这些物体时必须写出锚定尺寸、使用统一称呼：\n"
                f"{obj_anchors}"
            )
    # 使用 replace 替换，避免 JSON 大括号与 format 冲突
    prompt = HOOK_PROMPT_TEMPLATE.replace("{novel_text}", novel_text)
    prompt = prompt.replace("{character_anchors}", char_anchor_str)
    prompt = prompt.replace("{scene_object_anchors}", obj_anchor_str)
    # v6.32：注入主分镜「禁止台词」列表，防钩子台词与主分镜重复（倒叙穿帮）
    forbidden_str = ""
    if forbidden_lines:
        _items = [ln.strip()[:40] for ln in forbidden_lines if ln and ln.strip()]
        if _items:
            forbidden_str = (
                "\n\n### 禁止台词（主分镜已使用，钩子不得重复）\n"
                "钩子的 narration_segment 不得与下列任何一条相同或近似"
                "（仅标点/语气词/重复字差异也算重复），必须换一句不同台词：\n"
                + "\n".join(f"  - {ln}" for ln in _items)
            )
    prompt = prompt.replace("{forbidden_lines}", forbidden_str)
    # 注入知识技能上下文（光位/运镜/转场/材质等），与主分镜保持同一视觉语言。
    # 钩子通常是人物/面部开场特写，正是光位检查的核心场景（8种AI人像专业光位）。
    knowledge_ctx = _load_knowledge_skills()
    if knowledge_ctx:
        prompt = prompt + knowledge_ctx
    # 视频模型时长上限引导（钩子同样受模型上限约束）
    cap_ctx = _duration_cap_guidance()
    if cap_ctx:
        prompt = prompt + cap_ctx
    return prompt


def parse_hook_response(raw: str) -> dict:
    """解析钩子分镜生成的原始响应（复用 json_repair 的容错解析）.

    裸 json.loads 在 LLM 返回不规范 JSON 时（如字符串未闭合/尾逗号/截断）
    会直接抛异常导致钩子丢失。改用 safe_parse_json_object 与主分镜同一套
    六层修复（去围栏/提取子串/常见错误/尾逗号/截断恢复），保证钩子不因
    轻微格式问题而静默丢失。
    """
    from shared.utils.json_repair import safe_parse_json_object
    return safe_parse_json_object(raw.strip())


# 兼容别名
