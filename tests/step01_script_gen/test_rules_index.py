"""rules_index.py 防腐烂校验（硬编码清理 2026-09-06 P0）.

RULES_INDEX 是「prompt 铁律 / constraint / validation / tests」的单一事实源。
索引本身若引用了不存在的 constraint 名、测试路径或 md 锚点，就会静默腐烂——
本测试把不变量锁死：索引里写的每一条引用都必须真实存在。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _real_constraint_names() -> set[str]:
    """全部合法 Constraint 名：全局注册表（所有 provider 共用）."""
    from step01_script_gen.constraints import engine as eng
    return {c.name for c in eng.ALL_CONSTRAINTS}


def test_all_constraint_names_resolve():
    """constraints 字段里的每个名字必须是真实 Constraint.name 或独立检测器模块."""
    from step01_script_gen.rules_index import RULES_INDEX
    real = _real_constraint_names()
    bad: list[str] = []
    for version, entry in RULES_INDEX.items():
        for name in entry.get("constraints", []):
            # 合法性二选一：Constraint.name 命中，或 constraints/<name>.py 存在
            # （conflict_detector 等由 engine 直调的独立检测器）
            if name in real or (_PROJECT_ROOT / "step01_script_gen" / "constraints" / f"{name}.py").exists():
                continue
            bad.append(f"{version}: {name!r} 既非 Constraint.name 也无对应模块文件")
    assert not bad, "索引引用了不存在的 constraint：\n" + "\n".join(bad)


def test_all_test_paths_exist():
    """tests 字段里每个路径（文件或目录）必须真实存在."""
    from step01_script_gen.rules_index import RULES_INDEX
    bad: list[str] = []
    for version, entry in RULES_INDEX.items():
        for tp in entry.get("tests", []):
            if not (_PROJECT_ROOT / tp).exists():
                bad.append(f"{version}: 测试路径不存在 {tp!r}")
    assert not bad, "索引引用了不存在的测试路径：\n" + "\n".join(bad)


def test_prompt_sections_resolve():
    """prompt_sections 的 `文件:锚点` 引用必须能在对应 md 中找到锚点文字.

    规则：`文件`（相对 step01_script_gen/ 包，兼容项目根）必须存在；带 `:锚点`
    的条目在锚点文字 ≥4 字时校验其出现（短文本/整文件引用不校验内容）。
    行号引用已废弃（md 一改就漂移），若出现 `（第N行）` 字样直接判失败。
    """
    from step01_script_gen.rules_index import RULES_INDEX
    bad: list[str] = []
    for version, entry in RULES_INDEX.items():
        for ref in entry.get("prompt_sections", []):
            if "（第" in ref and "行）" in ref:
                bad.append(f"{version}: prompt_sections 禁止行号引用（易漂移），改用锚点文字: {ref!r}")
                continue
            path_part, _, anchor = ref.partition(":")
            md = _PROJECT_ROOT / "step01_script_gen" / path_part
            if not md.exists():
                md = _PROJECT_ROOT / path_part
            if not md.exists():
                bad.append(f"{version}: prompt 文件不存在 {path_part!r}")
                continue
            anchor = anchor.strip().strip("（）()")
            if len(anchor) >= 4 and anchor not in md.read_text(encoding="utf-8"):
                bad.append(f"{version}: 锚点 {anchor!r} 在 {path_part} 中找不到")
    assert not bad, "prompt_sections 引用失效：\n" + "\n".join(bad)


def test_index_dates_and_descending_order():
    """date 字段格式统一 YYYY-MM-DD；条目按版本号降序（最新在前）."""
    from step01_script_gen.rules_index import RULES_INDEX
    versions = list(RULES_INDEX.keys())
    for version, entry in RULES_INDEX.items():
        date = entry.get("date", "")
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise AssertionError(f"{version}: date 格式应为 YYYY-MM-DD，得 {date!r}")

    def _vkey(v: str) -> tuple[int, int]:
        m = re.fullmatch(r"v(\d+)\.(\d+)", v)
        assert m, f"版本号格式应为 v<主>.<次>: {v!r}"
        return (int(m.group(1)), int(m.group(2)))

    keys = [_vkey(v) for v in versions]
    assert keys == sorted(keys, reverse=True), (
        f"条目应按版本号降序排列（最新在前），当前顺序: {versions}"
    )


def test_helper_functions():
    """get_rule_version 模糊匹配 / latest_version / all_constraint_names 基本语义."""
    from step01_script_gen.rules_index import (
        all_constraint_names,
        get_rule_version,
        latest_version,
    )
    # latest_version 语义断言：数值最大者（不写死版本号——写死是 change
    # detector，每次加版本都要改测试却测不到任何行为）
    from step01_script_gen.rules_index import RULES_INDEX, _version_key
    assert latest_version() == max(RULES_INDEX.keys(), key=_version_key)
    assert get_rule_version("v6.5") is not None  # 模糊匹配 → v6.5x 最新
    assert get_rule_version("v9.99") is None
    assert "ff_lf_temporal" in all_constraint_names()


def test_version_sort_is_numeric():
    """数值序回归（09-13）：字符串字典序在 v6.100 边界翻车（"v6.100"<"v6.70"）."""
    from step01_script_gen.rules_index import _version_key
    assert _version_key("v6.100") > _version_key("v6.70")
    assert _version_key("v6.100") > _version_key("v6.99")
    assert _version_key("v7.1") > _version_key("v6.100")
