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
    "光影随场景自然变化，浅景深缓慢推镜，写实古风风格，高清1080P，"
    "轻微电影胶片颗粒，真实镜头微反差，轻微自然暗角"
)

# 精简版前缀（用于减少 token 占用）
STYLE_PREFIX_SHORT = "古风真人"

NEGATIVE_PROMPT = "动漫风格、动画、2D渲染、卡通脸、卡通人物、三头身、Q版、动漫脸、画风突变、畸形五官、异形船只、四叶草、现代服装、现代建筑、现代楼房、水泥建筑、玻璃幕墙、电线杆、汽车、摩托车、现代设施、3D渲染、塑料质感、English text, watermark, logo, English speech"

# ===============================================================================
# 图片幻觉专项防护（2025-07-23 v1 渐进增强）
# ===============================================================================

# 图片防幻觉锁图正向关键词（注入 prompt）
IMAGE_HALLUCINATION_LOCK_PROMPT = (
    "identity locked, same face, consistent facial structure, fixed costume, "
    "no extra objects, consistent scene layout, "
    "面部锁定，統一五官，固定服裝，無多餘雜物，場景布局不變"
)

# 图片防幻觉锁图 - 尾帧/关键帧版（解除"场景布局不变"锁）
# 尾帧/关键帧需表现动作演变与构图变化，若沿用带"布局不变"的完整锁图，
# 再叠加首帧整图参考，构图会被锁死在首帧上，三帧雷同、动作无法推进。
# 保留五官/服装/杂物锁定，去掉 "consistent scene layout / 場景布局不變"。
IMAGE_HALLUCINATION_LOCK_PROMPT_NO_LAYOUT = (
    "identity locked, same face, consistent facial structure, fixed costume, "
    "no extra objects, "
    "面部锁定，統一五官，固定服裝，無多餘雜物"
)

# 图片防幻觉追加负向词（追加到现有负向词）
IMAGE_HALLUCINATION_EXTRA_NEGATIVE = (
    "換臉、五官變形、臉型改變、五官錯位、多指、畸形手腳、扭曲肢體、"
    "漂浮物體、憑空多出家具、多餘擺件、亂加裝飾、服裝花紋突變、"
    "發色改變、妝容變化、畫面文字、水印、塗鴉、場景結構錯亂、"
    "人物大小忽大忽小、多餘人物、模糊五官"
)

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
    "杀": "击", "刺杀": "掠杀", "死": "倒", "毙命": "倒地",
    # 尸体/死亡相关
    "尸体": "身影", "死亡": "倒下", "毙命": "倒地", "尸骸": "遗影",
    # 面色描述（防止图片模型按字面颜色渲染皮肤）
    "脸色铁青": "面色凝重", "铁青": "凝重", "脸色发青": "面色紧绷",
    "面如死灰": "面色苍白", "脸色惨白": "面色苍白",
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
    "刺伤": "负伤", "伤口": "身形一晃", "伤口渗血": "衣襟湿渍",
    "皮肉破损": "衣衫碎裂", "砍断手臂": "剑影交错", "砍伤": "剑光过",
    "刺入": "中剑", "桶入": "中剑", "扎入": "中剑",
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


def sanitize_prompt(prompt: str, verbose: bool = False) -> str:
    """替换可能触发内容审核的敏感词."""
    out = prompt
    for old, new in _SENSITIVE_SUBSTITUTIONS.items():
        if old in out:
            out = out.replace(old, new)
            if verbose:
                try:
                    from shared.tee_logger import get_logger
                    from shared.paths import chapter_dir
                    get_logger(chapter_dir() / "step02.log").verbose(
                        f"  [合规] prompt 已替换敏感词: '{old}' -> '{new}'"
                    )
                except Exception:
                    pass
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
# 图片生成严格约束（防止 AI 幻觉，2026-07-26）
# ============================================================================

def preprocess_prompt_for_image(prompt: str) -> str:
    """图片生成前预处理：替换 AI 可能误解的词汇。"""
    replacements = {
        "掠客": "刺客",
        "一道人影": "刺客",
        "一道身影": "杀手",
        "蒙面刺客": "刺客",
        "蒙面黑衣人": "黑衣人",
    }
    result = prompt
    for old, new in replacements.items():
        result = result.replace(old, new)
    return result


def add_image_strict_constraints(prompt: str, frame_type: str = "首帧",
                                 shot: dict | None = None) -> str:
    """为图片生成添加严格约束，防止 AI 幻觉生成错误画面。

    frame_type: "首帧"、"尾帧" 或 "关键帧"
    shot: 镜头数据（可选）。传入时根据镜头实际出现的角色动态生成约束，
          避免向无刺客的镜头注入刺客描述导致图片模型脑补多余人物。
    """
    strict_constraints = (
        "【强制】你必须严格参照以下描述的具体细节来生成画面。"
        "禁止AI自由发挥。"
        "画面中只允许出现描述中明确提到的角色，禁止凭空添加任何额外人物。"
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

    # 根据镜头实际内容动态生成角色约束（不再硬编码刺客）
    if shot is not None:
        # 收集本镜头所有文本字段，判断哪些角色实际出现
        shot_text = " ".join([
            shot.get("main_subject", ""),
            shot.get("ff_desc", ""),
            shot.get("lf_desc", ""),
            shot.get("motion_desc", "") or shot.get("prompt", ""),
        ])
        has_assassin = "刺客" in shot_text
        has_yangxu = "杨旭" in shot_text
        has_tingxiang = "听香" in shot_text

        if has_assassin:
            strict_constraints += (
                "【武器归属】武器（短刃、刀、剑）只能由刺客持有。"
                "杨旭手中没有武器，没有刀，没有剑，没有短刃。"
                "杨旭是被刺伤的人，不是拿武器的人。"
                "听香手中也没有武器。"
                "【人物】杨旭不能蒙面，杨旭不是刺客。"
                "刺客是蒙面的。刺客是成年男性。"
                "禁止两个人拿武器。只有刺客一个人拿武器。"
            )

        # 统计本镜头应出现的人物数量，显式限制
        char_names = []
        if has_yangxu:
            char_names.append("杨旭")
        if has_tingxiang:
            char_names.append("听香")
        if has_assassin:
            char_names.append("刺客")
        # 其他角色：从 main_subject 等字段中检测
        for name in ["张十三", "老汉"]:
            if name in shot_text:
                char_names.append(name)

        if char_names:
            strict_constraints += (
                f"【人物数量限制】画面中只允许出现以下角色：{'、'.join(char_names)}。"
                f"总共{len(char_names)}人，禁止出现任何其他人物、黑影、人影。"
            )
    else:
        # 无 shot 上下文时，只添加通用约束（不注入具体角色描述）
        strict_constraints += (
            "【人物】画面中只出现描述中提到的角色，禁止凭空添加额外人物。"
        )

    return prompt + "\n\n" + strict_constraints
def mentions_person(text: str) -> bool:
    """文本是否提到泛人物（角色库之外的路人/群像）."""
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
        "背影", "身影", "人影", "黑影", "身形", "轮廓",
    )
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
    # 裸露性描写（注意："赤膊"是正常词汇，指光膀子的小说描写，不列入敏感词）
    "裸露", "赤身", "光腚",
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
