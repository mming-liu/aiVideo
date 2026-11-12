# -*- coding: utf-8 -*-
"""shared.execution_contract - 跨阶段执行绑定的纯函数合同（S02）.

本模块只做**确定性**的绑定、身份与变化权限校验，不读文件、不调模型、不写盘：

1. :func:`build_execution_binding`：从一份 Step01 稿件与显式 scope/stage 生成
   有版本的执行绑定（来源身份、本阶段要求、允许变化、变化/检查/未决/反馈槽位）；
2. :func:`validate_execution_binding`：消费前重验绑定与当前稿件、期望 scope、
   期望 stage 是否仍然一致；缺失、空白、未知版本、跨章、错误 revision 与过期
   输入都返回**带字段路径的结构化记录**，不以异常代替拒收；
3. :func:`validate_execution_changes`：比较执行前/后的候选，区分合法执行细化
   与越界变化；事实主张、事件顺序、必要人物/事件、说话人、播出文本与声音模式
   属于 Step01 语义权限，不因 ``allowed_changes`` 放行。

约定：

- ``source_digest`` 的唯一算法是 :func:`script_digest`（规范化 JSON 的 sha256）。
  调用方必须用同一算法算摘要；``build_execution_binding`` 会拒绝不匹配的值，
  避免把「另一份输入」的摘要写进绑定。
- 绑定本身不带时间戳/随机数，同一输入重复构建得到逐字相同的结果。
- 校验函数返回值是 ``list[dict]``；空列表 = 通过。记录至少含
  ``code`` / ``severity`` / ``path`` / ``message``，需要时附 ``expected`` / ``actual``。
- 本模块**不**声称能判断自然语言语义等价；它只声明确定性字段/摘要/权限能证明
  的范围。语义争议仍按内容合同交给对应修订入口。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from shared.run_scope import scope_identity

#: 绑定 schema 与版本。读取侧只接受这两个值；旧产物没有该字段时按缺失处理。
SCHEMA = "execution_binding.v1"
CONTRACT_VERSION = 1

#: 摘要算法标识（与 :func:`script_digest` 的实现绑定）。
DIGEST_ALGORITHM = "sha256.canonical_json.v1"

#: 绑定必填顶层字段（顺序用于稳定报错）。
BINDING_FIELDS = (
    "schema", "contract_version", "stage", "source", "requirements",
    "protection", "allowed_changes", "changes", "checks", "unresolved", "feedback",
)

#: 下游执行可以细化、但**不能**借此改写 Step01 语义的路径段。
#: 命中任一段即拒收；``allowed_changes`` 不能为这些字段授权。
PROTECTED_SEGMENTS = frozenset({
    # 事实/主张/事件/因果/人物
    "facts", "fact_uids", "required_facts", "fact_bindings", "hook_bindings",
    "main_events", "climax", "causal_chain", "end_hook", "character_motives",
    "shot_beats", "events", "event_order", "order", "characters", "story",
    "story_text",
    # 声音/播出文本
    "speaker", "speakers", "voice", "voice_mode", "audio_mode",
    "dialogue", "dialogue_lines", "narration", "narration_lines",
    "narration_segment", "subtitle", "spoken_text",
})

#: 语义承载、但允许执行阶段**在通过语义检查后**细化的字段段（S02 R3）。
#:
#: 这些字段过去不在保护集里，``allowed_changes=["prompt"]`` 或
#: ``["visual_plan.visible_characters"]`` 就能把画外说话人画进画面。现在它们属于
#: **受保护但可验证细化** 的字段：
#:
#: 1. 变化必须由语义检查器（``shared.execution_semantics``）给出通过记录
#:    （``binding.checks`` 中 ``kind/checker`` 以 ``execution_semantics`` 开头、
#:    ``status=pass`` 且路径覆盖本次变化）；
#: 2. 没有通过记录、或存在对应 ``unresolved`` 时，返回
#:    ``semantic_change_unverified`` 并给出 ``route=step01`` 的反馈路径；
#: 3. 调用方可通过 ``protection`` / ``build_execution_binding(protection=...)``
#:    追加自定义受保护/受验证字段，不把保护集写死成一个不可扩展的常量。
GUARDED_SEGMENTS = frozenset({
    "prompt", "visual_plan", "visible_characters", "visible_props",
    "frame_layouts", "ff_desc", "lf_desc", "motion_desc", "main_subject",
    "offscreen_speakers", "on_screen", "audio_cue",
})

#: 语义检查记录的可识别前缀（写入 ``binding.checks[].checker``）。
SEMANTIC_CHECKER_PREFIX = "execution_semantics"

#: 默认保护配置（构建时复制进绑定，消费侧可再追加）。
DEFAULT_PROTECTION = {
    "protected_segments": sorted(PROTECTED_SEGMENTS),
    "guarded_segments": sorted(GUARDED_SEGMENTS),
}

#: ``text`` 只有在声音/对白/旁白上下文中才受保护（普通画面字段叫 prompt/desc）。
_TEXT_SEGMENTS = frozenset({"voice", "voices", "voice_unit", "voice_units",
                            "dialogue", "dialogues", "narration", "narrations"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PATH_TOKEN_RE = re.compile(r"[^.\[\]]+")


class ExecutionContractError(ValueError):
    """构建/规范化阶段的确定性失败（调用方按拒收或输入错误处理）."""

    def __init__(self, code: str, message: str, *, path: str = ""):
        super().__init__(message)
        self.code = code
        self.path = path


# ---------------------------------------------------------------------------
# 基础规范化
# ---------------------------------------------------------------------------

def _issue(code: str, path: str, message: str, **fields) -> dict:
    record = {"code": code, "severity": "reject", "path": path, "message": message}
    record.update(fields)
    return record


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_text(value: Any, *, code: str, path: str, label: str) -> str:
    text = _text(value)
    if not text:
        raise ExecutionContractError(code, f"{label}不能为空", path=path)
    return text


def _normalize_scope(scope: Any, *, path: str = "scope") -> dict:
    """把 scope 规范成身份三元组；缺键/空白/类型不符时抛确定性错误.

    ``scope`` 只认 :data:`shared.run_scope.SCOPE_IDENTITY_KEYS` 三个身份键；
    ``source/overrides/summary`` 等诊断字段不参与身份比较，也不复制进绑定。
    """
    if not isinstance(scope, dict):
        raise ExecutionContractError("scope_invalid", "scope 必须是对象", path=path)
    chapter = scope.get("chapter")
    book_index = scope.get("book_index")
    book_name = scope.get("book_name")
    if not _is_strict_int(chapter) or chapter < 1:
        raise ExecutionContractError(
            "scope_chapter_invalid", f"scope.chapter 必须是 >=1 的整数，收到 {chapter!r}",
            path=f"{path}.chapter")
    if not _is_strict_int(book_index) or book_index < 1:
        raise ExecutionContractError(
            "scope_book_index_invalid", f"scope.book_index 必须是 >=1 的整数，收到 {book_index!r}",
            path=f"{path}.book_index")
    name = _text(book_name)
    if not name:
        raise ExecutionContractError(
            "scope_book_name_blank", "scope.book_name 不能为空", path=f"{path}.book_name")
    return {"chapter": chapter, "book_index": book_index, "book_name": name}


def _scope_issues(scope: Any, *, path: str) -> list[dict]:
    """非抛异常版本的 scope 校验，供 validate_execution_binding 汇总."""
    issues: list[dict] = []
    if not isinstance(scope, dict):
        return [_issue("scope_invalid", path, "scope 必须是对象")]
    chapter = scope.get("chapter")
    book_index = scope.get("book_index")
    book_name = scope.get("book_name")
    if "chapter" not in scope:
        issues.append(_issue("field_missing", f"{path}.chapter", "scope 缺 chapter"))
    elif not _is_strict_int(chapter) or chapter < 1:
        issues.append(_issue("scope_chapter_invalid", f"{path}.chapter",
                             f"scope.chapter 必须是 >=1 的整数，收到 {chapter!r}"))
    if "book_index" not in scope:
        issues.append(_issue("field_missing", f"{path}.book_index", "scope 缺 book_index"))
    elif not _is_strict_int(book_index) or book_index < 1:
        issues.append(_issue("scope_book_index_invalid", f"{path}.book_index",
                             f"scope.book_index 必须是 >=1 的整数，收到 {book_index!r}"))
    if "book_name" not in scope:
        issues.append(_issue("field_missing", f"{path}.book_name", "scope 缺 book_name"))
    else:
        name = _text(book_name)
        if not name:
            issues.append(_issue("field_blank", f"{path}.book_name", "scope.book_name 为空"))
    return issues


def _strip_private(value: Any) -> Any:
    """去掉下划线开头的运行期字段；其余值原样保留（用于稳定摘要）."""
    if isinstance(value, dict):
        return {k: _strip_private(v) for k, v in value.items()
                if isinstance(k, str) and not k.startswith("_")}
    if isinstance(value, list):
        return [_strip_private(v) for v in value]
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(_strip_private(value), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ExecutionContractError(
            "not_canonical_json", f"值不能规范化为 JSON: {exc}", path="script") from exc


def script_digest(script: Any) -> str:
    """一份稿件的唯一内容摘要（去运行期 ``_`` 字段后的规范 JSON sha256）.

    这是 ``source_digest`` 的唯一算法。摘要包含 ``script_revision``；稿件内容或
    revision 变化都会得到新摘要，消费侧据此拒绝过期绑定。
    """
    if not isinstance(script, dict):
        raise ExecutionContractError("script_invalid", "script 必须是对象", path="script")
    raw = _canonical_json(script).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _script_identity(script: Any) -> dict:
    if not isinstance(script, dict):
        raise ExecutionContractError("script_invalid", "script 必须是对象", path="script")
    revision = _require_text(script.get("script_revision"), code="script_revision_blank",
                             path="script.script_revision", label="script_revision")
    chapter = script.get("chapter_index")
    if not _is_strict_int(chapter) or chapter < 1:
        raise ExecutionContractError(
            "script_chapter_invalid", f"script.chapter_index 必须是 >=1 的整数，收到 {chapter!r}",
            path="script.chapter_index")
    book_name = _require_text(script.get("book_name"), code="script_book_name_blank",
                              path="script.book_name", label="script.book_name")
    outline = script.get("chapter_outline")
    if outline is None:
        outline = {}
    if not isinstance(outline, dict):
        raise ExecutionContractError("chapter_outline_invalid",
                                     "script.chapter_outline 必须是对象",
                                     path="script.chapter_outline")
    shots = script.get("shots")
    if shots is None:
        shots = []
    if not isinstance(shots, list):
        raise ExecutionContractError("shots_invalid", "script.shots 必须是数组",
                                     path="script.shots")
    return {"script_revision": revision, "chapter": chapter, "book_name": book_name,
            "outline": outline, "shots": shots}


# ---------------------------------------------------------------------------
# 要求提取
# ---------------------------------------------------------------------------

def _fact_requirements(outline: dict) -> list[dict]:
    uids = outline.get("fact_uids")
    facts = outline.get("required_facts")
    uids = uids if isinstance(uids, list) else []
    facts = facts if isinstance(facts, list) else []
    out: list[dict] = []
    for i in range(max(len(uids), len(facts))):
        uid = _text(uids[i]) if i < len(uids) else ""
        raw_claim = facts[i] if i < len(facts) else ""
        claim = (_text(raw_claim.get("claim") or raw_claim.get("text"))
                 if isinstance(raw_claim, dict) else _text(raw_claim))
        out.append({"uid": uid, "claim": claim})
    return out


def _shot_requirements(shots: list) -> list[dict]:
    out: list[dict] = []
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue
        index = shot.get("index", i + 1)
        refs = shot.get("plan_refs")
        duration = shot.get("duration_sec", shot.get("duration"))
        out.append({
            "index": index,
            "plan_refs": copy.deepcopy(refs) if isinstance(refs, dict) else {},
            "duration_sec": (duration
                             if isinstance(duration, (int, float))
                             and not isinstance(duration, bool) else None),
        })
    return out


def _character_requirements(script: dict) -> list[str]:
    out: list[str] = []
    for item in script.get("characters") or []:
        name = ""
        if isinstance(item, dict):
            name = _text(item.get("identifier") or item.get("name"))
        elif isinstance(item, str):
            name = item.strip()
        if name and name not in out:
            out.append(name)
    return out


def _voice_requirements(outline: dict) -> list[dict]:
    units = outline.get("voice_units")
    if not isinstance(units, list):
        units = (outline.get("plan_verification") or {}).get("voice_units") \
            if isinstance(outline.get("plan_verification"), dict) else None
    if not isinstance(units, list):
        return []
    out: list[dict] = []
    for i, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        out.append({
            "unit_id": _text(unit.get("unit_id") or unit.get("unit_uid") or f"voice-{i + 1:02d}"),
            "kind": _text(unit.get("kind") or unit.get("source_kind")),
            "speaker": _text(unit.get("speaker")),
            "text": _text(unit.get("text") or unit.get("echo_text")),
            "shot_index": unit.get("shot_index"),
            "window_sec": unit.get("window_sec"),
            "actual_sec": unit.get("actual_sec"),
            "verified": unit.get("verified"),
        })
    return out


def _extract_requirements(script: dict) -> dict:
    identity = _script_identity(script)
    outline = identity["outline"]
    return {
        "facts": _fact_requirements(outline),
        "shots": _shot_requirements(identity["shots"]),
        "characters": _character_requirements(script),
        "voice_units": _voice_requirements(outline),
    }


# ---------------------------------------------------------------------------
# 保护配置
# ---------------------------------------------------------------------------

def _normalize_segment_list(value: Any, *, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExecutionContractError("protection_invalid", f"{path} 必须是数组", path=path)
    out: list[str] = []
    for i, entry in enumerate(value):
        text = entry.strip() if isinstance(entry, str) else ""
        if not text:
            raise ExecutionContractError(
                "protection_invalid", f"{path}[{i}] 必须是非空字符串", path=f"{path}[{i}]")
        if text not in out:
            out.append(text)
    return out


def normalize_protection(protection: dict | None = None) -> dict:
    """合并默认与调用方追加的保护配置（默认在前，追加项排序去重）。

    ``protection`` 可含 ``protected_segments``（完全禁止执行改写）和
    ``guarded_segments``（必须附 ``execution_semantics`` 通过记录才可改写）。
    """
    if protection is not None and not isinstance(protection, dict):
        raise ExecutionContractError("protection_invalid", "protection 必须是对象",
                                     path="protection")
    extra = protection or {}
    return {
        "protected_segments": sorted(set(DEFAULT_PROTECTION["protected_segments"]) | set(
            _normalize_segment_list(extra.get("protected_segments"),
                                    path="protection.protected_segments"))),
        "guarded_segments": sorted(set(DEFAULT_PROTECTION["guarded_segments"]) | set(
            _normalize_segment_list(extra.get("guarded_segments"),
                                    path="protection.guarded_segments"))),
    }


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------

def build_execution_binding(script: dict, *, scope: dict, stage: str,
                            source_digest: str,
                            protection: dict | None = None) -> dict:
    """从稿件与显式 scope/stage 构建有版本的执行绑定.

    Args:
        script: Step01 稿件（必须含 ``script_revision`` / ``chapter_index`` /
            ``book_name``；``chapter_outline`` 与 ``shots`` 可缺省为空）。
        scope: 本阶段声明的运行范围身份（chapter/book_index/book_name）。
        stage: 责任阶段标识（如 ``step02a.visual_plan``），不能为空。
        source_digest: 调用方用 :func:`script_digest` 算出的摘要；必须与当前
            ``script`` 一致，否则拒绝构建。
        protection: 可选的追加保护配置（见 :func:`normalize_protection`）。

    Raises:
        ExecutionContractError: 任一输入缺失/空白/类型不符/摘要不匹配。
    """
    stage_text = _require_text(stage, code="stage_blank", path="stage", label="stage")
    scope_norm = _normalize_scope(scope, path="scope")
    protection_norm = normalize_protection(protection)
    digest_text = _require_text(source_digest, code="source_digest_blank",
                                path="source_digest", label="source_digest")
    if not _SHA256_RE.match(digest_text):
        raise ExecutionContractError(
            "source_digest_invalid",
            "source_digest 必须是 64 位小写十六进制 sha256（由 script_digest 计算）",
            path="source_digest")
    identity = _script_identity(script)
    actual_digest = script_digest(script)
    if actual_digest != digest_text:
        raise ExecutionContractError(
            "source_digest_mismatch",
            f"source_digest 与当前稿件摘要不符（传入 {digest_text[:12]}…，实际 {actual_digest[:12]}…）",
            path="source_digest")
    outline = identity["outline"]
    plan_verification = outline.get("plan_verification")
    plan_verification = plan_verification if isinstance(plan_verification, dict) else {}
    source_input = {
        "book_name": identity["book_name"],
        "chapter_index": identity["chapter"],
        "chapter_title": _text(script.get("chapter_title")),
        "source_hash": _text(script.get("source_hash")),
        "plan_contract_version": outline.get("plan_contract_version"),
        "plan_digest": _text(plan_verification.get("plan_digest") or script.get("plan_digest")),
    }
    return {
        "schema": SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "stage": stage_text,
        "source": {
            "scope": scope_norm,
            "script_revision": identity["script_revision"],
            "source_digest": actual_digest,
            "digest_algorithm": DIGEST_ALGORITHM,
            "input": source_input,
        },
        "requirements": _extract_requirements(script),
        "protection": protection_norm,
        "allowed_changes": [],
        "changes": [],
        "checks": [],
        "unresolved": [],
        "feedback": [],
    }


# ---------------------------------------------------------------------------
# 绑定校验
# ---------------------------------------------------------------------------

def _known_version(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == CONTRACT_VERSION
    if isinstance(value, str):
        return value.strip() == str(CONTRACT_VERSION)
    return False


def _binding_shape_issues(binding: Any) -> list[dict]:
    issues: list[dict] = []
    if not isinstance(binding, dict):
        return [_issue("binding_missing", "binding", "执行绑定必须是对象")]
    for key in BINDING_FIELDS:
        if key not in binding:
            issues.append(_issue("field_missing", f"binding.{key}", f"绑定缺字段 {key}"))

    schema = binding.get("schema")
    if "schema" in binding:
        if not _text(schema):
            issues.append(_issue("field_blank", "binding.schema", "绑定 schema 为空"))
        elif _text(schema) != SCHEMA:
            issues.append(_issue("schema_unknown", "binding.schema",
                                 f"未知绑定 schema: {schema!r}", expected=SCHEMA, actual=schema))

    version = binding.get("contract_version")
    if "contract_version" in binding:
        if version is None or (isinstance(version, str) and not version.strip()):
            issues.append(_issue("field_blank", "binding.contract_version",
                                 "绑定 contract_version 为空"))
        elif not _known_version(version):
            issues.append(_issue("contract_version_unknown", "binding.contract_version",
                                 f"未知合同版本: {version!r}", expected=CONTRACT_VERSION,
                                 actual=version))

    stage = binding.get("stage")
    if "stage" in binding and not _text(stage):
        issues.append(_issue("field_blank", "binding.stage", "绑定 stage 为空"))

    source = binding.get("source")
    if "source" in binding:
        if not isinstance(source, dict):
            issues.append(_issue("source_invalid", "binding.source", "绑定 source 必须是对象"))
        else:
            if "scope" not in source:
                issues.append(_issue("field_missing", "binding.source.scope", "绑定缺 source.scope"))
            else:
                issues.extend(_scope_issues(source.get("scope"), path="binding.source.scope"))
            for key in ("script_revision", "source_digest"):
                if key not in source:
                    issues.append(_issue("field_missing", f"binding.source.{key}",
                                         f"绑定缺 source.{key}"))
                elif not _text(source.get(key)):
                    issues.append(_issue("field_blank", f"binding.source.{key}",
                                         f"绑定 source.{key} 为空"))
            digest = _text(source.get("source_digest"))
            if digest and not _SHA256_RE.match(digest):
                issues.append(_issue("source_digest_invalid", "binding.source.source_digest",
                                     "绑定 source_digest 不是 64 位小写十六进制 sha256"))
            algorithm = source.get("digest_algorithm")
            if algorithm is not None and _text(algorithm) != DIGEST_ALGORITHM:
                issues.append(_issue("digest_algorithm_unknown", "binding.source.digest_algorithm",
                                     f"未知摘要算法: {algorithm!r}", expected=DIGEST_ALGORITHM,
                                     actual=algorithm))

    requirements = binding.get("requirements")
    if "requirements" in binding:
        if not isinstance(requirements, dict):
            issues.append(_issue("requirements_invalid", "binding.requirements",
                                 "绑定 requirements 必须是对象"))
        else:
            for key in ("facts", "shots", "characters", "voice_units"):
                if key not in requirements:
                    issues.append(_issue("field_missing", f"binding.requirements.{key}",
                                         f"绑定 requirements 缺 {key}"))
                elif not isinstance(requirements.get(key), list):
                    issues.append(_issue("requirements_field_invalid",
                                         f"binding.requirements.{key}",
                                         f"requirements.{key} 必须是数组"))
            for i, fact in enumerate(requirements.get("facts") or []):
                if not isinstance(fact, dict) or not _text(fact.get("uid")):
                    issues.append(_issue("requirement_identity_missing",
                                         f"binding.requirements.facts[{i}].uid",
                                         "事实要求缺稳定 uid，无法绑定到 Step01 事实"))
            for i, unit in enumerate(requirements.get("voice_units") or []):
                if not isinstance(unit, dict) or not _text(unit.get("unit_id")):
                    issues.append(_issue("requirement_identity_missing",
                                         f"binding.requirements.voice_units[{i}].unit_id",
                                         "声音要求缺稳定 unit_id，无法绑定到声音单元"))

    protection = binding.get("protection")
    if "protection" in binding:
        if not isinstance(protection, dict):
            issues.append(_issue("protection_invalid", "binding.protection",
                                 "绑定 protection 必须是对象"))
        else:
            for key in ("protected_segments", "guarded_segments"):
                if key not in protection:
                    issues.append(_issue("field_missing", f"binding.protection.{key}",
                                         f"绑定 protection 缺 {key}"))
                elif not isinstance(protection.get(key), list):
                    issues.append(_issue("protection_invalid", f"binding.protection.{key}",
                                         f"protection.{key} 必须是数组"))
                else:
                    for i, entry in enumerate(protection.get(key) or []):
                        if not _text(entry):
                            issues.append(_issue(
                                "protection_invalid",
                                f"binding.protection.{key}[{i}]",
                                "保护配置条目必须是非空字符串"))

    for key in ("allowed_changes", "changes", "checks", "unresolved", "feedback"):
        if key in binding and not isinstance(binding.get(key), list):
            issues.append(_issue("field_type_invalid", f"binding.{key}",
                                 f"绑定 {key} 必须是数组"))
    return issues


def _dedupe(issues: list[dict]) -> list[dict]:
    seen = set()
    out: list[dict] = []
    for item in issues:
        key = (item.get("code"), item.get("path"), item.get("message"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def validate_execution_binding(binding: dict, script: dict, *, expected_scope: dict,
                               expected_stage: str) -> list[dict]:
    """消费前重验执行绑定；返回空列表 = 通过，否则逐条结构化拒收原因.

    覆盖：绑定结构/版本缺失或未知、来源 scope/revision/摘要缺失或空白、跨章、
    stage 不符、稿件 revision 不符、稿件内容已变化（过期输入）。
    """
    issues = _binding_shape_issues(binding)

    expected_stage_text = _text(expected_stage)
    if not expected_stage_text:
        issues.append(_issue("expected_stage_missing", "expected_stage",
                             "调用方必须声明期望 stage"))
    elif isinstance(binding, dict) and _text(binding.get("stage")) != expected_stage_text:
        issues.append(_issue("stage_mismatch", "binding.stage",
                             f"绑定 stage 与期望不符: {binding.get('stage')!r} != {expected_stage_text!r}",
                             expected=expected_stage_text, actual=binding.get("stage")))

    actual_scope = (binding.get("source") or {}).get("scope") \
        if isinstance(binding, dict) and isinstance(binding.get("source"), dict) else None
    actual_norm = None
    if isinstance(actual_scope, dict):
        try:
            actual_norm = _normalize_scope(actual_scope, path="binding.source.scope")
        except ExecutionContractError:
            actual_norm = None

    if not isinstance(expected_scope, dict):
        issues.append(_issue("expected_scope_missing", "expected_scope",
                             "调用方必须声明期望 scope（章/部）"))
    else:
        issues.extend(_scope_issues(expected_scope, path="expected_scope"))
        expected_norm = None
        try:
            expected_norm = _normalize_scope(expected_scope, path="expected_scope")
        except ExecutionContractError:
            expected_norm = None
        if expected_norm is not None and actual_norm is not None \
                and scope_identity(actual_norm) != scope_identity(expected_norm):
            issues.append(_issue(
                "scope_mismatch", "binding.source.scope",
                f"绑定 scope 与期望不符: {actual_norm} != {expected_norm}",
                expected=expected_norm, actual=actual_norm))

    if actual_norm is not None and isinstance(script, dict):
        script_book = _text(script.get("book_name"))
        script_chapter = script.get("chapter_index")
        if script_book and _is_strict_int(script_chapter) \
                and (actual_norm["book_name"] != script_book
                     or actual_norm["chapter"] != script_chapter):
            issues.append(_issue(
                "scope_script_mismatch", "binding.source.scope",
                f"绑定 scope 与稿件身份不符: scope={actual_norm}, "
                f"script={script_book}/chapter_{script_chapter}",
                expected={"book_name": script_book, "chapter": script_chapter},
                actual=actual_norm))

    if not isinstance(script, dict):
        issues.append(_issue("script_invalid", "script", "校验绑定必须提供稿件对象"))
        return _dedupe(issues)

    if not _text(script.get("book_name")):
        issues.append(_issue("script_book_name_blank", "script.book_name",
                             "稿件缺 book_name，无法判断绑定属于哪一部"))
    script_chapter = script.get("chapter_index")
    if not _is_strict_int(script_chapter) or script_chapter < 1:
        issues.append(_issue("script_chapter_invalid", "script.chapter_index",
                             f"稿件 chapter_index 必须是 >=1 的整数，收到 {script_chapter!r}"))

    script_revision = _text(script.get("script_revision"))
    if not script_revision:
        issues.append(_issue("script_revision_blank", "script.script_revision",
                             "稿件缺 script_revision，无法判断绑定是否对应本稿"))
    else:
        source = binding.get("source") if isinstance(binding, dict) else None
        binding_revision = _text((source or {}).get("script_revision")) \
            if isinstance(source, dict) else ""
        if binding_revision and binding_revision != script_revision:
            issues.append(_issue(
                "script_revision_mismatch", "binding.source.script_revision",
                f"绑定 revision 与当前稿件不符: {binding_revision} != {script_revision}",
                expected=script_revision, actual=binding_revision))

    try:
        actual_digest = script_digest(script)
    except ExecutionContractError as exc:
        issues.append(_issue("script_digest_unavailable", "script", str(exc)))
        actual_digest = ""
    source = binding.get("source") if isinstance(binding, dict) else None
    binding_digest = _text((source or {}).get("source_digest")) \
        if isinstance(source, dict) else ""
    if actual_digest and binding_digest and _SHA256_RE.match(binding_digest) \
            and binding_digest != actual_digest:
        issues.append(_issue(
            "source_digest_stale", "binding.source.source_digest",
            "绑定摘要与当前稿件不符——输入已变化，派生计划不得沿用",
            expected=actual_digest, actual=binding_digest))

    return _dedupe(issues)


# ---------------------------------------------------------------------------
# 变化校验
# ---------------------------------------------------------------------------

_MISSING = object()


def _path_segments(path: str) -> list[str]:
    return [token for token in _PATH_TOKEN_RE.findall(str(path or "")) if token]


def _path_hits_segments(path: str, configured) -> bool:
    names = {str(s).strip().lower() for s in configured if str(s).strip()}
    segments = _path_segments(path)
    lowered = [s.lower() for s in segments]
    for i, segment in enumerate(lowered):
        if segment in names:
            return True
        if segment == "text" and any(s in _TEXT_SEGMENTS for s in lowered[:i]):
            return True
    return False


def _is_protected_path(path: str, protected_segments=None) -> bool:
    return _path_hits_segments(
        path, PROTECTED_SEGMENTS if protected_segments is None else protected_segments)


def _is_guarded_path(path: str, guarded_segments=None) -> bool:
    return _path_hits_segments(
        path, GUARDED_SEGMENTS if guarded_segments is None else guarded_segments)


def _effective_segments(binding: Any, protection: dict | None, key: str) -> list[str]:
    """默认保护集 + 绑定内配置 + 本次调用追加配置（不修改入参）."""
    values = list(DEFAULT_PROTECTION[key])
    if isinstance(binding, dict) and isinstance(binding.get("protection"), dict):
        configured = binding["protection"].get(key)
        if isinstance(configured, list):
            values.extend(str(v).strip() for v in configured if str(v).strip())
    if isinstance(protection, dict):
        configured = protection.get(key)
        if isinstance(configured, list):
            values.extend(str(v).strip() for v in configured if str(v).strip())
    return sorted(set(values))


def _semantic_check_passes(path: str, binding: Any) -> bool:
    """``binding.checks`` 里是否有覆盖本次变化的语义通过记录."""
    checks = binding.get("checks") if isinstance(binding, dict) else None
    if not isinstance(checks, list):
        return False
    for record in checks:
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "").strip().lower()
        checker = str(record.get("checker") or record.get("kind") or "").strip().lower()
        if status not in ("pass", "ok", "passed"):
            continue
        if not checker.startswith(SEMANTIC_CHECKER_PREFIX):
            continue
        if any(_entry_covers(entry, path) for entry in _change_record_paths(record)):
            return True
    return False


def _unresolved_for_path(path: str, binding: Any) -> list[dict]:
    """返回覆盖该路径、仍为未决语义问题的 ``binding.unresolved`` 记录."""
    unresolved = binding.get("unresolved") if isinstance(binding, dict) else None
    if not isinstance(unresolved, list):
        return []
    out: list[dict] = []
    for record in unresolved:
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "unresolved").strip().lower()
        if status in ("resolved", "pass", "ok"):
            continue
        route = str(record.get("route") or "").strip().lower()
        code = str(record.get("code") or "")
        if route != "step01" and not code:
            continue
        entries = _change_record_paths(record)
        if entries and not any(_entry_covers(entry, path) for entry in entries):
            continue
        out.append(record)
    return out


def _entry_covers(entry: str, path: str) -> bool:
    entry = str(entry or "").strip().rstrip(".")
    if not entry:
        return False
    if path == entry or path.startswith(entry + ".") or path.startswith(entry + "["):
        return True
    # 简单字段名（不含路径分隔）按「任意镜头的同名字段」授权，方便
    # `allowed_changes=["prompt"]` 这类按字段族的声明；含点/下标的条目仍按
    # 精确路径/子树处理，避免误把 `shots[0]` 当成任意层的 `shots`。
    if "." not in entry and "[" not in entry:
        return path.endswith("." + entry)
    return False


def _path_allowed(path: str, allowed_changes: list[str]) -> bool:
    return any(_entry_covers(entry, path) for entry in allowed_changes)


def _change_record_paths(record: dict) -> list[str]:
    paths = record.get("paths")
    if isinstance(paths, list):
        return [str(p).strip() for p in paths if str(p).strip()]
    for key in ("path", "field", "field_path"):
        value = str(record.get(key) or "").strip()
        if value:
            return [value]
    return []


def _has_reason(path: str, changes: list[dict]) -> bool:
    for record in changes:
        if not isinstance(record, dict):
            continue
        if not _text(record.get("reason")):
            continue
        if any(_entry_covers(entry, path) for entry in _change_record_paths(record)):
            return True
    return False


def _display(value: Any) -> str:
    if value is _MISSING:
        return "<missing>"
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= 120 else text[:117] + "..."


def _diff(before: Any, after: Any, path: str = "") -> list[dict]:
    """叶级差异：只报告实际变化的路径，供权限与理由校验逐条消费."""
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[dict] = []
        for key in sorted(set(before) | set(after), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                out.append({"path": child, "before": _MISSING, "after": after[key]})
            elif key not in after:
                out.append({"path": child, "before": before[key], "after": _MISSING})
            else:
                out.extend(_diff(before[key], after[key], child))
        return out
    if isinstance(before, list) and isinstance(after, list):
        out = []
        for i in range(max(len(before), len(after))):
            child = f"{path}[{i}]" if path else f"[{i}]"
            if i >= len(before):
                out.append({"path": child, "before": _MISSING, "after": after[i]})
            elif i >= len(after):
                out.append({"path": child, "before": before[i], "after": _MISSING})
            else:
                out.extend(_diff(before[i], after[i], child))
        return out
    if type(before) is type(after) and before == after:
        return []
    return [{"path": path or "<root>", "before": before, "after": after}]


def _changes_version_issues(binding: Any) -> list[dict]:
    if not isinstance(binding, dict):
        return [_issue("binding_missing", "binding", "执行绑定必须是对象")]
    issues: list[dict] = []
    version = binding.get("contract_version")
    if "contract_version" not in binding:
        issues.append(_issue("contract_version_missing", "binding.contract_version",
                             "绑定缺 contract_version"))
    elif not _known_version(version):
        issues.append(_issue("contract_version_unknown", "binding.contract_version",
                             f"未知合同版本: {version!r}", expected=CONTRACT_VERSION,
                             actual=version))
    schema = binding.get("schema")
    if schema is not None and _text(schema) and _text(schema) != SCHEMA:
        issues.append(_issue("schema_unknown", "binding.schema",
                             f"未知绑定 schema: {schema!r}", expected=SCHEMA, actual=schema))
    if "changes" in binding and not isinstance(binding.get("changes"), list):
        issues.append(_issue("field_type_invalid", "binding.changes",
                             "绑定 changes 必须是数组"))
    return issues


def validate_execution_changes(before: dict, after: dict, *, binding: dict,
                               allowed_changes: list[str],
                               protection: dict | None = None) -> list[dict]:
    """比较执行前/后的候选，逐条判定变化是否在授权内且有理由.

    拒绝四类变化：

    1. **语义变化**：路径命中 :data:`PROTECTED_SEGMENTS`（事实、事件顺序、必要
       人物/事件、说话人、播出文本、声音模式等），即使列进 ``allowed_changes``
       也不能由下游改写；
    2. **受验证字段无证据**：路径命中 :data:`GUARDED_SEGMENTS`（``prompt``、
       ``visual_plan.visible_characters`` 等语义承载字段），但 ``binding.checks``
       没有 ``execution_semantics`` 的通过记录，或有对应未决项 → 
       ``semantic_change_unverified``，反馈路由回 Step01；
    3. **越界变化**：路径不在 ``allowed_changes`` 覆盖范围内；
    4. **缺少理由**：合法变化必须在 ``binding["changes"]`` 留非空 ``path`` + ``reason``。

    ``protection`` 可在默认集合之外追加受保护/受验证字段；绑定内
    ``binding["protection"]`` 同样生效。本函数只做确定性字段判据，不判断自然语言
    改写是否语义等价。
    """
    issues = _changes_version_issues(binding)
    protected_segments = _effective_segments(binding, protection, "protected_segments")
    guarded_segments = _effective_segments(binding, protection, "guarded_segments")

    if not isinstance(allowed_changes, list):
        issues.append(_issue("allowed_changes_invalid", "allowed_changes",
                             "allowed_changes 必须是数组"))
        allowed: list[str] = []
    else:
        allowed = []
        for i, entry in enumerate(allowed_changes):
            text = entry.strip() if isinstance(entry, str) else ""
            if not text:
                issues.append(_issue("allowed_changes_invalid", f"allowed_changes[{i}]",
                                     "allowed_changes 条目必须是非空字符串"))
                continue
            allowed.append(text)

    if not isinstance(before, dict) or not isinstance(after, dict):
        issues.append(_issue("changes_input_invalid", "changes",
                             "before/after 必须是对象"))
        return _dedupe(issues)

    raw_changes = binding.get("changes") if isinstance(binding, dict) else None
    reason_records = raw_changes if isinstance(raw_changes, list) else []

    for item in _diff(before, after):
        path = item["path"]
        if _path_hits_segments(path, protected_segments):
            issues.append(_issue(
                "protected_field_change", path,
                f"{path} 属于 Step01 语义/声音权限，下游执行候选不得改写",
                route="step01", before=_display(item["before"]),
                after=_display(item["after"])))
            continue
        if _path_hits_segments(path, guarded_segments):
            unresolved = _unresolved_for_path(path, binding)
            if unresolved:
                issues.append(_issue(
                    "semantic_change_unverified", path,
                    f"{path} 的语义检查未决，不能按执行细化默认放行",
                    route="step01",
                    unresolved=[dict(record) for record in unresolved],
                    feedback=[{"route": "step01", "path": path,
                               "action": "revise_or_restore_semantics"}],
                    before=_display(item["before"]), after=_display(item["after"])))
                continue
            if not _semantic_check_passes(path, binding):
                issues.append(_issue(
                    "semantic_change_unverified", path,
                    f"{path} 是语义承载字段，必须附 execution_semantics 通过记录"
                    "（未验证不得默认放行）",
                    route="step01",
                    feedback=[{"route": "step01", "path": path,
                               "action": "attach_semantic_check_or_revise"}],
                    before=_display(item["before"]), after=_display(item["after"])))
                continue
        if not _path_allowed(path, allowed):
            issues.append(_issue(
                "change_not_allowed", path,
                f"{path} 不在本次 allowed_changes 授权范围内",
                before=_display(item["before"]), after=_display(item["after"])))
        elif not _has_reason(path, reason_records):
            issues.append(_issue(
                "change_reason_missing", path,
                f"{path} 已获授权但仍需在 binding.changes 记录实际变化及理由",
                before=_display(item["before"]), after=_display(item["after"])))

    return _dedupe(issues)


def attach_semantic_checks(binding: dict, *, checked_paths: list[str],
                           findings: list[dict],
                           checker: str = "execution_semantics.v1") -> dict:
    """把语义检查结果写进绑定副本：通过 → ``checks``；失败 → ``unresolved`` + 反馈.

    这是唯一的生产填写入口：真实候选入口调用语义检查器后必须调用本函数，消费侧
    :func:`validate_execution_changes` 只认 ``kind/checker`` 以
    ``execution_semantics`` 开头且 ``status=pass`` 的记录。不修改入参。
    """
    import copy as _copy

    updated = _copy.deepcopy(binding) if isinstance(binding, dict) else {}
    checks = updated.get("checks")
    checks = checks if isinstance(checks, list) else []
    unresolved = updated.get("unresolved")
    unresolved = unresolved if isinstance(unresolved, list) else []
    feedback = updated.get("feedback")
    feedback = feedback if isinstance(feedback, list) else []
    paths = [str(p) for p in (checked_paths or []) if str(p).strip()]
    if findings:
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            rec = {
                "code": str(finding.get("code") or "semantic_check_failed"),
                "path": str(finding.get("path") or ""),
                "message": str(finding.get("message") or ""),
                "severity": "reject",
                "route": "step01",
                "status": "unresolved",
            }
            unresolved.append(rec)
            feedback.append({
                "route": "step01",
                "code": rec["code"],
                "path": rec["path"],
                "action": "revise_or_restore_semantics",
                "affected": finding.get("unit_id") or finding.get("shot_index")
                or finding.get("speaker") or finding.get("listeners"),
            })
        checks.append({
            "kind": "semantic", "checker": checker, "status": "fail",
            "paths": paths, "codes": sorted({str(f.get("code")) for f in findings}),
        })
    else:
        for path in paths or ["<all>"]:
            checks.append({
                "kind": "semantic", "checker": checker, "status": "pass",
                "path": path,
            })
    updated["checks"] = checks
    updated["unresolved"] = unresolved
    updated["feedback"] = feedback
    return updated


def unresolved_gate_issues(binding: dict) -> list[dict]:
    """消费前检查 ``binding.unresolved``：未决语义项一律拒收并给出 Step01 路由."""
    issues: list[dict] = []
    unresolved = binding.get("unresolved") if isinstance(binding, dict) else None
    if not isinstance(unresolved, list):
        return issues
    for record in unresolved:
        if not isinstance(record, dict):
            continue
        status = str(record.get("status") or "unresolved").strip().lower()
        if status in ("resolved", "pass", "ok"):
            continue
        route = str(record.get("route") or "").strip().lower()
        code = str(record.get("code") or "")
        if route != "step01" and not code:
            continue
        issues.append(_issue(
            "semantic_change_unverified",
            str(record.get("path") or "binding.unresolved"),
            f"候选仍含未决语义项 {code or 'unresolved'}，不得默认放行",
            route="step01",
            feedback=[{"route": "step01", "path": record.get("path"),
                       "action": "revise_or_restore_semantics"}],
        ))
    return _dedupe(issues)


__all__ = [
    "SCHEMA",
    "CONTRACT_VERSION",
    "DIGEST_ALGORITHM",
    "PROTECTED_SEGMENTS",
    "GUARDED_SEGMENTS",
    "DEFAULT_PROTECTION",
    "SEMANTIC_CHECKER_PREFIX",
    "ExecutionContractError",
    "script_digest",
    "normalize_protection",
    "build_execution_binding",
    "validate_execution_binding",
    "validate_execution_changes",
    "attach_semantic_checks",
    "unresolved_gate_issues",
]
