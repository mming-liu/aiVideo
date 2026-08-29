"""文本生成数据模型 — 供应商无关."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TextGenerationRequest:
    """单次文本生成请求.

    v6.41+ 稳定化（08-23, 治理多次跑批结构漂移）：
    - temperature 0.7 → 0.05（参考大模型稳定性指南，结构化解析任务建议 0.05-0.1）
    - 新增 top_p=0.2（核采样收窄，双重压制随机性）
    - 新增 seed=42（API 支持则传；不支持静默忽略；同 seed 同输入应稳定）
    """

    system_prompt: str
    user_prompt: str
    # 供应商侧输出 token 上限（可选）。None = 不传，由供应商默认；
    # DeepSeek 路径下 None 时进一步兜底到 config.DEEPSEEK_MAX_TOKENS；
    # 都为 None 时 DeepSeek API 不收 max_output_tokens 约束。
    max_tokens: Optional[int] = None
    temperature: float = 0.05
    top_p: float = 0.2
    seed: Optional[int] = 42
    # DeepSeek Responses API json_schema 结构化输出（可选）。
    # 传了就用 strict json_schema 约束模型输出，返回必然合法 JSON。
    json_schema: Optional[dict] = None
    # DeepSeek 思考模式强度（可选，默认取 config.DEEPSEEK_REASONING_EFFORT）。
    # 合法值: none/minimal/low/medium/high/xhigh/max（none 关闭思考）。
    # bench_reasoning.py 用它做逐档位 A/B；生产 main.py 不传则回退配置。
    reasoning_effort: Optional[str] = None
    # 模型覆盖（可选，默认取 config.DEEPSEEK_MODEL）。bench 对比 v4-flash/v4-pro 用。
    model: Optional[str] = None
    # v6.37（08-20）：结构化多轮 user 输入。每项形如 {"role": "user"|"assistant", "content": "..."}
    # 若设置，provider 优先使用（DeepSeek Responses 走 input；其他 chat 走 messages）。
    # 修复轮用此分离"INPUT(原文+当前分镜)"与"REPAIR FEEDBACK"，避免拼成一段。
    # 旧 user_prompt 仍保留作回退（None 时使用）。
    user_inputs: Optional[list] = None


@dataclass
class TextGenerationResult:
    """单次文本生成结果."""

    success: bool
    text: str = ""
    error: Optional[str] = None
    # DeepSeek token 用量（input/output/reasoning/total），供 bench 统计思维链开销。
    # 其他供应商不填（None）。
    usage: Optional[dict] = None
