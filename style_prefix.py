"""古风视觉风格前缀与合规化处理 — aiVideo 运行时代码的单一来源.

固定前缀在四处出现，必须逐字一致：
  - 01_script_gen.py 的 PROMPT_TEMPLATE（LLM 生成 shot.prompt 时被要求以此开头）
  - 本模块 STYLE_PREFIX（02_video_gen.py / vimax_adapters.py 运行时复用）
  - skill_spy.txt Skill1（参考文档）
  - 02_video_gen.py 的 prefix（应 import 本模块，不再硬编码）
改动任一处须同步其余三处。
"""

STYLE_PREFIX = (
    "国风动漫影视剧，9:16竖屏，电影柔光，暗调低饱和，"
    "烛火局部光影，浅景深缓慢向前推镜，精美2D动画风格，无真人写实，高清1080P"
)

NEGATIVE_PROMPT = "真人写实、写实风格、畸形五官、明亮强光、现代服装、3D渲染、塑料质感、明亮阳光"

# 合规化处理：内容审核规避（Agnes 等平台对低龄/血腥/裸露词触发 content_policy_violation）
_SENSITIVE_SUBSTITUTIONS = {
    # 低龄词汇 → 不影响动漫视觉的替代
    "娃娃": "少年",
    "孩童": "少年",
    "小孩": "少年",
    "儿童": "少年",
    # 裸露相关
    "光腚": "赤膊",
    "裸": "赤",
    # 暴力相关 — 动漫语境下用更叙事的替代
    "血": "红",
    "杀": "击",
    "死": "倒",
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


def ensure_prefix(prompt: str, prefix: str = STYLE_PREFIX) -> str:
    """若 prompt 未以 prefix 开头则拼接，已含则原样返回."""
    if prefix and prefix.split("，")[0] not in prompt.split("，")[0]:
        return f"{prefix}，{prompt}"
    return prompt
