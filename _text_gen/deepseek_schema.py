"""分镜/钩子/角色 JSON Schema — 供 DeepSeek Responses API json_schema 结构化输出.

所有 schema 与 step01_script_gen/prompts.py 的 PROMPT_TEMPLATE / HOOK_PROMPT_TEMPLATE /
character_extractor.py 的 CHARACTER_EXTRACTION_PROMPT 输出结构保持一致。
用 strict json_schema 后，LLM 输出天然合法，validation 只做语义校验。
"""

# ========================================================================
# 通用小结构
# ========================================================================

# v6.34：kf 时间字段迁移 time_ratio → stage + t_sec
# 6 值精细粒度 stage enum（pre/approach/climax/react/exit/after）；
# t_sec 直写秒数（0 < t_sec < duration_sec），禁止 0-1 比例/除法表达式。
# 旧 time_ratio 字段已彻底删除——LLM 必须用新字段。
_KF_STAGE_DESC = {
    "pre":      "动作前一刻：威胁源潜伏/未察觉/盘坐（潜伏/微澜/未察觉/盘坐/静止/暗流）",
    "approach": "逼近/动作启动：破水/举刀/起身（破水/跃出/逼近/举刀/起身）",
    "climax":   "接触/爆发瞬间：动作最高点（刺入/劈落/相击/砸下/此刻/溅起）",
    "react":    "受击方即时反应：闷哼/后仰/初现血（闷哼/后仰/捂嘴/初现洇痕/刚察觉）",
    "exit":     "施动者退场/离开画面（倒纵入水/没入/退入/消失/离去）",
    "after":    "最终结果态：余波/收束/定格（已倒/仰卧/洇痕扩大/只剩涟漪/余波）",
}
_KEYFRAME = {
    "type": "object",
    "properties": {
        "desc": {
            "type": "string",
            "description": "单帧静态画面描述（规则16.2 禁止动作链；每个元素必须能在物理上同瞬间发生）",
        },
        "stage": {
            "type": "string",
            "enum": list(_KF_STAGE_DESC.keys()),
            "description": "kf 语义阶段：" + "；".join(f"{k}={v}" for k, v in _KF_STAGE_DESC.items()),
        },
        "t_sec": {
            "type": "number",
            "minimum": 0.05,
            "maximum": 8.0,
            "description": "kf 物理时刻（秒，0 < t_sec ≤ 8.0）",
        },
    },
    "required": ["desc", "stage", "t_sec"],
    "additionalProperties": False,
}

# 规则51 多人站位蓝图：本镜所有出镜角色的结构化站位（_SHOT 与 _HOOK 共用）。
# LLM 显式给 position/facing/role，供站位清单/帧锚定/校验直读，治站位混乱与比例失衡。
# 空镜/无角色填 []；每个出镜角色必须写入。
_CHAR_LAYOUT = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "角色名，须与顶层 characters.identifier 一致"},
            "position": {"type": "string", "description": "画面位置：画左/画右/画中/前景/中景/后景，可组合（如 画左前景）"},
            "facing": {"type": "string", "description": "朝向：面向画左/面向画右/面向镜头/背对镜头/面向[另一角色名]"},
            "role": {"type": "string", "enum": ["主", "次", "背景"], "description": "画面主次：主要人物/次要人物/背景群演"},
        },
        "required": ["name", "position", "facing"],
        "additionalProperties": False,
    },
    "description": "本镜所有出镜角色的站位布局（规则51多人站位蓝图）；空镜为 []",
}

# v6.57+：per-frame 出镜表（事实源唯一化第一步）
# 替代 v6.35 char_layout 单一站位表：spec 逐帧独立，因为 lf 角色可能与 ff 不同
# （威胁源入水/受害方倒下），per-frame 才能正确表达，治 lf 互斥 + kf 全用 ff+lf
# 拼接的结构性必败。
# on_screen=false 是 lf 互斥根因的字段化方案：QC 期望人数 = on_screen 计数，
# 散文「已没入水中」无需被启发式窗口匹配破译。
_FRAME_SPEC_ITEM = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "角色名（与 characters.identifier 一致）；空镜/无角色填「空镜」",
        },
        "on_screen": {
            "type": "boolean",
            "description": "本帧是否在场。false=本帧已退场（例：lf「神秘人已没入水中」），治 lf 互斥",
        },
        "position": {
            "type": "string",
            "description": "画面位置：画左/画右/画中/前景/中景/后景，可组合（如 画左前景）",
        },
        "pose": {
            "type": "string",
            "description": "本帧姿态（≤15字）：盘坐/潜伏/仰卧/半身探水/站立/倒纵入水/...；空镜填空串",
        },
        "gaze_target": {
            "type": "string",
            "description": "视线目标（角色名 / 画左 / 画右 / 画上 / 画下 / 无）；无视线角色填「无」",
        },
        "props": {
            "type": "array",
            "items": {"type": "string"},
            "description": "本帧持有/身边的道具列表（匕首/酒壶/灯盏/竹箫...）；空填 []",
        },
    },
    "required": ["name", "on_screen", "position", "pose", "gaze_target", "props"],
    "additionalProperties": False,
}

_FRAME_LAYOUTS = {
    "type": "object",
    "properties": {
        "ff": {
            "type": "array",
            "items": _FRAME_SPEC_ITEM,
            "description": "首帧出镜表（per-frame spec 事实源，pre-state 时刻）",
        },
        "lf": {
            "type": "array",
            "items": _FRAME_SPEC_ITEM,
            "description": "尾帧出镜表（per-frame spec 事实源，post-state 时刻，可与 ff 角色不同）",
        },
        "kfs": {
            "type": "array",
            "items": {
                "type": "array",
                "items": _FRAME_SPEC_ITEM,
            },
            "description": "关键帧出镜表（与 shot.keyframes 顺序一一对应，每个 kf 一项出镜表）",
        },
    },
    "required": ["ff", "lf", "kfs"],
    "additionalProperties": False,
}

# ========================================================================
# 镜头 shot
# ========================================================================

# ===========================================================================
# v6.77（第二阶段）：结构化事件表 temporal_plan
#
# 目标：把「时间」从自由文本 motion_desc 提升为结构化事实——每个事件有明确的
# 起止秒数与出处，KF 可按事件身份归属，而不是反复用正则猜。
#
# 迁移策略（决定 1，2026-09-20）：**目标必填，当前过渡态先保持可选且只读**，
# 切换必须按依赖顺序发布——先补全整镜覆盖/连续性/grounding/motion 一致性校验
# （发现 10），再把 canonical plan 纳入 script/per-shot revision 并升级规则版本
# （发现 11），然后所有结构消费者只读同一个已验证 plan，最后才同时把
# `_SHOT`/`_HOOK`/replacement schema 改为 required（旧缓存统一失效）。
# 过渡态下该字段**只参与审计与对比统计**：不得驱动拆镜、合并、duration 或 KF 写入。
# 见 docs/step01-temporal-writers-implementation-review-2026-09-19.md 决定 1。
#
# `event_id` 由管线按镜头 index 确定性分配（`<index>_e<n>`），不由模型产出——
# 避免模型自造 id 冲突或与镜头对不上号。
# ===========================================================================
_TEMPORAL_EVENT = {
    "type": "object",
    "properties": {
        "start_sec": {"type": "number", "minimum": 0},
        "end_sec": {"type": "number", "minimum": 0},
        "description": {
            "type": "string",
            "description": "该时段发生的事件（动词+主体+方向），与 motion_desc 对应段一致",
        },
        "source_quote": {
            "type": "string",
            "description": "该事件在原文中的出处短句；原文无对应句时为空字符串",
        },
    },
    "required": ["start_sec", "end_sec", "description", "source_quote"],
    "additionalProperties": False,
}

_TEMPORAL_PLAN = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "description": (
                "本镜的事件表，**按时间升序、首尾相接、覆盖 (0, duration]**；"
                "通常 2-4 个事件。start_sec/end_sec 必须落在镜头时长内。"
                "与 motion_desc 的分段一一对应。"
            ),
            "items": _TEMPORAL_EVENT,
        },
    },
    "required": ["events"],
    "additionalProperties": False,
}


# v6.138 事件履约声明：本镜承载的节拍(B)/台词(D)序号（编号见「生成执行契约」块；
# 判据在 plan_feasibility.check_event_contract——覆盖/顺序/越界确定性核验）
_PLAN_REFS = {
    "type": "object",
    "description": "事件履约声明：beats=本镜承载的节拍编号 B1..Bn（生成执行契约第四节），"
                   "lines=本镜台词对应的选用台词编号（第一节）；纯过渡镜写空数组",
    "properties": {
        "beats": {"type": "array", "items": {"type": "integer", "minimum": 1}},
        "lines": {"type": "array", "items": {"type": "integer", "minimum": 1}},
    },
    "required": ["beats", "lines"],
    "additionalProperties": False,
}

_SHOT = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "plan_refs": _PLAN_REFS,
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
        "ff_desc": {"type": "string", "description": "首帧静态画面描述。必须定格在 motion 第一个动作的「前一刻」（动作尚未发生、可画出的静止画面），禁止动作进行态（破水而出/正在刺向/张嘴尖叫）；破水而出→潜伏水下/水面微澜、刺向→刀尖抵近/即将触及、尖叫→张嘴欲呼；禁止时间推进/多动作堆叠、禁止完成态动词（已刺中/倒地，动作结果只能在lf_desc）；多人视线用画左/画右/画上方锚定并汇聚到同一焦点。用短句动词前置、一角色一短句，普通镜头 ≤120 字符；只删冗余不删角色/前一刻/明确朝向/服装色。"},
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
        "temporal_plan": _TEMPORAL_PLAN,
        # v6.21：人物朝向（镜头组接规则33，可选——同场景连续镜头朝向一致性校验用）
        "subject_direction": {"type": "string", "description": "人物朝向（左/右/面向镜头/背对），可选；同场景相邻镜头朝向不应翻转"},
        # v6.57+：per-frame spec 事实源（必填；QC + prompt 组装均走它，逐帧独立出镜表）。
        # 替代 v6.35 char_layout 单一站位表（已从 schema 移除，旧章数据由 accessor 降级）。
        "frame_layouts": _FRAME_LAYOUTS,
    },
    "required": [
        "index", "plan_refs", "narration_segment", "subtitle", "scene", "location",
        "main_subject", "core_keywords", "prompt", "motion_desc",
        "ff_desc", "lf_desc", "context_bridge", "audio_cue", "fx_effect",
        "color_tone", "cam_idx", "clip_transition", "duration",
        "variation_type", "keyframes", "frame_layouts",
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
        "duration": {
            "type": "number",
            "description": "LLM 预估时长（秒），仅作参考",
        },
        "duration_sec": {
            "type": "number",
            "description": "本镜头视频目标时长（秒）——由 step01 timecode 权威计算，视频生成以它为准",
        },
        "keyframes": {"type": "array", "items": _KEYFRAME},
        "temporal_plan": _TEMPORAL_PLAN,
        "ff_desc": {"type": "string", "description": "钩子首帧静态画面描述。必须定格在 motion 第一个动作的「前一刻」（动作尚未发生），禁止动作进行态（破水而出/正在刺向/张嘴尖叫）；破水而出→潜伏水下/水面微澜、刺向→刀尖抵近/即将触及、尖叫→张嘴欲呼；禁止时间推进/多动作堆叠、禁止完成态动词（已刺中/倒地）；多人视线用画左/画右锚定并汇聚到同一焦点。用短句动词前置，钩子 ≤160 字符；只删冗余不删角色/前一刻/明确朝向/服装色。"},
        "lf_desc": {"type": "string"},
        "motion_desc": {"type": "string"},
        "context_bridge": {"type": "string"},
        "prompt": {"type": "string"},
        # v6.57+：per-frame spec 事实源（必填；QC + prompt 组装均走它，逐帧独立出镜表）。
        # 替代 Plan A char_layout（已从 schema 移除，旧章数据由 accessor 降级）。
        "frame_layouts": _FRAME_LAYOUTS,
    },
    "required": [
        "index", "narration_segment", "subtitle", "scene", "location",
        "main_subject", "core_keywords", "fx_effect", "audio_cue",
        "mask_area", "color_tone", "clip_transition", "cam_idx",
        "variation_type", "duration", "keyframes",
        "ff_desc", "lf_desc", "motion_desc",
        "context_bridge", "prompt", "frame_layouts",
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
            "required_facts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "必须保留的剧情事实，每条包含事实和原文证据",
            },
            "causal_chain": {
                "type": "array",
                "items": {"type": "string"},
                "description": "起因→行动→结果→悬念",
            },
            "character_motives": {
                "type": "array",
                "items": {"type": "string"},
                "description": "角色想做什么、为什么、原文证据",
            },
            "shot_beats": {
                "type": "array",
                "items": {"type": "string"},
                "description": "镜头节拍、信息目的和原文依据",
            },
            # v6.130（用户 2026-09-25 第二步）：计划是**本集内容取舍与预算的权威**
            # （小说原文仍是事实与台词的依据）。下面两个字段是"生成前可行性"的输入：
            # 选用的逐字台词（代码据此估时）与可省略内容（重选时的候选池）。
            "dialogue_lines": {
                "type": "array",
                "items": {"type": "string"},
                "description": "本集选用的逐字台词/独白，从原文逐字摘出（不改写、不自造）",
            },
            "omittable": {
                "type": "array",
                "items": {"type": "string"},
                "description": "本集可省略的原文内容（不拍也不影响主线，重选时的候选池）",
            },
            "time_budget": {
                "type": "string",
                "description": "对白、动作、反应的大致分配倾向（不必报精确秒数，时长由代码估算）",
            },
        },
        "required": ["main_events", "characters", "emotional_arc", "climax", "end_hook", "key_scenes",
                     "required_facts", "causal_chain", "character_motives", "shot_beats",
                     "dialogue_lines", "omittable", "time_budget"],
        "additionalProperties": False,
    },
}



# ===========================================================================
# v6.77（第四阶段）：修复轮改为「完整镜头事务」
#
# 旧契约 `REPAIR_PATCH_SCHEMA` 返回字段级 `patches[]`。问题是：LLM 只补它想到
# 的字段，结构字段之间可以互不一致（补了 motion 没补 keyframes、补了 lf 没补
# frame_layouts.kfs），系统还得再跑 post_fix / hard_fix / timecode 去"兜"，
# 而接受判定比较的是「补丁 + 全部后处理副作用」——后处理引入的新阻断会把
# 本该通过的局部修复整轮拖回滚。
#
# 新契约要求返回**完整镜头对象**（与首次生成同构，复用 `_SHOT`），
# 使「结构字段成套出现」成为 schema 层保证，而不是事后校验。
# ===========================================================================
# v6.89（发现 14 选项 2，2026-09-22）：**单镜定向拆分**的响应契约。
#
# 背景：单镜内容需求 > cap（实测 9.8 / 12.5 / 14.7 / 20.2s）是唯一没被任何修复
# 路径消掉的桶，而 prompt 侧的「单条台词也可以切开」显式许可**已被证伪**——三个
# 候选行为逐字不变。故改为对**单个**被点名的超载镜头做一次定向拆分请求。
#
# 关键：`items` 直接复用 `_SHOT`（**24 个必填字段**）——返回的子镜在 schema 层
# 就不可能是空壳。这正是当年禁止「复制字段拆镜」要防的东西，而模型重生成的
# 子镜天然不踩（子镜的视觉字段是模型新写的，不是从父镜复制的）。
# plan_refs 是**管线维护的契约状态**（v6.138）：只在首次生成 schema 强制必填；
# 拆分/修复返回的镜头对象里**可选**——拆分子镜由管线按父镜注入（_scoped_split_one），
# 修复替换由 merge_authorized 按 target 保留，模型不声明也不算违约。
_SHOT_OPTIONAL_REFS = {
    **_SHOT,
    "required": [f for f in _SHOT["required"] if f != "plan_refs"],
}

SPLIT_SHOT_SCHEMA = {
    "name": "split_over_cap_shot",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "sub_shots": {
                "type": "array",
                "minItems": 2,
                # v6.89 续：上限 3 → 4。提示词要求「最少段数 = ⌈需求 ÷ 上限⌉」，
                # 若某镜需求 > 3×上限（>24s），3 段就**不可能**满足——上限必须
                # 容得下那条指令，否则又是一处「判据永远不可能通过」。
                "maxItems": 4,
                "description": (
                    "把被点名的那**一个**超载镜头拆成的 2-4 个**完整**子镜。"
                    "每个子镜必须是与首次生成同构的完整镜头对象（24 个字段成套出现），"
                    "禁止只给变化字段、禁止留空壳。台词按原文**连续切分**、逐字不增不减。"
                ),
                "items": _SHOT_OPTIONAL_REFS,
            },
        },
        "required": ["sub_shots"],
        "additionalProperties": False,
    },
}


REPAIR_SHOT_REPLACEMENT_SCHEMA = {
    "name": "repair_shot_replacements",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "shot_replacements": {
                "type": "array",
                "description": (
                    "只放**被点名镜头**的完整替换版本。未点名的镜头不要出现在这里。"
                    "每个 shot 必须是结构完整的镜头对象（与首次生成同构："
                    "narration/subtitle/prompt/motion_desc/ff_desc/lf_desc/"
                    "keyframes/frame_layouts/duration 等成套出现），"
                    "禁止只给发生变化的字段。"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "shot_id": {
                            "type": "string",
                            "description": "目标普通镜头 index（如 \"4\"）",
                        },
                        "shot": _SHOT_OPTIONAL_REFS,
                    },
                    "required": ["shot_id", "shot"],
                    "additionalProperties": False,
                },
            },
            # v6.80（发现 9 #1）：hook 与普通镜头**字段要求不同**，必须分开承载。
            # 旧契约让 hook 也走 `_SHOT`，而真实 hook 用 `_HOOK`（多 mask_area、
            # 少 scene/location 等），模型只能产出不合规对象或被迫填假字段。
            "hook_replacement": {
                "type": "object",
                "description": "hook 的完整替换版本（只在 hook 被点名时输出）",
                "properties": {
                    "shot_id": {
                        "type": "string",
                        "description": "固定为 \"hook\"",
                    },
                    "shot": _HOOK,
                },
                "required": ["shot_id", "shot"],
                "additionalProperties": False,
            },
        },
        "required": ["shot_replacements"],
        "additionalProperties": False,
    },
}

REPAIR_PATCH_SCHEMA = {
    "name": "repair_patches",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "patches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "shot_id": {"type": "string"},
                        "field_path": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["shot_id", "field_path", "value"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["patches"],
        "additionalProperties": False,
    },
}
