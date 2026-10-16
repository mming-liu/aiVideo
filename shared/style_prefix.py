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
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

# ===========================================================================
# 一、固定前缀（四源一致）
# ===========================================================================

STYLE_PREFIX = (
    "古风真人影视剧，9:16竖屏，电影质感，暗调低饱和，"
    "光影随场景自然变化，浅景深缓慢推镜，写实古风风格，高清1080P，"
    "轻微电影胶片颗粒，真实镜头微反差，轻微自然暗角"
)

# 精简版前缀（用于减少 token 占用）
STYLE_PREFIX_SHORT = "古风真人"

NEGATIVE_PROMPT = "动漫风格、动画、2D渲染、卡通脸、卡通人物、三头身、Q版、动漫脸、画风突变、畸形五官、异形船只、四叶草、现代服装、现代建筑、现代楼房、水泥建筑、玻璃幕墙、电线杆、汽车、摩托车、现代设施、3D渲染、塑料质感、English text, watermark, logo, English speech、画面字幕、中文文字、水印"

# ===============================================================================
# 图片幻觉专项防护（2025-07-23 v1 渐进增强）
# ===============================================================================

# 图片防幻觉锁图正向关键词（注入 prompt）
IMAGE_HALLUCINATION_LOCK_PROMPT = (
    "identity locked, same face, consistent facial structure, fixed costume, "
    "no extra objects, consistent scene layout, "
    "面部锁定，统一五官，固定服装，无多余杂物，场景布局不变"
)

# 图片防幻觉锁图 - 尾帧/关键帧版（解除"场景布局不变"锁）
# 尾帧/关键帧需表现动作演变与构图变化，若沿用带"布局不变"的完整锁图，
# 再叠加首帧整图参考，构图会被锁死在首帧上，三帧雷同、动作无法推进。
# 保留五官/服装/杂物锁定，去掉 "consistent scene layout / 场景布局不变"。
IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT = (
    "identity locked, same face, consistent facial structure, fixed costume, "
    "no extra objects, "
    "面部锁定，统一五官，固定服装，无多余杂物"
)

# 图片防幻觉追加负向词（追加到现有负向词）
IMAGE_HALLUCINATION_EXTRA_NEGATIVE = (
    "換臉、五官变形、臉型改变、五官錯位、多指、畸形手腳、扭曲肢體、"
    "漂浮物体、凭空多出家具、多余摆件、乱加裝饰、服装花紋突变、"
    "發色改变、妝容变化、畫面文字、水印、塗鴉、场景結構錯亂、"
    "人物大小忽大忽小、多余人物、模糊五官"
)

# ===========================================================================
# 纯中文版（2026-08 决策：模型均为国内模型，prompt 只留中文；
# 英文负面词/正向词仅 Agnes 使用，中文模型用纯中文版）
# ===========================================================================
# NEGATIVE_PROMPT 去掉英文 token（"English text, watermark, logo, English speech"）的纯中文版
NEGATIVE_PROMPT_CN = NEGATIVE_PROMPT.replace(
    "English text, watermark, logo, English speech、", ""
)
# 图片锁图正向词纯中文版（英文半句仅 Agnes 用，中文模型读中文即可）
IMAGE_HALLUCINATION_LOCK_PROMPT_CN = "面部锁定，统一五官，固定服装，无多余杂物，场景布局不变"
IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT_CN = "面部锁定，统一五官，固定服装，无多余杂物"


def video_negative_prompt() -> str:
    """视频负向词：英文 token 仅 Agnes 使用；中文模型用纯中文版."""
    if os.getenv("VIDEO_PROVIDER", "bailian") == "agnes":
        return NEGATIVE_PROMPT
    return NEGATIVE_PROMPT_CN


def image_negative_prompt() -> str:
    """图片负向词：英文 token 仅 Agnes 使用；中文模型用纯中文版."""
    if os.getenv("IMAGE_PROVIDER", "bailian") == "agnes":
        return NEGATIVE_PROMPT
    return NEGATIVE_PROMPT_CN


def image_lock_prompt(layout_lock: bool = True) -> str:
    """图片防幻觉锁图正向词：英文半句仅 Agnes 使用；中文模型用纯中文版.

    layout_lock=True 用带「场景布局不变」的完整锁（首帧）；
    False 用去除布局锁的版本（尾帧/关键帧需表现动作演变）。
    """
    if os.getenv("IMAGE_PROVIDER", "bailian") == "agnes":
        return IMAGE_HALLUCINATION_LOCK_PROMPT if layout_lock else IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT
    return IMAGE_HALLUCINATION_LOCK_PROMPT_CN if layout_lock else IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT_CN

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
# 四、风格库（10种AI动画风格 + 古风真人）
# ===========================================================================

STYLE_PRESETS = {
    # 1. 古风真人（默认）
    "gufeng": {
        "prefix": "古风真人影视剧，9:16竖屏，电影质感，暗调低饱和， 光影随场景自然变化，浅景深缓慢推镜，写实古风风格，高清1080P， 轻微电影胶片颗粒，真实镜头微反差，轻微自然暗角",
        "negative": "动漫风格、动画、2D渲染、卡通脸、卡通人物、三头身、Q版、 动漫脸、画风突变、畸形五官、异形船只、四叶草、现代服装、3D渲染、 塑料质感、English text, watermark, logo, English speech",
        "ratio": "9:16",
        # v6.23：技术参数（seedance-storyboard/Micro-Drama 吸收，古风化适配）
        "camera": "中长焦镜头（约50mm人像焦段），浅景深",
        "film_stock": "复古胶片质感，轻微颗粒",
        "filter": "自然柔光滤镜，暗角轻微",
        "focal_length": "50mm",
        "aperture": "f/2.0",
        "lighting": "烛火/月光为主光源，暖金与冷蓝交织，侧逆光勾勒轮廓",
    },
    # 2. 吉卜力动画
    "ghibli": {
        "prefix": "治愈系水彩蜡笔手绘风，日系独立动画电影质感，水彩、蜡笔和柔和油画颗粒混合笔触，层叠的绿色山谷、远处蓝紫色群山、金色夕阳、风吹野花， 低饱和自然色，人物比例真实但略带绘本感，空气透视，细腻体积光， 安静、孤热、希望、成长，16:9，电影级构图，原创角色，连续角色设计",
        "negative": "城市、现代建筑、强烈饱和、写实照片、人物脸部变形、 服装变化、额外人物、写实CG",
        "ratio": "16:9",
    },
    # 3. 治愈系插画风
    "healing": {
        "prefix": "温暖生活方式手绘插画动画，扁平色块与纸张颗粒，奶油白背景， 芥末黄、鼠尾草绿、陶土橙、深棕色配色，简洁黑色线稿，植物和器物作为装饰图案， 柔和晨光，轻微二维纸片运动，治愈、松弛、日常、女性生活氛围， 16:9，电影海报级构图，原创人物",
        "negative": "复杂写实纹理、阴暗氛围、人物比例突变、过多文字和品牌标志、写实照片",
        "ratio": "16:9",
    },
    # 4. 童话绘本风
    "fairy_tale": {
        "prefix": "儿童绘本定格动画，米白色纸张背景，剪纸、软陶、拼贴和手绘铅笔线混合材质， 留白构图，橙色花瓣像魔法粒子，女孩与橙色小精灵形成互补色，柔和侧光， 微小定格抖动，边缘可见纸张纤维，温柔、童真、友谊、秋日氛围，16:9，原创角色",
        "negative": "写实真人、复杂背景、恐怖元素、过多文字、现代建筑",
        "ratio": "16:9",
    },
    # 5. 油画电影风
    "oil_painting": {
        "prefix": "浪漫写实电影绘画风动画，写实绘画与高质量角色动画融合， 雨后城市地面反光，蓝灰环境与金色街灯，柔焦、胶片颗粒、浅景深、 自然皮肤和布料褶皱，优雅舞蹈动作，镜头环绕、慢动作，旅行蒙太奇， 浓烈但克制的爱情氛围，16:9，电影级构图，原创人物",
        "negative": "城市地标变形、人物脸部漂移、服装颜色改变、过度梦幻发光、 多人群舞、写实CG",
        "ratio": "16:9",
    },
    # 6. 皮克斯3D卡通风
    "pixar": {
        "prefix": "极简定格动画，软陶和纸手工质感，灰白空旷背景，低饱和色调， 角色尺度反差极大，清晰色块，弹跳和飞溅动作，动态低机位广角，16:9， 商业动画电影品质，家庭友好，节奏明快，干净渲染，原创角色",
        "negative": "真实动物、复杂道具、快速剪辑、鲜艳背景、写实照片",
        "ratio": "16:9",
    },
    # 7. 粘土定格风
    "clay": {
        "prefix": "高质感原创3D动画模型，圆润角色造型，夸张大眼睛和丰富面部表情， 柔软皮肤与毛发，明亮室内大厅，暖黄色三点布光，浅景深， 镜头重视反应特写，商业动画电影品质，家庭友好，节奏明快，16:9，干净渲染，原创角色",
        "negative": "恐怖鸟群、真实动物纪录片质感、血腥、人物五官融化、过暗环境、写实CG",
        "ratio": "16:9",
    },
    # 8. 荒诞卡通风
    "absurd": {
        "prefix": "高饱和荒诞卡通动画，明亮蓝天，奶油色沙滩，海水为深蓝和青绿色， 角色尺度反差极大，清晰色块，弹跳和飞溅动作，动态低机位广角， 巨大海怪从水中跃起，水花向镜头飞溅，张开满是尖牙的大嘴，16:9， 夸张表情，幽默冒险，儿童友好，原创角色",
        "negative": "写实恐怖怪物、血腥、复杂路人、过度阴暗、写实照片CG",
        "ratio": "16:9",
    },
    # 9. 孩童草稿风
    "sketch": {
        "prefix": "夸张毛球角色卡通动画，橙色毛球猫咪与灰色细长老鼠，超大白色眼睛， 黄昏灰蓝天空，暖黄色街灯，写实简笔木屋和街道背景，角色极度夸张， 广角透视，运动模糊、速度线、突然急停，喜剧节奏，16:9，原创角色",
        "negative": "真实猫脸、暴力伤害、过度阴暗、角色颜色变化、复杂路人、写实照片",
        "ratio": "16:9",
    },
    # 10. 迪士尼童话风
    "disney": {
        "prefix": "原创童话公主动画，古典宫廷室内，木质墙板、落地镜、暖金色窗光， 红色礼服作为视觉焦点，细腻3D卡通材质，柔软布料、金色发丝、镜面反射， 戏剧性强，优雅、觉醒、自我发现、舞台感，16:9，电影级场景",
        "negative": "恐怖镜像、镜中动作错位、服装变色、现代物件、夸张魔法特效、写实CG",
        "ratio": "16:9",
    },
    # 11. 扁平矢量风
    "flat": {
        "prefix": "现代扁平矢量广告动画，鲜红色一级方程式赛车，浅青色天空，米白色道路， 黄色中心线，红色细杆路灯，干净几何色块，极少阴影，强烈透视， 轮胎烟雾和速度线，节奏明快，品牌广告视觉系统，16:9，高清， 原创赛车与人物，画面边缘整洁",
        "negative": "复杂写实背景、 品牌真实Logo、 车身变形、 赛车颜色变化、 镜头抖动过度、 写实照片",
        "ratio": "16:9",
    },
    # 12. 国风水墨动画
    "ink_painting": {
        "prefix": "东方写意水墨动画，宣纸原生肌理，浓淡干湿多层次水墨晕染， 少量淡彩朱砂、 花青点染， 大面积留白构图， 中式散点透视， 古风武侠人物， 衣袂水墨笔触流动感， 远山雾霭、 竹林石桥、 扁舟溪流， 柔和漫射天光， 朦胧晨雾氛围感， 无强烈硬阴影， 东方诗意、 孤寂、 侠义意境， 16:9电影宽幅， 4K超清， 角色形象全程统一",
        "negative": "写实照片质感、 鲜艳高饱和色彩、 现代建筑、 五官扭曲、 角色服装变色、 大量路人、 厚重阴影、 3D写实建模、 金属塑料反光",
        "ratio": "16:9",
    },
    # 13. 二次元漫剧
    "anime": {
        "prefix": "高清国漫二次元漫剧画风，精致厚涂人物上色，清晰干净黑色轮廓线， 细腻皮肤质感， 发丝分层精细， 柔和电影级漫反射光影， 浅景深虚化背景， 古风/都市小说人物适配， 动态张力强， 衣物布料褶皱真实， 环境细节丰富， 氛围感色调统一， 16:9短视频画幅， 4K高细节渲染， 全程锁定人物五官、 发型、 服饰",
        "negative": "真人写实照片、 低模糊画质、 五官崩坏、 人物发型/服饰突变、 杂乱多余路人、 刺眼高饱和荧光色、 厚重暗黑色块、 塑料廉价材质质感、 画面文字水印",
        "ratio": "16:9",
    },
}


def get_style_preset(name: str = None) -> dict:
    """获取风格预设，默认为古风真人（可通过环境变量 VIDEO_STYLE 切换）.

    用法:
        style = get_style_preset()                    # 从环境变量读取， 默认 gufeng
        style = get_style_preset("ghibli")            # 明确指定吉卜力风格
        style = get_style_preset("pixar")             # 皮克斯风格
    """
    if name is None:
        name = os.environ.get("VIDEO_STYLE", "gufeng")
    return STYLE_PRESETS.get(name, STYLE_PRESETS["gufeng"])


def get_style_prefix(name: str = None) -> str:
    """获取风格前缀 prompt."""
    return get_style_preset(name)["prefix"]


def get_style_negative(name: str = None) -> str:
    """获取风格负向提示词."""
    return get_style_preset(name)["negative"]


# ===========================================================================
# 五、全局渲染配置（兼容旧代码）
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
    """获取当前全局渲染配置（可从 .env.content 覆盖）.

    解析顺序（v6.43 08-23 审计）：
      1. RENDER_RESOLUTION（专用 override，未设则空）
      2. VIDEO_SIZE（项目统一目标尺寸，格式 `WIDTH*HEIGHT`，如 `1440*2560`）
      3. _GLOBAL_RENDER_DEFAULTS["resolution"] = "1080*1920"（仅作 fallback）
    原顺序只查 RENDER_RESOLUTION，没设就回到硬编码 1080*1920——但项目里
    VIDEO_SIZE 才是真值（与 step04 MERGE_RESOLUTION 一致）。
    """
    config = dict(_GLOBAL_RENDER_DEFAULTS)
    # 允许 .env.content 选择性覆盖
    env_overrides = {
        "resolution": os.getenv("RENDER_RESOLUTION") or os.getenv("VIDEO_SIZE"),
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

# 权威替换词表统一收敛在 shared/content_safety.py（审计 P0-2：敏感词三套合一）。
# style_prefix / content_policy_fix / scene_compliance 不再各自维护词表。
from shared.content_safety import (
    SENSITIVE_SUBSTITUTIONS,
    sanitize as _safety_sanitize,
    FORBIDDEN_WORDS,
    WEAPON_WORDS, WEAPON_HIDDEN_WORDS, WEAPON_VISIBLE_WORDS, WEAPON_STAB_VERBS,
)


def sanitize_prompt(prompt: str, verbose: bool = False) -> str:
    """替换可能触发内容审核的敏感词（复用 shared.content_safety 权威词表）."""
    out = _safety_sanitize(prompt)
    if verbose and out != prompt:
        try:
            from shared.tee_logger import get_logger
            from shared.paths import chapter_dir
            get_logger(chapter_dir() / "step02.log").verbose(
                f"  [合规] prompt 已替换敏感词"
            )
        except Exception:
            pass
    return out


def sanitize_script_shots(script: dict, verbose: bool = True) -> dict:
    """对 script 中所有镜头 prompt 做敏感词替换.

    v6.53：main_subject / char_layout[].name 一并走 alias 折叠（此前完全
    跳过合规替换——hook char_layout[0].name="刺客"、shot3 "船夫"、shot10
    main_subject="庄仆" 实测，下游人数名单/肖像查找/锚匹配三方失配）。
    hook 与 shots 同处理。
    """
    targets = list(script.get("shots", []) or [])
    if isinstance(script.get("hook"), dict):
        targets.append(script["hook"])
    for shot in targets:
        for field in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            val = shot.get(field, "")
            if val:
                shot[field] = sanitize_prompt(val, verbose=verbose)
        try:
            from shared.alias_map import alias_walk_shot
            alias_walk_shot(shot)
        except Exception:
            pass
    return script


def ensure_prefix(prompt: str, prefix: str = STYLE_PREFIX) -> str:
    """若 prompt 未以 prefix 开头则拼接，已含则原样返回."""
    if prefix and prefix.split("，")[0] not in prompt.split("，")[0]:
        return f"{prefix}，{prompt}"
    return prompt


# ============================================================================
# 图片生成严格约束（防止 AI 幻觉，2026-07-26）
# ============================================================================

def preprocess_prompt_for_image(prompt: str) -> str:
    """图片生成前预处理：替换 AI 可能误解的词汇.

    图片模型对文言词/颜色词按字面直译（code-audit 2026-08-08 钩子首帧）：
    - "青丝" 是黑发的文言说法，图片模型会把"青"直译成青色/绿色 → 替换为"黑发"
    - "平定四方巾" 未指定颜色时，图片模型在冷青色调下会脑补成绿色/青绿色头巾
      （明制绿头巾是伶人贱籍标识，公子绝不能戴；2026-08-10 杨旭首帧绿头巾修复）
      → 无颜色前缀时强制锁"乌黑色"
    - 其余为模糊称谓归一化
    """
    replacements = {
        "掠客": "刺客",
        "一道人影": "刺客",
        "一道身影": "杀手",
        "蒙面刺客": "刺客",
        "蒙面黑衣人": "黑衣人",
        "青丝": "黑发",        # 文言"青丝"=黑发，图片模型误译成绿/青色
        "乌丝": "黑发",
        "黛眉": "柳眉",        # 黛=青黑色，避免误译
        "平定四方巾": "乌黑色平定四方巾",  # 未锁色会被脑补成绿/青（08-10 杨旭绿头巾）
        "戟指": "手指直指",    # 戟=WEAPON_WORDS 武器词，图片模型把"戟指"(怒指)直译成持戟/持杆（08-15 hook 首帧）
    }
    result = prompt
    for old, new in replacements.items():
        if old == "平定四方巾":
            # 只在头巾前无颜色词时锁色（防已锁色的"乌黑平定四方巾"被二次替换）。
            # 检查"平定四方巾"前 2 字窗口是否含颜色字，含则保持原样。
            import re as _re
            _PINGDING_RE = _re.compile(r"平定四方巾")

            def _lock_black(m):
                pre = result[max(0, m.start() - 2):m.start()]
                if any(c in pre for c in "黑乌青绿白灰素墨黛玄深"):
                    return m.group(0)
                return "乌黑色" + m.group(0)

            result = _PINGDING_RE.sub(_lock_black, result)
        else:
            result = result.replace(old, new)
    return result


# ============================================================================
# ff_desc 归一化：多动作/进行态堆叠 → 动作「前一刻」静态定格
#
# 背景：LLM 生成的 ff_desc 常把多个动作（破水而出 + 惊觉欲起 + 尖叫）堆进
# 一个静态首帧描述，且 ff_desc 与 prompt 高度雷同（都是"破水而出"），导致
# 视频【视频起点】和【视频中段】动作重叠、模型把动作做两遍脑补假动作。
# step01 生成时修复 + step02 消费时兜底，共用本模块这一套函数
# （code-audit 2026-08-07 钩子首帧；v6.19 升级：收敛到动作「前一刻」——
# 破水前=潜伏水下/水面微澜，而非破水而出）。
#
# 设计原则：
#   1. 保守——`detect_ff_multi_action` 是闸门，无信号不动原文；
#   2. 幂等——`normalize(normalize(x)) == normalize(x)`（归一化产物不再含
#      动作动词/时间推进词/过渡态/持续动词，二次检测无信号直接返回）；
#   3. 只碰动作动词/时间连接词/过渡态/持续动词，角色名/静态姿势/场景/视线全保留；
#   4. 只作用于 ff_desc，绝不作用于 lf_desc（动作结果态）/keyframes（进行中瞬间）；
#   5. 产物必须与 motion_desc 首段动作彻底错开（平静→破水→没入），禁止同词重叠。
#
# 注意：不并入 preprocess_prompt_for_image——该函数同时服务 ff 和 lf，
# 而 lf 是动作结果态（尖叫/倒地在那是对的），归一化不能作用到 lf。
# ============================================================================

# 时间推进连接词：表示先后/并行 → 单帧图片不需要，删
FF_TIME_ADVANCE_WORDS = (
    "随即", "然后", "接着", "紧接着", "紧随其后", "紧跟着",
    "与此同时", "同时", "就在这时", "霎时间", "霎那间", "下一瞬", "下一刹", "下一刻",
    "骤然", "猛地", "突然", "忽地", "忽然", "继而", "随之", "随后", "旋即", "立刻", "马上",
)

# 过渡态标记（「即将」刻意不在表内 → 保留，符合"即将刺中→保持"的正确定格）
FF_TRANSITION_MARKERS = (
    "欲起", "欲动", "欲站", "欲纵", "欲扑", "欲跃",
    "正要", "刚要", "准备", "正准备",
)

# 持续声音动词 → 可画静态姿势（(短替换, 完整替换)，完整替换含"张嘴"，
# 分句已有 张嘴/张口/仰头 时用短替换，避免"张嘴张嘴"）。
# 值不得命中本表其他键（防链式替换破坏幂等）：
#   尖叫 → 惊呼/张嘴惊呼（惊呼不在本表键中）
#   惊叫 → 低呼/张嘴低呼（低呼不在本表键中）
FF_SUSTAINED_TO_POSE = {
    "尖声尖叫": ("惊呼", "张嘴惊呼"),
    "发出尖叫": ("惊呼", "张嘴惊呼"),
    "尖叫": ("惊呼", "张嘴惊呼"),
    "惊叫": ("低呼", "张嘴低呼"),
    "怒喝": ("怒喝", "张嘴怒喝"),
    "厉喝": ("厉喝", "张嘴厉喝"),
    "大喊": ("大喊", "张嘴大喊"),
    "大声喊叫": ("喊叫", "张嘴喊叫"),
    "呼救": ("呼救", "张嘴呼救"),
    "放声大哭": ("欲哭", "张口欲哭"),
}

# 进行态动作动词（构成独立"动作时刻"）。
# 注意：单字"抓"不在表内——"抓住船舷/抓住手腕"是静态姿势（FF_STATIC_POSTURE_VERBS），
# 只有"抓向/抓夺/抢夺"这类明显位移动作才算动作时刻。
FF_MOTION_VERBS = (
    "破水", "冲出", "窜出", "跃出", "跃起", "腾空", "翻身", "下坠", "跌落",
    "跌倒", "倒纵", "没入", "潜入", "入水", "落水", "栽倒",
    "刺", "劈", "砍", "斩", "挥", "扑", "撞", "夺", "踢", "踹",
    "拔出", "抽出", "挥出", "砸下", "劈落", "刺下", "劈下",
    "追杀", "疾冲", "狂奔", "疾驰", "纵身", "抓向", "抓夺", "抢夺",
)

# 静态姿势动词（定格的一部分，不构成独立动作时刻）
FF_STATIC_POSTURE_VERBS = (
    "高举", "紧握", "握", "举", "持", "握紧", "指", "盯着", "凝视",
    "注视", "看", "望", "跪", "坐", "立", "站", "躺", "卧",
    "仰头", "低头", "抬头", "张嘴", "张口", "闭眼", "睁眼", "睁大", "微张",
    "前倾", "后仰", "侧身", "半起身", "抓住", "扶着", "倚着", "靠着",
)

# 特定过渡短语 → 具体静态姿势（保守映射，优先于正则兜底）
FF_TRANSITION_REPLACE = (
    ("惊觉欲起", "惊觉、身体前倾"),
    ("惊觉欲站起", "惊觉、半起身"),
    ("欲纵身", "身体前倾"),
    ("欲扑", "身体前倾"),
    ("欲起", "半起身"),
    ("欲动", "身体前倾"),
    ("欲站", "半起身"),
    ("正要起身", "半起身"),
    ("刚要起身", "半起身"),
    ("准备起身", "半起身"),
)

# 进行态动作动词 → 动作「前一刻」的静态描述（短语精确替换，长词优先）。
# 幂等性：产物不得再命中 FF_MOTION_VERBS / FF_TRANSITION_MARKERS / FF_SUSTAINED_TO_POSE；
# 含「即将」的产物因 _collect_ff_action_moments 的「即将」豁免而天然幂等。
# 刺/劈/砍/斩/挥/砸下 不走此表（需保留目标 NP，走 _preactionize_strike 正则）。
FF_MOTION_TO_PREACTION = (
    # ── water_break 破水/出水类 → 潜伏水下（水面微澜） ──
    ("半身破水而出", "潜伏水下，水面微澜"),
    ("半身已冲出水面", "潜伏水下"),          # 有独立水面分句时用「潜伏水下」，避免水面微澜重复
    ("从水中半身跃出", "潜伏水下，水面微澜"),   # v6.51: LLM 常写"黑衣人从水中半身跃出"
    ("从水中半身探出", "潜伏水下，水面微澜"),   # v6.51
    ("半身跃出水面", "潜伏水下"),
    ("半身探出水面", "潜伏水下"),
    ("破水而出", "潜伏水下，水面微澜"),
    ("冲出水面", "潜伏水下"),
    ("窜出水面", "潜伏水下"),
    ("跃出水面", "潜伏水下"),
    ("半身已冲出", "潜伏水下"),
    ("破水", "潜伏水下，水面微澜"),
    ("从水中跃出", "潜伏水下，水面微澜"),   # v6.51: 变体
    ("从水中探出", "潜伏水下，水面微澜"),   # v6.51
    # ── water_enter 倒纵/没入/入水/落水类 → 即将入水 ──
    ("双腿一弹正倒纵入水", "重心后移"),       # 精确处理 shot10（紧随的「身体后仰即将没入水中」分句保留）
    ("倒纵入水", "重心后移，即将入水"),
    ("倒纵", "重心后移"),
    ("没入水中", "身体前倾，即将没入"),
    ("没入", "身体前倾，即将没入"),
    ("入水", "身体后仰，即将入水"),
    ("落水", "身体前倾，即将落水"),
    ("潜入", "身体低伏，即将潜入"),
    ("栽倒", "身体前倾，将倒未倒"),
    # ── jump 跃起/纵身/腾空类 → 曲膝蓄势 ──
    ("纵身跃起", "曲膝蓄势"),
    ("纵身一跃", "曲膝蓄势"),
    ("跃起", "双腿微屈，曲膝蓄势"),
    ("纵身", "双腿微屈，曲膝蓄势"),
    ("腾空", "双腿蹬地，即将腾空"),
    ("翻身", "侧身欲翻，重心偏移"),
    # ── draw 拔/抽类 → 未出鞘 ──
    ("拔出匕首", "手按匕首柄，刀刃未出鞘"),
    ("抽出长刀", "手按刀柄，刀刃未出鞘"),
    ("拔出", "手按柄，刀刃未出鞘"),
    ("抽出", "手按柄，刀刃未出鞘"),
    # ── rush 陆上位移/扑击类 → 蓄势待发 ──
    ("疾冲", "身体前倾，蓄势待发"),
    ("狂奔", "弓身起步，蓄势待发"),
    ("疾驰", "身体前倾，蓄势待发"),
    ("追杀", "身体前倾，蓄势待发"),
    ("扑", "身体前倾，扑势未发"),
    ("撞", "身体前倾，撞势未发"),
    ("抓向", "手臂前伸，指尖将触"),
    ("抓夺", "手臂前伸，指尖将触"),
    ("抢夺", "手臂前伸，指尖将触"),
    ("夺", "手臂前伸，指尖将触"),
    # ── fall 下坠/跌落/跌倒类 → 将倒未倒 ──
    ("下坠", "身体失衡，将坠未坠"),
    ("跌落", "身体失衡，将倒未倒"),
    ("跌倒", "身体失衡，将倒未倒"),
)

# 动作「前一刻」路径下需改写/删除的关联分句。
#   值 None → 删除该分句（动作进行/完成后的效果，前一刻不存在）；
#   值 str  → 改写为前一刻。
FF_PREACTION_CLAUSE_FIXES = {
    "水面破开大洞": "水面微澜",     # 动作瞬间 → 前一刻
    "水面炸开": "水面微澜",
    "破开大洞": "水面微澜",
    "水花四溅": None,          # 破水后才出现 → 删
    "水花溅起": None,
    "水花凝住": None,
    "浪花高卷": None,
    "水珠沿刀刃滑落": None,
    "水珠滑落": None,
    # 手持物+身体粘连病句（08-09 首帧杨旭姿势）：「左手钓杆垂身体后仰」把钓杆
    # 和身体动作混在一个分句，模型理解不了钓杆归属 → 拆成钓杆垂挂 + 身体坐姿。
    "左手钓杆垂身体后仰": "左手握钓杆，钓杆垂向水面",
    "钓杆垂身体后仰": "握钓杆，钓杆垂向水面",
    "钓杆垂身": "握钓杆，钓杆垂挂",
}

# v6.51: water_break 场景下"出水后姿态"→"水下潜伏姿态"的全局替换。
# 长词在前（优先匹配）；产物不得再命中 FF_MOTION_VERBS。
# 用于 normalize_ff_desc 的 anchor-aware 后处理 pass，
# 解决"分句切在一起导致 _preactionize_associated_clause 不生效"的问题。
_WB_POSE_TO_UNDERWATER = (
    ("一手按住晃动的船舷", ""),
    ("一手按稳晃动的船舷", ""),
    ("一手按住船舷", ""),
    ("一手按稳船舷", ""),
    ("按住船舷", ""),
    ("按稳船舷", ""),
    ("（画中船中）", ""),
    ("身体如蛙蹲踞", "身体曲膝收腿"),
    ("如蛙蹲踞到船头", "曲膝收腿"),
    ("踞蹲到船头", "曲膝收腿"),
    ("蹲踞船头", "曲膝收腿"),
    ("踞蹲船头", "曲膝收腿"),
    ("端坐、手肘高扬", "手臂贴于身侧"),
    ("手肘高扬", "手臂贴于身侧"),
    ("刀尖朝下蓄势", "匕首反握贴于腰侧"),
    ("刀尖朝下", "匕首反握"),
    ("一手高扬匕首", "匕首反握贴于腰侧"),
    ("高举匕首", "匕首收于身侧"),
    ("高扬匕首", "匕首收于身侧"),
    ("匕首寒光", "匕首贴于腰侧"),
    ("闪出一道寒光", ""),
    ("端坐", "曲膝"),
)

# 动作残留虚词（前一刻改写时剥离，如「双腿一弹正倒纵入水」里的「一弹/正」）
FF_PREACTION_STRIP_WORDS = (
    "正在", "正要", "猛地", "突然", "骤然", "随即", "一弹", "一扬", "一送", "一下",
)

# 角色反应 → 前一刻（破水前角色还没察觉：惊觉→静坐，惊呼/尖叫→未出声）。
# 长词/完整短语在前（先匹配「惊觉、身体前倾」再匹配「惊觉」），防残词拼接。
# 幂等：产物（静坐/未出声/欲呼）不含 motion/transition/sustained 触发词。
FF_REACTION_TO_PREACTION = (
    ("惊觉、身体前倾", "静坐未察"),
    ("惊觉欲起", "静坐未察"),
    ("惊觉、半起身", "静坐"),
    ("仰头张嘴惊呼", "仰头未出声"),
    ("仰头张嘴尖叫", "仰头未出声"),
    ("张嘴惊呼", "张嘴未出声"),
    ("张嘴尖叫", "张嘴未出声"),
    ("惊呼", "未出声"),
    ("尖叫", "欲呼"),
    ("惊觉", "静坐"),
    ("身体前倾", "端坐"),
    ("双眼瞪大", "神色平静"),
    ("瞪大", "神色平静"),
    ("双眼睁大", "神色平静"),
    ("睁大", "神色平静"),
)


def _subject_names(characters) -> set:
    """角色名集合（含 刺客/杀手/黑衣人 等兜底称谓）. 用于动作主体识别.

    M6 修复 2026-08-14：兜底表原硬编码第一章角色名（杨旭/听香/张十三/老汉/
    肖管事），换章/换书会注入错误的角色约束。现只保留通用称谓兜底，
    具体角色名一律来自 characters 参数。
    """
    names = set()
    for c in (characters or []):
        ident = (c.get("identifier") or "").strip()
        if ident:
            names.add(ident)
    # 兜底常见称谓（非注册角色名也可能出现在 ff_desc）
    for fallback in ("刺客", "杀手", "黑衣人", "蒙面人", "神秘人"):
        names.add(fallback)
    return names


def _mask_subjects(text: str, names: set) -> str:
    """遮蔽角色名，防止单字动词（刺/杀）误匹配人名（刺客/杀手）.

    如"视线全部汇聚于刺客"遮蔽后 刺 不再命中 FF_MOTION_VERBS。
    """
    masked = text
    for n in sorted(names, key=len, reverse=True):
        if n:
            masked = masked.replace(n, "\x00" * len(n))
    return masked


def _ff_existing_state(clause: str, names: set) -> bool:
    """状态/否定限定紧贴动作词时，不把当前事实倒推成尚未发生。"""
    verbs = "|".join(re.escape(v) for v in sorted(FF_MOTION_VERBS, key=len, reverse=True))
    return bool(re.search(r"(?:已经|早已|依旧|始终|一直|尚未|并未|没有|不曾|仍|尚|已|未|不)(?:" + verbs + r")",
                          _mask_subjects(clause, names)))


def _ff_actor_motion(clause: str, names: set) -> bool:
    """只能给明确领句的人物动作生成人体前态；名词宾语不能继承人体姿态。"""
    return any(clause.strip().startswith(n) for n in names if n) and not _ff_existing_state(clause, names)


def _collect_ff_action_moments(text: str, characters=None) -> list[dict]:
    """分句，收集含 进行态/过渡态/持续动作 的分句（= 独立"动作时刻"）.

    「即将X」分句（逼近瞬间的定格，如"即将刺中杨旭"）刻意豁免——
    是可画的静止画面（动作尚未发生），保留不处理（与 _check_ff_motion_timeline
    的 in_progress_exempt 一致）。角色名先遮蔽，防 刺/杀 误匹配 刺客/杀手。

    Returns:
        list[dict]: [{subject, clause, kind}]，kind ∈ motion/transition/sustained
    """
    names = _subject_names(characters)
    moments = []
    for clause in re.split(r"[，,；;。！？]", text):
        clause = clause.strip()
        if not clause:
            continue
        if "即将" in clause:
            continue  # 逼近瞬间的定格：可画，保留
        if _ff_existing_state(clause, names):
            continue
        masked = _mask_subjects(clause, names)
        if any(v in masked for v in FF_MOTION_VERBS):
            kind = "motion"
        elif any(w in masked for w in FF_TRANSITION_MARKERS):
            kind = "transition"
        elif any(w in masked for w in FF_SUSTAINED_TO_POSE):
            kind = "sustained"
        else:
            continue
        subject = next((s for s in names if s in clause), None)
        moments.append({"subject": subject, "clause": clause, "kind": kind})
    return moments


def detect_ff_multi_action(ff_desc: str, characters=None) -> list[dict]:
    """检测 ff_desc 是否含多动作/时间推进/过渡态/进行中动作堆叠.

    这是归一化是否动手的闸门：返回空列表 = 无问题，normalize 原样返回。
    v6.19：新增「pre_action」信号——单一 motion/sustained 时刻也触发归一化
    （要把动作改写成前一刻，不再要求多动作堆叠）。
    Returns:
        list[dict]: 问题描述列表，每项 {type, ...}；空 = 无问题
    """
    if not ff_desc:
        return []
    problems: list[dict] = []
    ta = [w for w in FF_TIME_ADVANCE_WORDS if w in ff_desc]
    if ta:
        problems.append({"type": "time_advance", "words": ta})
    moments = _collect_ff_action_moments(ff_desc, characters)
    if len(moments) >= 2:
        problems.append({
            "type": "multi_action",
            "count": len(moments),
            "clauses": [m["clause"] for m in moments],
        })
    for m in moments:
        if m["kind"] in ("motion", "sustained"):
            problems.append({"type": "pre_action", "clause": m["clause"]})
        elif m["kind"] == "transition":
            problems.append({"type": "transition", "clause": m["clause"]})
    return problems


def _strip_ff_time_advance(text: str) -> str:
    """删除时间推进连接词，并清理双标点."""
    for w in FF_TIME_ADVANCE_WORDS:
        text = text.replace(w, "")
    text = re.sub(r"[，、]{2,}", "，", text)
    text = re.sub(r"[；;]{1,}", "，", text)  # 统一分号为逗号（同帧元素并列）
    return text


def _split_ff_clauses(text: str) -> list[str]:
    """按标点分句."""
    return [c.strip() for c in re.split(r"[，,；;。！？]", text) if c.strip()]


def _drop_followup_motion(clauses: list[str], characters=None) -> list[str]:
    """动作链只留最早定格：保留第一个含进行态动词的分句，后续进行态分句
    去动词后若剩静态姿势残词则保留，否则丢弃（处理"破水而出→随即倒纵入水"）.
    角色名先遮蔽防 刺/杀 误匹配；「即将」分句豁免（逼近瞬间定格，可画）。
    """
    names = _subject_names(characters)
    kept_first_motion = False
    out: list[str] = []
    for c in clauses:
        if "即将" in c:
            out.append(c)
            continue
        if _ff_existing_state(c, names):
            out.append(c)
            continue
        masked = _mask_subjects(c, names)
        is_motion = any(v in masked for v in FF_MOTION_VERBS)
        if is_motion and not kept_first_motion:
            kept_first_motion = True
            out.append(c)
            continue
        if is_motion:
            stripped = c
            for v in FF_MOTION_VERBS:
                stripped = stripped.replace(v, "")
            has_posture = any(v in stripped for v in FF_STATIC_POSTURE_VERBS)
            # 保留含静态姿势的分句（如"杨旭仰头"），丢弃纯动作链分句
            if has_posture:
                out.append(c)
            continue
        out.append(c)
    return out


def _staticize_ff_transition(clause: str) -> str:
    """过渡态 → 静态姿势（欲起→半起身等）。「即将」不在表内，自动保留."""
    for old, new in FF_TRANSITION_REPLACE:
        if old in clause:
            return clause.replace(old, new)
    # 正则兜底：欲X → 半起身（不匹配"即将"）
    clause = re.sub(r"欲(起|站|动|扑|纵|跃)", "半起身", clause)
    return clause


def _staticize_ff_sustained(clause: str, multi: bool) -> str:
    """持续声音动词 → 静态姿势（尖叫→惊呼）。仅多动作时替换（单动作定格可保留原声）."""
    if not multi:
        return clause
    for word, (short, full) in FF_SUSTAINED_TO_POSE.items():
        if word in clause:
            # 分句已有 张嘴/张口/仰头 时只换短形式，避免"张嘴张嘴"
            if any(k in clause for k in ("张嘴", "张口", "仰头")):
                return clause.replace(word, short)
            return clause.replace(word, full)
    return clause


# ---------------------------------------------------------------------------
# 「前一刻」改写辅助（v6.19）
#
# 语义：ff_desc 必须定格在 motion 第一个动作的「前一刻」——破水前=潜伏水下、
# 水面微澜；刺向=刀尖抵近、即将触及；尖叫=张嘴欲呼。产物与 motion_desc 首段
# 动作彻底错开（平静→破水→没入），避免【视频起点】与【视频中段】动作重叠、
# 模型把动作做两遍。
# ---------------------------------------------------------------------------

def _motion_archetype(clause: str, masked: str, watery: bool) -> str:
    """动作分句的原型分类（决定关联分句怎么处理）."""
    if any(v in masked for v in ("破水", "出水", "冲出水面", "窜出水面", "跃出水面")) \
            or (watery and any(v in masked for v in ("冲出", "窜出", "跃出"))):
        return "water_break"
    if any(v in masked for v in ("倒纵", "没入", "入水", "落水", "潜入", "栽倒")):
        return "water_enter"
    if any(v in masked for v in ("纵身", "跃起", "腾空", "翻身")):
        return "jump"
    if any(v in masked for v in ("刺", "劈", "砍", "斩", "挥", "砸下")):
        return "strike"
    if any(v in masked for v in ("抓向", "抓夺", "抢夺", "夺")):
        return "grab"
    if any(v in masked for v in ("拔出", "抽出")):
        return "draw"
    return "rush"   # 疾冲/狂奔/疾驰/追杀/扑/撞/冲出(陆上)


def _detect_preaction_anchor(clauses: list[str], characters=None,
                             shot: dict | None = None) -> str | None:
    """首个待改写动作分句的原型（water_break/water_enter/jump/strike/...）.

    用 shot.location/scene 的水词提示区分陆上/水上动作，避免「冲出/窜出/跃出」
    在陆上语境被 water_break 误判。
    """
    names = _subject_names(characters)
    watery = bool(shot) and any(
        w in f"{shot.get('location', '')}{shot.get('scene', '')}"
        for w in ("湖", "河", "水", "塘", "湾", "荷", "潭", "江", "海")
    )
    for c in clauses:
        if "即将" in c:
            continue
        masked = _mask_subjects(c, names)
        if any(v in masked for v in FF_MOTION_VERBS) and _ff_actor_motion(c, names):
            return _motion_archetype(c, masked, watery)
        if any(w in masked for w in FF_TRANSITION_MARKERS):
            return "transition"
        if any(w in masked for w in FF_SUSTAINED_TO_POSE):
            return "sustained"
    return None


def _cleanup_preaction_particles(c: str) -> str:
    """剥离动作残留虚词（正在/正要/猛地/一弹 等），清理重复标点."""
    for w in FF_PREACTION_STRIP_WORDS:
        c = c.replace(w, "")
    # 独立的"正"（"正在"已被上面替换，此处处理"正刀尖抵近"这类残留）
    c = c.replace("正刀尖", "刀尖").replace("正伸手", "伸手").replace("正握", "握")
    c = re.sub(r"[，、]{2,}", "，", c)
    return c.strip("，、。 ")


def _preactionize_strike(clause: str, names: set) -> str:
    """单字攻击动词 → 刀将刺未刺（保留目标 NP，如「刀尖抵近杨旭心口，即将触及」）."""
    masked = _mask_subjects(clause, names)
    for verb in ("刺向", "刺入", "刺中", "刺下", "劈向", "砍向", "斩向", "挥向"):
        idx = masked.find(verb)
        if idx != -1:
            tail = clause[idx + len(verb):].lstrip("，、")
            target = re.split(r"[，。！？；]", tail)[0] or ""
            if verb.startswith("刺"):
                return _cleanup_preaction_particles(f"{clause[:idx]}刀尖抵近{target}，即将触及")
            if verb.startswith(("劈", "砍", "斩")):
                return _cleanup_preaction_particles(f"{clause[:idx]}刀高高扬起，即将劈向{target}")
            if verb.startswith("挥"):
                return _cleanup_preaction_particles(f"{clause[:idx]}手臂后引，即将挥向{target}")
    # 无目标兜底
    if any(v in masked for v in ("劈", "砍", "斩", "砸下")):
        return "刀高高扬起，即将落下"
    if "挥" in masked:
        return "手臂后引，即将挥出"
    return "刀尖抵近，即将触及"


def _preactionize_ff_motion(clause: str, names: set) -> str:
    """进行态动作分句 → 前一刻（长词短语替换优先，刺/劈/砍/挥走目标保留正则）."""
    if not _ff_actor_motion(clause, names):
        return clause
    if any(v in _mask_subjects(clause, names) for v in ("刺", "劈", "砍", "斩", "挥", "砸下")):
        return _preactionize_strike(clause, names)
    for old, new in FF_MOTION_TO_PREACTION:
        if old in clause:
            return _cleanup_preaction_particles(clause.replace(old, new))
    return clause


def _preactionize_ff_sustained(clause: str) -> str:
    """持续声音动词 → 前一刻（尖叫→欲呼，怒喝→欲喝；已含张嘴/仰头时只换尾词）."""
    for w in ("尖声尖叫", "发出尖叫", "尖叫", "惊叫"):
        if w in clause:
            return clause.replace(w, "欲呼")
    for w in ("怒喝", "厉喝"):
        if w in clause:
            return clause.replace(w, "欲喝")
    for w, repl in (("大喊", "欲喊"), ("大声喊叫", "欲喊"), ("呼救", "欲呼救"), ("放声大哭", "欲哭")):
        if w in clause:
            return clause.replace(w, repl)
    return clause


def _preactionize_ff_transition(clause: str) -> str:
    """过渡态 → 前一刻（正要仰头尖叫→仰头张嘴欲呼；正要纵身跃起→曲膝蓄势）."""
    c = clause
    for w in ("正准备", "正要", "刚要", "准备"):
        c = c.replace(w, "")
    # 反应词 → 前一刻（惊觉欲起→静坐未察；用户决策：破水前角色还没察觉）。
    # 先于 _staticize_ff_transition，否则"惊觉欲起"先被转成"惊觉、身体前倾"匹配不到。
    for old, new in FF_REACTION_TO_PREACTION:
        if old in c:
            c = c.replace(old, new)
    if any(w in c for w in ("尖叫", "惊叫", "怒喝", "厉喝", "大喊", "呼救")):
        return _preactionize_ff_sustained(c)
    if any(v in c for v in ("纵身", "跃起", "腾空")):
        return "曲膝蓄势"
    return _staticize_ff_transition(c)   # 其余沿用旧静态化（欲起→半起身等）


# 武器词交替组（water_break 分支共用：高举改写 + 未出水检测同源）
_FF_WEAPON_ALT = r"(?:匕首|弯刀|短刃|长刀|剑|刃)"
_FF_WEAPON_HELD_HIGH_RE = re.compile(r"高举的" + _FF_WEAPON_ALT)


def _preactionize_associated_clause(clause: str, anchor: str | None) -> str | None:
    """动作前一刻路径下处理静态分句：删动作后效果、改写武器出水/视线落点/角色反应.

    anchor 为 None（无动作原型）时原样返回。
    """
    if not anchor:
        return clause
    # 1. 角色反应 → 前一刻（破水前角色还没察觉）。先于其他改写（如"惊觉、身体前倾"）
    for old, new in FF_REACTION_TO_PREACTION:
        if old in clause:
            clause = clause.replace(old, new)
    # 2. 水面破开/水花/水珠 精确改写或删除
    for old, new in FF_PREACTION_CLAUSE_FIXES.items():
        if old in clause:
            return clause.replace(old, new) if new else None
    # 3. water_break：视线汇聚匕首 → 汇聚水面；武器出水分句 → 未出水
    if anchor == "water_break":
        if "视线" in clause and any(k in clause for k in ("匕首", "高举", "寒光")):
            # 「[主语]高举的+武器」→ 水面涟漪。v6.62 正则化：原 3 条字面量里
            # 「刺客高举的匕首」在合规折叠（刺客→神秘人）后永不命中（纯死条目），
            # 且武器清单缺 短刃/长刀/剑——与下方未出水检测共用 _FF_WEAPON_ALT。
            return _FF_WEAPON_HELD_HIGH_RE.sub("水面涟漪", clause)
        m = re.search(_FF_WEAPON_ALT, clause)
        if m and re.search(r"(高举|紧握|握).{0,4}" + _FF_WEAPON_ALT, clause):
            return f"{m.group(0)}未出水"
    # 4. strike：高举武器 → 握（贴近目标，避免「高举+抵近」构图矛盾）
    if anchor == "strike" and "高举" in clause \
            and any(w in clause for w in ("匕首", "刀", "剑", "刃")):
        return clause.replace("高举", "握")
    return clause


# 手持物+身体粘连病句：钓杆/鱼竿/伞/扇等手持物直接连在身体动作后，模型无法
# 判断归属（「左手钓杆垂身体后仰」→ 钓杆到底在左手还是身体？）。
# v6.62 正则化：原 5 条字面量按「左右手×物名」枚举，新物名/新主语就得加条；
# 规则收敛为两种粘连模式（垂身体后仰 / 垂身），主语（左/右手）可选捕获，
# 物名词表声明式维护。长模式在前。
# ⚠️ 替换词禁用「入水/落水/没入」等 FF_MOTION_VERBS——会二次命中前一刻改写产生病句。
_FF_GRAMMAR_HANDHELD_OBJ = r"(钓杆|鱼竿|伞|扇)"
_FF_GRAMMAR_HANDHELD_RULES = (
    (re.compile(r"((?:左|右)手)?" + _FF_GRAMMAR_HANDHELD_OBJ + r"垂身体后仰"),
     r"\1握\2，\2垂向水面"),
    (re.compile(r"((?:左|右)手)?" + _FF_GRAMMAR_HANDHELD_OBJ + r"垂身"),
     r"\1握\2，\2垂挂"),
)


def _fix_ff_grammar(ff_desc: str) -> str:
    """ff_desc 病句语法修复（不依赖动作检测闸门，幂等）.

    修复 LLM 输出的手持物+身体粘连病句（钓杆/鱼竿/伞/扇 + 垂身/后仰混写），
    让图片模型能正确理解「谁拿什么、身体什么姿势」。长模式规则在前。
    """
    if not ff_desc:
        return ff_desc
    out = ff_desc
    for pat, repl in _FF_GRAMMAR_HANDHELD_RULES:
        out = pat.sub(repl, out)
    return out


def _fix_ff_lurk_position(ff_desc: str) -> str:
    """给潜伏威胁（刺客/黑衣人潜伏水下）补画左站位硬锚，防「背对目标/朝左」.

    图片模型对「潜伏水下+目光望向画X」这种只锚视线不锚站位的描述，容易把
    威胁源画成朝镜头或朝左（背后长眼睛）。补上「潜伏于画左水下」让模型明确
    威胁源在画左、望向画右目标（规则41 威胁源-视线耦合在图片层的落地）。
    """
    if not ff_desc or not isinstance(ff_desc, str):
        return ff_desc
    # 仅当「潜伏」类威胁源描述里既没有站位锚、又有望向目标方向时补锚
    has_lurk = any(w in ff_desc for w in ("潜伏", "潜于", "藏身水中", "只露一双"))
    if not has_lurk:
        return ff_desc
    # 已有明确站位锚（潜伏于画X / 画X水下 / 画X侧）→ 跳过
    if any(a in ff_desc for a in ("潜伏于画", "画左水下", "画右水下", "画左侧", "画右侧", "画左方", "画右方")):
        return ff_desc
    # 望向画右目标 → 补「画左」站位；望向画左目标 → 补「画右」站位
    if "望向画右" in ff_desc or "望向画右上" in ff_desc or "盯向画右" in ff_desc:
        return ff_desc.replace("潜伏水下", "潜伏于画左水下", 1) \
                      .replace("潜于水下", "潜于画左水下", 1)
    if "望向画左" in ff_desc or "盯向画左" in ff_desc:
        return ff_desc.replace("潜伏水下", "潜伏于画右水下", 1) \
                      .replace("潜于水下", "潜于画右水下", 1)
    return ff_desc


def _fix_ff_weapon_visible(ff_desc: str, motion_desc: str) -> str:
    """Legacy semantic repair is intentionally disabled.

    Changing an explicitly hidden weapon into a visible blade tip fabricates a
    visual fact that the source description does not establish.  Keep this
    compatibility entry point as a no-op so older callers remain import-safe;
    validation reports the timeline conflict and the semantic repair is left to
    the LLM repair round.
    """
    return ff_desc


def normalize_ff_desc(ff_desc: str, shot: dict | None = None,
                      characters=None) -> str:
    """ff_desc 归一化：多动作/进行态堆叠 → 动作「前一刻」静态定格（幂等、保守）.

    无问题信号（detect_ff_multi_action 为空）时原样返回；只作用于 ff_desc，
    绝不作用于 lf_desc（动作结果态）/keyframes（进行中瞬间）。

    v6.19 语义：首个动作时刻改写为「前一刻」（破水→潜伏水下、水面微澜；
    刺向→刀尖抵近、即将触及；尖叫→欲呼），与 motion_desc 首段动作彻底错开。
    """
    if not ff_desc or not isinstance(ff_desc, str):
        return ff_desc
    # 病句语法修复先行（不依赖动作检测闸门）：「左手钓杆垂身体后仰」等手持物+身体
    # 粘连，模型理解不了钓杆归属。与「前一刻」改写无关，任何 ff_desc 都应先修语法。
    ff_desc = _fix_ff_grammar(ff_desc)
    # 潜伏威胁站位硬锚：刺客「潜伏水下+望向画X」缺站位 → 补「潜伏于画X水下」
    ff_desc = _fix_ff_lurk_position(ff_desc)
    if not detect_ff_multi_action(ff_desc, characters):
        return ff_desc
    text = _strip_ff_time_advance(ff_desc)
    clauses = _split_ff_clauses(text)
    names = _subject_names(characters)
    moments = _collect_ff_action_moments("，".join(clauses), characters)
    multi = len(moments) >= 2
    anchor = _detect_preaction_anchor(clauses, characters, shot)

    out: list[str] = []
    preaction_done = False
    for c in clauses:
        if "即将" in c:                    # 已是前一刻定格，原样保留
            out.append(c)
            continue
        masked = _mask_subjects(c, names)
        is_motion = any(v in masked for v in FF_MOTION_VERBS)
        is_transition = any(w in masked for w in FF_TRANSITION_MARKERS)
        is_sustained = any(w in masked for w in FF_SUSTAINED_TO_POSE)

        if _ff_existing_state(c, names) or (is_motion and not _ff_actor_motion(c, names)):
            out.append(c)
            continue
        if is_motion:
            if not preaction_done:
                out.append(_preactionize_ff_motion(c, names))
                preaction_done = True
            elif multi:
                out.extend(_drop_followup_motion([c], characters))
            continue
        if is_transition:
            if not preaction_done:
                out.append(_preactionize_ff_transition(c))
                preaction_done = True
            else:
                out.append(_preactionize_ff_transition(c))
            continue
        if is_sustained:
            out.append(_preactionize_ff_sustained(c))
            preaction_done = True
            continue
        fixed = _preactionize_associated_clause(c, anchor)
        if fixed is not None:
            out.append(fixed)
    result = "，".join(out).strip("，、。 ")
    # v6.51: anchor-aware 后处理——整句级姿态改写，
    # 解决"分句切在一起导致 associated_clause 不生效"的问题。
    if anchor == "water_break" and result:
        for old, new in _WB_POSE_TO_UNDERWATER:
            if old in result:
                result = result.replace(old, new)
        # 清理空残
        result = re.sub(r"[，、]+\s*[，、]+", "，", result)
        result = result.strip("，、。 ")
    return result


# ============================================================================
# lf_desc 终态归一化（与 ff 侧 normalize_ff_desc 对称，审计 S1 收敛）
# ============================================================================
# 尾帧（lf_desc）应是动作**彻底结束的终态**（刺客已没入、杨旭已倒下、水面已平静），
# 但 LLM 常输出中间态（正在刺/即将刺/半截刀尖露水面/缓缓没入中）。视频模型把 lf_desc
# 当【视频终点】定格，任何中间态都会被定格成诡异画面（08-09 钩子「匕首尖停水面」）。
# normalize_lf_desc 把中间态确定性改写为终态。
# 词表命名 LF_*（与 FF_* 区分），值**禁用** 正在/仍/缓缓/即将/正要/欲 及 没入/倒下/
# 沉入 等动词（幂等铁律：产物不命中任何触发词）。
LF_WEAPON_WORDS = ("刀尖", "匕首", "剑尖", "枪尖", "刀", "剑", "枪", "矛", "戟", "斧", "锏", "鞭", "钩", "镖")
LF_WATER_RESIDUE_VERBS = (
    "刺破水面", "露出水面", "露在水面", "探出水面", "挑破水面",
    "浮于水面", "悬于水面", "划过水面", "戳破水面", "掠在水面",
    "在水面划动", "于水面划动",
)
LF_RESIDUE_PREFIX = ("仅余", "只剩", "仅剩", "只余", "唯余", "留下", "余下", "半截", "一截")
LF_PROGRESSIVE_MARKERS = ("正在", "仍在", "还在", "正", "还")
LF_PROGRESSIVE_VERBS = (
    "挣扎", "扑腾", "沉入", "没入", "下沉", "刺", "劈", "砍", "斩", "捅",
    "跃", "冲", "窜", "跑", "坠", "倒下", "倒地", "滑落", "蠕动", "抽搐",
)
LF_IMMINENT_MARKERS = ("即将", "正要", "将要", "快要", "就要", "欲")
LF_IMMINENT_VERBS = (
    "刺入", "刺中", "刺向", "刺到", "捅入", "劈向", "砍向", "斩向",
    "扑向", "扑倒", "坠入", "砸向", "击中", "撞向", "贯入", "没入", "沉入",
)
LF_TRANSITION_MARKERS = ("缓缓", "慢慢", "逐渐", "渐渐", "徐徐", "一点一点")
LF_HALF_BODY_TRIGGERS = ("半身", "半个身子", "半截", "半个")
LF_HALF_BODY_VERBS = ("探出", "露出", "没入", "沉入", "浮出", "仰出")
LF_STRIP_PREFIXES = ("随即", "随后", "然后", "接着", "紧接着", "与此同时")

# 中间态 → 终态改写映射（长词优先替换；值不得命中任何触发词）
LF_TRANSITION_TO_DONE = (
    ("缓缓没入", "已没入"), ("慢慢没入", "已没入"), ("逐渐没入", "已没入"),
    ("徐徐没入", "已没入"), ("缓缓沉入", "已沉入"), ("慢慢沉入", "已沉入"),
    ("逐渐沉入", "已沉入"), ("缓缓倒下", "仰面倒下"), ("慢慢倒下", "仰面倒下"),
    ("逐渐倒下", "仰面倒下"), ("缓缓下沉", "已沉入水下"), ("慢慢下沉", "已沉入水下"),
)
LF_PROGRESSIVE_TO_DONE = (
    ("正在没入", "已没入"), ("仍在没入", "已没入"), ("正在沉入", "已沉入"),
    ("还在沉入", "已沉入"), ("正在下沉", "已沉入"), ("正没入", "已没入"),
    ("正在倒下", "仰面倒下"), ("仍在倒下", "仰面倒下"),
    ("正在挣扎", "已不再挣扎"), ("仍在挣扎", "已不再挣扎"), ("还在挣扎", "已不再挣扎"),
    ("还在抽搐", "已不再抽搐"), ("正在抽搐", "已不再抽搐"),
)
LF_IMMINENT_TO_DONE = (
    ("即将刺入", "已刺入"), ("正要刺入", "已刺入"), ("即将刺中", "已刺中"),
    ("即将刺向", "已刺向"), ("正要刺向", "已刺向"), ("正要扑向", "已扑向"),
    ("即将扑向", "已扑向"), ("即将坠入", "已坠入"), ("正要坠入", "已坠入"),
)


def detect_lf_midstate(lf_desc: str, characters=None) -> list[dict]:
    """检测 lf_desc 是否含「动作未彻底结束」的中间态表达.

    这是 normalize_lf_desc 是否动手的闸门：返回空列表 = 无问题，原样返回。
    中间态类别（每一类是一条问题记录）：
      residue     残留态：仅余一截刀尖刺破水面
      half_body   半身态：半身探出水面
      transition  过渡态：缓缓没入中
      imminent    即将态：即将刺入
      progressive 进行态：正在没入
    保守设计：无标记裸动词（挣扎）不触发（防误伤静态终态）；「只剩」前缀单独
    出现不触发（残留态要求「武器词 + 水面残留动词」同时命中）。
    """
    if not lf_desc or not isinstance(lf_desc, str):
        return []
    problems: list[dict] = []
    names = _subject_names(characters)
    for clause in _split_ff_clauses(lf_desc):
        masked = _mask_subjects(clause, names)
        # 1. 残留态：剥前缀后 武器+水面残留动词
        stripped = clause
        for p in LF_RESIDUE_PREFIX:
            stripped = stripped.replace(p, "")
        if any(w in stripped for w in LF_WEAPON_WORDS) \
                and any(v in stripped for v in LF_WATER_RESIDUE_VERBS):
            problems.append({"type": "residue", "clause": clause})
        # 2. 半身态
        if any(t in masked for t in LF_HALF_BODY_TRIGGERS) \
                and any(v in masked for v in LF_HALF_BODY_VERBS):
            problems.append({"type": "half_body", "clause": clause})
        # 3. 过渡态
        if any(t in masked for t in LF_TRANSITION_MARKERS) \
                and any(v in masked for v in LF_PROGRESSIVE_VERBS):
            problems.append({"type": "transition", "clause": clause})
        # 4. 即将态
        if any(m in masked for m in LF_IMMINENT_MARKERS) \
                and any(v in masked for v in LF_IMMINENT_VERBS):
            problems.append({"type": "imminent", "clause": clause})
        # 5. 进行态（标记+动词成对）
        if any(m in masked for m in LF_PROGRESSIVE_MARKERS) \
                and any(v in masked for v in LF_PROGRESSIVE_VERBS):
            problems.append({"type": "progressive", "clause": clause})
    return problems


def _finalize_lf_clause(clause: str, names: set) -> str:
    """逐分句终态化（优先级：残留 → 过渡 → 进行 → 即将 → 半身，命中一条即返回）."""
    masked = _mask_subjects(clause, names)

    # 1. 残留态：吞前缀，武器残留动词 → 武器已没入水下（保留武器名，不吞词）
    stripped = clause
    for p in LF_RESIDUE_PREFIX:
        stripped = stripped.replace(p, "")
    if any(w in stripped for w in LF_WEAPON_WORDS) \
            and any(v in stripped for v in LF_WATER_RESIDUE_VERBS):
        weapon = next(w for w in LF_WEAPON_WORDS if w in stripped)
        return f"{weapon}已没入水下"

    # 2. 过渡态短语替换（长词优先）
    for old, new in LF_TRANSITION_TO_DONE:
        if old in clause:
            return clause.replace(old, new)

    # 3. 进行态短语替换
    for old, new in LF_PROGRESSIVE_TO_DONE:
        if old in clause:
            return clause.replace(old, new)

    # 4. 即将态短语替换
    for old, new in LF_IMMINENT_TO_DONE:
        if old in clause:
            return clause.replace(old, new)

    # 5. 半身态：半身探出/没入 → 已没入水下（保留主语，不删整句——
    #    否则「刺客半身探出水面」→ 空字符串，主语丢失）
    if any(t in masked for t in LF_HALF_BODY_TRIGGERS) \
            and any(v in masked for v in LF_HALF_BODY_VERBS):
        # 剥离半身/探出/露出 等中间态词，保留主语 + 改写为已没入
        subj = ""
        for name in sorted(names, key=len, reverse=True):
            if name in clause:
                subj = name
                break
        for t in LF_HALF_BODY_TRIGGERS:
            clause = clause.replace(t, "")
        return f"{subj}已没入水下".strip() if subj else "已没入水下"

    return clause


def normalize_lf_desc(lf_desc: str, shot: dict | None = None,
                      characters=None) -> str:
    """Return ``lf_desc`` unchanged.

    Rewriting "about to stab" into "already stabbed" (or "falling" into
    "fallen") invents the action result.  ``detect_lf_midstate`` remains the
    audit primitive; callers must report the conflict and let the LLM repair
    round rewrite the frame from the original story evidence.
    """
    return lf_desc


# 朝向强化触发词（与 vimax_bridge._collect_portrait_refs_for_shot 的 subject_view
# 判定同源——参考图三视图是伪三视图，朝向语义改由 prompt 文字驱动）
ORIENTATION_KW = (
    "面向画", "朝向画", "盯向画", "望向画", "朝画右", "朝画左",
    "面向右", "面向左", "看画右", "看画左", "转向画",
)


def extract_orientation(desc: str, characters=None) -> list[tuple[str, str]]:
    """从描述提取「动作主体 + 朝向目标角色」朝向对.

    只对明确写了「角色 + 盯向/面向/望向/朝向 + 画X + 目标角色」的主体生效。
    如「刺客潜伏水下，正盯向画右的杨旭」→ (刺客, 杨旭)——刺客面向杨旭。
    目标须是已知角色名（杨旭/听香等）；望向水面/望向消失处 → 目标非角色，跳过。

    保守闸门：未指定朝向主体、目标非角色、空镜 → 返回 []（不强加）。
    """
    if not desc:
        return []
    names = _subject_names(characters)  # None 时返回兜底称谓
    result: list[tuple[str, str]] = []
    seen = set()
    # 朝向触发词：角色 + 动词 + 画X + 目标
    for ident in sorted(names, key=len, reverse=True):
        if not ident:
            continue
        idx = 0
        while True:
            idx = desc.find(ident, idx)
            if idx < 0:
                break
            # 角色名后 40 字窗口（覆盖「潜伏水下...正盯向画右的杨旭」）
            window = desc[idx:idx + 40]
            for kw in ORIENTATION_KW:
                kpos = window.find(kw)
                if kpos < 0:
                    continue
                # 主体须紧邻朝向动词：角色名与触发词之间不能隔着另一角色名
                # （如"杨旭，听香望向画右"→杨旭是宾语，听香才是主体）
                between = window[len(ident):kpos]
                if any(n in between for n in names if n != ident):
                    continue
                # 从触发词后找目标角色：仅认「画X + 的 + 角色名」紧密模式
                # （如"盯向画右的杨旭"→目标杨旭），不认"逗号+角色名"（避免
                # 宾语位的角色名被误当目标，如"画右的刺客"里刺客是宾语）。
                import re as _re
                tail = window[kpos:]
                m = _re.search(r"画[左右]的([一-鿿]{1,4})", tail)
                target = m.group(1) if m else None
                if target and target in names and target != ident:
                    key = (ident, target)
                    if key not in seen:
                        seen.add(key)
                        result.append((ident, target))
                    break
            idx += len(ident)
    return result


def add_image_strict_constraints(prompt: str, frame_type: str = "首帧",
                                 shot: dict | None = None,
                                 characters: list | None = None) -> str:
    """为图片生成添加严格约束，防止 AI 幻觉生成错误画面。

    frame_type: "首帧"、"尾帧" 或 "关键帧"
    shot: 镜头数据（可选）。传入时根据镜头实际出现的角色动态生成约束，
          避免向无刺客的镜头注入刺客描述导致图片模型脑补多余人物。
    characters: 角色表（可选）。M6 修复 2026-08-14：约束原先按第一章硬编码
          角色名（杨旭/听香/张十三/老汉）生成，换章/换书会注入错误约束。
          现改为从 characters + 镜头文本动态推导：性别约束取角色属性，
          出场名单取镜头文本中实际出现的角色名。
    """
    strict_constraints = (
        "【强制】你必须严格参照以下描述的具体细节来生成画面。"
        "禁止AI自由发挥。"
        "画面中只允许出现描述中明确提到的角色，禁止凭空添加任何额外人物。"
        # v6.50（08-29）参考图样式污染防护：15:42 钩子首帧实测——白底道具参考图
        # 的版式混入主画面（下 1/3 变成白底产品图）。参考图只借"内容"不借"版式"。
        "画面必须是完整连贯的单幅场景环境，"
        "禁止纯白背景/影棚布光/产品图版式，禁止输出上下拼接或分屏的合成画面；"
        "参考图仅用于人物长相、服装与道具外观参考，参考图的背景样式禁止混入画面。"
    )
    if frame_type == "首帧":
        strict_constraints += (
            "【首帧】这是动作开始前的静态构图画面，展示动作发生前的瞬间静态布局。"
        )
    elif frame_type == "尾帧":
        strict_constraints += (
            "【尾帧】这是动作完成后的结果画面，展示动作最终状态。"
        )
    elif frame_type == "关键帧":
        strict_constraints += (
            "【关键帧】这是动作进行中的画面，展示动作过程中的一个瞬间状态。"
        )

    # 地点一致性锚定：把镜头的 location 注入画面，防止图片模型自由发挥地点，
    # 导致同一场景在不同镜头/不同帧之间环境不一致（与视频 prompt 的
    # 「场景一致性约束」同源，保证图生视频前导帧与视频场景统一）。
    if shot is not None:
        location = shot.get("location", "") or shot.get("scene", "")
        if location:
            strict_constraints += (
                f"【场景地点】本画面发生地点必须是{location}。"
                f"严格遵守该地点特征，禁止画成其他场景（如河流/江面/室内/街道）。"
            )

    # 根据镜头实际内容动态生成角色约束（不再硬编码刺客/第一章角色名）
    if shot is not None:
        # 收集本镜头所有文本字段，判断哪些角色实际出现
        shot_text = " ".join([
            shot.get("main_subject", ""),
            shot.get("ff_desc", ""),
            shot.get("lf_desc", ""),
            shot.get("motion_desc", "") or shot.get("prompt", ""),
        ])
        # 威胁源角色（刺客一类的通称），与 _subject_names 兜底一致
        _THREAT_NAMES = ("刺客", "杀手", "黑衣人", "蒙面人", "神秘人")
        has_threat = any(n in shot_text for n in _THREAT_NAMES)

        # 08-16 hook ff 刺客回头盯镜头根因：威胁源在该帧**自己的**描述里若仅
        # 以非实体方式出现（水下黑影/刀尖微露/没入水中），画面里没有完整刺客
        # → 人物数量/武器归属/「禁止黑影」都按此调整。延迟导入避免和
        # vimax_bridge（已被其模块级导入 style_prefix）形成循环依赖。
        if frame_type == "首帧":
            _threat_desc = shot.get("ff_desc", "") or ""
        elif frame_type == "尾帧":
            _threat_desc = shot.get("lf_desc", "") or ""
        else:
            _threat_desc = " ".join(filter(None, [
                shot.get("ff_desc", ""), shot.get("lf_desc", ""),
                shot.get("motion_desc", "") or shot.get("prompt", ""),
            ]))
        from shared.vimax_bridge import _threat_visible_in
        _threat_vis = _threat_visible_in(_threat_desc, characters)
        _threat_onscreen = _threat_vis is True
        _threat_invisible_only = has_threat and _threat_vis is False

        # 本镜实际出场的注册角色（按 identifier 在镜头文本中匹配）
        present_chars = []
        for c in (characters or []):
            ident = (c.get("identifier") or "").strip()
            if ident and ident in shot_text:
                present_chars.append(c)

        if has_threat:
            # 武器归属：武器只能由威胁源持有；其余出场角色不得持武器/蒙面。
            # 08-16：威胁源自己持有武器，不应被列入"无武器"——否则同段既写
            # "武器只能由刺客持有"又写"刺客手中没有武器"自相矛盾。
            others = [c["identifier"] for c in present_chars
                      if c["identifier"] not in _THREAT_NAMES]
            _no_weapon = "".join(f"{n}手中没有武器，没有刀，没有剑，没有短刃。" for n in others)
            _not_threat = "".join(f"{n}不能蒙面，{n}不是刺客。" for n in others)
            strict_constraints += (
                "【武器归属】武器（短刃、刀、剑）只能由刺客持有。"
                + _no_weapon
                + f"【人物】{_not_threat}刺客是蒙面的。刺客是成年男性。"
                "禁止两个人拿武器。只有刺客一个人拿武器。"
            )

        # 非战斗镜头禁武器（08-15 hook 尾帧被图片模型塞匕首）：镜头从未提及任何
        # 武器且无威胁源时，画面禁止出现任何武器。检测同时看原始 shot 文本与
        # 预处理后的最终 prompt——「戟指」已被替换，避免「戟」误判；若描述本身
        # 写了武器（如"杨旭持剑"）则跳过，该镜允许武器、由正向约束管理。
        if not has_threat and not any(w in shot_text for w in WEAPON_WORDS) \
                and not any(w in prompt for w in WEAPON_WORDS):
            strict_constraints += (
                "【禁止武器】本镜头无任何武器，画面中禁止出现武器、刀具、匕首、刀、剑、"
                "短刃、暗器、凶器；所有角色徒手空拳，手中禁止握持任何物品。"
            )

        # 性别硬约束（防角色性别错乱）：从角色属性派生，非硬编码人名
        for c in present_chars:
            ident = c["identifier"]
            gender = (c.get("gender") or "").strip().lower()
            if gender in ("female", "女", "女子"):
                age_note = (c.get("age_range") or "").strip() or "年轻"
                strict_constraints += (
                    f"【性别】{ident}是{age_note}女子，面容符合设定；"
                    "必须画成女性，禁止男性化/中性化。"
                )
            elif gender in ("male", "男", "男子"):
                age_note = (c.get("age_range") or "").strip() or "青年"
                strict_constraints += (
                    f"【性别】{ident}是{age_note}男子；必须画成男性。"
                )

        # 08-16：人数名单只列**该帧可见**角色。威胁源在本帧若仅以非实体方式
        # 出现（水下黑影/刀尖/没入水中）→ 画面没有完整刺客，不列入（否则
        # hook ff「总共4人」会逼图片模型画出完整刺客）。同时去重——has_threat
        # 追加"刺客"曾与 present_chars 里已匹配的刺客重复，出现"杨旭、听香、
        # 刺客、刺客，总共4人"的严重幻觉源。
        _count_chars = [c for c in present_chars
                        if c["identifier"] not in _THREAT_NAMES or _threat_onscreen]
        char_names = list(dict.fromkeys(c["identifier"] for c in _count_chars))
        if has_threat and _threat_onscreen and "刺客" not in char_names:
            char_names.append("刺客")

        if char_names:
            # 威胁源以水下黑影/刀尖呈现时，黑影是剧情要素，不在禁用之列——
            # 否则"禁止…黑影"与 ff_desc「水下黑影轮廓」直接打架，模型被搞乱。
            # v6.50：不可见威胁时补正向锚（黑影保持暗影轮廓不画成人形），
            # 消除「禁人影 vs 画黑影」的模型侧歧义。
            # v6.52：两分支各自点名真实漏网形态——不可见分支防"第二个黑影/
            # 第二个角色"；可见分支防"复用已列角色"（hook ff 第 4 蒙面人 =
            # 蒙面人即别名刺客被复制成第二个分身，实测）。
            if _threat_invisible_only:
                # 2026-08-29：黑影自触崩溃修复——原措辞「禁止…第二个黑影、第二个{名单}」
                # 命中 shared.prompt_contradiction 的 `禁止[^\n。]{0,24}?(黑影|人影)`
                # 正则（24 字内含「黑影」），与本应保留的剧情威胁伏笔「水下黑影轮廓」自相矛盾。
                # 现把「黑影」从「禁止…」从句中移出，改成正向陈述句，
                # 既保持防「第二个黑影」语义，又脱离正则匹配窗口。
                # 保留「不计入实体人数」语义（威胁源以黑影呈现，不计入实体人数）。
                _banned_tail = (
                    f"仅实体可见角色：威胁源以水下黑影呈现，不计入实体人数。"
                    f"黑影只允许出现一个，作为剧情威胁伏笔仅以水下暗影轮廓呈现；"
                    f"禁止出现任何其他人物或第二名{'、'.join(char_names)}角色。"
                    f"禁止复制/复用画面已列角色作为新人物（如把同一角色画两次、"
                    f"给蒙面人画第二个分身），每个角色在画面中只出现一次。"
                )
            else:
                # v6.69：帧描述自身把黑影当剧情要素（水下黑影伏笔等，如 shot4
                # lf「水面下黑影更清晰」）时，默认禁令不得含「黑影」——否则与
                # 帧描述对称矛盾，prompt_contradiction fail-fast 拦下整镜帧图。
                # 禁「人影」保留：暗影轮廓仍不得画成人形（与 v6.50 精化分支
                # 「正向陈述黑影」同一原则）。
                _shadow_in_frame = "黑影" in _threat_desc
                _extra_ban = "" if _shadow_in_frame else "、黑影"
                _banned_tail = (
                    f"禁止出现任何其他人物{_extra_ban}、人影。"
                    "禁止复制/复用画面已列角色作为新人物（如把同一角色画两次、"
                    "给蒙面人画第二个分身），每个角色在画面中只出现一次。"
                )
            strict_constraints += (
                f"【人物数量限制】画面中只允许出现以下角色：{'、'.join(char_names)}。"
                f"总共{len(char_names)}人，{_banned_tail}"
            )

        # v6.52 防线2：char_layout 结构化直推人数硬锚——present_chars 靠文本
        # 匹配推导，威胁源别名（刺客→神秘人）漏配时名单缺人、人数锚失真
        # （hook ff 实测画 4 人）。char_layout ≥3 条（含威胁源）时把名单写成
        # 逐个清点的硬锚。
        _cl_items = shot.get("char_layout") or []
        _cl_names = [str(it.get("name", "")).strip() for it in _cl_items
                     if isinstance(it, dict) and str(it.get("name", "")).strip()]
        if len(_cl_items) >= 3 and char_names:
            strict_constraints += (
                f"【画面人数硬锚】本画面共 {len(char_names)} 个角色：{'、'.join(char_names)}。"
                "请在心中逐个清点画面人物，多于上述名单即错误。"
            )

        # v6.20：帧朝向强化——参考图三视图是伪三视图（side/back 0.95+ 相似度，
        # 图生图没执行 Facing left），朝向语义改由 prompt 文字驱动。只对明确写了
        # 「角色 + 盯向/面向 + 画X + 目标角色」的主体追加朝向，未指定不强加。
        _orient_text = " ".join([
            shot.get("ff_desc", ""),
            shot.get("lf_desc", ""),
            shot.get("prompt", "") or shot.get("motion_desc", ""),
        ])
        _orient_pairs = extract_orientation(_orient_text)
        if _orient_pairs:
            _parts = "，".join(
                f"{c}面朝{d}方向，侧身不看向镜头" for c, d in _orient_pairs
            )
            strict_constraints += f"【朝向】{_parts}。"

        # 08-23 景别量化（用户反馈"应中景却画成远景"）：描述写"中景/中近景"却又
        # 写"全身入画"时，图片模型为塞下全身把镜头拉远成远景。中景 = 人物腰腹以上
        # 入画、人物占画面高度约 2/3——追加量化约束覆盖「全身入画」的拉远倾向。
        _frame_desc = (shot.get("ff_desc", "") or "") + " " + (shot.get("lf_desc", "") or "")
        if any(sz in _frame_desc for sz in ("中景", "中近景")) and "全身入画" in _frame_desc:
            strict_constraints += (
                "【景别】本镜头为【中景】：人物腰部以上、半身入画，人物高度约占画面"
                "高度的三分之二，主体足够大、清晰可辨。禁止把人物缩成远景小人，"
                "禁止全身入画拉远景别。"
            )

        # 首帧禁血/禁动作结果（08-09 首帧杨旭胸口血迹、刺客血眼）：首帧是动作
        # 「前一刻」（破水前/未刺中），画面必须干净无血。血污/伤口只允许出现在尾帧。
        if frame_type == "首帧" and has_threat:
            _victim_note = "。".join(f"{c['identifier']}胸口、身上必须干净无伤" for c in present_chars)
            strict_constraints += (
                "【首帧禁血】这是动作开始前的画面，刺客尚未出水、未刺中任何人。"
                "所有人物身上禁止血迹、血污、血点、洇痕；眼睛禁止充血/泛红/血丝。"
                f"{_victim_note}。"
            )
    else:
        # 无 shot 上下文时，只添加通用约束（不注入具体角色描述）
        strict_constraints += (
            "【人物】画面中只出现描述中提到的角色，禁止凭空添加额外人物。"
        )

    return prompt + "\n\n" + strict_constraints
def mentions_person(text: str) -> bool:
    """文本是否提到泛人物（角色库之外的路人/群像）.

    v6.62.1（09-06）：否定短语豁免——「空无一人/无一人/无人/不见人影/无人物」
    等词内部的「人」是**否定语境**（声明画面没人），按子串命中会把纯空镜
    误判为有人（09-06 chapter_001 镜头 1/3/7 每轮误报 6 条「空镜含人影/人」）。
    判定顺序：先剥掉否定短语再扫 PERSON_HINTS——剥完命中的「人」才是真有人。
    """
    PERSON_HINTS = (
        "人", "人们", "人群", "路人", "群众", "村民", "农夫",
        "少年", "少女", "小孩", "孩子", "娃娃", "婴儿",
        "老人", "老者", "老妇", "老丈", "老农",
        "公子", "小姐", "姑娘", "女眷", "丫鬟", "仆人", "下人",
        "男子", "女子", "男人", "女人", "汉子", "壮汉",
        "士兵", "官差", "衙役", "捕快", "官兵",
        "商贩", "摊贩", "小贩", "商人", "店主", "掌柜",
        "书生", "秀才", "举人", "道士", "和尚", "僧人",
        "车夫", "轿夫", "船夫", "渔夫", "樵夫",
        "背影", "身影", "人影", "黑影", "身形",
    )
    # 否定短语（长词优先）：命中即从扫描文本中剥除，剩下的「人」才算有人
    _NEGATIONS = (
        "空无一人", "不见人影", "无人物", "没有人物", "没有人", "画面无人",
        "不见人", "无一人", "无人烟", "无人", "杳无人迹", "人迹罕至",
        # 船/车容量标注：「可容2-3人/可容3人」的「人」是座位数不是画面人物
        # （09-06 shot3 实测 ff_desc「无篷舴艋舟（宽1.4米，可容3人）…空无一人」
        # 仍被误判有人）
        "可容2-3人", "可容3人", "可容4人", "可容5人", "可容2人", "可容1人",
        # 风格前缀：STYLE_PREFIX 固定头部「古风真人影视剧」——「真人」是风格词
        # （live-action），子串「人影」曾把纯空镜误判有人（09-06 shot1/3/7）
        "古风真人影视剧", "古风真人竖屏短视频", "真人影视剧", "真人影视",
    )
    stripped = text
    for neg in _NEGATIONS:
        stripped = stripped.replace(neg, "")
    return any(k in stripped for k in PERSON_HINTS)


# 合规替换威胁源别名（B4 修复 08-11）：content_safety 会把「刺客→神秘人」
# 等威胁源替换词写进文本，但注册角色名仍是「刺客」。空镜判定（is_empty_scene）
# 此前只靠 `mentions_person` 的单字「人」意外命中「神秘人」——依赖子串巧合，
# 一旦别名不含「人」就误判空镜、人物渗入。这里把威胁源别名显式列出，
# 让 has_character 按别名匹配（与 shared/vimax_bridge._char_aliases 同源逻辑）。
THREAT_ALIASES = ("神秘人", "黑衣人", "蒙面人", "黑影", "杀手", "刺客", "偷袭者")


def is_empty_scene(shot: dict, characters: list[dict]) -> bool:
    """判断镜头是否为空镜（画面中不应出现任何人物）.

    依据：main_subject 不是角色、画面描述（motion_desc/ff_desc）未提到
    角色名、也无泛人物字眼。注意不检查 context_bridge —— 它描述的是
    上一镜头的画面，常含上一镜头的角色名，会造成误判。

    B4 修复：角色匹配含威胁源别名——合规替换把「刺客→神秘人」后文本用
    「神秘人」，但注册角色名是「刺客」；仅 ident in search_text 会漏配
    （此前靠单字「人」意外命中），导致带威胁源的空镜镜头被误判为空镜、
    人物渗入（威胁源肖像漏注入）。现按别名显式匹配。
    """
    main_subject = shot.get("main_subject", "")
    motion_desc = shot.get("motion_desc", "").strip() or shot.get("prompt", "")
    ff_desc = shot.get("ff_desc", "")
    search_text = f"{main_subject} {motion_desc} {ff_desc}"

    has_character = False
    for c in (characters or []):
        ident = c.get("identifier") or ""
        if not ident:
            continue
        # 精确名 + 威胁源别名（合规替换后的称谓）都算"画面出现角色"
        if ident in search_text:
            has_character = True
            break
        if ident in THREAT_ALIASES and any(a in search_text for a in THREAT_ALIASES):
            has_character = True
            break
    return not has_character and not mentions_person(search_text)


# ============================================================================
# 五、敏感词校验（v6 新增）
# ============================================================================

# 高危敏感词列表（必须完全禁止出现），M6 收敛 2026-08-14：单一来源
# 为 shared.content_safety.FORBIDDEN_WORDS（原 _SENSITIVE_FORBIDDEN 本地表删除）。
_FORBIDDEN = FORBIDDEN_WORDS


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

            for forbidden in _FORBIDDEN:
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
