# -*- coding: utf-8 -*-
"""合规替换不得产出**不成词**的文本（v6.112）.

实测（第二十七次真实重跑）：shot 6/7 的四个字段出现「击气腾腾」，被模型审计判为
错别字（**判得对**）——而这条错误的产物来自 `SENSITIVE_SUBSTITUTIONS` 自己的
逐字规则 `"杀": "击"`：「杀气」被换成不成词的「击气」。修复轮改一次、
下一次生成又造一次，于是它**永远占着审计预算**。

修法：`sanitize()` 按**长词优先**应用，补短语级映射即可根治。
"""
from __future__ import annotations

from shared.content_safety import SENSITIVE_SUBSTITUTIONS, check_chain_safety, sanitize


def test_sanitize_never_produces_nonsense_sha_compounds():
    """「杀」系词的替换产物必须是词——不能出现「击气/击意/击机」这类非词."""
    for src, bad in (("杀气腾腾", "击气"), ("眼中闪过杀意", "击意"),
                     ("已无杀机", "击机"), ("杀气", "击气")):
        out = sanitize(src)
        assert bad not in out, f"{src} -> {out}"
        assert "杀气" not in out and "杀意" not in out and "杀机" not in out


def test_sanitize_keeps_known_good_phrases():
    assert sanitize("杀气腾腾") == "煞气腾腾"
    assert sanitize("追杀不止") == "追击不止"
    assert sanitize("杀出重围") == "冲出重围"


def test_shao_phrases_are_longer_keys_than_the_single_char_rule():
    """短语级映射必须比单字规则长——否则长词优先也轮不到它."""
    for key in ("杀气", "杀意", "杀机", "追杀", "击杀"):
        assert len(key) > len("杀")
        assert key in SENSITIVE_SUBSTITUTIONS
        # 值不得含本表其它 key（链式替换铁律）
        assert not any(k in SENSITIVE_SUBSTITUTIONS[key]
                       for k in SENSITIVE_SUBSTITUTIONS if k != key)
    assert check_chain_safety() == []
