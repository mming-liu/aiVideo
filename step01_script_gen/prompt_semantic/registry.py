"""prompt_semantic.registry — 数据驱动的实体注册表.

新增 prop / 角色姿态 / 场景只改本文件。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple

from step01_script_gen.scene_anchors import (
    scene_anchors_for,
    is_horizontal_scene,
    is_containing_scene,
    get_scene_type,
)


@dataclass(frozen=True)
class PropSpec:
    """道具物理规格.

    Attributes:
        names: 别名集合（任意一个出现即匹配）
        can_float: 是否可浮在水面
        default_owner: 缺失归属时回退的默认持有角色
        can_be_dropped: 是否允许脱手（false=武器等"必须持有"）
    """
    names: Tuple[str, ...]
    can_float: bool = False
    default_owner: str = ""
    can_be_dropped: bool = True


PROP_REGISTRY: dict[str, PropSpec] = {
    "wine_jug":     PropSpec(("酒壶", "酒瓮"),            can_float=True,  default_owner="杨旭", can_be_dropped=True),
    "wine_cup":     PropSpec(("酒杯",),                   can_float=True,  default_owner="",     can_be_dropped=True),
    "fishing_rod":  PropSpec(("钓杆", "钓鱼竿", "钓竿"),   can_float=False, default_owner="杨旭", can_be_dropped=True),
    "umbrella":     PropSpec(("油纸伞", "伞"),             can_float=False, default_owner="听香", can_be_dropped=True),
    "flute":        PropSpec(("竹箫", "箫", "笛"),         can_float=False, default_owner="杨旭", can_be_dropped=True),
    "lantern":      PropSpec(("灯笼",),                    can_float=False, default_owner="",     can_be_dropped=True),
    "lamp":         PropSpec(("灯盏", "油灯"),             can_float=False, default_owner="",     can_be_dropped=True),
    "stove":        PropSpec(("炭炉",),                    can_float=False, default_owner="",     can_be_dropped=True),
    "dagger":       PropSpec(("刺客匕首", "匕首", "短刃", "利刃", "寒光"), can_float=False, default_owner="刺客", can_be_dropped=False),
    "sword":        PropSpec(("剑", "长剑", "短剑"),       can_float=False, default_owner="",     can_be_dropped=False),
    "hairpin":      PropSpec(("步摇",),                    can_float=False, default_owner="听香", can_be_dropped=False),
    "boat":         PropSpec(("舴艋舟", "小舟", "木舟", "一叶舟", "扁舟"), can_float=True, default_owner="", can_be_dropped=False),
}

_PROP_NAME_TO_SPEC: dict[str, PropSpec] = {}
for _spec_key, _spec in PROP_REGISTRY.items():
    for _name in _spec.names:
        _PROP_NAME_TO_SPEC[_name] = _spec


def get_prop_spec(prop_name: str) -> PropSpec | None:
    """根据 prop 中文名查 PropSpec（O(1) 查表）."""
    return _PROP_NAME_TO_SPEC.get(prop_name)


def is_known_prop(prop_name: str) -> bool:
    """是否在 PROP_REGISTRY 登记的 prop（用于 extractor 跳过未知词）."""
    return prop_name in _PROP_NAME_TO_SPEC


POSE_REGISTRY: dict[str, Tuple[str, ...]] = {
    "reclining": (
        "仰卧", "仰面躺", "仰面倒", "俯卧", "侧卧",
        "昏迷", "熟睡", "装死", "闭目", "失神", "晕厥",
        "失血昏迷", "躺于", "仰躺", "侧躺",
    ),
    "sitting": ("盘坐", "盘膝", "跪坐", "端坐", "坐", "坐于", "落座", "入座", "就座"),
    "standing": ("站", "立", "立于", "站于", "站立", "直立"),
    "kneeling": ("跪倒", "瘫倒"),
    "in_motion": ("跃", "跃出", "破水", "举", "挥", "刺", "跃上", "倒纵", "扑"),
}


def classify_pose(text: str) -> str:
    """从文本判断姿态分类（返回 POSE_REGISTRY 的 key）.

    优先级：in_motion > reclining > sitting（含跪坐）> standing > kneeling（跪倒/瘫倒）> ""
    """
    if any(w in text for w in POSE_REGISTRY["in_motion"]):
        return "in_motion"
    if any(w in text for w in POSE_REGISTRY["reclining"]):
        return "reclining"
    if any(w in text for w in POSE_REGISTRY["sitting"]):
        return "sitting"
    if any(w in text for w in POSE_REGISTRY["standing"]):
        return "standing"
    if any(w in text for w in POSE_REGISTRY["kneeling"]):
        return "kneeling"
    return ""


_NON_SURFACE_ANCHORS = (
    "船舷左", "船舷右", "船舷", "桥栏", "桥墩",
    "前甲板", "后甲板",
    "桅杆",
)


def get_surfaces(location: str = "") -> Tuple[str, ...]:
    """根据 location 派生可用承重面（prop 落地时该往哪掉）."""
    surfaces: list[str] = []
    scene = get_scene_type(location) if location else ""

    if scene and is_horizontal_scene(location):
        for a in scene_anchors_for(location):
            if a not in _NON_SURFACE_ANCHORS:
                surfaces.append(a)
    elif scene and is_containing_scene(location):
        surfaces.extend(("案上", "桌上", "地面", "案前", "桌前"))
    else:
        surfaces.extend(("地面", "地上", "桌上", "案上"))

    for s in ("地面", "甲板", "地上", "案上", "桌上"):
        if s not in surfaces:
            surfaces.append(s)
    return tuple(surfaces)


INVALID_PROP_POSITIONS: Tuple[Tuple[str, str], ...] = (
    ("hang_outside_gunwale", r"(挂|吊|悬)在外?船舷(外|边缘)?"),
    # 斜伸出船舷（允许中间有"画左/画右/画中"方位词）
    ("floating_above_water", r"漂浮|悬空|斜伸出(画[左右中])?船舷"),
    ("hang_in_air",          r"悬[浮挂]在空[中上]"),
    ("lean_outside",         r"斜倚船舷外"),
)

_INVALID_PROP_POS_PATTERNS: dict[str, re.Pattern] = {
    name: re.compile(pat) for name, pat in INVALID_PROP_POSITIONS
}


def get_invalid_prop_patterns() -> dict[str, re.Pattern]:
    return _INVALID_PROP_POS_PATTERNS


ANCHOR_DEDUP_PHRASES: Tuple[str, ...] = (
    "披头散发", "散发", "一袭春衫", "素白轻袍", "素白色轻袍",
    "青衣小帽", "玄色劲装", "头插步摇", "蒙面", "黑发披散",
)


def get_anchor_dedup_phrases() -> Tuple[str, ...]:
    return ANCHOR_DEDUP_PHRASES


THREAT_KEYWORDS: Tuple[str, ...] = (
    "刺客", "神秘人", "黑衣人", "蒙面", "杀手", "黑影", "暗影",
    "蒙面剑客", "蒙面人", "匪",
)

WEAPON_KEYWORDS: Tuple[str, ...] = (
    "刀尖", "匕首", "短刃", "寒光", "刀", "剑", "利刃",
)

ABOVE_WATER_PAT = re.compile(r"露出水面|露头|仅头顶|仅露|约\s*2cm\s*露出|头顶正上方")


# ============================================================================
# 7. 攻击/动作关键词（Rule 6 时序塌缩、Rule 7 道具主谓歧义 共用）
# ============================================================================

ATTACK_KEYWORDS: Tuple[str, ...] = (
    # 攻击动作词
    "破水", "跃出", "破门", "一按船舷", "举刀", "挥刀", "挥砍", "直刺",
    "刺杀", "刺中", "刺入", "心口洇", "闷哼", "后仰", "推臂一送",
    "双腿一弹", "倒纵", "没入水中", "逃去", "奔远", "消失", "只剩涟漪",
    "已中刀", "已中剑", "已没入", "已倒下",
)

PRE_STATE_KEYWORDS: Tuple[str, ...] = (
    # pre-state（动作前一刻）触发词
    "半身探出", "破水", "举刀", "出剑", "跃起",
)

POST_STATE_KEYWORDS: Tuple[str, ...] = (
    # post-state（动作后）触发词
    "破门", "跃起", "出剑", "扑出", "倒地", "坠落", "被擒",
)

# "X手+prop" 模式（Rule 7 主谓歧义）的 prop 清单
# 包含 PROP_REGISTRY 中所有可被手持的道具 + 长武器（刀/剑/枪/锤/鞭）
HANDLE_PROPS: Tuple[str, ...] = (
    "竹箫", "酒壶", "酒杯", "钓杆", "油纸伞", "步摇", "灯盏", "灯笼",
    "炭炉", "刺客匕首", "刀", "剑", "枪", "锤", "鞭",
)

# 模糊方位词（Rule 9 替换）
VAGUE_DIRECTION_KW: dict[str, str] = {
    "回首": "侧头凝望画X的[角色名]",
    "抬头": "仰面朝上双目紧闭",
    "仰头": "仰面朝上双目紧闭",
    "抬眼": "目光紧盯[目标]",
    "凝神": "目光紧盯[目标]",
    "眺望": "目光紧盯[目标]",
    "抬头看": "目光紧盯[目标]",
    "仰头看": "目光紧盯[目标]",
}

# 模糊数量词 + 危险锚点（Rule 8 锚点位置约束）
ANCHOR_VAGUE_KW_PATTERN: re.Pattern = re.compile(
    r"(一点|一截|一缕|一丝|一道|一束|一柄)("
    + "|".join(re.escape(p) for p in
               ("刀尖", "刀", "剑", "匕首", "剑尖", "寒光", "黑影", "冷目", "刀光"))
    + r")"
)

# 抽象位置词（Rule 4 替换目标）—— 「其侧/身旁/旁边/身侧/近旁」
ABSTRACT_POSITION_WORDS: Tuple[str, ...] = (
    "其侧", "身旁", "旁边", "身侧", "近旁",
)

# 仰卧 + 头部固定检测（Rule 2）—— 同时是 POSE_REGISTRY["reclining"] 的子集
# 但保留为常量便于 Rule 2 单独检测（语义不完全相同）
# v6.50（08-29）收窄：去掉「闭目/失神」——它们是眼神/精神状态词，直立角色
# （俯首抹泪的听香）同样适用，匹配到会误拼「仰面朝上双目紧闭」进其子句
# （shot13.5 实测「不敢仰面朝上双目紧闭」）。头固定的语义前提是「躺/卧」姿态。
LYING_NO_HEAD_FIX: re.Pattern = re.compile(
    r"(仰卧|仰面朝上|仰躺|昏迷|熟睡|装死|失血昏迷)"
)

# 背对（Rule 5 threat facing 修复）
BACK_PATTERN: re.Pattern = re.compile(r"背对(镜头|画X|画左|画右|画中|目标|人)?")


def is_threat_name(name: str) -> bool:
    return any(kw in name for kw in THREAT_KEYWORDS)


def is_weapon_name(name: str) -> bool:
    return any(kw in name for kw in WEAPON_KEYWORDS)


# ============================================================================
# 8. 服装/发型/面部特征词表（vimax_bridge._inject_character_costume 共享）
# ============================================================================

# 服装字（用于判定 dynamic 是否需要"身着"连接器）
WARDROBE_WORDS: Tuple[str, ...] = ("袍", "衫", "衣", "裳", "裙", "袄", "裰", "服", "袈", "氅")

# 动态发型状态词（dynamic 含这些词 → 触发"披发覆盖"逻辑）
HAIR_STATE_WORDS: Tuple[str, ...] = (
    "披头散发", "披发", "散发", "乱发", "发髻散落", "蓬发", "黑发披散",
)

# 静态发型短语（与动态发型冲突时剔除）
HAIR_STATIC_PHRASES: Tuple[str, ...] = (
    "青丝挽美人髻", "黑发挽美人髻", "青丝盘起", "秀发高挽",
    "发髻高耸", "云鬓高挽", "挽发", "束发", "盘发",
    "美人髻", "发髻", "云鬓", "发髻高",
)

# 动物脸型关键词（用于追加"无兽耳/无角"护栏，防 Seedance 兽化幻觉）
ANIMAL_FACE_KEYWORDS: Tuple[str, ...] = (
    "鹿", "猫", "犬", "狐", "蛇", "狼", "虎", "鹰", "豹", "兽", "犄", "角",
)

# 动物脸型护栏全文（追加到角色 anchor 末尾）
NO_ANIMAL_FEATURES_GUARD: str = "（头面部无任何动物特征：无兽耳/无角/无犄角/无鳞/无羽毛）"

# "X系脸"动物比喻 → 具体人类五官词（v6.50，08-28）。
# 源头是 character_extractor 的脸型教学词表与 _FACE_SHAPE_MAPPING；比喻词
# （鹿系脸/猫系脸/狐系脸）会诱导图片模型画出兽耳兽脸（P23/v6.43 实测听香
# 被画成毛茸茸尖兽耳）。靠 NO_ANIMAL_FEATURES_GUARD 否定句对抗是下策——
# 注入期直接把比喻词替换为同等锁脸功能的人类五官词，替换后不含动物字根，
# 护栏自然不再触发，prompt 也更短。
ANIMAL_METAPHOR_FACE_REPLACEMENTS: Tuple[Tuple[str, str], ...] = (
    ("鹿系脸", "杏圆脸，大而清澈的杏眼"),
    ("猫系脸", "细长丹凤眼，眼尾微挑"),
    ("狐系脸", "眼尾微挑，尖下巴，眉眼妩媚"),
    ("天鹅脸", "修长鹅蛋脸，高鼻梁"),
    ("犬系脸", "方圆脸，眉眼憨厚"),
)


# ============================================================================
# 9. 致命伤锚词（validation._INJURY_ANCHOR_WORDS / _INJURY_ANCHOR_TAIL 共享）
# ============================================================================

# 致命伤锚词（用于致命伤持续可见约束）
INJURY_ANCHOR_WORDS: Tuple[str, ...] = (
    "心口", "洇痕", "暗红", "中刀", "白袍", "胸口", "心窝", "腹部",
    "中剑", "中枪", "暗伤", "剑伤", "刀伤", "溅血", "血痕", "血斑",
)

# 致命伤后追加的伤处描述模板（不与已有冲突、短小可叠加）
INJURY_ANCHOR_TAIL: str = "伤处白袍心口一片暗红血迹洇开，血痕清晰可见"

# 伤口类型词（validation _WOUND_ANCHOR_WORDS 共享）
WOUND_ANCHOR_WORDS: Tuple[str, ...] = (
    "剑伤", "刀伤", "枪伤", "中剑", "中刀", "中枪", "暗伤", "剑痕", "刀痕",
    "血痕", "血斑", "溅血", "血渍", "血迹",
)


# ============================================================================
# 11. 威胁源扩展词表（validation._THREAT_WORDS / _THREAT_FACING_WORDS 等）
# ============================================================================

# 威胁源词（含合规替换后的"神秘人" + 行为动词"潜伏/埋伏/伏击" + 持刀等）
# 比 THREAT_KEYWORDS 更宽：THREAT_KEYWORDS 仅角色名；THREAT_WORDS_ENLARGED
# 含行为/装备/状态词，用于"该文本是否提及威胁源"判定。
THREAT_WORDS_ENLARGED: Tuple[str, ...] = (
    "刺客", "杀手", "偷袭者", "持刀", "黑衣人", "黑影",
    "潜伏", "埋伏", "伏击", "神秘人", "蒙面人",
)

# 威胁源"面部朝向"词（v6.25 规则41：发现者视线-威胁方位耦合）
THREAT_FACING_WORDS: Tuple[str, ...] = (
    "持刀", "蒙面", "蒙面剑客", "杀气", "阴鸷",
    "正对", "背对镜头", "侧身",
)

# 威胁源"消失"词（v6.25 规则41 延伸：刺客没入水中后不需再朝向目标）
THREAT_GONE_WORDS: Tuple[str, ...] = (
    "已没入", "没入水中", "没入水下", "沉入水底", "消失", "不见踪影",
)

# 威胁源"刚出水面/半身探出"类（kf pre-stage 检测用）
THREAT_WATER_WORDS: Tuple[str, ...] = (
    "刚出水面", "半身探出", "破水而出", "窜出", "浮出", "半身破水",
)

# 威胁源攻击动作（找动作目标角色）
THREAT_STAB_VERBS: Tuple[str, ...] = (
    "刺", "劈", "扑", "砍", "斩", "捅", "扎", "挥",
)

# 强动作词（v6.25 通用：检测镜中是否有"动作正在进行"）
ACTION_STRONG_VERBS: Tuple[str, ...] = (
    "跃出", "冲出", "扑向", "扑上", "刺向", "刺入", "劈砍", "挥刀", "拔剑",
    "转身", "回头", "抬头", "低头", "起身", "站起", "坐下", "跪倒", "摔倒",
    "按", "抽", "甩", "挥", "刺", "砍", "劈", "踢", "蹬", "窜", "跃", "扑",
)

# 威胁源"可见性"特征词（vimax_bridge._threat_visible_in 用：判断威胁源是否在画面中可见）
THREAT_VISIBLE_HINTS: Tuple[str, ...] = (
    "蒙面", "持刀", "持剑", "刀", "剑", "手", "眼", "脸", "侧身", "黑衣",
    "杀意", "杀气", "潜伏", "潜伏水下", "半身探出", "破水",
)


# ============================================================================
# 10. 通用人物名池（gaze_direction 兜底）
# ============================================================================

# 当 char_layout 不提供角色名时，用此兜底（兼容旧测试 / 单镜无 char_layout 场景）
COMMON_PERSON_NAMES: Tuple[str, ...] = (
    "杨旭", "听香", "刺客", "张十三", "老汉", "肖管事", "神秘人",
    "那女子", "女子", "那男子", "男子", "公子", "姑娘",
)


# ============================================================================
# 12. 受害者伤征+死亡征词（v6.44 W15 hook 专用）
# ============================================================================
# 判定"受害者已死/被刺"——与 INJURY_ANCHOR_WORDS（伤口持续可见）正交：
# INJURY_ANCHOR_WORDS 是"伤处描述"；VICTIM_SIGNS 是"死亡/被刺的当下动作"，
# 多了「插着/被刺/已死/身亡」这类死亡动作词。
VICTIM_SIGNS: Tuple[str, ...] = (
    "胸口插着", "心口插着", "胸口赫然", "胸口白袍暗红", "心口白袍暗红",
    "胸口暗红", "胸口洇痕", "心口洇痕", "胸口被刺", "心口被刺",
    "被刺", "已死", "身亡", "死去",
)


# ============================================================================
# 13. 武器可触部位词（v6.44 W15 hook 专用）
# ============================================================================
# 判定"手部伸向凶器"——与 WEAPON_KEYWORDS（武器名词）正交：
# WEAPON_KEYWORDS 是"武器名"（刀/剑/匕首）；WEAPON_HANDLE_WORDS 是"武器上
# 角色可能手部触碰的部位"（刀柄/剑柄/刀刃）+ 兜底词（凶器/短刀/长刀）。
# 新武器部位只改本表。
WEAPON_HANDLE_WORDS: Tuple[str, ...] = (
    "刀柄", "剑柄", "刀身", "刀刃", "刀锋", "剑身", "剑刃",
    "短刀", "长刀", "凶器", "刀鞘",
)


# ============================================================================
# 14. 画面方位/方向词（facing_to_gaze 等用：区分"面向人名"vs"面向方向"）
# ============================================================================
# facing_to_gaze 修复要求：char_layout facing="面向X" 里 X 是人名而非方向词。
# 若 X 命中本表（画左/画右/...）→ 跳过（不需要追加"目光凝望X"）
DIRECTION_WORDS: Tuple[str, ...] = (
    "画左", "画右", "画中", "画上", "画下", "画上方", "画下方",
    "船头", "船尾", "镜头", "观众",
)


# ============================================================================
# 15. main_subject 动作/状态前缀词（injury_continuity 等用：剥离前缀提取 victim）
# ============================================================================
# "刺杀杨旭与听香" → 剥离"刺杀"剩"杨旭与听香"；"倒地刺客" → 剥离"倒地"剩"刺客"。
# 注意：与 THREAT_KEYWORDS 互补——THREAT_KEYWORDS 整名匹配；本表用于 .replace() 剥离。
MAIN_SUBJECT_PREFIXES: Tuple[str, ...] = (
    "刺杀", "被刺", "倒地", "躺血", "奄奄一息", "濒死", "受伤", "倒下",
    "刺客", "神秘人", "黑影", "破水", "出水", "破门", "夜袭",
)


# ============================================================================
# 16. 严格致命伤锚词（injury_continuity 收紧判定专用）
# ============================================================================
# INJURY_ANCHOR_WORDS 的"严格子集"——判定"真有致命伤建立"，避免"暗红/白袍"
# 单独出现时误判（听香手指沾血/衣服打湿都会让 INJURY_ANCHOR_WORDS 误命中）。
# 必须含"洇痕/中刀/中剑/中枪/刀伤/剑伤"才算真致命伤锚定。
STRONG_INJURY_ANCHORS: Tuple[str, ...] = (
    "洇痕", "中刀", "中剑", "中枪", "刀伤", "剑伤",
    "血痕", "血斑", "溅血", "中伤", "倒卧", "倒地",
)


__all__ = [
    "PROP_REGISTRY", "PropSpec", "get_prop_spec", "is_known_prop",
    "POSE_REGISTRY", "classify_pose",
    "get_surfaces",
    "INVALID_PROP_POSITIONS", "get_invalid_prop_patterns",
    "ANCHOR_DEDUP_PHRASES", "get_anchor_dedup_phrases",
    "THREAT_KEYWORDS", "WEAPON_KEYWORDS", "ABOVE_WATER_PAT",
    "COMMON_PERSON_NAMES",
    "VICTIM_SIGNS", "WEAPON_HANDLE_WORDS", "DIRECTION_WORDS",
    "MAIN_SUBJECT_PREFIXES", "STRONG_INJURY_ANCHORS",
    "is_threat_name", "is_weapon_name",
]
