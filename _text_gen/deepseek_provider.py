"""DeepSeek 文本生成 — OpenAI 兼容 SDK 调用.

支持:
  - deepseek-chat: 标准对话模型
  - deepseek-4-flash: 1M 上下文窗口，高速推理
  - reasoning_effort: low/medium/high，适用于 reasoner 系列
"""

from .config import config
from .models import TextGenerationRequest, TextGenerationResult
from .service import TextGenerationService


class DeepSeekTextService(TextGenerationService):
    """DeepSeek (OpenAI 兼容接口) 文本生成实现.

    支持标准对话和推理模式:
    - deepseek-chat: 标准对话
    - deepseek-4-flash: 1M 上下文窗口，高速
    - deepseek-reasoner: 推理模型，需设置 reasoning_effort
    """

    def __init__(self) -> None:
        config.validate()
        self._model = config.DEEPSEEK_MODEL

    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        try:
            from openai import OpenAI
        except ImportError:
            return TextGenerationResult(
                success=False,
                error="使用 DeepSeek 需要安装 openai 库: pip install openai",
            )

        try:
            client = OpenAI(
                api_key=config.DEEPSEEK_API_KEY,
                base_url=config.DEEPSEEK_BASE_URL,
            )

            # 构建请求参数
            api_params = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": req.system_prompt},
                    {"role": "user", "content": req.user_prompt},
                ],
                "max_tokens": req.max_tokens,
                "temperature": req.temperature,
            }

            # 推理模型支持 reasoning_effort 参数
            if "reasoner" in self._model.lower():
                api_params["reasoning_effort"] = config.DEEPSEEK_REASONING_EFFORT

            # 打印日志
            reasoning_tag = ""
            if "reasoner" in self._model.lower():
                reasoning_tag = f" [reasoning:{config.DEEPSEEK_REASONING_EFFORT}]"
            print(f"[INFO] 正在调用 DeepSeek ({self._model}) 生成分镜脚本{reasoning_tag}...")

            response = client.chat.completions.create(**api_params)
            message = response.choices[0].message

            # 推理模型返回 reasoning_content（思维链），可选择记录或忽略
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning:
                print(f"[DEBUG] DeepSeek 推理过程（前500字）: {reasoning[:500]}")

            text = message.content
            if not text:
                return TextGenerationResult(success=False, error="DeepSeek 返回文本为空")
            return TextGenerationResult(success=True, text=text)
        except Exception as e:
            return TextGenerationResult(success=False, error=str(e))