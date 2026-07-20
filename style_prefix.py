"""古风视觉风格前缀与全局渲染配置 — aiVideo 单一来源.

固定前缀在四处出现，必须逐字一致：
  - step01_script_gen/prompts.py 的 PROMPT_TEMPLATE（LLM 生成 shot.prompt 时被要求以此开头）
  - 本模块 STYLE_PREFIX（02b_vimax_gen.py / vimax_adapters.py 运行时复用）
  - skill_spy.txt Skill1（参考文档）
改动任一处须同步其余三处。
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

# ===========================================================================
# 一、固定前缀（四源一致）
# ===========================================================================

STYLE_PREFIX = (
    "古风真人影视剧，9:16竖屏，电影质感，暗调低饱和，"
    "光影随场景自然变化，浅景深缓慢推镜，写实古风风格，高清1080P"
)

# 精简版前缀（用于减少 token 占用）
STYLE_PREFIX_SHORT = "古风真人"

NEGATIVE_PROMPT = "动漫风格、动画、2D渲染、卡通脸、卡通人物、三头身、Q版、动漫脸、画风突变、畸形五官、异形船只、四叶草、现代服装、3D渲染、塑料质感、English text, watermark, logo, English speech"

# ===========================================================================
# 二、全局渲染配置（输出 JSON 顶层字段来源）
# ===========================================================================

_GLOBAL_RENDER_DEFAULTS = {
    "resolution": "1080*1920",
    "ratio": "9:16",
    "fps": 24,
    "render_style": "古风真人",
    "global_lighting": "电影质感，暗调低饱和，光源随场景（白天日光/夜晚烛火灯笼）",
    "global_camera_base": "浅景深，支持缓慢向前推镜，推镜速度约 0.3 倍常规",
    "global_weather": "酷暑燥热",
    "global_atmosphere": "燥热慵懒感，空气蒸腾扭曲，金色扬尘弥漫",
    "negative_prompt": NEGATIVE_PROMPT + "、过曝高光、赛博荧光、西式建筑、机械齿轮",
}

# ===========================================================================
# 三、负向控制：禁用元素合集
# ===========================================================================

# 分层负面词（从紧到松，可根据平台审核阈值切换）
NEGATIVE_LAYERS = {
    "strict": (
        "动漫风格、动画、2D渲染、卡通脸、卡通人物、三头身、Q版、"
        "塑料质感、过曝、赛博荧光、西式建筑、机械齿轮、"
        "现代字体、logo水印、AI伪影、重复纹理、不合理手指、扭曲边缘"
    ),
    "normal": NEGATIVE_PROMPT,
    "relaxed": "动漫风格、动画、2D渲染、现代服装",
}


def get_global_render_config() -> dict:
    """获取当前全局渲染配置（可从 .env 覆盖）."""
    config = dict(_GLOBAL_RENDER_DEFAULTS)
    # 允许 .env 选择性覆盖
    env_overrides = {
        "resolution": os.getenv("RENDER_RESOLUTION"),
        "ratio": os.getenv("RENDER_RATIO"),
        "fps": os.getenv("RENDER_FPS"),
        "render_style": os.getenv("RENDER_STYLE"),
    }
    for k, v in env_overrides.items():
        if v:
            if k == "fps":
                config[k] = int(v)
            else:
                config[k] = v
    return config


def inject_global_config_into_script(script: dict) -> dict:
    """将全局渲染配置注入 script dict 的顶层字段.

    不会覆盖已有字段。
    """
    config = get_global_render_config()
    for k, v in config.items():
        if k not in script:
            script[k] = v
    return script


def validate_fps_consistency(script: dict) -> None:
    """校验 script 中所有涉及帧率/时长的字段是否与全局配置一致."""
    fps = script.get("fps", 24)
    timeline = script.get("timeline", [])
    for entry in timeline:
        dur = entry.get("duration", 0)
        # 粗略校验：duration * fps 应为整数帧
        frame_count = round(dur * fps, 2)
        fractional = frame_count - int(frame_count)
        if abs(fractional) > 0.01:
            print(f"[WARN] 镜头{entry.get('index')} duration={dur}s "
                  f"与 fps={fps} 不对齐（帧数={frame_count}）")


# ===========================================================================
# 四、合规化处理
# ===========================================================================

_SENSITIVE_SUBSTITUTIONS = {
    # 裸露相关
    "光腚": "赤膊", "裸体": "赤身",
    # 死亡/暴力基础替换（用户指定词表）
    "杀": "击", "刺杀": "掠杀", "刺": "掠", "死": "倒", "毙命": "倒地",
    # 尸体/死亡相关
    "尸体": "身影", "死亡": "倒下", "毙命": "倒地", "尸骸": "遗影",
    # ============================================================
    # 血腥类（按用户提供的对照表扩展 v6）
    # ============================================================
    # 血腥嗅觉/味觉类（语义识别，不只是字面匹配）
    "腥甜": "奇怪的味道", "血腥味": "异样味道", "血腥": "红影",
    "腥味": "异味", "血液味": "淡淡味道",
    # 血液液体类
    "血液": "淡红色痕迹", "鲜血": "淡红水渍", "血迹": "淡红印记",
    "血滴": "淡红水点", "血痕": "痕迹", "血": "红",
    "血液滴落": "痕迹滴落", "血液飞溅": "细碎印记散落",
    "鲜血飞溅": "淡色水点散开", "血溅": "淡色水点",
    "血流一地": "淡红水渍蔓延", "血流成河": "赤色浸染",
    "鲜血淋漓": "赤色浸染", "血肉模糊": "赤影朦胧",
    # 伤口刺入暴力细节
    "刺入心口": "剑光掠过", "刺进心口": "剑光掠过",
    "刺伤": "剑影交错", "伤口": "身形一晃", "伤口渗血": "衣襟湿渍",
    "皮肉破损": "衣衫碎裂", "砍断手臂": "剑影交错", "砍伤": "剑光过",
    "刺入": "剑光", "桶入": "剑光", "扎入": "剑光",
    "折断": "断裂", "扭断": "错位", "掐断": "停止",
    "斩首": "剑光过", "割喉": "剑影闪", "割开": "剑光掠过",
    # 飞溅流淌特效
    "血溅": "淡色水点", "喷血": "赤色飞散", "滴血": "赤色滴落",
    # 断肢/内脏
    "断头": "倒下", "断臂": "身影", "断肢": "身影",
    "肢解": "倒下", "开膛破肚": "伤裂", "脑浆": "浊液",
    "内脏": "体内", "肠子": "体内", "断肢": "身影",
    # ============================================================
    # 暴力动作细节（按用户提供的对照表）
    # ============================================================
    "杀死": "击败", "击杀": "制服", "掐死": "制住", "勒死": "困住", "毒死": "倒下",
    "尸体堆积": "身影重叠", "尸横遍野": "残垣断壁",
    # 感官关联血腥（语义级禁止）
    "品尝血": "味道在口中散开", "沾到血": "衣襟沾湿", "伤口血流": "衣襟湿渍",
    # ============================================================
    # 性暗示
    # ============================================================
    "亲吻": "凝望", "拥抱": "相视", "抚摸": "凝视",
    "裸露": "衣襟微敞", "酥胸": "衣襟", "玉体": "身影",
}


def sanitize_prompt(prompt: str, verbose: bool = True) -> str:
    """替换可能触发内容审核的敏感词."""
    out = prompt
    for old, new in _SENSITIVE_SUBSTITUTIONS.items():
        if old in out:
            out = out.replace(old, new)
            if verbose:
                print(f"  [合规] prompt 已替换敏感词: '{old}' -> '{new}'")
    return out


def sanitize_script_shots(script: dict, verbose: bool = True) -> dict:
    """对 script 中所有镜头 prompt 做敏感词替换."""
    for shot in script.get("shots", []):
        for field in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            val = shot.get(field, "")
            if val:
                shot[field] = sanitize_prompt(val, verbose=verbose)
    return script


def ensure_prefix(prompt: str, prefix: str = STYLE_PREFIX) -> str:
    """若 prompt 未以 prefix 开头则拼接，已含则原样返回."""
    if prefix and prefix.split("，")[0] not in prompt.split("，")[0]:
        return f"{prefix}，{prompt}"
    return prompt


# ============================================================================
# 空镜检测（供 prompt_builder 与 vimax_bridge 共用，避免两处逻辑分歧）
# ============================================================================

# 泛人物特征字（角色库之外的路人/群像，如"赤膊娃娃""车夫"）
# 用于空镜检测兜底：宁可漏判（不加空镜约束）也不能错杀画面里该有的人
PERSON_HINTS = (
    "人", "娃", "孩", "童", "汉", "翁", "叟", "妇", "女", "男",
    "客", "卒", "兵", "仆", "婢", "侍", "夫", "郎", "姑", "婆",
    "身影", "众", "民",
)


def mentions_person(text: str) -> bool:
    """文本是否提到泛人物（角色库之外的路人/群像）."""
    return any(k in text for k in PERSON_HINTS)


def is_empty_scene(shot: dict, characters: list[dict]) -> bool:
    """判断镜头是否为空镜（画面中不应出现任何人物）.

    依据：main_subject 不是角色、画面描述（motion_desc/ff_desc）未提到
    角色名、也无泛人物字眼。注意不检查 context_bridge —— 它描述的是
    上一镜头的画面，常含上一镜头的角色名，会造成误判。
    """
    main_subject = shot.get("main_subject", "")
    motion_desc = shot.get("motion_desc", "").strip() or shot.get("prompt", "")
    ff_desc = shot.get("ff_desc", "")
    search_text = f"{main_subject} {motion_desc} {ff_desc}"

    has_character = any(
        c.get("identifier") and c["identifier"] in search_text
        for c in (characters or [])
    )
    return not has_character and not mentions_person(search_text)


# ============================================================================
# 五、敏感词校验（v6 新增）
# ============================================================================

# 高危敏感词列表（必须完全禁止出现）
_SENSITIVE_FORBIDDEN = {
    # 血腥类（任何形式都不允许）
    "血", "血液", "鲜血", "血迹", "血腥", "腥甜", "血腥味",
    "品尝血", "伤口渗血", "喷血", "血溅", "血流",
    # 极端暴力
    "割喉", "斩首", "开膛破肚", "肢解", "分尸",
    # 裸露性描写
    "裸露", "赤身", "光腚", "赤膊",
}


def validate_sensitive_words(script: dict, verbose: bool = True) -> dict:
    """校验脚本中是否还有残留敏感词，返回问题列表.

    Returns:
        dict with 'has_issues' (bool), 'issues' (list of dicts)
    """
    issues = []

    for shot in script.get("shots", []):
        shot_idx = shot.get("index", "?")

        # 检查所有文本字段
        for field in ("prompt", "ff_desc", "lf_desc", "motion_desc", "narration_segment", "subtitle"):
            text = shot.get(field, "")
            if not text:
                continue

            for forbidden in _SENSITIVE_FORBIDDEN:
                if forbidden in text:
                    issues.append({
                        "shot": shot_idx,
                        "field": field,
                        "word": forbidden,
                        "context": text[max(0, text.find(forbidden) - 10):text.find(forbidden) + len(forbidden) + 10],
                    })

    if issues and verbose:
        print(f"[WARN] 检测到 {len(issues)} 处残留敏感词:")
        for issue in issues[:10]:  # 最多显示10个
            print(f"  镜头{issue['shot']} {issue['field']}: 包含 '{issue['word']}'")

    return {"has_issues": len(issues) > 0, "issues": issues}
