"""故事板数据模型 — 类型定义和请求/响应结构"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StoryboardType(str, Enum):
    """7种故事板类型"""

    PER_SHOT = "per_shot"  # 逐镜执行故事板
    CHARACTER_SET = "character_set"  # 角色设定故事板
    SCENE_FIXED = "scene_fixed"  # 场景固定故事板
    SCENE_PLAN = "scene_plan"  # 场景规划故事板
    MOTION_KEYFRAMES = "motion_keyframes"  # 动作拆解故事板
    MOTION_DESIGN = "motion_design"  # 动作设计故事板
    SCENE_OVERVIEW = "scene_overview"  # 场景概览故事板


# 景别枚举
class ShotSize(str, Enum):
    EXTREME_WIDE = "extreme_wide"  # 超远景
    WIDE = "wide"  # 远景
    FULL = "full"  # 全景
    MEDIUM_WIDE = "medium_wide"  # 中全景
    MEDIUM = "medium"  # 中景
    MEDIUM_CLOSE = "medium_close"  # 中近景
    CLOSE_UP = "close_up"  # 特写
    EXTREME_CLOSE_UP = "extreme_close_up"  # 大特写


# 运镜枚举
class CameraMovement(str, Enum):
    STATIC = "static"  # 静止
    PAN_LEFT = "pan_left"  # 左摇
    PAN_RIGHT = "pan_right"  # 右摇
    TILT_UP = "tilt_up"  # 仰拍
    TILT_DOWN = "tilt_down"  # 俯拍
    DOLLY_IN = "dolly_in"  # 推进
    DOLLY_OUT = "dolly_out"  # 拉出
    TRACK_LEFT = "track_left"  # 左移
    TRACK_RIGHT = "track_right"  # 右移
    CRANE_UP = "crane_up"  # 升
    CRANE_DOWN = "crane_down"  # 降
    ROTATE = "rotate"  # 旋转
    HANDHELD = "handheld"  # 手持


# 光影风格枚举
class LightingStyle(str, Enum):
    NATURAL = "natural"  # 自然光
    CHIAROSCURO = "chiiaroscuro"  # 明暗对比（伦勃朗光）
    BACKLIGHT = "backlight"  # 逆光
    SOFT_FILL = "soft_fill"  # 柔光
    HARD_DIRECT = "hard_direct"  # 硬光
    CANDLELIGHT = "candlelight"  # 烛光
    MOONLIGHT = "moonlight"  # 月光
    SUNSET = "sunset"  # 日落光


@dataclass
class CharacterSpec:
    """角色规格（用于角色设定故事板）"""

    name: str  # 角色名
    description: str  # 角色外貌描述
    features_static: str = ""  # 静态特征（服饰、发型等）
    features_dynamic: str = ""  # 动态特征（表情、习惯动作）
    personality: str = ""  # 性格特点（影响动作设计）


@dataclass
class SceneSpec:
    """场景规格（用于场景固定/规划/概览故事板）"""

    name: str  # 场景名
    description: str  # 场景描述
    time_period: str = ""  # 时间段（晨/昏/夜等）
    mood: str = ""  # 氛围（阴森、温馨等）
    props: list[str] = field(default_factory=list)  # 关键道具列表
    layout: str = ""  # 布局描述（用于规划/概览）


@dataclass
class MotionSpec:
    """动作规格（用于动作拆解/设计故事板）"""

    name: str  # 动作名
    description: str  # 动作描述
    keyframes: list[str] = field(default_factory=list)  # 关键帧描述列表
    duration_seconds: float = 3.0  # 动作时长
    difficulty: str = "medium"  # 难度（easy/medium/hard）


@dataclass
class PerShotSpec:
    """逐镜参数（用于逐镜执行故事板）"""

    shot_index: int
    duration_seconds: float
    shot_size: ShotSize
    camera_movement: CameraMovement
    lighting: LightingStyle
    narration: str = ""  # 旁白/台词
    sound_effect: str = ""  # 音效描述
    prompt_supplement: str = ""  # 额外提示词补充


@dataclass
class StoryboardRequest:
    """故事板生成请求"""

    storyboard_type: StoryboardType
    # 角色相关（角色设定/动作设计需要）
    characters: list[CharacterSpec] = field(default_factory=list)
    # 场景相关（场景类故事板需要）
    scene: Optional[SceneSpec] = None
    # 动作相关（动作类故事板需要）
    motion: Optional[MotionSpec] = None
    # 逐镜参数列表（逐镜执行故事板需要）
    shots: list[PerShotSpec] = field(default_factory=list)
    # 风格锚点（从 style_bank 加载）
    style_reference: str = ""
    # 额外描述（补充说明）
    extra_description: str = ""


@dataclass
class StoryboardImage:
    """故事板生成的单个图片"""

    name: str  # 图片名称（如 "front_view", "keyframe_01"）
    prompt: str  # 使用的 prompt
    save_path: Optional[str] = None  # 保存路径
    success: bool = False
    error: Optional[str] = None


@dataclass
class StoryboardResult:
    """故事板生成结果"""

    storyboard_type: StoryboardType
    images: list[StoryboardImage] = field(default_factory=list)
    # 逐镜执行故事板：包含完整的参数表
    shot_parameters: list[PerShotSpec] = field(default_factory=list)
    # 元数据
    success: bool = False
    error: Optional[str] = None

    @property
    def image_count(self) -> int:
        return len([i for i in self.images if i.success])

    @property
    def all_success(self) -> bool:
        return self.success and all(i.success for i in self.images)