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
    # v6.62.6（09-09）截断容忍开关，**按调用点语义**决定截断是否致命：
    #   False（默认）：输出被截断 → success=False 整份丢弃。适用于**全脚本生成**——
    #     半个分镜 JSON 不可用，静默下发就是 v6.62.5 那个「多次 JSON 解析失败」根因。
    #   True：截断仍 success=True，并置 result.truncated=True。适用于**列表型提取**
    #     （scene_objects / props / characters / scene_assets）——schema 是
    #     {"key": [...]}，shared.utils.json_repair._truncate_fix 能丢弃未完成的尾对象、
    #     保留前面完整对象，抢救回来的是真数据。v6.62.5 的无条件硬失败把这条抢救路径
    #     一并掐了（截断 → return [] → 物体库零增量），是那次修复的过度收敛。
    #
    # 这是**供应商无关的请求契约**，不是某个 provider 的私有补丁：全部 4 个
    # provider（agent_plan / deepseek / agnes / dashscope）都必须检测截断并走
    # `truncated_result()` 处置。否则同一份调用点代码换 TEXT_PROVIDER 后语义反转
    # ——显式声明「截断不可接受」的全脚本生成会拿到静默下发的半个 JSON。
    allow_truncated: bool = False
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
    # v6.70.3（09-14）：单次请求客户端超时（秒，供应商无关契约）。None = 用
    # provider 默认（agent_plan 走 AGENT_PLAN_TEXT_TIMEOUT_SEC=600）。按调用点
    # 语义分级：候选生成 300 / 定向修复+hook 180——09-14 实测 600s 单值让
    # 「一个慢请求拖死整轮」（候选 3 耗 12 分钟，修复轮超时保留原版烧穿整镜）。
    timeout_sec: Optional[int] = None


@dataclass
class TextGenerationResult:
    """单次文本生成结果."""

    success: bool
    text: str = ""
    error: Optional[str] = None
    # DeepSeek token 用量（input/output/reasoning/total），供 bench 统计思维链开销。
    # 其他供应商不填（None）。
    usage: Optional[dict] = None
    # v6.62.6（09-09）本次输出是否被截断（finish_reason=length /
    # incomplete_details.reason=max_output_tokens）。
    # 仅在 req.allow_truncated=True 时可能出现「success=True 且 truncated=True」——
    # 调用方据此打 WARN，避免「提取到 N 个」静默少报（沉默的数据丢失正是今天这串
    # 问题的共同点）。allow_truncated=False 时截断直接 success=False（此时
    # truncated 仍为 True，供调用方区分「截断」与「其它失败」）。
    truncated: bool = False


def truncated_result(
    *,
    text: str,
    usage: Optional[dict],
    allow_truncated: bool,
    error: str,
) -> "TextGenerationResult":
    """截断响应的**统一**处置（v6.62.6，09-09）.

    全部 provider 检测到截断后都必须经这里返回，`allow_truncated` 才是通用契约
    而非 agent_plan 的私有分支。分两种落法：
      - allow_truncated=True 且有正文 → success=True + truncated=True（放行给
        json_repair 抢救完整列表项，调用方看 truncated 打 WARN）
      - 否则 → success=False + truncated=True（整份丢弃，error 说明为何丢）

    Args:
        text: 已截断但可能含可用前缀的正文。
        usage: provider 侧 token 用量（无则 None）。
        allow_truncated: 见 TextGenerationRequest.allow_truncated。
        error: allow_truncated=False 时给调用方看的失败原因（含 provider/模型/上限）。
    """
    if allow_truncated and text.strip():
        return TextGenerationResult(
            success=True, text=text, usage=usage, truncated=True
        )
    return TextGenerationResult(
        success=False, usage=usage, truncated=True, error=error
    )
