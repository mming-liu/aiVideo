"""12位导演电影感风格提示词模板

用于在视频生成时注入导演风格关键词，提升电影感。

使用方式：
    from style_cinematic import get_cinematic_prefix, get_director_negative

    prompt = get_cinematic_prefix("wong_kar_wai") + original_prompt
    negative = get_director_negative("wong_kar_wai")
"""

# 导演风格前缀模板（中文）
_CINEMATIC_PREFIX_ZH = {
    "wong_kar_wai": (
        "王家卫电影风格，高饱和情绪化色彩（深红、翡翠绿、电蓝），慢镜头效果，手持摄影美学，"
        "都市疏离氛围，运动模糊，霓虹招牌，怀旧浪漫情绪，梦幻焦外，花样年华风格，"
    ),
    "kubrick": (
        "库布里克电影风格，完美对称构图，中心取景，冷色调（蓝灰、米白），极简主义布景，"
        "硬侧光，高对比度，无多余元素，2001太空漫游风格，"
    ),
    "villeneuve": (
        "丹尼斯·维伦纽瓦电影风格，粗粝现实主义，IMAX 70mm质感，广袤空间，大量负空间，"
        "慢节奏推轨镜头，压抑庄严氛围，体积光，沙丘风格，"
    ),
    "fincher": (
        "大卫·芬奇电影风格，冷蓝灰调色，暗部细节丰富，锐利构图，轻微手持抖动，高对比度，"
        "七宗罪风格，纪实感，"
    ),
    "malick": (
        "泰伦斯·马力克电影风格，自然柔光，自然环境，诗意慢镜头，柔和焦外，生命之树风格，"
    ),
    "anderson": (
        "韦斯·安德森电影风格，完美对称构图，中心取景，糖果色配色（粉色、黄色、薄荷绿），"
        "精致场景设计，平铺美学，复古道具，奇想氛围，布达佩斯大饭店风格，"
    ),
    "spielberg": (
        "斯皮尔伯格电影风格，暖金色调，广角镜头，英雄主义构图，戏剧性侧光，辛德勒的名单风格，"
    ),
    "hitchcock": (
        "希区柯克电影风格，低角度仰拍，阴影压迫感，窥视视角，悬疑冷色调，惊魂记风格，"
    ),
    "kurosawa": (
        "黑泽明电影风格，高饱和色彩，动态构图，自然光影，武士道美学，七武士风格，"
    ),
    "fellini": (
        "费德里科·费里尼电影风格，荒诞构图，暖黄复古色调，梦幻氛围感，夸张人物造型，"
        "八部半风格，"
    ),
    "bergman": (
        "英格玛·伯格曼电影风格，冷灰调，极简构图，面部特写，存在主义氛围，第七封印风格，"
    ),
    "nolan": (
        "克里斯托弗·诺兰电影风格，高对比度，IMAX质感，时间交错构图，冷蓝调，盗梦空间风格，"
    ),
}

# 导演风格前缀模板（英文，更兼容AI视频模型）
_CINEMATIC_PREFIX_EN = {
    "wong_kar_wai": (
        "Wong Kar-wai cinematography, highly saturated emotional color grading (deep red, emerald green, electric blue), "
        "slow motion effect, handheld camera aesthetic, urban isolation atmosphere, motion blur, Hong Kong neon signs, "
        "nostalgic romantic mood, In the Mood for Love inspired, dreamy bokeh, "
    ),
    "kubrick": (
        "Stanley Kubrick cinematography, perfectly symmetrical composition, centered framing, "
        "cool color palette (blue-gray, off-white), minimalist set design, hard side lighting, high contrast, "
        "no redundant elements, 2001: A Space Odyssey inspired, "
    ),
    "villeneuve": (
        "Denis Villeneuve cinematography, gritty realism, IMAX 70mm texture, vast space, lots of negative space, "
        "slow tracking shot, oppressive and solemn atmosphere, volumetric light, Dune inspired, "
    ),
    "fincher": (
        "David Fincher cinematography, cool blue-gray color grading, rich shadow details, sharp composition, "
        "slight handheld jitter, high contrast, Se7en inspired, documentary feel, "
    ),
    "malick": (
        "Terrence Malick cinematography, soft natural light, natural environment, poetic slow motion, "
        "soft bokeh, The Tree of Life inspired, "
    ),
    "anderson": (
        "Wes Anderson cinematography, perfectly symmetrical composition, centered framing, "
        "pastel candy color palette (pink, yellow, mint green), meticulous production design, flat lay aesthetic, "
        "retro vintage props, whimsical atmosphere, The Grand Budapest Hotel inspired, "
    ),
    "spielberg": (
        "Steven Spielberg cinematography, warm golden tone, wide-angle lens, heroic composition, "
        "dramatic side lighting, Schindler's List inspired, "
    ),
    "hitchcock": (
        "Alfred Hitchcock cinematography, low-angle shot, shadow oppression, peeping perspective, "
        "suspense cool tone, Psycho inspired, "
    ),
    "kurosawa": (
        "Akira Kurosawa cinematography, highly saturated colors, dynamic composition, natural light, "
        "samurai aesthetics, Seven Samurai inspired, "
    ),
    "fellini": (
        "Federico Fellini cinematography, absurd composition, warm yellow retro tone, dreamy atmosphere, "
        "exaggerated character styling, 8½ inspired, "
    ),
    "bergman": (
        "Ingmar Bergman cinematography, cool gray tone, minimalist composition, close-up of face, "
        "existential atmosphere, The Seventh Seal inspired, "
    ),
    "nolan": (
        "Christopher Nolan cinematography, high contrast, IMAX texture, time-interleaved composition, "
        "cool blue tone, Inception inspired, "
    ),
}

# 导演风格负面提示词（需要避免的元素）
_CINEMATIC_NEGATIVE = {
    "wong_kar_wai": "灰暗色调、现代建筑、塑料感、过度锐化",
    "kubrick": "手持抖动、色彩鲜艳、不对称构图、杂乱背景",
    "villeneuve": "明亮色调、卡通风格、过度饱和、小场景",
    "fincher": "柔焦效果、过度鲜艳、梦幻色调、舞台化布光",
    "malick": "硬光直射、高对比度、锐利边缘、人工痕迹",
    "anderson": "真实纹理、脏乱场景、自然光影、模糊焦点",
    "spielberg": "冷色调、小景别、手持晃动、极简构图",
    "hitchcock": "明亮场景、喜剧氛围、彩色服装、正面光源",
    "kurosawa": "静态镜头、单调配色、柔和光线、现代场景",
    "fellini": "冷色调、极简构图、自然光、现代服饰",
    "bergman": "暖色调、彩色画面、宽广场景、轻松氛围",
    "nolan": "柔焦效果、单色调、慢节奏、静态构图",
}

# 别名映射（支持多种输入格式）
_ALIASES = {
    "wangjiawei": "wong_kar_wai",
    "王家卫": "wong_kar_wai",
    "kubrick": "kubrick",
    "库布里克": "kubrick",
    "villeneuve": "villeneuve",
    "维伦纽瓦": "villeneuve",
    "denis": "villeneuve",
    "fincher": "fincher",
    "大卫芬奇": "fincher",
    "david fincher": "fincher",
    "malick": "malick",
    "马力克": "malick",
    "terrence malick": "malick",
    "anderson": "anderson",
    "韦斯安德森": "anderson",
    "wes anderson": "anderson",
    "spielberg": "spielberg",
    "斯皮尔伯格": "spielberg",
    "steven spielberg": "spielberg",
    "hitchcock": "hitchcock",
    "希区柯克": "hitchcock",
    "alfred hitchcock": "hitchcock",
    "kurosawa": "kurosawa",
    "黑泽明": "kurosawa",
    "akira kurosawa": "kurosawa",
    "fellini": "fellini",
    "费里尼": "fellini",
    "federico fellini": "fellini",
    "bergman": "bergman",
    "伯格曼": "bergman",
    "ingmar bergman": "bergman",
    "nolan": "nolan",
    "诺兰": "nolan",
    "christopher nolan": "nolan",
}


def _normalize_style(style: str) -> str:
    """标准化导演风格名称"""
    if not style:
        return ""
    style = style.strip().lower().replace(" ", "_").replace("-", "_")
    return _ALIASES.get(style, style)


def get_cinematic_prefix(style: str, lang: str = "en") -> str:
    """获取导演风格前缀

    Args:
        style: 导演风格名称（支持中文/英文/别名）
        lang: 语言 "en" 或 "zh"

    Returns:
        风格前缀字符串（无则返回空字符串）
    """
    normalized = _normalize_style(style)
    if not normalized or normalized not in _CINEMATIC_PREFIX_EN:
        return ""

    templates = _CINEMATIC_PREFIX_EN if lang == "en" else _CINEMATIC_PREFIX_ZH
    return templates.get(normalized, "")


def get_director_negative(style: str) -> str:
    """获取导演风格对应的负面提示词

    Args:
        style: 导演风格名称

    Returns:
        负面提示词字符串（无则返回空字符串）
    """
    normalized = _normalize_style(style)
    if not normalized or normalized not in _CINEMATIC_NEGATIVE:
        return ""
    return _CINEMATIC_NEGATIVE[normalized]


def list_available_styles() -> list[str]:
    """列出所有可用的导演风格"""
    return list(_CINEMATIC_PREFIX_EN.keys())


def get_style_info(style: str) -> dict | None:
    """获取导演风格详细信息

    Returns:
        dict: {name, prefix_zh, prefix_en, negative, description}
    """
    normalized = _normalize_style(style)
    if not normalized or normalized not in _CINEMATIC_PREFIX_EN:
        return None

    descriptions = {
        "wong_kar_wai": "高饱和红蓝对比、手持抽帧、都市疏离感、慢镜头",
        "kubrick": "完美对称构图、冷色调、极简布景、硬光对比",
        "villeneuve": "粗粝现实主义、IMAX 70mm质感、负空间、慢推轨",
        "fincher": "冷蓝灰调色、暗部细节丰富、锐利构图、手持纪实",
        "malick": "自然光、自然环境、诗意慢镜头、柔和焦外",
        "anderson": "糖果色配色、完美对称、复古精致、平铺美学",
        "spielberg": "暖金色调、广角镜头、英雄主义、戏剧性光影",
        "hitchcock": "低角度仰拍、阴影压迫感、窥视视角、悬疑冷调",
        "kurosawa": "高饱和色彩、动态构图、自然光影、武士道美学",
        "fellini": "荒诞构图、暖黄复古、梦幻氛围、夸张造型",
        "bergman": "冷灰调、极简构图、面部特写、存在主义",
        "nolan": "高对比度、IMAX质感、时间交错、冷蓝调",
    }

    return {
        "name": normalized,
        "prefix_zh": _CINEMATIC_PREFIX_ZH.get(normalized, ""),
        "prefix_en": _CINEMATIC_PREFIX_EN.get(normalized, ""),
        "negative": _CINEMATIC_NEGATIVE.get(normalized, ""),
        "description": descriptions.get(normalized, ""),
    }