"""shared.motion_render.schema - MotionBundle v1 / motion_revision.v1 的**唯一定义**（计划 §A2/§A3/§A5）.

为什么是这个结构
----------------
计划要求「JSON schema 和运行校验共用定义」。这里把 schema 写成**类型表达式树**
（``Str``/``IntT``/``NumT``/``Arr``/``Obj`` …），每个节点同时提供两个能力：

1. ``json_schema()`` —— 渲染成标准 JSON Schema 文档（对外发布/给复核读）；
2. ``check(value, path, problems)`` —— 运行时校验，把结构化问题追加进列表。

两者由同一棵树派生，因此不存在"改了运行时忘了改 JSON Schema"的分叉：
``tests/enhancements/test_motion_schema.py`` 用一份独立的小型 JSON Schema 求值器
反查"运行校验拒绝 ⟺ JSON Schema 拒绝"，判别性见该文件。

字段语义（§A2 原文逐条落实）
---------------------------
- 顶层恰好 7 个键，全部必填，**禁止未知字段**；布尔不得冒充数值；NaN/Infinity 拒收。
- ``source``：``kind=fixture|accepted_script`` + ``source_sha256``；accepted_script
  另需 novel/book/chapter/script_revision。
- ``canvas``：整数宽高、``fps`` 必须 == ``shared.media_contract.MERGE_FPS``、
  ``safe_rect``/``subtitle_exclusion_rect`` 在画幅内。
- ``assets``：asset_id → ``path/sha256/license_source/kind``，path 相对只读素材根，
  **必须有字体**。
- ``specs``：字段固定为 12 项（不适用字段为空表）。
- ``overlays``：``overlay_id/spec_id/shot_index/start_sec/end_sec``。

落库前先过这里（结构），再过 ``validate.py``（语义/引用/版式/字体）。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from shared.media_contract import MERGE_FPS

# ---------------------------------------------------------------- 常量

SCHEMA_MOTION_BUNDLE = "motion_bundle.v1"
SCHEMA_MOTION_REVISION = "motion_revision.v1"
SCHEMA_MOTION_OVERLAY_PLAN = "motion_overlay_plan.v1"

SOURCE_KIND_FIXTURE = "fixture"
SOURCE_KIND_ACCEPTED_SCRIPT = "accepted_script"

TEMPLATE_TITLE = "title"
TEMPLATE_LETTER = "letter"
TEMPLATE_MAP = "map"
TEMPLATES = (TEMPLATE_TITLE, TEMPLATE_LETTER, TEMPLATE_MAP)

#: 会被叠加进 Step04 的模板（§A2「消费 Step04 时非空，只有 title/letter 可用」）。
OVERLAY_TEMPLATES = (TEMPLATE_TITLE, TEMPLATE_LETTER)

ASSET_KIND_FONT = "font"
ASSET_KIND_IMAGE = "image"
ASSET_KINDS = (ASSET_KIND_FONT, ASSET_KIND_IMAGE)

#: 本包**自己声明**的可定位字段形状（**不复用**审计模块的 field_path 白名单）。
#: ``<i>`` 是**当前 source revision 的非负规范整数**（无前导零、不越界）。
DECLARED_TEXT_LEAF_SHAPES: tuple[str, ...] = (
    "/shots/<i>/subtitle",
    "/shots/<i>/narration_segment",
    "/hook/subtitle",
    "/hook/narration_segment",
    "/chapter_title",
)

#: map 模板的关系来源叶（对象叶，非字符串叶）：显式 fixture / 人工关系对象。
DECLARED_RELATION_LEAF_SHAPES: tuple[str, ...] = (
    "/map_relations/<i>",
    "/relations",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_ASSET_REL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


# ---------------------------------------------------------------- 问题记录

def problem(code: str, axis: str, path: str, message: str, **detail) -> dict:
    """统一的问题记录：code / axis / path（RFC6901）/ message / detail。

    生产者（schema 或 validate）在遍历时给出**具体字段路径与原因**，报告层只组合透传。
    """
    rec = {"code": code, "axis": axis, "path": path, "message": message}
    if detail:
        rec["detail"] = detail
    return rec


def escape_token(token: str) -> str:
    """RFC6901 转义：``~`` → ``~0``，``/`` → ``~1``（先 ~ 后 /）。"""
    return str(token).replace("~", "~0").replace("/", "~1")


def pointer_join(*tokens: object) -> str:
    """把若干 token 拼成 RFC6901 指针；无 token 时为 ``""``（整文档）。"""
    parts = [p for p in tokens if p is not None and str(p) != ""]
    if not parts:
        return ""
    return "".join(f"/{escape_token(str(p))}" for p in parts)


def pointer_child(base: str, *tokens: object) -> str:
    """在**已有指针**后面追加 token（与 ``pointer_join`` 的区别：base 不再被转义）。

    报告层要能直接点出 ``/specs/0/segments/2/text`` 这样的位置，base 已经是合法指针。
    """
    out = str(base or "")
    for t in tokens:
        out = f"{out}/{escape_token(str(t))}"
    return out


def unescape_token(token: str) -> str:
    """RFC6901 反转义：``~1`` → ``/``，``~0`` → ``~``；非法转义抛 ``ValueError``。"""
    out: list[str] = []
    i = 0
    while i < len(token):
        ch = token[i]
        if ch != "~":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(token) or token[i + 1] not in ("0", "1"):
            raise ValueError(f"非法 RFC6901 转义: {token!r}")
        out.append("~" if token[i + 1] == "0" else "/")
        i += 2
    return "".join(out)


def parse_pointer(pointer: str) -> list[str]:
    """解析 RFC6901 指针为 token 列表（**只做文法**，不做白名单）。

    拒绝：不以 ``/`` 开头（空串除外）、非法 ``~`` 转义、空 token（``//``）、
    通配符/表达式字符、``.``/``..`` 与路径分隔逃逸。
    """
    if pointer is None or not isinstance(pointer, str):
        raise ValueError(f"JSON Pointer 必须是字符串: {pointer!r}")
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"JSON Pointer 必须以 / 开头: {pointer!r}")
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        tok = unescape_token(raw)
        if tok == "":
            raise ValueError(f"JSON Pointer 含空 token（//）: {pointer!r}")
        if tok in (".", ".."):
            raise ValueError(f"JSON Pointer 含路径逃逸 token: {pointer!r}")
        # 下标形态只允许规范非负整数："-1" / "01" / "-" 一律是**文法错误**（不是"查不到"）
        if re.fullmatch(r"-?[0-9]+", tok) and not re.fullmatch(r"0|[1-9][0-9]*", tok):
            raise ValueError(f"JSON Pointer 数组下标必须是规范非负整数（禁负号/前导零）: {pointer!r}")
        if tok == "-":
            raise ValueError(f"JSON Pointer 不支持 '-'（RFC6901 的追加标记）: {pointer!r}")
        if any(ch in tok for ch in "*?[]{}$=<>|\\\"'"):
            raise ValueError(f"JSON Pointer 含通配符/表达式字符: {pointer!r}")
        if any(ord(ch) < 0x20 for ch in tok):
            raise ValueError(f"JSON Pointer 含控制字符: {pointer!r}")
        tokens.append(tok)
    return tokens


def split_index_token(token: str) -> int | None:
    """token 是否为**非负规范整数**数组下标；不是则返回 None。"""
    if isinstance(token, str) and re.fullmatch(r"0|[1-9][0-9]{0,8}", token):
        return int(token)
    return None


def resolve_pointer(doc: Any, tokens: Sequence[str]) -> tuple[bool, Any]:
    """按 token 逐级解析；返回 ``(是否命中, 值)``。数组下标必须规范非负整数。"""
    cur = doc
    for tok in tokens:
        if isinstance(cur, Mapping):
            if tok not in cur:
                return False, None
            cur = cur[tok]
            continue
        if isinstance(cur, (list, tuple)):
            idx = split_index_token(tok)
            if idx is None or idx >= len(cur):
                return False, None
            cur = cur[idx]
            continue
        return False, None
    return True, cur


# ---------------------------------------------------------------- 类型表达式

class Checker:
    """类型表达式基类：JSON Schema 渲染 + 运行时校验的**同源**实现。"""

    #: 该表达式的 JSON Schema 类型名（子类覆盖；None 表示无 type 关键字）
    json_type: str | None = None

    def json_schema(self) -> dict:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def check(self, value: Any, path: str, problems: list[dict], *, axis: str) -> None:
        """结构校验；不抛裸异常，问题进 ``problems``。"""
        if not self._check_type(value, path, problems, axis=axis):
            return
        self._check_value(value, path, problems, axis=axis)

    def _check_type(self, value: Any, path: str, problems: list[dict], *, axis: str) -> bool:
        return True

    def _check_value(self, value: Any, path: str, problems: list[dict], *, axis: str) -> None:
        return None

    # -- 供子类复用 --
    @staticmethod
    def _reject(problems: list[dict], code: str, axis: str, path: str, message: str, **detail) -> None:
        problems.append(problem(code, axis, path, message, **detail))

    @staticmethod
    def _nonfinite(value: Any) -> bool:
        return isinstance(value, float) and (math.isnan(value) or math.isinf(value))


class Str(Checker):
    json_type = "string"

    def __init__(self, *, min_len: int = 0, max_len: int | None = None,
                 enum: Sequence[str] | None = None, const: str | None = None,
                 pattern: str | None = None, pattern_hint: str = ""):
        self.min_len = int(min_len)
        self.max_len = max_len
        self.enum = tuple(enum) if enum is not None else None
        self.const = const
        self.pattern = re.compile(pattern) if pattern else None
        self.pattern_hint = pattern_hint

    def json_schema(self) -> dict:
        out: dict = {"type": "string"}
        if self.min_len:
            out["minLength"] = self.min_len
        if self.max_len is not None:
            out["maxLength"] = self.max_len
        if self.enum is not None:
            out["enum"] = list(self.enum)
        if self.const is not None:
            out["const"] = self.const
        if self.pattern is not None:
            out["pattern"] = self.pattern.pattern
        return out

    def _check_type(self, value, path, problems, *, axis):
        if isinstance(value, str):
            return True
        self._reject(problems, "wrong_type", axis, path,
                     f"期望字符串，实际 {type(value).__name__}", expected="string",
                     actual=type(value).__name__)
        return False

    def _check_value(self, value, path, problems, *, axis):
        if self.const is not None and value != self.const:
            self._reject(problems, "wrong_const", axis, path,
                         f"必须是 {self.const!r}，实际 {value!r}", expected=self.const, actual=value)
            return
        if self.enum is not None and value not in self.enum:
            self._reject(problems, "bad_enum", axis, path,
                         f"取值必须在 {list(self.enum)} 内，实际 {value!r}",
                         allowed=list(self.enum), actual=value)
        if len(value) < self.min_len:
            self._reject(problems, "empty_or_short", axis, path,
                         f"长度必须 >= {self.min_len}，实际 {len(value)}",
                         min_length=self.min_len, actual=len(value))
        if self.max_len is not None and len(value) > self.max_len:
            self._reject(problems, "too_long", axis, path,
                         f"长度必须 <= {self.max_len}，实际 {len(value)}",
                         max_length=self.max_len, actual=len(value))
        if self.pattern is not None and not self.pattern.match(value):
            self._reject(problems, "bad_pattern", axis, path,
                         f"取值不符合格式{'（' + self.pattern_hint + '）' if self.pattern_hint else ''}: {value!r}",
                         pattern=self.pattern.pattern, actual=value)


class IntT(Checker):
    json_type = "integer"

    def __init__(self, *, minimum: int | None = None, maximum: int | None = None):
        self.minimum = minimum
        self.maximum = maximum

    def json_schema(self) -> dict:
        out: dict = {"type": "integer"}
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        return out

    def _check_type(self, value, path, problems, *, axis):
        if isinstance(value, bool):
            self._reject(problems, "bool_as_number", axis, path,
                         "布尔值不得冒充数值", expected="integer", actual="boolean")
            return False
        if isinstance(value, int):
            return True
        if isinstance(value, float):
            self._reject(problems, "expected_integer", axis, path,
                         f"期望整数，实际浮点 {value!r}", expected="integer", actual="number")
            return False
        self._reject(problems, "wrong_type", axis, path,
                     f"期望整数，实际 {type(value).__name__}", expected="integer",
                     actual=type(value).__name__)
        return False

    def _check_value(self, value, path, problems, *, axis):
        if self.minimum is not None and value < self.minimum:
            self._reject(problems, "out_of_range", axis, path,
                         f"必须 >= {self.minimum}，实际 {value}",
                         minimum=self.minimum, actual=value)
        if self.maximum is not None and value > self.maximum:
            self._reject(problems, "out_of_range", axis, path,
                         f"必须 <= {self.maximum}，实际 {value}",
                         maximum=self.maximum, actual=value)


class NumT(Checker):
    json_type = "number"

    def __init__(self, *, minimum: float | None = None, maximum: float | None = None,
                 exclusive_minimum: float | None = None, finite: bool = True):
        self.minimum = minimum
        self.maximum = maximum
        self.exclusive_minimum = exclusive_minimum
        self.finite = finite

    def json_schema(self) -> dict:
        out: dict = {"type": "number"}
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.exclusive_minimum is not None:
            out["exclusiveMinimum"] = self.exclusive_minimum
        return out

    def _check_type(self, value, path, problems, *, axis):
        if isinstance(value, bool):
            self._reject(problems, "bool_as_number", axis, path,
                         "布尔值不得冒充数值", expected="number", actual="boolean")
            return False
        if isinstance(value, (int, float)):
            if self._nonfinite(value):
                self._reject(problems, "non_finite_number", axis, path,
                             f"数值必须有限（拒绝 NaN/Infinity），实际 {value!r}", actual=repr(value))
                return False
            return True
        self._reject(problems, "wrong_type", axis, path,
                     f"期望数值，实际 {type(value).__name__}", expected="number",
                     actual=type(value).__name__)
        return False

    def _check_value(self, value, path, problems, *, axis):
        if self.minimum is not None and value < self.minimum:
            self._reject(problems, "out_of_range", axis, path,
                         f"必须 >= {self.minimum}，实际 {value}", minimum=self.minimum, actual=value)
        if self.maximum is not None and value > self.maximum:
            self._reject(problems, "out_of_range", axis, path,
                         f"必须 <= {self.maximum}，实际 {value}", maximum=self.maximum, actual=value)
        if self.exclusive_minimum is not None and value <= self.exclusive_minimum:
            self._reject(problems, "out_of_range", axis, path,
                         f"必须 > {self.exclusive_minimum}，实际 {value}",
                         exclusive_minimum=self.exclusive_minimum, actual=value)


class BoolT(Checker):
    json_type = "boolean"

    def json_schema(self) -> dict:
        return {"type": "boolean"}

    def _check_type(self, value, path, problems, *, axis):
        if isinstance(value, bool):
            return True
        self._reject(problems, "wrong_type", axis, path,
                     f"期望布尔，实际 {type(value).__name__}", expected="boolean",
                     actual=type(value).__name__)
        return False


class Arr(Checker):
    json_type = "array"

    def __init__(self, items: Checker, *, min_items: int = 0, max_items: int | None = None):
        self.items = items
        self.min_items = int(min_items)
        self.max_items = max_items

    def json_schema(self) -> dict:
        out: dict = {"type": "array", "items": self.items.json_schema()}
        if self.min_items:
            out["minItems"] = self.min_items
        if self.max_items is not None:
            out["maxItems"] = self.max_items
        return out

    def _check_type(self, value, path, problems, *, axis):
        if isinstance(value, (list, tuple)):
            return True
        self._reject(problems, "wrong_type", axis, path,
                     f"期望数组，实际 {type(value).__name__}", expected="array",
                     actual=type(value).__name__)
        return False

    def _check_value(self, value, path, problems, *, axis):
        if len(value) < self.min_items:
            self._reject(problems, "too_few_items", axis, path,
                         f"元素数必须 >= {self.min_items}，实际 {len(value)}",
                         min_items=self.min_items, actual=len(value))
        if self.max_items is not None and len(value) > self.max_items:
            self._reject(problems, "too_many_items", axis, path,
                         f"元素数必须 <= {self.max_items}，实际 {len(value)}",
                         max_items=self.max_items, actual=len(value))
        for i, item in enumerate(value):
            self.items.check(item, f"{path}/{i}", problems, axis=axis)


class Obj(Checker):
    json_type = "object"

    def __init__(self, fields: Mapping[str, tuple[Checker, bool]], *, name: str = "",
                 allow_unknown: bool = False):
        self.fields = {str(k): (v[0], bool(v[1])) for k, v in fields.items()}
        self.name = name
        self.allow_unknown = allow_unknown

    def json_schema(self) -> dict:
        props = {k: v[0].json_schema() for k, v in self.fields.items()}
        required = [k for k, v in self.fields.items() if v[1]]
        out: dict = {"type": "object", "properties": props, "required": required}
        out["additionalProperties"] = True if self.allow_unknown else False
        return out

    def _check_type(self, value, path, problems, *, axis):
        if isinstance(value, Mapping):
            return True
        self._reject(problems, "wrong_type", axis, path,
                     f"期望对象，实际 {type(value).__name__}", expected="object",
                     actual=type(value).__name__)
        return False

    def _check_value(self, value, path, problems, *, axis):
        if not self.allow_unknown:
            for key in value:
                if str(key) not in self.fields:
                    sub = pointer_join(key) if not path else f"{path}/{escape_token(str(key))}"
                    self._reject(problems, "unknown_field", axis, sub,
                                 f"{self.name or '对象'}不允许未知字段: {key!r}", field=str(key))
        for key, (expr, required) in self.fields.items():
            sub_path = f"{path}/{escape_token(key)}"
            if key not in value:
                if required:
                    self._reject(problems, "missing_field", axis, sub_path,
                                 f"缺少必填字段 {key!r}", field=key)
                continue
            expr.check(value[key], sub_path, problems, axis=axis)


class AnyOf(Checker):
    def __init__(self, options: Sequence[Checker]):
        self.options = tuple(options)

    def json_schema(self) -> dict:
        return {"anyOf": [o.json_schema() for o in self.options]}

    def check(self, value, path, problems, *, axis):
        for opt in self.options:
            probe: list[dict] = []
            opt.check(value, path, probe, axis=axis)
            if not probe:
                return
        problems.append(problem("no_variant_matched", axis, path,
                                "取值不匹配任何合法形态",
                                variants=[o.json_schema() for o in self.options]))


# ---------------------------------------------------------------- 复合校验：带逐位元素的定长数组

class RectArray(Checker):
    """``[x, y, w, h]``：4 个整数（拒绝布尔冒充），元素为非负整数。"""

    def json_schema(self) -> dict:
        return {"type": "array", "items": {"type": "integer", "minimum": 0},
                "minItems": 4, "maxItems": 4}

    def check(self, value, path, problems, *, axis):
        if isinstance(value, bool) or not isinstance(value, (list, tuple)):
            self._reject(problems, "wrong_type", axis, path,
                         f"期望 [x,y,w,h] 数组，实际 {type(value).__name__}",
                         expected="array", actual=type(value).__name__)
            return
        if len(value) != 4:
            self._reject(problems, "bad_rect_length", axis, path,
                         f"矩形必须是 4 个整数，实际 {len(value)} 个", actual=len(value))
            return
        for i, item in enumerate(value):
            if isinstance(item, bool) or not isinstance(item, int):
                self._reject(problems, "bool_as_number" if isinstance(item, bool) else "wrong_type",
                             axis, f"{path}/{i}",
                             f"矩形分量必须是整数，实际 {item!r}", actual=repr(item))
                continue
            if item < 0:
                self._reject(problems, "out_of_range", axis, f"{path}/{i}",
                             f"矩形分量不得为负，实际 {item}", actual=item)


class ColorArray(Checker):
    """``[r, g, b, a]``：r/g/b 为 0..255 整数，a 为 0..1 数值（直通 alpha）。"""

    def json_schema(self) -> dict:
        return {
            "type": "array", "minItems": 4, "maxItems": 4, "items": False,
            "prefixItems": [
                {"type": "integer", "minimum": 0, "maximum": 255},
                {"type": "integer", "minimum": 0, "maximum": 255},
                {"type": "integer", "minimum": 0, "maximum": 255},
                {"type": "number", "minimum": 0, "maximum": 1},
            ],
        }

    def check(self, value, path, problems, *, axis):
        if isinstance(value, bool) or not isinstance(value, (list, tuple)):
            self._reject(problems, "wrong_type", axis, path,
                         f"期望 [r,g,b,a] 数组，实际 {type(value).__name__}",
                         expected="array", actual=type(value).__name__)
            return
        if len(value) != 4:
            self._reject(problems, "bad_color_length", axis, path,
                         f"颜色必须是 4 个分量，实际 {len(value)} 个", actual=len(value))
            return
        for i in range(3):
            item = value[i]
            if isinstance(item, bool) or not isinstance(item, int):
                self._reject(problems, "bool_as_number" if isinstance(item, bool) else "wrong_type",
                             axis, f"{path}/{i}",
                             f"颜色分量必须是 0..255 整数，实际 {item!r}", actual=repr(item))
            elif not (0 <= item <= 255):
                self._reject(problems, "out_of_range", axis, f"{path}/{i}",
                             f"颜色分量必须在 0..255，实际 {item}", actual=item)
        alpha = value[3]
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
            self._reject(problems, "wrong_type", axis, f"{path}/3",
                         f"alpha 必须是数值，实际 {alpha!r}", actual=repr(alpha))
        elif self._nonfinite(alpha) or not (0.0 <= float(alpha) <= 1.0):
            self._reject(problems, "out_of_range", axis, f"{path}/3",
                         f"alpha 必须在 0..1，实际 {alpha!r}", actual=repr(alpha))


class Sha256Str(Str):
    def __init__(self):
        super().__init__(min_len=64, max_len=64, pattern=_HEX64.pattern,
                         pattern_hint="64 位小写十六进制 SHA-256")


class IdStr(Str):
    def __init__(self):
        super().__init__(min_len=1, max_len=128, pattern=_ID_RE.pattern,
                         pattern_hint="字母/数字开头，允许 . _ : -")


class AssetRelPath(Str):
    def __init__(self):
        super().__init__(min_len=1, max_len=256, pattern=_SAFE_ASSET_REL.pattern,
                         pattern_hint="相对只读素材根，禁止绝对路径/.. 逃逸")


# ---------------------------------------------------------------- 具体 schema

_FIXTURE_LOCATOR = Obj({
    "fixture_id": (IdStr(), True),
    "field_path": (Str(min_len=1), False),
}, name="fixture source_locator")

_SCRIPT_LOCATOR = Obj({
    "field_path": (Str(min_len=1), True),
    "sha256": (Sha256Str(), True),
}, name="accepted_script source_locator")

SOURCE_LOCATOR = AnyOf((_FIXTURE_LOCATOR, _SCRIPT_LOCATOR))

SEGMENT = Obj({
    "segment_id": (IdStr(), True),
    "text": (Str(min_len=1), True),
    "char_start": (IntT(minimum=0), True),
    "char_end": (IntT(minimum=1), True),
    "start_sec": (NumT(minimum=0.0), True),
    "end_sec": (NumT(minimum=0.0), True),
}, name="segment")

NODE = Obj({
    "node_id": (IdStr(), True),
    "label": (Str(min_len=1), True),
    "x": (NumT(minimum=0.0, maximum=1.0), True),
    "y": (NumT(minimum=0.0, maximum=1.0), True),
}, name="map node")

EDGE = Obj({
    "edge_id": (IdStr(), True),
    "from_node": (IdStr(), True),
    "to_node": (IdStr(), True),
    "start_sec": (NumT(minimum=0.0), True),
    "end_sec": (NumT(minimum=0.0), True),
}, name="map edge")

SPEC = Obj({
    "spec_id": (IdStr(), True),
    "template": (Str(enum=TEMPLATES), True),
    "duration_sec": (NumT(minimum=0.0, exclusive_minimum=0.0), True),
    "font_asset_id": (IdStr(), True),
    "font_size_px": (IntT(minimum=1), True),
    "color": (ColorArray(), True),
    "rect": (RectArray(), True),
    "source_text": (Str(), True),
    "source_locator": (SOURCE_LOCATOR, True),
    "segments": (Arr(SEGMENT), True),
    "nodes": (Arr(NODE), True),
    "edges": (Arr(EDGE), True),
}, name="spec")

OVERLAY = Obj({
    "overlay_id": (IdStr(), True),
    "spec_id": (IdStr(), True),
    "shot_index": (IntT(minimum=0), True),
    "start_sec": (NumT(minimum=0.0), True),
    "end_sec": (NumT(minimum=0.0), True),
}, name="overlay")

_ASSET = Obj({
    "path": (AssetRelPath(), True),
    "sha256": (Sha256Str(), True),
    "license_source": (Str(min_len=1), True),
    "kind": (Str(enum=ASSET_KINDS), True),
}, name="asset")

_CANVAS = Obj({
    "width": (IntT(minimum=1), True),
    "height": (IntT(minimum=1), True),
    "fps": (IntT(minimum=1, maximum=240), True),
    "safe_rect": (RectArray(), True),
    "subtitle_exclusion_rect": (RectArray(), True),
}, name="canvas")

_SOURCE_ACCEPTED = Obj({
    "kind": (Str(const=SOURCE_KIND_ACCEPTED_SCRIPT), True),
    "source_sha256": (Sha256Str(), True),
    "novel": (Str(min_len=1), True),
    "book": (Str(min_len=1), True),
    "chapter": (IntT(minimum=1), True),
    "script_revision": (Str(min_len=1), True),
}, name="source(accepted_script)")

_SOURCE_FIXTURE = Obj({
    "kind": (Str(const=SOURCE_KIND_FIXTURE), True),
    "source_sha256": (Sha256Str(), True),
    "fixture_id": (IdStr(), True),
}, name="source(fixture)")

SOURCE = AnyOf((_SOURCE_FIXTURE, _SOURCE_ACCEPTED))

MOTION_BUNDLE = Obj({
    "schema_version": (Str(const=SCHEMA_MOTION_BUNDLE), True),
    "experiment_id": (Str(min_len=1, max_len=64,
                          pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
                          pattern_hint="[A-Za-z0-9][A-Za-z0-9_-]{0,63}"), True),
    "source": (SOURCE, True),
    "canvas": (_CANVAS, True),
    "assets": (Obj({}, allow_unknown=True), True),
    "specs": (Arr(SPEC), True),
    "overlays": (Arr(OVERLAY), True),
}, name="MotionBundle")

#: assets 是「asset_id → asset 对象」的映射：这里单独用一份键值校验器
#: （JSON Schema 侧渲染为 ``additionalProperties``）。
ASSET_MAP_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": _ASSET.json_schema(),
    "minProperties": 1,
}

REVISION_REQUEST = Obj({
    "schema_version": (Str(const=SCHEMA_MOTION_REVISION), True),
    "request_id": (IdStr(), True),
    "base_input_key": (Sha256Str(), True),
    "replacement_bundle": (MOTION_BUNDLE, True),
    "change_reason": (Str(min_len=1), True),
}, name="motion_revision.v1")


# ---------------------------------------------------------------- 入口

def check_object(spec: Obj, value: Any, path: str = "", *, axis: str = "schema") -> list[dict]:
    problems: list[dict] = []
    spec.check(value, path, problems, axis=axis)
    return problems


def check_assets_map(value: Any, path: str = "/assets", *, axis: str = "assets") -> list[dict]:
    """assets：键必须是合法 asset_id，值过 ``_ASSET``；空表拒收（必须有字体）。"""
    problems: list[dict] = []
    if not isinstance(value, Mapping):
        problems.append(problem("wrong_type", axis, path,
                                f"assets 必须是 asset_id → 资源对象的映射，实际 {type(value).__name__}",
                                expected="object", actual=type(value).__name__))
        return problems
    if not value:
        problems.append(problem("no_assets", axis, path, "assets 不能为空（must have 字体）"))
    for key, item in value.items():
        sub = f"{path}/{escape_token(str(key))}"
        if not _ID_RE.match(str(key)):
            problems.append(problem("bad_asset_id", axis, sub,
                                    f"asset_id 非法: {key!r}",
                                    pattern=_ID_RE.pattern))
        _ASSET.check(item, sub, problems, axis=axis)
    return problems


def check_motion_bundle(bundle: Any) -> list[dict]:
    """结构校验（含 assets 映射）。语义/引用/版式见 ``validate.py``。"""
    problems = check_object(MOTION_BUNDLE, bundle, "", axis="schema")
    if isinstance(bundle, Mapping) and "assets" in bundle:
        existing = {p.get("path") for p in problems}
        if "/assets" not in existing:
            problems.extend(check_assets_map(bundle["assets"]))
    return problems


def check_revision_request(request: Any) -> list[dict]:
    return check_object(REVISION_REQUEST, request, "", axis="schema")


def motion_bundle_json_schema() -> dict:
    """发布的 JSON Schema 文档（与运行校验同源派生）。"""
    doc = MOTION_BUNDLE.json_schema()
    doc["properties"]["assets"] = ASSET_MAP_JSON_SCHEMA
    doc["title"] = "MotionBundle v1"
    doc["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    doc["$id"] = "urn:aivideo:motion_bundle.v1"
    return doc


def motion_revision_json_schema() -> dict:
    doc = REVISION_REQUEST.json_schema()
    doc["title"] = "motion_revision.v1"
    doc["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    doc["$id"] = "urn:aivideo:motion_revision.v1"
    return doc


def load_json_strict(text: str) -> Any:
    """严格 JSON 解析：NaN/Infinity 直接拒收（标准 ``json.loads`` 默认接受它们）。"""
    def _reject(token: str):
        raise ValueError(f"JSON 含非有限数 {token}（NaN/Infinity 在本协议中非法）")
    return json.loads(text, parse_constant=_reject)


def load_json_file_strict(path) -> Any:
    from pathlib import Path
    return load_json_strict(Path(path).read_text(encoding="utf-8"))


def canonical_text_sha256(text: str) -> str:
    """source_text / 定位叶的原样 UTF-8 字节摘要。"""
    import hashlib
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


__all__ = [
    "SCHEMA_MOTION_BUNDLE", "SCHEMA_MOTION_REVISION", "SCHEMA_MOTION_OVERLAY_PLAN",
    "SOURCE_KIND_FIXTURE", "SOURCE_KIND_ACCEPTED_SCRIPT",
    "TEMPLATE_TITLE", "TEMPLATE_LETTER", "TEMPLATE_MAP", "TEMPLATES", "OVERLAY_TEMPLATES",
    "ASSET_KIND_FONT", "ASSET_KIND_IMAGE", "ASSET_KINDS",
    "DECLARED_TEXT_LEAF_SHAPES", "DECLARED_RELATION_LEAF_SHAPES",
    "problem", "escape_token", "pointer_join", "pointer_child", "unescape_token", "parse_pointer",
    "resolve_pointer", "split_index_token",
    "Checker", "Str", "IntT", "NumT", "BoolT", "Arr", "Obj", "AnyOf",
    "RectArray", "ColorArray", "Sha256Str", "IdStr", "AssetRelPath",
    "SEGMENT", "NODE", "EDGE", "SPEC", "OVERLAY", "SOURCE", "SOURCE_LOCATOR",
    "MOTION_BUNDLE", "REVISION_REQUEST",
    "check_object", "check_assets_map", "check_motion_bundle", "check_revision_request",
    "motion_bundle_json_schema", "motion_revision_json_schema",
    "load_json_strict", "load_json_file_strict", "canonical_text_sha256",
]
