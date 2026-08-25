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
