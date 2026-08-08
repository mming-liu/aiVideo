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
        "ff_desc": {"type": "string", "description": "首帧静态画面描述。必须定格在 motion 第一个动作的「前一刻」（动作尚未发生、可画出的静止画面），禁止动作进行态（破水而出/正在刺向/张嘴尖叫）；破水而出→潜伏水下/水面微澜、刺向→刀尖抵近/即将触及、尖叫→张嘴欲呼；禁止时间推进/多动作堆叠、禁止完成态动词（已刺中/倒地，动作结果只能在lf_desc）；多人视线用画左/画右/画上方锚定并汇聚到同一焦点。用短句动词前置、一角色一短句，普通镜头 ≤80 字符；只删冗余不删角色/前一刻/明确朝向。"},
        "lf_desc": {"type": "string", "description": "尾帧静态画面描述"},
        "context_bridge": {"type": "string"},
        "audio_cue": {"type": "string"},
        "fx_effect": {"type": "string"},
        "color_tone": {"type": "string"},
        "cam_idx": {"type": "integer"},
        "clip_transition": {"type": "string"},
        "duration": {
            "type": "number",
            "description": "LLM 预估时长（秒），仅作参考",
        },
        "duration_sec": {
            "type": "number",
            "description": "本镜头视频目标时长（秒）——由 step01 timecode 权威计算，视频生成以它为准；剥离 TTS 后由它驱动时长",
        },
        "variation_type": {"type": "string", "enum": ["small", "large"]},
        "keyframes": {"type": "array", "items": _KEYFRAME},
        # v6.21：人物朝向（镜头组接规则33，可选——同场景连续镜头朝向一致性校验用）
        "subject_direction": {"type": "string", "description": "人物朝向（左/右/面向镜头/背对），可选；同场景相邻镜头朝向不应翻转"},
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
        "ff_desc": {"type": "string", "description": "钩子首帧静态画面描述。必须定格在 motion 第一个动作的「前一刻」（动作尚未发生），禁止动作进行态（破水而出/正在刺向/张嘴尖叫）；破水而出→潜伏水下/水面微澜、刺向→刀尖抵近/即将触及、尖叫→张嘴欲呼；禁止时间推进/多动作堆叠、禁止完成态动词（已刺中/倒地）；多人视线用画左/画右锚定并汇聚到同一焦点。用短句动词前置，钩子 ≤120 字符；只删冗余不删角色/前一刻/明确朝向。"},
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
            # v6.21：战场分层字段（仅大战场镜头输出，普通镜头省略）
            "scene_layer": {"type": "string", "description": "战场镜头分层（hero_layer英雄层/near_action近景动作层/mid_queue中景队列层/far_density远景密度层），非战场镜头省略"},
            "world_dna": {
                "type": "object",
                "description": "战场世界观恒定参数（同一战役全部镜头共用，禁止乱跳）",
                "properties": {
                    "terrain": {"type": "string", "description": "地形建筑（城墙/滩涂/营寨结构固定）"},
                    "lighting": {"type": "string", "description": "光照（太阳方位/阴影投射方向固定）"},
                    "atmosphere": {"type": "string", "description": "环境氛围（烟尘颜色/雾气浓度）"},
                    "scale_anchor": {"type": "string", "description": "尺度锚点（城门/战车/战马，约束士兵大小防透视崩坏）"},
                },
                "additionalProperties": False,
            },
            "battle_asset_group": {"type": "string", "description": "战场资产组ID（复用预制盾牌/头盔/旗帜资产，禁止自由随机设计军备）"},
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

# ========================================================================
# 场景资产提取（step01_script_gen/asset_extractor.py）
# 方法论：大场景建立镜头锁定地理/时间/天气色调，近景功能场景从其派生。
# 与 SCENE_OBJECTS_SCHEMA 的分工：后者锚定空间物体的尺寸/容量（文字），
# 本 schema 锚定场景的建立镜头/近景镜头描述（供资产生成图 + r2v 场景参考）。
# ========================================================================

_SCENE_ASSET = {
    "type": "object",
    "properties": {
        "identifier": {
            "type": "string",
            "description": "场景名称（跨章节保持一致，如：照月湾小舟、杨府别庄大门）",
        },
        "location": {
            "type": "string",
            "description": "物理地点（如：照月湾荷花丛中、云河镇杨府别庄大门外）",
        },
        "time_of_day": {
            "type": "string",
            "description": "主要时间段（如：初秋上午、黄昏、夜晚）",
        },
        "weather": {
            "type": "string",
            "description": "天气状态（如：轻薄云层、月光晴朗）",
        },
        "color_tone": {
            "type": "string",
            "description": "整体色调（如：自然温暖朴素、冷蓝夜色）",
        },
        "wide_desc": {
            "type": "string",
            "description": "大场景建立镜头描述：地理环境/空间关系/季节光线/年代风格，画面中不得出现人物",
        },
        "near_desc": {
            "type": "string",
            "description": "近景功能场景描述：同一地点的内部/近景，与大场景保持季节/光线/年代/风格一致，画面中不得出现人物",
        },
        "consistent_terms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "跨镜头统一称呼（如：[\"照月湾荷花丛中\", \"荷花荡\"]）",
        },
        "is_new": {
            "type": "boolean",
            "description": "是否不在已知场景库中",
        },
    },
    "required": [
        "identifier", "location", "time_of_day", "weather", "color_tone",
        "wide_desc", "near_desc", "consistent_terms", "is_new",
    ],
    "additionalProperties": False,
}

SCENE_ASSETS_SCHEMA = {
    "name": "scene_assets",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "scene_assets": {
                "type": "array",
                "items": _SCENE_ASSET,
            },
        },
        "required": ["scene_assets"],
        "additionalProperties": False,
    },
}

# ========================================================================
# 道具资产提取（step01_script_gen/asset_extractor.py）
# 方法论：剧本核心道具 → 独立棚拍白底资产图，为后续贴图与视频生成提供稳定基础。
# ========================================================================

_PROP = {
    "type": "object",
    "properties": {
        "identifier": {
            "type": "string",
            "description": "道具名称（跨章节保持一致，如：竹编背篓、青石马车）",
        },
        "category": {
            "type": "string",
            "description": "道具类别（如：背具、武器、器物、交通工具）",
        },
        "appearance": {
            "type": "string",
            "description": "外观描述：材质/颜色/结构/尺寸/使用痕迹/装饰，供资产生成图与跨章一致性引用",
        },
        "consistent_terms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "跨镜头统一称呼",
        },
        "is_new": {
            "type": "boolean",
            "description": "是否不在已知道具库中",
        },
    },
    "required": [
        "identifier", "category", "appearance", "consistent_terms", "is_new",
    ],
    "additionalProperties": False,
}

PROPS_SCHEMA = {
    "name": "props",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "props": {
                "type": "array",
                "items": _PROP,
            },
        },
        "required": ["props"],
        "additionalProperties": False,
    },
}


# ========================================================================
# 章节大纲（step01_script_gen/outline.py，ShortVideoDirector director-outline 吸收）
# ========================================================================

OUTLINE_SCHEMA = {
    "name": "chapter_outline",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "main_events": {
                "type": "array",
                "items": {"type": "string"},
                "description": "本章主要事件（按时间顺序，3-6个，覆盖全章）",
            },
            "characters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "出场角色列表",
            },
            "emotional_arc": {
                "type": "string",
                "description": "本章情绪弧线（如：平静→惊疑→爆发→余波）",
            },
            "climax": {
                "type": "string",
                "description": "本章高潮事件（冲突/转折点）",
            },
            "end_hook": {
                "type": "string",
                "description": "结尾钩子（吸引观众看下一集）",
            },
            "key_scenes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "需要重点刻画的关键场景",
            },
        },
        "required": ["main_events", "characters", "emotional_arc", "climax", "end_hook", "key_scenes"],
        "additionalProperties": False,
    },
}
