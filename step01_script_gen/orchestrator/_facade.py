# -*- coding: utf-8 -*-
"""兼容门面：让 `step01_script_gen.main` 继续像拆分前那样被读取和打桩（计划 D3）.

拆分后函数分散在 `orchestrator/*.py`，函数体里的全局名字在**各自模块**查找。
测试与工具对 `step01_script_gen.main.X` 的读写，需要落到 X 实际所在的模块，
否则打桩只改了 main 的属性、真实代码照旧调用原函数。

`install(module, owners)` 把 main 模块的类换成 `_FacadeModule`。契约只有三条：

* **读**：`main.X`（含 `from step01_script_gen.main import X`）→ 所属模块的当前值；
* **写**：`main.X = v` → 写到所属模块，同时更新 main 自身 `__dict__` 里的镜像。
  镜像让 `mock.patch` 判定为本地属性、退出时用 setattr 写回它保存的原对象；
  每次访问 `main.__dict__` 前先按所属模块刷新镜像，避免镜像过期；
* **删**：受管理的名字**拒绝删除**（抛 `AttributeError`）。门面不猜测补丁会话、
  不缓存原值：临时覆盖由调用方（`finally` 写回）或测试框架（`mock.patch` /
  `monkeypatch` 保存原对象再 setattr）负责恢复。

2026-11-19 复核收口：原方案的「删除即恢复」需要原值缓存与会话判断，没有实际
消费者，却反复产生过期恢复问题，已撤回。

不在所属表里的名字（main.py 自己定义的 `main()` 等）保持普通模块行为。
"""
from __future__ import annotations

import types

_MISSING = object()
_OWNER_KEY = "__facade_owner__"


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
        mod = (d.get(_OWNER_KEY) or {}).get(name)
        if mod is None:
            types.ModuleType.__setattr__(self, name, value)
            return
        setattr(mod, name, value)
        d[name] = value

    def __delattr__(self, name):
        d = _plain_dict(self)
        mod = (d.get(_OWNER_KEY) or {}).get(name)
        if mod is None:
            types.ModuleType.__delattr__(self, name)
            return
        raise AttributeError(
            f"{self.__name__}.{name} 由门面转发到 {mod.__name__}，不支持删除；"
            f"临时覆盖请由调用方或测试框架写回原对象")


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
    for name, mod in table.items():
        d[name] = mod.__dict__[name]
    module.__class__ = _FacadeModule


__all__ = ["install", "build_owner_table"]
