"""step01_script_gen.prompts - Prompt 模板定义（v5.0 增强版）

包含：
- PROMPT_TEMPLATE: 分镜生成主模板（含扩展词库 + 四大维度优化规则）
- SYSTEM_PROMPT: 系统提示词
- build_prompt(): 构建完整 prompt（注入所有扩展词库）
- _format_list(), _format_dict(): 格式化辅助
- _load_local_skill_context(): 加载伴生技能
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import storyboard_refs
# 从 .md 文件加载 prompt 模板（v6.39 拆分，2026-08-22）
# 改 prompt 内容只改 .md，不再动 .py——避免 SyntaxError / git checkout 反复回滚
_PROMPTS_DIR = Path(__file__).parent / "prompts"

# v6.99（2026-09-23 二次审核 P1-B）：**模板里的时长硬数字必须是占位符**。
#
# 原状态：`.md` 里写死「单镜 ≤ 15s」「对话镜 6-15s / 动作 3.5-8s」「安全值 ≤56 字」，
# 而运行时再追加一段「生效上限是 12s，以本值为准」。cap=6 或 12 时，模型**同时**
# 看到静态 15s 与运行时 6/12s——追加一句「以本值为准」能降低冲突影响，但不等于
# 单一事实源（长 prompt 里规则的权重与位置会直接影响遵循率）。
#
# 现在模板只写占位符，`_load_prompt()` 在**导入期**统一渲染成生效值：
#   {{SHOT_CAP}}        生效单镜上限（整数，如 12）
#   {{SHOT_CAP_DEC}}    同上（一位小数，用于 t_sec 区间表达）
#   {{NARR_MAX_CHARS}}  台词安全字数 = cap × 4 字/秒 − 4（cap=15 → 56，与原值一致）
#   {{DIALOGUE_TARGET}} 对话镜建议区间（已夹到 cap）
#   {{DUR_RANGES}}      按镜头类型的推荐区间（全部夹到 cap）
#
# 渲染后若还有残留占位符 → **直接抛错**：让模型看到 `{{...}}` 比少一条规则更糟。
_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def _cap_range(lo: float, hi: float, cap: float) -> str:
    """类型推荐区间——上限夹到生效 cap，下限不低于 3.5s（与 `_dur_range` 同口径）."""
    lo = max(float(lo), 3.5)
    hi = min(float(hi), float(cap))
    return "≤%.0fs" % float(cap) if hi <= lo else "%g-%gs" % (lo, hi)


def render_prompt_template(text: str) -> str:
    """把模板中的时长占位符渲染成**运行时生效值**（v6.99，审核 P1-B）."""
    from step01_script_gen.timecode import active_shot_cap

    cap = active_shot_cap()
    table = {
        "SHOT_CAP": "%.0f" % cap,
        "SHOT_CAP_DEC": "%.1f" % cap,
        "NARR_MAX_CHARS": str(int(cap * 4) - 4),
        "DIALOGUE_TARGET": _cap_range(6, 12, cap),
        "DUR_RANGES": "对话镜 %s / 动作·打斗·空镜 %s / 过渡 %s" % (
            _cap_range(6, 15, cap), _cap_range(3.5, 8, cap), _cap_range(4, 8, cap)),
    }
    out = text
    for key, value in table.items():
        out = out.replace("{{%s}}" % key, value)
    leftover = sorted(set(_PLACEHOLDER_RE.findall(out)))
    if leftover:
        raise ValueError(
            "prompt 模板残留未渲染占位符 %s（拼写错误或新增了未注册的占位符）"
            % leftover)
    return out


def _load_prompt(name: str) -> str:
    """从 prompts/<name>.md 读模板内容（去掉开头 HTML 注释行 + 渲染时长占位符）."""
    path = _PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    # 去掉 <!-- ... --> 注释行
    lines = []
    for line in text.split(chr(10)):
        if line.lstrip().startswith("<!--"):
            continue
        lines.append(line)
    return render_prompt_template(chr(10).join(lines).strip(chr(10)))

PROMPT_TEMPLATE = _load_prompt("main")
SYSTEM_PROMPT = _load_prompt("system")
HOOK_PROMPT_TEMPLATE = _load_prompt("hook")

# doubao-seed-2-1-turbo 独立 prompt 集（2026-09-03 接入）
# 仅 system prompt 不同（XML schema + 2 few-shot + 显式步骤链），
# main.md / hook.md 复用 deepseek 版（业务规则一致；输出 schema 一致）。
# 加载方式：TEXT_PROVIDER=agent_plan 时 auto_select_prompt_builder() 返回 doubao 系列。
DOUBAO_SYSTEM_PROMPT = _load_prompt("doubao_system")

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
    # v6.43 精简：删 10 个与 knowledge_skills 重复的分类，保留 9 个独有
    sections = [
        ("景别", _format_kv(storyboard_refs.SHOT_SIZES)),
        ("景深/对焦", _format_kv(storyboard_refs.DEPTH_OF_FIELD)),
        ("色调", _format_kv(storyboard_refs.COLOR_PALETTES)),
        ("古代服饰", _format_kv(storyboard_refs.ANCIENT_COSTUMES)),
        ("古代场景", _format_kv(storyboard_refs.ANCIENT_LOCATIONS)),
        ("天气", _format_kv(storyboard_refs.WEATHER_TAGS)),
        ("粒子特效", _format_kv(storyboard_refs.PARTICLE_EFFECTS)),
        ("地面物理", _format_kv(storyboard_refs.GROUND_PHYSICS)),
        ("音效提示", _format_kv(storyboard_refs.AUDIO_CUES)),
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
                 outline_anchor: str = "", prop_anchor_block: str = "") -> str:
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
    knowledge_ctx = _load_knowledge_skills(novel_text=novel_text)
    if knowledge_ctx:
        prompt = prompt + knowledge_ctx

    # 视频模型时长上限引导（仅当当前模型上限 < 模板里的 ≤10s 引导时收紧）
    cap_ctx = _duration_cap_guidance()
    if cap_ctx:
        prompt = prompt + cap_ctx

    # 08-23 v6.41 P19 闭环：硬约束总时长（红果标准 60-90s）
    # 8-22~8-23 跑批实测 LLM 默认每镜 3-5s → 总 30-40s，差红果 60s 下限 20+s。
    # 根因：prompts 仅有"特写 2-4s/中景 3-6s/远景 5-8s"逐镜引导，无总时长约束。
    # 修复：在 prompt 末尾追加"总时长 60-90s + 镜头数 → 平均每镜时长"硬约束，
    # 让 LLM 规划时按平均时长分配单镜 duration_sec，避免 hard_fix 层后置拉伸。
    total_dur_ctx = _build_total_duration_constraint(shot_count_min=SHOT_COUNT_MIN,
                                                     shot_count_max=SHOT_COUNT_MAX)
    if total_dur_ctx:
        prompt = prompt + total_dur_ctx

    # v6.125.2（用户 2026-09-25 决定）：道具状态候选走**独立短提示**追加在**末尾**
    # ——不并进 `outline_anchor`（doubao/agent_plan 剖面会把大纲锚点压到约 200 字，
    # 除【高潮事件】外的行会被裁掉，候选锚点必被吞）。两条剖面统一走这条路径，
    # 便于对照。本块**只影响提示词**：不接 BEST-GUARD、不接质量门、不自动删 props。
    if prop_anchor_block:
        prompt = prompt + "\n\n" + prop_anchor_block

    return prompt


def _dur_range(lo: float, hi: float, cap: float) -> str:
    """按镜头类型的**推荐**时长区间——上限一律夹到生效 cap，下限不低于 3.5s.

    v6.98（审核 P1-1 第 3 条）：推荐区间可以存在，但**不得与生效硬上限矛盾**。
    原实现写死「动作镜 3.5-8s / 对话镜 6-15s」，cap=6 时提示里就同时出现
    「上限 6s」与「3.5-8s」两条互斥指令——模型只能二选一。这是「提示与门禁
    不同源」的另一种形态。
    """
    lo = max(float(lo), 3.5)
    hi = min(float(hi), float(cap))
    if hi <= lo:
        return f"≤{float(cap):.0f}s"
    return f"{lo:g}-{hi:g}s"


def _build_total_duration_constraint(shot_count_min: int, shot_count_max: int) -> str:
    """v6.41 P19 闭环：红果 60-90s 总时长硬约束（08-23 增，08-23 改 8-10 镜）.

    8-22 跑批实测 LLM 生成的 duration_sec 总和 30-40s，距红果 60-90s 下限差 20+s。
    旧版仅在 hard_fix 层后置拉伸（W6 _hard_fix_runtime_extend），但受 motion_desc
    物理动作时长约束，单镜最多加 2s × 8 镜 = +16s，仍达不到 60s。

    根因：prompts 模板只规定单镜 duration 范围（特写 2-4s / 中景 3-6s / 远景 5-8s），
    没规定 SUM(duration_sec) 的总时长。LLM 按每镜 3-5s 规划自然落入 30-40s。

    修法：prompt 末尾追加"总时长 60-90s"硬约束 + 镜头数→平均时长推导，让 LLM
    在第一次生成就把每镜 duration_sec 拉到合理区间（结合 ≤8s 单镜 cap）。

    08-23 v6.41 P19.2：原 3-8 镜 + 平均 7.5s → LLM 默认按 6s/镜规划 → 8 镜 48s
    不到 60s 红果下限。改为 **8-10 镜 + 平均 6-9s/镜** → 48-90s 覆盖红果区间。

    08-23 v6.43 用户规则：**正片分镜总时长（不含钩子）≥ 60s，单镜 ≤ 8s，
    钩子时长另算；镜头数不限（SHOT_COUNT_MIN/MAX<=0 即视为不限）**。
    单镜 cap 从 12s 收紧到 8s；60s 下限只统计正片分镜，钩子不进总时长。
    镜头数不限时，提示「按需拆镜」+ 用「每镜平均时长」作为软目标，不再硬塞 N 镜。

    09-13 A/B 实测（doubao 94.0 / deepseek 95.1 后唯一短板）：旧引导「每镜平均
    6-7.5s、四类镜头全 6-8s」在镜头数不限（实际 15+ 镜）下必然顶格——doubao
    一刀切默认 6s/镜（12/15 镜 6s + 2 镜 8s）→ 92s，deepseek 79-83s。
    红果 60-90s 是合法区间不变，规划目标先收紧为 60-70s（DeepSeek raw 66s 达标，
    doubao 87s 仍超）；**09-13 用户决策再放宽为 70-80s（按 ~75s 分配）**——避免
    时长压太短导致场景被过度压缩。每镜平均 4-5s，按镜头类型拉开时长差
    （对话 5-6s / 动作 3-5s / 空镜 3-4s），禁止全章统一 6s。
    W6 runtime_extend 仍只守 ≥60s 下限，超顶侧全靠本引导。
    """
    from shared.model_caps import active_video_duration_cap
    from shared.constants import DEFAULT_VIDEO_DURATION_FALLBACK
    cap = active_video_duration_cap() or DEFAULT_VIDEO_DURATION_FALLBACK
    # 规划目标 70-80s → 平均每镜 4-5s（单镜硬上限 = 运行时生效值，见下）
    min_per_shot = 4.0
    target_per_shot = 5.0
    # v6.98（2026-09-22 审核 P1-1）：单镜硬上限**只有一个源**。
    #
    # 原实现是 `min(8.0, cap)`——项目上限早已放宽到 15s（v6.93），这里却仍把 8s
    # 当模板硬上限，于是**生成层主动把 8-12s 的合法镜头拆开**（下面还写着
    # 「超 8s 必拆镜」），而质量门按 12s 收——多出来的镜头受最短时长（3.5s）约束
    # 后又抬高总时长，是「正片顶格」的一个**代码侧**来源（审核 P1-1 的实测推论）。
    # 现在与门禁同源。
    from step01_script_gen.timecode import active_shot_cap
    max_per_shot = active_shot_cap()

    # v6.43 镜头数不限：min/max 任一 ≤0 时按"按需拆镜"措辞，不再硬塞 N 镜数字
    unlimited = shot_count_min <= 0 or shot_count_max <= 0
    if unlimited:
        plan_line = (
            f"**镜头数不限**，按动作密度自然拆分：总时长目标 70-80s ÷ 每镜平均 "
            f"4.5-{target_per_shot:.1f}s ≈ 14-17 镜"
            f"（单镜 ≤ {max_per_shot:.0f}s 硬上限，超 {max_per_shot:.0f}s 必拆镜）。\n"
        )
    else:
        plan_line = (
            f"按 {shot_count_min}-{shot_count_max} 镜规划：每镜平均 {min_per_shot:.1f}-{target_per_shot:.1f}s"
            f"（以总时长 70-80s ÷ 实际镜数反推每镜时长为准，勿逐镜顶格）。\n"
        )

    return (
        f"\n\n### ⏰ 总时长硬约束（合法区间 60-90s，规划目标 70-80s，最高优先级）\n"
        f"本章是抖音 60-90s 竖屏短剧，**正片分镜（不含钩子，钩子时长另算）duration_sec 之和必须落在 60-90s 合法区间内，规划目标 70-80s（按 ~75s 分配每镜时长）**。\n"
        f"{plan_line}"
        f"\n"
        f"**规划方式**（按镜头类型拉开时长差，禁止一刀切）：\n"
        f"- 开场/建立镜：4-5s（含人物姿态、空间关系建立）\n"
        f"- 对话/反应镜：5-6s（必须能念完台词+展示反应）\n"
        f"- 打斗/动作镜：{_dur_range(3.5, 5, max_per_shot)}（动作链拆多镜短镜快切，不靠拉长单镜；**不得低于 3.5s**）\n"
        f"- 空镜/过渡镜：{_dur_range(3.5, 4.5, max_per_shot)}（无对白无关键动作，从简；**不得低于 3.5s**）\n"
        f"- 末镜/留白镜：4-5s（情绪渲染时间）\n"
        f"\n"
        f"**硬约束**：\n"
        f"- 正片分镜 SUM(duration_sec) ≥ 60s（差 1s 都会被打回；钩子不计入，钩子时长另算）\n"
        f"- 正片分镜 SUM(duration_sec) ≤ 90s（超时会被强制截断）；规划目标 70-80s，超 80s 说明每镜时长分多了\n"
        f"- 末镜 duration_sec ≥ 4s（红果末镜情绪渲染下限）\n"
        f"- 单镜 ≤ {max_per_shot:.0f}s（硬上限，超过必被打回夹取）\n"
        f"\n"
        f"**禁止**：\n"
        f"- 单镜 2-3s 短镜堆叠（15 镜 × 3s = 45s，差 15s 必返工）\n"
        f"- 全章一刀切默认 6s（15 镜 × 6s = 90s 顶格贴线、毫无快慢节奏，按规划失败处理）\n"
        f"- 单镜 > {max_per_shot:.0f}s 长镜头（动作链必须拆多镜，每镜 ≤ {max_per_shot:.0f}s）\n"
        f"\n"
        f"**自检**：写完每个 shot 后，累计正片分镜 SUM(duration_sec)（不含钩子），"
        f"未到 70s 可把对话镜/末镜 +1s（仍 ≤ {max_per_shot:.0f}s）让场景充分展开；"
        f"超过 80s 就把无对白的过渡镜/空镜 -1s 或合并同场景过渡镜。\n"
    )


def _build_audio_instruction() -> str:
    """按当前视频供应商返回对白/声音生成指令段（模板 {audio_instruction} 占位）.

    无声模型（2026-09-12，如 volcengine Seedance 1.0 系列）：prompt 明令禁止
    BGM/SFX/VO 等声音指令——模型不生成声音，写了也是无效 token 且可能干扰画面；
    台词照常写（说话人道：台词），仅作字幕与 step03 配音文本锚。
    原生发声供应商（volcengine/minimax/agent_plan 有声模型）：视频模型原生发声，
    prompt 写自然对白即可，禁止添加「用中文说」指令前缀。
    旧供应商（bailian/agnes/dashscope）：保留「（用中文说）」指令格式（历史行为）。
    """
    from shared.model_caps import (
        active_video_supports_audio, active_video_uses_native_voice,
    )
    if not active_video_supports_audio():
        return (
            "### 声音生成指令（无声视频模型，最高优先级覆盖上述声音规则）\n"
            "当前视频模型不生成任何声音：prompt 与 motion 中**禁止**出现 BGM/SFX/VO/"
            "背景音效/「说出」「喊出」等声音类指令（写了模型也无法执行，后期配音负责声音）。\n"
            "台词按规则0照常写（说话人道：台词），仅作为字幕与配音文本锚。\n"
        )
    if active_video_uses_native_voice():
        return (
            "### 声音生成指令（视频模型自带语音）\n"
            "视频模型会直接为对白生成说话+口型：prompt 中按规则0写自然台词"
            "（说话人道：台词）即可，由模型自行发声。**禁止**添加「（用中文说）」这类指令前缀。\n"
        )
    return ""


def _duration_cap_guidance() -> str:
    """当前**生效**单镜时长上限引导（v6.98：无条件按唯一源渲染，不再以 8s 为模板值）.

    模板已要求「单镜头时长：特写/空镜 2-4秒，中景 3-6秒，远景/动作 5-8秒，
    严禁超过 8 秒（08-23 用户规则硬上限）」。对上限更低的模型（如 Hailuo v1
    720P=6s）必须覆盖该引导，否则 LLM 会按 8s 规划、生成超上限镜头被 provider 截断。
    """
    # v6.98（审核 P1-1）：**无条件**按唯一源渲染，且拆镜下限改为 3.5s。
    #
    # 原实现只在 `cap < 8` 时才输出、并称其为「覆盖上述 8 秒引导」——cap=12 时这条
    # 引导**整条消失**，模板里「超 8s 会被截断」的措辞却还在。另外「拆成多个
    # **2-3 秒**短镜快切」低于最短时长 3.5s，是 `shot_duration_below_min` 的
    # 一条**指令层**来源。
    from step01_script_gen.timecode import active_shot_cap
    cap = active_shot_cap()
    if not cap:
        return ""
    return (
        f"\n\n### 生效单镜时长上限 {cap:.0f} 秒（必须遵守，优先于正文里任何其它单镜时长数字——更大或更小都以本值为准）\n"
        f"当前生效的单镜时长上限为 {cap:.0f} 秒（= min(视频模型能力, 项目上限)）。任何镜头（含钩子）时长不得超过 {cap:.0f} 秒，"
        f"超出会被视频服务截断。动作/对白规划必须落在 {cap} 秒内；"
        f"无法在 {cap} 秒内完成的动作拆成多个 **3.5-6 秒**短镜快切（**不得低于 3.5 秒**，低于下限同样拒收）。\n"
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


def _load_knowledge_skills(novel_text: str = "") -> str:
    """加载独立知识库 skill 作为 prompt 附加上下文（v6.43 精简版）.

    v6.43 08-25 优化：
    - 删 4 个与 cinematic_vocab 重复的 skill（camera/transition/portrait/material）
    - 3 个通用 skill 默认全注入（seedance-craft / character-performance / story-bible）
    - 2 个按需 skill（combat-mastery / character-entrance）按 novel_text 关键词检测注入

    Args:
        novel_text: 章节原文，用于关键词检测（combat / character-entrance 按需加载判断）
    """
    mod = _get_skills_loader()
    if not mod:
        return ""
    try:
        parts = []

        # === 通用 skill（默认注入） ===

        # Seedance 镜头工艺全集（v2.0 合并：13 个 seedance-* 技能）
        craft_body = mod.read_skill("seedance-craft")
        if craft_body:
            parts.append(f"\n\n# Seedance 镜头工艺全集（来自 seedance-craft）\n{craft_body}")

        # 角色表演工艺全集（v1.0 合并：restrained-emotion + voice-punctuation）
        performance_body = mod.read_skill("character-performance")
        if performance_body:
            parts.append(f"\n\n# 角色表演工艺全集（来自 character-performance）\n{performance_body}")

        # 故事圣经（世界观/人物/主线设定）
        bible_body = mod.read_skill("story-bible")
        if bible_body:
            parts.append(f"\n\n# 故事圣经（来自 story-bible）\n{bible_body}")

        # === 按需 skill（关键词检测） ===

        if _novel_has_combat(novel_text):
            combat_body = mod.read_skill("combat-mastery")
            if combat_body:
                parts.append(f"\n\n# 打斗工艺全集（来自 combat-mastery）\n{combat_body}")

        if _novel_has_entrance(novel_text):
            entrance_body = mod.read_skill("character-entrance")
            if entrance_body:
                parts.append(f"\n\n# 人物出场四大方式（来自 character-entrance）\n{entrance_body}")

        return "".join(parts)
    except Exception:
        pass
    return ""


# v6.43 08-25：knowledge_skills 按需加载关键词检测
_COMBAT_KEYWORDS = ("打斗", "刺杀", "拔剑", "挥刀", "搏斗", "砍", "击", "拳", "剑", "刀", "枪", "厮杀", "搏击", "刺客", "血战")
_ENTRANCE_KEYWORDS = ("出场", "登场", "亮相", "走入", "缓步", "迈步", "踏出", "下轿", "下马")


def _novel_has_combat(novel_text: str) -> bool:
    """novel_text 是否含打斗关键词（决定是否注入 combat-mastery）."""
    if not novel_text:
        return True  # 无 novel_text 时默认注入（保险）
    return any(kw in novel_text for kw in _COMBAT_KEYWORDS)


def _novel_has_entrance(novel_text: str) -> bool:
    """novel_text 是否含出场关键词（决定是否注入 character-entrance）."""
    if not novel_text:
        return True
    return any(kw in novel_text for kw in _ENTRANCE_KEYWORDS)


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
    # v6.62.4（09-08）解除二次遮蔽：旧实现把高潮分支嵌在 `if not dist["valid"]`
    # 内，而零高潮时 gap_analysis 旧代码判 status=pass → valid=True → 外层进不去，
    # 这个分支**正好在它该报的场景失效**（09-08 实测反馈文本为空）。
    # 现改为：高潮覆盖独立判断，不受 valid 影响（防御纵深——即使上游 status
    # 判定将来又被改动，补高潮的反馈仍能到达 LLM）。
    _climax_shots = dist.get("climax_shots")
    # C1 修复（08-11）：旧实现读 `arc.arc_coverage.has_climax_coverage`，但
    # gap_analysis 返回的 distribution dict 用的是顶层 `has_climax_coverage`
    # ——键路径不匹配，分支永远不进、分布问题反馈从不到达 LLM。改读真实键。
    has_climax = bool(dist.get("has_climax_coverage", False))
    if dist and not has_climax:
        issues.append(
            "【镜头分布问题】**正片零高潮覆盖**——后半段镜头完全未覆盖高潮剧情"
            "（战斗/刺杀/反转/冲突/追逐等强事件）"
        )
        issues.append(
            "  → 后半段至少 3 镜必须覆盖原文结尾的冲突/反转/动作高潮，"
            "每镜写明具体动作（谁对谁做了什么）与台词；禁止用空镜/群演/环境镜充数"
        )
            # 附原文高潮段（v6.63, 09-10）
        # 只说"补高潮"而不给原文，LLM 容易瞎编剧情走向
        # 直接贴原文 → LLM 只需"对着原文拆3镜"，大幅降低规划方差
        _excerpt = dist.get("novel_climax_excerpt") or ""
        if _excerpt:
            issues.append("  → 以下是原文冲突最密集的段落（高潮段），请据此拆至少3镜：")
            # 每行加缩进，保持反馈区格式一致
            for _line in _excerpt.strip().splitlines():
                if _line.strip():
                    issues.append("     " + _line.strip())
            issues.append("  → 要求：逐镜标注原文哪一句对应镜头画面（grounding 校验用）")

    elif isinstance(_climax_shots, int) and 0 < _climax_shots < 3:
        issues.append(
            f"【镜头分布问题】高潮镜仅 {_climax_shots} 个，少于 3 个"
        )
        issues.append("  → 请把高潮拆为至少 3 个独立镜头（如：跃出、寒光一闪、刺入）")

    # 问题3: 高潮镜全哑（v6.62.8，09-09）——doubao_system.md R1(c)
    # 09-09 实跑：正片终于拍出高潮（镜 5/6/10），但三镜台词全空，
    # 全片 11 镜仅 1 镜有台词、70s 只有 2 条字幕。而原文高潮段恰是全章
    # 对白最密集处。R1(c) 规则一直写在 system prompt 里但**无人校验**，
    # 于是从未生效——补一条定位到具体镜号的反馈。
    _silent_climax = dist.get("silent_climax_shots") or []
    if _silent_climax and isinstance(_climax_shots, int) and _climax_shots > 0:
        if len(_silent_climax) == _climax_shots:
            issues.append(
                f"【高潮镜全哑】高潮镜 {', '.join(str(i) for i in _silent_climax)} "
                f"的 narration_segment/subtitle **全部为空**——违反 R1(c)"
                f"「高潮镜必有对白，禁止全镜沉默」"
            )
            issues.append(
                "  → 从原文高潮段逐字摘对白填进这些镜头（格式「角色名：台词」，"
                "5-15 字）；原文该段本就对白密集，禁止编造原文没有的台词"
            )
            # v6.62.8：附**可直接照抄的台词池**。只说「补台词」而不给料，
            # LLM 会自己编——09-09 实测重试版凭空造 5 条原文没有的台词，
            # 被回退保护判退化扔掉，终稿台词镜反而 2→1。给了池子就是「挑选」
            # 而非「创作」，与「台词必须逐字来自原文」不再互相矛盾。
            _src_lines = dist.get("source_dialogue") or []
            if _src_lines:
                issues.append("  → 可直接引用的原文台词（照抄，勿改字）：")
                for _ln in _src_lines:
                    issues.append(f"     · {_ln}")

    # 生成反馈文本
    if issues:
        gap_feedback = "\n".join(issues)
        return f"\n\n# 差距分析反馈（来自本地校验）\n{gap_feedback}\n"
    return ""


def _build_regen_iron_rules() -> str:
    """重生成铁律（v6.47 反馈前置）——GAP/收敛重试时注入反馈区最前.

    长 prompt 后段规则易失声（v6.43 实测 + 08-27 复现：总时长/kf/合规规则被
    尾部反馈淹没，重试版把时长写塌到 11s、编造原文没有的台词）。重试 prompt
    必须在反馈前重申非协商规则，作为「本轮拒收标准」。
    """
    from step01_script_gen.timecode import active_shot_cap
    _cap = active_shot_cap()
    return (
        "\n\n# ★ 重生成铁律（最高优先级，违反本轮拒收）\n"
        "1. **正片分镜 SUM(duration_sec) 必须在 [60s, 90s]，规划目标 70-80s**（不含钩子），"
        f"每镜 ≥3.5s 且 ≤ {_cap:.0f}s 上限（对话镜 {_dur_range(6, 15, _cap)} / 动作镜 {_dur_range(3.5, 8, _cap)} / 空镜 {_dur_range(3.5, 6, _cap)}）；"
        "禁止 1-2s 短镜堆叠凑镜头数，也禁止全章一刀切默认 6s——写时长时先算 SUM，"
        "不足 60s 就加长对话镜/加反应镜，超 80s 就压缩无对白过渡镜。\n"
        "2. **台词（narration_segment/subtitle）必须逐字来自原文**——禁止编造原文没有的"
        "对白（如『凶手是谁？如何击杀了公子』这类原文不存在的台词）。\n"
        "3. **合规**：禁止「击杀/杀人/尸体」等敏感词，用「出人命/倒地/倒下」替换。\n"
        "4. **每镜 kf.t_sec 必须在 [0.1, 0.9]×duration_sec 内**——写 kf 前先定 "
        "duration_sec，不要写绝对值（如 24.5s/44.1s 这种远超单镜时长的值）。\n"
        "5. **为补覆盖率可以加镜头/扩写镜头，但禁止推翻上一版已通过的内容重写**。\n"
    )


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
    knowledge_ctx = _load_knowledge_skills(novel_text=novel_text)
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


# ---------------------------------------------------------------------------
# doubao-seed-2-1-turbo 独立 prompt（2026-09-03 接入）
# 复用 main.md / hook.md 主体，仅 system prompt 走 doubao_system.md
# (XML schema + 2 few-shot + 显式步骤链；适配 thinking=disabled)。
# ---------------------------------------------------------------------------

# v6.59：分级截断（段边界 → 句号 → 字符兜底），原 09-03 实装仅做句号边界，
# 切在段中间会丢开场伏笔/场景切换/冲突起始。
# 不设最低保留比例：段落边界 50% 也比字符切碎好，强制 ≥70% 会让某些章开篇丢失。
_PLOT_UNIT_ENDERS = ("\n\n", "。", "！", "？", "；")


def _slice_by_plot_unit(text: str, max_chars: int) -> str:
    """分级截断长文本为长度上限 max_chars，按以下顺序寻找截断点：
      1. 段落边界（\\n\\n）——保完整剧情单元（场景/场景切换/段间空行）
      2. 句号边界（。！？；）——保完整句子
      3. 字符边界兜底——超出 max_chars 也强制截断

    总是用「最晚出现的边界」（rfind），最大化保留上下文。
    即使段落边界只保留 50%，也比字符切碎好——后者丢的不止是字符而是「剧情意义」。
    """
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    for ender in _PLOT_UNIT_ENDERS:
        idx = truncated.rfind(ender)
        if idx > 0:
            return truncated[:idx + len(ender)]
    return truncated


def _compact_doubao_outline_anchor(outline_anchor: str, max_chars: int = 200) -> str:
    """Compress outline anchors while always retaining the climax line."""
    lines = [line.strip() for line in outline_anchor.splitlines() if line.strip()]
    if not lines:
        return ""

    def joined_len(parts: list[str]) -> int:
        return sum(len(part) for part in parts) + max(len(parts) - 1, 0)

    if joined_len(lines) <= max_chars:
        return "\n".join(lines)

    climax = next(
        (line for line in lines if line.startswith("【高潮事件】")),
        "",
    )
    if not climax:
        kept: list[str] = []
        for line in lines:
            if joined_len(kept + [line]) > max_chars:
                break
            kept.append(line)
        return "\n".join(kept)

    kept = []
    for line in lines:
        if line == climax:
            continue
        if joined_len(kept + [line]) + 1 + len(climax) > max_chars:
            break
        kept.append(line)
    return "\n".join(kept + [climax])


def build_doubao_prompt(novel_text: str, bank=None, objects_bank=None,
                        outline_anchor: str = "", prop_anchor_block: str = "") -> str:
    """doubao-seed-2-1-turbo 专用分镜生成 prompt.

    与 build_prompt 区别：
      - system prompt 走 DOUBAO_SYSTEM_PROMPT（XML schema 块 + few-shot + workflow 9 步）
      - main 主体复用 PROMPT_TEMPLATE（业务规则统一，schema 同步）
      - 不做 Doubao 私有截断；由上游 PROMPT_MAX_CHARS 统一控制章节上限，
        避免后半段高潮/反转被切到 prompt 外
      - **outline_anchor 精简到约 200 字符且必保【高潮事件】**（避免完整大纲
        被当成 schema 字段；高潮行是分镜分布的最关键锚点，压缩时不可丢弃）
      - 同样注入 character_anchors / scene_object_anchors / vocab / local_ctx / knowledge_ctx /
        cap_ctx / total_dur_ctx（与 build_prompt 对齐）

    用法：TEXT_PROVIDER=agent_plan 时 main.py 调用本函数；其他 provider 仍走 build_prompt。
    """
    from shared.config import SHOT_COUNT_MIN, SHOT_COUNT_MAX
    char_anchors = _build_character_anchors(bank) if bank else "无已知角色"
    scene_object_anchors = _build_scene_object_anchors(objects_bank) if objects_bank else "无场景物体"

    # 2026-09-03 现场压测：main_events 完整注入会让模型把"大纲"当 schema 字段
    # 输出额外字段（gender/age_range/main_events 等），违反 R6 规则。
    # 精简到核心 1-2 行：优先保留主事件与高潮；情绪弧线/结尾钩子可让位。
    if outline_anchor and len(outline_anchor) > 200:
        outline_anchor = _compact_doubao_outline_anchor(outline_anchor)

    # 注入 doubao 专属 system + 复用 main 主体
    main_body = PROMPT_TEMPLATE.format(
        novel_text=novel_text,
        character_anchors=char_anchors,
        scene_object_anchors=scene_object_anchors,
        audio_instruction=_build_audio_instruction(),
        shot_count_min=SHOT_COUNT_MIN,
        shot_count_max=SHOT_COUNT_MAX,
    )
    prompt = main_body

    if outline_anchor:
        prompt = prompt + "\n\n### 本章大纲（分镜必须覆盖以下事件与高潮）\n" + outline_anchor

    vocab = _build_cinematic_vocab()
    if vocab:
        prompt = prompt + "\n" + vocab
    local_ctx = _load_local_skill_context()
    if local_ctx:
        prompt = prompt + local_ctx
    knowledge_ctx = _load_knowledge_skills(novel_text=novel_text)
    if knowledge_ctx:
        prompt = prompt + knowledge_ctx
    cap_ctx = _duration_cap_guidance()
    if cap_ctx:
        prompt = prompt + cap_ctx
    total_dur_ctx = _build_total_duration_constraint(shot_count_min=SHOT_COUNT_MIN,
                                                     shot_count_max=SHOT_COUNT_MAX)
    if total_dur_ctx:
        prompt = prompt + total_dur_ctx

    # v6.125.2（用户 2026-09-25 决定）：道具状态候选走**独立短提示**追加在**末尾**
    # ——不并进 `outline_anchor`（doubao/agent_plan 剖面会把大纲锚点压到约 200 字，
    # 除【高潮事件】外的行会被裁掉，候选锚点必被吞）。两条剖面统一走这条路径，
    # 便于对照。本块**只影响提示词**：不接 BEST-GUARD、不接质量门、不自动删 props。
    if prop_anchor_block:
        prompt = prompt + "\n\n" + prop_anchor_block

    return prompt


def build_doubao_hook_prompt(novel_text: str, bank=None, objects_bank=None,
                             forbidden_lines: list[str] | None = None) -> str:
    """doubao 钩子分镜 prompt：与 build_hook_prompt 逐字等价，委托单源实现.

    v6.60 时代两者是双写实现；v6.61 provider-neutral 后经 AST 对比确认零差异，
    改为直接委托。保留函数名是因为 auto_select_prompt_builder 返回 (main, hook)
    函数对，test_prompt_provider_selection 锁定该选择契约。
    """
    return build_hook_prompt(novel_text, bank=bank, objects_bank=objects_bank,
                             forbidden_lines=forbidden_lines)


def auto_select_prompt_builder(provider: str):
    """根据 TEXT_PROVIDER 自动选择 prompt builder.

    Returns:
        (build_main_fn, build_hook_fn) 元组，分别对应主分镜和钩子分镜构建函数。

    用法：main.py 不再 hardcode build_prompt / build_hook_prompt，而是
    `main_fn, hook_fn = auto_select_prompt_builder(TEXT_PROVIDER)` 后调用。
    """
    if provider == "agent_plan":
        return build_doubao_prompt, build_doubao_hook_prompt
    return build_prompt, build_hook_prompt


def auto_select_system_prompt(provider: str) -> str:
    """Select the provider's system prompt without changing the main framework."""
    if provider == "agent_plan":
        return DOUBAO_SYSTEM_PROMPT
    return SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# v6.68: 按实际模型选 prompt 剖面（per-model prompt profile）
# ---------------------------------------------------------------------------
# 背景（2026-09-12 47 分事故分析）：TEXT_PROVIDER=agent_plan 时所有候选模型共用
# build_doubao_prompt + DOUBAO_SYSTEM_PROMPT，novel 被 _DOUBAO_NOVEL_MAX=1600 截断。
# 该截断是 09-03 为 doubao 180s 超时压测定的——本章高潮在原文 2878-4847 字，被切在
# prompt 外 → 候选结构性零高潮，全靠 GAP 救援兜底。该私有截断已于本轮移除；
# 章节长度统一由上游 PROMPT_MAX_CHARS 控制。
# 规则：子串匹配、大小写不敏感、具体在前泛化在后（同 shared/ref_compose 惯例）。
# 威胁源统一写「神秘人」的 main 规则由后处理折叠约束处理，不作为模型画像差异。

MODEL_PROMPT_PROFILES = [
    # (模型名子串, main_builder, system_prompt)
    ("doubao", build_doubao_prompt, DOUBAO_SYSTEM_PROMPT),
    ("deepseek", build_prompt, SYSTEM_PROMPT),
]


def resolve_model_prompt_profile(model: str | None = None):
    """按实际模型解析 (main_builder, system_prompt)，prompt 跟着模型走.

    - model 给定：按 MODEL_PROMPT_PROFILES 子串匹配（大小写不敏感）
    - model=None：按 provider 默认模型解析（_text_gen.config.default_text_model），
      保证 GAP 重生成等「不指定模型」的调用点与实际提交的模型一致
    - 未注册模型/解析失败：回退 provider 级选择（auto_select_prompt_builder，旧行为）

    Returns:
        (build_main_fn, system_prompt) 二元组
    """
    if not model:
        try:
            from _text_gen.config import config as _tg_cfg
            model = _tg_cfg.default_text_model()
        except Exception:
            model = None
    if model:
        _m = model.lower()
        for _sub, _builder, _system in MODEL_PROMPT_PROFILES:
            if _sub in _m:
                return _builder, _system
    # 未注册模型 → provider 级选择兜底（旧行为）
    try:
        from _text_gen.config import config as _tg_cfg
        _provider = _tg_cfg.PROVIDER
    except Exception:
        from shared.constants import TEXT_PROVIDER_DEFAULT
        _provider = TEXT_PROVIDER_DEFAULT
    _main_fn, _ = auto_select_prompt_builder(_provider)
    return _main_fn, auto_select_system_prompt(_provider)


# 兼容别名
