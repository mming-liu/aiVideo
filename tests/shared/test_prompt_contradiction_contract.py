# -*- coding: utf-8 -*-
"""`shared.prompt_contradiction` 的判据回归（夹具驱动）.

历史沿革：这套用例原本把 08-26 的现场 prompt **原文**写进测试代码（含真实人名、
专属地名），等于「判据只对那一本书成立」——换小说以后，同一段代码是否仍然有效，
回归看不出来。2026-11-14 按夹具制改为：

* 数据：`fixtures/prompt_contradiction/packs.json`，人物一律 `甲某/乙某`（两位占位符
  满足判据里 `[\\u4e00-\\u9fff]{2,5}` 的 holder 长度约束），场景用通用名词；
* 机制：本文件只做「取 prompt → 跑判据 → 把消息映射回判据族 → 与预期比较」；
* 换作品/换章节 = 换或加 pack，本文件与判据代码都不改。

覆盖 5 个判据族：`weapon`（持有/否认对称矛盾）、`prohibition`（禁令禁掉前文需要
的实体）、`duplicate`（≥24 字块重复注入）、`punctuation`（「。。」「，，」）、
`stage`（前态词 + 刚刚成立的伤情）。

词表已外置到 `shared/prompt_contradiction_lexicon.py`：本文件比的是**判据族**，
换题材（古装/现代/科幻）只加 pack 与词表覆盖，不改判据代码。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
ROOT = TESTS_DIR.parent
for _p in (str(TESTS_DIR), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.prompt_contradiction import (          # noqa: E402
    PromptContradictionError,
    assert_no_contradictions,
    find_contradictions,
)
from _content_binding import assert_obj_clean     # noqa: E402

PACKS = TESTS_DIR / "shared" / "fixtures" / "prompt_contradiction" / "packs.json"
SCHEMA = "prompt_contradiction_pack.v1"

FAMILIES = {"weapon", "prohibition", "duplicate", "punctuation", "stage"}

#: {packs 路径: JSON 规范化文本 sha256[:16]}——受控夹具只读。
FROZEN = "7e640cbf4c198c82"


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_packs() -> dict[str, dict]:
    doc = json.loads(PACKS.read_text(encoding="utf-8"))
    assert doc.get("schema") == SCHEMA, doc.get("schema")
    packs = {p["id"]: p for p in doc["packs"]}
    assert len(packs) == len(doc["packs"]), "pack id 重复"
    for pack in packs.values():
        assert pack["family"] in FAMILIES, pack["id"]
        for case in pack["cases"]:
            assert set(case["expect"]) <= FAMILIES, (pack["id"], case["id"])
            assert case["prompt"], (pack["id"], case["id"])
    return packs


def kinds(issues: list[str]) -> list[str]:
    """把判据消息映射回判据族（断言族而不是文案，改文案不会假红）."""
    out = set()
    for msg in issues:
        text = str(msg)
        if "描述块重复注入" in text:
            out.add("duplicate")
        elif "双句号" in text:
            out.add("punctuation")
        elif "ff_lf_stage_conflict" in text:
            out.add("stage")
        elif "prohibition_conflict" in text:
            out.add("prohibition")
        elif "对称矛盾" in text:
            out.add("weapon")
        else:
            out.add("unknown:" + text[:24])
    return sorted(out)


def _cases():
    out = []
    for pack_id, pack in load_packs().items():
        for case in pack["cases"]:
            out.append((pack_id, case["id"]))
    return out


@pytest.mark.parametrize("pack_id,case_id", _cases(),
                         ids=["%s::%s" % (p, c) for p, c in _cases()])
def test_case_matches_expected_families(pack_id, case_id):
    pack = load_packs()[pack_id]
    case = next(c for c in pack["cases"] if c["id"] == case_id)
    got = kinds(find_contradictions(case["prompt"]))
    assert got == sorted(case["expect"]), (
        "%s::%s（%s）\n预期 %s\n实际 %s\nprompt=%s"
        % (pack_id, case_id, case.get("why", ""), sorted(case["expect"]), got,
           case["prompt"]))


def test_fixture_is_content_agnostic():
    assert_obj_clean(json.loads(PACKS.read_text(encoding="utf-8")), "prompt_contradiction packs")


def test_fixture_is_frozen():
    doc = json.loads(PACKS.read_text(encoding="utf-8"))
    digest = hashlib.sha256(_dump(doc).encode("utf-8")).hexdigest()[:16]
    assert digest == FROZEN, ("样本包已被改写；确需更新请同步本文件的 FROZEN=%r" % digest)


def test_every_family_has_both_sides():
    """每族都要有「报」与「不报」两侧，只有一侧等于没校准精度."""
    seen: dict[str, set[str]] = {}
    for pack in load_packs().values():
        kinds_seen = seen.setdefault(pack["family"], set())
        for case in pack["cases"]:
            kinds_seen.add("alert" if case["expect"] else "clean")
    assert set(seen) == FAMILIES
    missing = {f: sorted({"alert", "clean"} - s) for f, s in seen.items()
               if s != {"alert", "clean"}}
    assert not missing, "以下判据族缺对照侧：%s" % missing


# ── fail-fast 开关（与内容无关，直接内联）────────────────────────────────

def test_strict_mode_raises(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "1")
    with pytest.raises(PromptContradictionError):
        assert_no_contradictions("短刃在甲某手中。甲某手中没有武器，没有刀。", "首帧")


def test_lenient_mode_does_not_raise(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    assert_no_contradictions("短刃在甲某手中。甲某手中没有武器，没有刀。", "首帧")


def test_empty_and_non_string_inputs_are_ignored():
    assert find_contradictions("") == []
    assert find_contradictions(None) == []
