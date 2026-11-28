"""A 包结构层判别性测试：MotionBundle v1 / motion_revision.v1 与 RFC6901 指针文法。

两个重点
--------
1. **「JSON schema 与运行校验共用同一份定义」不是口号**：这里用一个独立的小型
   JSON Schema 求值器（只实现本协议用到的关键字，按 JSON 语义：布尔不是数值），
   对一批变异文档断言「运行校验拒绝 ⟺ JSON Schema 拒绝」。任何一侧漏了约束
   （比如运行时加了 `maximum` 而 JSON Schema 没渲染）都会让这条断言转红。
2. **指针文法**：`~0`/`~1` 转义、数组下标必须是规范非负整数、拒绝通配符/负索引/
   表达式/逃逸——判别用例逐条独立触发。
"""
from __future__ import annotations

import copy
import json
import re

import pytest

from shared import enhancement_io as eio
from shared.motion_render import schema as S
from tests.enhancements import build_fixtures as bf


# ---------------------------------------------------------------- 迷你 JSON Schema 求值器

def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type_ok(value, t: str) -> bool:
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return _is_number(value)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "array":
        return isinstance(value, (list, tuple))
    if t == "object":
        return isinstance(value, dict)
    raise AssertionError(f"evaluator 不支持 type={t}")


def _schema_ok(value, sch) -> bool:
    """独立实现（不复用产品的 Checker），按 JSON Schema 语义判定。"""
    if not isinstance(sch, dict):
        return True
    if "const" in sch:
        return value == sch["const"] and isinstance(value, type(sch["const"]))
    if "enum" in sch and value not in sch["enum"]:
        return False
    if "anyOf" in sch:
        return any(_schema_ok(value, o) for o in sch["anyOf"])
    t = sch.get("type")
    if t is not None and not _type_ok(value, t):
        return False
    if isinstance(value, str):
        if len(value) < sch.get("minLength", 0):
            return False
        if "maxLength" in sch and len(value) > sch["maxLength"]:
            return False
        if "pattern" in sch and re.search(sch["pattern"], value) is None:
            return False
    if _is_number(value):
        if "minimum" in sch and value < sch["minimum"]:
            return False
        if "maximum" in sch and value > sch["maximum"]:
            return False
        if "exclusiveMinimum" in sch and value <= sch["exclusiveMinimum"]:
            return False
    if isinstance(value, (list, tuple)):
        if len(value) < sch.get("minItems", 0):
            return False
        if "maxItems" in sch and len(value) > sch["maxItems"]:
            return False
        prefix = sch.get("prefixItems") or []
        for i, sub in enumerate(prefix):
            if i < len(value) and not _schema_ok(value[i], sub):
                return False
        rest = value[len(prefix):]
        items = sch.get("items")
        if items is False and rest:
            return False
        if isinstance(items, dict) and not all(_schema_ok(v, items) for v in rest):
            return False
    if isinstance(value, dict):
        if len(value) < sch.get("minProperties", 0):
            return False
        props = sch.get("properties") or {}
        for key in sch.get("required", []):
            if key not in value:
                return False
        extra = sch.get("additionalProperties", True)
        for key, v in value.items():
            if key in props:
                if not _schema_ok(v, props[key]):
                    return False
            elif extra is False:
                return False
            elif isinstance(extra, dict):
                if not _schema_ok(v, extra):
                    return False
    return True


# ---------------------------------------------------------------- 夹具

@pytest.fixture(scope="module")
def bundle() -> dict:
    import os
    default_root = eio.Path(__file__).resolve().parent.parent / ".pytest_tmp"
    root = eio.Path(os.environ.get("OUTPUT_BASE", default_root)) / f"motion_fixtures_{os.getpid()}"
    built = bf.ensure_fixtures(root, scale=0.25)
    return json.loads(eio.Path(built["cases"]["content_a__title"]["bundle"]).read_text(encoding="utf-8"))


DOC_SCHEMA = S.motion_bundle_json_schema()


# ---------------------------------------------------------------- 「同一份定义」判别

def _mutations(base: dict) -> dict[str, dict]:
    """一批**只改结构**的变异：预期每一份都被运行校验拒绝。"""
    out: dict[str, dict] = {}

    def add(name, fn):
        doc = copy.deepcopy(base)
        fn(doc)
        out[name] = doc

    add("unknown_top_level", lambda d: d.update({"extra": 1}))
    add("missing_experiment_id", lambda d: d.pop("experiment_id"))
    add("bad_schema_version", lambda d: d.update({"schema_version": "motion_bundle.v2"}))
    add("bool_as_width", lambda d: d["canvas"].update({"width": True}))
    add("float_width", lambda d: d["canvas"].update({"width": 270.0}))
    add("nan_duration", lambda d: d["specs"][0].update({"duration_sec": float("nan")}))
    add("inf_rect", lambda d: d["specs"][0].update({"rect": [0, 0, float("inf"), 10]}))
    add("bool_in_rect", lambda d: d["specs"][0].update({"rect": [0, 0, 10, True]}))
    add("negative_rect", lambda d: d["specs"][0].update({"rect": [-1, 0, 10, 10]}))
    add("color_alpha_oob", lambda d: d["specs"][0].update({"color": [1, 2, 3, 1.5]}))
    add("color_bool_channel", lambda d: d["specs"][0].update({"color": [True, 2, 3, 1.0]}))
    add("bad_template", lambda d: d["specs"][0].update({"template": "poster"}))
    add("font_size_zero", lambda d: d["specs"][0].update({"font_size_px": 0}))
    add("sha_short", lambda d: d["assets"]["font_sans"].update({"sha256": "abc"}))
    add("asset_unknown_field", lambda d: d["assets"]["font_sans"].update({"note": "x"}))
    add("asset_bad_kind", lambda d: d["assets"]["font_sans"].update({"kind": "audio"}))
    add("asset_abs_path", lambda d: d["assets"]["font_sans"].update({"path": "/etc/passwd"}))
    add("segment_missing_field", lambda d: d["specs"][0]["segments"][0].pop("char_end"))
    add("segment_negative_start", lambda d: d["specs"][0]["segments"][0].update({"char_start": -1}))
    add("overlay_unknown_field", lambda d: d["overlays"][0].update({"z": 1}))
    add("overlay_negative_shot", lambda d: d["overlays"][0].update({"shot_index": -1}))
    add("spec_unknown_field", lambda d: d["specs"][0].update({"opacity": 0.5}))
    add("node_oob", lambda d: d["specs"].append({
        **copy.deepcopy(d["specs"][0]), "spec_id": "t_map", "template": "map",
        "nodes": [{"node_id": "a", "label": "甲", "x": 1.5, "y": 0.5}],
        "edges": [], "segments": [], "source_text": ""}))
    add("specs_not_array", lambda d: d.update({"specs": {"a": 1}}))
    add("empty_assets", lambda d: d.update({"assets": {}}))
    add("locator_unknown_field", lambda d: d["specs"][0]["source_locator"].update({"extra": 1}))
    add("source_unknown_kind", lambda d: d["source"].update({"kind": "somewhere"}))
    add("experiment_id_bad_chars", lambda d: d.update({"experiment_id": "bad id!"}))
    return out


def test_structural_mutations_are_rejected(bundle):
    """先证明变异确实非法（否则下面的一致性断言可能两边都放行）。"""
    for name, doc in _mutations(bundle).items():
        problems = S.check_motion_bundle(doc)
        assert problems, f"{name}: 运行校验没有拒绝这份变异（断言太弱）"


#: JSON 文本里**不存在** NaN/Infinity（严格解析器直接拒收），JSON Schema 无法表达；
#: 这一条由运行时独有地兜住，故不参与"两个出口一致"的对拍。
_NON_FINITE_CASES = {"nan_duration", "inf_rect"}


def test_json_schema_and_runtime_agree_on_mutations(bundle):
    """运行校验拒绝 ⟺ JSON Schema 拒绝（同一份定义的两个出口必须一致）。"""
    for name, doc in _mutations(bundle).items():
        if name in _NON_FINITE_CASES:
            continue
        runtime_ok = not S.check_motion_bundle(doc)
        schema_ok = _schema_ok(doc, DOC_SCHEMA)
        assert runtime_ok == schema_ok, (
            f"{name}: 运行校验 ok={runtime_ok} 但 JSON Schema ok={schema_ok}"
            "（两套判据已分叉）")


def test_json_schema_and_runtime_agree_on_valid_bundle(bundle):
    assert S.check_motion_bundle(bundle) == []
    assert _schema_ok(bundle, DOC_SCHEMA), "合法 bundle 被 JSON Schema 拒绝"


def test_revision_request_schema_matches_runtime(bundle):
    doc = {"schema_version": "motion_revision.v1", "request_id": "r1",
           "base_input_key": "0" * 64, "replacement_bundle": bundle,
           "change_reason": "noop"}
    assert S.check_revision_request(doc) == []
    assert _schema_ok(doc, S.motion_revision_json_schema())

    bad = copy.deepcopy(doc)
    bad["verdict"] = "accepted"  # 请求不允许自带裁决
    assert S.check_revision_request(bad)
    assert not _schema_ok(bad, S.motion_revision_json_schema())

    missing = copy.deepcopy(doc)
    missing.pop("base_input_key")
    assert S.check_revision_request(missing)
    assert not _schema_ok(missing, S.motion_revision_json_schema())


def test_bool_is_not_a_number(bundle):
    doc = copy.deepcopy(bundle)
    doc["canvas"]["fps"] = True
    codes = {p["code"] for p in S.check_motion_bundle(doc)}
    assert "bool_as_number" in codes


def test_nan_and_infinity_rejected_in_python_objects(bundle):
    doc = copy.deepcopy(bundle)
    doc["specs"][0]["duration_sec"] = float("inf")
    assert any(p["code"] == "non_finite_number" for p in S.check_motion_bundle(doc))


def test_load_json_strict_rejects_nan_and_infinity(tmp_path):
    for text in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
        p = tmp_path / "b.json"
        p.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            S.load_json_file_strict(p)
    ok = tmp_path / "ok.json"
    ok.write_text('{"a": 1.5}', encoding="utf-8")
    assert S.load_json_file_strict(ok) == {"a": 1.5}


def test_unknown_field_reports_pointer_path(bundle):
    doc = copy.deepcopy(bundle)
    doc["specs"][0]["extra"] = 1
    problems = S.check_motion_bundle(doc)
    assert any(p["path"] == "/specs/0/extra" and p["code"] == "unknown_field"
               for p in problems)


# ---------------------------------------------------------------- RFC6901

@pytest.mark.parametrize("pointer,tokens", [
    ("", []),
    ("/shots/0/subtitle", ["shots", "0", "subtitle"]),
    ("/a~1b/c~0d", ["a/b", "c~d"]),
])
def test_pointer_parse_ok(pointer, tokens):
    assert S.parse_pointer(pointer) == tokens


@pytest.mark.parametrize("pointer", [
    "shots/0", "/shots/-1/subtitle", "/shots/01/subtitle", "/shots/*/subtitle",
    "/shots/0/sub?title", "/shots/{0}/subtitle", "/../etc/passwd", "/shots//subtitle",
    "/shots/~2bad", "/shots/~", "/a/b/", "/shots/0/./x",
])
def test_pointer_grammar_rejected(pointer):
    with pytest.raises(ValueError):
        S.parse_pointer(pointer)


def test_pointer_roundtrip_escape():
    for token in ("a/b", "a~b", "~1", "青州"):
        assert S.unescape_token(S.escape_token(token)) == token


def test_resolve_pointer_requires_canonical_index():
    doc = {"shots": [{"subtitle": "甲"}]}
    assert S.resolve_pointer(doc, ["shots", "0", "subtitle"]) == (True, "甲")
    assert S.resolve_pointer(doc, ["shots", "1", "subtitle"]) == (False, None)
    # 非规范下标（带前导零）即使能转成 int 也不认
    assert S.resolve_pointer(doc, ["shots", "00", "subtitle"]) == (False, None)


def test_pointer_child_does_not_escape_existing_pointer():
    assert S.pointer_child("/specs/0", "segments", 2) == "/specs/0/segments/2"
    assert S.pointer_join("specs", 0) == "/specs/0"
    assert S.pointer_join("a/b") == "/a~1b"
