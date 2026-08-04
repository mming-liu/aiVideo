"""分镜/钩子/角色 JSON Schema — 供 DeepSeek Responses API json_schema 结构化输出.

所有 schema 与 step01_script_gen/prompts.py 的 PROMPT_TEMPLATE / HOOK_PROMPT_TEMPLATE /
character_extractor.py 的 CHARACTER_EXTRACTION_PROMPT 输出结构保持一致。
用 strict json_schema 后，LLM 输出天然合法，validation 只做语义校验。
"""

# ========================================================================
# 通用小结构
# ========================================================================

_KEYFRAME = {
    "type": "object",
    "properties": {
        "desc": {"type": "string"},
        "time_ratio": {"type": "number"},
    },
    "required": ["desc", "time_ratio"],
    "additionalProperties": False,
}

# ========================================================================
# 镜头 shot
# ========================================================================

_SHOT = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "narration_segment": {
            "type": "string",
            "description": "人物对话原文，格式「说话人道：台词」；无对白为空字符串",
        },
        "subtitle": {"type": "string", "description": "画面上显示的字幕，与 narration_segment 相同"},
        "scene": {"type": "string", "description": "场景名称"},
        "location": {"type": "string", "description": "物理地点"},
        "main_subject": {"type": "string", "description": "画面主体"},
        "core_keywords": {"type": "array", "items": {"type": "string"}},
        "prompt": {"type": "string"},
        "motion_desc": {"type": "string"},
        "ff_desc": {"type": "string", "description": "首帧静态画面描述"},
        "lf_desc": {"type": "string", "description": "尾帧静态画面描述"},
        "context_bridge": {"type": "string"},
        "audio_cue": {"type": "string"},
        "fx_effect": {"type": "string"},
        "color_tone": {"type": "string"},
        "cam_idx": {"type": "integer"},
        "clip_transition": {"type": "string"},
        "duration": {"type": "number"},
        "variation_type": {"type": "string", "enum": ["small", "large"]},
        "keyframes": {"type": "array", "items": _KEYFRAME},
    },
    "required": [
        "index", "narration_segment", "subtitle", "scene", "location",
        "main_subject", "core_keywords", "prompt", "motion_desc",
        "ff_desc", "lf_desc", "context_bridge", "audio_cue", "fx_effect",
        "color_tone", "cam_idx", "clip_transition", "duration",
        "variation_type", "keyframes",
    ],
    "additionalProperties": False,
}

# ========================================================================
# 钩子分镜 hook
# ========================================================================

_HOOK = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "narration_segment": {"type": "string"},
        "subtitle": {"type": "string"},
        "scene": {"type": "string"},
        "location": {"type": "string"},
        "main_subject": {"type": "string"},
        "core_keywords": {"type": "array", "items": {"type": "string"}},
        "fx_effect": {"type": "string"},
        "audio_cue": {"type": "string"},
        "mask_area": {"type": "string"},
        "color_tone": {"type": "string"},
        "clip_transition": {"type": "string"},
        "cam_idx": {"type": "integer"},
        "variation_type": {"type": "string"},
        "ff_desc": {"type": "string"},
        "lf_desc": {"type": "string"},
        "motion_desc": {"type": "string"},
        "context_bridge": {"type": "string"},
        "prompt": {"type": "string"},
    },
    "required": [
        "index", "narration_segment", "subtitle", "scene", "location",
        "main_subject", "core_keywords", "fx_effect", "audio_cue",
        "mask_area", "color_tone", "clip_transition", "cam_idx",
        "variation_type", "ff_desc", "lf_desc", "motion_desc",
        "context_bridge", "prompt",
    ],
    "additionalProperties": False,
}

# ========================================================================
# 角色提取
# ========================================================================

_CHARACTER = {
    "type": "object",
    "properties": {
        "identifier": {"type": "string"},
        "gender": {"type": "string", "enum": ["male", "female"]},
        "age_range": {"type": "string", "enum": ["young", "middle", "old"]},
        "static_features": {"type": "string"},
        "dynamic_features": {"type": "string"},
        "is_visible": {"type": "boolean"},
        "is_new": {"type": "boolean"},
    },
    "required": [
        "identifier", "gender", "age_range",
        "static_features", "dynamic_features",
        "is_visible", "is_new",
    ],
    "additionalProperties": False,
}

# ========================================================================
# 顶层 schema
# ========================================================================

SCRIPT_SCHEMA = {
    "name": "storyboard_script",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "shot_count": {"type": "integer"},
            "characters": {
                "type": "array",
                "items": _CHARACTER,
            },
            "shots": {
                "type": "array",
                "items": _SHOT,
            },
            "hook": {"anyOf": [{"type": "object", "properties": _HOOK["properties"], "required": _HOOK["required"], "additionalProperties": False}, {"type": "null"}]},
        },
        "required": ["title", "characters", "shots"],
        "additionalProperties": False,
    },
}

HOOK_SCHEMA = {
    "name": "hook_shot",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "hook": _HOOK,
        },
        "required": ["hook"],
        "additionalProperties": False,
    },
}

CHARACTERS_SCHEMA = {
    "name": "characters",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "characters": {
                "type": "array",
                "items": _CHARACTER,
            },
        },
        "required": ["characters"],
        "additionalProperties": False,
    },
}

# ========================================================================
# 章节场景分析（shared/chapter_reader.py _analyze_chapter_scenes）
# ========================================================================

SCENE_ANALYSIS_SCHEMA = {
    "name": "scene_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "main_scenes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "主线场景描述",
            },
            "main_characters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "主要人物",
            },
            "filtered_sections": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "需要过滤的节索引（与主线无关的回忆、插叙）",
            },
            "reason": {
                "type": "string",
                "description": "分析理由",
            },
        },
        "required": ["main_scenes", "main_characters", "filtered_sections", "reason"],
        "additionalProperties": False,
    },
}

# ========================================================================
# 场景物体尺寸提取（step01_script_gen/object_extractor.py）
# ========================================================================

_SCENE_OBJECT = {
    "type": "object",
    "properties": {
        "identifier": {
            "type": "string",
            "description": "物体名称（跨章节保持一致，如：照月湾小舟、青石马车）",
        },
        "category": {
            "type": "string",
            "description": "物体类别（如：船、车、轿、厅堂、院落）",
        },
        "subtype": {
            "type": "string",
            "description": "形制（如：窄身无篷舴艋舟、四轮马车）",
        },
        "length_m": {
            "type": "number",
            "description": "总长（米），由原文空间线索推导",
        },
        "width_m": {
            "type": "number",
            "description": "最大宽度（米）",
        },
        "height_m": {
            "type": "number",
            "description": "舱面高度/船舷高度（米）；无高度意义物体可填 0",
        },
        "capacity": {
            "type": "string",
            "description": "可容人数与装载（如：可容3-4人+炭炉酒菜）",
        },
        "structure": {
            "type": "string",
            "description": "内部结构分区（如：船头/中舱/船梢三段；船头可坐、中舱敞舱可站立烹煮、船梢收窄）",
        },
        "consistent_terms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "跨镜头统一称呼（如：[\"这条舴艋舟\", \"窄身小舟\"]，全脚本必须用同一规格词）",
        },
        "size_reasoning": {
            "type": "string",
            "description": "尺寸推导依据（原文哪些线索→如何排除过小/过大）",
        },
        "is_new": {
            "type": "boolean",
            "description": "是否不在已知物体库中",
        },
    },
    "required": [
        "identifier", "category", "subtype", "length_m", "width_m",
        "height_m", "capacity", "structure", "consistent_terms",
        "size_reasoning", "is_new",
    ],
    "additionalProperties": False,
}

SCENE_OBJECTS_SCHEMA = {
    "name": "scene_objects",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "scene_objects": {
                "type": "array",
                "items": _SCENE_OBJECT,
            },
        },
        "required": ["scene_objects"],
        "additionalProperties": False,
    },
}
