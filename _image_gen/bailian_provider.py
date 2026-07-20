"""百炼 (Bailian) 文生图 — 基于 dashscope SDK 的 MultiModalConversation API.

使用 MultiModalConversation.call() 同步调用 qwen-image 模型.
支持多模型回退 (qwen-image-2.0-pro / qwen-image-2.0-pro-2026-04-22).

API 文档: docs/bailian/api-docs/Qwen-Image.md
"""

import os
import time
from typing import Optional

import dashscope
from dashscope import MultiModalConversation

from .config import config
from .models import ImageGenerationRequest, ImageGenerationResult
from .service import ImageGenerationService
from shared.config import (
    BAILIAN_REGION,
    BAILIAN_WORKSPACE_ID,
    BAILIAN_API_KEY,
    BAILIAN_T2I_MODEL,
)
from shared.key_scheduler import KeyScheduler


# Bailian 图片 API 限速（每分钟请求数，默认 3，类似 Agnes 21s 冷却）
_BAILIAN_IMAGE_RPM = int(os.getenv("BAILIAN_IMAGE_RPM", "3"))


class BailianImageService(ImageGenerationService):
    """百炼文生图实现.

    使用 dashscope SDK 的 MultiModalConversation 接口:
      - call() → 同步返回图片 URL

    模型回退:
      - 主模型返回 403 时，自动切换到 BAILIAN_I2V_FALLBACK_MODELS 中的下一个
    """

    def __init__(self) -> None:
        config.validate()
        # 构建完整模型队列: [主模型, 回退1, 回退2, ...]
        fallback_models = [
            m.strip() for m in os.getenv("BAILIAN_T2I_FALLBACK_MODELS", "").split(",")
            if m.strip()
        ]
        self._model_queue: list[str] = [BAILIAN_T2I_MODEL] + fallback_models
        self._current_model_index: int = 0

        # 设置 dashscope API key 和 Bailian Workspace URL
        dashscope.api_key = BAILIAN_API_KEY
        # Bailian 使用 Workspace 专用端点
        if BAILIAN_WORKSPACE_ID and BAILIAN_REGION:
            dashscope.base_url = f"https://{BAILIAN_WORKSPACE_ID}.{BAILIAN_REGION}.maas.aliyuncs.com"
            print(f"[INFO] Bailian API URL: {dashscope.base_url}")

        # Bailian 单 key 限速（类似 Agnes）
        self._key_scheduler = KeyScheduler(
            n_keys=1,
            cooldown=60 / _BAILIAN_IMAGE_RPM if _BAILIAN_IMAGE_RPM > 0 else 0,
            name="bailian_image",
        )

        print(f"[INFO] Bailian 图片服务初始化，模型队列: {self._model_queue}")

    @property
    def _current_model(self) -> str:
        return self._model_queue[self._current_model_index]

    def _advance_model(self) -> Optional[str]:
        """切换到下一个回退模型. 返回新模型名，无可用模型时返回 None."""
        if self._current_model_index + 1 >= len(self._model_queue):
            return None
        self._current_model_index += 1
        return self._current_model

    def generate(self, req: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片."""
        # Bailian 限速：每个请求等待冷却
        self._key_scheduler.acquire()

        # 解析 size (如 "10x24" -> ["10", "24"])
        size_map = {
            "10x24": "10*24",
            "10x18": "10*18",
            "10x16": "10*16",
            "10x12": "10*12",
            "720x1280": "720*12",
            "1024x1024": "10*10",
            "10*24": "10*24",
            "10*18": "10*18",
            "10*16": "10*16",
            "10*12": "10*12",
            "720*12": "720*1280",
            "10*10": "10*10",
        }
        size_str = size_map.get(req.size.replace("x", "*"), req.size.replace("x", "*"))

        # 解析 ratio (如 "9:16" -> ["9", "16"])
        ratio_str = req.ratio.replace(":", "*")

        model_idx = 0
        last_error = ""

        while model_idx < len(self._model_queue):
            model = self._model_queue[model_idx]

            # 构建 messages 格式（MultiModalConversation 需要 messages 列表）
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"text": req.prompt},
                    ],
                }
            ]

            # 构建参数
            params = {
                "model": model,
                "messages": messages,
                "parameters": {
                    "size": size_str,
                    "n": 1,  # 生成1张
                }
            }

            # 添加 negative_prompt
            if req.negative_prompt:
                params["parameters"]["negative_prompt"] = req.negative_prompt

            # 添加 seed（用于可复现）
            if req.seed is not None:
                params["parameters"]["seed"] = req.seed

            print(f"  [镜头{req.shot_index}] 正在生成图片 [{model}]...")
            print(f"    prompt: {req.prompt[:80]}...")
            print(f"    size: {size_str}, ratio: {ratio_str}")

            try:
                rsp = MultiModalConversation.call(**params)

                if rsp.status_code == 200:
                    # 解析响应获取图片 URL
                    content = rsp.output.choices[0].message.content
                    if content and len(content) > 0:
                        img_data = content[0]
                        if "image" in img_data:
                            img_url = img_data["image"]
                            return self._download(req, img_url)

                    return ImageGenerationResult(
                        success=False,
                        error="响应中未找到图片 URL",
                    )

                # ---- 403: 额度耗尽或无权限，尝试回退 ----
                if rsp.status_code == 403:
                    if model_idx + 1 < len(self._model_queue):
                        model_idx += 1
                        print(f"  [镜头{req.shot_index}] [!] 模型 {model} 返回 403，"
                              f"切换到 {self._model_queue[model_idx]}")
                        continue
                    last_error = f"所有模型均返回 403: {rsp.message}"
                    break

                # ---- 其他错误 ----
                last_error = f"API 错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}"
                break

            except Exception as e:
                last_error = f"生成异常: {e}"
                break

        return ImageGenerationResult(success=False, error=last_error)

    def _download(self, req: ImageGenerationRequest, img_url: str) -> ImageGenerationResult:
        """下载图片到本地."""
        import requests
        try:
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            save_path = os.path.join(config.OUTPUT_DIR, f"shot_{req.shot_index}.png")

            img_bytes = requests.get(img_url, timeout=180).content
            with open(save_path, "wb") as f:
                f.write(img_bytes)

            size_kb = len(img_bytes) / 1024
            print(f"  [镜头{req.shot_index}] [OK] 图片已保存 -> {save_path} ({size_kb:.1f} KB)")
            return ImageGenerationResult(success=True, save_path=save_path)

        except Exception as e:
            return ImageGenerationResult(success=False, error=f"下载失败: {e}")