# -*- coding: utf-8 -*-
"""文本调用的**通道覆盖**解析（单一来源，2026-09-28 复核后新增）.

`<PREFIX>_TEXT_PROVIDER` / `<PREFIX>_TEXT_MODEL` 是「某一类调用单独走一条通道」的统一
约定（现用于**语义审计**与**定向修复**）。语义三态，全部显式：

- 两者都未设 → `(None, None)`：沿用全局 `TEXT_PROVIDER`（零行为变化）；
- 设了 provider 但缺 model → **显式抛错**（静默沿用调用方的模型名，等于把 400 留到
  跑批时才暴露——准则 7：宁可显式失败，不可静默降级）；
- 两者都设 → `(provider, model)`。

**为什么通道与模型必须一起换**：模型 ID 是**按通道**命名的——ark plan 通道是
`deepseek-v4.1-flash` 这类名字，原生 DeepSeek 的 Responses API 只认
`deepseek-flash` / `deepseek-v4-pro`。只换通道而沿用旧模型名，请求会被直接拒。

**为什么要允许覆盖**（2026-09-28 实测）：agent_plan 通道把 `json_schema` 降级成
`response_format=json_object`（schema 内容整体丢弃）。对**输出形状复杂**的调用
（审计的 `issues[].code`、修复的 `shot_replacements[].shot` 同构镜头），丢掉 schema
的代价是模型按自己的理解输出 → 审计全部退化成同一个无名 code、修复整镜原样回抄
（6/6 次 `resolved=0`）。这类调用需要一个保留 strict json_schema 的通道。
"""
from __future__ import annotations

import os


def resolve_channel(provider_env: str, model_env: str, *, hint: str = "") -> tuple:
    """解析一对通道覆盖环境变量 → `(provider | None, model | None)`.

    Args:
        provider_env: 通道 provider 的环境变量名（如 `AUDIT_TEXT_PROVIDER`）。
        model_env: 该通道可用的模型 ID 环境变量名。
        hint: 抛错时附带的补充说明（例如「原生 DeepSeek 只认 X / Y」）。
    """
    provider = (os.getenv(provider_env) or "").strip().lower()
    if not provider:
        return None, None
    model = (os.getenv(model_env) or "").strip()
    if not model:
        raise ValueError(
            f"{provider_env}={provider} 但未设置 {model_env}：通道覆盖必须**显式**给出"
            f"该通道可用的模型 ID——不能沿用调用方的模型名"
            f"（模型 ID 按通道命名，换通道必须一起换）。"
            + (f" {hint}" if hint else "")
        )
    return provider, model
