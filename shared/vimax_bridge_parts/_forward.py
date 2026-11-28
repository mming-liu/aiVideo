"""Forward proxy so a split module still honours monkeypatches on the parent.

`shared/vimax_bridge.py` is the public module; tests (and callers) patch names on
it. Code moved into `vimax_bridge_parts/*` must therefore resolve those names
through the parent **at call time**, not bind them at import time.
"""
from __future__ import annotations

import importlib


class _Forward:
    """Callable proxy: resolves ``module.name`` on every call/attribute access."""

    __slots__ = ("_module", "_name")

    def __init__(self, module: str, name: str):
        self._module = module
        self._name = name

    def __call__(self, *args, **kwargs):
        target = getattr(importlib.import_module(self._module), self._name)
        return target(*args, **kwargs)

    def __getattr__(self, item):
        target = getattr(importlib.import_module(self._module), self._name)
        return getattr(target, item)

    def __repr__(self) -> str:            # pragma: no cover - 诊断用
        return "<forward %s.%s>" % (self._module, self._name)


def forward(module: str, name: str) -> _Forward:
    return _Forward(module, name)


__all__ = ["_Forward", "forward"]
