# -*- coding: utf-8 -*-
"""测试包标记：避免 ``tests/tts`` 与生产 ``tts`` 包同名而劫持导入.

pytest 在没有 ``tests/__init__.py`` 时会把 ``tests/tts/test_*.py`` 以
``tts.test_*`` 的模块名导入，``from tts.voice_registry import ...`` 于是解析到
测试目录而不是生产包，collection 直接失败。加这个包标记后模块名是
``tests.tts.test_*``，生产 ``tts`` 包保持唯一。
"""
