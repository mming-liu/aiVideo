# -*- coding: utf-8 -*-
"""`tools/script_preflight.py` 的夹具构造器（内容无关）.

按 AGENTS.md「内容无关性与夹具」：**样本数据与机制分离**。

* 本模块只做三件事：把声明式 shot 展开成 `script_result.json` 结构、按 pack 声明的
  字段级变异造缺陷、校验内容无关性与变异因果性；
* 角色、地点、道具、台词、镜头编号、判据预期全部由
  ``fixtures/script_preflight/packs/sample_packs.json`` 提供；
* **新增一类缺陷 / 换一章语料 = 只改 packs，不改本模块、不改消费测试、不改工具代码**；
* 构造器不认识任何具体作品词，也不内置章节答案。

字段级约定（pack 里可省略的部分，由构造器补默认值）：

====== ==============================================================
字段   说明
====== ==============================================================
``ff`` / ``lf`` / ``kfs``   简写；展开成 ``frame_layouts``。条目可写
                            ``"甲"`` 或 ``{"name": "甲", "props": [...]}``
``keyframes``               原样保留，缺 ``stage`` / ``t_sec`` 时补默认值
``patches[].path``          支持 ``a.b[0].c`` 形式，只允许改已存在的路径
====== ==============================================================

「基线先合法」：每个 case 的起点都是 pack 的基线（legal 变体必须 zero finding），
defect 变体只能改**自己声明的那些字段**——`assert_case_is_causal()` 逐镜对比顶层字段，
多改一个字段就报错，避免「造了个缺陷，顺手把别的地方也改了」导致归因失真。
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "script_preflight"
PACKS_PATH = FIXTURES / "packs" / "sample_packs.json"

#: 修复闭环的第二套语料（同一套展开机制，另一种用例形态）。
REPAIR_FIXTURES = HERE / "fixtures" / "repair_loop"
REPAIR_PACKS_PATH = REPAIR_FIXTURES / "packs.json"
REPAIR_SCHEMA = "repair_loop_pack.v1"

SCHEMA = "script_preflight_pack.v1"

#: 具体作品词的单点定义在 `tests/_content_binding.py`（门禁与棘轮共用同一份）。
_TESTS_DIR = HERE.parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
from _content_binding import ALL_TERMS as FORBIDDEN_WORK_TERMS  # noqa: E402

_PATH_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


class FixtureInvariantError(AssertionError):
    """夹具违反内容无关性或因果性不变量；消息给出具体位置。"""


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise FixtureInvariantError(msg)


# ---------------------------------------------------------------------------
# 样本包
# ---------------------------------------------------------------------------

def load_packs() -> dict[str, dict]:
    doc = json.loads(PACKS_PATH.read_text(encoding="utf-8"))
    _check(doc.get("schema") == SCHEMA, "样本包 schema 不符：%r" % doc.get("schema"))
    packs = {p["id"]: p for p in doc["packs"]}
    _check(len(packs) == len(doc["packs"]), "样本包 id 重复")
    for pack in packs.values():
        assert_pack_wellformed(pack)
    return packs


def iter_cases() -> list[tuple[str, str]]:
    """(pack_id, case_id) 列表，供 pytest 参数化。顺序稳定，便于读数对照。"""
    out: list[tuple[str, str]] = []
    for pack_id, pack in load_packs().items():
        for case in pack["cases"]:
            out.append((pack_id, case["id"]))
    return out


def assert_pack_wellformed(pack: dict) -> None:
    pid = pack.get("id")
    for key in ("family", "code", "scope", "shots", "cases", "why", "discriminating"):
        _check(key in pack, "pack %r 缺字段 %r" % (pid, key))
    _check(pack["code"] in pack["scope"],
           "pack %r 的 code %r 不在自己的 scope 里" % (pid, pack["code"]))
    _check(len(pack["shots"]) >= 1, "pack %r 没有 shots" % pid)
    _check(len(pack["cases"]) >= 1, "pack %r 没有 cases" % pid)

    indices = [int(s["index"]) for s in pack["shots"]]
    _check(len(set(indices)) == len(indices), "pack %r 的 shot index 重复" % pid)

    for case in pack["cases"]:
        cid = case.get("id")
        _check(case.get("kind") in ("legal", "defect"),
               "pack %r case %r 的 kind 非法" % (pid, cid))
        issues = (case.get("expected") or {}).get("issues")
        _check(isinstance(issues, list), "pack %r case %r 缺 expected.issues" % (pid, cid))
        for issue in issues:
            _check(int(issue["shot"]) in indices,
                   "pack %r case %r 的 expected.shot %r 不在 shots 里"
                   % (pid, cid, issue.get("shot")))
        if case["kind"] == "legal":
            _check(issues == [], "pack %r case %r 标了 legal 却有预期问题" % (pid, cid))
        else:
            _check(bool(issues), "pack %r case %r 标了 defect 却无预期问题" % (pid, cid))

    # 每个 legal 变体都是「改过字段」的显式声明，不能是空补丁（避免「legal 就是原样」的假覆盖）
    for case in pack["cases"]:
        for patch in case.get("patches") or []:
            pos = int(patch["shot"])
            _check(0 <= pos < len(pack["shots"]),
                   "pack %r case %r 的 patch.shot %r 越界" % (pid, case["id"], patch["shot"]))
            _check(bool(patch.get("path")), "pack %r case %r 的 patch 缺 path"
                   % (pid, case["id"]))


# ---------------------------------------------------------------------------
# 展开：声明式 pack -> script_result.json 结构
# ---------------------------------------------------------------------------

def _entry(raw) -> dict:
    if isinstance(raw, str):
        raw = {"name": raw}
    _check(isinstance(raw, dict) and raw.get("name"),
           "layout 条目缺少 name：%r" % (raw,))
    return {
        "name": str(raw["name"]),
        "on_screen": bool(raw.get("on_screen", True)),
        "pose": str(raw.get("pose") or ""),
        "props": list(raw.get("props") or []),
        "states": list(raw.get("states") or []),
    }


def _layout(raw_list) -> list[dict]:
    return [_entry(e) for e in (raw_list or [])]


def expand_shot(raw: dict) -> dict:
    keyframes = []
    for i, kf in enumerate(raw.get("keyframes") or []):
        item = dict(kf)
        item.setdefault("stage", "approach")
        item.setdefault("t_sec", float(i))
        keyframes.append(item)
    kfs = [_layout(group) for group in (raw.get("kfs") or [])]
    return {
        "index": int(raw["index"]),
        "duration_sec": float(raw.get("duration_sec", 6.0)),
        "scene": str(raw.get("scene") or raw.get("location") or ""),
        "location": str(raw.get("location") or ""),
        "clip_transition": str(raw.get("clip_transition") or "硬切"),
        "context_bridge": str(raw.get("context_bridge") or ""),
        "ff_desc": str(raw.get("ff_desc") or ""),
        "lf_desc": str(raw.get("lf_desc") or ""),
        "motion_desc": str(raw.get("motion_desc") or ""),
        "prompt": str(raw.get("prompt") or ""),
        "audio_cue": str(raw.get("audio_cue") or ""),
        "narration_segment": str(raw.get("narration_segment") or ""),
        "keyframes": keyframes,
        "frame_layouts": {
            "ff": _layout(raw.get("ff")),
            "lf": _layout(raw.get("lf")),
            "kfs": kfs,
        },
    }


def build_baseline(pack: dict) -> dict:
    shots = [expand_shot(s) for s in pack["shots"]]
    return {
        "shots": shots,
        "hook": None,
        "characters": [],
        "total_duration": round(sum(s["duration_sec"] for s in shots), 2),
    }


# ---------------------------------------------------------------------------
# 字段级变异
# ---------------------------------------------------------------------------

def _tokenize(path: str) -> list:
    tokens, pos = [], 0
    while pos < len(path):
        if path[pos] == ".":
            pos += 1
            continue
        m = _PATH_TOKEN.match(path, pos)
        _check(m is not None, "非法 patch.path：%r（位置 %d）" % (path, pos))
        tokens.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
        pos = m.end()
    return tokens


def _set_path(obj, path: str, value) -> None:
    tokens = _tokenize(path)
    cur = obj
    for tok in tokens[:-1]:
        _check(isinstance(cur, (dict, list)), "patch.path %r 中途不是容器" % path)
        try:
            cur = cur[tok]
        except (KeyError, IndexError, TypeError):
            raise FixtureInvariantError("patch.path %r 指向不存在的节点" % path) from None
    last = tokens[-1]
    if isinstance(cur, list):
        _check(isinstance(last, int) and 0 <= last < len(cur),
               "patch.path %r 的列表下标越界（不允许新增元素位置）" % path)
    else:
        try:
            exists = last in cur
        except TypeError:
            raise FixtureInvariantError("patch.path %r 末级不是容器" % path) from None
        _check(exists, "patch.path %r 末级节点不存在（不允许新增字段）" % path)
    _check(type(cur[last]) is type(value),
           "patch.path %r 类型不符：期望 %s，给的是 %s"
           % (path, type(cur[last]).__name__, type(value).__name__))
    cur[last] = value


def apply_patches(script: dict, patches: list[dict]) -> dict:
    out = copy.deepcopy(script)
    for patch in patches or []:
        _set_path(out["shots"][int(patch["shot"])], patch["path"], patch["value"])
    return out


def _declared_keys(pack: dict, case: dict) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for patch in case.get("patches") or []:
        top = re.split(r"[.\[]", patch["path"])[0]
        out.setdefault(int(patch["shot"]), set()).add(top)
    return out


def assert_case_is_causal(pack: dict, case: dict, script: dict) -> None:
    """变异只能动自己声明的顶层字段；其余镜头逐字段不变."""
    base = build_baseline(pack)
    want = _declared_keys(pack, case)
    _check(len(base["shots"]) == len(script["shots"]), "变异不应增删镜头")
    for i, (a, b) in enumerate(zip(base["shots"], script["shots"])):
        changed = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
        _check(changed == want.get(i, set()),
               "pack %r case %r shot[%d] 变更字段 %r，期望 %r"
               % (pack["id"], case["id"], i, sorted(changed), sorted(want.get(i, set()))))


# ---------------------------------------------------------------------------
# 内容无关性
# ---------------------------------------------------------------------------

def assert_content_agnostic(obj, label: str) -> None:
    blob = json.dumps(obj, ensure_ascii=False)
    for term in FORBIDDEN_WORK_TERMS:
        _check(term not in blob, "%s 含具体作品词 %r" % (label, term))


# ---------------------------------------------------------------------------
# 对外：构造 + 自检
# ---------------------------------------------------------------------------

def build_case(pack: dict, case_id: str) -> dict:
    case = next((c for c in pack["cases"] if c["id"] == case_id), None)
    _check(case is not None, "pack %r 没有 case %r" % (pack["id"], case_id))
    script = apply_patches(build_baseline(pack), case.get("patches"))
    assert_case_is_causal(pack, case, script)
    out = {
        "pack_id": pack["id"],
        "family": pack["family"],
        "code": pack["code"],
        "scope": list(pack["scope"]),
        "case_id": case["id"],
        "kind": case["kind"],
        "why": case.get("why", ""),
        "discriminating": list(pack["discriminating"]),
        "expected": case["expected"],
        "script": script,
    }
    assert_content_agnostic(out, "pack %r case %r" % (pack["id"], case_id))
    return out


# ---------------------------------------------------------------------------
# 第二种用例形态：缺陷 → 授权修复 → 复查（repair_loop）
# ---------------------------------------------------------------------------

def load_repair_packs() -> dict[str, dict]:
    doc = json.loads(REPAIR_PACKS_PATH.read_text(encoding="utf-8"))
    _check(doc.get("schema") == REPAIR_SCHEMA,
           "修复样本包 schema 不符：%r" % doc.get("schema"))
    packs = {p["id"]: p for p in doc["packs"]}
    _check(len(packs) == len(doc["packs"]), "修复样本包 id 重复")
    for pack in packs.values():
        for key in ("why", "source_codes", "shots", "defect", "repair"):
            _check(key in pack, "修复 pack %r 缺字段 %r" % (pack["id"], key))
        _check(pack["defect"].get("patches"), "修复 pack %r 的 defect 没有 patches" % pack["id"])
        _check(pack["repair"].get("patches"), "修复 pack %r 的 repair 没有 patches" % pack["id"])
        _check(pack["repair"].get("allowed"), "修复 pack %r 的 repair 没有 allowed" % pack["id"])
        _check(bool(pack["defect"].get("expect_codes")),
               "修复 pack %r 的 defect 没有 expect_codes" % pack["id"])
    return packs


def iter_repair_cases() -> list[str]:
    return list(load_repair_packs())


def _rename_value(value, mapping: dict):
    if isinstance(value, str):
        out = value
        for old, new in mapping.items():
            out = out.replace(old, new)
        return out
    if isinstance(value, list):
        return [_rename_value(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _rename_value(v, mapping) for k, v in value.items()}
    return value


def rename(script: dict, aliases: dict) -> dict:
    """把整份脚本里的占位人名替换成另一组名字（通用性验证用）."""
    if not aliases:
        return copy.deepcopy(script)
    return _rename_value(copy.deepcopy(script), aliases)


def diff_paths(a, b, prefix: str = "") -> set[str]:
    """两棵树之间的**全路径**差异（只到字段级，不比较值）."""
    out: set[str] = set()
    if type(a) is not type(b):
        return {prefix or "<root>"}
    if isinstance(a, dict):
        for key in set(a) | set(b):
            path = ("%s.%s" % (prefix, key)) if prefix else str(key)
            if key not in a or key not in b:
                out.add(path)
            else:
                out |= diff_paths(a[key], b[key], path)
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            out.add(prefix or "<root>")
        for i, (x, y) in enumerate(zip(a, b)):
            out |= diff_paths(x, y, "%s[%d]" % (prefix, i))
        return out
    if a != b:
        out.add(prefix)
    return out


def build_repair_case(pack: dict) -> dict:
    """返回 baseline / defect / repaired 三份脚本与授权路径；并做因果与内容无关自检."""
    baseline = build_baseline(pack)
    defect = apply_patches(baseline, pack["defect"]["patches"])
    repaired = apply_patches(defect, pack["repair"]["patches"])
    out = {
        "pack_id": pack["id"],
        "why": pack["why"],
        "source_codes": list(pack["source_codes"]),
        "allowed": list(pack["repair"]["allowed"]),
        "aliases": dict(pack.get("aliases") or {}),
        "expect_codes": list(pack["defect"]["expect_codes"]),
        "baseline": baseline,
        "defect": defect,
        "repaired": repaired,
        "defect_diff": sorted(diff_paths(baseline, defect)),
        "repair_diff": sorted(diff_paths(defect, repaired)),
        "total_repair_diff": sorted(diff_paths(baseline, repaired)),
    }
    assert_content_agnostic(out, "repair pack %r" % pack["id"])
    return out
