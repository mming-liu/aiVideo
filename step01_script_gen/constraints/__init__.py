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
from .kf_event_order import KfEventOrderConstraint
from .split_continuity import SplitContinuityConstraint
from .motion_time_integrity import MotionTimeIntegrityConstraint
from .kf_start_time_normalize import KfStartTimeNormalizeConstraint
from .shot_index_renumber import ShotIndexRenumberConstraint
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
from .identity_unification import IdentityUnificationConstraint
from .climax_distribution import ClimaxDistributionConstraint  # M
from .core_keyword_coverage import CoreKeywordCoverageConstraint
from .core_keyword_pollution_cleanup import CoreKeywordPollutionCleanupConstraint
from .frame_spec_repair import FrameSpecRepairConstraint
from .frame_anchor_rebuild import FrameAnchorRebuildConstraint
from .frame_text_state_cleanup import FrameTextStateCleanupConstraint
from .group_actor_continuity import GroupActorContinuityConstraint
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
from .kf_frame_redundancy import KfFrameRedundancyConstraint  # K9 kf 帧级冗余（09-13）
from .kf_pose_anchor import KfPoseAnchorConstraint  # F4 kf 姿态+位置+威胁朝向（v6.49, 08-28）
from .redguo_scene_char_limit import RedguoSceneCharLimitConstraint  # F5 红果场景/人物硬上限（v6.49, 08-28）
from .post_combat_blank import PostCombatBlankConstraint  # F6 打斗战后空镜收尾（v6.49, 08-28）
from .restrained_emotion_chain import RestrainedEmotionChainConstraint  # F7 克制情绪时间链（v6.49, 08-28）
from .climax_priority import ClimaxPriorityConstraint  # F8 高潮优先（v6.49, 08-28）
from .climax_completeness import ClimaxCompletenessConstraint  # F17 高潮完整性校验
from .short_shot_merge import ShortShotMergeConstraint  # F9 短镜合并（v6.49, 08-28）
from .ff_lf_temporal import FfLfTemporalConstraint  # F10 ff/lf 时序协议（v6.50, 08-28）
from .frame_spec_consistency import FrameSpecConsistencyConstraint  # S v6.57+ per-frame spec 一致性
from .offscreen_action_consistency import OffscreenActionConsistencyConstraint
from .motion_segment_kf_align import MotionSegmentKfAlignConstraint  # v6.102 时段合并
from .motion_time_bounds import MotionTimeBoundsConstraint
from .duration_budget import DurationBudgetConstraint
from .framing_prefix_fill import FramingPrefixFillConstraint  # F11 景别词回填（v6.62, 09-06）
from .gaze_state_repair import GazeStateRepairConstraint  # F15 无视线状态 gaze_target 修复
from .self_review import SelfReviewConstraint  # SR（LLM 自检回路）
from .split_content_similarity import SplitContentSimilarityConstraint  # A7 拆分镜内容重复检测（v6.58, 09-03）
from .mandatory_biz_fields import MandatoryBizFieldsConstraint
from .role_anchor import RoleAnchorConstraint
from .surface_backfill import SurfaceBackfillConstraint  # F12 surface 承重面回填（v6.62.1, 09-06）
from .framing_variety import FramingVarietyConstraint  # F13 景别多样性达标（v6.62.1, 09-06）
from .gaze_typo import GazeTypoConstraint  # F14 目光错别字归一（v6.62.1, 09-06）
from .final_quality_normalizer import FinalQualityNormalizerConstraint  # 链尾：prompt 预算/单运镜/kf 时间窗/blood 过渡
from .emotion_visualization_repair import EmotionVisualizationRepairConstraint
from .weapon_state_consistency import WeaponStateConsistencyConstraint

# v6.76/v6.77（09-19）：**7 个 _Final 重复注册实例已全部删除**。
#
# 判据（两步都过才删）：
#   1. 基类已降为只报告 —— v6.76 删 3 个（kf_time_window_alignment_final /
#      kf_event_order_final / kf_pose_anchor_final）：只读检测重复跑没有意义。
#   2. 基类仍是写入者，v6.77 删 4 个（kf_blood_monotonic_final /
#      kf_blood_monotonic_final2 / group_actor_continuity_final /
#      frame_text_state_cleanup_final），**理由是顺序依赖，不是「全输入等价」**：
#
#      ⚠️ **v6.79 复核（审核发现 6）纠正了本段原措辞**。原注释依据是「造一个
#      同时触发三个基类的 fixture，逐个摘掉 _Final 跑 run_hard_fix —— 输出
#      字节级相同」，并把它写成对四个实例的等价证明。那个证明只覆盖该 fixture
#      的状态，**不构成交换律证明**；中段 `_KfBloodMonotonicFinal` 有确定反例：
#
#        `KfBloodMonotonicConstraint` 不只在 kf.desc 补血锚，还会把伤情锚追加到
#        `lf_desc`；而 `V643PromptEnforcementConstraint._head_fix_context_ok()`
#        读 `lf_desc` 里的「伤处/暗红/洇痕」决定是否补「仰面朝上双目紧闭，
#        头颈僵直不侧转」这类**结果态**。两者**不满足交换律**：
#
#          旧顺序 blood → v6_43：lf_desc 同时出现伤情锚 + 闭眼/僵直
#          新顺序 v6_43 → blood：lf_desc 只有伤情锚，没有闭眼/僵直
#
#        也就是说，删掉中段 blood 实例**有意**移除了「后加的伤情锚触发 v6_43
#        补造死亡姿态」这条路径。最终对象明确不相等——这是**有意的语义变更**
#        （更符合「不要凭伤情虚构死亡姿态」的方向），不是 no-op。
#        回归见 `tests/step01_script_gen/constraints/test_constraint_order_dependency.py`。
#
#      其余三个实例仍按原 fixture 判定为链尾重复，但同样只在该 fixture 状态上
#      成立；若要证明「基类之后的写入者重新制造缺陷」，需要各自构造触发 fixture。
#
# 原注释给出的理由已不成立：
#   - 「事件顺序定稿后」—— `kf_event_order` 已改为只报告（不再删/补帧）
#   - 「最终重建后补回」—— `frame_anchor_rebuild._replace_anchor` 只替换
#     `【角色锚定】` 标记块，标记外的血锚尾部会保留
#
# 详见 docs/step01-temporal-writers-audit-2026-09-19.md 与
# docs/step01-temporal-writers-implementation-review-2026-09-19.md 发现 6。

# ===========================================================================
# v6.84（二次审核发现 2）：约束链的**显式三阶段**
#
# 原实现是一条混合链：文本归一、语义改写、时间派生和只读审计混在一起，靠人工
# 排序维持。屏障（`shared/semantic_barrier.py`）能**发现**越权写入，却不能自动
# 把写入者搬到正确阶段——本次重跑实测 `v6_43_prompt_enforcement`（第 39 位）与
# `prompt_sanitize`（第 46 位）都在 KF 审计（第 8 位起）之后改 `motion_desc`，
# 屏障正确地报了 blocking，但链路本身仍然是错的。
#
# 三段语义：
#   ① `PRE_TEMPORAL_NORMALIZE`：文本消毒、对象别名/尺寸归一、确有必要的结构改写。
#      所有会写 `motion_desc` / 时长的确定性约束都必须在这里跑完。
#   ② `TEMPORAL_DERIVE`：唯一一次 duration / timecode / KF 时间派生
#      （在 `timecode._compute_timecodes` / `write_timeline_from_durations`，
#      不在本链内）。
#   ③ `POST_TEMPORAL_AUDIT`：只读检查；运行时断言语义 payload 零写入。
#
# `TEMPORAL_DERIVE_START` 是 ①/③ 的分界名字——`semantic_barrier` 直接引用它，
# 不再各自维护一份「审计段起点」。分界之后的约束**不得**写
# `duration` / `duration_sec` / `motion_desc` / `keyframes[*].t_sec`。
# ===========================================================================
PRE_TEMPORAL_NORMALIZE = "pre_temporal_normalize"
TEMPORAL_DERIVE = "temporal_derive"
POST_TEMPORAL_AUDIT = "post_temporal_audit"
# ①/③ 分界：此约束**及其之后**属审计段。
# 定义在 `shared.semantic_barrier`（屏障要用它判断写入发生在哪一段），这里
# **引用同一份**而不是各写一个字面量——两个名字指向两个值正是这类守卫失效的常见形态。
from shared.semantic_barrier import AUDIT_SEGMENT_START as TEMPORAL_DERIVE_START  # noqa: E402
# 分界之后禁止写入的时间字段（与 `shared.semantic_barrier.SHOT_TIME_FIELDS` 同源）
POST_AUDIT_LOCKED_WRITERS: tuple = ()

ALL_CONSTRAINTS.extend([
    # ===== ① PRE_TEMPORAL_NORMALIZE：所有时间字段写入者必须在这里跑完 =====
    # v6.84（发现 2）：两个「靠后注册却在写 motion_desc」的约束前移到这里。
    #   `prompt_sanitize`：清（画中X）/（AI生成）等噪声——纯文本消毒，本属第 1 段。
    #   `v6_43_prompt_enforcement`：船体尺寸扩写/头部定态/抽象方位具象化会改
    #     `motion_desc`，必须在 KF/事件表生成之前完成。
    # v6.84（09-21 用户决定）：**第一位**——模型自造的半整数 index（9.5/10.5）
    # 会让下游按「拆镜子镜」处理它们。先归一成 1..N 连续整数，
    # 后面所有约束（含结构插入类）都按干净 index 组织。
    ShotIndexRenumberConstraint(),
    PromptSanitizeConstraint(),   # J4 prompt 消毒（v6.51, 09-02）
    V643PromptEnforcementConstraint(),  # v6.43 prompt 强化（v6.84 起前置于时间派生之前）
    # v6.84（09-21 重跑发现）：`group_actor_continuity` 会写 `motion_desc`
    # （恢复群体动作主体），注册位置却在审计段之后 → 实测第二条
    # `time_field_audit_write` 就是它（`shot 11.motion_desc`）。同步前移到第 1 段。
    #
    # 静态检查漏掉它的原因：它用 `shot[field] = fixed` 这种**变量下标**写入，
    # 按字面量匹配的防腐测试看不见——所以那条检查也一并加宽（见
    # `tests/shared/test_semantic_barrier.py::test_late_constraints_are_report_only`）。
    GroupActorContinuityConstraint(),  # 恢复群体主体并同步关键帧在场状态
    # v6.84：`emotion_visualization_repair` 的 `shot[field] = fixed` 走的字段表
    # 含 `motion_desc`——同样是**审计段里的时间字段写入者**（只是本片数据还没
    # 触发）。加宽的静态检查抓到它，一并前移。
    EmotionVisualizationRepairConstraint(),  # 最终模型可见字段情绪可视化门
    # v6.94（2026-09-22 21:32 实测）：`prompt_repetition` 的字段表
    # `fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")` 含 `motion_desc`，
    # 而写入是 `for fld in self.fields: shot[fld] = new_v`——**变量下标**。
    # 注册位置在审计段之后 → 真实运行报出第三条 `time_field_audit_write`
    # （`shot 14.motion_desc`）。
    #
    # 静态防腐测试漏掉它的原因：那条检查只匹配 `for <var> in (<字面量元组>)`，
    # 而这里迭代的是**类属性** `self.fields`。检查已一并加宽到能解析类属性/模块常量
    # （见 `tests/shared/test_semantic_barrier.py`）。它是纯文本去重（复读机/名字连跑/
    # 修复指令回显），本属第 1 段。
    PromptRepetitionConstraint(),
    # v6.94（09-22）：加宽后的**静态防腐检查**当场抓到同类第二个——`boat_width`
    # 的字段表同为 `("prompt", "ff_desc", "lf_desc", "motion_desc")`、写入同为
    # `shot[fld] = new_var`，注册位置也在审计段（本片数据未触发，属潜在写入者）。
    BoatWidthConstraint(),
    # v6.102（第十六次修复项 #3）：**时段数 > KF 数 → 合并相邻时段**。
    # 放在①段的**最后**：它要读已经定稿的 `motion_desc` 与 `keyframes`，
    # 而它写的是 `motion_desc`（时间字段），必须在审计段之前跑完。
    MotionSegmentKfAlignConstraint(),
    # Phase 2
    InjuryContinuityConstraint(),
    GazeDirectionConstraint(),
    PropPositionConstraint(),
    KfPrePostConflictConstraint(),
    OpeningEmptyShotInsertConstraint(),
    OpeningEmptyShotCapConstraint(),
    # Phase 3
    MotionTimeIntegrityConstraint(),  # 先修非法时间段，再进入拆镜/关键帧链
    SplitContinuityConstraint(),  # 先修拆分边界，再生成/校正关键帧
    # v6.84（09-21 重跑发现）：钩子首个 KF 稳定写成 t_sec=0.0，钩子门重生 2 次都
    # 改不掉——按规则 16.4「kf 时刻 = 时段中点」在第 1 段确定性归一。
    KfStartTimeNormalizeConstraint(),
    KfTimeWindowAlignmentConstraint(),
    KfTimelineNormalizeConstraint(),  # kf 时间线归一：同刻重排/越界钳制/标签错位/hold帧回归（2026-09-12）
    KfTSecBoundsConstraint(),
    KfMonotonicConstraint(),
    KfEventOrderConstraint(),  # 只报告：核对事件时刻/阶段（v6.76 起不再删同段重复帧）

    # Phase 4
    AxisJumpConstraint(),  # G HARD
    BoatCropConstraint(),
    BridgeWordsConstraint(),
    CameraVarietyConstraint(),
    IdentityUnificationConstraint(),  # 别名/群演占位必须先于锚点与 char_layout 消费
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
    CoreKeywordPollutionCleanupConstraint(),
    CoreKeywordCoverageConstraint(),
    OpeningHookConstraint(),
    # v6.43（08-23 审计）：PlatformCompliance 必须在所有 prompt 追加类
    # 约束（bridge_words / transition_keywords / prompt_repetition）之后
    # 执行，否则「（AI生成）」会被推挤到 prompt 中段。
    TransitionKeywordsConstraint(),
    RuntimeExtendConstraint(),  # HARD
    ThreatGazeCouplingConstraint(),
    PlatformComplianceConstraint(),
    KfWeakTemplateConstraint(),  # W16 kf弱模板占位（v6.45, 08-26）
    CameraMovementFillConstraint(),  # J3 运镜缺失补填（v6.47, 08-27）
    FrameTextStateCleanupConstraint(),  # 再清理首尾帧里无标记的旧状态分句
    FrameAnchorRebuildConstraint(),  # 先重建锚块，再让血状态单调约束补回锚
    KfBloodMonotonicConstraint(),  # F2 kf 血状态单调（v6.49, 08-28）
    FfMotionOverlapConstraint(),  # F3 ff_desc 动作重叠（v6.49, 08-28）
    PropPromptConsistencyConstraint(),  # L2 道具名一致性：别名→标准名（v6.51, 09-02）
    CrossShotContinuityConstraint(),   # K2 跨镜衔接：场景转场+状态跳变检测（v6.51, 09-02）
    FfLfRedundancyConstraint(),        # K3 三态冗余：FF/LF/Motion 内容重复检测（v6.51, 09-02）
    KfFrameRedundancyConstraint(),     # K9 kf 帧级冗余：mid-kf≈ff / kf≈kf / spec 复制（09-13）
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
    OffscreenActionConsistencyConstraint(),  # on_screen=false vs 可见动作矛盾
    MotionTimeBoundsConstraint(),  # motion 时段 > duration 时扩时长，禁止静默截断
    FramingPrefixFillConstraint(),  # F11 prompt 无景别词 → 推断前置「[景别]」（v6.62, 09-06）
    GazeStateRepairConstraint(),  # F15 无视线状态 gaze_target 修复
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
    DurationBudgetConstraint(),  # 正片 60-90s：只压缩冗余时长，最低需求仍超限则 BLOCK
    FinalQualityNormalizerConstraint(),  # 链尾最终质量归一
    WeaponStateConsistencyConstraint(),  # 同帧持握/脱手状态最终一致性
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
