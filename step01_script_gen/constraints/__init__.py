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
from .kf_timeline_normalize import KfTimelineNormalizeConstraint  # kf 时间线归一（2026-09-12）
from .duplicate_shot_merge import DuplicateShotMergeConstraint  # 相邻镜重复视觉合并（2026-09-12）
from .char_side_continuity import CharSideContinuityConstraint  # 同地点角色侧翻转交换（2026-09-12）
from .kf_monotonic import KfMonotonicConstraint  # F1 (v6.43, 08-23)

# Phase 4 迁移：G~Z + T1/T2（08-22 ~ 08-23 跑批累计）
from .axis_jump import AxisJumpConstraint  # G
from .boat_crop import BoatCropConstraint  # H
from .boat_width import BoatWidthConstraint  # I
from .bridge_words import BridgeWordsConstraint  # J
from .camera_variety import CameraVarietyConstraint  # K
from .char_layout_sync import CharLayoutSyncConstraint  # L
from .climax_distribution import ClimaxDistributionConstraint  # M
from .core_keyword_coverage import CoreKeywordCoverageConstraint
from .frame_spec_repair import FrameSpecRepairConstraint
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
from .opening_empty_shot_insert import OpeningEmptyShotInsertConstraint
from .opening_empty_shot_cap import OpeningEmptyShotCapConstraint  # F15 开头空镜硬上限
from .opening_character_presence import OpeningCharacterPresenceConstraint  # R2 开篇人物兜底（v6.62.2, 09-07 通用）
from .opening_empty_shot_remove import OpeningEmptyShotRemoveConstraint  # R2b 开头空镜硬删除（v6.64, P4.3+）
from .dialogue_density import DialogueDensityConstraint  # R3 对白密度兜底（v6.62.2, 09-07 通用）
from .dialogue_grounding import DialogueGroundingConstraint  # F16 失锚台词硬替换
from .frame_layouts_required import FrameLayoutsRequiredConstraint  # R4 frame_layouts 必填（v6.62.2, 09-07 通用）
from .shot_count_bounds import ShotCountBoundsConstraint  # 镜头数/场景数范围加严（v6.62.2, 09-07 通用）
from .overshoulder_consistency import OvershoulderConsistencyConstraint
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
from .prompt_sanitize import PromptSanitizeConstraint  # J4 prompt 消毒（v6.51, 09-02）
from .prop_prompt_consistency import PropPromptConsistencyConstraint  # L2 道具名一致性（v6.51, 09-02）
from .cross_shot_continuity import CrossShotContinuityConstraint  # K2 跨镜衔接（v6.51, 09-02）
from .ff_lf_redundancy import FfLfRedundancyConstraint  # K3 三态冗余检测（v6.51, 09-02）
from .kf_pose_anchor import KfPoseAnchorConstraint  # F4 kf 姿态+位置+威胁朝向（v6.49, 08-28）
from .redguo_scene_char_limit import RedguoSceneCharLimitConstraint  # F5 红果场景/人物硬上限（v6.49, 08-28）
from .post_combat_blank import PostCombatBlankConstraint  # F6 打斗战后空镜收尾（v6.49, 08-28）
from .restrained_emotion_chain import RestrainedEmotionChainConstraint  # F7 克制情绪时间链（v6.49, 08-28）
from .climax_priority import ClimaxPriorityConstraint  # F8 高潮优先（v6.49, 08-28）
from .climax_completeness import ClimaxCompletenessConstraint  # F17 高潮完整性校验
from .short_shot_merge import ShortShotMergeConstraint  # F9 短镜合并（v6.49, 08-28）
from .ff_lf_temporal import FfLfTemporalConstraint  # F10 ff/lf 时序协议（v6.50, 08-28）
from .frame_spec_consistency import FrameSpecConsistencyConstraint  # S v6.57+ per-frame spec 一致性
from .framing_prefix_fill import FramingPrefixFillConstraint  # F11 景别词回填（v6.62, 09-06）
from .self_review import SelfReviewConstraint  # SR（LLM 自检回路）
from .split_content_similarity import SplitContentSimilarityConstraint  # A7 拆分镜内容重复检测（v6.58, 09-03）
from .mandatory_biz_fields import MandatoryBizFieldsConstraint
from .role_anchor import RoleAnchorConstraint
from .surface_backfill import SurfaceBackfillConstraint  # F12 surface 承重面回填（v6.62.1, 09-06）
from .framing_variety import FramingVarietyConstraint  # F13 景别多样性达标（v6.62.1, 09-06）
from .gaze_typo import GazeTypoConstraint  # F14 目光错别字归一（v6.62.1, 09-06）

ALL_CONSTRAINTS.extend([
    # Phase 2
    InjuryContinuityConstraint(),
    GazeDirectionConstraint(),
    PropPositionConstraint(),
    KfPrePostConflictConstraint(),
    OpeningEmptyShotInsertConstraint(),
    OpeningEmptyShotCapConstraint(),
    # Phase 3
    KfTimeWindowAlignmentConstraint(),
    KfTimelineNormalizeConstraint(),  # kf 时间线归一：同刻重排/越界钳制/标签错位/hold帧回归（2026-09-12）
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
    RoleAnchorConstraint(),
    CoreKeywordCoverageConstraint(),
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
    PromptSanitizeConstraint(),   # J4 prompt 消毒：清理（画中X）/（AI生成）等噪声（v6.51, 09-02）
    PropPromptConsistencyConstraint(),  # L2 道具名一致性：别名→标准名（v6.51, 09-02）
    CrossShotContinuityConstraint(),   # K2 跨镜衔接：场景转场+状态跳变检测（v6.51, 09-02）
    FfLfRedundancyConstraint(),        # K3 三态冗余：FF/LF/Motion 内容重复检测（v6.51, 09-02）
    KfPoseAnchorConstraint(),  # F4 kf 姿态+位置+威胁朝向（v6.49, 08-28）
    RedguoSceneCharLimitConstraint(),  # F5 红果场景/人物硬上限 HARD（v6.49, 08-28）
    PostCombatBlankConstraint(),  # F6 打斗战后空镜收尾（v6.49, 08-28）
    RestrainedEmotionChainConstraint(),  # F7 克制情绪时间链（v6.49, 08-28）
    ClimaxPriorityConstraint(),  # F8 高潮优先（v6.49, 08-28）
    ClimaxCompletenessConstraint(),
    ShortShotMergeConstraint(),  # F9 短镜合并 HARD（v6.49, 08-28）
    FfLfTemporalConstraint(),  # F10 ff 时序协议：本镜有攻击时 ff 禁结束态（v6.50, 08-28）
    FrameSpecRepairConstraint(),
    FrameSpecConsistencyConstraint(),  # S per-frame spec 必填 + 完整性 + desc 一致性（v6.57+）
    FramingPrefixFillConstraint(),  # F11 prompt 无景别词 → 推断前置「[景别]」（v6.62, 09-06）
    SelfReviewConstraint(),  # SR opt-in via SELF_REVIEW_ENABLED=1
    SplitContentSimilarityConstraint(),  # A7 拆分镜内容重复检测（v6.58, 09-03）
    MandatoryBizFieldsConstraint(),  # B1 业务必填字段兜底（v6.60 doubao 适配引入，v6.61 起全 provider 共用）
    OvershoulderConsistencyConstraint(),
    SurfaceBackfillConstraint(),  # F12 char_layout.surface 承重面回填（v6.62.1, 09-06）
    FramingVarietyConstraint(),  # F13 景别多样性达标：unique<5 → 内容适配补缺（v6.62.1, 09-06）
    GazeTypoConstraint(),  # F14 目光错别字归一：倒倒→紧紧（v6.62.1, 09-06）
    # v6.62.2 (09-07) 通用兜底加严：4 个新约束，不绑小说角色，跨章节/换书自动跟随
    OpeningCharacterPresenceConstraint(),  # R2 前 2 镜必含 1 个角色（从 script.characters 动态取）
    OpeningEmptyShotRemoveConstraint(),     # R2b 开头空镜硬删除
    DialogueDensityConstraint(),            # R3 对白镜占比 ≥ 30%（仅看通用对话关键词）
    DialogueGroundingConstraint(),
    FrameLayoutsRequiredConstraint(),      # R4 frame_layouts 必填 + 长度对齐
    ShotCountBoundsConstraint(),            # 镜头数/场景数范围加严（[10,18]/[1,5]）
    # 2026-09-12 结构归一约束置于链尾：文本清洗类约束跑完后再看最终文本，
    # 否则相似度在后续改写中漂移会漏判（首跑实测 0.83→0.89 漂移漏合并）
    DuplicateShotMergeConstraint(),   # 相邻镜重复视觉 HARD 合并
    CharSideContinuityConstraint(),   # 同地点连续镜角色侧翻转交换
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
