# -*- coding: utf-8 -*-
"""tools.enhancement_workers - 增强包隔离 worker 集合（计划 §F5）.

宿主（Python 3.14 主项目）**只通过 subprocess 参数列表**调用这里的 worker 脚本；
worker 运行在独立的 Python 3.12 解释器里（``.runtime/enhancements-py312`` 等显式
工作区），宿主**不 import** 隔离环境里的包（PySceneDetect / WhisperX / torch）。

协议：
    request JSON 文件  →  worker 进程  →  response JSON 文件
见 :mod:`tools.enhancement_workers.protocol`。

worker 自身：
- 不继承模型凭据（宿主剥掉 ``*_API_KEY`` / ``*_TOKEN`` 一类环境变量）；
- 不自动联网（``HF_HUB_OFFLINE=1`` 等，详见 ``protocol.OFFLINE_ENV``）；
- 结果不可用时如实返回 ``unavailable`` + 结构化 error，而不是崩溃或猜测。
"""

WORKER_PROTOCOL_VERSION = 1

__all__ = ["WORKER_PROTOCOL_VERSION"]
