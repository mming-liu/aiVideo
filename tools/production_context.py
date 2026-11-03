# -*- coding: utf-8 -*-
"""production_context - 从 step01 采纳凭据建立后续阶段的运行上下文（P02 单源）.

**为什么需要它**：正式章节目录在 `output/<小说>/part_NN_<部>/chapter_NNN`，而
`.env.content` 的 `OUTPUT_BASE` 默认是 `./output/video`；`DURATION_*` 在
`shared/config.py` 里是 **import 期求值**的常量，`shared/quality_gate.py` 又用
它们核 `step01_commit.json` 里的 `duration_band`。两者任一不一致，下游阶段会在
`_require_duration_band` 处被判 `duration_band mismatch`——而错误信息只会说
"re-run the upstream stage"（重跑上游同样会失败，因为上游其实没问题）。

本模块把"采纳时到底用了哪套输出根/时长合同/章部范围"变成**机器可读的输入**：

1. :func:`load_receipt` 读 `step01_adoption.json`（schema=step01.formal_adoption/1）；
2. :func:`apply_context` 在 **import 任何阶段模块之前** 把 `OUTPUT_BASE` /
   `NOVEL_*` / `BOOK_*` / `DURATION_*` / `HOOK_ENABLED` 写进 `os.environ`；
3. :func:`verify` 用正式入口（`shared.quality_gate.require_step01_upstream`）
   核提交清单、三份产物摘要与 revision，并核对 `shared.paths` 解析出的章目录
   确实是被采纳的那个（不按"最新目录"猜）；
4. `--run` 把同一套 env 交给子进程——子进程是全新解释器，import 顺序天然正确。

典型用法::

    # 只核上下文（零模型调用、零写入）
    python tools/production_context.py --receipt <正式章>/step01_adoption.json

    # 写运行记录（P01—P14 进度表 + 已验证身份）
    python tools/production_context.py --receipt ... --write-record

    # 带着正确的 OUTPUT_BASE / 时长合同跑下游阶段
    python tools/production_context.py --receipt ... --run -- python step02a_frame_gen/main.py --shots 1

失败语义：凭据不可读/字段缺失 -> 退出码 3；身份或摘要不符、import 顺序违反了
"env 先于 shared.config" -> 退出码 2。任一失败都**不**写记录、不启动子进程。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:                     # 供 ~python tools/xxx.py~ 直接运行
    sys.path.insert(0, str(ROOT))

RECEIPT_SCHEMA = "step01.formal_adoption/1"
RECORD_SCHEMA = "production.run_context/1"
DEFAULT_PHASES = ("P01", "P02", "P03", "P04", "P05", "P06", "P07",
                  "P08", "P09", "P10", "P11", "P12", "P13", "P14")

#: 必须与采纳时一致的环境键。**只放"会改变下游判定"的键**：模型/通道选择
#: （IMAGE_PROVIDER / VIDEO_PROVIDER / TTS_PROVIDER / VIDEO_REFERENCE_MODE）
#: 属于"本次实际调用"的事实，应在费用关口核对，不在此处静默覆盖。
CONTEXT_ENV_KEYS = (
    "OUTPUT_BASE", "NOVEL_NAME", "BOOK_INDEX", "BOOK_NAME", "NOVEL_CHAPTER",
    "HOOK_ENABLED",
    "DURATION_MIN_SEC", "DURATION_MAX_SEC",
    "DURATION_TARGET_MIN_SEC", "DURATION_TARGET_MAX_SEC",
    "VOICE_ADAPTATION_ENABLED",
    # 画风决定前缀/负向词/图片与视频内容哈希；必须在采纳时锁定，
    # 不能让同一章因后续 .env.content 改动而静默换画风。
    "VIDEO_STYLE",
)

#: 凭据里 `effective_config` 允许携带的键（其余忽略并记入 ignored_keys）。
CONFIG_KEYS = ("HOOK_ENABLED", "DURATION_MIN_SEC", "DURATION_MAX_SEC",
               "DURATION_TARGET_MIN_SEC", "DURATION_TARGET_MAX_SEC",
               "VOICE_ADAPTATION_ENABLED", "VIDEO_STYLE")


#: ~shared.config.duration_band()~ 的键名与 env 键名不同，比较时按这张表换算
#: （写死对应关系，避免"两个地方各自拼一遍 key"——拼错就会把合法上下文判成不符）。
_BAND_ENV_TO_KEY = {
    "DURATION_MIN_SEC": "min",
    "DURATION_MAX_SEC": "max",
    "DURATION_TARGET_MIN_SEC": "target_min",
    "DURATION_TARGET_MAX_SEC": "target_max",
}


class ContextError(RuntimeError):
    """上下文不可用（带稳定 code，供 CLI 退出码与记录使用）。"""

    def __init__(self, code: str, message: str, **fields):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.fields = dict(fields)

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, **self.fields}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 1) 凭据
# ---------------------------------------------------------------------------
def load_receipt(path) -> dict:
    """读并校验采纳凭据；只做**结构**校验，身份核对在 :func:`verify` 里."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContextError("receipt_unreadable", "采纳凭据不可读：%s" % exc,
                           path=str(p)) from exc
    except json.JSONDecodeError as exc:
        raise ContextError("receipt_not_json", "采纳凭据不是合法 JSON：%s" % exc,
                           path=str(p)) from exc
    if not isinstance(raw, dict):
        raise ContextError("receipt_not_object", "采纳凭据不是 JSON 对象", path=str(p))
    if str(raw.get("schema") or "") != RECEIPT_SCHEMA:
        raise ContextError("receipt_schema_mismatch",
                           "采纳凭据 schema 不是 %s：%r" % (RECEIPT_SCHEMA, raw.get("schema")),
                           path=str(p))
    status = str(raw.get("status") or "")
    if status != "adopted_and_verified":
        raise ContextError("receipt_not_adopted",
                           "采纳凭据 status 不是 adopted_and_verified：%r" % status,
                           path=str(p))
    for key in ("formal_chapter", "output_base_override", "script_revision", "scope"):
        if not raw.get(key):
            raise ContextError("receipt_field_missing", "采纳凭据缺字段 %s" % key,
                               path=str(p), field=key)
    if not isinstance(raw.get("scope"), dict):
        raise ContextError("receipt_field_invalid", "scope 不是对象", path=str(p))
    if not isinstance(raw.get("formal_sha256"), dict) or \
            not raw["formal_sha256"].get("script_result.json"):
        raise ContextError("receipt_field_missing",
                           "采纳凭据缺 formal_sha256['script_result.json']", path=str(p))
    raw["_receipt_path"] = str(p)
    raw["_receipt_sha256"] = _sha256_file(p)
    return raw


def context_env(receipt: dict) -> dict:
    """凭据 -> 应生效的环境变量（全部为字符串，值来自凭据而非当前 .env）."""
    scope = receipt["scope"]
    env = {
        "OUTPUT_BASE": str(receipt["output_base_override"]),
        "NOVEL_NAME": str(scope.get("novel") or ""),
        "BOOK_INDEX": str(scope.get("book_index") or 1),
        "BOOK_NAME": str(scope.get("book_name") or ""),
        "NOVEL_CHAPTER": str(scope.get("chapter") or 1),
    }
    cfg = receipt.get("effective_config") or {}
    if isinstance(cfg, dict):
        for key in CONFIG_KEYS:
            if cfg.get(key) is not None:
                env[key] = str(cfg[key])
    return env


def apply_context(receipt: dict) -> dict:
    """把上下文写进 `os.environ`（幂等）.

    **必须在 import** `shared.config` / `shared.paths` / 任何阶段模块**之前**调用：
    `shared/config.py` 的 `DURATION_*` 是 import 期常量，晚于 import 的写入对
    已经加载的模块无效（:func:`verify` 会显式检出这种情形）。
    """
    env = context_env(receipt)
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    changed = {k: (previous[k], env[k]) for k in env if previous[k] != env[k]}
    return {"applied": env, "previous": previous, "changed": changed}


# ---------------------------------------------------------------------------
# 2) 核验（正式入口）
# ---------------------------------------------------------------------------
def _same_path(a, b) -> bool:
    if not a or not b:
        return False
    try:
        pa = Path(a).resolve()
        pb = Path(b).resolve()
    except OSError:
        return False
    return os.path.normcase(str(pa)) == os.path.normcase(str(pb))


def verify(receipt: dict, *, apply: bool = True) -> dict:
    """核验当前进程上下文确实指向被采纳的那一章（fail-closed）.

    检查项：env 与凭据一致 -> `shared.paths` 解析出的章目录 == `formal_chapter`
    -> `script_result.json` 的 SHA-256 与 revision -> 语义门 + 提交清单
    （`require_step01_upstream`）。任一项失败抛 :class:`ContextError`。
    """
    if apply:
        apply_context(receipt)

    # import 顺序守卫：shared.config 若在本进程里已被先行 import，其 DURATION_* 是
    # 旧值——此时 gate 会用错的时长带核 manifest，报出来的却是"上游要重跑"。
    from shared import config as _cfg
    want_band = {_BAND_ENV_TO_KEY[k]: float(v)
                 for k, v in context_env(receipt).items() if k in _BAND_ENV_TO_KEY}
    have_band = _cfg.duration_band()
    if want_band and any(float(have_band[k]) != v for k, v in want_band.items()):
        raise ContextError(
            "import_order_violation",
            "shared.config 已按旧环境求值，DURATION_* 常量与本凭据不符："
            "current=%r expected=%r；请在 import 阶段模块之前调用 apply_context()，"
            "或改用本工具的 --run 启动子进程" % (have_band, want_band),
            current=have_band, expected=want_band)

    from shared.paths import chapter_dir
    from shared.quality_gate import require_step01_upstream

    ch = Path(chapter_dir())
    if not _same_path(ch, receipt["formal_chapter"]):
        raise ContextError("chapter_dir_mismatch",
                           "shared.paths 解析到 %s，但凭据的正式目录是 %s"
                           % (ch, receipt["formal_chapter"]),
                           resolved=str(ch), expected=str(receipt["formal_chapter"]))

    script_path = ch / "script_result.json"
    if not script_path.exists():
        raise ContextError("script_missing", "正式 script_result.json 不存在",
                           path=str(script_path))
    actual_sha = _sha256_file(script_path)
    expected_sha = str((receipt.get("formal_sha256") or {}).get("script_result.json") or "")
    if actual_sha != expected_sha:
        raise ContextError("script_digest_mismatch",
                           "script_result.json 与采纳凭据的摘要不符（正式产物被改动？）",
                           expected=expected_sha, actual=actual_sha)

    try:
        script = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextError("script_unreadable", "正式 script_result.json 不可读：%s" % exc) from exc

    revision = str(script.get("script_revision") or "")
    if revision != str(receipt["script_revision"]):
        raise ContextError("revision_mismatch", "script_revision 与凭据不符",
                           expected=str(receipt["script_revision"]), actual=revision)

    expected_chapter = int((receipt.get("scope") or {}).get("chapter") or 0) or None
    try:
        manifest = require_step01_upstream(ch, script, stage="step01",
                                           expected_chapter=expected_chapter)
    except Exception as exc:                                  # noqa: BLE001 - 统一成 code
        raise ContextError("upstream_gate_failed", "step01 准入未通过：%s" % exc) from exc

    timeline = script.get("timeline") or []
    return {
        "chapter_dir": str(ch),
        "script_sha256": actual_sha,
        "script_revision": revision,
        "commit_status": str(manifest.get("status") or ""),
        "commit_run_id": str(manifest.get("run_id") or ""),
        "duration_band": manifest.get("duration_band"),
        "blocking_count": manifest.get("blocking_count"),
        "body_shots": len(script.get("shots") or []),
        "hook_present": bool(isinstance(script.get("hook"), dict) and script["hook"].get("prompt")),
        "total_duration": script.get("total_duration"),
        "timeline_entries": len(timeline),
    }


# ---------------------------------------------------------------------------
# 3) 运行记录
# ---------------------------------------------------------------------------
def default_record_path(receipt: dict) -> Path:
    """运行记录落在正式章目录的 `_production/` 子目录.

    **不放在章目录顶层**：`step02b_video_gen.main.production_snapshot` 会把章目录
    顶层的 `*.json/*.log/*.mp4/*.srt` 纳入"正式产物是否被动过"的逐文件摘要，
    运行记录本身不该出现在那份对比里。
    """
    return Path(receipt["formal_chapter"]) / "_production" / "run_context.json"


def build_record(receipt: dict, verified: dict, *, phases: "dict | None" = None,
                 extra: "dict | None" = None) -> dict:
    env = context_env(receipt)
    record = {
        "schema": RECORD_SCHEMA,
        "created_at": _now(),
        "receipt": {"path": receipt["_receipt_path"],
                    "sha256": receipt["_receipt_sha256"],
                    "source_run_id": receipt.get("source_run_id")},
        "formal_chapter": verified["chapter_dir"],
        "script_revision": verified["script_revision"],
        "scope": dict(receipt.get("scope") or {}),
        "effective_config": {k: env[k] for k in CONTEXT_ENV_KEYS if k in env},
        "verification": {
            "script_sha256": verified["script_sha256"],
            "commit_status": verified["commit_status"],
            "commit_run_id": verified["commit_run_id"],
            "duration_band": verified["duration_band"],
            "blocking_count": verified["blocking_count"],
            "gate": "passed",
            "model_sends": 0,
        },
        "script_facts": {
            "body_shots": verified["body_shots"],
            "hook_present": verified["hook_present"],
            "total_duration": verified["total_duration"],
            "timeline_entries": verified["timeline_entries"],
        },
        "phases": dict(phases or {k: "pending" for k in DEFAULT_PHASES}),
        "limitations": list(receipt.get("limitations") or []),
    }
    if extra:
        record.update(extra)
    return record


def write_record(path, record: dict) -> Path:
    """原子写（先写 .tmp 再 replace）：半个 JSON 比没有记录更危险."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def load_record(path) -> "dict | None":
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None




# ---------------------------------------------------------------------------
# 5) 门禁离线重签（S10R：零模型调用）
# ---------------------------------------------------------------------------
REFRESH_SCHEMA = "step01.gate_refresh/1"
REFRESH_ARTIFACTS = ("step01_quality.json", "step01_audit.json",
                     "step01_commit.json", "step01_adoption.json")


def _staging_root() -> Path:
    """可写的暂存根：优先用调用方给的（沙箱里由上层预建），否则临时目录."""
    env = os.environ.get("PRODUCTION_CONTEXT_STAGING")
    if env:
        root = Path(env)
        root.mkdir(parents=True, exist_ok=True)
        return root
    import tempfile
    return Path(tempfile.mkdtemp(prefix="pc_stage_"))


def _chapter_rel(chapter_dir: Path) -> Path:
    """章目录相对 output 根的路径（暂存时保持同构，便于逐文件核对）."""
    parts = list(Path(chapter_dir).parts)
    try:
        i = parts.index("output")
    except ValueError:
        return Path(Path(chapter_dir).name)
    return Path(*parts[i + 1:])


def _read_novel_text(chapter: int, novel_file: "str | None") -> str:
    """读本章原文；**禁用场景分析**（该分析会真实调用文本模型，违反 model_sends=0）.

    `shared.chapter_reader._analyzing_scenes` 是既有的防递归守卫：置 True 时
    `_get_chapter_scenes` 直接返回空分析、不调用模型、不写缓存；而
    `filter_section_by_chapter` 当前只记录分析结果、不过滤正文（见其 docstring），
    因此原文与有缓存时一致。
    """
    import shared.chapter_reader as chapter_reader
    import step01_script_gen.main as step01_main

    previous = chapter_reader._analyzing_scenes
    chapter_reader._analyzing_scenes = True
    try:
        if novel_file:
            return step01_main.read_chapter(novel_file, chapter=chapter)
        return step01_main.read_chapter(chapter=chapter)
    finally:
        chapter_reader._analyzing_scenes = previous


def build_refreshed_gate(receipt: dict, *, novel_file: "str | None" = None) -> dict:
    """在暂存目录重建四份门禁产物（不触碰正式目录、零模型调用）.

    返回 `{name: 文本}`；调用方按 quality → audit → commit → adoption 顺序落盘
    （manifest 最后写，崩溃时摘要对不上即拒用）。
    """
    import json as _json

    apply_context(receipt)
    ch = Path(receipt["formal_chapter"])
    script_path = ch / "script_result.json"
    script = _json.loads(script_path.read_text(encoding="utf-8"))

    root = _staging_root()
    staging = root / _chapter_rel(ch)
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "script_result.json").write_bytes(script_path.read_bytes())

    # 导入 step01.main 会按 OUTPUT_BASE 建日志；确定性检查器运行时也会写日志。
    # 暂存根可写、正式根在沙箱里不可写，因此整段构建期都把 OUTPUT_BASE 指向暂存根
    # （构建只依赖 script 字典与原文，不依赖正式目录里的其它产物）。
    previous_base = os.environ.get("OUTPUT_BASE")
    os.environ["OUTPUT_BASE"] = str(root)
    try:
        import step01_script_gen.main as step01_main
        novel_text = _read_novel_text(
            int((receipt.get("scope") or {}).get("chapter") or 1), novel_file)
        quality = step01_main._build_step01_quality_gate(script, novel_text)
    finally:
        if previous_base is None:
            os.environ.pop("OUTPUT_BASE", None)
        else:
            os.environ["OUTPUT_BASE"] = previous_base

    from shared.quality_gate import (QualityGateError, build_step01_commit_manifest,
                                     ensure_script_revision, sha1_file)
    revision = ensure_script_revision(script)
    if quality.get("status") != "PASS" or quality.get("blocking"):
        raise ContextError(
            "refresh_gate_blocked",
            "确定性质量门未通过（status=%s blocking=%s hard=%s visual=%s）："
            "不得用重签掩盖真实 blocking" % (
                quality.get("status"), quality.get("blocking"),
                quality.get("hard_count"), quality.get("visual_risk_count")),
            categories=quality.get("blocking_categories") or [])
    if str(quality.get("script_revision") or "") != revision or \
            revision != str(receipt.get("script_revision") or ""):
        raise ContextError("refresh_revision_mismatch",
                           "重算质量门的 revision 与采纳凭据不一致",
                           expected=str(receipt.get("script_revision") or ""),
                           actual=str(quality.get("script_revision") or ""))

    audit = _json.loads((ch / "step01_audit.json").read_text(encoding="utf-8"))
    if str(audit.get("status") or "").strip().lower() != "ok":
        raise ContextError("refresh_audit_not_ok",
                           "原审计结论不是 ok，不能离线重签：status=%r" % audit.get("status"))
    if str(audit.get("input_revision") or "") != revision:
        raise ContextError("refresh_audit_revision_mismatch",
                           "原审计 input_revision 与当前 revision 不一致",
                           audit=str(audit.get("input_revision") or ""), current=revision)
    blocking_sev = {"hard", "visual_risk", "blocking"}
    blocking = [i for i in (audit.get("issues") or [])
                if str((i or {}).get("severity") or "").strip() in blocking_sev]
    if blocking:
        raise ContextError("refresh_audit_blocking",
                           "原审计仍含 blocking 条目，不能离线重签",
                           blocking=len(blocking))
    if not str(audit.get("audit_scope") or "").strip():
        raise ContextError("refresh_audit_scope_missing", "原审计缺 audit_scope")

    previous_fp = str(quality.get("rule_source_fingerprint") or "")
    old_audit_fp = str(audit.get("rule_source_fingerprint") or "")
    audit["rule_source_fingerprint"] = previous_fp
    audit["duration_band"] = quality.get("duration_band")
    audit["reverified_from_fingerprint"] = old_audit_fp
    audit["reverified_at"] = _now()
    audit["reverified_by"] = REFRESH_SCHEMA

    from shared.json_io import atomic_dump_json
    atomic_dump_json(staging / "step01_quality.json", quality, ensure_ascii=False, indent=2)
    atomic_dump_json(staging / "step01_audit.json", audit, ensure_ascii=False, indent=2)

    previous_manifest = {}
    try:
        previous_manifest = _json.loads((ch / "step01_commit.json").read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        previous_manifest = {}
    manifest = build_step01_commit_manifest(
        run_id=str(previous_manifest.get("run_id") or ""),
        created_at=str(previous_manifest.get("created_at") or _now()),
        revision=revision,
        rule_source_fingerprint=previous_fp,
        rule_version=str(quality.get("rule_version") or ""),
        audit_scope=str(audit.get("audit_scope") or ""),
        chapter_dir=staging,
        status="COMMITTED",
        blocking=[],
        detail="gate refreshed offline (rule-source decoupled); script bytes unchanged",
        prompt_source_fingerprint=str(previous_manifest.get("prompt_source_fingerprint") or ""),
        audit_status=str(audit.get("status") or ""),
    )
    manifest["gate_refresh"] = {
        "schema": REFRESH_SCHEMA,
        "at": _now(),
        "from_fingerprint": old_audit_fp,
        "to_fingerprint": previous_fp,
        "script_sha1": sha1_file(staging / "script_result.json"),
        "model_sends": 0,
    }
    atomic_dump_json(staging / "step01_commit.json", manifest, ensure_ascii=False, indent=2)

    adoption = _json.loads((ch / "step01_adoption.json").read_text(encoding="utf-8"))
    formal = dict(adoption.get("formal_sha256") or {})
    for name in ("script_result.json", "step01_quality.json", "step01_audit.json",
                 "step01_commit.json"):
        # adoption 的 formal_sha256 全部是 SHA-256（与 verify() 的 _sha256_file 对齐）；
        # commit manifest 的 artifacts 才是 SHA-1。两者不能混用。
        formal[name] = _sha256_file(staging / name)
    adoption["formal_sha256"] = formal
    adoption["gate_refresh"] = {
        "schema": REFRESH_SCHEMA,
        "at": _now(),
        "from_fingerprint": old_audit_fp,
        "to_fingerprint": previous_fp,
        "script_sha256": _sha256_file(staging / "script_result.json"),
        "script_revision": revision,
        "duration_band": quality.get("duration_band"),
        "model_sends": 0,
    }
    atomic_dump_json(staging / "step01_adoption.json", adoption, ensure_ascii=False, indent=2)

    return {name: (staging / name).read_text(encoding="utf-8")
            for name in REFRESH_ARTIFACTS}


def build_shot_patch_gate(receipt: dict, new_script: dict, *, patch_meta: dict,
                          novel_file: "str | None" = None) -> tuple:
    """单镜画面字段补丁：离线重建 quality/audit/commit/adoption（零模型调用）.

    只允许改非锁定画面字段；调用方必须先做逐镜/锁定契约/质量门校验。
    返回 (files, new_adoption, revision)。
    """
    import json as _json

    apply_context(receipt)
    ch = Path(receipt["formal_chapter"])
    root = _staging_root()
    staging = root / _chapter_rel(ch)
    staging.mkdir(parents=True, exist_ok=True)

    previous_base = os.environ.get("OUTPUT_BASE")
    os.environ["OUTPUT_BASE"] = str(root)
    try:
        import step01_script_gen.main as step01_main
        novel_text = _read_novel_text(
            int((receipt.get("scope") or {}).get("chapter") or 1), novel_file)
        quality = step01_main._build_step01_quality_gate(new_script, novel_text)
    finally:
        if previous_base is None:
            os.environ.pop("OUTPUT_BASE", None)
        else:
            os.environ["OUTPUT_BASE"] = previous_base

    from shared.quality_gate import build_step01_commit_manifest, ensure_script_revision
    revision = ensure_script_revision(new_script, force=True)
    if quality.get("status") != "PASS" or quality.get("blocking"):
        raise ContextError(
            "shot_patch_gate_blocked",
            "补丁后质量门未通过（status=%s blocking=%s hard=%s visual=%s）"
            % (quality.get("status"), quality.get("blocking"),
               quality.get("hard_count"), quality.get("visual_risk_count")),
            categories=quality.get("blocking_categories") or [])
    if str(quality.get("script_revision") or "") != revision:
        raise ContextError("shot_patch_revision_mismatch",
                           "质量门 revision 与重算结果不一致",
                           gate=str(quality.get("script_revision") or ""), actual=revision)

    from shared.json_io import atomic_dump_json
    # 先算 revision/质量门（会写回 script_revision），再落暂存脚本；
    # 否则暂存脚本缺新 revision，adoption 的 sha 与实际文件对不上。
    atomic_dump_json(staging / "script_result.json", new_script,
                     ensure_ascii=False, indent=2)

    audit = _json.loads((ch / "step01_audit.json").read_text(encoding="utf-8"))
    if str(audit.get("status") or "").strip().lower() != "ok":
        raise ContextError("shot_patch_audit_not_ok",
                           "原审计不是 ok，不能离线重签：%r" % audit.get("status"))
    old_audit_fp = str(audit.get("rule_source_fingerprint") or "")
    audit["input_revision"] = revision
    audit["rule_source_fingerprint"] = str(quality.get("rule_source_fingerprint") or "")
    audit["duration_band"] = quality.get("duration_band")
    audit["reverified_from_fingerprint"] = old_audit_fp
    audit["reverified_at"] = _now()
    audit["reverified_by"] = "shot_patch.v1"
    audit["shot_patch"] = dict(patch_meta)

    from shared.json_io import atomic_dump_json
    atomic_dump_json(staging / "step01_quality.json", quality, ensure_ascii=False, indent=2)
    atomic_dump_json(staging / "step01_audit.json", audit, ensure_ascii=False, indent=2)

    previous_manifest = {}
    try:
        previous_manifest = _json.loads((ch / "step01_commit.json").read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        previous_manifest = {}
    manifest = build_step01_commit_manifest(
        run_id=str(previous_manifest.get("run_id") or ""),
        created_at=_now(),
        revision=revision,
        rule_source_fingerprint=str(quality.get("rule_source_fingerprint") or ""),
        rule_version=str(quality.get("rule_version") or ""),
        audit_scope=str(audit.get("audit_scope") or ""),
        chapter_dir=staging,
        status="COMMITTED",
        blocking=[],
        detail="single-shot visual patch (zero model sends)",
        prompt_source_fingerprint=str(previous_manifest.get("prompt_source_fingerprint") or ""),
        audit_status=str(audit.get("status") or ""),
    )
    manifest["shot_patch"] = dict(patch_meta)
    atomic_dump_json(staging / "step01_commit.json", manifest, ensure_ascii=False, indent=2)

    adoption = _json.loads((ch / "step01_adoption.json").read_text(encoding="utf-8"))
    formal = dict(adoption.get("formal_sha256") or {})
    for name in ("script_result.json", "step01_quality.json", "step01_audit.json",
                 "step01_commit.json"):
        formal[name] = _sha256_file(staging / name)
    adoption["formal_sha256"] = formal
    adoption["script_revision"] = revision
    adoption["shot_patch"] = dict(patch_meta)
    atomic_dump_json(staging / "step01_adoption.json", adoption, ensure_ascii=False, indent=2)

    files = {name: (staging / name).read_text(encoding="utf-8")
             for name in REFRESH_ARTIFACTS}
    return files, adoption, revision


def apply_refreshed_gate(receipt: dict, files: dict) -> list:
    """按 quality → audit → commit → adoption 顺序原子替换正式产物."""
    from shared.json_io import atomic_dump_json
    ch = Path(receipt["formal_chapter"])
    written = []
    for name in REFRESH_ARTIFACTS:
        payload = json.loads(files[name])
        atomic_dump_json(ch / name, payload, ensure_ascii=False, indent=2)
        written.append(name)
    return written


def parse_phase_updates(items) -> dict:
    out = {}
    for item in items or ():
        text = str(item or "")
        if "=" not in text:
            raise ContextError("phase_update_invalid", "阶段更新格式应为 P02=completed：%r" % text)
        key, value = text.split("=", 1)
        key, value = key.strip().upper(), value.strip()
        if key not in DEFAULT_PHASES:
            raise ContextError("phase_update_invalid", "未知阶段编号：%r" % key)
        if value not in ("pending", "in_progress", "completed", "blocked"):
            raise ContextError("phase_update_invalid", "未知阶段状态：%r" % value)
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# 4) CLI
# ---------------------------------------------------------------------------
def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


SHOT_PATCH_ALLOWED_FIELDS = frozenset({
    "prompt", "ff_desc", "lf_desc", "motion_desc", "camera_movement",
    "frame_layouts", "keyframes",
})


def apply_shot_patch(script: dict, patch: dict, shot_index: int) -> tuple:
    """把单镜画面字段补丁应用到脚本副本，并校验逐镜/锁定契约不变量."""
    import copy
    import step01_script_gen.main as step01_main
    from shared.quality_gate import changed_shot_keys, per_shot_revisions

    if not isinstance(patch, dict):
        raise ContextError("shot_patch_invalid", "补丁必须是 JSON 对象")
    if int(patch.get("shot_index") or 0) != int(shot_index):
        raise ContextError("shot_patch_index_mismatch",
                           "补丁 shot_index=%r 与 --shot=%r 不一致"
                           % (patch.get("shot_index"), shot_index))
    changes = patch.get("set") or {}
    if not isinstance(changes, dict) or not changes:
        raise ContextError("shot_patch_empty", "补丁 set 不能为空")
    bad = sorted(set(changes) - SHOT_PATCH_ALLOWED_FIELDS)
    if bad:
        raise ContextError("shot_patch_field_not_allowed",
                           "不允许通过补丁修改字段：%r" % bad, fields=bad)

    before = copy.deepcopy(script)
    after = copy.deepcopy(script)
    target = next((s for s in (after.get("shots") or [])
                   if isinstance(s, dict) and int(s.get("index", -1)) == int(shot_index)), None)
    if target is None:
        raise ContextError("shot_patch_shot_missing", "脚本里没有 shot %r" % shot_index)
    for key, value in changes.items():
        target[key] = copy.deepcopy(value)

    changed = changed_shot_keys(per_shot_revisions(before), per_shot_revisions(after))
    if changed != [int(shot_index)]:
        raise ContextError("shot_patch_changed_other_shots",
                           "补丁影响了目标之外的镜头：%r" % changed, changed=changed)
    if step01_main._locked_repair_signature(before) != step01_main._locked_repair_signature(after):
        raise ContextError("shot_patch_locked_field_changed",
                           "补丁改动了锁定合同字段（duration/subtitle/narration/plan_refs 等）")

    from shared.prompt_contradiction import assert_no_contradictions
    for field in ("ff_desc", "lf_desc"):
        if field in changes:
            assert_no_contradictions(str(target.get(field) or ""),
                                     "shot%d %s" % (int(shot_index), field))
    return after, {"shot_index": int(shot_index), "changed_fields": sorted(changes)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从 step01 采纳凭据建立后续运行上下文")
    ap.add_argument("--receipt", required=True, help="step01_adoption.json 路径")
    ap.add_argument("--write-record", action="store_true", help="写运行记录（默认路径见 --record-out）")
    ap.add_argument("--record-out", default=None, help="运行记录路径（默认 <正式章>/_production/run_context.json）")
    ap.add_argument("--phase", action="append", default=None, metavar="ID=STATE",
                    help="更新阶段状态，如 --phase P02=completed（可重复）")
    ap.add_argument("--expect-chapter", type=int, default=None, help="额外核对章号")
    ap.add_argument("--print-record", action="store_true",
                    help="只把运行记录 JSON 打到 stdout（沙箱不允许子进程写 output/ 时，"
                         "交由上层用文件 API 落盘；内容与 --write-record 同一构造函数）")
    ap.add_argument("--refresh-gate", action="store_true",
                    help="零模型调用重签 step01 门禁（重建 quality/audit/commit/adoption）")
    ap.add_argument("--print-refresh", action="store_true",
                    help="配合 --refresh-gate：只把四份产物 JSON 打到 stdout，由上层落盘")
    ap.add_argument("--novel-file", default=None,
                    help="重签时读取的原文文件（默认沿用 step01 入口的默认小说路径）")
    ap.add_argument("--apply-shot-patch", default=None, metavar="PATCH",
                    help="应用单镜画面字段补丁（JSON；零模型调用，默认 dry-run）")
    ap.add_argument("--shot", type=int, default=None, help="配合 --apply-shot-patch 的镜头号")
    ap.add_argument("--write", action="store_true", help="配合 --apply-shot-patch：落盘脚本与四份门禁")
    ap.add_argument("--run", action="store_true",
                    help="核验通过后用同一套 env 启动子进程：--run -- python step02a_frame_gen/main.py ...")
    # `--run -- <cmd>` 必须在 argparse 之前手工切开：argparse 会把 `--` 当成"选项结束"，
    # REMAINDER 拿不到后面的命令（实测报 unrecognized arguments）。
    raw = list(sys.argv[1:] if argv is None else argv)
    run_cmd: list = []
    if "--run" in raw:
        cut = raw.index("--run")
        run_cmd = raw[cut + 1:]
        if run_cmd and run_cmd[0] == "--":
            run_cmd = run_cmd[1:]
        raw = raw[:cut] + ["--run"]
    args = ap.parse_args(raw)

    try:
        receipt = load_receipt(args.receipt)
        env_plan = context_env(receipt)
        unknown = sorted(k for k in (receipt.get("effective_config") or {}) if k not in CONFIG_KEYS)
        if args.expect_chapter is not None:
            got = int((receipt.get("scope") or {}).get("chapter") or 0)
            if got != args.expect_chapter:
                raise ContextError("chapter_request_mismatch",
                                   "请求第 %d 章，凭据是第 %d 章" % (args.expect_chapter, got))
        if args.apply_shot_patch:
            if args.shot is None:
                raise ContextError("shot_patch_shot_missing", "--apply-shot-patch 需要 --shot")
            patch_path = Path(args.apply_shot_patch)
            try:
                patch = json.loads(patch_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ContextError("shot_patch_unreadable", "补丁不可读：%s" % exc) from exc
            ch = Path(receipt["formal_chapter"])
            script_path = ch / "script_result.json"
            script = json.loads(script_path.read_text(encoding="utf-8"))
            new_script, patch_meta = apply_shot_patch(script, patch, args.shot)
            files, adoption, revision = build_shot_patch_gate(
                receipt, new_script, patch_meta=patch_meta, novel_file=args.novel_file)
            if not args.write:
                _print_json({"schema": "production.shot_patch/1", "ok": True,
                             "dry_run": True, "revision": revision,
                             "before_revision": receipt.get("script_revision"),
                             "meta": patch_meta})
                return 0
            written = apply_refreshed_gate(receipt, files)
            from shared.json_io import atomic_dump_json
            atomic_dump_json(script_path, new_script, ensure_ascii=False, indent=2)
            record_dir = ch / "_production" / "shot_patches"
            record_dir.mkdir(parents=True, exist_ok=True)
            stamp = _now().replace(":", "").replace("+", "_")
            atomic_dump_json(record_dir / ("shot_patch_%s.json" % stamp), {
                "schema": "production.shot_patch_record/1", "at": _now(),
                "meta": patch_meta, "before_revision": receipt.get("script_revision"),
                "after_revision": revision, "patch": patch}, ensure_ascii=False, indent=2)
            receipt.update({"script_revision": revision,
                            "formal_sha256": adoption.get("formal_sha256") or {}})
            print(json.dumps({"schema": "production.shot_patch/1", "ok": True,
                              "written": written, "revision": revision,
                              "meta": patch_meta, "model_sends": 0}, ensure_ascii=False))

        refreshed = None
        if args.refresh_gate:
            files = build_refreshed_gate(receipt, novel_file=args.novel_file)
            if args.print_refresh:
                _print_json({"schema": "production.gate_refresh/1", "ok": True,
                             "formal_chapter": receipt["formal_chapter"],
                             "files": files})
                return 0
            refreshed = apply_refreshed_gate(receipt, files)
        verified = verify(receipt)
    except ContextError as exc:
        _print_json({"schema": "production.run_context.failure/1", **exc.payload()})
        return 2

    result = {"schema": "production.run_context.verify/1", "ok": True,
              "receipt": receipt["_receipt_path"],
              "context_env": env_plan, "verified": verified}
    if unknown:
        result["ignored_config_keys"] = unknown
    if refreshed:
        result["gate_refresh"] = {"schema": "production.gate_refresh/1",
                                  "written": refreshed, "model_sends": 0}

    if args.run:
        cmd = list(run_cmd)
        if not cmd:
            _print_json({"schema": "production.run_context.failure/1",
                         "code": "run_command_missing",
                         "message": "--run 后面没有命令"})
            return 3
        env = dict(os.environ)
        env.update(env_plan)
        result["run"] = {"command": cmd, "env_keys": sorted(env_plan)}
        _print_json(result)
        sys.stdout.flush()
        return subprocess.call(cmd, cwd=str(ROOT), env=env)

    if args.print_record:
        out = Path(args.record_out) if args.record_out else default_record_path(receipt)
        existing = load_record(out) or {}
        phases = dict(existing.get("phases") or {k: "pending" for k in DEFAULT_PHASES})
        phases.update(parse_phase_updates(args.phase))
        record = build_record(receipt, verified, phases=phases)
        if existing.get("created_at"):
            record["first_created_at"] = existing["created_at"]
        _print_json(record)
        return 0

    if args.write_record or args.phase:
        out = Path(args.record_out) if args.record_out else default_record_path(receipt)
        existing = load_record(out) or {}
        phases = dict(existing.get("phases") or {k: "pending" for k in DEFAULT_PHASES})
        phases.update(parse_phase_updates(args.phase))
        record = build_record(receipt, verified, phases=phases)
        if existing.get("created_at"):
            record["first_created_at"] = existing["created_at"]
        path = write_record(out, record)
        result["record"] = {"path": str(path),
                            "phases": {k: record["phases"].get(k, "pending") for k in DEFAULT_PHASES}}

    _print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
