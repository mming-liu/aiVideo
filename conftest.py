# -*- coding: utf-8 -*-
"""R3-6 测试环境修正：Windows 下 pytest 临时目录不要用 0o700.

已独立复现：Python 3.14 + Windows 上 `os.mkdir(path, 0o700)` 生成的目录
当前进程 `os.scandir` 都会 `WinError 5`；`0o777` 正常。pytest 的
`TempPathFactory` 对 basetemp / numbered dir 固定用 0o700，导致所有用
`tmp_path` 的用例在 setup 阶段失败。这里只改 pytest 临时目录的创建模式，
不改业务代码、不删断言、不跳过用例，也不绕过 safe-delete。
"""
from __future__ import annotations

import os

if os.name == "nt":
    from _pytest import pathlib as _pytest_pathlib
    import _pytest.tmpdir as _pytest_tmpdir

    _orig_make_numbered_dir = _pytest_pathlib.make_numbered_dir

    def _make_numbered_dir(*args, **kwargs):
        kwargs["mode"] = 0o777
        return _orig_make_numbered_dir(*args, **kwargs)

    _pytest_pathlib.make_numbered_dir = _make_numbered_dir
    _pytest_tmpdir.make_numbered_dir = _make_numbered_dir

    _orig_getbasetemp = _pytest_tmpdir.TempPathFactory.getbasetemp

    def _getbasetemp(self):
        if self._basetemp is not None:
            return self._basetemp
        if self._given_basetemp is not None:
            basetemp = self._given_basetemp
            if basetemp.exists():
                _pytest_pathlib.rm_rf(basetemp)
            basetemp.mkdir(mode=0o777)
            self._basetemp = basetemp.resolve()
            return self._basetemp
        return _orig_getbasetemp(self)

    _pytest_tmpdir.TempPathFactory.getbasetemp = _getbasetemp

    #: Python 3.14 Windows 下 `tempfile.mkdtemp()` 也固定 0o700；子进程在
    #: `txn-*` / `prompt_bench_*` 目录里 mkdir 会 WinError 5。这里只把
    #: 测试进程创建的临时根 chmod 到 0o777，不改业务代码、不绕过 safe-delete。
    import tempfile as _tempfile

    _orig_mkdtemp = _tempfile.mkdtemp

    def _mkdtemp(*args, **kwargs):
        path = _orig_mkdtemp(*args, **kwargs)
        try:
            os.chmod(path, 0o777)
        except OSError:
            pass
        return path

    _tempfile.mkdtemp = _mkdtemp
