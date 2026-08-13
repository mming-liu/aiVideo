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
    # DeepSeek Responses API json_schema 结构化输出（可选）。
    # 传了就用 strict json_schema 约束模型输出，返回必然合法 JSON。
    json_schema: Optional[dict] = None
    # DeepSeek 思考模式强度（可选，默认取 config.DEEPSEEK_REASONING_EFFORT）。
    # 合法值: none/minimal/low/medium/high/xhigh/max（none 关闭思考）。
    # bench_reasoning.py 用它做逐档位 A/B；生产 main.py 不传则回退配置。
    reasoning_effort: Optional[str] = None
    # 模型覆盖（可选，默认取 config.DEEPSEEK_MODEL）。bench 对比 v4-flash/v4-pro 用。
    model: Optional[str] = None


@dataclass
class TextGenerationResult:
    """单次文本生成结果."""

    success: bool
    text: str = ""
    error: Optional[str] = None
    # DeepSeek token 用量（input/output/reasoning/total），供 bench 统计思维链开销。
    # 其他供应商不填（None）。
    usage: Optional[dict] = None
