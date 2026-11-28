"""环境变量覆盖检测 — 检测 shell 变量是否覆盖 .env.content 设置.

v6.59 (2026-09-04) 新增：
  2026-09-04 21:12 实跑：shell `export TEXT_PROVIDER=agent_plan` 覆盖了 .env.content 的
  `TEXT_PROVIDER=deepseek`，导致 9-12 分钟慢章 + doubao-seed-2-1-turbo 在 70k input +
  strict json_schema 下结构化输出能力不足（kf t_sec 越界、narration 吐出"镜头降级"占位
  文本等）。本模块启动时检测 shell 覆盖并 WARN 提示，让"配置漂移"可见。

设计原则：
  - **不阻断**：shell 覆盖仍生效（保留调试灵活性），仅 WARN 提示用户可见
  - **不修改 dotenv 行为**：默认 shell > .env.content 优先级保留
  - **集中**：所有需要检测的 key 集中传入，单点维护（避免每个 main 都重复检测）
  - **可升级**：raise_if_override=True 切强约束（CI/生产环境用，调试期不推荐）

调用方式（在 main 入口处一次）：
    from shared.env_guard import warn_shell_overrides
    warn_shell_overrides(
        ["TEXT_PROVIDER", "IMAGE_PROVIDER", "VIDEO_PROVIDER", "TTS_PROVIDER"],
        logger=_logger,
    )
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Mapping, Optional

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = None  # type: ignore[assignment]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_CONTENT = _PROJECT_ROOT / ".env.content"


def _read_env_content() -> dict[str, str]:
    """读取 .env.content 文件中的所有 key=value（不写回 os.environ）.

    复用 dotenv_values 解析（与 load_dotenv 同款语法），但只读不写。
    """
    if dotenv_values is None:
        return {}
    if not _ENV_CONTENT.exists():
        return {}
    return {k: v for k, v in dotenv_values(_ENV_CONTENT).items() if v is not None}


def find_shell_overrides(keys: Iterable[str]) -> list[tuple[str, str, str]]:
    """检测 shell 环境变量中哪些 key 覆盖了 .env.content 的值.

    Returns:
        [(key, shell_value, file_value), ...] 仅返回真正不一致的项。
        顺序与输入 keys 一致。.env.content 未设的 key 不算覆盖（shell 是唯一来源）。

    注意：os.environ.get(key) 直接读真实 env（含 shell 导出），不受 load_dotenv 影响——
    load_dotenv 默认不覆盖已存在的 env var，所以 shell 值会被保留。
    """
    file_values = _read_env_content()
    overrides: list[tuple[str, str, str]] = []
    for key in keys:
        shell_value = os.environ.get(key)
        if shell_value is None:
            # shell 没设这个 key，.env.content 的值生效，无需 WARN
            continue
        file_value = file_values.get(key)
        if file_value is None:
            # .env.content 没设这个 key，shell 是唯一来源，不算覆盖
            continue
        if shell_value.strip() != file_value.strip():
            overrides.append((key, shell_value, file_value))
    return overrides


def warn_shell_overrides(
    keys: Iterable[str],
    logger=None,
    *,
    raise_if_override: bool = False,
) -> list[tuple[str, str, str]]:
    """检测并 WARN shell 覆盖（推荐在 main 入口处调用一次）.

    Args:
        keys: 要检测的环境变量名列表（如 ["TEXT_PROVIDER", "IMAGE_PROVIDER"]）
        logger: 可选 logger 对象（有 warn 方法）；None 时用 print（控制台 + 落 log）
        raise_if_override: 若为 True，遇到任何覆盖直接 raise RuntimeError。
            仅在 CI/生产环境用——调试期设为 False 保留 shell 调试灵活性。

    Returns:
        覆盖项列表（[(key, shell_value, file_value), ...]），调用方可自行处理。
    """
    overrides = find_shell_overrides(keys)
    if not overrides:
        return overrides

    msg_lines = [
        "[!] 检测到 shell 环境变量覆盖 .env.content 设置（dotenv 默认行为，shell 优先）：",
        "    建议 unset 后重跑，避免配置漂移：",
    ]
    for key, shell_value, file_value in overrides:
        msg_lines.append(
            f"      unset {key}   # shell={shell_value!r} ≠ .env.content={file_value!r}"
        )
    msg = "\n".join(msg_lines)

    if raise_if_override:
        raise RuntimeError(msg)

    if logger is not None and hasattr(logger, "warn"):
        logger.warn(msg)
    else:
        print(msg)
    return overrides


# ---------------------------------------------------------------------------
# 跨键配置一致性预检（2026-11-28 O1）
#
# 「改了 A 配置、B 配置悄悄覆盖它」与「开了不兼容能力」这两类问题只在真实运行里
# 才暴露，代价是钱和成片。判定集中成**纯函数**（不写 os.environ、无 I/O），由
# 单测、`tools/print_effective_config.py --check` 与各阶段入口共用同一份结果。
#
# 与 `warn_shell_overrides` 的分工：
#   - `warn_shell_overrides`：同一个 key 在 shell 与 .env 之间的**来源冲突**；
#   - 本节：不同 key 之间的**语义冲突**（模型能力 / 画幅 / 声音合同 / 数值边界）。
#
# 严重度口径：
#   - error   = 会让产物错误，或让配置被静默改写（必须修；`CONFIG_GUARD_STRICT=1` 时阻断）；
#   - warning = 语义互相拉扯但可能是有意为之（只提示，人工确认）。
# ---------------------------------------------------------------------------

#: 设为 1/true 时，error 级问题在入口处直接抛错（默认只记日志，不破坏现有单测）。
CONFIG_GUARD_STRICT_ENV = "CONFIG_GUARD_STRICT"

STAGES: tuple[str, ...] = ("all", "step01", "step02a", "step02b", "step03", "step04")

_RATIO_KEYS: dict[str, str] = {
    "bailian": "BAILIAN_VIDEO_RATIO",
    "volcengine": "VOLCENGINE_VIDEO_RATIO",
    "agent_plan": "AGENT_PLAN_VIDEO_RATIO",
}
_LEGAL_RATIOS = ("16:9", "9:16", "1:1")
_AUDIO_MODES = ("native", "tts_mix")
_TRUTHY = ("1", "true", "yes", "on")

#: 规则 → 适用的阶段入口。`stage="all"` 时全部输出；具体阶段只输出命中项，
#: 避免 step01 报 step04 的合成画幅问题。
_RULE_STAGES: dict[str, frozenset[str]] = {
    "VIDEO_VOICE_UNSUPPORTED": frozenset({"step01", "step02a", "step02b", "step03", "step04"}),
    "VIDEO_R2V_UNSUPPORTED": frozenset({"step02a", "step02b"}),
    "RATIO_CONFLICT_PROVIDER": frozenset({"step02b"}),
    "RATIO_CONFLICT_MERGE": frozenset({"step04"}),
    "AUDIO_MODE_VOICE_CONFLICT": frozenset({"step03", "step04"}),
    "INVALID_NUMERIC_CONFIG": frozenset({"step01", "step02a", "step02b", "step03", "step04"}),
    "IMAGE_REFS_BELOW_MULTI": frozenset({"step02a"}),
    "STYLE_LABEL_MISMATCH": frozenset({"step01", "step02a", "step02b", "step04"}),
}


@dataclass(frozen=True)
class ConfigIssue:
    """一条跨键配置问题（纯数据，可 JSON 化）."""

    severity: Literal["error", "warning"]
    code: str
    keys: tuple[str, ...]
    message: str
    suggestion: str

    def as_dict(self) -> dict[str, object]:
        return {"severity": self.severity, "code": self.code,
                "keys": list(self.keys), "message": self.message,
                "suggestion": self.suggestion}


class ConfigConsistencyError(RuntimeError):
    """`CONFIG_GUARD_STRICT=1`（或 strict=True）下的 error 级配置冲突."""

    def __init__(self, issues: Iterable[ConfigIssue]):
        self.issues = tuple(i for i in issues if i.severity == "error")
        super().__init__("配置一致性预检未通过：\n" + format_config_issues(self.issues))


def format_config_issues(issues: Iterable[ConfigIssue]) -> str:
    """人类可读的多行摘要（入口日志与 `--check` 文本输出共用）."""
    lines = []
    for issue in issues:
        lines.append(f"[{issue.severity.upper()}] {issue.code} ({', '.join(issue.keys)}) "
                     f"{issue.message} → {issue.suggestion}")
    return "\n".join(lines)


def _is_truthy(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _is_one(value: object, default: str = "1") -> bool:
    """`VIDEO_MODEL_VOICE` 的历史口径：只有字符串 "1" 算开启（见 audio_policy）."""
    raw = default if value is None else value
    return str(raw).strip() == "1"


def _provider(env: Mapping[str, str]) -> str:
    return (env.get("VIDEO_PROVIDER") or "bailian").strip().lower()


def _mode(env: Mapping[str, str]) -> str:
    return (env.get("VIDEO_REFERENCE_MODE") or "text").strip().lower()


def _read_number(env: Mapping[str, str], key: str) -> tuple[bool, float | None]:
    """(键是否存在且非空白, 数值或 None)。None 同时表示非数字 / 非有限值."""
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return False, None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return True, None
    return True, (value if math.isfinite(value) else None)


def _canvas_ratio(env: Mapping[str, str]) -> str | None:
    """`VIDEO_SIZE` 派生的最简画幅（不可解析 None），与 `frame_canvas_ratio()` 同源."""
    from shared.config import DEFAULT_VIDEO_SIZE, size_ratio

    return size_ratio(env.get("VIDEO_SIZE") or DEFAULT_VIDEO_SIZE)


def _normalize_ratio(value: str | None) -> str | None:
    from shared.config import size_ratio

    return size_ratio(value)


# ------------------------------------------------------------------ 8 类规则
def _check_video_voice(env: Mapping[str, str]) -> list[ConfigIssue]:
    """VIDEO_VOICE_UNSUPPORTED：声明「模型自带语音」但模型没有原生音频."""
    if not _is_one(env.get("VIDEO_MODEL_VOICE")):
        return []
    from shared.model_caps import active_video_model, active_video_supports_audio

    if active_video_supports_audio(env):
        return []
    model = active_video_model(env) or "unknown"
    return [ConfigIssue(
        "error", "VIDEO_VOICE_UNSUPPORTED",
        ("VIDEO_MODEL_VOICE", "VIDEO_PROVIDER"),
        f"VIDEO_MODEL_VOICE=1，但当前视频模型 {model}（provider={_provider(env)}）"
        "没有原生音频：对白只能由 step03 TTS 提供，声音合同会被静默改成 tts_mix",
        "设 VIDEO_MODEL_VOICE=0，或换成支持 generate_audio 的模型")]


def _check_r2v(env: Mapping[str, str]) -> list[ConfigIssue]:
    """VIDEO_R2V_UNSUPPORTED：要求 r2v 但当前模型不支持参考图."""
    if _mode(env) != "r2v":
        return []
    from shared.model_caps import active_video_model

    model = active_video_model(env)
    if not model:
        return []
    from shared.ref_compose import model_supports_reference_image

    if model_supports_reference_image(model):
        return []
    return [ConfigIssue(
        "error", "VIDEO_R2V_UNSUPPORTED",
        ("VIDEO_REFERENCE_MODE", "VIDEO_PROVIDER"),
        f"VIDEO_REFERENCE_MODE=r2v，但当前模型 {model} 不支持 reference_image",
        "改成 image（首尾帧）或 text（文生视频），或换支持 r2v 的模型")]


def _check_provider_ratio(env: Mapping[str, str]) -> list[ConfigIssue]:
    """RATIO_CONFLICT_PROVIDER：供应商级画幅覆盖正在扭转 VIDEO_SIZE 的画幅."""
    key = _RATIO_KEYS.get(_provider(env))
    if not key:
        return []
    raw = (env.get(key) or "").strip()
    if not raw:
        return []
    canvas = _canvas_ratio(env)
    normalized = _normalize_ratio(raw)
    if normalized is None:
        if raw.lower() == "adaptive":
            return []          # Seedance 2.5 首尾帧的合法特殊值
        return [ConfigIssue(
            "warning", "RATIO_CONFLICT_PROVIDER", (key, "VIDEO_SIZE"),
            f"{key}={raw!r} 不是可解析的画幅（16:9 / 9:16 / 1:1），provider 会退回按 req 推导",
            "改成合法画幅，或留空（空=按画幅推导）")]
    if canvas is None or canvas == normalized:
        return []
    return [ConfigIssue(
        "warning", "RATIO_CONFLICT_PROVIDER", (key, "VIDEO_SIZE"),
        f"{key}={raw} 与 VIDEO_SIZE 推导的画幅 {canvas} 不一致："
        "该供应商的请求会按 vendor 覆盖出图，与帧 prompt 里的画幅词相反",
        "留空让画幅跟随 VIDEO_SIZE，或同步修改 VIDEO_SIZE")]


def _check_merge_ratio(env: Mapping[str, str]) -> list[ConfigIssue]:
    """RATIO_CONFLICT_MERGE：成片压制画幅方向与 VIDEO_SIZE 相反（强裁切/黑边）."""
    from shared.config import parse_size

    raw = (env.get("MERGE_RESOLUTION") or "").strip()
    if not raw:
        return []
    merge = parse_size(raw)
    if merge is None:
        return [ConfigIssue(
            "warning", "RATIO_CONFLICT_MERGE", ("MERGE_RESOLUTION",),
            f"MERGE_RESOLUTION={raw!r} 不是 WxH 格式，step04 会按默认值处理",
            "改成 WxH（如 1440x2560）或留空")]
    canvas = parse_size(env.get("VIDEO_SIZE")) if env.get("VIDEO_SIZE") else None
    if canvas is None:
        from shared.config import DEFAULT_VIDEO_SIZE

        canvas = parse_size(DEFAULT_VIDEO_SIZE)
    if canvas is None or canvas[0] == canvas[1] or merge[0] == merge[1]:
        return []
    if (canvas[0] > canvas[1]) == (merge[0] > merge[1]):
        return []
    return [ConfigIssue(
        "warning", "RATIO_CONFLICT_MERGE", ("MERGE_RESOLUTION", "VIDEO_SIZE"),
        f"MERGE_RESOLUTION={raw}（{'横' if merge[0] > merge[1] else '竖'}屏）与 "
        f"VIDEO_SIZE={env.get('VIDEO_SIZE')}（{'横' if canvas[0] > canvas[1] else '竖'}屏）方向相反",
        "让压制分辨率与生成画布同向，否则 step04 会强裁切或加黑边")]


def _check_audio_mode(env: Mapping[str, str]) -> list[ConfigIssue]:
    """AUDIO_MODE_VOICE_CONFLICT：合并声音模式与 VIDEO_MODEL_VOICE 自相矛盾.

    权威判定在 `shared.audio_policy.resolve_audio_policy`（voice=0 或模型无声时
    一律回落 tts_mix，不会产出静音成片）。本条只把**已声明的覆盖**变可见：
    写错的模式名会被静默当成 native，声明 native 却在等 TTS 的配置也提示出来。
    """
    raw = env.get("MERGE_AUDIO_MODE")
    mode = (raw if raw is not None else "native").strip().lower()
    if mode not in _AUDIO_MODES:
        return [ConfigIssue(
            "error", "AUDIO_MODE_VOICE_CONFLICT", ("MERGE_AUDIO_MODE",),
            f"MERGE_AUDIO_MODE={raw!r} 不是 native / tts_mix：audio_policy 会静默按 "
            "native 处理，实际声音合同与本行配置不一致",
            "改成 native 或 tts_mix")]
    if mode == "native" and not _is_one(env.get("VIDEO_MODEL_VOICE")):
        return [ConfigIssue(
            "warning", "AUDIO_MODE_VOICE_CONFLICT",
            ("MERGE_AUDIO_MODE", "VIDEO_MODEL_VOICE"),
            "MERGE_AUDIO_MODE=native 与 VIDEO_MODEL_VOICE=0 冲突：声音合同实际会回落 "
            "tts_mix（step03 必须先跑），配置字面与实际执行不一致",
            "改成 MERGE_AUDIO_MODE=tts_mix，或设 VIDEO_MODEL_VOICE=1 并用有声模型")]
    return []


def _check_numeric(env: Mapping[str, str]) -> list[ConfigIssue]:
    """INVALID_NUMERIC_CONFIG：数值配置写错时拒绝静默回退."""
    issues: list[ConfigIssue] = []
    positive = ("RENDER_FPS", "DURATION_MIN_SEC", "DURATION_MAX_SEC",
                "IMAGE_MAX_REFS", "IMAGE_MULTI_REF")
    non_negative = ("VIDEO_MAX_RETRIES",)
    for key in positive + non_negative:
        present, value = _read_number(env, key)
        if not present:
            continue
        raw = env.get(key)
        if value is None:
            issues.append(ConfigIssue(
                "error", "INVALID_NUMERIC_CONFIG", (key,),
                f"{key}={raw!r} 不是合法数字", "改成数字，或删除该行使用代码默认值"))
        elif key in positive and value <= 0:
            issues.append(ConfigIssue(
                "error", "INVALID_NUMERIC_CONFIG", (key,),
                f"{key}={raw!r} 必须 > 0", "改成正数，或删除该行使用代码默认值"))
        elif key in non_negative and value < 0:
            issues.append(ConfigIssue(
                "error", "INVALID_NUMERIC_CONFIG", (key,),
                f"{key}={raw!r} 不能为负（0 = 不重试，合法）",
                "改成 >= 0 的整数"))
    _, low = _read_number(env, "DURATION_MIN_SEC")
    _, high = _read_number(env, "DURATION_MAX_SEC")
    if low is not None and high is not None and low > high:
        issues.append(ConfigIssue(
            "error", "INVALID_NUMERIC_CONFIG", ("DURATION_MIN_SEC", "DURATION_MAX_SEC"),
            f"DURATION_MIN_SEC={low:g} 大于 DURATION_MAX_SEC={high:g}：时长合同自相矛盾",
            "调换/修正上下限"))
    return issues


def _check_image_refs(env: Mapping[str, str]) -> list[ConfigIssue]:
    """IMAGE_REFS_BELOW_MULTI：显式参考图上限把 IMAGE_MULTI_REF 压扁."""
    present_cap, cap = _read_number(env, "IMAGE_MAX_REFS")
    present_multi, multi = _read_number(env, "IMAGE_MULTI_REF")
    if not (present_cap and present_multi) or cap is None or multi is None:
        return []
    if cap >= multi:
        return []
    return [ConfigIssue(
        "warning", "IMAGE_REFS_BELOW_MULTI", ("IMAGE_MAX_REFS", "IMAGE_MULTI_REF"),
        f"IMAGE_MAX_REFS={cap:g} 小于 IMAGE_MULTI_REF={multi:g}：多参考图会被硬上限压扁",
        "把参考图上限上调到 >= IMAGE_MULTI_REF，或把 IMAGE_MULTI_REF 调小")]


def _check_style(env: Mapping[str, str]) -> list[ConfigIssue]:
    """STYLE_LABEL_MISMATCH：VIDEO_STYLE 与展示用 RENDER_STYLE 标签指向不同画风.

    画风词表只来自 `shared.style_prefix.STYLE_PRESETS` 的 label（单一事实源），
    不在这里写死任何题材/画风词；两者互相包含视为同一画风。
    """
    declared = (env.get("RENDER_STYLE") or "").strip()
    if not declared:
        return []
    from shared.style_prefix import STYLE_PRESETS, style_label

    style = (env.get("VIDEO_STYLE") or "").strip() or "gufeng"
    if style not in STYLE_PRESETS:
        return [ConfigIssue(
            "warning", "STYLE_LABEL_MISMATCH", ("VIDEO_STYLE",),
            f"VIDEO_STYLE={style!r} 不在预设表里：style_profile 会静默回退 gufeng",
            f"改成合法预设：{', '.join(sorted(STYLE_PRESETS))}")]

    def related(a: str, b: str) -> bool:
        return bool(a and b) and (a in b or b in a)

    own = style_label(style)
    if related(own, declared):
        return []
    others = [style_label(name) for name in STYLE_PRESETS if name != style]
    hits = sorted({label for label in others if related(label, declared)})
    if not hits:
        return []               # 自定义标签（不属于任何预设）不判
    return [ConfigIssue(
        "warning", "STYLE_LABEL_MISMATCH", ("VIDEO_STYLE", "RENDER_STYLE"),
        f"VIDEO_STYLE={style!r}（画风标签 {own!r}）与 RENDER_STYLE={declared!r} 指向不同画风"
        f"（命中的是 {', '.join(hits)}）",
        "同步修改 RENDER_STYLE，或确认 RENDER_STYLE 只是展示文案")]


_CHECKS = (
    _check_video_voice,
    _check_r2v,
    _check_provider_ratio,
    _check_merge_ratio,
    _check_audio_mode,
    _check_numeric,
    _check_image_refs,
    _check_style,
)


def validate_config_consistency(env: Mapping[str, str] | None = None, *,
                                stage: str = "all") -> list[ConfigIssue]:
    """跨键配置一致性预检（纯函数；`env=None` 时读进程环境）。

    Args:
        env: 配置映射。默认 `os.environ`（生产语义：`.env.content`/`.env.keys`
            已由 `shared.config` 的 load_dotenv 载入）。
        stage: `all` 或阶段名；具体阶段只返回与该入口相关的规则结果。

    Returns:
        按规则顺序排列的问题列表（可能为空）。**不抛异常**，由调用方决定处置。
    """
    if stage not in STAGES:
        raise ValueError(f"未知阶段 {stage!r}（合法值：{', '.join(STAGES)}）")
    resolved: Mapping[str, str] = os.environ if env is None else env
    issues: list[ConfigIssue] = []
    for check in _CHECKS:
        for issue in check(resolved):
            stages = _RULE_STAGES.get(issue.code, frozenset(STAGES))
            if stage == "all" or stage in stages:
                issues.append(issue)
    return issues


def assert_config_consistent(env: Mapping[str, str] | None = None, *,
                             stage: str = "all", strict: bool = False,
                             logger=None) -> list[ConfigIssue]:
    """入口处调用：记录问题；`strict`（或 `CONFIG_GUARD_STRICT=1`）且存在 error 时抛错.

    Returns:
        同 `validate_config_consistency`；即便抛错也已先把全部问题写进日志。
    """
    strict = bool(strict) or _is_truthy(os.getenv(CONFIG_GUARD_STRICT_ENV))
    issues = validate_config_consistency(env, stage=stage)
    if not issues:
        return issues
    text = f"[配置一致性] 阶段 {stage} 检出 {len(issues)} 条问题：\n" + format_config_issues(issues)
    if any(i.severity == "error" for i in issues):
        if logger is not None and hasattr(logger, "error"):
            logger.error(text)
        else:
            print(text)
        if strict:
            raise ConfigConsistencyError(issues)
    elif logger is not None and hasattr(logger, "warn"):
        logger.warn(text)
    else:
        print(text)
    return issues
