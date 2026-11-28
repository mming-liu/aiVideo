# -*- coding: utf-8 -*-
"""I 包链 1 用：**远端状态落盘**的桩 provider，用来真实模拟"本机重启".

`tests.enhancements.video_stub_provider.StubVideoService` 的"远端状态"（task/task_id/
binding）在内存里；本机进程一没，恢复就无从谈起——这正好掩盖了"恢复是否真的
沿用了原 task"这条判据。

本子类只把同一批状态**持久化到文件**（env ``I_VIDEO_STUB_STORE``），其余行为
（prepared 组装、**最底层 POST 计数**、故障注入、下载）全部沿用父类，不覆盖判据。
于是"新起一个进程/新构造一个实例"= 重连同一个远端，恢复是否新增 POST 可判别。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tests.enhancements.video_stub_provider import StubVideoService

STORE_ENV = "I_VIDEO_STUB_STORE"


class FileBackedStubVideoService(StubVideoService):
    """远端状态在 ``I_VIDEO_STUB_STORE`` 指向的 JSON 文件里；重连即重载."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._store = Path(os.environ[STORE_ENV]) if os.environ.get(STORE_ENV) else None
        self._load()

    # -- 远端存储 --
    def _load(self) -> None:
        if not self._store or not self._store.is_file():
            return
        blob = json.loads(self._store.read_text(encoding="utf-8"))
        self.tasks = {str(k): dict(v) for k, v in (blob.get("tasks") or {}).items()}
        self.bindings = {str(k): dict(v) for k, v in (blob.get("bindings") or {}).items()}
        self.posts = list(blob.get("posts") or [])
        self._n = int(blob.get("n") or len(self.posts))

    def _save(self) -> None:
        if not self._store:
            return
        self._store.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tasks": self.tasks, "bindings": self.bindings,
                   "posts": self.posts, "n": self._n}
        fd, tmp = tempfile.mkstemp(dir=str(self._store.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, self._store)

    # -- 唯一被覆盖的行为：POST 之后把远端状态落盘（无论成败） --
    def create_prepared_task(self, prepared, context=None):
        try:
            return super().create_prepared_task(prepared, context)
        finally:
            self._save()
