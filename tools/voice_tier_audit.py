# -*- coding: utf-8 -*-
"""多章声音分级/音色身份审计（S04/A1，只读，零 provider 调用，2026-11-27）.

用途：同一部小说的多章共用一份 `global/voice_registry.json`；单章报告看不出
「上一章的角色在这一章被重分声线」「龙套桶撞了独占角色」这类跨章问题。本工具：

- 逐章用**生产构造器** `build_speech_units()` 生成声音单元（不预设单元数/字数）；
- 逐章核 `check_voice_uniqueness()`（按 primary/recurring 独占、minor/extra 共享）；
- 再把多章单元**合并**核一次（跨章同一角色必须始终同一声线）；
- 汇总分级来源（declared/history/derived）与共享桶，落 JSON 报告。

边界：不实例化 provider、不发任何请求、不写正式章节目录；注册表**无条件复制件**
（即使 env 指向生产注册表也不用它做写入），与原报告的隔离口径一致。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.paths import chapter_dir  # noqa: E402
from shared.plan_cache import FILENAME as PLAN_CACHE_FILENAME, load_plan_cache  # noqa: E402
from shared.run_scope import current_scope_identity  # noqa: E402


def _parse_chapters(raw: str) -> list[int]:
    out: list[int] = []
    for item in str(raw or "").replace("，", ",").split(","):
        item = item.strip()
        if not item:
            continue
        out.append(int(item))
    if not out:
        raise SystemExit("--chapters 至少给一个章号")
    return out


def _load_chapter(ch: Path, scope_chapter: int) -> tuple[dict, dict, dict]:
    """读章稿 + 计划；返回 `(script, plan, identity)`.

    受绑定计划缓存（含 scope/script_revision）优先；旧格式缓存（只有
    `source_hash/plan_contract_version/outline`）仍可读，但 identity 如实标记为
    `legacy_unverified`——不冒充"已核身份"。
    """
    from shared.plan_cache import PlanCacheError
    from shared.quality_gate import compute_script_revision
    script_path = ch / "script_result.json"
    if not script_path.is_file():
        raise SystemExit("缺少 Step01 产物: %s" % script_path)
    script = json.loads(script_path.read_text(encoding="utf-8"))
    plan_path = ch / PLAN_CACHE_FILENAME
    if not plan_path.is_file():
        plan = script.get("chapter_outline")
        if not isinstance(plan, dict) or not plan:
            raise SystemExit("缺少受绑定计划缓存（%s），稿内也没有 chapter_outline" % plan_path)
        return script, plan, {"source": "script.chapter_outline", "verified": False}
    revision = str(script.get("script_revision") or "") or compute_script_revision(script)
    try:
        plan = load_plan_cache(plan_path, scope={"book_name": script.get("book_name"),
                                                "chapter_index": script.get("chapter_index")
                                                or scope_chapter},
                               script_revision=revision)
        return script, plan, {"source": "plan_cache_bound", "verified": True,
                              "path": str(plan_path)}
    except PlanCacheError as exc:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = raw.get("outline") if isinstance(raw, dict) else None
        if not isinstance(plan, dict) or not plan:
            raise SystemExit("旧格式计划缓存无法解析出 outline: %s (%s)"
                             % (plan_path, exc)) from exc
        return script, plan, {"source": "legacy_outline_unverified", "verified": False,
                              "path": str(plan_path), "reason": str(exc)[:200],
                              "plan_contract_version": raw.get("plan_contract_version"),
                              "source_hash": raw.get("source_hash")}


def build_audit(chapters: list[int]) -> dict:
    from step03_tts_audio.shot_tts import (build_speech_units,
                                           check_voice_uniqueness,
                                           derive_voice_tiers,
                                           _role_counts_free)
    from tts.voice_registry import get_registry

    per_chapter = []
    all_units = []
    for chapter in chapters:
        ch = chapter_dir(chapter)
        script, plan, identity = _load_chapter(ch, chapter)
        scope = dict(current_scope_identity())
        scope["chapter"] = chapter
        units = build_speech_units(script, scope=scope, script_revision="", plan=plan)
        tiers = derive_voice_tiers(script, plan, roles=_role_counts_free(script, plan))
        uniqueness = check_voice_uniqueness(units, None, script.get("characters") or [])
        per_chapter.append({
            "chapter": chapter,
            "chapter_dir": str(ch),
            "plan_identity": identity,
            "unit_count": len(units),
            "roles": sorted({u.voice_role for u in units if u.voice_role}),
            "voice_uniqueness_ok": bool(uniqueness.get("ok")),
            "conflicts": uniqueness.get("conflicts") or [],
            "shared_roles": uniqueness.get("shared_roles") or [],
            "tiers": {role: {"tier": (rec or {}).get("tier"),
                             "source": (rec or {}).get("source"),
                             "reason": (rec or {}).get("reason")}
                      for role, rec in sorted(tiers.items())},
            "voice_of": {u.voice_role: (u.voice_profile_id or u.model_id or u.speaker)
                         for u in units if u.voice_role},
        })
        all_units.extend(units)

    union = check_voice_uniqueness(all_units, None, [])
    registry = get_registry()
    work = registry._work_of(None)
    return {
        "schema": "voice_tier_audit.v1",
        "scope": current_scope_identity(),
        "chapters": per_chapter,
        "union": {
            "unit_count": len(all_units),
            "role_count": len({u.voice_role for u in all_units if u.voice_role}),
            "voice_uniqueness_ok": bool(union.get("ok")),
            "conflicts": union.get("conflicts") or [],
            "shared_roles": union.get("shared_roles") or [],
            "role_voices": {role: sorted(vs)
                            for role, vs in sorted(union.get("roles", {}).items())},
        },
        "registry": {
            "path": str(registry.registry_path),
            "work_id": work,
            "role_tiers": (registry.role_tiers.get(work) or {}),
            "shared_buckets": (registry.shared_buckets.get(work) or {}),
            "shared_voice_ids": sorted(registry.shared_voice_ids),
            "mapping": {r: v.get("id") for r, v in sorted(registry.mapping.items())},
        },
        "provider_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="多章声音分级/音色身份审计（只读）")
    parser.add_argument("--chapters", required=True,
                        help="章号列表，如 1,2（同一部内共享注册表）")
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args()

    from tts.voice_registry import get_registry, reset_registry_cache

    prev = os.environ.get("TTS_VOICE_REGISTRY_PATH")
    tmp_dir = Path(tempfile.mkdtemp(prefix="voice_tier_audit_"))
    src = Path(prev) if prev else Path(get_registry().registry_path)
    dst = tmp_dir / "voice_registry.json"
    if src.is_file():
        shutil.copyfile(src, dst)
    os.environ["TTS_VOICE_REGISTRY_PATH"] = str(dst)
    reset_registry_cache()
    try:
        report = build_audit(_parse_chapters(args.chapters))
    finally:
        if prev is None:
            os.environ.pop("TTS_VOICE_REGISTRY_PATH", None)
        else:
            os.environ["TTS_VOICE_REGISTRY_PATH"] = prev
        reset_registry_cache()
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": report["union"]["voice_uniqueness_ok"],
        "chapters": [{"chapter": c["chapter"], "units": c["unit_count"],
                      "ok": c["voice_uniqueness_ok"],
                      "shared": c["shared_roles"]} for c in report["chapters"]],
        "union_ok": report["union"]["voice_uniqueness_ok"],
        "roles": report["union"]["role_count"],
        "shared_roles": report["union"]["shared_roles"],
        "json": str(out), "provider_calls": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if report["union"]["voice_uniqueness_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
