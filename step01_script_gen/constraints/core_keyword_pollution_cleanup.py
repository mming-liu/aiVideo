"""Remove legacy ``core_keywords`` fragments appended to visual fields.

The old CoreKeywordCoverageConstraint mutated ff_desc with:
``，<missing keyword 1>、<missing keyword 2>可见。``
Because missing keywords can be malformed fragments, this produced garbage
such as ``庄仆、举灯盏扬可见``.  New writes are diagnostic-only; this migration
constraint removes only suffixes whose pieces actually match core_keywords.
"""
from __future__ import annotations

import re

from .base import Constraint
from .types import RepairRecord, Severity


_SUFFIX_RE = re.compile(r"(?P<suffix>，[^，。！？；]{1,120}可见。?)$")


class CoreKeywordPollutionCleanupConstraint(Constraint):
    name = "core_keyword_pollution_cleanup"
    severity = Severity.WARN
    fields = ("ff_desc", "lf_desc")

    def _is_legacy_suffix(self, suffix: str, core_keywords) -> bool:
        body = suffix.lstrip("，").rstrip("。").removesuffix("可见")
        parts = [p.strip() for p in body.split("、") if p.strip()]
        if len(parts) < 1:
            return False
        if not isinstance(core_keywords, (list, tuple)):
            core_keywords = [core_keywords] if core_keywords else []
        keywords = [str(x).strip() for x in core_keywords if str(x).strip()]
        for part in parts:
            if any(part == kw or part in kw or kw in part for kw in keywords):
                return True
        return False

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            keywords = shot.get("core_keywords") or []
            for field in ("ff_desc", "lf_desc"):
                text = str(shot.get(field) or "")
                match = _SUFFIX_RE.search(text)
                if not match:
                    continue
                suffix = match.group("suffix")
                if not self._is_legacy_suffix(suffix, keywords):
                    continue
                old = text
                new = text[:match.start()].rstrip()
                if not new:
                    continue
                shot[field] = new
                repairs.append(RepairRecord(
                    constraint_name=self.name,
                    shot_index=None if is_hook else shot.get("index", idx),
                    action="rewrite",
                    target=field,
                    before=old[-160:],
                    after=new[-160:],
                    detail=f"删除旧 core_keywords 污染后缀: {suffix}",
                ))
        return repairs


__all__ = ["CoreKeywordPollutionCleanupConstraint"]