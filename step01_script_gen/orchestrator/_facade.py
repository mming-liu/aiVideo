# -*- coding: utf-8 -*-
"""兼容门面：让 `step01_script_gen.main` 继续像拆分前那样被读取和打桩（计划 D3）.

拆分后函数分散在 `orchestrator/*.py`，函数体里的全局名字在**各自模块**查找。
测试与工具对 `step01_script_gen.main.X` 的读写，需要落到 X 实际所在的模块，
否则打桩只改了 main 的属性、真实代码照旧调用原函数。

`install(module, owners)` 把 main 模块的类换成 `_FacadeModule`：

* **读**：`main.X`（含 `from step01_script_gen.main import X`）→ 所属模块的当前值；
* **写**：`main.X = v`（`monkeypatch.setattr` / `mock.patch` 进入与退出）→ 写到所属模块，
  同时更新 main 自身 `__dict__` 里的镜像；
* **镜像**：所属名字在 main 的 `__dict__` 里都有一份，`mock.patch` 据此判定为
  “本地属性”、退出时用 setattr 恢复原值（嵌套 patch 逐层恢复）；每次访问
  `main.__dict__` 前先按所属模块刷新镜像，避免所属模块内部改写全局后镜像过期；
* **删**：`del main.X`（如 `monkeypatch.delattr`、或未判为本地属性的 patch 退出）
  → 把**首次覆盖前缓存的原值**写回所属模块，不做真删除，避免把真实函数删掉。
* **缓存生命周期**：经门面第一次覆盖 X 时缓存所属模块的当前值；之后只要有一次写入
  把 X 写回这个原值（`mock.patch` 正常退出、monkeypatch 撤销），或经删除恢复，
  缓存就清除。下一次覆盖重新以所属模块**当时**的值为原值。

不在所属表里的名字（main.py 自己定义的 `main()` 等）保持普通模块行为。
"""
from __future__ import annotations

import types

_MISSING = object()
_OWNER_KEY = "__facade_owner__"
_ORIG_KEY = "__facade_originals__"


def _same(a, b) -> bool:
    """同一对象，或同类型且相等（比较本身出错按不同处理）."""
    if a is b:
        return True
    try:
        return type(a) is type(b) and bool(a == b)
    except Exception:
        return False


def _plain_dict(module) -> dict:
    return types.ModuleType.__getattribute__(module, "__dict__")


class _FacadeModule(types.ModuleType):

    def __getattribute__(self, name):
        d = _plain_dict(self)
        owner = d.get(_OWNER_KEY)
        if owner is not None:
            if name == "__dict__":
                for key, mod in owner.items():
                    value = mod.__dict__.get(key, _MISSING)
                    if value is not _MISSING:
                        d[key] = value
                return d
            mod = owner.get(name)
            if mod is not None:
                return getattr(mod, name)
        return types.ModuleType.__getattribute__(self, name)

    def __setattr__(self, name, value):
        d = _plain_dict(self)
        owner = d.get(_OWNER_KEY) or {}
        mod = owner.get(name)
        if mod is None:
            types.ModuleType.__setattr__(self, name, value)
            return
        originals = d.setdefault(_ORIG_KEY, {})
        if name not in originals:
            originals[name] = mod.__dict__.get(name, _MISSING)
        elif _same(value, originals[name]):
            # 写回的正是首次覆盖前的原值（mock.patch 退出 / monkeypatch 撤销）：
            # 本次覆盖会话结束，清掉缓存。否则所属模块之后自己更新了值，下一次
            # 经门面覆盖再删除时会恢复成过期的旧原值（2026-11-19 复核 P2-5）。
            del originals[name]
        setattr(mod, name, value)
        d[name] = value

    def __delattr__(self, name):
        d = _plain_dict(self)
        owner = d.get(_OWNER_KEY) or {}
        mod = owner.get(name)
        if mod is None:
            types.ModuleType.__delattr__(self, name)
            return
        originals = d.setdefault(_ORIG_KEY, {})
        original = originals.pop(name, mod.__dict__.get(name, _MISSING))
        if original is _MISSING:
            mod.__dict__.pop(name, None)
            d.pop(name, None)
            return
        setattr(mod, name, original)
        d[name] = original


def _is_alias(value, package: str) -> bool:
    return isinstance(value, types.ModuleType) and value.__name__.startswith(package + ".")


def build_owner_table(owners) -> dict:
    """名字 → 所属模块。排在前面的模块优先（runtime 放第一位：同名的标准库导入归它）."""
    table: dict = {}
    for mod in reversed(list(owners)):
        package = mod.__name__.rsplit(".", 1)[0]
        for name, value in vars(mod).items():
            if name.startswith("__") and name.endswith("__"):
                continue
            if _is_alias(value, package):
                continue
            table[name] = mod
    return table


def install(module, owners) -> None:
    """把 module 变成门面；owners 为实现模块列表（runtime 在首位）."""
    table = build_owner_table(owners)
    d = _plain_dict(module)
    d[_OWNER_KEY] = table
    d[_ORIG_KEY] = {}
    for name, mod in table.items():
        d[name] = mod.__dict__[name]
    module.__class__ = _FacadeModule


__all__ = ["install", "build_owner_table"]
