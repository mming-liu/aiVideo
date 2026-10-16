"""step03_tts_audio.cli - **早期**参数解析（计划 §T4、§15「bootstrap与源码守卫」）.

为什么单独一个模块：`step03_tts_audio/main.py` 顶部会**在 import 时**冻住
`CHAPTER_DIR` / `SCRIPT_FILE` / `AUDIO_OUTPUT_DIR`（以及 `tts.config` 的
`OUTPUT_PATH`），所以运行范围与候选参数必须抢在这些求值**之前**解析完。

约束（与本模块的调用方式一起构成合同）：

- 本模块在 import 期**只允许标准库**（外加同样只依赖标准库的 `shared.run_scope`）：
  一旦在 import 这里 import `shared.paths` / `shared.config`，章号就会被提前冻住；
- :func:`bootstrap` 只做三件事：解析 argv → 把运行范围写进 env（`apply_scope`）→
  把**候选隔离**写进 env（音色注册表路径等）；不建目录、不读脚本、不碰网络；
- 无参数时行为与既有入口完全一致（不设任何候选 env、不打印候选信息）。

命令行形态（§T4 与 §11）：

    python -m step03_tts_audio.main --chapter 2 --plan-only \
        --video-candidates ROOT/video/attempts/<id>/manifest.json --candidate-root ROOT/tts

`--video-candidates` 是**读**的候选 manifest（视频侧，用于解析逐镜素材与来源身份），
`--candidate-root` 是**写**的候选包根（本次 TTS 候选 attempt 落在 `<root>/attempts/<id>/`）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from shared.run_scope import add_scope_arguments, apply_scope

#: 候选参数写进 env 的键（供子模块在**不传参**的深层调用点读到同一份隔离配置）
ENV_CANDIDATE_ROOT = "STEP03_CANDIDATE_ROOT"
ENV_VIDEO_CANDIDATES = "STEP03_VIDEO_CANDIDATES"
ENV_AUDIO_CANDIDATES = "STEP03_AUDIO_CANDIDATES"
ENV_PLAN_ONLY = "STEP03_PLAN_ONLY"
ENV_ATTEMPT_ID = "STEP03_ATTEMPT_ID"
ENV_EXPERIMENT_ID = "STEP03_EXPERIMENT_ID"
#: 候选运行使用的音色注册表（候选根内；**不写生产** global/voice_registry.json）
ENV_VOICE_REGISTRY_PATH = "TTS_VOICE_REGISTRY_PATH"

#: 本模块上一次写进 env 的注册表路径（只用于撤销自己写的那条，见 _register_candidate_env）
_LAST_REGISTRY_PATH = ""


@dataclass
class StageOptions:
    """本次运行的解析结果（main(options=None) 在无参调用时取模块级已解析值）."""

    scope: dict = field(default_factory=dict)
    candidate_root: Optional[str] = None
    video_candidates: Optional[str] = None
    audio_candidates: Optional[str] = None
    plan_only: bool = False
    attempt_id: Optional[str] = None
    experiment_id: Optional[str] = None
    raw_argv: list = field(default_factory=list)

    @property
    def candidate_mode(self) -> bool:
        return bool(self.candidate_root)

    @property
    def chapter(self) -> Optional[int]:
        raw = (self.scope or {}).get("chapter")
        return int(raw) if raw is not None else None

    def as_dict(self) -> dict:
        return {"scope": dict(self.scope or {}),
                "candidate_root": self.candidate_root,
                "video_candidates": self.video_candidates,
                "audio_candidates": self.audio_candidates,
                "plan_only": bool(self.plan_only),
                "attempt_id": self.attempt_id,
                "experiment_id": self.experiment_id}


def add_candidate_arguments(parser) -> None:
    """挂上候选参数（与运行范围参数共用同一个 parser）."""
    parser.add_argument(
        "--candidate-root", default=None, metavar="DIR",
        help="候选包根（本次 TTS 候选 attempt 落在 DIR/attempts/<attempt_id>/；"
             "全部产物/日志/注册表都隔离在这里，不写正式目录）",
    )
    parser.add_argument(
        "--video-candidates", default=None, metavar="FILE",
        help="视频候选 manifest（**读**：解析逐镜素材与来源身份，缺素材显式失败）",
    )
    parser.add_argument(
        "--audio-candidates", default=None, metavar="FILE",
        help="声音候选 manifest（**读**：已有候选音轨的身份期望，供消费侧核对）",
    )
    parser.add_argument(
        "--plan-only", action="store_true",
        help="只生产 SpeechUnit 计划并落候选 manifest，不调用任何 provider",
    )
    parser.add_argument(
        "--attempt-id", default=None, metavar="ID",
        help="指定候选 attempt 目录名（默认按时间生成；已存在的 attempt 拒绝覆盖）",
    )
    parser.add_argument(
        "--experiment-id", default=None, metavar="ID",
        help="实验标识（仅用于记录与来源身份；不用于拼路径——路径由 --candidate-root 给）",
    )


def build_parser(description: str = ""):
    import argparse
    parser = argparse.ArgumentParser(
        description=description or None,
        epilog="未给运行范围参数时，章/部沿用 .env.content 的设置；"
               "不给 --candidate-root 时按原生产路径运行。",
    )
    add_scope_arguments(parser)
    add_candidate_arguments(parser)
    return parser


def parse_argv(argv=None, *, description: str = "") -> StageOptions:
    """严格解析（拼错的参数立刻报错，不静默吞）——**不写任何 env**."""
    parser = build_parser(description)
    args = parser.parse_args(argv)  # 严格：未知参数 → SystemExit(2)
    return StageOptions(
        scope={"chapter": args.chapter, "book": args.book,
               "book_name": args.book_name},
        candidate_root=args.candidate_root,
        video_candidates=args.video_candidates,
        audio_candidates=args.audio_candidates,
        plan_only=bool(args.plan_only),
        attempt_id=args.attempt_id,
        experiment_id=args.experiment_id,
        raw_argv=list(argv) if argv is not None else [],
    )


def bootstrap(description: str = "", argv=None) -> StageOptions:
    """入口顶部调用：解析 → 生效运行范围 → 登记候选隔离。**无副作用地**可重复调用."""
    options = parse_argv(argv, description=description)
    options.scope = apply_scope(options.scope.get("chapter"),
                                options.scope.get("book"),
                                options.scope.get("book_name"))
    _register_candidate_env(options)
    return options


def new_attempt_id(prefix: str = "tts") -> str:
    """候选 attempt 目录名（`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`）.

    本模块在 import 期只允许标准库，所以这里自带一个极小的生成器（不 import tts 包——
    那会把 `shared.paths` 提前拉进来，章号就被冻死在 apply_scope 之前）。
    """
    import random
    import time
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    return "%s-%s-%04d" % (prefix, stamp, random.randint(0, 9999))


def _register_candidate_env(options: StageOptions) -> None:
    """把候选参数写进 env（深层调用点无需逐层传参也能读到同一份隔离配置）.

    候选根一旦给定，音色注册表也**必须**指向候选根内——否则 `get_registry()`
    会把实验分配写进生产 `global/voice_registry.json`（§T4 点名的形态）。
    """
    if options.candidate_root and not options.attempt_id:
        options.attempt_id = new_attempt_id()
    mapping = {
        ENV_CANDIDATE_ROOT: options.candidate_root,
        ENV_VIDEO_CANDIDATES: options.video_candidates,
        ENV_AUDIO_CANDIDATES: options.audio_candidates,
        ENV_ATTEMPT_ID: options.attempt_id,
        ENV_EXPERIMENT_ID: options.experiment_id,
    }
    for key, value in mapping.items():
        if value:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
    if options.plan_only:
        os.environ[ENV_PLAN_ONLY] = "1"
    else:
        os.environ.pop(ENV_PLAN_ONLY, None)
    global _LAST_REGISTRY_PATH
    if options.candidate_root:
        _LAST_REGISTRY_PATH = str(os.path.join(str(options.candidate_root),
                                               "voice_registry.json"))
        os.environ[ENV_VOICE_REGISTRY_PATH] = _LAST_REGISTRY_PATH
    elif _LAST_REGISTRY_PATH and (
            os.environ.get(ENV_VOICE_REGISTRY_PATH) == _LAST_REGISTRY_PATH):
        # 同一进程内二次解析、这次不给候选根：只撤销**我们自己**刚写进去的那条
        # （用户显式设的 TTS_VOICE_REGISTRY_PATH 不能被我们清掉）
        os.environ.pop(ENV_VOICE_REGISTRY_PATH, None)
        _LAST_REGISTRY_PATH = ''


def candidate_root_from_env() -> Optional[str]:
    raw = os.getenv(ENV_CANDIDATE_ROOT, "").strip()
    return raw or None


def plan_only_from_env() -> bool:
    return os.getenv(ENV_PLAN_ONLY, "").strip() == "1"


def attempt_id_from_env() -> Optional[str]:
    raw = os.getenv(ENV_ATTEMPT_ID, "").strip()
    return raw or None


__all__ = ["StageOptions", "bootstrap", "parse_argv", "build_parser",
           "add_candidate_arguments", "candidate_root_from_env", "plan_only_from_env",
           "attempt_id_from_env", "new_attempt_id",
           "ENV_CANDIDATE_ROOT", "ENV_VIDEO_CANDIDATES", "ENV_AUDIO_CANDIDATES",
           "ENV_PLAN_ONLY", "ENV_ATTEMPT_ID", "ENV_EXPERIMENT_ID",
           "ENV_VOICE_REGISTRY_PATH"]
