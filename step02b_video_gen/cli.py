"""step02b_video_gen.cli - 早期**无副作用**参数解析（计划 §F4/§15）.

为什么单独一个模块：``main.py`` 在 import 期就会拉进 ``shared.vimax_adapters`` →
``_video_gen.config``（provider/尺寸等配置在 import 时求值），而候选模式还要求
"路径/日志/配置读取之前"运行范围与候选参数就已生效。把解析固定在这里，保证：

1. **解析零副作用**：只读 argv，不建目录、不读配置、不发网络、不写日志；
2. **运行范围先于配置求值**：``bootstrap()`` 立刻 ``apply_scope``，main 不二次解析；
3. **非脚本 import 不解析用户 argv**：``bootstrap()`` 只在 ``__main__`` 块里调用，
   import 本模块（含 pytest 收集）不会触发 ``parse_args``；``main()`` 在 options
   为 None 时取已解析结果，取不到则用**空 argv 的默认值**（不读 sys.argv）。

退出码沿用 §F3 统一集合（``shared.enhancement_io.EXIT_*``）：0 成功（≠内容通过）、
2 输入/身份非法、3 工具/IO 失败、4 明确拒收、5 锁忙/base 变化、6 commit_unconfirmed、
7 submission_unknown/恢复不可用。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

#: 已解析选项（main() 取用，不二次解析）
_PARSED = None

#: 已生效的运行范围（同一份 options 只应用一次，避免重复打印/重复写 env）
_SCOPE_FOR = None


def build_parser() -> argparse.ArgumentParser:
    from shared.run_scope import add_scope_arguments

    parser = argparse.ArgumentParser(
        prog="python -m step02b_video_gen.main",
        description="step02b 视频生成（候选模式：--plan-only / --candidate-root / --resume）",
        epilog="未给运行范围参数时，章/部沿用 .env.content 的设置。",
    )
    parser.add_argument("--shots", "-s", default=None,
                        help="只生成指定镜头（逗号分隔，支持区间），如 3,5-7,9；默认全部")
    parser.add_argument("--force", "-f", action="store_true",
                        help="忽略缓存强制重生成（候选模式下仅跳过可复用缓存，不隐式允许新 POST）")
    parser.add_argument("--plan-only", action="store_true",
                        help="只组装冻结请求 + 预检并落计划文件：零网络、零生成 POST")
    parser.add_argument("--candidate-root", default=None, metavar="DIR",
                        help="候选**包根**：<chapter>/_enhancement/<experiment_id>/video"
                             "（开启候选模式；实验根单源校验不通过即拒收）")
    parser.add_argument("--resume", default=None, metavar="DIR",
                        help="恢复指定 attempt 目录里**已有**的 task（无 ID 不许新生成）")
    parser.add_argument("--all-shots", action="store_true",
                        help="候选模式下显式确认全量镜头（未给 --shots 时必须显式声明）")
    add_scope_arguments(parser)
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    """纯解析（**无副作用**：不动 env、不建目录、不读配置）。"""
    return build_parser().parse_args(argv)


def apply_early_scope(options) -> dict:
    """运行范围必须先于任何 config/paths/logger 求值（计划 §F4）。

    幂等：同一份 options 只应用一次（``bootstrap`` 已应用过时，``main`` 取回同一份
    解析结果，不重复写 env、不重复打印 SCOPE 行）。
    """
    global _SCOPE_FOR
    if _SCOPE_FOR is not None and _SCOPE_FOR[0] is options:
        return _SCOPE_FOR[1]
    from shared.run_scope import apply_scope

    resolved = apply_scope(options.chapter, options.book, options.book_name)
    _SCOPE_FOR = (options, resolved)
    return resolved


def bootstrap(argv=None):
    """脚本入口用：解析 + 立即生效运行范围，并缓存结果供 main() 取用。"""
    global _PARSED
    options = parse_args(argv)
    apply_early_scope(options)
    _PARSED = options
    return options


def cached_options():
    return _PARSED


def options_or_default(argv=None) -> argparse.Namespace:
    """``main()`` 的第二入口：已 bootstrap ⇒ 用它；否则按给定 argv（默认**空**）解析。"""
    if _PARSED is not None:
        return _PARSED
    return parse_args(argv if argv is not None else [])


def parse_shot_selector_safe(value):
    from shared.shot_selector import parse_shot_selector
    return parse_shot_selector(value)


def validate_options(options) -> None:
    """参数组合校验（非法组合显式失败，不静默降级）。"""
    if options.plan_only and options.resume:
        raise ValueError("--plan-only 与 --resume 互斥（前者零网络规划，后者恢复已有任务）")
    if options.resume and options.candidate_root:
        raise ValueError("--resume 直接给 attempt 目录，不再需要 --candidate-root")
    if options.resume and options.force:
        raise ValueError("--resume 不允许 --force（恢复不产生新的生成）")
    if options.plan_only and options.force:
        # 计划层允许带 force 语义（表示"不复用缓存"），但仍然零网络
        pass
    if options.candidate_root and not options.resume:
        if options.shots is None and not options.all_shots:
            raise ValueError(
                "候选模式未明确 --shots：实验分支必须显式给出镜头集合或 --all-shots"
                "（禁止未声明的实验扩量）")


def resolve_experiment_root(candidate_root, *, chapter: Optional[int] = None) -> str:
    """把 ``--candidate-root`` 收敛到实验根单源，返回 ``experiment_id``。

    只接受 ``<chapter_dir>/_enhancement/<experiment_id>/video``（§F1：实验根由
    ``shared.paths.enhancement_package_dir`` 单源计算）；其它形态拒收，避免出现
    第二套路径推导。
    """
    from shared.paths import enhancement_package_dir

    root = Path(candidate_root).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root)
    root = root.resolve()
    if root.name != "video":
        raise ValueError(
            f"--candidate-root 必须是实验的**包根**（…/<experiment_id>/video）：{root}")
    experiment_id = root.parent.name
    expected = enhancement_package_dir("video", experiment_id, chapter).resolve()
    if expected != root:
        raise ValueError(
            f"--candidate-root 与实验根单源不一致：给定 {root}，"
            f"按 shared.paths 推导为 {expected}（不得自行拼另一套实验路径）")
    return experiment_id


def new_attempt_id(label: str) -> str:
    """候选 attempt 身份（每次本地尝试唯一，不复用；见 §F2）。"""
    from shared.video_attempts import new_attempt_id as _new
    return _new(label)


def resolve_candidate_context(options, *, chapter: Optional[int],
                              attempt_id: Optional[str] = None,
                              scope: Optional[dict] = None,
                              script_revision: str = "",
                              source: str = ""):
    """由 ``--candidate-root`` 构造候选上下文（路径守卫单源 shared.enhancement_io）。

    ``scope`` / ``script_revision`` / ``source`` 是**采纳 manifest 的来源身份**：
    入口必须把本次运行的真实范围与稿件 revision 传进来（消费方据此判"是不是这一章
    这一稿"）；缺了会被采纳出口拒收，而不是记成空身份。
    """
    from shared.enhancement_io import CandidateContext

    experiment_id = resolve_experiment_root(options.candidate_root, chapter=chapter)
    ctx = CandidateContext(experiment_id=experiment_id, package="video",
                           attempt_id=attempt_id or new_attempt_id("shot"),
                           chapter=chapter, scope=dict(scope or {}),
                           script_revision=str(script_revision or ""),
                           source=str(source or ""))
    return ctx
