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
    "国风动漫影视剧，9:16竖屏，电影柔光，暗调低饱和，"
    "烛火局部光影，浅景深缓慢向前推镜，精美2D动画风格，无真人写实，高清1080P"
)

NEGATIVE_PROMPT = "真人写实、写实风格、畸形五官、明亮强光、现代服装、3D渲染、塑料质感、明亮阳光"

# ===========================================================================
# 二、全局渲染配置（输出 JSON 顶层字段来源）
# ===========================================================================

_GLOBAL_RENDER_DEFAULTS = {
    "resolution": "1080*1920",
    "ratio": "9:16",
    "fps": 24,
    "render_style": "2D国风动画",
    "global_lighting": "电影柔光，暗调低饱和，烛火局部光影",
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
        "真人写实、写实风格、畸形五官、明亮强光、现代服装、3D渲染、"
        "塑料质感、明亮阳光、过曝、赛博荧光、西式建筑、机械齿轮、"
        "现代字体、logo水印、AI伪影、重复纹理、不合理手指、扭曲边缘"
    ),
    "normal": NEGATIVE_PROMPT,
    "relaxed": "真人写实、现代服装、3D渲染",
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
    "光腚": "赤膊",
    "裸体": "赤身",
    # 尸体/死亡相关（保留更柔和的替代，非完全删除）
    "尸体": "身影",
    "死亡": "倒下",
    "毙命": "倒地",
    "尸骸": "遗影",
    # 过于血腥的具体描写
    "血腥": "红影",
    "断头": "倒下",
    "肢解": "倒下",
    "鲜血淋漓": "赤色浸染",
    "血肉模糊": "赤影朦胧",
    "脑浆": "浊液",
    "开膛": "伤裂",
    # 新增：暴力动作细节
    "刺进心口": "剑光掠过",
    "砍断手臂": "剑影交错",
    "斩首": "剑光过",
    "割喉": "剑影闪",
    # 新增：性暗示
    "亲吻": "凝望",
    "拥抱": "相视",
    "抚摸": "凝视",
    "裸露": "衣襟微敞",
    "酥胸": "衣襟",
    "玉体": "身影",
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
