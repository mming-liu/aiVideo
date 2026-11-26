# -*- coding: utf-8 -*-
"""受身份绑定的章节计划缓存（S44/P1）.

缓存不是无主 JSON：必须绑定 `book_name`/`chapter`/`script_revision`，并对
`outline` 的规范化 JSON 摘要做校验。Step03 只消费通过校验的计划。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA = "step01.chapter_outline.cache.v2"
FILENAME = "chapter_outline.cache.json"


class PlanCacheError(RuntimeError):
    pass


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(_canonical(plan).encode("utf-8")).hexdigest()


def _scope_book(scope) -> str:
    if isinstance(scope, dict):
        return str(scope.get("book_name") or "").strip()
    return ""


def _scope_chapter(scope) -> int:
    if isinstance(scope, dict):
        value = scope.get("chapter")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return value
    raise PlanCacheError("计划缓存 scope.chapter 非法")


def write_plan_cache(path, plan: dict, *, scope, script_revision: str) -> Path:
    if not isinstance(plan, dict) or not plan:
        raise PlanCacheError("计划缓存 outline 不能为空")
    book = _scope_book(scope)
    chapter = _scope_chapter(scope)
    revision = str(script_revision or "").strip()
    if not book or not revision:
        raise PlanCacheError("计划缓存缺少 book_name 或 script_revision")
    payload = {
        "schema": SCHEMA,
        "scope": {"book_name": book, "chapter": chapter},
        "script_revision": revision,
        "plan_sha256": plan_digest(plan),
        "outline": plan,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def load_plan_cache(path, *, scope, script_revision: str,
                    allow_missing: bool = False) -> dict:
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if allow_missing:
            return {}
        raise PlanCacheError(f"计划缓存缺失: {target}") from None
    except (OSError, ValueError) as exc:
        raise PlanCacheError(f"计划缓存不可读或损坏: {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise PlanCacheError("计划缓存根节点必须是对象")
    if data.get("schema") != SCHEMA:
        raise PlanCacheError("计划缓存缺少受绑定 schema，拒绝使用无主计划")
    want_book = _scope_book(scope)
    want_chapter = _scope_chapter(scope)
    cache_scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    if str(cache_scope.get("book_name") or "").strip() != want_book:
        raise PlanCacheError("计划缓存的 book_name 与当前章节不符")
    if cache_scope.get("chapter") != want_chapter:
        raise PlanCacheError("计划缓存的 chapter 与当前章节不符")
    revision = str(data.get("script_revision") or "").strip()
    if not revision or revision != str(script_revision or "").strip():
        raise PlanCacheError("计划缓存的 script_revision 与当前稿不符")
    outline = data.get("outline")
    if not isinstance(outline, dict) or not outline:
        raise PlanCacheError("计划缓存 outline 为空")
    digest = str(data.get("plan_sha256") or "").strip()
    if not digest or digest != plan_digest(outline):
        raise PlanCacheError("计划缓存的 plan_sha256 不符（内容被改动或损坏）")
    return outline
