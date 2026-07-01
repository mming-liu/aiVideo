"""DeepSeek 文本生成 — OpenAI 兼容 SDK 调用."""

from .config import config
from .models import TextGenerationRequest, TextGenerationResult
from .service import TextGenerationService


class DeepSeekTextService(TextGenerationService):
    """DeepSeek (OpenAI 兼容接口) 文本生成实现."""

    def __init__(self) -> None:
        config.validate()

    def generate(self, req: TextGenerationRequest) -> TextGenerationResult:
        try:
            from openai import OpenAI
        except ImportError:
            return TextGenerationResult(
                success=False,
                error="使用 DeepSeek 需要安装 openai 库: pip install openai",
            )

        try:
            client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL)
            print(f"[INFO] 正在调用 DeepSeek ({config.DEEPSEEK_MODEL}) 生成分镜脚本...")
            response = client.chat.completions.create(
                model=config.DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": req.system_prompt},
                    {"role": "user", "content": req.user_prompt},
                ],
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            )
            text = response.choices[0].message.content
            if not text:
                return TextGenerationResult(success=False, error="DeepSeek 返回文本为空")
            return TextGenerationResult(success=True, text=text)
        except Exception as e:
            return TextGenerationResult(success=False, error=str(e))
