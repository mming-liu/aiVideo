# -*- coding: utf-8 -*-
"""`orchestrator._facade` 门面契约（离线，用临时合成模块，不导入真实编排模块）.

契约（2026-11-19 复核收口后）：读写转发到所属模块 + `__dict__` 镜像；受管理名字
**拒绝删除**，临时覆盖由调用方或测试框架恢复。覆盖实际在用的三种恢复方式：
`mock.patch`（字符串路径 / patch.object / 嵌套）、`monkeypatch.setattr` 撤销、
调用方 `finally` 写回保存的原对象。
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.orchestrator import _facade  # noqa: E402

_PKG = "pkg_facade_probe"


@pytest.fixture
def fake(monkeypatch):
    """合成：pkg_facade_probe.impl.rt / .impl.work + 门面 pkg_facade_probe.front."""
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = []
    impl = types.ModuleType(_PKG + ".impl")
    impl.__path__ = []
    rt = types.ModuleType(_PKG + ".impl.rt")
    work = types.ModuleType(_PKG + ".impl.work")
    front = types.ModuleType(_PKG + ".front")
    exec("COUNTER = 0\nSETTINGS = {'k': 1}\n"
         "def bump():\n"
         "    global COUNTER\n"
         "    COUNTER += 1\n"
         "    return COUNTER\n", rt.__dict__)
    work.__dict__["_mrt"] = rt
    exec("def helper():\n    return 'real'\n"
         "def run():\n    return helper() + ':' + str(_mrt.COUNTER)\n", work.__dict__)
    exec("def main():\n    return 'entry'\n", front.__dict__)
    for m in (pkg, impl, rt, work, front):
        monkeypatch.setitem(sys.modules, m.__name__, m)
    _facade.install(front, [rt, work])
    return types.SimpleNamespace(rt=rt, work=work, front=front)


# --------------------------------------------------------------------------- 读写转发
def test_read_forwards_to_owner_and_keeps_own_names(fake):
    assert fake.front.helper is fake.work.helper
    assert fake.front.main() == "entry"
    assert "_mrt" not in fake.front.__dict__, "模块别名不应进入门面"


def test_set_forwards_and_owner_internal_calls_see_it(fake):
    real = fake.work.helper
    fake.front.helper = lambda: "stub"
    assert fake.work.helper() == "stub"
    assert fake.work.run().startswith("stub:")
    fake.front.helper = real


def test_from_import_reads_current_owner_value(fake):
    with mock.patch.object(fake.front, "helper", return_value="stub") as m:
        mod = importlib.import_module(_PKG + ".front")
        assert mod.helper is m
        ns: dict = {}
        exec("from %s.front import helper as h" % _PKG, ns)
        assert ns["h"] is m


def test_dict_mirror_refreshes_after_owner_rewrites_global(fake):
    fake.rt.bump()
    fake.rt.bump()
    assert fake.front.__dict__["COUNTER"] == 2
    with mock.patch(_PKG + ".front.COUNTER", 99):
        assert fake.rt.COUNTER == 99
    assert fake.rt.COUNTER == 2, "退出时应恢复为所属模块的当前值，而非过期镜像"


def test_unowned_names_behave_normally(fake):
    fake.front.extra = 1
    assert fake.front.extra == 1
    del fake.front.extra
    assert not hasattr(fake.front, "extra")
    with pytest.raises(AttributeError):
        fake.front.does_not_exist


# --------------------------------------------------------------------------- 三种实际恢复方式
def test_mock_patch_string_path_exit_keeps_original(fake):
    real = fake.work.helper
    with mock.patch(_PKG + ".front.helper", return_value="stub") as m:
        assert fake.work.run().startswith("stub")
        assert m.called
    assert fake.work.helper is real, "mock.patch 退出后所属模块原函数必须仍在"
    assert fake.front.helper is real


def test_mock_patch_object_and_nested(fake):
    real = fake.work.helper
    with mock.patch.object(fake.front, "helper", return_value="outer"):
        with mock.patch.object(fake.front, "helper", return_value="inner"):
            assert fake.work.helper() == "inner"
        assert fake.work.helper() == "outer", "内层退出应恢复到外层桩"
    assert fake.work.helper is real


def test_monkeypatch_undo_restores_owner(fake):
    real = fake.work.helper
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fake.front, "helper", lambda: "stub")
        assert fake.work.run().startswith("stub")
    assert fake.work.helper is real


def test_caller_finally_restores_saved_object(fake):
    """`tools/replay_step01_repair.py` 的写法：保存原对象 → 覆盖 → finally 写回."""
    saved = fake.front.helper
    try:
        fake.front.helper = lambda: "tmp"
        assert fake.work.run().startswith("tmp")
    finally:
        fake.front.helper = saved
    assert fake.work.helper is saved


def test_repeated_sessions_follow_owner_updates(fake):
    """连续补丁会话之间所属模块自己更新了值：每次退出都恢复到进入时的值."""
    with mock.patch(_PKG + ".front.COUNTER", 0):
        with mock.patch(_PKG + ".front.COUNTER", 1):
            assert fake.rt.COUNTER == 1
    fake.rt.COUNTER = 10
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fake.front, "COUNTER", 20)
        assert fake.rt.COUNTER == 20
    assert fake.rt.COUNTER == 10


def test_equal_but_distinct_dict_restores_original_identity(fake):
    original = fake.rt.SETTINGS
    replacement = {"k": 1}                     # 等值、不同对象
    with mock.patch(_PKG + ".front.SETTINGS", replacement):
        assert fake.rt.SETTINGS is replacement
        fake.rt.SETTINGS["k"] = 2              # 修改的是替换对象
    assert fake.rt.SETTINGS is original
    assert original == {"k": 1}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fake.front, "SETTINGS", {"k": 1})
    assert fake.rt.SETTINGS is original


# --------------------------------------------------------------------------- 删除拒绝
def test_direct_delete_of_managed_name_is_refused(fake):
    real = fake.work.helper
    with pytest.raises(AttributeError, match="不支持删除"):
        del fake.front.helper
    assert fake.work.helper is real
    assert fake.front.helper is real


def test_monkeypatch_delattr_of_managed_name_is_refused(fake):
    real = fake.work.helper
    with pytest.MonkeyPatch.context() as mp:
        with pytest.raises(AttributeError):
            mp.delattr(fake.front, "helper")
    assert fake.work.helper is real


def test_build_owner_table_prefers_first_module_for_duplicates():
    a = types.ModuleType("p.impl.a")
    b = types.ModuleType("p.impl.b")
    a.shared = b.shared = object()
    a.only_a, b.only_b = 1, 2
    table = _facade.build_owner_table([a, b])
    assert table["shared"] is a and table["only_a"] is a and table["only_b"] is b
