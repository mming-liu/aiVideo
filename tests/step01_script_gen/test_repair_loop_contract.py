# -*- coding: utf-8 -*-
"""修复闭环的契约：缺陷 → 授权修复 → 复查通过，且**只改被点名的字段**（夹具驱动）.

这一族回答的是「修复是否可信」，与哪本小说无关：

1. **点名的缺陷真的存在**——`defect` 变体必须产出预期判据，否则审计条目是空指控；
2. **修完真的干净**——`repaired` 变体过全部确定性判据（不再只是本族那一条）；
3. **修复不越权**——修复产生的每一处字段差异都必须落在 `repair.allowed` 里
   （用生产的 `step01_script_gen.repair_fields.path_allowed` 判，不是自己再写一套）；
4. **换名字裁决不变**——把占位人名整份替换后，缺陷与修复的判定逐条相同，
   证明机制不依赖具体人物（AGENTS.md「内容无关性与夹具」）。

数据在 `fixtures/repair_loop/packs.json`；构造器复用 `script_preflight_builder`
的同一套展开/变异机制，不另起一套。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for _p in (str(HERE), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import script_preflight as pf                       # noqa: E402
import script_preflight_builder as builder                     # noqa: E402
from step01_script_gen.repair_fields import path_allowed       # noqa: E402


def _codes(script: dict) -> list[str]:
    return sorted(i["code"] for i in pf.run(script))


def _strip_shot_prefix(path: str) -> str:
    return re.sub(r"^shots\[\d+\]\.", "", path)


def _packs() -> dict[str, dict]:
    return builder.load_repair_packs()


@pytest.mark.parametrize("pack_id", sorted(_packs()),
                         ids=sorted(_packs()))
def test_baseline_is_clean(pack_id):
    """基线先合法：没有缺陷的起点必须过全部判据，否则后面两条无从归因."""
    case = builder.build_repair_case(_packs()[pack_id])
    assert _codes(case["baseline"]) == []


@pytest.mark.parametrize("pack_id", sorted(_packs()),
                         ids=sorted(_packs()))
def test_defect_reproduces_the_cited_findings(pack_id):
    case = builder.build_repair_case(_packs()[pack_id])
    assert _codes(case["defect"]) == sorted(case["expect_codes"]), (
        "%s：预期 %s，实际 %s" % (pack_id, case["expect_codes"],
                                 _codes(case["defect"])))


@pytest.mark.parametrize("pack_id", sorted(_packs()),
                         ids=sorted(_packs()))
def test_repair_clears_all_findings(pack_id):
    """修完要过**全部**判据（而不是只让本族那条消失）."""
    case = builder.build_repair_case(_packs()[pack_id])
    assert _codes(case["repaired"]) == []


@pytest.mark.parametrize("pack_id", sorted(_packs()),
                         ids=sorted(_packs()))
def test_repair_stays_within_authorized_paths(pack_id):
    """修复只改被点名的字段：每一处差异都要被 `repair.allowed` 覆盖."""
    case = builder.build_repair_case(_packs()[pack_id])
    allowed = case["allowed"]
    outside = sorted(p for p in case["repair_diff"]
                     if not path_allowed(_strip_shot_prefix(p), allowed))
    assert not outside, "%s：修复动到了未授权字段 %s（allowed=%s）" % (
        pack_id, outside, allowed)


@pytest.mark.parametrize("pack_id", sorted(_packs()),
                         ids=sorted(_packs()))
def test_verdicts_survive_a_rename(pack_id):
    """把占位人名整份替换后，缺陷与修复的判定必须逐条相同（内容无关）."""
    case = builder.build_repair_case(_packs()[pack_id])
    aliases = case["aliases"]
    if not aliases:
        # 该 pack 通篇不含人物占位符——它本来就不依赖人物，改名无从谈起。
        # 这里断言「确实没有人名」，避免用一个空映射假装跑过通用性。
        blob = json.dumps(case["defect"], ensure_ascii=False)
        leaked = [n for n in ("甲", "乙", "丙") if n in blob]
        assert not leaked, "%s 声明无别名却在脚本里用了 %s" % (pack_id, leaked)
        return
    renamed_defect = builder.rename(case["defect"], aliases)
    assert _codes(renamed_defect) == _codes(case["defect"])
    renamed_repaired = builder.rename(case["repaired"], aliases)
    assert _codes(renamed_repaired) == []
    # 差异结构也必须一致（改名不该改变「改了哪些字段」）
    assert (sorted(builder.diff_paths(builder.rename(case["baseline"], aliases), renamed_defect))
            == case["defect_diff"])


def test_pack_declares_a_real_repair_authorization():
    """>= 2 个 pack 的修复是「重写文本」而不是「把缺陷原样改回去」——否则闭环是自证."""
    rewrites = 0
    for pack_id, pack in _packs().items():
        case = builder.build_repair_case(pack)
        restored = case["repaired"] == case["baseline"]
        if not restored:
            rewrites += 1
    assert rewrites >= 2, "样本包里至少要有两例是真正的文本重写式修复"


def test_repair_fixture_is_content_agnostic():
    builder.assert_content_agnostic(
        {"packs": list(_packs().values())}, "repair_loop packs")
