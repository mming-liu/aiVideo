"""shared.motion_render - A 包（动效协议、模板与修订）的公共入口（计划 §A1–§A5）。

模块分工（**一份定义、一处判据**）：

| 模块 | 职责 |
|---|---|
| ``schema`` | MotionBundle v1 / motion_revision.v1 的**唯一定义**：类型表达式树同时渲染 JSON Schema 与运行时校验；RFC6901 文法 |
| ``validate`` | 纯校验：来源、引用、字符索引、时间、版式、字体字形；返回结构化 problems |
| ``render`` | 固定 HTML/SVG + Playwright 定时 seek + FFmpeg 编码；帧计划、runtime 探测、PNG 校验、联系表 |
| ``overlay`` | 给 Step04 的**唯一接线契约**：绝对时间层表、顺序、身份守卫、滤镜片段 |

事务与提交不在这里：候选副本、attempt 锁、manifest、accepted 指针统一走
``shared.enhancement_io``，由 ``tools/motion_lab.py`` 编排（见计划 §F2/§F3）。
"""
from .schema import (  # noqa: F401
    SCHEMA_MOTION_BUNDLE,
    SCHEMA_MOTION_OVERLAY_PLAN,
    SCHEMA_MOTION_REVISION,
    SOURCE_KIND_ACCEPTED_SCRIPT,
    SOURCE_KIND_FIXTURE,
    TEMPLATE_LETTER,
    TEMPLATE_MAP,
    TEMPLATE_TITLE,
    TEMPLATES,
    check_motion_bundle,
    check_revision_request,
    load_json_strict,
    motion_bundle_json_schema,
    motion_revision_json_schema,
    parse_pointer,
    pointer_child,
    pointer_join,
    resolve_pointer,
)
from .validate import (  # noqa: F401
    ValidationReport,
    fixture_doc_sha256,
    font_codepoint_set,
    layout_spec,
    leaf_offset,
    load_bundle_with_sources,
    load_fixture_doc,
    shot_table,
    source_doc_sha256,
    validate_motion_bundle,
)
from .render import (  # noqa: F401
    BrowserRejected,
    FrameRequest,
    PlaywrightShooter,
    RuntimeUnavailable,
    ShooterSpec,
    ShooterUnavailable,
    SpecRenderResult,
    compose_contact_sheet,
    copy_keyframes,
    encode_display_mp4,
    frame_count_for,
    frame_plan,
    frames_identical,
    render_spec,
    runtime_status,
    seek_endpoint,
    verify_frame_sequence,
)
from .overlay import (  # noqa: F401
    OverlayPlanReport,
    build_overlay_plan,
    overlay_filter_fragment,
    png_input_args,
)

__all__ = [
    "SCHEMA_MOTION_BUNDLE", "SCHEMA_MOTION_REVISION", "SCHEMA_MOTION_OVERLAY_PLAN",
    "SOURCE_KIND_FIXTURE", "SOURCE_KIND_ACCEPTED_SCRIPT",
    "TEMPLATE_TITLE", "TEMPLATE_LETTER", "TEMPLATE_MAP", "TEMPLATES",
    "check_motion_bundle", "check_revision_request", "load_json_strict",
    "motion_bundle_json_schema", "motion_revision_json_schema",
    "parse_pointer", "pointer_join", "pointer_child", "resolve_pointer",
    "ValidationReport", "validate_motion_bundle", "layout_spec",
    "load_bundle_with_sources", "load_fixture_doc", "shot_table", "leaf_offset",
    "source_doc_sha256", "fixture_doc_sha256", "font_codepoint_set",
    "frame_count_for", "frame_plan", "seek_endpoint", "runtime_status",
    "render_spec", "SpecRenderResult", "verify_frame_sequence", "frames_identical",
    "FrameRequest", "ShooterSpec", "PlaywrightShooter", "ShooterUnavailable",
    "RuntimeUnavailable", "BrowserRejected",
    "encode_display_mp4", "compose_contact_sheet", "copy_keyframes",
    "build_overlay_plan", "OverlayPlanReport", "overlay_filter_fragment", "png_input_args",
]
