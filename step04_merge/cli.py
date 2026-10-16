# -*- coding: utf-8 -*-
"""step04_merge.cli - 早期参数解析与候选执行上下文（计划 §A4、§15「bootstrap与源码守卫」）.

两个职责，都**不得**在 import 时读配置/建日志/碰网络：

1. :func:`parse_bootstrap` —— 把旧的运行范围（``--chapter/--book/--book-name``）与
   本阶段新增的增强参数放进**同一个** argparse，解析后调用
   ``shared.run_scope.apply_scope`` 并返回 :class:`BootstrapOptions`。
   它必须能在 ``main.py`` 模块最顶部（任何 ``shared.config`` / ``shared.paths``
   求值之前）被调用——这也是 ``tests/shared/test_run_scope.py`` 与
   ``tests/shared/_stage_head_probe.py`` 盯住的位置契约。
2. :class:`MergeAttemptContext` —— 候选隔离：任一增强参数出现时必须给
   ``--candidate-root``（包 root），本模块在其中建 ``attempts/<attempt_id>/``，
   并把 SRT / TEMP / log / final_tmp / 候选 mp4 全部路由进该 attempt，
   **不**只改 ``FINAL_OUT``。
"""
from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

#: 出现任一即视为"增强模式"（必须配 --candidate-root）
ENHANCEMENT_FLAGS = ("motion_plan", "candidate_root", "candidate_out", "subtitle_timings",
                     "video_candidates", "audio_candidates", "screen_card_plan")

#: 候选产物在 attempt 内的固定子目录
OUTPUTS_DIRNAME = "outputs"
WORK_DIRNAME = "work"
LOGS_DIRNAME = "logs"
DEFAULT_CANDIDATE_NAME = "candidate.mp4"
DEFAULT_SRT_NAME = "subtitles.srt"


@dataclass(frozen=True)
class BootstrapOptions:
    """``parse_bootstrap`` 的结果：scope 生效值 + 原样保留的增强参数。"""

    scope: dict = field(default_factory=dict)
    motion_plan: Path | None = None
    screen_card_plan: Path | None = None
    candidate_root: Path | None = None
    candidate_out: Path | None = None
    subtitle_timings: Path | None = None
    video_candidates: Path | None = None
    audio_candidates: Path | None = None
    argv: tuple = ()

    @property
    def enhancement_flags(self) -> tuple:
        return tuple(name for name in ENHANCEMENT_FLAGS if getattr(self, name) is not None)

    @property
    def enhancement_requested(self) -> bool:
        return bool(self.enhancement_flags)

    def require_candidate_root(self) -> Path:
        if self.candidate_root is None:
            flags = [f"--{name.replace('_', '-')}" for name in self.enhancement_flags]
            raise ValueError(
                f"增强参数 {flags} 必须与 --candidate-root DIR 一起给出"
                "（候选隔离，不写正式产物）")
        return Path(self.candidate_root)


def build_parser(description: str = "") -> argparse.ArgumentParser:
    """注册旧 scope 参数 + 本阶段增强参数的完整 parser（无副作用）。"""
    from shared.run_scope import add_scope_arguments

    parser = argparse.ArgumentParser(
        description=description or "step04 视频合成",
        epilog="未给运行范围参数时，章/部沿用 .env.content 的设置。")
    add_scope_arguments(parser)
    parser.add_argument("--motion-plan", type=Path, default=None, metavar="FILE",
                        help="A 包渲染出的 motion_overlay_plan.v1（title/letter 叠加）")
    parser.add_argument("--screen-card-plan", type=Path, default=None, metavar="FILE",
                        help="screen_card_plan.v1 卡片清单（文字卡的时间线编排输入）。"
                             "**只提供编排输入**，没有改变正文/模式的权限；省略时从"
                             "当前稿生产并核验，新模式**漏传会拒收而不是静默合成无卡视频**")
    parser.add_argument("--candidate-root", type=Path, default=None, metavar="DIR",
                        help="增强实验的**包 root**（<chapter>/_enhancement/<id>/merge）；"
                             "执行器在其下建 attempts/<attempt_id>/")
    parser.add_argument("--candidate-out", type=Path, default=None, metavar="FILE",
                        help="可选候选 mp4 目标，必须落在本 attempt 的 outputs/ 内；"
                             "省略则 outputs/candidate.mp4")
    parser.add_argument("--subtitle-timings", type=Path, default=None, metavar="FILE",
                        help="C 包校验后的 subtitle_timing.v1 对齐候选（仅 tts_mix 对白）")
    parser.add_argument("--video-candidates", type=Path, default=None, metavar="FILE",
                        help="video_candidates.v1 清单（按稳定身份取片，不按镜号猜路径）")
    parser.add_argument("--audio-candidates", type=Path, default=None, metavar="FILE",
                        help="audio_candidates.v1 清单（含 audio_identity.v2 富身份）")
    return parser


def parse_bootstrap(argv=None) -> BootstrapOptions:
    """解析参数并**生效运行范围**；不建目录、不读配置、不建日志。"""
    import sys

    parser = build_parser()
    args = parser.parse_args(argv)
    requested = [name for name in ENHANCEMENT_FLAGS if getattr(args, name) is not None]
    if requested and args.candidate_root is None:
        parser.error(
            "增强参数 "
            + "、".join(f"--{name.replace('_', '-')}" for name in requested)
            + " 必须与 --candidate-root DIR 一起给出（候选隔离；无增强参数才走旧合法路径）")

    from shared.run_scope import apply_scope
    resolved = apply_scope(args.chapter, args.book, args.book_name)
    if args.candidate_root is not None:
        # 目录形状在**解析期**就核（干净退出码 2）；真正的 attempt 目录由调用方建立。
        from shared.enhancement_io import EnhancementError
        try:
            resolve_experiment_id(args.candidate_root)
        except EnhancementError as exc:
            parser.error(str(exc))
    return BootstrapOptions(
        scope=resolved,
        motion_plan=args.motion_plan,
        screen_card_plan=args.screen_card_plan,
        candidate_root=args.candidate_root,
        candidate_out=args.candidate_out,
        subtitle_timings=args.subtitle_timings,
        video_candidates=args.video_candidates,
        audio_candidates=args.audio_candidates,
        argv=tuple(sys.argv[1:] if argv is None else argv),
    )


# ------------------------------------------------------------------ 候选执行上下文

def resolve_experiment_id(package_root, *, chapter: int | None = None) -> str:
    """从包 root 反推 experiment_id，并核路径确实是 ``enhancement_package_dir`` 的形状.

    路径单源在 ``shared.paths``；这里只**核对**，不另拼一套目录层级。
    """
    from shared.enhancement_io import EnhancementError, EXIT_INVALID_INPUT
    from shared.paths import enhancement_package_dir

    root = Path(package_root).resolve()
    experiment_id = root.parent.name
    try:
        expected = enhancement_package_dir("merge", experiment_id, chapter).resolve()
    except ValueError as exc:
        raise EnhancementError(
            "invalid_candidate_root", "paths",
            f"--candidate-root 的布局不符（期望 <chapter_dir>/_enhancement/<实验id>/merge）: "
            f"{root}（{exc}）", exit_code=EXIT_INVALID_INPUT,
            field_path="candidate_root") from exc
    if expected != root:
        raise EnhancementError(
            "invalid_candidate_root", "paths",
            f"--candidate-root 必须是 <chapter_dir>/_enhancement/<实验id>/merge："
            f"实际 {root}，按 paths 单源应为 {expected}", exit_code=EXIT_INVALID_INPUT,
            field_path="candidate_root")
    return experiment_id


@dataclass
class MergeAttemptContext:
    """一次候选执行的路径/锁上下文（SRT、TEMP、log、final_tmp 全在 attempt 内）。"""

    package_root: Path
    experiment_id: str
    attempt_id: str
    root: Path
    outputs_dir: Path
    work_dir: Path
    log_dir: Path
    candidate_out: Path
    srt_path: Path
    scope: str

    # -- 供 shared.paths.execution_log_file(context) 使用 --
    @property
    def attempt_dir(self) -> Path:
        return self.root

    @property
    def log_path(self) -> Path:
        return self.log_dir / "step04.log"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def final_tmp(self) -> Path:
        return self.work_dir / (self.candidate_out.stem + ".tmp.mp4")

    def to_dict(self) -> dict:
        return {"package_root": str(self.package_root), "experiment_id": self.experiment_id,
                "attempt_id": self.attempt_id, "root": str(self.root),
                "outputs_dir": str(self.outputs_dir), "work_dir": str(self.work_dir),
                "log_dir": str(self.log_dir), "candidate_out": str(self.candidate_out),
                "srt_path": str(self.srt_path), "scope": self.scope}


def create_attempt_context(options: BootstrapOptions, *, chapter: int | None = None,
                           attempt_id: str | None = None) -> MergeAttemptContext:
    """在包 root 下建立（或复用）本次 attempt 的目录，并解析候选输出路径。"""
    from shared.enhancement_io import (EnhancementError, EXIT_INVALID_INPUT, assert_attempt_id,
                                       attempt_dir)

    package_root = Path(options.require_candidate_root()).resolve()
    experiment_id = resolve_experiment_id(package_root, chapter=chapter)
    if attempt_id is None:
        attempt_id = "a-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
    assert_attempt_id(attempt_id)
    root = attempt_dir("merge", experiment_id, attempt_id, chapter).resolve()
    outputs_dir = root / OUTPUTS_DIRNAME
    work_dir = root / WORK_DIRNAME
    log_dir = root / LOGS_DIRNAME
    for directory in (outputs_dir, work_dir, log_dir):
        directory.mkdir(parents=True, exist_ok=True)
    if options.candidate_out is not None:
        candidate_out = Path(options.candidate_out)
        if not candidate_out.is_absolute():
            candidate_out = (Path.cwd() / candidate_out)
        candidate_out = candidate_out.resolve()
        if outputs_dir != candidate_out.parent and outputs_dir not in candidate_out.parents:
            raise EnhancementError(
                "candidate_out_outside_attempt", "paths",
                f"--candidate-out 必须落在本 attempt 的 outputs/ 内: {candidate_out} "
                f"（本 attempt outputs: {outputs_dir}）", exit_code=EXIT_INVALID_INPUT,
                field_path="candidate_out")
    else:
        candidate_out = outputs_dir / DEFAULT_CANDIDATE_NAME
    return MergeAttemptContext(
        package_root=package_root, experiment_id=experiment_id, attempt_id=attempt_id,
        root=root, outputs_dir=outputs_dir, work_dir=work_dir, log_dir=log_dir,
        candidate_out=candidate_out, srt_path=outputs_dir / DEFAULT_SRT_NAME,
        scope=experiment_id)


__all__ = [
    "BootstrapOptions", "DEFAULT_CANDIDATE_NAME", "DEFAULT_SRT_NAME", "ENHANCEMENT_FLAGS",
    "MergeAttemptContext", "build_parser", "create_attempt_context", "parse_bootstrap",
    "resolve_experiment_id",
]
