from .types import Severity, Violation, RepairRecord, RepairReport
from .context import ConstraintContext
from .base import Constraint
from .engine import apply_constraints, run_hard_fix, ALL_CONSTRAINTS

# ============================================================================
# Constraint 子类注册（按"迁移批次"分组，每条都已通过单测）
# ============================================================================

# Phase 2 迁移：A/B/C/D 4 个原始约束
from .injury_continuity import InjuryContinuityConstraint  # A
from .gaze_direction import GazeDirectionConstraint  # B
from .prop_position import PropPositionConstraint  # C
from .kf_pre_post_conflict import KfPrePostConflictConstraint  # D

# Phase 3 迁移：E/F
from .kf_time_window_alignment import KfTimeWindowAlignmentConstraint  # E
from .kf_t_sec_bounds import KfTSecBoundsConstraint  # F
from .kf_monotonic import KfMonotonicConstraint  # F1 (v6.43, 08-23)

# Phase 4 迁移：G~Z + T1/T2（08-22 ~ 08-23 跑批累计）
from .axis_jump import AxisJumpConstraint  # G
from .boat_crop import BoatCropConstraint  # H
from .boat_width import BoatWidthConstraint  # I
from .bridge_words import BridgeWordsConstraint  # J
from .camera_variety import CameraVarietyConstraint  # K
from .char_layout_sync import CharLayoutSyncConstraint  # L
from .climax_distribution import ClimaxDistributionConstraint  # M
from .combat_impact import CombatImpactConstraint  # N
from .facing_to_gaze import FacingToGazeConstraint  # O
from .framing_diversity_v2 import FramingDiversityV2Constraint  # P
from .framing_jump import FramingJumpConstraint  # Q
from .hook_duration import HookDurationConstraint  # R
from .lf_desc_length import LfDescLengthConstraint  # S
from .lf_midstate import LfMidstateConstraint  # T
from .multi_char_layout_depth import MultiCharLayoutDepthConstraint  # U
from .narration_subtitle_consistency import NarrationSubtitleConsistencyConstraint  # V
from .opening_hook import OpeningHookConstraint  # W
from .platform_compliance import PlatformComplianceConstraint  # X
from .prompt_repetition import PromptRepetitionConstraint  # Y
from .punch_density import PunchDensityConstraint  # Z
from .runtime_extend import RuntimeExtendConstraint  # W6（红果时长拉伸，HARD）
from .threat_gaze_coupling import ThreatGazeCouplingConstraint  # T1
from .transition_keywords import TransitionKeywordsConstraint  # T3
from .self_review import SelfReviewConstraint  # SR（LLM 自检回路）
from .v6_43_prompt_enforcement import V643PromptEnforcementConstraint  # v6.43 (08-23 强化)
from .kf_weak_template import KfWeakTemplateConstraint  # W16 kf弱模板占位（v6.45, 08-26）
from .camera_movement_fill import CameraMovementFillConstraint  # J3 运镜缺失补填（v6.47, 08-27）
from .kf_blood_monotonic import KfBloodMonotonicConstraint  # F2 kf 血状态单调（v6.49, 08-28）
from .ff_motion_overlap import FfMotionOverlapConstraint  # F3 ff_desc 动作重叠（v6.49, 08-28）
from .kf_pose_anchor import KfPoseAnchorConstraint  # F4 kf 姿态+位置+威胁朝向（v6.49, 08-28）
from .redguo_scene_char_limit import RedguoSceneCharLimitConstraint  # F5 红果场景/人物硬上限（v6.49, 08-28）
from .post_combat_blank import PostCombatBlankConstraint  # F6 打斗战后空镜收尾（v6.49, 08-28）
from .restrained_emotion_chain import RestrainedEmotionChainConstraint  # F7 克制情绪时间链（v6.49, 08-28）
from .climax_priority import ClimaxPriorityConstraint  # F8 高潮优先（v6.49, 08-28）
from .short_shot_merge import ShortShotMergeConstraint  # F9 短镜合并（v6.49, 08-28）
from .ff_lf_temporal import FfLfTemporalConstraint  # F10 ff/lf 时序协议（v6.50, 08-28）
from .frame_spec_consistency import FrameSpecConsistencyConstraint  # S v6.57+ per-frame spec 一致性
from .self_review import SelfReviewConstraint  # SR（LLM 自检回路）

ALL_CONSTRAINTS.extend([
    # Phase 2
    InjuryContinuityConstraint(),
    GazeDirectionConstraint(),
    PropPositionConstraint(),
    KfPrePostConflictConstraint(),
    # Phase 3
    KfTimeWindowAlignmentConstraint(),
    KfTSecBoundsConstraint(),
    KfMonotonicConstraint(),
    # Phase 4
    AxisJumpConstraint(),  # G HARD
    BoatCropConstraint(),
    BoatWidthConstraint(),
    BridgeWordsConstraint(),
    CameraVarietyConstraint(),
    CharLayoutSyncConstraint(),
    ClimaxDistributionConstraint(),
    CombatImpactConstraint(),
    FacingToGazeConstraint(),
    FramingDiversityV2Constraint(),
    FramingJumpConstraint(),
    HookDurationConstraint(),
    LfDescLengthConstraint(),
    LfMidstateConstraint(),
    MultiCharLayoutDepthConstraint(),
    NarrationSubtitleConsistencyConstraint(),
    OpeningHookConstraint(),
    # v6.43（08-23 审计）：PlatformCompliance 必须在所有 prompt 追加类
    # 约束（bridge_words / transition_keywords / prompt_repetition）之后
    # 执行，否则「（AI生成）」会被推挤到 prompt 中段。
    TransitionKeywordsConstraint(),
    PromptRepetitionConstraint(),
    RuntimeExtendConstraint(),  # HARD
    ThreatGazeCouplingConstraint(),
    PlatformComplianceConstraint(),
    V643PromptEnforcementConstraint(),  # v6.43 prompt 强化（早执行，独立于 LLM）
    KfWeakTemplateConstraint(),  # W16 kf弱模板占位（v6.45, 08-26）
    CameraMovementFillConstraint(),  # J3 运镜缺失补填（v6.47, 08-27）
    KfBloodMonotonicConstraint(),  # F2 kf 血状态单调（v6.49, 08-28）
    FfMotionOverlapConstraint(),  # F3 ff_desc 动作重叠（v6.49, 08-28）
    KfPoseAnchorConstraint(),  # F4 kf 姿态+位置+威胁朝向（v6.49, 08-28）
    RedguoSceneCharLimitConstraint(),  # F5 红果场景/人物硬上限 HARD（v6.49, 08-28）
    PostCombatBlankConstraint(),  # F6 打斗战后空镜收尾（v6.49, 08-28）
    RestrainedEmotionChainConstraint(),  # F7 克制情绪时间链（v6.49, 08-28）
    ClimaxPriorityConstraint(),  # F8 高潮优先（v6.49, 08-28）
    ShortShotMergeConstraint(),  # F9 短镜合并 HARD（v6.49, 08-28）
    FfLfTemporalConstraint(),  # F10 ff 时序协议：本镜有攻击时 ff 禁结束态（v6.50, 08-28）
    FrameSpecConsistencyConstraint(),  # S per-frame spec 必填 + 完整性 + desc 一致性（v6.57+）
    SelfReviewConstraint(),  # SR opt-in via SELF_REVIEW_ENABLED=1
])

__all__ = [
    "Severity",
    "Violation",
    "RepairRecord",
    "RepairReport",
    "ConstraintContext",
    "Constraint",
    "apply_constraints",
    "run_hard_fix",
    "ALL_CONSTRAINTS",
]
