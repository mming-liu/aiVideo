"""scene_anchors.py — 场景空间锚点单一事实源.

**设计目标**：让 prompt / 校验器 / 硬修复层 / 未来新场景扩展共用同一份词表。
加新场景（如「马车」「桥」「厅堂」）：只在本文件加一行 + 一行 HORIZONTAL/CONTAINING 标记。

用法：
  - 校验器：`is_valid_position(pos, location)` 替 `_STAGING_POS_WORDS`
  - 校验器：`is_valid_facing(facing)` 替 `_STAGING_FACING_WORDS`（朝向词表稳定，仍在本文件）
  - 硬修复：`_FF_ABSTRACT_TO_CONCRETE` 改用 `scene_anchors_for(location)` 查表
  - prompt：system.md 末尾告诉 LLM 「完整场景锚点见 step01_script_gen/scene_anchors.py」

幂等：本文件纯函数 + 常量，无副作用.
"""
from __future__ import annotations

from typing import Tuple


# ============================================================================
# 1. 基础空间词（任何场景都合法）—— 画X + 景深
# ============================================================================

BASE_POS_WORDS: Tuple[str, ...] = (
    "画左", "画右", "画中", "画上方", "画下方",
    "前景", "中景", "后景",
)

# 远景/画远处是合法的构图位置提示，不应被当成“缺空间词”。
# 09-19 真实产物「群演 站位 画远处」曾被 multi_person_staging 误报。
DISTANT_POS_WORDS: Tuple[str, ...] = (
    "画远处", "远处", "远景", "背景", "深处",
)


# 朝向词（stable：任何角色任何场景都适用）
BASE_FACING_WORDS: Tuple[str, ...] = (
    "面向", "背对", "侧身", "正对", "面朝", "朝向",
)


# ============================================================================
# 2. 场景具象空间词字典（新增场景在此加一行）
# ============================================================================

# scene_key -> 该场景下"具象空间词"列表（写进 char_layout.position 算合法）
SCENE_ANCHORS: dict[str, Tuple[str, ...]] = {
    # 横向工具（"船头→船中→船尾"天然三段纵深，不需画X）
    "船":   ("船头", "船中", "船尾", "船舷左", "船舷右", "船舷",
             "前甲板", "后甲板", "船舱", "桅杆", "船头甲板", "船尾甲板",
             "船梢",  # 船尾/船梢 同义
             "船首",  # 船头/船首 同义
             "舱内", "中舱", "船头板", "船尾板", "船舷内"),
    "桥":   ("桥头", "桥中", "桥尾", "桥栏", "桥墩", "桥面", "桥上", "桥下"),
    "街道": ("街口", "街中", "路中", "巷口", "巷尾", "墙根", "檐下"),
    "马车": ("车头", "车辕", "车板", "车尾", "车帘", "车把", "左轮", "右轮",
             "车夫座", "车厢", "车厢内", "前座", "后座", "车篷"),
    "山道": ("山道", "山腰", "山巅", "山脚"),

    # 围合空间（必须靠前景/中景/后景拉开纵深，具象词只是位置不是纵深）
    "厅堂": ("正厅", "侧厅", "前厅", "后厅", "左廊", "右廊", "门槛", "梁下", "柱旁"),
    "院落": ("院门", "正堂", "东厢", "西厢", "影壁", "院中", "檐下", "廊下", "天井"),
    "室内": ("桌前", "案前", "床前", "椅中", "窗前", "门后", "帘后"),
    "殿":   ("殿前", "殿中", "殿后", "丹墀", "御座", "金阶"),
}


# ============================================================================
# 3. 场景类型分类（决定纵深判定走哪个分支）
# ============================================================================

# 横向场景：「船头/船中/船尾」自然构成三段纵深，校验器不报"全同侧无纵深"
HORIZONTAL_SCENES: frozenset[str] = frozenset({"船", "桥", "街道", "马车", "山道"})

# 围合场景：必须显式用"前景/中景/后景"才算拉开纵深
CONTAINING_SCENES: frozenset[str] = frozenset({"厅堂", "院落", "室内", "殿"})


# ============================================================================
# 4. 公共 API
# ============================================================================

def get_scene_type(location: str) -> str:
    """从 location 字段（如 "舴艋舟"/"花厅"/"小巷"）反查场景类.

    匹配规则（按优先级）：
      1. 关键字（kw in location）：先匹配具体场景
      2. 同义词（舟→船、街→街道、堂→厅堂、屋→室内、殿→殿）
      3. 具象词 in location（如 location="湖心舴艋舟"，含 "船舷" → 船）
      4. 兜底：返回 ""（抽象场景，走通用画X 判定）
    """
    if not location:
        return ""
    loc = str(location)
    # 1) 关键字匹配（更精确，先做）
    for kw in SCENE_ANCHORS.keys():
        if kw in loc:
            return kw
    # 2) 同义词映射（舟=船、街=街道、堂=厅堂、屋=室内、殿=殿、舍=院落、桥=桥）
    _SYNONYMS = {
        "船": ("舟", "舴艋", "渔舟", "画舫", "轻舟", "扁舟"),
        "桥": ("拱桥", "石桥", "木桥"),
        "街道": ("街", "巷", "道", "路"),
        "厅堂": ("堂", "厅", "花厅", "正堂", "大堂"),
        "院落": ("院", "舍", "庄", "府"),
        "室内": ("屋", "室", "房", "阁", "楼"),
        "殿": ("宫殿", "金殿", "大殿"),
    }
    for scene, syns in _SYNONYMS.items():
        if any(s in loc for s in syns):
            return scene
    # 3) 具象词匹配（location 含 "船舷" 也算船场景）
    for kw, anchors in SCENE_ANCHORS.items():
        if any(a in loc for a in anchors):
            return kw
    return ""


def scene_anchors_for(location: str) -> Tuple[str, ...]:
    """查 location 场景对应的具象空间词元组.

    返回空 tuple 表示「抽象场景」/「未知场景」—— 校验器只认 BASE_POS_WORDS.
    """
    scene = get_scene_type(location)
    if not scene:
        return ()
    return SCENE_ANCHORS.get(scene, ())


def is_horizontal_scene(location: str) -> bool:
    """横向场景（船/桥/街道等）：船头/船中/船尾天然三段纵深."""
    scene = get_scene_type(location)
    return scene in HORIZONTAL_SCENES


def is_containing_scene(location: str) -> bool:
    """围合场景（厅堂/院落/室内）：必须前/中/后景."""
    scene = get_scene_type(location)
    return scene in CONTAINING_SCENES


def is_valid_position(pos: str, location: str = "") -> bool:
    """校验器用：char_layout.position 是否合法空间词.

    合法当且仅当（任一）：
      ① 含 BASE_POS_WORDS 之一（画X/前景/中景/后景）
      ② 或含 location 场景对应的 SCENE_ANCHORS 之一（船头/船尾/船舷 等具象词）
      ③ 或 pos 自身含任意 SCENE_ANCHORS 的 anchor（兜底：
         LLM 把「舴艋舟」类场景名写进 location 时可能用「云河镇照月湾」，
         此时 pos 里的「船头/船中/船尾」已是合法空间锚点，无视 location 不匹配）

    空字符串视为合法（角色暂无站位，由 LLM 后续补，校验不刷屏）.
    """
    if not pos:
        return True
    if any(k in pos for k in BASE_POS_WORDS):
        return True
    if any(k in pos for k in DISTANT_POS_WORDS):
        return True
    # location 场景的具象词
    for anchor in scene_anchors_for(location):
        if anchor in pos:
            return True
    # 兜底：pos 自带任何场景的 anchor（防止 location 字段与 position 场景不一致）
    for anchors in SCENE_ANCHORS.values():
        if any(a in pos for a in anchors):
            return True
    return False


def is_valid_facing(facing: str) -> bool:
    """校验器用：char_layout.facing 是否合法朝向词.

    朝向词表稳定，不分场景——任何角色任何场景都通用.
    空字符串视为合法.
    """
    if not facing:
        return True
    return any(k in facing for k in BASE_FACING_WORDS)


def has_lateral_depth(positions: list[str], location: str = "") -> bool:
    """校验器用：多角色站位是否已拉开纵深.

    横向场景（船/桥/街道）：只要 ≥2 个不同具象词（如 船头/船尾/船中）就算有纵深
    围合场景：必须含 BASE_POS_WORDS 的前/中/后景
    抽象场景：必须有画X 之外的纵深词
    兜底：pos 自带任何场景 anchor 时也按横向场景判定（location 与 position 场景不一致时）.
    """
    if not positions:
        return False
    # 1) 显式景深词（任何场景通用）
    if any(any(k in p for k in ("前景", "中景", "后景")) for p in positions):
        return True
    if any("画中" in p for p in positions):
        return True
    # 2) 横向场景：具象词差异化
    # 优先用 location 场景查表；location 兜不住时用 pos 自带 anchor 推断
    scene = get_scene_type(location)
    if scene in HORIZONTAL_SCENES:
        anchors = SCENE_ANCHORS[scene]
    else:
        # 兜底：pos 自带横向 anchor → 按横向判定
        scene = _infer_scene_from_positions(positions)
        anchors = SCENE_ANCHORS.get(scene, ()) if scene in HORIZONTAL_SCENES else ()
    if anchors:
        present = {a for p in positions for a in anchors if a in p}
        return len(present) >= 2
    # 3) 围合/抽象场景：必须有显式景深（已上面 if 过）
    return False


def _infer_scene_from_positions(positions: list[str]) -> str:
    """从 positions 字符串数组反推场景（location 缺失或不匹配时用）.

    规则：找到任一 anchor，归属到其 SCENE_ANCHORS key（按字典序，先匹配先返回）.
    """
    for scene, anchors in SCENE_ANCHORS.items():
        for p in positions:
            if any(a in p for a in anchors):
                return scene
    return ""


# ============================================================================
# 5. 道具位置 hint（通用化，迁移自 _PROP_POS_FALLBACK）
# ============================================================================

# 道具 → 相对位置（场景无关部分）
# 例：步摇→发间（人像固定）/ 油纸伞→撑开（道具自描述）
_PROP_DEFAULT_POSITIONS: dict[str, str] = {
    "步摇": "发间",
    "油纸伞": "画中",
    "竹箫": "画中",
    "钓杆": "画中",
    "刺客匕首": "画中",
    "酒壶": "画中",
    "酒杯": "画中",
    "灯盏": "画中",
    "灯笼": "画中",
    "炭炉": "画中",
    "叉子粪铲": "画右",
    "农具": "画右",
}

# 道具 → 横向场景专属位置（is_horizontal_scene 时优先用这个）
# 注意：保留「画中」前缀以兼容 _PROP_POS_FALLBACK 旧行为（炭炉→画中甲板）
_PROP_HORIZONTAL_POSITIONS: dict[str, str] = {
    "舴艋舟": "画中水面",
    "炭炉": "画中甲板",
    "酒壶": "画中甲板",
    "灯盏": "画中船头",
    "灯笼": "画中岸边",
    "钓杆": "画右船舷",
    "马车": "画中车辕",
}


def get_prop_position_hint(prop: str, location: str = "") -> str:
    """根据道具 + 场景位置，返回应追加的方位 hint.

    优先级：
      1) 横向场景 + 道具在 _PROP_HORIZONTAL_POSITIONS → 场景专属位置（如 画中甲板/画中船头）
      2) 道具在 _PROP_DEFAULT_POSITIONS → 通用位置（如 发间/画中/画右）
      3) 兜底：横向场景 → 第 2 个 anchor（船中/桥中/街中）；围合/抽象 → 画中

    返回纯位置字符串（无括号），调用方自行加「（位置）」格式.
    """
    # 1) 横向场景专属
    if is_horizontal_scene(location) and prop in _PROP_HORIZONTAL_POSITIONS:
        return _PROP_HORIZONTAL_POSITIONS[prop]
    # 2) 道具通用位置
    if prop in _PROP_DEFAULT_POSITIONS:
        return _PROP_DEFAULT_POSITIONS[prop]
    # 3) 兜底
    if is_horizontal_scene(location):
        anchors = scene_anchors_for(location)
        if len(anchors) >= 2:
            return f"画中{anchors[1]}"
    return "画中"


# ============================================================================
# 6. 道具空间动词（system.md 50a + hard_fix Rule 7 配套）
# ============================================================================

# 角色躺/卧/坐姿态下，道具**脱手/非握**的合法描述动词
# 按物理场景分类，prompt 用以替代"X手+道具"歧义写法
SPATIAL_RELEASE_VERBS: dict[str, tuple[str, ...]] = {
    # 默认（无场景特定）
    "_default": (
        "已脱手", "已倾倒", "搁在", "放在", "倚在", "靠着", "挂在",
        "搭在", "枕在", "压在", "落在", "搁在身侧", "倒在身侧",
    ),
    # 船/水场景
    "船": (
        "已脱手滑落", "斜伸出船舷没入水面", "已倾倒挂在船舷",
        "搁在画左甲板", "搁在画右甲板", "漂浮在水面", "沉入水底",
        "斜倚在桅杆", "倒在船舱",
    ),
    # 桥/街道/户外
    "桥": ("已脱手落在桥面", "斜倚桥栏", "倒在路中"),
    "街道": ("已脱手落在地上", "倚在墙根", "倒在巷口", "搁在檐下"),
    # 室内
    "室内": ("已脱手搁在桌上", "倒在案前", "倚在床前", "挂在帘后"),
    "厅堂": ("已脱手搁在案上", "倒在柱旁", "倚在梁下"),
    "院落": ("已脱手落在院中", "倚在廊柱", "倒在影壁前"),
    "殿": ("已脱手落在丹墀", "倚在金阶", "倒在御座前"),
    # 马车
    "马车": ("已脱手落在车板", "斜倚车帘", "倒在车辕"),
}


def get_spatial_release_verbs(location: str = "") -> tuple[str, ...]:
    """返回该场景下"道具脱手"的合法动词列表（system.md 50a 配套）.

    横向场景（如船）返回场景专属动词（"斜伸出船舷没入水面"等）；
    围合/抽象场景返回 _default 列表.
    """
    if location and get_scene_type(location) in SPATIAL_RELEASE_VERBS:
        scene = get_scene_type(location)
        return SPATIAL_RELEASE_VERBS[scene] + SPATIAL_RELEASE_VERBS["_default"]
    return SPATIAL_RELEASE_VERBS["_default"]


# 模糊方位词 → 显式视线（system.md 50a + hard_fix Rule 9 配套）
VAGUE_DIRECTION_REPLACEMENTS: dict[str, str] = {
    "回首": "侧头凝望画左的[角色名]",
    "抬头": "仰面朝上双目紧闭",
    "仰头": "仰面朝上双目紧闭",
    "抬眼": "目光紧盯[目标]",
    "凝神": "目光紧盯[目标]",
    "眺望": "目光紧盯[目标]",
}


__all__ = [
    "BASE_POS_WORDS",
    "BASE_FACING_WORDS",
    "SCENE_ANCHORS",
    "HORIZONTAL_SCENES",
    "CONTAINING_SCENES",
    "get_scene_type",
    "scene_anchors_for",
    "is_horizontal_scene",
    "is_containing_scene",
    "is_valid_position",
    "is_valid_facing",
    "has_lateral_depth",
    "get_prop_position_hint",
    "SPATIAL_RELEASE_VERBS",
    "get_spatial_release_verbs",
    "VAGUE_DIRECTION_REPLACEMENTS",
]
