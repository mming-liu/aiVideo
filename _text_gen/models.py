"""文本生成数据模型 — 供应商无关."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TextGenerationRequest:
    """单次文本生成请求."""

    system_prompt: str
    user_prompt: str
    max_tokens: int = 8000
    temperature: float = 0.7


@dataclass
class TextGenerationResult:
    """单次文本生成结果."""

    success: bool
    text: str = ""
    error: Optional[str] = None
